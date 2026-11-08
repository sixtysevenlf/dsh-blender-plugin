# -*- coding: utf-8 -*-
"""Split from blockout.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .templates import _dedupe_ids, _parse_export_opt, _parse_landmarks, _parse_part, _parse_render_opt, _template_parts
from .geometry import _audit_objects, _bbox_bu, _bbox_mm, _export_objs, _render_views, _tris_base, _tris_eval



# ────────────────────────────── op: base ──────────────────────────────
def _bo_parse_args(name, template, units, anchor, bevel_mm, template_scale, outdir, render, export,
                    audit, reset, parts, landmarks):
    """base 入参解析/校验；返回 dict（坏值 raise ValueError）。"""
    nm = _safe_token(name, "name")
    tmpl = str(template or "none").strip().lower()
    if tmpl not in ALLOWED_TEMPLATES:
        raise ValueError("template 只支持 %s（收到 %r）" % ("/".join(ALLOWED_TEMPLATES), template))
    un = str(units or "mm").strip().lower()
    un_mm = _unit_to_mm(un)
    anc = str(anchor or "bottom").strip().lower()
    if anc not in ALLOWED_ANCHORS:
        raise ValueError("anchor 只支持 %s（收到 %r）" % ("/".join(ALLOWED_ANCHORS), anchor))
    bevel_val = _num(bevel_mm, "bevel_mm", default=DEFAULT_BEVEL_MM, minv=0.0, maxv=20.0)
    tscale = _num(template_scale, "template_scale", default=1.0, gt=0.0, maxv=20.0)
    root = _root_outdir(outdir)
    ropt = _parse_render_opt(render, root, nm)
    eopt = _parse_export_opt(export, root, nm)
    do_audit = _bool(audit, "audit", False)
    do_reset = _bool(reset, "reset", False)
    lm_items, lm_warn = _parse_landmarks(landmarks, un)
    tmpl_parts = _template_parts(tmpl, tscale) if tmpl != "none" else []
    user_parts = []
    if parts is not None:
        if not isinstance(parts, (list, tuple)):
            raise ValueError("parts 必须是 list（收到 %r）" % (type(parts).__name__,))
        user_parts = list(parts)
    if not tmpl_parts and not user_parts:
        raise ValueError("没有几何件：template=none 时 parts 必填；或给 template=humanoid_prim/vehicle")
    parsed = []
    for i, sp in enumerate(tmpl_parts):
        parsed.append(_parse_part(sp, i, "mm"))
    for j, sp in enumerate(user_parts):
        parsed.append(_parse_part(sp, len(parsed) + j, un))
    warnings = list(lm_warn)
    warnings.extend(_dedupe_ids(parsed))
    return {"nm": nm, "tmpl": tmpl, "un": un, "un_mm": un_mm, "anc": anc, "bevel_val": bevel_val,
            "root": root, "ropt": ropt, "eopt": eopt, "do_audit": do_audit, "do_reset": do_reset,
            "lm_items": lm_items, "parsed": parsed, "warnings": warnings}


def _bo_build(parsed, nm, col, mat, bevel_bu):
    """按 parsed 建 primitive 对象（材质/位置/旋转/倒角）；返回 (objs, recs)。"""
    objs = []
    recs = []
    for p in parsed:
        stem = "BO-%s-%s" % (nm, p["id"])
        if p["prim"] == "box":
            me = _prim_box(p["size_bu"][0], p["size_bu"][1], p["size_bu"][2], stem)
        elif p["prim"] == "cylinder":
            me = _prim_cylinder(p["radius_bu"], p["depth_bu"], p["axis"], stem)
        else:
            me = _prim_sphere(p["radius_bu"], stem)
        try:
            me.materials.append(mat)
        except Exception:
            pass
        ob = bpy.data.objects.new(stem, me)
        col.objects.link(ob)
        ob.location = tuple(p["at_bu"])
        ob.rotation_mode = "XYZ"
        ob.rotation_euler = Euler([math.radians(a) for a in p["rot_deg"]], "XYZ")
        _add_bevel(ob, bevel_bu)
        objs.append(ob)
        recs.append({"id": p["id"], "object": ob.name, "prim": p["prim"],
                     "at_mm": [round(v * _mm_per_bu(), 4) for v in p["at_bu"]],
                     "size_mm": p["size_mm"], "radius_mm": p["radius_mm"],
                     "depth_mm": p["depth_mm"], "axis": p["axis"] if p["prim"] == "cylinder" else None,
                     "rot_deg": p["rot_deg"], "mirror": bool(p["mirror"]),
                     "mirror_object": None, "modifiers": ["Bevel"] if bevel_bu > 0 else []})
    return objs, recs


def _bo_mirror(objs, recs, col, bevel_bu):
    """镜像件（矩阵更新后做：真镜像 + 法线修正），追加进 objs 并回填 rec.mirror_object。"""
    for ob, rec in list(zip(list(objs), recs)):
        if not rec["mirror"]:
            continue
        mob = _mirror_object(ob, ob.name + "-m", col)
        _add_bevel(mob, bevel_bu)
        rec["mirror_object"] = mob.name
        objs.append(mob)


def _bo_anchor(objs, anc):
    """anchor：整体平移（只动 Z；X/Y 保持输入位置，便于 bbox 对拍）；返回 dz。"""
    mn0, mx0 = _bbox_bu(objs)
    if anc == "bottom":
        dz = -float(mn0.z)
    elif anc == "center":
        dz = -float(mn0.z + mx0.z) * 0.5
    else:
        dz = 0.0
    if abs(dz) > 1e-12:
        for ob in objs:
            ob.location.z = float(ob.location.z) + dz
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
    return dz


def _bo_extras(result, objs, nm, do_audit, ropt, eopt):
    """可选附加产物：内建体检 / 渲染 / 导出（失败置 ok=false 并带 error）。"""
    if do_audit:
        result["audit"] = _audit_objects(objs)
        if not result["audit"].get("ok"):
            result["ok"] = False
            result.setdefault("error", "内建体检没通过：%s" % (result["audit"].get("note") or ""))
            result.setdefault("hint", "看 audit.per_object；白模一般是封闭 primitive，出现非流形多半是参数/镜像异常")
    if ropt is not None:
        try:
            result["render"] = _render_views(objs, ropt, nm)
            if not result["render"].get("ok"):
                result["ok"] = False
                result.setdefault("error", "渲染失败")
        except Exception as e:
            result["render"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:260]),
                                "hint": "渲染是附加产物；场景里的几何已建好（objects 在回执里）"}
            result["ok"] = False
            result.setdefault("error", "渲染失败：%s" % str(e)[:200])
    if eopt is not None:
        try:
            result["export"] = _export_objs(objs, eopt)
            if not result["export"].get("ok"):
                result["ok"] = False
                result.setdefault("error", result["export"].get("error") or "导出失败")
        except Exception as e:
            result["export"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}
            result["ok"] = False
            result.setdefault("error", "导出失败：%s" % str(e)[:200])
    return result


def blockout_base(name=None, template="none", units="mm", anchor="bottom", parts=None,
                  landmarks=None, collection=None, render=None, export=None, audit=False,
                  reset=False, bevel_mm=DEFAULT_BEVEL_MM, template_scale=1.0, outdir=None):
    t_start = time.perf_counter()
    try:
        a = _bo_parse_args(name, template, units, anchor, bevel_mm, template_scale, outdir, render,
                           export, audit, reset, parts, landmarks)
    except ValueError as e:
        return _err(e, hint="base 参数说明见 K.dsh_blockout_api['help']()；parts 每件要 prim+尺寸+at_mm",
                    op="base")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), hint="参数解析失败，场景未改动", op="base")
    try:
        col = _ensure_collection(collection or DEFAULT_COLLECTION)
        removed = _clear_bo_objects(col) if a["do_reset"] else []
        mat = _white_material()
        bevel_bu = a["bevel_val"] / _mm_per_bu()
        objs, recs = _bo_build(a["parsed"], a["nm"], col, mat, bevel_bu)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        _bo_mirror(objs, recs, col, bevel_bu)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        dz = _bo_anchor(objs, a["anc"])
        mn, mx = _bbox_bu(objs)
        geom_ms = int(round((time.perf_counter() - t_start) * 1000.0))
        landmarks_out = {}
        for lm_name, vals in a["lm_items"]:
            landmarks_out[lm_name] = [round((vals[i] + (dz if i == 2 else 0.0)) * _mm_per_bu(), 4)
                                      for i in range(3)]
        total_tris, per_tris = _tris_base(objs)
        for rec, t in zip(recs, per_tris):
            rec["tris"] = int(t)
        result = {
            "ok": True, "version": BLOCKOUT_VERSION, "op": "base", "name": a["nm"],
            "template": a["tmpl"], "units": a["un"], "anchor": a["anc"],
            "collection": col.name, "material": mat.name,
            "object_count": len(objs), "objects": [ob.name for ob in objs],
            "parts": recs, "total_tris": int(total_tris), "total_tris_eval": _tris_eval(objs),
            "bbox_mm": _bbox_mm(mn, mx),
            "landmarks": landmarks_out,
            "bevel_mm": a["bevel_val"], "reset_removed": removed,
            "units_meta": {"mm_per_unit": round(_mm_per_bu(), 6), "input": a["un"],
                           "input_mm_factor": a["un_mm"]},
            "warnings": a["warnings"],
            "ms": {"geom": geom_ms},
        }
        result = _bo_extras(result, objs, a["nm"], a["do_audit"], a["ropt"], a["eopt"])
        result["ms"]["total"] = int(round((time.perf_counter() - t_start) * 1000.0))
        return result
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)[:280]),
                    hint="建几何阶段失败；场景可能留下部分对象，可用 K.dsh_qc_api 或 blender_session 回退",
                    op="base", name=name)


# ────────────────────────────── op: batch ──────────────────────────────
def blockout_batch(models=None, outdir=None, export=None, collection=None, cleanup=True, render=None):
    t_start = time.perf_counter()
    try:
        if not isinstance(models, (list, tuple)) or not models:
            raise ValueError("models 必须是非空 list（每项是 base 的参数 dict，至少含 name+parts）")
        if len(models) > 100:
            raise ValueError("models 最多 100 件（收到 %d）" % len(models))
        cleanup_b = _bool(cleanup, "cleanup", True)
        root = _root_outdir(outdir)
        base_col = str(collection or "BLOCKOUT-BATCH").strip() or "BLOCKOUT-BATCH"
        names = []
        for i, m in enumerate(models):
            if not isinstance(m, dict):
                raise ValueError("models[%d] 必须是 dict（收到 %r）" % (i, type(m).__name__))
            mn = _safe_token(m.get("name") or ("m%02d" % i), "models[%d].name" % i)
            if mn in names:
                raise ValueError("models 里 name 重复：%r（导出文件名会互相覆盖，先改名）" % mn)
            names.append(mn)
    except ValueError as e:
        return _err(e, hint="batch 的每项与 base 同构；建议每件至少 {name, parts}", op="batch")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), op="batch")
    items = []
    ok_all = True
    for i, spec in enumerate(models):
        item = dict(spec)
        nm = names[i]
        item["name"] = nm
        if "collection" not in item or not item.get("collection"):
            item["collection"] = "%s-%s" % (base_col, nm)
        if item.get("render") is None and render is not None:
            item["render"] = render
        if item.get("export") is None and export is not None:
            item["export"] = export
        if item.get("outdir") is None:
            item["outdir"] = root
        try:
            r = blockout_base(**item)
        except TypeError as e:
            r = _err("参数不匹配：%s" % str(e)[:220],
                     hint="batch 项与 base 参数同名（见 help）；不认识的键已列出", op="batch-item",
                     name=nm)
        except Exception as e:
            r = _err("%s: %s" % (type(e).__name__, str(e)[:240]), op="batch-item", name=nm)
        items.append(r)
        if not r.get("ok"):
            ok_all = False
        if cleanup_b:
            try:
                _remove_objects(r.get("objects") or [])
                _remove_collection_if_empty(item.get("collection"))
            except Exception:
                pass
    built = sum(1 for r in items if r.get("ok"))
    return {"ok": bool(ok_all), "op": "batch", "count": len(items), "built": built,
            "failed": [it.get("name") for it in items if not it.get("ok")],
            "items": items, "collection": base_col, "cleanup": cleanup_b, "outdir": root,
            "ms": int(round((time.perf_counter() - t_start) * 1000.0)),
            "total_ms": int(round((time.perf_counter() - t_start) * 1000.0))}


# ────────────────────────────── op: export（对已建场景补）─────────────────────────────
def _find_model_objects(objects=None, name=None, collection=None):
    found = []
    missing = []
    if objects:
        names = [objects] if isinstance(objects, str) else list(objects)
        for n in names:
            ob = bpy.data.objects.get(str(n))
            if ob is None:
                missing.append(str(n))
            elif ob.type == "MESH":
                found.append(ob)
    if name:
        prefix = "BO-%s-" % _safe_token(name, "name")
        for ob in bpy.context.scene.objects:
            if ob.type == "MESH" and str(ob.name).startswith(prefix):
                found.append(ob)
    if collection:
        col = bpy.data.collections.get(str(collection))
        if col is None:
            missing.append("collection:%s" % collection)
        else:
            for ob in col.objects:
                if ob.type == "MESH" and str(ob.name).startswith("BO-"):
                    found.append(ob)
    uniq = []
    seen = set()
    for ob in found:
        if ob.name not in seen:
            seen.add(ob.name)
            uniq.append(ob)
    if missing:
        raise ValueError("找不到这些对象：%s" % missing[:6])
    if not uniq:
        raise ValueError("找不到已建白模：给 objects=[名字] / name=<模型名> / collection=<集合名>")
    return uniq


def blockout_export(objects=None, name=None, collection=None, glb=None, obj=None, export=None,
                    render=None, outdir=None):
    t_start = time.perf_counter()
    try:
        objs = _find_model_objects(objects=objects, name=name, collection=collection)
        root = _root_outdir(outdir)
        # export 参数既可嵌套 dict，也可平级给 glb/obj
        exp = {}
        if isinstance(export, dict):
            exp.update(export)
        elif export is True:
            exp["glb"] = True
        elif isinstance(export, str) and export.strip():
            exp["glb"] = export
        elif export not in (None, False, 0):
            raise ValueError("export 必须是 dict / true / false / 路径字符串（收到 %r）" % (export,))
        if glb is not None:
            exp["glb"] = glb
        if obj is not None:
            exp["obj"] = obj
        stem = _safe_token(name or (objs[0].name if objs else "export"), "name")
        eopt = _parse_export_opt(exp if exp else None, root, stem)
        ropt = _parse_render_opt(render, root, stem)
        if eopt is None and ropt is None:
            raise ValueError("什么都没要求：至少给 render=<视图参数> / glb= / obj= 之一")
        mn, mx = _bbox_bu(objs)
        result = {"ok": True, "op": "export", "objects": [ob.name for ob in objs],
                  "object_count": len(objs), "bbox_mm": _bbox_mm(mn, mx)}
        if ropt is not None:
            try:
                result["render"] = _render_views(objs, ropt, stem)
                if not result["render"].get("ok"):
                    result["ok"] = False
            except Exception as e:
                result["render"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}
                result["ok"] = False
                result.setdefault("error", "渲染失败：%s" % str(e)[:200])
        if eopt is not None:
            try:
                result["export"] = _export_objs(objs, eopt)
                if not result["export"].get("ok"):
                    result["ok"] = False
                    result.setdefault("error", result["export"].get("error") or "导出失败")
            except Exception as e:
                result["export"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}
                result["ok"] = False
                result.setdefault("error", "导出失败：%s" % str(e)[:200])
        result["ms"] = int(round((time.perf_counter() - t_start) * 1000.0))
        return result
    except ValueError as e:
        return _err(e, hint="export 不重建几何：对象要先用 base 建好；名字形如 BO-<name>-<id>", op="export")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)[:280]), op="export")

