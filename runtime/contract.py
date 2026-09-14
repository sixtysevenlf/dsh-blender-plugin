# -*- coding: utf-8 -*-
"""DSH 契约层（S1+S2）—— 把「假设 / 区间 / 校验 / 证据 / 门控」变成机器可查的 API。

API 挂在持久内核上：K.dsh_contract_api（与 K.dsh_view_api / K.dsh_perf_api 同一个约定）。

S1 能力：
  help()                     契约速查
  reset()                    清空契约（不动场景）
  register_component(id, objects, tags, note)      组件 = 一组对象的语义名
  register_connection(id, a, b, candidates, params, forbidden, confidence, evidence_required)
                             连接 = 两个组件之间的关系（含候选类型 / 参数区间 / 禁止项）
  register_envelope(id, min, max, scope, note)     包络 = 允许空间范围
  check_envelope(id)                               越界清单（只读）
  check_interference(scope, use_bvh, limit)        AABB 粗筛 + BVH 精查（只读）
  check_interface(conn_id)                         两组件间隙/侵入（bbox 级，只读）
  destructive_guard(op, targets, conn_ids)         破坏性操作门控（boolean/weld/merge/apply_transform）
  evidence(label, view)                            出图 + md5 + 记账（复用 K.dsh_view_api）
  ledger() / status()                              证据账本 / 全局概览
  report(path)                                     生成 Markdown 报告（含判定与复现命令）

S2 能力（假设生命周期，三态）：
  verify(conn_id, err, tolerance, identifiable, probe_err, probe_tolerance)
      外部证据 + 探针证据 → supported / refuted / unresolved（规则见 docs/假设驱动建模-cookbook.md §5）
  flip(conn_id, to, why)      假设翻转（记历史，不删旧判定）
  advance(conn_id, status, why)  手动推进状态（proposed → testing → supported/refuted）

约定：状态放 K.dsh_contract（跨调用保留）；任何"只读"检查都不会改场景。
"""
import bpy, json, time, math

CONTRACT_VERSION = 1
DESTRUCTIVE_OPS = ("boolean_union", "weld", "merge", "join", "apply_transform")


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _store():
    K = _kernel()
    if K is None:
        raise RuntimeError("需要持久内核 K（走 blender_rt_* 通道）")
    if not hasattr(K, "dsh_contract"):
        K.dsh_contract = {"components": {}, "connections": {}, "envelopes": {}, "evidence": [], "log": []}
    return K.dsh_contract


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(action, detail):
    st = _store()
    st["log"].append({"t": _now(), "action": action, "detail": detail})
    del st["log"][:-200]


# ---------------------------------------------------------------- 组件 / 连接 / 包络

def c_reset():
    K = _kernel()
    K.dsh_contract = {"components": {}, "connections": {}, "envelopes": {}, "evidence": [], "log": []}
    return _j({"ok": True, "reset": True})


def c_register_component(cid, objects=None, tags=None, note=""):
    st = _store()
    miss = [n for n in (objects or []) if bpy.data.objects.get(n) is None]
    st["components"][str(cid)] = {"objects": list(objects or []), "tags": list(tags or []),
                                  "note": str(note), "missing_objects": miss, "registered": _now()}
    _log("register_component", {"id": cid, "objects": objects, "missing": miss})
    return _j({"ok": True, "id": cid, "objects": objects, "missing_objects": miss})


def c_register_connection(cid, a, b, candidates=None, params=None, forbidden=None,
                          confidence="", evidence_required=None, status="proposed", note=""):
    st = _store()
    for side, name in (("a", a), ("b", b)):
        if name not in st["components"]:
            return _j({"ok": False, "error": "组件未注册: %s" % name, "side": side})
    st["connections"][str(cid)] = {
        "a": str(a), "b": str(b),
        "candidates": list(candidates or []),
        "params": params or {},
        "forbidden": list(forbidden or []),
        "confidence": str(confidence),
        "evidence_required": list(evidence_required or []),
        "status": str(status),
        "note": str(note),
        "verdict": None, "history": [], "registered": _now(),
    }
    _log("register_connection", {"id": cid, "a": a, "b": b, "candidates": candidates, "status": status})
    return _j({"ok": True, "id": cid, "status": status, "forbidden": list(forbidden or [])})


def c_register_envelope(eid, mn, mx, scope=None, note=""):
    st = _store()
    st["envelopes"][str(eid)] = {"min": list(mn), "max": list(mx), "scope": list(scope or []),
                                 "note": str(note), "registered": _now()}
    _log("register_envelope", {"id": eid, "min": mn, "max": mx, "scope": scope})
    return _j({"ok": True, "id": eid})


def _scope_objects(scope=None, include_hidden=False):
    out = []
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        if not include_hidden and (ob.hide_render or ob.hide_viewport):
            continue
        if scope:
            if any(str(ob.name).startswith(str(s)) for s in scope):
                out.append(ob)
        else:
            out.append(ob)
    return out


def _bbox(ob):
    from mathutils import Vector
    pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def c_check_envelope(eid=None):
    st = _store()
    ids = [eid] if eid else list(st["envelopes"])
    out = []
    for i in ids:
        env = st["envelopes"].get(str(i))
        if not env:
            out.append({"id": i, "error": "未注册"})
            continue
        bad = []
        for ob in _scope_objects(env["scope"] or None):
            (mnx, mny, mnz), (mxx, mxy, mxz) = _bbox(ob)
            over = []
            for k, (lo, hi) in enumerate(zip(env["min"], env["max"])):
                v_lo, v_hi = (mnx, mny, mnz)[k], (mxx, mxy, mxz)[k]
                if v_lo < lo - 1e-9:
                    over.append(["%s_min" % "xyz"[k], round(v_lo - lo, 4)])
                if v_hi > hi + 1e-9:
                    over.append(["%s_max" % "xyz"[k], round(v_hi - hi, 4)])
            if over:
                bad.append({"name": ob.name, "out_of_envelope": over})
        out.append({"id": i, "scope": env["scope"], "violations": len(bad), "objects": bad[:50]})
    return _j({"ok": True, "envelopes": out})


def c_check_interference(scope=None, use_bvh=True, limit=400, include_hidden=False):
    """AABB 按 x 扫描剪枝 → 可选 BVH 精查。返回候选对、精查命中、耗时（只读）。"""
    import numpy as np
    t0 = time.perf_counter()
    objs = _scope_objects(scope, include_hidden)
    n = len(objs)
    if n == 0:
        return _j({"ok": True, "objects": 0, "pairs": 0, "ms": 0})
    mn = np.empty((n, 3)); mx = np.empty((n, 3))
    for i, ob in enumerate(objs):
        (a, b) = _bbox(ob)
        mn[i] = a; mx[i] = b
    t_box = time.perf_counter() - t0
    order = np.argsort(mn[:, 0])
    t1 = time.perf_counter()
    cand = []
    for ii in range(n):
        i = int(order[ii]); jj = ii + 1
        while jj < n:
            j = int(order[jj])
            if mn[j][0] > mx[i][0]:
                break
            if (mn[i][1] <= mx[j][1] and mx[i][1] >= mn[j][1] and mn[i][2] <= mx[j][2] and mx[i][2] >= mn[j][2]):
                cand.append((i, j))
            jj += 1
    t_sweep = time.perf_counter() - t1
    refined = None
    t2 = time.perf_counter()
    if use_bvh and cand:
        from mathutils.bvhtree import BVHTree
        dg = bpy.context.evaluated_depsgraph_get()
        trees = {}
        used = sorted({x for p in cand[:limit] for x in p})
        for i in used:
            try:
                trees[i] = BVHTree.FromObject(objs[i], dg)
            except Exception:
                trees[i] = None
        hit = []
        for (i, j) in cand[:limit]:
            if trees.get(i) and trees.get(j) and trees[i].overlap(trees[j]):
                hit.append([objs[i].name, objs[j].name])
        refined = hit
    t_bvh = time.perf_counter() - t2
    return _j({"ok": True, "objects": n, "pairs_aabb": len(cand),
               "pairs_bvh": (len(refined) if refined is not None else None),
               "bvh_tested": min(len(cand), limit),
               "bbox_ms": round(t_box * 1000, 1), "sweep_ms": round(t_sweep * 1000, 1),
               "bvh_ms": round(t_bvh * 1000, 1),
               "pairs": (refined if refined is not None else [[objs[i].name, objs[j].name] for i, j in cand[:limit]])})


def _component_objects(cid):
    st = _store()
    comp = st["components"].get(str(cid))
    if not comp:
        return []
    return [ob for ob in (bpy.data.objects.get(n) for n in comp["objects"]) if ob is not None]


def c_check_interface(conn_id):
    """两组件间的间隙（bbox 级）：>0 表示有缝，<0 表示按 bbox 已经重叠。"""
    st = _store()
    conn = st["connections"].get(str(conn_id))
    if not conn:
        return _j({"ok": False, "error": "连接未注册: %s" % conn_id})
    A = _component_objects(conn["a"]); B = _component_objects(conn["b"])
    if not A or not B:
        return _j({"ok": False, "error": "组件没有对象", "a": len(A), "b": len(B)})
    def union(obs):
        bs = [_bbox(o) for o in obs]
        return ([min(b[0][k] for b in bs) for k in range(3)], [max(b[1][k] for b in bs) for k in range(3)])
    (amn, amx) = union(A); (bmn, bmx) = union(B)
    gaps = {}
    for k, ax in enumerate("xyz"):
        if amx[k] < bmn[k]:
            gaps[ax] = round(bmn[k] - amx[k], 5)
        elif bmx[k] < amn[k]:
            gaps[ax] = round(amn[k] - bmx[k], 5)
        else:
            gaps[ax] = 0.0
    sep = math.sqrt(sum(g * g for g in gaps.values()))
    return _j({"ok": True, "connection": conn_id, "gap_axis_m": gaps, "gap_m": round(sep, 5),
               "overlap_bbox": all(g == 0.0 for g in gaps.values()),
               "a_objects": [o.name for o in A], "b_objects": [o.name for o in B]})


# ---------------------------------------------------------------- 门控

def c_destructive_guard(op, targets=None, conn_ids=None):
    """破坏性操作门控：只要目标对象属于「未判别（status != supported）」的连接，或该连接把它列为禁止项，就拦住。"""
    st = _store()
    op = str(op)
    if op not in DESTRUCTIVE_OPS:
        return _j({"ok": True, "allowed": True, "op": op, "reason": "非破坏性操作，不拦"})
    targets = list(targets or [])
    ids = [str(x) for x in (conn_ids or list(st["connections"]))]
    hits = []
    for cid in ids:
        conn = st["connections"].get(cid)
        if not conn:
            continue
        objs = {o.name for o in _component_objects(conn["a"])} | {o.name for o in _component_objects(conn["b"])}
        touched = [t for t in targets if t in objs] if targets else []
        forbidden = (op in conn.get("forbidden", [])) or (op == "boolean_union" and "boolean_union" in conn.get("forbidden", []))
        unresolved = conn.get("status") != "supported"
        if forbidden or (unresolved and (not targets or touched)):
            hits.append({"connection": cid, "a": conn["a"], "b": conn["b"], "status": conn.get("status"),
                         "forbidden_listed": bool(forbidden), "touched": touched})
    if hits:
        return _j({"ok": False, "allowed": False, "op": op,
                   "code": "Unsupported Destructive Merge" if op in ("boolean_union", "weld", "merge", "join") else "Forbidden Destructive Op",
                   "reason": "目标涉及未判别的连接或该连接显式禁止该操作；先按 cookbook 判定（probe/explain）再来做",
                   "blocked_by": hits})
    return _j({"ok": True, "allowed": True, "op": op, "reason": "没有未判别连接涉及该操作"})


# ---------------------------------------------------------------- 证据

def c_evidence(label, view=None, note=""):
    K = _kernel()
    api = getattr(K, "dsh_view_api", None)
    if api is None:
        return _j({"ok": False, "error": "需要 view.py：先 preload/注入 K.dsh_view_api（引擎里 ensureView）"})
    import hashlib, os
    spec = dict(view or {})
    path = spec.get("path")
    if not path:
        return _j({"ok": False, "error": "view.path 必填（引擎会带默认工作目录）"})
    info = json.loads(api["capture"](json.dumps(spec)))
    if not info.get("ok"):
        return _j({"ok": False, "error": info})
    h = hashlib.md5(open(path, "rb").read()).hexdigest()
    rec = {"label": str(label), "path": path, "md5": h, "bytes": os.path.getsize(path),
           "width": info.get("width"), "height": info.get("height"),
           "view": {k: spec.get(k) for k in ("from", "look_at", "lens", "ortho", "ortho_scale", "shading")},
           "note": str(note), "t": _now()}
    _store()["evidence"].append(rec)
    _log("evidence", {"label": label, "md5": h})
    return _j({"ok": True, "evidence": rec, "total": len(_store()["evidence"])})


def c_ledger():
    return _j({"ok": True, "evidence": _store()["evidence"]})


def c_status():
    st = _store()
    conns = {k: {"a": v["a"], "b": v["b"], "status": v["status"], "candidates": v["candidates"],
                 "forbidden": v["forbidden"], "verdict": v["verdict"],
                 "history_len": len(v["history"])} for k, v in st["connections"].items()}
    unresolved = [k for k, v in st["connections"].items() if v["status"] != "supported"]
    return _j({"ok": True, "components": len(st["components"]), "connections": len(st["connections"]),
               "envelopes": len(st["envelopes"]), "evidence": len(st["evidence"]),
               "unresolved_connections": unresolved, "detail": conns})


# ---------------------------------------------------------------- S2：三态判定与翻转

def c_verify(conn_id, err=None, tolerance=None, identifiable=None, probe_err=None,
             probe_tolerance=None, evidence_labels=None, note=""):
    """三态判定（cookbook §5）：
         err <= tol 且关键参数可辨识          → supported
         err >  tol                            → refuted
         err <= tol 但关键参数不可辨识/多假设   → unresolved（并列出需要什么探针）
       probe_err 给定时：probe_err <= probe_tolerance → 支持；否则推翻外部结论。
    """
    st = _store()
    conn = st["connections"].get(str(conn_id))
    if not conn:
        return _j({"ok": False, "error": "连接未注册: %s" % conn_id})
    ident = list(identifiable or [])
    missing = [p for p in (conn.get("params") or {}) if p not in ident]
    verdict = None; why = []
    if err is not None and tolerance is not None:
        if float(err) > float(tolerance):
            verdict = "refuted"; why.append("外部误差 %s > 容差 %s" % (err, tolerance))
        elif missing:
            verdict = "unresolved"
            why.append("外部误差 %s <= %s 但参数不可辨识: %s（需要探针）" % (err, tolerance, ", ".join(missing)))
        else:
            verdict = "supported"; why.append("外部误差 %s <= %s 且参数可辨识" % (err, tolerance))
    if probe_err is not None and probe_tolerance is not None:
        if float(probe_err) <= float(probe_tolerance):
            verdict = "supported"; why.append("探针误差 %s <= %s → 支持" % (probe_err, probe_tolerance))
        else:
            verdict = "refuted"; why.append("探针误差 %s > %s → 推翻" % (probe_err, probe_tolerance))
    if verdict is None:
        return _j({"ok": False, "error": "至少要给 err+tolerance 或 probe_err+probe_tolerance"})
    ev = [{"label": e["label"], "md5": e["md5"], "path": e["path"]} for e in st["evidence"]
          if not evidence_labels or e["label"] in evidence_labels]
    conn["status"] = verdict
    conn["verdict"] = {"verdict": verdict, "why": why, "err": err, "tolerance": tolerance,
                       "identifiable": ident, "unidentifiable": missing,
                       "probe_err": probe_err, "probe_tolerance": probe_tolerance,
                       "evidence": ev, "note": str(note), "t": _now()}
    conn["history"].append({"t": _now(), "status": verdict, "why": why})
    _log("verify", {"id": conn_id, "verdict": verdict})
    return _j({"ok": True, "connection": conn_id, "verdict": verdict, "why": why,
               "unidentifiable_params": missing, "evidence_used": len(ev)})


def c_flip(conn_id, to, why=""):
    """假设翻转：把连接切到另一个候选类型，保留历史（不删旧判定）。"""
    st = _store()
    conn = st["connections"].get(str(conn_id))
    if not conn:
        return _j({"ok": False, "error": "连接未注册: %s" % conn_id})
    if conn["candidates"] and to not in conn["candidates"]:
        return _j({"ok": False, "error": "候选里没有: %s" % to, "candidates": conn["candidates"]})
    conn["history"].append({"t": _now(), "flip_from": conn.get("flipped_to"), "status": conn.get("status"),
                            "why": "flip -> %s：%s" % (to, why)})
    conn["flipped_to"] = str(to)
    conn["status"] = "testing"
    conn["verdict"] = None
    _log("flip", {"id": conn_id, "to": to, "why": why})
    return _j({"ok": True, "connection": conn_id, "now": to, "status": "testing", "history_len": len(conn["history"])})


def c_advance(conn_id, status, why=""):
    st = _store()
    conn = st["connections"].get(str(conn_id))
    if not conn:
        return _j({"ok": False, "error": "连接未注册: %s" % conn_id})
    if status not in ("proposed", "testing", "supported", "refuted"):
        return _j({"ok": False, "error": "非法状态", "allowed": ["proposed", "testing", "supported", "refuted"]})
    conn["status"] = status
    conn["history"].append({"t": _now(), "status": status, "why": why})
    _log("advance", {"id": conn_id, "status": status})
    return _j({"ok": True, "connection": conn_id, "status": status})


def c_report(path=None):
    """生成 Markdown 报告：假设/区间/判定/证据/复现命令（S7 的 provenance 表）。"""
    st = _store()
    L = ["# 契约与假设报告（自动生成）", "", "> 生成时间：%s" % _now(), "",
         "## 组件（%d）" % len(st["components"]), "", "| 组件 | 对象 | 标签 | 缺失对象 |", "|---|---|---|---|"]
    for k, v in st["components"].items():
        L.append("| %s | %s | %s | %s |" % (k, ", ".join(v["objects"]) or "-", ", ".join(v["tags"]) or "-",
                                            ", ".join(v["missing_objects"]) or "-"))
    L += ["", "## 连接与判定（%d）" % len(st["connections"]), "",
          "| 连接 | a → b | 候选 | 禁止项 | 状态 | 判定依据 |", "|---|---|---|---|---|---|"]
    for k, v in st["connections"].items():
        vd = v.get("verdict") or {}
        why = "；".join(vd.get("why") or []) or "-"
        extra = ""
        if vd.get("unidentifiable"):
            extra = "（不可辨识：%s）" % ", ".join(vd["unidentifiable"])
        L.append("| %s | %s → %s | %s | %s | **%s** | %s%s |" % (
            k, v["a"], v["b"], ", ".join(v["candidates"]) or "-", ", ".join(v["forbidden"]) or "-",
            v["status"], why, extra))
    if st["envelopes"]:
        L += ["", "## 包络", "", "| 包络 | min | max | 作用域 |", "|---|---|---|---|"]
        for k, v in st["envelopes"].items():
            L.append("| %s | %s | %s | %s |" % (k, v["min"], v["max"], ", ".join(v["scope"]) or "(全部)"))
    L += ["", "## 证据账本（%d）" % len(st["evidence"]), "",
          "| 标签 | 文件 | md5 | 尺寸 |", "|---|---|---|---|"]
    for e in st["evidence"]:
        L.append("| %s | %s | %s | %sx%s |" % (e["label"], e["path"], e["md5"], e.get("width"), e.get("height")))
    L += ["", "## 历史", ""]
    for r in st["log"][-30:]:
        L.append("- %s %s %s" % (r["t"], r["action"], json.dumps(r["detail"], ensure_ascii=False)[:200]))
    L += ["", "## 复现", "", "    该报告由契约层生成：blender_rt_plan(op=\"report\")",
          "    假设/区间/探针流程见 docs/假设驱动建模-cookbook.md", ""]
    text = "\n".join(L) + "\n"
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return _j({"ok": True, "path": path, "bytes": len(text.encode("utf-8")), "preview": text[:600]})
        except Exception as e:
            return _j({"ok": False, "error": str(e), "preview": text[:600]})
    return _j({"ok": True, "path": None, "text": text})


def c_help():
    return _j({"version": CONTRACT_VERSION,
               "S1_ops": {"register_component": "id, objects, tags, note",
                          "register_connection": "id, a, b, candidates, params, forbidden, confidence, evidence_required, status",
                          "register_envelope": "id, min, max, scope, note",
                          "check_envelope": "id(可选) → 越界清单",
                          "check_interference": "scope, use_bvh, limit → AABB 粗筛 + BVH 精查",
                          "check_interface": "conn_id → 两组件间隙/侵入（bbox 级）",
                          "destructive_guard": "op, targets, conn_ids → 允许/拦下 + 理由",
                          "evidence": "label, view → 出图 + md5 记账",
                          "ledger/status/report": "证据账本 / 概览 / Markdown 报告"},
               "S2_ops": {"verify": "conn_id, err, tolerance, identifiable, probe_err, probe_tolerance → 三态",
                          "flip": "conn_id, to, why → 翻转候选并记历史",
                          "advance": "conn_id, status(proposed|testing|supported|refuted), why"},
               "rules": "外部证据不足（参数不可辨识）必须报 unresolved；探针误差 > 容差则 refuted；破坏性操作在未判别前拦住",
               "docs": "docs/假设驱动建模-cookbook.md"})


def c_dispatch(op, args=None):
    """统一入口：工具侧发 {op, args}，这里按参数名派发（args 可选再包一层 dict）。"""
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
    fn = c_ops().get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown contract op", "op": op, "ops": sorted(c_ops())})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(kw),
                   "sig_hint": c_help()})


def c_ops():
    return {"reset": c_reset, "help": c_help, "status": c_status,
            "register_component": c_register_component, "register_connection": c_register_connection,
            "register_envelope": c_register_envelope,
            "check_envelope": c_check_envelope, "check_interference": c_check_interference,
            "check_interface": c_check_interface, "destructive_guard": c_destructive_guard,
            "evidence": c_evidence, "ledger": c_ledger, "report": c_report,
            "verify": c_verify, "flip": c_flip, "advance": c_advance}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_contract_api = {
        "dispatch": c_dispatch,
        "version": CONTRACT_VERSION,
        "reset": c_reset, "help": c_help, "status": c_status,
        "register_component": c_register_component, "register_connection": c_register_connection,
        "register_envelope": c_register_envelope,
        "check_envelope": c_check_envelope, "check_interference": c_check_interference,
        "check_interface": c_check_interface,
        "destructive_guard": c_destructive_guard,
        "evidence": c_evidence, "ledger": c_ledger, "report": c_report,
        "verify": c_verify, "flip": c_flip, "advance": c_advance,
    }
