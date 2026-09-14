# -*- coding: utf-8 -*-
"""DSH 事务 / 回滚（v0.7.0）—— 两级：文件级快照 + 对象级 mark/revert。

API 挂 K.dsh_txn_api。设计取舍（按外部反馈"没有事务/回滚"落地）：

  文件级  snapshot(label) / restore(label)
      snapshot 用 bpy.ops.wm.save_as_mainfile(copy=True) —— **不改当前 filepath**，
      文件落在 <K.out_dir>/snapshots/<label>.blend；restore 用 open_mainfile（**会丢掉当前未保存状态**）。
      适用："整个场景推倒重来之前先留个点"；代价：MB 级写盘（大场景明显）。

  对象级  mark(label, objects=[...]) / revert(label)
      只把对象的 transform / 材质槽 / 可见性 / 修改器参数记进内存（JSON 友好），revert 就地回写。
      适用："只改一处再对比"；**边界：不含拓扑改动**（Boolean / 合并 / 删面之后回不去），
      也不含 uv / 顶点位置变化 —— 需要那种级别的回滚请用文件级快照。

用法（GUI 或 headless 都能跑）：
    K.dsh_txn_api["snapshot"]("before_join")
    K.dsh_txn_api["mark"]("iter7", ["Cube", "Wheel_L"])
    ... 改动 ...
    K.dsh_txn_api["revert"]("iter7")     # 快速回到 mark 时的状态
    K.dsh_txn_api["restore"]("before_join")
"""
import bpy
import json
import os
import time

TXN_VERSION = 2


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _store():
    K = _kernel()
    if K is None:
        raise RuntimeError("需要持久内核 K（走 blender_rt_* 通道）")
    if not hasattr(K, "dsh_txn"):
        K.dsh_txn = {"snapshots": {}, "marks": {}}
    return K.dsh_txn


def _out_dir():
    K = _kernel()
    d = getattr(K, "out_dir", None) or os.path.join(os.path.expanduser("~"), "dsh_out")
    return d


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 文件级

def t_snapshot(label=None, note=""):
    st = _store()
    d = os.path.join(_out_dir(), "snapshots")
    os.makedirs(d, exist_ok=True)
    name = str(label) if label else time.strftime("snap_%Y%m%d_%H%M%S")
    path = os.path.join(d, name + ".blend")
    t0 = time.perf_counter()
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "path": path})
    ms = int((time.perf_counter() - t0) * 1000)
    rec = {"label": name, "path": path, "bytes": os.path.getsize(path) if os.path.isfile(path) else None,
           "ms": ms, "objects": len(bpy.data.objects), "note": str(note), "t": _now()}
    st["snapshots"][name] = rec
    return _j({"ok": True, "snapshot": rec, "total": len(st["snapshots"])})


def t_restore(label):
    st = _store()
    rec = st["snapshots"].get(str(label))
    if not rec:
        return _j({"ok": False, "error": "没有这个快照: %s" % label, "have": sorted(st["snapshots"])})
    path = rec["path"]
    if not os.path.isfile(path):
        return _j({"ok": False, "error": "快照文件不存在: %s" % path})
    t0 = time.perf_counter()
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "path": path})
    ms = int((time.perf_counter() - t0) * 1000)
    return _j({"ok": True, "restored": rec, "ms": ms, "objects": len(bpy.data.objects),
               "warning": "已用快照替换当前文件；restore 之前的未保存修改已丢失", "filepath": bpy.data.filepath})


def t_list():
    st = _store()
    snaps = sorted(st["snapshots"].values(), key=lambda r: r.get("t", ""))
    return _j({"ok": True, "snapshots": snaps, "marks": sorted(st["marks"]),
               "out_dir": _out_dir(),
               "snapshot_dir": os.path.join(_out_dir(), "snapshots")})


def t_prune(keep=5):
    st = _store()
    keep = max(1, int(keep or 5))
    snaps = sorted(st["snapshots"].values(), key=lambda r: r.get("t", ""))
    removed = []
    for rec in snaps[:-keep]:
        try:
            if os.path.isfile(rec["path"]):
                os.remove(rec["path"])
            removed.append(rec["label"])
        except Exception as e:
            pass
        st["snapshots"].pop(rec["label"], None)
    return _j({"ok": True, "removed": removed, "kept": sorted(st["snapshots"])})


# ---------------------------------------------------------------- 对象级

def _snap_obj(ob):
    d = {
        "name": ob.name,
        "loc": [round(v, 6) for v in ob.location],
        "rot": [round(v, 6) for v in ob.rotation_euler],
        "scale": [round(v, 6) for v in ob.scale],
        "hide_viewport": bool(ob.hide_viewport),
        "hide_render": bool(ob.hide_render),
        "hide_get": bool(ob.hide_get()) if hasattr(ob, "hide_get") else None,
        "materials": [m.name if m else None for m in ob.data.materials] if getattr(ob, "data", None) and hasattr(ob.data, "materials") else None,
        "modifiers": [{"name": m.name, "type": m.type, "show_viewport": bool(m.show_viewport),
                       "show_render": bool(m.show_render)} for m in ob.modifiers],
    }
    if ob.type == "MESH" and ob.data is not None:
        d["verts"] = len(ob.data.vertices)
        d["polys"] = len(ob.data.polygons)
    return d


def t_mark(label, objects=None):
    st = _store()
    name = str(label)
    if objects:
        obs = [bpy.data.objects.get(str(n)) for n in objects]
        obs = [o for o in obs if o is not None]
    else:
        obs = [o for o in bpy.data.objects]
    rec = {"label": name, "t": _now(), "objects": {o.name: _snap_obj(o) for o in obs},
           "count": len(obs), "scene": bpy.context.scene.name if bpy.context.scene else None}
    st["marks"][name] = rec
    return _j({"ok": True, "mark": name, "objects": len(obs), "note": "对象级：只有 transform/材质/可见性/修改器开关；不含拓扑改动"})


def t_revert(label, strict=False):
    st = _store()
    rec = st["marks"].get(str(label))
    if not rec:
        return _j({"ok": False, "error": "没有这个 mark: %s" % label, "have": sorted(st["marks"])})
    changed, missing, blocked = [], [], []
    for oname, snap in rec["objects"].items():
        ob = bpy.data.objects.get(oname)
        if ob is None:
            missing.append(oname)
            continue
        if snap.get("verts") is not None and ob.type == "MESH" and ob.data is not None:
            if len(ob.data.vertices) != snap["verts"]:
                blocked.append({"name": oname, "why": "拓扑已变（顶点数 %d → %d）" % (snap["verts"], len(ob.data.vertices))})
                continue
        before = _snap_obj(ob)
        try:
            ob.location = snap["loc"]
            ob.rotation_euler = snap["rot"]
            ob.scale = snap["scale"]
            ob.hide_viewport = snap["hide_viewport"]
            ob.hide_render = snap["hide_render"]
            if snap.get("hide_get") is not None and hasattr(ob, "hide_set"):
                ob.hide_set(snap["hide_get"])
            if snap.get("materials") is not None and getattr(ob, "data", None) is not None and hasattr(ob.data, "materials"):
                for i, mname in enumerate(snap["materials"]):
                    if i < len(ob.data.materials):
                        ob.data.materials[i] = bpy.data.materials.get(mname) if mname else None
            for mi, msnap in enumerate(snap.get("modifiers") or []):
                if mi < len(ob.modifiers):
                    ob.modifiers[mi].show_viewport = msnap["show_viewport"]
                    ob.modifiers[mi].show_render = msnap["show_render"]
        except Exception as e:
            blocked.append({"name": oname, "why": "%s: %s" % (type(e).__name__, e)})
            continue
        after = _snap_obj(ob)
        if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
            changed.append(oname)
    bpy.context.view_layer.update()
    return _j({"ok": True, "reverted": str(label), "changed": changed, "changed_count": len(changed),
               "missing_objects": missing, "blocked": blocked,
               "boundary": "对象级回滚不含拓扑/UV/顶点级改动；需要那种回滚请用 snapshot/restore"})


def t_marks():
    st = _store()
    return _j({"ok": True, "marks": [{"label": k, "objects": v["count"], "t": v["t"]} for k, v in st["marks"].items()]})


def t_drop(label):
    st = _store()
    hit = st["marks"].pop(str(label), None)
    return _j({"ok": True, "dropped": bool(hit), "label": str(label), "remaining": sorted(st["marks"])})


def t_help():
    return _j({
        "version": TXN_VERSION,
        "file_level": {"snapshot": "snapshot(label=None, note='') → 存 <out_dir>/snapshots/<label>.blend（copy=True，不动当前 filepath）",
                       "restore": "restore(label) → open_mainfile（会丢掉当前未保存状态）",
                       "list": "list() → 快照 + mark 清单", "prune": "prune(keep=5) → 只留最近 N 个快照"},
        "object_level": {"mark": "mark(label, objects=None) → 记 transform/材质/可见性/修改器开关",
                         "revert": "revert(label, strict=False) → 就地回写；拓扑变过的对象会被跳过并报告",
                         "marks": "marks() → 列出 mark", "drop": "drop(label) → 删掉 mark"},
        "boundary": "对象级不含拓扑/UV/顶点改动；文件级是完整回滚但有写盘成本",
        "usage": "K.dsh_txn_api[...]；也可用工具 blender_rt_txn(op=...)",
    })


def t_dispatch(op, args=None):
    """统一入口：工具侧发 {op, args}"""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    fn = t_ops().get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown txn op", "op": op, "ops": sorted(t_ops())})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(kw), "sig_hint": t_help()})


def t_ops():
    return {"snapshot": t_snapshot, "restore": t_restore, "list": t_list, "prune": t_prune,
            "mark": t_mark, "revert": t_revert, "marks": t_marks, "drop": t_drop, "help": t_help}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_txn_api = {"version": TXN_VERSION, "dispatch": t_dispatch, "snapshot": t_snapshot, "restore": t_restore,
                      "list": t_list, "prune": t_prune, "mark": t_mark, "revert": t_revert,
                      "marks": t_marks, "drop": t_drop, "help": t_help}
