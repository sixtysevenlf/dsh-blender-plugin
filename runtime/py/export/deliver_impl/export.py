# -*- coding: utf-8 -*-
"""Split from deliver.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .materials import _dv_bbox, _dv_eval_part, _dv_grey_seed, _dv_mat_ref, _dv_material_of, _dv_units_info, _dv_write_mtl, _dv_write_obj, _dv_write_stl, _dv_write_text


def deliver_export(dir=None, objects=None, scope=None, name="model", fmt="obj", normalize=True,
                   target_half_extent=1.0, per_part_materials=True, manifest=True, units_note=True,
                   include_hidden=False, require_gate=False, gate_spec=None, gate_preset=None,
                   gate_overrides=None, force_gate=False, gate_reason=""):
    """把选中的 mesh 导出成可交付的 OBJ(+MTL) / STL，并写 manifest（含 md5 / 面数 / 单位 / 归一化记录）。

    典型一行：K.dsh_deliver_api["export"]({"dir": "D:/Blender/house", "scope": "House", "name": "house"})
    空产出（挑不到对象 / 全都没有三角面）→ ok:false，绝不静默成功。
    """
    t0 = time.perf_counter()
    f = str(fmt or "obj").strip().lower()
    if f not in _DV_FORMATS:
        return _dv_j({"ok": False, "error": "非法 fmt=%s" % fmt, "allowed": list(_DV_FORMATS),
                   "hint": "fmt='obj'（默认，可带 .mtl）或 fmt='stl'（二进制单文件）"})
    try:
        the = float(target_half_extent)
    except Exception:
        return _dv_j({"ok": False, "error": "target_half_extent 必须是数字", "given": target_half_extent})
    if the <= 0:
        return _dv_j({"ok": False, "error": "target_half_extent 必须 > 0（最长边 = 2 × target_half_extent）", "given": the})
    nm, changed_name = _dv_safe(name), None
    if nm != str(name or ""):
        changed_name = "name 已安全化：%r → %r" % (name, nm)
    out_dir = dir
    if not out_dir:
        K = _dv_kernel()
        base = getattr(K, "out_dir", None) if K is not None else None
        out_dir = os.path.join(base or os.path.expanduser("~"), "deliver")
    out_dir = _dv_win(out_dir)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return _dv_j({"ok": False, "error": "产物目录建不出来（%s）：%s" % (type(e).__name__, e), "dir": out_dir})

    warn = []
    if changed_name:
        warn.append(changed_name)
    # P0：交付强制门（默认 false 兼容旧流程；model_deliver / gate_deliver 会传 true）。
    # 下一个 minor 计划默认开启；现在不通过时回执明确写 gate_warning，不静默。
    gate_rec = None
    if require_gate:
        K = _dv_kernel()
        gapi = getattr(K, "dsh_gate_api", None) if K is not None else None
        if gapi is None:
            return _dv_j({"ok": False, "blocked": True, "error": "require_gate=true 但 K.dsh_gate_api 不存在",
                          "hint": "headless 预热：preload=\"gate\"（或先跑 blender_rt_plan(op=\"gate_run\")）"})
        if force_gate and not str(gate_reason or "").strip():
            return _dv_j({"ok": False, "blocked": True,
                          "error": "force_gate=true 必须给 gate_reason（写进回执）"})
        try:
            gate_rec = gapi("run", {"spec": gate_spec, "preset": gate_preset,
                                    "overrides": gate_overrides})
            if isinstance(gate_rec, str):
                gate_rec = json.loads(gate_rec)
        except Exception as e:
            return _dv_j({"ok": False, "blocked": True,
                          "error": "gate_run 失败：%s: %s" % (type(e).__name__, str(e)[:220])})
        _gv = (gate_rec or {}).get("verdict")
        if _gv != "pass" and not force_gate:
            return _dv_j({"ok": False, "blocked": True, "verdict": _gv, "gate": gate_rec,
                          "error": "安全门未通过（verdict=%s）：拒绝导出" % _gv,
                          "hint": "看 gate.failed/degraded/skipped；允许降级交付用 gate_deliver(allow_degraded=true)，强导必须 force_gate=true+gate_reason"})
    else:
        warn.append("gate_warning：require_gate=false —— 本次导出前未强制过门；流程化交付请用 gate_deliver 或 require_gate=true")
    objs, missing, err, excluded = _dv_pick(objects, scope, include_hidden)
    if err:
        return _dv_j({"ok": False, "error": err, "dir": out_dir, "objects": objects, "scope": scope})
    if missing:
        warn.append("对象不存在（已跳过）：%s" % ", ".join(missing))
    if not objs:
        return _dv_j({"ok": False, "error": "挑到 0 个 mesh：objects/scope 都没给出有效对象，或全部被隐藏过滤（include_hidden=False）",
                   "objects": objects, "scope": scope, "excluded_hidden": excluded, "dir": out_dir})

    dg = bpy.context.evaluated_depsgraph_get()
    parts, failed, empty = [], [], []
    for ob in objs:
        part, perr = _dv_eval_part(ob, dg)
        if part is None:
            failed.append({"name": ob.name, "why": perr})
            continue
        if part["triangles"] == 0:
            empty.append(ob.name)
            continue
        if part["degenerate"]:
            warn.append("%s：%d 个退化三角面（法线给了确定性占位 (0,0,1)）" % (ob.name, part["degenerate"]))
        parts.append(part)
    if failed:
        warn.append("求值失败（已跳过）：%s" % _dv_j(failed))
    if empty:
        warn.append("无三角面（已跳过）：%s" % ", ".join(empty))
    if not parts:
        return _dv_j({"ok": False, "error": "挑到的对象里 0 个有三角面 —— 空产出不接受（求值失败/空网格）",
                   "objects": [o.name for o in objs], "failed": failed, "empty": empty, "dir": out_dir})

    # ---- 材质（每个对象一条 usemtl；同材质对象共用同一条 newmtl）+ 基础色
    mats, by_src, guessed, ref_by_key = [], {}, [], {}
    for p in parts:
        ob = bpy.data.objects.get(p["name"])
        if ob is not None:
            mname, kd, src, rough, metal, is_guess = _dv_material_of(ob)
        else:
            mname, kd, src, rough, metal, is_guess = None, _dv_grey_seed(p["name"]), "deterministic_grey", None, None, True
        key = mname if mname else ("__nomat__:" + p["name"])
        ref = ref_by_key.get(key)
        if ref is None:
            ref = _dv_mat_ref(mname if mname else ("mat_" + _dv_safe(p["name"], "part")),
                           set(m["ref"] for m in mats))
            ref_by_key[key] = ref
            mats.append({"ref": ref, "src": mname, "kd": [round(float(kd[0]), 6), round(float(kd[1]), 6), round(float(kd[2]), 6)],
                         "source": src, "rough": None if rough is None else round(float(rough), 6),
                         "metal": None if metal is None else round(float(metal), 6), "objects": [p["name"]]})
        else:
            for m in mats:
                if m["ref"] == ref:
                    m["objects"].append(p["name"])
                    break
        p["material"] = mname
        p["material_ref"] = ref
        p["material_source"] = src
        p["kd"] = [round(float(kd[0]), 6), round(float(kd[1]), 6), round(float(kd[2]), 6)]
        p["guessed"] = bool(is_guess)
        by_src[src] = by_src.get(src, 0) + 1
        if is_guess:
            guessed.append({"object": p["name"], "material": mname, "kd": p["kd"],
                            "why": "对象没有材质槽或颜色读不出来 → 由名字 md5 派生的确定性灰阶（不是真值）"})

    # ---- bbox → normalize（归档铁律：scale/center/原 bbox 三件套必须留）
    all_verts = []
    for p in parts:
        all_verts.extend(p["verts"])
    lo, hi = _dv_bbox(all_verts)
    size = [hi[i] - lo[i] for i in range(3)]
    center = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
    longest = max(size) if size else 0.0
    norm = {"applied": False, "target_half_extent": the,
            "bbox_before_min": [_dv_r(v) for v in lo], "bbox_before_max": [_dv_r(v) for v in hi],
            "bbox_before_size": [_dv_r(v) for v in size], "center": [_dv_r(v) for v in center],
            "scale": 1.0, "center_offset": [0.0, 0.0, 0.0],
            "recover": "原始坐标 p_orig = p_out / scale + center（scale=1、center=0 即未归一化）"}
    if normalize:
        if not (longest > 0):
            return _dv_j({"ok": False, "error": "零尺寸包围盒（几何塌成一点/共面到零）：归一化无从谈起",
                       "bbox_size": [_dv_r(v) for v in size], "dir": out_dir})
        scale = (2.0 * the) / longest
        center = [lo[i] + size[i] * 0.5 for i in range(3)]
        for p in parts:
            p["verts"] = [[(c[0] - center[0]) * scale, (c[1] - center[1]) * scale, (c[2] - center[2]) * scale]
                          for c in p["verts"]]
        lo2, hi2 = _dv_bbox([c for p in parts for c in p["verts"]])
        norm.update({"applied": True, "scale": _dv_r(scale, 9), "center": [_dv_r(v) for v in center],
                     "center_offset": [_dv_r(-v) for v in center],
                     "bbox_after_min": [_dv_r(v) for v in lo2], "bbox_after_max": [_dv_r(v) for v in hi2],
                     "bbox_after_size": [_dv_r(hi2[i] - lo2[i]) for i in range(3)],
                     "formula": "p_out = (p_orig - center) * scale",
                     "recover": "原始坐标 p_orig = p_out / scale + center（scale=%.9g）" % scale})
    else:
        norm.update({"bbox_after_min": norm["bbox_before_min"], "bbox_after_max": norm["bbox_before_max"],
                     "bbox_after_size": norm["bbox_before_size"],
                     "note": "normalize=False：文件里的尺度即原始场景尺度（scale=1、center=(0,0,0)）"})

    units = _dv_units_info() if units_note else {"suppressed": True, "reason": "units_note=False（归档铁律要求数值带单位，verify 会判 FAIL）"}

    n_tri = sum(len(p["faces"]) for p in parts)
    n_v = sum(len(p["verts"]) for p in parts)
    head = ["# DSH deliver v%d  name=%s  fmt=%s" % (DELIVER_VERSION, nm, f),
            "# objects=%d triangles=%d vertices=%d" % (len(parts), n_tri, n_v)]
    if units_note:
        head.append("# units: " + units["note"])
    if norm.get("applied"):
        head.append("# normalize: scale=%.9g center=(%.6g, %.6g, %.6g) target_half_extent=%.6g  →  p_orig = p_out/scale + center"
                    % (norm["scale"], center[0], center[1], center[2], the))
    else:
        head.append("# normalize: 未做（文件尺度 = 原始场景尺度）")
    head.append("# axes: Blender 世界系 Z-up 原样写出（未做 Y-up 转换）；法线为几何面法线")

    files = {}
    try:
        if f == "obj":
            obj_path = os.path.join(out_dir, nm + ".obj")
            mtl_name = (nm + ".mtl") if per_part_materials else None
            info = _dv_write_obj(obj_path, parts, head, mtl_name)
            files["obj"] = {"role": "obj", "path": _dv_win(obj_path), "bytes": os.path.getsize(obj_path),
                            "md5": _dv_md5(obj_path), "triangles": n_tri, "vertices": info["vertices"],
                            "normals": info["normals"], "groups": info["groups"], "usemtl": info["usemtl"]}
            if mtl_name:
                mtl_path = os.path.join(out_dir, mtl_name)
                minfo = _dv_write_mtl(mtl_path, mats, ["# DSH deliver v%d  name=%s" % (DELIVER_VERSION, nm),
                                                    "# 对象 → 材质映射见 %s_manifest.json" % nm])
                files["mtl"] = {"role": "mtl", "path": _dv_win(mtl_path), "bytes": os.path.getsize(mtl_path),
                                "md5": _dv_md5(mtl_path), "materials": minfo["materials"]}
        else:
            stl_path = os.path.join(out_dir, nm + ".stl")
            sinfo = _dv_write_stl(stl_path, parts, nm)
            files["stl"] = {"role": "stl", "path": _dv_win(stl_path), "bytes": sinfo["bytes"], "md5": _dv_md5(stl_path),
                            "triangles": sinfo["triangles"], "groups": len(parts)}
            if per_part_materials:
                warn.append("fmt='stl'：STL 不携带材质（per_part_materials 对 STL 无效，已忽略）")
    except Exception as e:
        return _dv_j({"ok": False, "error": "写文件失败（%s）：%s" % (type(e).__name__, e), "dir": out_dir})

    objects_rec = [{"name": p["name"], "triangles": len(p["faces"]), "vertices": len(p["verts"]),
                    "material": p.get("material"), "material_ref": p.get("material_ref"),
                    "material_source": p.get("material_source"), "kd": p.get("kd"), "guessed": p.get("guessed")}
                   for p in parts]
    counts = {"objects": len(parts), "triangles": n_tri, "vertices": n_v, "materials": len(mats),
              "groups": len(parts), "usemtl": len(set(p["material_ref"] for p in parts))}
    result = {"ok": True, "dir": out_dir, "name": nm, "fmt": f, "files": files, "counts": counts,
              "objects": objects_rec, "materials": mats, "normalize": norm, "units": units,
              "guessed_colors": guessed, "material_color_sources": by_src, "excluded_hidden": excluded,
              "skipped_empty": empty, "missing_objects": missing, "warnings": warn,
              "boundary": _DV_BOUNDARY, "ms": int((time.perf_counter() - t0) * 1000)}
    if gate_rec is not None:
        result["gate"] = gate_rec
    else:
        result["gate_warning"] = "require_gate=false（未强制过门）"

    if manifest:
        man = {"tool": "dsh-deliver", "api_version": DELIVER_VERSION, "generated_at": _dv_now(),
               "name": nm, "fmt": f, "dir": out_dir,
               "blender": bpy.app.version_string, "scene": bpy.context.scene.name if bpy.context.scene else None,
               "blend_filepath": bpy.data.filepath or None,
               "files": [dict(v, name=os.path.basename(str(v["path"]))) for v in files.values()],
               "objects": objects_rec, "counts": counts,
               "materials": [{"ref": m["ref"], "src": m["src"], "kd": m["kd"], "source": m["source"],
                              "rough": m["rough"], "metal": m["metal"], "objects": m["objects"]} for m in mats],
               "units": units, "normalize": norm, "guessed_colors": guessed,
               "material_color_sources": by_src, "excluded_hidden": excluded,
               "warnings": warn, "boundary": _DV_BOUNDARY,
               "recovery_rule": "归档必须能反推原始尺度：p_orig = p_out / normalize.scale + normalize.center"}
        man_path = os.path.join(out_dir, nm + "_manifest.json")
        try:
            _dv_write_text(man_path, json.dumps(man, ensure_ascii=False, indent=2, default=str) + "\n")
        except Exception as e:
            result["ok"] = False
            result["error"] = "几何已写出，但 manifest 写失败（%s）：%s" % (type(e).__name__, e)
            return _dv_j(result)
        result["manifest"] = {"path": _dv_win(man_path), "bytes": os.path.getsize(man_path), "md5": _dv_md5(man_path)}
    else:
        warn.append("manifest=False：没有清单文件（verify 只能用内存里最近一次导出的记录做参考）")

    st = _dv_store()
    st["last"] = {"dir": out_dir, "name": nm, "fmt": f, "manifest": result.get("manifest", {}).get("path"),
                  "files": [dict(v, name=os.path.basename(str(v["path"]))) for v in files.values()],
                  "counts": counts, "normalize": norm, "units": units, "t": _dv_now(),
                  "authority": "内存记录（K.dsh_deliver.last）—— 只作参考；权威是磁盘上的 <name>_manifest.json"}
    st["exports"].append({"t": _dv_now(), "dir": out_dir, "name": nm, "fmt": f, "objects": counts["objects"],
                          "triangles": n_tri, "md5": {k: v.get("md5") for k, v in files.items()}})
    del st["exports"][:-50]
    result["hint"] = "交付前先 K.dsh_deliver_api['verify'](%r) 逐项核对（md5 / 面数 / g 与 usemtl 数量）" % out_dir
    return _dv_j(result)


