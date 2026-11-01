# -*- coding: utf-8 -*-
"""asm_export 常量与辅助函数（从 export.py 原样搬移，行为不变）。"""
from ..common import *  # noqa: F401,F403

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


__all__ = [name for name in list(globals()) if not name.startswith("__")]
