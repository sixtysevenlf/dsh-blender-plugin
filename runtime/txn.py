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

  待定编辑（v0.9.0 / #12）  edit_begin / edit_check / edit_accept / edit_revert / edit_status
      把"改一步看一眼"升格成协议：**编辑必须先验证再接受**（上游 Procedura src/tools/edit-transaction.ts
      的 accept_edit / revert_edit 落地到 bpy）。
        edit_begin(label, op, targets, note)  → 记下目标对象当时的 transform/材质/可见性快照，挂一条 pending
        edit_check(kind, result, note)        → 登记一次复核（kind = audit / qc / measure / render ...）
        edit_accept(verified)                 → verified **必填**（一行说清"看了什么、确认了什么"），
                                                且**至少一条 check** 才放行；通过后清 pending、入历史
        edit_revert(why)                      → why 必填；回滚到 begin 时的对象状态；
                                                **免预算但有上限**（口径见 _txn_revert_cap：上限 = max(2, 历史步数)），
                                                并防"改一次撤一次"的抖动（同 label 连续 revert 直接拒）
        edit_status()                         → 当前 pending（label/目标/开始时间/check 数）+ 接受/撤销计数 + 剩余额度
      **同一时间只允许一条 pending**：已有 pending 时 edit_begin 返回 ok:false，让调用者先 accept 或 revert。
      与文件级快照的关系：edit_begin **不做** .blend 快照（大场景有 MB 级写盘成本）；但在返回体里给提示 ——
      **拓扑类改动（Boolean / 合并 / 删面 / apply_transform）必须先 t_snapshot(...) 留文件级回退点**，
      因为对象级快照回不去拓扑。

用法（GUI 或 headless 都能跑）：
    K.dsh_txn_api["snapshot"]("before_join")
    K.dsh_txn_api["mark"]("iter7", ["Cube", "Wheel_L"])
    ... 改动 ...
    K.dsh_txn_api["revert"]("iter7")     # 快速回到 mark 时的状态
    K.dsh_txn_api["restore"]("before_join")

    # 待定编辑（#12）：改 → 复核 → 接受 / 撤销
    K.dsh_txn_api["edit_begin"]("tenon_12mm", "edit", ["Backrest"])
    ... 改 ...
    K.dsh_txn_api["edit_check"]("render", {"view": "iso", "note": "头进去了"})
    K.dsh_txn_api["edit_accept"]("iso 视角比对目标：榫头进 12mm，方向对，无脱离")
"""
import bpy
import json
import os
import time

TXN_VERSION = 3

# 对象级回滚的边界（原文照抄给调用者，别让人以为 revert 能回去拓扑）
_TXN_BOUNDARY = ("对象级快照/回滚只覆盖 transform / 材质槽 / 可见性 / 修改器开关；"
            "不含拓扑（Boolean / 合并 / 删面 / apply_transform）、UV、顶点位置 —— "
            "那类改动请先 t_snapshot(...) 用文件级回退点")
# 撤销额度下限：保证新会话至少能"改一次 → 撤一次"（上游 = Math.max(2, maxRefineSteps)）
_TXN_REVERT_FLOOR = 2
# 复核 kind 的已知取值（非标准 kind 仍记账，但会回一句"非标准"）
_TXN_CHECK_KINDS = ("audit", "qc", "measure", "render", "read", "compare", "other")


def _txn_j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _txn_kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _txn_store():
    K = _txn_kernel()
    if K is None:
        raise RuntimeError("需要持久内核 K（走 blender_rt_* 通道）")
    if not hasattr(K, "dsh_txn"):
        K.dsh_txn = {"snapshots": {}, "marks": {}}
    st = K.dsh_txn
    # v3 新键用 setdefault 补 —— K 跨调用/跨重载存活，老会话里的 store 可能还是 v2 的形状
    st.setdefault("snapshots", {})
    st.setdefault("marks", {})
    st.setdefault("pending", None)        # 当前待定编辑（同一时间最多一条）
    st.setdefault("edits", [])            # 已落定的编辑历史（accepted / reverted 各一条）
    st.setdefault("edit_steps", 0)        # edit_begin 的累计次数 —— "历史步数"的唯一口径
    st.setdefault("accepted", 0)
    st.setdefault("reverted", 0)
    st.setdefault("last_action", None)    # {"kind": "accept"|"revert", "label": ...} → 防抖判据
    return st


def _txn_out_dir():
    K = _txn_kernel()
    d = getattr(K, "out_dir", None) or os.path.join(os.path.expanduser("~"), "dsh_out")
    return d


def _txn_now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 文件级

def t_snapshot(label=None, note=""):
    st = _txn_store()
    d = os.path.join(_txn_out_dir(), "snapshots")
    os.makedirs(d, exist_ok=True)
    name = str(label) if label else time.strftime("snap_%Y%m%d_%H%M%S")
    path = os.path.join(d, name + ".blend")
    t0 = time.perf_counter()
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    except Exception as e:
        return _txn_j({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "path": path})
    ms = int((time.perf_counter() - t0) * 1000)
    rec = {"label": name, "path": path, "bytes": os.path.getsize(path) if os.path.isfile(path) else None,
           "ms": ms, "objects": len(bpy.data.objects), "note": str(note), "t": _txn_now()}
    st["snapshots"][name] = rec
    return _txn_j({"ok": True, "snapshot": rec, "total": len(st["snapshots"])})


def t_restore(label):
    st = _txn_store()
    rec = st["snapshots"].get(str(label))
    if not rec:
        return _txn_j({"ok": False, "error": "没有这个快照: %s" % label, "have": sorted(st["snapshots"])})
    path = rec["path"]
    if not os.path.isfile(path):
        return _txn_j({"ok": False, "error": "快照文件不存在: %s" % path})
    t0 = time.perf_counter()
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
    except Exception as e:
        return _txn_j({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "path": path})
    ms = int((time.perf_counter() - t0) * 1000)
    return _txn_j({"ok": True, "restored": rec, "ms": ms, "objects": len(bpy.data.objects),
               "warning": "已用快照替换当前文件；restore 之前的未保存修改已丢失", "filepath": bpy.data.filepath})


def t_list():
    st = _txn_store()
    snaps = sorted(st["snapshots"].values(), key=lambda r: r.get("t", ""))
    return _txn_j({"ok": True, "snapshots": snaps, "marks": sorted(st["marks"]),
               "out_dir": _txn_out_dir(),
               "snapshot_dir": os.path.join(_txn_out_dir(), "snapshots")})


def t_prune(keep=5):
    st = _txn_store()
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
    return _txn_j({"ok": True, "removed": removed, "kept": sorted(st["snapshots"])})


# ---------------------------------------------------------------- 对象级

def _txn_snap_obj(ob):
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
    st = _txn_store()
    name = str(label)
    if objects:
        obs = [bpy.data.objects.get(str(n)) for n in objects]
        obs = [o for o in obs if o is not None]
    else:
        obs = [o for o in bpy.data.objects]
    rec = {"label": name, "t": _txn_now(), "objects": {o.name: _txn_snap_obj(o) for o in obs},
           "count": len(obs), "scene": bpy.context.scene.name if bpy.context.scene else None}
    st["marks"][name] = rec
    return _txn_j({"ok": True, "mark": name, "objects": len(obs), "note": "对象级：只有 transform/材质/可见性/修改器开关；不含拓扑改动"})


def _txn_restore_objects(objects_snap):
    """把 {对象名: _txn_snap_obj 记录} 就地回写 → (changed, missing, blocked)。

    边界：只回写 transform / 材质槽 / 可见性 / 修改器开关；**拓扑变过的对象跳过并报告**
    （顶点数对不上就是拓扑变了，硬写会把材质槽错位）。t_revert 与 edit_revert 共用这一份实现。
    """
    changed, missing, blocked = [], [], []
    for oname, snap in (objects_snap or {}).items():
        ob = bpy.data.objects.get(oname)
        if ob is None:
            missing.append(oname)
            continue
        if snap.get("verts") is not None and ob.type == "MESH" and ob.data is not None:
            if len(ob.data.vertices) != snap["verts"]:
                blocked.append({"name": oname, "why": "拓扑已变（顶点数 %d → %d）" % (snap["verts"], len(ob.data.vertices))})
                continue
        before = _txn_snap_obj(ob)
        try:
            ob.location = snap["loc"]
            ob.rotation_euler = snap["rot"]
            ob.scale = snap["scale"]
            ob.hide_viewport = snap["hide_viewport"]
            ob.hide_render = snap["hide_render"]
            if snap.get("hide_get") is not None and hasattr(ob, "hide_set"):
                ob.hide_set(snap["hide_get"])
            if snap.get("materials") is not None and getattr(ob, "data", None) is not None and hasattr(ob.data, "materials"):
                mats = ob.data.materials
                for i, mname in enumerate(snap["materials"]):
                    m = bpy.data.materials.get(mname) if mname else None
                    if i < len(mats):
                        mats[i] = m
                    else:
                        mats.append(m)          # 槽被删掉过 → 补回原位（不补就"回滚不完整"）
                while len(mats) > len(snap["materials"]):
                    try:
                        mats.pop(index=len(mats) - 1)   # 多出来的槽（编辑时加的）也撤掉
                    except Exception:
                        break
            for mi, msnap in enumerate(snap.get("modifiers") or []):
                if mi < len(ob.modifiers):
                    ob.modifiers[mi].show_viewport = msnap["show_viewport"]
                    ob.modifiers[mi].show_render = msnap["show_render"]
        except Exception as e:
            blocked.append({"name": oname, "why": "%s: %s" % (type(e).__name__, e)})
            continue
        after = _txn_snap_obj(ob)
        if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
            changed.append(oname)
    return changed, missing, blocked


def t_revert(label, strict=False):
    st = _txn_store()
    rec = st["marks"].get(str(label))
    if not rec:
        return _txn_j({"ok": False, "error": "没有这个 mark: %s" % label, "have": sorted(st["marks"])})
    changed, missing, blocked = _txn_restore_objects(rec["objects"])
    bpy.context.view_layer.update()
    return _txn_j({"ok": True, "reverted": str(label), "changed": changed, "changed_count": len(changed),
               "missing_objects": missing, "blocked": blocked,
               "boundary": _TXN_BOUNDARY})


def t_marks():
    st = _txn_store()
    return _txn_j({"ok": True, "marks": [{"label": k, "objects": v["count"], "t": v["t"]} for k, v in st["marks"].items()]})


def t_drop(label):
    st = _txn_store()
    hit = st["marks"].pop(str(label), None)
    return _txn_j({"ok": True, "dropped": bool(hit), "label": str(label), "remaining": sorted(st["marks"])})


# ---------------------------------------------------------------- 待定编辑（v0.9.0 / #12）

def _txn_revert_cap(steps, explicit=None):
    """撤销额度上限 —— "免预算但有上限"里的那个上限（取值口径写死在这里，并随每次返回回传）。

        cap = 显式参数 cap（给了且 >= 1 时优先）
              否则 max(_TXN_REVERT_FLOOR, 已 edit_begin 的历史步数)

    为什么这么取：上游 edit-transaction.ts 是 `Math.max(2, maxRefineSteps)` —— 干得越多、允许返工越多；
    下限 2 保证**新会话**至少能"改一次 → 撤一次"（不然第一次就撤不动，协议等于没有）；
    上限跟着步数增长而不是钉死常数，长会话里不会因为早先抖过一次就永久失去撤销权。
    防抖（同 label 连续 revert）是另一条独立闸门，不占用这里的额度。
    """
    if explicit is not None:
        try:
            return max(1, int(explicit))
        except Exception:
            pass
    try:
        return max(_TXN_REVERT_FLOOR, int(steps or 0))
    except Exception:
        return _TXN_REVERT_FLOOR


def _txn_pending_view(st):
    """pending 的可读摘要（给 edit_status / 拒绝信息复用）。"""
    p = st.get("pending")
    if not p:
        return None
    return {"label": p["label"], "op": p["op"], "targets": list(p["targets"]), "objects": p["count"],
            "started": p["t"], "age_ms": int((time.perf_counter() - p["t0"]) * 1000),
            "checks": len(p["checks"]), "check_kinds": [c["kind"] for c in p["checks"]],
            "note": p["note"], "scene": p["scene"]}


def t_edit_begin(label, op="edit", targets=None, note=""):
    """开始一次待定编辑：记目标对象的 transform/材质/可见性快照 + 挂 pending（同一时间只允许一条）。

    **不做** .blend 快照（大场景 MB 级成本）—— 要文件级回退点请先 t_snapshot()；
    拓扑类改动（Boolean / 合并 / 删面 / apply_transform）**必须**走 t_snapshot，对象级回不去拓扑。
    """
    st = _txn_store()
    if st.get("pending"):
        p = st["pending"]
        return _txn_j({"ok": False, "error": "已有 pending 编辑（label=%s）—— 先 edit_accept 或 edit_revert，一次只允许一条" % p.get("label"),
                   "pending": _txn_pending_view(st), "hint": "edit_status() 看当前 pending"})
    name = str(label).strip() if label is not None else ""
    if not name:
        return _txn_j({"ok": False, "error": "label 必填（空 label 在历史里对不上号）"})
    missing = []
    if targets:
        want = [str(x) for x in (targets if isinstance(targets, (list, tuple)) else [targets])]
        got = []
        for n in want:
            o = bpy.data.objects.get(n)
            if o is None:
                missing.append(n)
            else:
                got.append(o)
    else:
        got = [o for o in bpy.context.scene.objects]
    if not got:
        return _txn_j({"ok": False, "error": "没有可用目标对象（targets 全不存在，或场景里没有对象）",
                   "targets": targets, "missing_targets": missing})
    rec = {"label": name, "op": str(op or "edit"), "note": str(note), "t": _txn_now(), "t0": time.perf_counter(),
           "targets": [o.name for o in got], "objects": {o.name: _txn_snap_obj(o) for o in got}, "count": len(got),
           "checks": [], "scene": bpy.context.scene.name if bpy.context.scene else None}
    st["pending"] = rec
    st["edit_steps"] = int(st.get("edit_steps", 0)) + 1
    return _txn_j({"ok": True, "pending": True, "label": name, "op": rec["op"], "objects": len(got),
               "targets": rec["targets"], "missing_targets": missing, "checks": 0,
               "steps": st["edit_steps"], "revert_cap": _txn_revert_cap(st["edit_steps"]),
               "hint": "改完先 edit_check(kind='audit'|'qc'|'measure') 再 edit_accept(verified='...')",
               "hint_topology": "要文件级回退点就先 t_snapshot(label)；拓扑类改动（Boolean/合并/删面）对象级回不去",
               "boundary": _TXN_BOUNDARY})


def t_edit_check(kind, result=None, note=""):
    """登记一次编辑后的复核（kind = audit / qc / measure / render / read / compare / other）。

    edit_accept 要求**至少一条** check —— 这就是"先编译+看过再接受"的那道门。
    """
    st = _txn_store()
    p = st.get("pending")
    if not p:
        return _txn_j({"ok": False, "error": "没有 pending 编辑 —— 先 edit_begin(label, targets=[...])"})
    k = str(kind).strip() if kind is not None else ""
    if not k:
        return _txn_j({"ok": False, "error": "kind 必填（audit / qc / measure / render ...）", "known": list(_TXN_CHECK_KINDS)})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            pass
    p["checks"].append({"seq": len(p["checks"]) + 1, "kind": k, "result": result, "note": str(note), "t": _txn_now()})
    out = {"ok": True, "label": p["label"], "checks": len(p["checks"]), "kinds": [c["kind"] for c in p["checks"]]}
    if k not in _TXN_CHECK_KINDS:
        out["unknown_kind"] = "非标准 kind=%s（仍记账；标准取值 %s）" % (k, "/".join(_TXN_CHECK_KINDS))
    return _txn_j(out)


def t_edit_accept(verified):
    """接受待定编辑：`verified` 必填（一行说清"看了什么、确认了什么"），且必须已有 check 记录。

    空字符串 / 纯空白直接拒；没有 edit_check 记录拒（报"还没复核，先跑 audit/qc"）。
    """
    st = _txn_store()
    p = st.get("pending")
    if not p:
        return _txn_j({"ok": False, "error": "没有 pending 编辑可接受 —— 先 edit_begin + 改动"})
    v = str(verified).strip() if verified is not None else ""
    if not v:
        return _txn_j({"ok": False, "error": "verified 必填：一行写清'看了什么、确认了什么'（空字符串不接受）",
                   "want": "例如 verified='iso 视角比对目标：榫头进 12mm、方向对、无脱离'", "label": p["label"]})
    if not p["checks"]:
        return _txn_j({"ok": False, "error": "还没复核，先跑 audit/qc —— 编辑必须先验证再接受（edit_check(kind=...) 至少一条）",
                   "label": p["label"], "checks": 0,
                   "hint": "edit_check('audit', result={...}) 或 edit_check('qc', result={...}) 之后再 edit_accept"})
    ms = int((time.perf_counter() - p["t0"]) * 1000)
    st["accepted"] = int(st.get("accepted", 0)) + 1
    st["edits"].append({"label": p["label"], "op": p["op"], "outcome": "accepted", "t_begin": p["t"], "t_end": _txn_now(),
                        "ms": ms, "targets": list(p["targets"]), "checks": [c["kind"] for c in p["checks"]],
                        "verified": v})
    del st["edits"][:-200]
    st["pending"] = None
    st["last_action"] = {"kind": "accept", "label": p["label"], "t": _txn_now()}
    limit = _txn_revert_cap(st.get("edit_steps"))
    used = int(st.get("reverted", 0))
    return _txn_j({"ok": True, "accepted": st["accepted"], "label": p["label"], "verified": v, "checks": len(p["checks"]),
               "check_kinds": [c["kind"] for c in p["checks"]], "ms": ms, "pending": False,
               "reverts_left": max(0, limit - used), "revert_cap": limit,
               "next": "下一个循环：改了再 edit_begin；只在真看过之后才 accept"})


def t_edit_revert(why, cap=None):
    """撤销待定编辑：回滚到 edit_begin 时的对象状态。`why` 必填；免预算但有上限 + 防抖。

    上限口径见 _txn_revert_cap（默认 max(2, 已 edit_begin 的历史步数)，可用 cap= 显式覆盖）。
    防抖：若上一次动作也是 revert 且 label 相同 → 拒绝（"别在改动与撤销之间来回抖，先把量算对"）。
    边界同对象级：拓扑/UV/顶点改动回不去（拓扑变过的对象跳过并报告），那类改动要走 t_snapshot。
    """
    st = _txn_store()
    p = st.get("pending")
    if not p:
        return _txn_j({"ok": False, "error": "没有 pending 编辑可撤销 —— 先 edit_begin + 改动"})
    w = str(why).strip() if why is not None else ""
    if not w:
        return _txn_j({"ok": False, "error": "why 必填：一行写清'复核/渲染看到的哪里不对'（空字符串不接受）",
                   "want": "例如 why='iso 里榫头短了 4mm，方向对但没到底 → 重算 delta 再改一次'", "label": p["label"]})
    la = st.get("last_action") or {}
    if la.get("kind") == "revert" and str(la.get("label")) == str(p["label"]):
        return _txn_j({"ok": False, "code": "dither_guard",
                   "error": "防抖：上一次动作就是 revert(%s)，这次同 label 又撤 —— 别在改动与撤销之间来回抖，先把量算对" % p["label"],
                   "label": p["label"], "last_action": la,
                   "hint": "先 measure（量出真实偏差）再改一次；或换个 label 表示这是不同的编辑意图"})
    limit = _txn_revert_cap(st.get("edit_steps"), cap)
    used = int(st.get("reverted", 0))
    if used >= limit:
        return _txn_j({"ok": False, "code": "revert_budget_spent",
                   "error": "撤销额度用完（%d/%d）—— 若这条编辑是净改进就 edit_accept，别在改动与撤销之间来回抖" % (used, limit),
                   "label": p["label"], "reverts_left": 0, "revert_cap": limit,
                   "budget_rule": "撤销额度 = max(%d, 已 edit_begin 的历史步数)，可用 cap= 显式覆盖" % _TXN_REVERT_FLOOR})
    changed, missing, blocked = _txn_restore_objects(p["objects"])
    bpy.context.view_layer.update()
    ms = int((time.perf_counter() - p["t0"]) * 1000)
    st["reverted"] = used + 1
    st["edits"].append({"label": p["label"], "op": p["op"], "outcome": "reverted", "t_begin": p["t"], "t_end": _txn_now(),
                        "ms": ms, "targets": list(p["targets"]), "checks": [c["kind"] for c in p["checks"]],
                        "why": w, "changed_count": len(changed), "blocked": blocked})
    del st["edits"][:-200]
    st["pending"] = None
    st["last_action"] = {"kind": "revert", "label": p["label"], "t": _txn_now()}
    return _txn_j({"ok": True, "reverted": p["label"], "why": w, "changed": changed, "changed_count": len(changed),
               "missing_objects": missing, "blocked": blocked, "ms": ms, "pending": False,
               "reverts_left": max(0, limit - st["reverted"]), "revert_cap": limit,
               "amend": "这次撤销不花编辑预算：现在把量算对，再改一次（可以沿用同一个 label 之外的标签）",
               "boundary": _TXN_BOUNDARY})


def t_edit_status():
    """当前 pending（label/目标/开始时间/check 数）+ 接受/撤销计数 + 剩余撤销额度 + 边界提醒。"""
    st = _txn_store()
    steps = int(st.get("edit_steps", 0))
    used = int(st.get("reverted", 0))
    limit = _txn_revert_cap(steps)
    hist = []
    for e in (st.get("edits") or [])[-5:]:
        hist.append({"label": e.get("label"), "outcome": e.get("outcome"), "t_end": e.get("t_end"),
                     "checks": e.get("checks"), "ms": e.get("ms")})
    return _txn_j({"ok": True, "version": TXN_VERSION, "pending": _txn_pending_view(st), "has_pending": bool(st.get("pending")),
               "accepted": int(st.get("accepted", 0)), "reverted": used, "steps": steps,
               "revert_cap": limit, "reverts_left": max(0, limit - used),
               "budget_rule": "撤销额度 = max(%d, 已 edit_begin 的历史步数)（免预算但有上限）；可用 cap= 显式覆盖" % _TXN_REVERT_FLOOR,
               "dither_guard": "上一次动作若是同 label 的 revert，则本次 revert 被拒（防在改动与撤销之间来回抖）",
               "accept_gate": "edit_accept 要求 verified 非空 + 至少一条 edit_check 记录",
               "topology_warning": "编辑含拓扑改动（Boolean / 合并 / 删面 / apply_transform）时：先 t_snapshot(label) —— 对象级快照回不去拓扑，这类对象在 revert 里会被跳过并报告",
               "history": hist, "boundary": _TXN_BOUNDARY})


def t_help():
    return _txn_j({
        "version": TXN_VERSION,
        "file_level": {"snapshot": "snapshot(label=None, note='') → 存 <out_dir>/snapshots/<label>.blend（copy=True，不动当前 filepath）",
                       "restore": "restore(label) → open_mainfile（会丢掉当前未保存状态）",
                       "list": "list() → 快照 + mark 清单", "prune": "prune(keep=5) → 只留最近 N 个快照"},
        "object_level": {"mark": "mark(label, objects=None) → 记 transform/材质/可见性/修改器开关",
                         "revert": "revert(label, strict=False) → 就地回写；拓扑变过的对象会被跳过并报告",
                         "marks": "marks() → 列出 mark", "drop": "drop(label) → 删掉 mark"},
        "pending_edit": {"edit_begin": "edit_begin(label, op='edit', targets=None, note='') → 挂 pending（同一时间只允许一条；已有 pending 直接拒）",
                         "edit_check": "edit_check(kind, result=None, note='') → 复核记账（kind=audit/qc/measure/render...）；accept 前至少一条",
                         "edit_accept": "edit_accept(verified) → verified 必填且非空；无 check 记录则拒；成功返回 accepted 计数",
                         "edit_revert": "edit_revert(why, cap=None) → why 必填；回滚到 begin 时状态；免预算但有上限（默认 max(2, 历史步数)）+ 同 label 连续 revert 防抖",
                         "edit_status": "edit_status() → pending（label/目标/开始时间/check 数）+ accepted/reverted + 剩余撤销额度"},
        "boundary": "对象级不含拓扑/UV/顶点改动；文件级是完整回滚但有写盘成本。"
                    "待定编辑协议：edit_begin 不做 .blend 快照 —— 拓扑类改动（Boolean/合并/删面/apply_transform）必须先 t_snapshot()",
        "usage": "K.dsh_txn_api[...]；也可用工具 blender_rt_txn(op=...)。典型：edit_begin → 改 → edit_check('qc', {...}) → edit_accept('iso 比对：方向对、进 12mm')",
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
        return _txn_j({"ok": False, "error": "unknown txn op", "op": op, "ops": sorted(t_ops())})
    try:
        return fn(**kw)
    except TypeError as e:
        return _txn_j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(kw), "sig_hint": t_help()})


def t_ops():
    return {"snapshot": t_snapshot, "restore": t_restore, "list": t_list, "prune": t_prune,
            "mark": t_mark, "revert": t_revert, "marks": t_marks, "drop": t_drop, "help": t_help,
            "edit_begin": t_edit_begin, "edit_check": t_edit_check, "edit_accept": t_edit_accept,
            "edit_revert": t_edit_revert, "edit_status": t_edit_status}


import sys as _sys
_txn_K = _sys.modules.get("dsh_rt_kernel")
if _txn_K is not None:
    _txn_K.dsh_txn_api = {"version": TXN_VERSION, "dispatch": t_dispatch, "snapshot": t_snapshot, "restore": t_restore,
                      "list": t_list, "prune": t_prune, "mark": t_mark, "revert": t_revert,
                      "marks": t_marks, "drop": t_drop, "help": t_help,
                      "edit_begin": t_edit_begin, "edit_check": t_edit_check, "edit_accept": t_edit_accept,
                      "edit_revert": t_edit_revert, "edit_status": t_edit_status}

# ---- v0.9.1（93-B1/B2）：API 可调用化（换成 dict 子类实例，返回已解析对象）----
# 背景：K.dsh_x_api 原来是普通 dict → 进程内 api(args) 报 TypeError: 'dict' object is not callable；
# 且 dispatch 返回 JSON 字符串，调用方还得自己 json.loads。
# 现在：api("op", {…}) 或 api({…}) → dict；api["dispatch"](op, json_str) 仍返回 str（引擎契约不变）。
# 注意：dict 是静态类型，不能对已有实例做 __class__ 赋值（实测 TypeError），所以换成一个新实例。
class _DshApi(dict):
    _DEFAULT_OP = "list"

    def __init__(self, base=None):
        dict.__init__(self, base or {})

    def __call__(self, op=None, args=None, **kw):
        if op is None or isinstance(op, dict):
            args, op = (op if isinstance(op, dict) else args), self._DEFAULT_OP
        payload = args if isinstance(args, str) else json.dumps(dict(args or {}, **kw),
                                                               ensure_ascii=False, default=str)
        return json.loads(self["dispatch"](str(op), payload))

    def call(self, op, args=None, **kw):
        return self(op, args, **kw)


import sys as _sys_api
_K_api = _sys_api.modules.get("dsh_rt_kernel")
if _K_api is not None and isinstance(getattr(_K_api, "dsh_txn_api", None), dict) \
        and not isinstance(getattr(_K_api, "dsh_txn_api", None), _DshApi):
    _K_api.dsh_txn_api = _DshApi(_K_api.dsh_txn_api)
