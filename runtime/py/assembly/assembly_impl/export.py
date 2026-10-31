# -*- coding: utf-8 -*-
"""Split from assembly.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .manifest import _load_manifest, _manifest_validate, _write_json
from .gate import asm_gate
from .merge import asm_merge
from .repair import _repair_flag

_ASM_EXPORT_FORMATS = ("glb", "fbx", "obj")
_ASM_EXPORT_LIMITS = [
    "GLB/FBX/OBJ 由 Blender 官方导出器写出（export_apply=True 会落地修改器）；材质/贴图随导出器规则，不保证与 .blend 逐字节一致",
    "manifest 的 bbox/counts 来自求值网格快照（导出瞬间），与导出器内部再求值可能有微小差异",
    "ledger 是 append-only 的本地审计流水（不含跨进程锁）；并发追加可能交错，只保证单次调用内事件顺序",
    "gate verdict 只反映导出前 asm_gate 的三态结论；degraded ≠ 通过",
    "OBJ 附带 .mtl（export_materials=true），是否齐全取决于材质节点能否被 OBJ 导出器理解",
]


def _asm_export_name(name):
    """交付名安全化：只留字母数字/._-（中文等替换成 _）；空 → 错误。"""
    raw = str(name or "").strip()
    if not raw:
        return None, "name 不能为空（用于 <name>.glb / <name>_manifest.json）"
    keep = []
    for ch in raw:
        keep.append(ch if (ch.isalnum() or ch in "._-") else "_")
    out = "".join(keep).strip("._-")
    if not out:
        return None, "name 安全化后为空：%r" % (name,)
    if out != raw:
        return out, "name 已安全化：%r → %r" % (raw, out)
    return out, None


def _asm_export_formats(formats):
    """formats 归一化（列表或逗号/空格分隔字符串；gltf→glb；去重保序）。"""
    if formats is None:
        return list(_ASM_EXPORT_FORMATS), None
    raw = formats
    if isinstance(raw, str):
        s = raw.replace(",", " ").replace(";", " ").replace("/", " ")
        raw = [x for x in s.split() if x]
    if not isinstance(raw, (list, tuple, set)):
        return None, "formats 必须是列表或逗号分隔字符串，收到 %r" % (formats,)
    out, bad = [], []
    for x in raw:
        t = str(x).strip().lower()
        if t in ("gltf", "gltf2", "gltf-binary"):
            t = "glb"
        if t not in _ASM_EXPORT_FORMATS:
            bad.append(str(x))
            continue
        if t not in out:
            out.append(t)
    if bad:
        return None, "formats 里有不支持的格式 %s（允许 glb/fbx/obj）" % bad
    if not out:
        return None, "formats 为空（至少给 glb/fbx/obj 之一）"
    return out, None


def _asm_md5_file(path):
    """真 md5（manifest/files/ledger 用）；读不到回 None（不抛）。"""
    try:
        h = hashlib.md5()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _asm_file_receipt(path):
    rec = {"path": str(path), "exists": False, "bytes": None, "md5": None}
    try:
        rec["exists"] = bool(os.path.isfile(path))
        if rec["exists"]:
            rec["bytes"] = int(os.path.getsize(path))
            rec["md5"] = _asm_md5_file(path)
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return rec


def _asm_verify_file(fmt, path):
    """按格式验文件真实性：GLB 魔数 / FBX Kaydara 头 / OBJ v & f 行；返回 ok + 证据。"""
    rec = {"fmt": str(fmt), "path": str(path), "ok": False, "exists": False,
           "bytes": None, "md5": None, "magic_ok": False}
    try:
        rec["exists"] = bool(os.path.isfile(path))
        if not rec["exists"]:
            rec["error"] = "文件不存在"
            return rec
        rec["bytes"] = int(os.path.getsize(path))
        if rec["bytes"] <= 0:
            rec["error"] = "文件为空（0 字节）"
            return rec
        rec["md5"] = _asm_md5_file(path)
        with open(path, "rb") as fh:
            head = fh.read(4096)
        if fmt == "glb":
            rec["magic_ok"] = head[:4] == b"glTF"
            if not rec["magic_ok"]:
                rec["error"] = "GLB 魔数不是 b'glTF'（拿到 %r）" % head[:4]
                return rec
        elif fmt == "fbx":
            rec["magic_ok"] = bool(head.startswith(b"Kaydara FBX Binary")
                                   or head.lstrip()[:3] in (b"; FB", b"FBX"))
            if not rec["magic_ok"]:
                rec["error"] = "FBX 头不像 Kaydara FBX Binary（拿到 %r）" % head[:16]
                return rec
        elif fmt == "obj":
            with open(path, "rb") as fh:
                chunk = fh.read(1 << 17)
            rec["magic_ok"] = bool((b"\nv " in chunk) or chunk.startswith(b"v ")
                                   or (b"\nvt " in chunk) or (b"\nf " in chunk))
            if not rec["magic_ok"]:
                rec["error"] = "OBJ 里没找到 v/vt/f 行"
                return rec
        else:
            rec["error"] = "未知格式: %s" % fmt
            return rec
        rec["ok"] = True
        return rec
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        return rec


def _asm_select_only(objs):
    """只选中要导出的对象（导出 use_selection=True）→ 返回可恢复的选择快照。"""
    snap = {"prev_selected": [], "prev_active": None}
    try:
        vl = bpy.context.view_layer
        act = vl.objects.active
        snap["prev_active"] = str(act.name) if act is not None else None
        snap["prev_selected"] = [str(ob.name) for ob in vl.objects if ob.select_get()]
        for ob in vl.objects:
            try:
                ob.select_set(False)
            except Exception:
                pass
        for ob in objs:
            try:
                ob.select_set(True)
            except Exception:
                pass
        if objs:
            vl.objects.active = objs[0]
    except Exception as e:
        snap["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return snap


def _asm_restore_selection(snap):
    try:
        vl = bpy.context.view_layer
        prev = set(snap.get("prev_selected") or [])
        for ob in vl.objects:
            try:
                ob.select_set(str(ob.name) in prev)
            except Exception:
                pass
        act = bpy.data.objects.get(str(snap.get("prev_active") or ""))
        if act is not None:
            vl.objects.active = act
    except Exception:
        pass


def _asm_ensure_addon(module):
    try:
        if module not in bpy.context.preferences.addons.keys():
            bpy.ops.preferences.addon_enable(module=module)
    except Exception:
        pass


def _asm_export_one(fmt, objs, path):
    """跑一次官方导出器（不改场景，只改选中状态，结束后恢复）。返回文件回执。"""
    t0 = time.perf_counter()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    sel = _asm_select_only(objs)
    try:
        if fmt == "glb":
            _asm_ensure_addon("io_scene_gltf2")
            if not hasattr(bpy.ops.export_scene, "gltf"):
                raise RuntimeError("glTF 导出器不可用（io_scene_gltf2 未启用且 bpy.ops.export_scene.gltf 不存在）")
            try:
                bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                                          export_apply=True, export_yup=True)
            except TypeError:
                bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True)
        elif fmt == "fbx":
            _asm_ensure_addon("io_scene_fbx")
            if not hasattr(bpy.ops.export_scene, "fbx"):
                raise RuntimeError("FBX 导出器不可用（io_scene_fbx 未启用且 bpy.ops.export_scene.fbx 不存在）")
            try:
                bpy.ops.export_scene.fbx(filepath=path, use_selection=True, add_leaf_bones=False,
                                         bake_anim=False, apply_scale_options="FBX_SCALE_ALL")
            except TypeError:
                bpy.ops.export_scene.fbx(filepath=path, use_selection=True)
        elif fmt == "obj":
            if hasattr(bpy.ops.wm, "obj_export"):
                try:
                    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True,
                                          apply_modifiers=True, export_materials=True)
                except TypeError:
                    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True)
            elif hasattr(bpy.ops.export_scene, "obj"):
                bpy.ops.export_scene.obj(filepath=path, use_selection=True, use_mesh_modifiers=True)
            else:
                raise RuntimeError("OBJ 导出器不可用（bpy.ops.wm.obj_export / export_scene.obj 都不存在）")
        else:
            raise RuntimeError("不支持的格式: %s" % fmt)
    finally:
        _asm_restore_selection(sel)
    rec = _asm_file_receipt(path)
    rec["ms"] = int((time.perf_counter() - t0) * 1000)
    return rec


def _asm_export_paths(out_dir, name, formats, include_receipt, ledger):
    p = {}
    for fmt in formats:
        p[fmt] = os.path.join(out_dir, name + "." + fmt)
    p["manifest"] = os.path.join(out_dir, name + "_manifest.json") if include_receipt else None
    p["ledger"] = os.path.join(out_dir, "merge_ledger.jsonl") if ledger else None
    return p


def _asm_export_slim(rec):
    if not isinstance(rec, dict):
        return None
    out = {k: rec.get(k) for k in ("ok", "path", "bytes", "md5", "magic_ok", "error", "ms")}
    v = rec.get("verify")
    if isinstance(v, dict):
        out["verify"] = {k: v.get(k) for k in ("ok", "fmt", "magic_ok", "error", "skipped", "reason")}
    return out


def _asm_gate_slim(gate):
    if not isinstance(gate, dict):
        return None
    return {"ok": gate.get("ok"), "verdict": gate.get("verdict"),
            "export_allowed": gate.get("export_allowed"),
            "trend": gate.get("trend"), "coverage": gate.get("coverage"),
            "gates": [{"id": g.get("id"), "state": g.get("state"),
                       "reason": str(g.get("reason") or "")[:160]}
                      for g in (gate.get("gates") or [])[:12] if isinstance(g, dict)],
            "offenders": (gate.get("offenders") or [])[:10],
            "error": gate.get("error")}


def _asm_merge_slim(rec):
    if not isinstance(rec, dict):
        return None
    out = {k: rec.get(k) for k in ("ok", "verdict", "dry_run", "manifest_md5", "sealed",
                                   "objects_total", "object_warnings", "error")}
    out["order"] = (rec.get("order") or [])[:80]
    out["outputs"] = rec.get("outputs")
    out["snapshot"] = rec.get("snapshot")
    out["gate"] = _asm_gate_slim(rec.get("gate"))
    return out


def _asm_ledger_append(path, events):
    """append-only JSONL：只以 "a" 打开，绝不重写历史。"""
    rec = {"ok": False, "path": str(path), "lines": 0, "bytes": None, "error": None}
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                rec["lines"] += 1
        rec["bytes"] = int(os.path.getsize(path))
        rec["ok"] = True
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    return rec


def _asm_mesh_summary(names, md, mmu):
    """导出对象 → manifest 用的 objects/counts/bbox（求值网格快照，单位与 mm 双份）。"""
    rows, missing = [], []
    lo = [None, None, None]
    hi = [None, None, None]
    tris = verts = 0
    for nm in names:
        m = md.get(nm)
        if not isinstance(m, dict):
            rows.append({"name": str(nm), "missing": True})
            missing.append(str(nm))
            continue
        try:
            a0 = [float(x) for x in m["aabb"][0]]
            a1 = [float(x) for x in m["aabb"][1]]
        except Exception:
            rows.append({"name": str(nm), "missing": "no_aabb"})
            missing.append(str(nm))
            continue
        ob = bpy.data.objects.get(str(nm))
        v = int(len(ob.data.vertices)) if (ob is not None and ob.data is not None) else None
        t = int(m.get("tris_n") or 0)
        tris += t
        if v:
            verts += v
        for k in range(3):
            lo[k] = a0[k] if lo[k] is None else min(lo[k], a0[k])
            hi[k] = a1[k] if hi[k] is None else max(hi[k], a1[k])
        rows.append({"name": str(nm), "verts": v, "tris": t,
                     "bbox_mm": {"min": [_r(a0[k] * mmu, 4) for k in range(3)],
                                 "max": [_r(a1[k] * mmu, 4) for k in range(3)],
                                 "size": [_r((a1[k] - a0[k]) * mmu, 4) for k in range(3)]}})
    bbox_units = bbox_mm = None
    if lo[0] is not None:
        bbox_units = {"min": [_r(x, 6) for x in lo], "max": [_r(x, 6) for x in hi],
                      "size": [_r(hi[k] - lo[k], 6) for k in range(3)]}
        bbox_mm = {"min": [_r(lo[k] * mmu, 4) for k in range(3)],
                   "max": [_r(hi[k] * mmu, 4) for k in range(3)],
                   "size": [_r((hi[k] - lo[k]) * mmu, 4) for k in range(3)]}
    return {"objects": rows[:200], "missing": missing[:60],
            "counts": {"objects": len(names), "verts": verts, "tris": tris},
            "bbox_units": bbox_units, "bbox_mm": bbox_mm}


def asm_export(manifest=None, scope=None, objects=None, out_dir=None, name="assembly",
               formats=["glb", "fbx", "obj"], merge=False, dry_run=False,
               include_receipt=True, ledger=True, verify=True):
    """装配交付导出：可选 N 路合并 → GLB/FBX/OBJ → <out_dir>/<name>_manifest.json + append-only merge_ledger.jsonl。

    流程：
      1) merge=true 且 dry_run=false：先 asm_merge(dry_run=false)（内部 gate + snapshot；不过门不导出）；
         merge=true 且 dry_run=true：只跑 asm_merge(dry_run=true) 拿计划。
      2) 导出 GLB（export_scene.gltf, GLB/use_selection/export_apply/export_yup）、
         FBX（export_scene.fbx）、OBJ（wm.obj_export，回退 export_scene.obj）；自动 enable io_scene_gltf2/io_scene_fbx。
      3) manifest：objects/bbox(单位+mm)/units/md5/files/gate/merge receipt；manifest_md5 自指纹。
      4) ledger：append-only JSONL，一行一个 merge/export 事件（ts/action/manifest_md5/files/md5/gate_verdict）。
    失败口径：坏参数 / 空场景 / 全部格式失败 ⇒ ok:false；部分格式失败 ⇒ ok:true + warnings。
    dry_run=true：只回计划（不建目录、不导出、不写 manifest/ledger）；场景只读。
    """
    t0 = time.perf_counter()
    merge, ferr = _repair_flag(merge, "merge")
    if ferr:
        return _err(ferr)
    dry_run, ferr = _repair_flag(dry_run, "dry_run")
    if ferr:
        return _err(ferr)
    include_receipt, ferr = _repair_flag(include_receipt, "include_receipt")
    if ferr:
        return _err(ferr)
    ledger, ferr = _repair_flag(ledger, "ledger")
    if ferr:
        return _err(ferr)
    verify, ferr = _repair_flag(verify, "verify")
    if ferr:
        return _err(ferr)
    fmts, ferr = _asm_export_formats(formats)
    if ferr:
        return _err(ferr)
    nm, nwarn = _asm_export_name(name)
    if nm is None:
        return _err(nwarn)
    warnings = [nwarn] if nwarn else []
    if out_dir is not None and not isinstance(out_dir, (str, os.PathLike)):
        return _err("out_dir 必须是路径字符串，收到 %r" % (out_dir,))
    if out_dir:
        od = _path(str(out_dir))
    else:
        K = _kernel()
        base = getattr(K, "out_dir", None) if K is not None else None
        od = _path(os.path.join(base or os.path.expanduser("~"), "asm_export"))
        warnings.append("out_dir 未给 → 默认 %s" % od)
    m = None
    if manifest is not None:
        m, merr = _load_manifest(manifest)
        if merr:
            return _err(merr)
        mval = _manifest_validate(m, check_files=bool(merge), check_md5=bool(merge))
        if not mval["ok"]:
            return _err("manifest 校验失败（坏清单上不导出）", errors=mval["errors"][:10],
                        warnings=mval["warnings"][:10])
    if merge and m is None:
        return _err("merge=true 必须给 manifest（N 路合并需要零件清单）")
    merge_out_blend = os.path.join(od, nm + "_merged.blend")
    merge_rec = None
    if merge:
        merge_call = dict(manifest=m, out_blend=merge_out_blend, dry_run=bool(dry_run))
        raw_merge = asm_merge(**merge_call)
        merge_rec = json.loads(raw_merge) if isinstance(raw_merge, str) else raw_merge
        if not isinstance(merge_rec, dict) or not merge_rec.get("ok"):
            gv = ((merge_rec or {}).get("gate") or {}).get("verdict") if isinstance(merge_rec, dict) else None
            body = {"ok": False, "verdict": gv or "fail", "dry_run": bool(dry_run),
                    "stage": "merge", "dir": od, "name": nm, "formats": fmts,
                    "files": {}, "counts": {"objects": 0, "files": 0},
                    "gate": _asm_gate_slim((merge_rec or {}).get("gate")),
                    "merge": _asm_merge_slim(merge_rec),
                    "md5": {}, "warnings": warnings,
                    "error": "合并失败/装配门未通过 ⇒ 不导出",
                    "verdict_line": "asm_export：合并阶段失败（%s）→ 未导出任何文件"
                                    % str((merge_rec or {}).get("error"))[:160],
                    "hint": "先看 merge.gate / merge.error；修 manifest/几何后重跑；merge 失败不保存 .blend",
                    "honest_limits": list(_ASM_EXPORT_LIMITS)}
            return _j(_receipt(body, t0))
    # ---- 对象解析 ----
    names = []
    objs = []
    info = None
    if merge and dry_run:
        for p in merge_rec.get("parts") or []:
            tn = p.get("target_names") if isinstance(p, dict) else None
            if isinstance(tn, dict):
                names.extend(str(v) for v in tn.values())
        if not names:
            names = [str(x.get("object")) for x in (merge_rec.get("merged") or [])
                     if isinstance(x, dict) and x.get("object")]
        if not names:
            return _err("合并计划里没有可导出的对象（空合并）", merge=_asm_merge_slim(merge_rec))
    else:
        if merge:
            names = [str(x.get("object")) for x in (merge_rec.get("merged") or [])
                     if isinstance(x, dict) and x.get("object")]
            if not names:
                return _err("合并完成但回执里没有 merged 对象（异常）", merge=_asm_merge_slim(merge_rec))
        info = _pick_objects(scope, objects, include_hidden=False)
        if info.get("error"):
            return _err(info["error"], objects=info.get("names"), missing=info.get("missing"),
                        hint="空场景/坏参数必须失败；确认对象名/集合名")
        names = list(info["names"])
        objs = list(info["objects"])
    gate = None
    if merge and isinstance(merge_rec.get("gate"), dict):
        gate = merge_rec.get("gate")
    elif not (merge and dry_run):
        try:
            raw_g = asm_gate(objects=list(names), manifest=m, summary_only=True, strict=True)
            gate = json.loads(raw_g) if isinstance(raw_g, str) else raw_g
        except Exception as e:
            gate = {"ok": False, "verdict": "degraded",
                    "error": "asm_gate 异常: %s: %s" % (type(e).__name__, str(e)[:200])}
    gate_slim = _asm_gate_slim(gate)
    merge_slim = _asm_merge_slim(merge_rec)
    planned = _asm_export_paths(od, nm, fmts, include_receipt, ledger)
    if merge:
        planned["blend"] = merge_out_blend
    # ---- dry_run：只回计划 ----
    if dry_run:
        body = {"ok": True, "verdict": "planned", "dry_run": True, "readonly": True,
                "stage": "planned", "dir": od, "name": nm, "formats": fmts,
                "files": planned, "objects": list(names)[:200],
                "counts": {"objects": len(names), "formats": len(fmts)},
                "gate": gate_slim, "merge": merge_slim, "md5": {}, "manifest_md5": None,
                "warnings": warnings + (["merge dry_run：gate 跳过（gate=skipped_dry_run）"] if merge else []),
                "verify": bool(verify),
                "verdict_line": "asm_export（dry_run，只读）：计划 %d 件 → %s；不建目录/不导出/不写账本"
                                % (len(names), "/".join(fmts)),
                "hint": "dry_run 只回计划；确认后传 dry_run=false（merge=true 会先 asm_merge(dry_run=false)）",
                "honest_limits": list(_ASM_EXPORT_LIMITS)}
        return _j(_receipt(body, t0))
    # ---- 真实导出 ----
    try:
        os.makedirs(od, exist_ok=True)
    except Exception as e:
        return _err("产物目录建不出来: %s: %s" % (type(e).__name__, str(e)[:200]), dir=od)
    exports, files, md5map = {}, {}, {}
    ok_formats, failed = [], []
    for fmt in fmts:
        path = planned[fmt]
        try:
            rec = _asm_export_one(fmt, objs, path)
            if verify:
                v = _asm_verify_file(fmt, path)
                rec["verify"] = v
                rec["magic_ok"] = v.get("magic_ok")
                rec["ok"] = bool(v.get("ok"))
                if rec.get("md5") is None:
                    rec["md5"] = v.get("md5")
                if not rec["ok"]:
                    rec["error"] = v.get("error") or "verify 失败"
            else:
                rec["verify"] = {"skipped": True, "reason": "verify=false（只查存在与非空）"}
                rec["ok"] = bool(rec.get("exists") and (rec.get("bytes") or 0) > 0)
                if not rec["ok"]:
                    rec["error"] = "文件不存在或为空"
        except Exception as e:
            rec = {"ok": False, "path": path,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "verify": {"skipped": True, "reason": "导出异常"}}
        exports[fmt] = rec
        files[fmt] = path
        if rec.get("ok"):
            ok_formats.append(fmt)
            md5map[fmt] = rec.get("md5")
            if fmt == "obj":
                mtl = os.path.splitext(path)[0] + ".mtl"
                if os.path.isfile(mtl):
                    files["obj_mtl"] = mtl
                    md5map["obj_mtl"] = _asm_md5_file(mtl)
        else:
            failed.append(fmt)
            warnings.append("%s 导出/校验失败：%s" % (fmt, str(rec.get("error"))[:180]))
    mmu = float(_KIT.units())
    md, md_errors = _mesh_map(objs)
    summary = _asm_mesh_summary(names, md, mmu)
    if summary.get("missing"):
        warnings.append("以下对象没有求值网格（manifest 里标 missing）：%s" % summary["missing"][:10])
    manifest_path = planned.get("manifest")
    ledger_path = planned.get("ledger")
    if include_receipt:
        files["manifest"] = manifest_path
    if ledger:
        files["ledger"] = ledger_path
    if merge:
        files["blend"] = ((merge_slim or {}).get("outputs") or {}).get("blend") or merge_out_blend
    mid = None
    if include_receipt:
        manifest = {
            "schema": "dsh-assembly-export-manifest/1", "tool": "assembly.asm_export",
            "version": ASSEMBLY_VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "name": nm, "dir": od, "units": {"mm_per_unit": _r(mmu, 6)},
            "formats": fmts, "formats_ok": ok_formats, "formats_failed": failed,
            "objects": summary["objects"], "missing": summary.get("missing") or [],
            "counts": dict(summary["counts"], files=len(ok_formats)),
            "bbox_units": summary.get("bbox_units"), "bbox_mm": summary.get("bbox_mm"),
            "gate": gate_slim, "merge": merge_slim,
            "files": {k: {"path": v, "name": os.path.basename(str(v)),
                          "bytes": (exports.get(k) or {}).get("bytes"),
                          "md5": (exports.get(k) or {}).get("md5"),
                          "verify": _asm_export_slim(exports.get(k)) or {}}
                      for k, v in files.items() if k in ("glb", "fbx", "obj", "obj_mtl") and v},
            "exports": {k: _asm_export_slim(v) for k, v in exports.items()},
            "warnings": warnings,
            "honest_limits": list(_ASM_EXPORT_LIMITS),
        }
        # files 里 verify 字段瘦身（避免 manifest 嵌套过深）
        for k, v in (manifest.get("files") or {}).items():
            if isinstance(v.get("verify"), dict):
                v["verify"] = {kk: v["verify"].get(kk) for kk in ("ok", "magic_ok", "bytes", "md5")}
        manifest["manifest_md5"] = _md5_text(_canon({k: v for k, v in manifest.items()
                                                     if k != "manifest_md5"}))
        mid = manifest["manifest_md5"]
        try:
            _write_json(manifest_path, manifest)
            md5map["manifest"] = _asm_md5_file(manifest_path)
        except Exception as e:
            warnings.append("manifest 写失败：%s: %s" % (type(e).__name__, str(e)[:200]))
            files["manifest"] = None
    ledger_rec = None
    if ledger:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        events = []
        if merge and merge_slim is not None:
            events.append({
                "ts": ts, "action": "merge", "name": nm,
                "manifest_md5": merge_slim.get("manifest_md5"),
                "files": [os.path.basename(str(merge_slim.get("outputs", {}).get("blend") or merge_out_blend))]
                if isinstance(merge_slim.get("outputs"), dict) else [os.path.basename(merge_out_blend)],
                "md5": {"blend": (merge_slim.get("outputs") or {}).get("md5")
                        if isinstance(merge_slim.get("outputs"), dict) else None},
                "gate_verdict": (merge_slim.get("gate") or {}).get("verdict"),
                "objects": merge_slim.get("objects_total"),
                "tool": "asm_merge", "version": ASSEMBLY_VERSION})
        events.append({
            "ts": ts, "action": "export", "name": nm, "dir": od,
            "manifest_md5": mid,
            "files": [os.path.basename(str(files[k])) for k in ok_formats if files.get(k)],
            "md5": {k: md5map.get(k) for k in ok_formats},
            "gate_verdict": (gate_slim or {}).get("verdict"),
            "objects": summary["counts"].get("objects"),
            "formats": ok_formats, "failed": failed,
            "tool": "asm_export", "version": ASSEMBLY_VERSION})
        ledger_rec = _asm_ledger_append(ledger_path, events)
        if not ledger_rec.get("ok"):
            warnings.append("ledger 追加失败：%s" % str(ledger_rec.get("error"))[:200])
            files["ledger"] = None
        else:
            md5map["ledger"] = _asm_md5_file(ledger_path)
    if not ok_formats:
        verdict = "fail"
        warnings.append("全部格式都失败 ⇒ ok:false（看 exports[].error）")
    else:
        verdict = (gate_slim or {}).get("verdict") or "unverified"
        if verdict != "pass":
            warnings.append("gate verdict=%s（≠ pass）：文件已导出但不可直接当作出厂件交付" % verdict)
    ok = bool(ok_formats)
    body = {"ok": ok, "verdict": verdict, "dry_run": False, "stage": "exported",
            "dir": od, "name": nm, "formats": fmts,
            "formats_ok": ok_formats, "formats_failed": failed,
            "files": files, "counts": dict(summary["counts"], files=len(ok_formats)),
            "objects": list(names)[:200], "gate": gate_slim, "merge": merge_slim,
            "md5": md5map, "manifest_md5": mid,
            "exports": {k: _asm_export_slim(v) for k, v in exports.items()},
            "warnings": warnings, "verify": bool(verify), "ledger": ledger_rec,
            "verdict_line": ("asm_export：%d 件 → %s（成功 %s / 失败 %s）· gate=%s · manifest=%s · ledger=%s"
                             % (len(names), "/".join(fmts), "/".join(ok_formats) or "无",
                                "/".join(failed) or "无", verdict,
                                "written" if mid else "skipped",
                                "appended" if (ledger_rec or {}).get("ok") else "skipped")),
            "hint": ("导出完成：先读 verdict（degraded≠通过）与 warnings；manifest 里有逐文件 md5/bbox")
                    if ok else "全部格式失败：看 exports[].error（GLB 需 io_scene_gltf2 / FBX 需 io_scene_fbx）",
            "honest_limits": list(_ASM_EXPORT_LIMITS), "md_errors": md_errors}
    return _j(_receipt(body, t0))
