# -*- coding: utf-8 -*-
"""Split from contract.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .fingerprint import _r4
from .mate import _iface_hint, _mate_measure, _mesh_data, _sample_surface, _tris_diagonal, _units, _units_block



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




def c_check_interface(conn_id, mesh=True, samples=1500):
    """两组件间的间隙。**老字段口径一字未改**（向后兼容）：gap_axis_m / gap_m / overlap_bbox
    仍是 bbox 级（>0 有缝，<0 按 bbox 重叠）。

    本次新增 mesh 块（默认开，mesh=False 关）：同一对在**真实网格**上的量测。
    为什么必须补这一块：bbox 口径对"销插进孔"这种配合**永远**读成"重叠、0 缝" ——
    它既分不出 0.15 mm 的 location 配合，也看不出销已经压进母材 3 mm（实测：3 mm 深穿模时
    bbox 口径给 gap_m=0.0，网格口径给 depth_max=3.000 mm、中位单边间隙 0.1498 mm）。
    量测与 mate_check 共用同一份实现（_mate_measure），不另抄一套 —— 两处口径漂移过一次就够受了。
    量不了的时候（组件空 / 没三角面 / 网格过大）给 mesh.skipped + 理由，绝不静默省略。
    """
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
    out = {"ok": True, "connection": conn_id, "gap_axis_m": gaps, "gap_m": round(sep, 5),
           "overlap_bbox": all(g == 0.0 for g in gaps.values()),
           "a_objects": [o.name for o in A], "b_objects": [o.name for o in B]}
    # --- 新增：网格级量测（老字段一个不动；这一块只做加法） ---
    if not mesh:
        out["mesh"] = {"skipped": True, "why": "mesh=False（只要 bbox 口径粗筛）"}
        return _j(out)
    AM = [o for o in A if o.type == "MESH"]
    BM = [o for o in B if o.type == "MESH"]
    if not AM or not BM:
        out["mesh"] = {"skipped": True, "why": "两端之一没有 MESH 对象 → 量不了（不给假数）"}
        return _j(out)
    mdA, mdB = _mesh_data(AM), _mesh_data(BM)
    if mdA is None or mdB is None or mdA["tris_n"] == 0 or mdB["tris_n"] == 0:
        out["mesh"] = {"skipped": True, "why": "求值后没有三角面 → 量不了"}
        return _j(out)
    if MATE_MAX_TRIS > 0 and (mdA["tris_n"] > MATE_MAX_TRIS or mdB["tris_n"] > MATE_MAX_TRIS):
        out["mesh"] = {"skipped": True, "why": "网格过大（%d/%d 面 > %d）→ 不判，绝不读成已通过"
                       % (mdA["tris_n"], mdB["tris_n"], MATE_MAX_TRIS)}
        return _j(out)
    import numpy as np
    _m_per_unit, mm_per_unit = _units()
    diag_m, _lo, _hi = _tris_diagonal(np.concatenate([mdA["tris"], mdB["tris"]], axis=0))
    contact_max_m = DEFAULT_CONTACT_DISTANCE_FRAC * diag_m
    n = int(max(1, min(int(samples or 0), CONTACT_SAMPLE_CAP)))
    PA = _sample_surface(mdA["tris"], n)
    PB = _sample_surface(mdB["tris"], n)
    if PA is None or PB is None:
        out["mesh"] = {"skipped": True, "why": "零面积网格，采样失败"}
        return _j(out)
    meas = _mate_measure(mdA, mdB, mm_per_unit, PA, PB, contact_max_m)
    out["units"] = _units_block(_m_per_unit, mm_per_unit)
    out["mesh"] = {"skipped": False, "units_rule": "所有 *_mm = Blender 单位 × mm_per_unit",
                   "samples_used": min(meas["used_a"], meas["used_b"]), "cap": CONTACT_SAMPLE_CAP,
                   "contact_max_mm": _r4(contact_max_m * mm_per_unit),
                   "contact_area_fraction": meas["contact"]["contact_area_fraction"],
                   "gap_median_mm": meas["gap"]["median_mm"], "gap_mean_mm": meas["gap"]["mean_mm"],
                   "gap_min_mm": meas["gap"]["min_mm"], "gap_max_mm": meas["gap"]["max_mm"],
                   "contact_points": meas["gap"]["n"],
                   "depth_max_mm": meas["penetration"].get("depth_max_mm"),
                   "penetration_status": meas["penetration"].get("status"),
                   "closed": {"a": mdA["closed"], "b": mdB["closed"]},
                   "contact_anchor_world": meas["contact"]["anchor_world"],
                   "verdict_hint": _iface_hint(meas["contact"], meas["gap"], meas["penetration"]),
                   "next": "要判三态（supported/refuted/unresolved）用 mate_check(conn_id, fit=...)；"
                           "本块只报实测数字"}
    return _j(out)


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

