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
  check_interface(conn_id, mesh, samples)          两组件间隙/侵入（bbox 级老字段不变；
                                                   mesh=True 追加真实网格量测块，只做加法）
  destructive_guard(op, targets, conn_ids)         破坏性操作门控（boolean/weld/merge/apply_transform）
  evidence(label, view)                            出图 + md5 + 记账（复用 K.dsh_view_api）
  ledger() / status()                              证据账本 / 全局概览
  report(path)                                     生成 Markdown 报告（含判定与复现命令）

S2 能力（假设生命周期，三态）：
  verify(conn_id, err, tolerance, identifiable, probe_err, probe_tolerance)
      外部证据 + 探针证据 → supported / refuted / unresolved（规则见 docs/假设驱动建模-cookbook.md §5）
  flip(conn_id, to, why)      假设翻转（记历史，不删旧判定）
  advance(conn_id, status, why)  手动推进状态（proposed → testing → supported/refuted）

S3 能力（v0.9.0 · 装配级判据，全部在**真实网格**上量，不再用 bbox）：
  mate_check(conn_id, fit, nominal, tol_mm, samples, contact_max_mm, depth_eps_mm)
      配合门：面积加权采样 + BVH 最近点 → 接触面积占比 / 接触锚点 / 接触法向 / 单边间隙(mm)
      / 穿透深度(mm) → supported / refuted / unresolved（三态纪律：证据不足不许二选一）
  fit_help(fit, feature, m, d)
      装配特征库速查（读同目录 assembly_features.json）：9 个配合特征 + asm_fit 四档
      + asm_lead 导向倒角 + ISO 273 间隙孔 + 「Blender 侧建议怎么造」
  interference_report(scope, use_bvh, limit, declared_ok, depth_eps, include_hidden)
      逐对干涉报告（穿透深度 mm + 重叠体积占比 + 严重度排序 + 分组 + 设计意图白名单豁免）
      —— 老 op check_interference 原样保留，返回结构不变（向后兼容）

约定：状态放 K.dsh_contract（跨调用保留）；任何"只读"检查都不会改场景。
     铁律：空产出必须 ok:false；数值必带单位（mm 口径按场景 scale_length 换算）；结论三态。
"""
import bpy, json, time, math, os

CONTRACT_VERSION = 3
DESTRUCTIVE_OPS = ("boolean_union", "weld", "merge", "join", "apply_transform")
# v0.9.0（Procedura 融合 #8）：判定随几何失效的门限 —— 中心漂移 > 对角线 10% 或尺寸漂移 > 20%
# → 旧判定自动降级为 unresolved（上游 assembly-seed.ts 的 DRIFT_CENTER/SIZE_FRACTION）
DRIFT_CENTER_FRACTION = 0.10
DRIFT_SIZE_FRACTION = 0.20

# --- v0.9.0 S3：装配级阈值（来源见文件下方 S3 段的说明块；改数值必须同时改那里的理由） ---
CONTACT_SAMPLE_CAP = 4000              # 采样硬上限（上游 CONTACT_SAMPLE_CAP=4000，同口径）
CONTACT_MIN_POINTS = 8                 # 证据下限：接触点 < 8 一律 unresolved（上游 CONTACT_MIN_POINTS）
DEFAULT_CONTACT_DISTANCE_FRAC = 0.015  # 接触距离 = 1.5% × 合并 bbox 对角线（上游 DEFAULT_CONTACT_DISTANCE_FRAC）
FIT_TABLE = {"clearance": 0.25, "location": 0.15, "press": -0.05, "snap": 0.20}  # 单边 mm
FIT_DEFAULT_CLS = "location"
FIT_DEFAULT_TOL_MM = 0.15              # 默认容差（理由见 c_mate_check 文档串第 3 段）
MATE_DEPTH_EPS_MM = 0.2                # 计划内接触允许的穿透深度（理由见 c_interference_report）
MATE_MAX_TRIS = 2000000                # 单侧网格三角面上限：超了 ok:false，绝不"降级成看不清"
INSIDE_MAX_TRIS = 200000               # 射线奇偶内测试上限：超了报 skipped，不猜
VOL_SAMPLES = 1200                     # 重叠体积蒙特卡洛采样数（低差异序列，确定性）
VOL_MAX_TRIS = 60000                   # 体积估计的网格上限（超了只报深度，不报体积）
SEV_DEPTH_HIGH_MM = 2.0                # 严重度分档：深度
SEV_DEPTH_MED_MM = 0.5
SEV_FRAC_HIGH = 0.25                   # 严重度分档：体积占比
SEV_FRAC_MED = 0.02
FEATURES_JSON_NAME = "assembly_features.json"
try:
    _HERE = os.path.dirname(os.path.abspath(__file__))   # 常规 import：本文件所在目录
except NameError:
    # 源码注入通道（引擎 ensureContract / headless preload 都是 exec 源码）里 **没有 __file__**，
    # 直接引用会 NameError 把整个模块炸掉（实测：blender_rt_plan(op="fit_help") 报 "__file__ is not defined"）。
    # 这里退成空串，真正的定位交给 _features_path() 里的 K.runtime_dir。
    _HERE = ""
FEATURES_JSON = os.path.join(_HERE, FEATURES_JSON_NAME)


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
    if mdA["tris_n"] > MATE_MAX_TRIS or mdB["tris_n"] > MATE_MAX_TRIS:
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


# ---------------------------------------------------------------- 几何指纹 / 判定漂移（v0.9.0）

def _r4(x):
    try:
        return round(float(x), 4)
    except Exception:
        return x


def _fingerprint(objects=None, scope=None, include_hidden=False):
    """几何指纹（轻量）：对象数 / 三角面数 / 顶点数 / 世界 bbox + 12 位摘要。

    为什么不用全网格 md5：大场景算一次要几秒；而"改过 / 挪过 / 换过件"这三件事用
    面数+点数+bbox 已经够发现（顶点级的细微漂移交给 audit_drift 的 Chamfer 距离）。
    证据与判定都绑这个摘要 —— 源一变，旧结论就该失效（Procedura state.ts 的 staleness 纪律）。
    """
    import hashlib
    if objects:
        want = [str(x) for x in (objects if isinstance(objects, (list, tuple)) else [objects])]
        objs = [bpy.data.objects.get(n) for n in want]
        objs = [o for o in objs if o is not None and o.type == "MESH"]
    else:
        objs = _scope_objects(scope, include_hidden)
    lines, mn, mx, tris, verts = [], None, None, 0, 0
    for ob in sorted(objs, key=lambda o: o.name):
        t, v = len(ob.data.polygons), len(ob.data.vertices)
        tris += t
        verts += v
        (lo, hi) = _bbox(ob)
        lo, hi = [_r4(x) for x in lo], [_r4(x) for x in hi]
        if mn is None:
            mn, mx = list(lo), list(hi)
        else:
            for k in range(3):
                mn[k] = min(mn[k], lo[k])
                mx[k] = max(mx[k], hi[k])
        lines.append("%s:%d:%d:%.4f,%.4f,%.4f:%.4f,%.4f,%.4f" % (ob.name, t, v, lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]))
    return {"objects": len(objs), "tris": int(tris), "verts": int(verts),
            "bbox": {"min": mn, "max": mx},
            "digest": hashlib.md5("|".join(sorted(lines)).encode("utf-8")).hexdigest()[:12] if lines else "empty",
            "scope": scope, "object_names": [o.name for o in objs][:200],
            "rule": "面数/点数/世界 bbox 变了 → 摘要变 → 绑它的证据与判定视为过期"}


def c_fingerprint(objects=None, scope=None, include_hidden=False):
    """取当前指纹（改动前先记一份，改完再取一份对比即可判定"有没有动过"）。"""
    fp = _fingerprint(objects, scope, include_hidden)
    return _j({"ok": True, "fingerprint": fp,
               "usage": "改前先 fingerprint → 改后再 fingerprint：digest 不同即「源已变」；"
                        "也可以让 ledger 自动帮你比（evidence/verify 都绑了指纹）"})


def _conn_objects(conn):
    return _component_objects(conn["a"]) + _component_objects(conn["b"])


def _union_bbox(objs):
    mn = mx = None
    for ob in objs:
        (lo, hi) = _bbox(ob)
        if mn is None:
            mn, mx = list(lo), list(hi)
        else:
            for k in range(3):
                mn[k] = min(mn[k], lo[k])
                mx[k] = max(mx[k], hi[k])
    return mn, mx


def _snapshot_of(objs):
    mn, mx = _union_bbox(objs)
    if mn is None:
        return None
    size = [float(mx[k] - mn[k]) for k in range(3)]
    import math as _m
    return {"min": [_r4(x) for x in mn], "max": [_r4(x) for x in mx],
            "size": [_r4(x) for x in size], "diagonal": _r4(_m.sqrt(sum(s * s for s in size))),
            "t": _now()}


def _drift_of(conn):
    """与 verify 时记的 bbox 快照比 → (drifted, detail)。跳过的情形都写清理由，不静默。"""
    vd = conn.get("verdict") or {}
    snap = vd.get("bbox_snapshot")
    if not snap:
        return False, {"why": "旧判定没有 bbox 快照 → 不降级（建议重跑 verify 建立快照）"}
    objs = _conn_objects(conn)
    if not objs:
        return False, {"why": "连接涉及的对象已不存在", "objects": 0}
    if any(getattr(ob, "animation_data", None) is not None for ob in objs):
        return False, {"why": "有动画数据：位置由动画驱动，跳过漂移降级"}
    cur = _snapshot_of(objs)
    if cur is None:
        return False, {"why": "对象没有几何（0 面）"}
    diag = float(snap.get("diagonal") or 0) or 1.0
    c0 = [(snap["min"][k] + snap["max"][k]) / 2.0 for k in range(3)]
    c1 = [(cur["min"][k] + cur["max"][k]) / 2.0 for k in range(3)]
    moved = sum((c1[k] - c0[k]) ** 2 for k in range(3)) ** 0.5
    dsz = [abs(float(cur["size"][k]) - float(snap["size"][k])) for k in range(3)]
    size_bad = [k for k in range(3) if dsz[k] > DRIFT_SIZE_FRACTION * max(float(cur["size"][k]), 0.05 * diag)]
    drifted = (moved > DRIFT_CENTER_FRACTION * diag) or bool(size_bad)
    return drifted, {"why": "中心漂移 %.4f（门限 %.4f=对角线 %.0f%%）%s" % (
        moved, DRIFT_CENTER_FRACTION * diag, DRIFT_CENTER_FRACTION * 100,
        ("；尺寸漂移 %s（门限 %.0f%%）" % (["%s %.4f" % ("xyz"[k], dsz[k]) for k in size_bad],
                                        DRIFT_SIZE_FRACTION * 100)) if size_bad else ""),
        "center_moved": _r4(moved), "center_limit": _r4(DRIFT_CENTER_FRACTION * diag),
        "size_delta": [_r4(x) for x in dsz], "snapshot_t": snap.get("t"), "now_t": cur.get("t")}


def _demote_if_drifted(conn, cid):
    """几何漂移 → 把 supported 判定降级为 unresolved（保留旧判定，记历史）。"""
    if conn.get("status") not in ("supported",):
        return False
    drifted, detail = _drift_of(conn)
    if not drifted:
        return False
    conn["history"].append({"t": _now(), "status": "unresolved",
                            "why": "几何漂移 → 判定自动降级：%s" % detail.get("why")})
    vd = conn.get("verdict") or {}
    vd["demoted"] = {"t": _now(), "reason": detail}
    conn["verdict"] = vd
    conn["status"] = "unresolved"
    _log("demote", {"id": cid, "detail": detail})
    return True


# ---------------------------------------------------------------- 证据

def c_evidence(label, view=None, note="", objects=None, scope=None):
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
    # v0.9.0：证据绑几何指纹 —— 源一改，这条证据自动被 ledger 判 stale
    fp = _fingerprint(objects, scope)
    fp_objs = ([str(x) for x in (objects if isinstance(objects, (list, tuple)) else [objects])]
               if objects else None)
    rec = {"label": str(label), "path": path, "md5": h, "bytes": os.path.getsize(path),
           "width": info.get("width"), "height": info.get("height"),
           "view": {k: spec.get(k) for k in ("from", "look_at", "lens", "ortho", "ortho_scale", "shading")},
           "fp": fp, "fp_objects": fp_objs, "note": str(note), "t": _now()}
    _store()["evidence"].append(rec)
    _log("evidence", {"label": label, "md5": h, "fp": fp.get("digest")})
    return _j({"ok": True, "evidence": rec, "total": len(_store()["evidence"])})


def c_ledger(with_stale=True):
    """证据账本。with_stale=True（默认）时逐条与当前几何指纹对拍：源变过的证据标 stale=true。"""
    st = _store()
    out = {"ok": True, "evidence": [], "stale_evidence": 0, "stale_labels": [],
           "demoted_connections": [], "checked": bool(with_stale)}
    cache = {}

    def _cur_global():
        if "scene" not in cache:
            cache["scene"] = _fingerprint(None, None)
        return cache["scene"]
    for e in st["evidence"]:
        rec = dict(e)
        if with_stale and e.get("fp"):
            names = e.get("fp_objects")
            key = "scene" if not names else ",".join(names)
            if key not in cache:
                cache[key] = _cur_global() if not names else _fingerprint(names, None)
            rec["stale"] = (cache[key].get("digest") != e["fp"].get("digest"))
            rec["fp_now"] = cache[key].get("digest")
            if rec["stale"]:
                out["stale_evidence"] += 1
                out["stale_labels"].append(e.get("label"))
        out["evidence"].append(rec)
    for cid, conn in st["connections"].items():
        if _demote_if_drifted(conn, cid):
            out["demoted_connections"].append(cid)
    return _j(out)


def c_status():
    st = _store()
    demoted = []
    for k, v in st["connections"].items():
        if _demote_if_drifted(v, k):
            demoted.append(k)
    conns = {k: {"a": v["a"], "b": v["b"], "status": v["status"], "candidates": v["candidates"],
                 "forbidden": v["forbidden"], "verdict": v["verdict"],
                 "history_len": len(v["history"])} for k, v in st["connections"].items()}
    unresolved = [k for k, v in st["connections"].items() if v["status"] != "supported"]
    return _j({"ok": True, "components": len(st["components"]), "connections": len(st["connections"]),
               "envelopes": len(st["envelopes"]), "evidence": len(st["evidence"]),
               "unresolved_connections": unresolved, "detail": conns,
               "demoted_now": demoted,
               "note": "demoted_now = 本次读取时因几何漂移（中心>10%对角线 或 尺寸>20%）被自动降级的连接"})


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
    # v0.9.0：判定绑 bbox 快照 → 之后几何漂移（中心>10%对角线 / 尺寸>20%）会自动降级为 unresolved
    drifted, drift_detail = _drift_of(conn)
    if drifted:
        why.append("注意：上一版判定的几何已漂移（%s）→ 本次为重判" % drift_detail.get("why"))
    objs = _conn_objects(conn)
    snap = _snapshot_of(objs)
    conn["status"] = verdict
    conn["verdict"] = {"verdict": verdict, "why": why, "err": err, "tolerance": tolerance,
                       "identifiable": ident, "unidentifiable": missing,
                       "probe_err": probe_err, "probe_tolerance": probe_tolerance,
                       "evidence": ev, "bbox_snapshot": snap,
                       "objects": [o.name for o in objs], "drift_of_previous": drift_detail if drifted else None,
                       "note": str(note), "t": _now()}
    conn["history"].append({"t": _now(), "status": verdict, "why": why})
    _log("verify", {"id": conn_id, "verdict": verdict})
    return _j({"ok": True, "connection": conn_id, "verdict": verdict, "why": why,
               "unidentifiable_params": missing, "evidence_used": len(ev),
               "bbox_snapshot": snap,
               "drift_guard": {"center_fraction": DRIFT_CENTER_FRACTION, "size_fraction": DRIFT_SIZE_FRACTION,
                               "note": "之后若几何漂移超门限，status 会在 status/ledger 读取时自动降级为 unresolved，"
                                       "并在 verdict.demoted 里留理由（有动画数据的对象跳过降级）"}})


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


# ============================================================================
# v0.9.0 S3：装配级判据（配合门 / 装配特征库 / 干涉报告）
# ----------------------------------------------------------------------------
# 来源：SpatiaOS/Procedura（MIT）
#   * src/motion/geometry.ts —— analyzeContactRegion / analyzeMateRegistration / computeMeshVolume
#   * OpenSCAD lib/assembly.scad —— 9 个配合特征 + asm_fit 四档 + asm_lead + ISO 273 间隙孔
#
# 为什么把 check_interface 从 bbox 级升级到网格级（本次改动的全部动机）：
#   bbox 只看包围盒。"销插进孔"这种配合两端 bbox 必然重叠 → bbox 口径永远读成"重叠、0 缝"，
#   既分不出 0.15 mm 的 location 配合，也看不出销已经压进母材 3 mm。所以改成：
#     A 侧按**面积加权**采样 → 对 B 的**曲面**求最近点 → 距离场量间隙、射线奇偶判内外。
#
# 三条与上游同口径的不变量（改之前先读这里的理由）：
#   * 接触距离 contact_max = 1.5% × 两件**合并** bbox 对角线（不是各自的对角线之和）
#   * 采样硬上限 4000 点；接触点 < 8 点 → 证据不足 → unresolved
#   * 接触面积占比双向各算一次取**较大**者（小件贴在大件上时，只有小件那侧的读数有意义：
#     大件表面积大，它的"接触占比"天然很小，取最大值相当于按小件归一化 —— 上游注释原文）
# ============================================================================

def _r6(x):
    try:
        return round(float(x), 6)
    except Exception:
        return x


def _units():
    """场景单位换算 → (米/单位, 毫米/单位)。mm 口径的阈值一律经它换算，绝不硬套（项目铁律）。"""
    m = 1.0
    try:
        m = float(bpy.context.scene.unit_settings.scale_length) or 1.0
    except Exception:
        m = 1.0
    return m, m * 1000.0


def _units_block(m_per_unit, mm_per_unit):
    try:
        sys_name = str(bpy.context.scene.unit_settings.system)
    except Exception:
        sys_name = "?"
    return {"m_per_unit": _r6(m_per_unit), "mm_per_unit": _r4(mm_per_unit),
            "scale_length": _r6(m_per_unit), "unit_system": sys_name,
            "note": "铁律：所有 *_mm 字段 = Blender 单位值 × mm_per_unit；本返回体里的长度一律带单位后缀"}


def _mesh_data(objs):
    """一组对象的**求值后**网格 → 世界三角面（numpy）+ 闭合性 + 体积。

    为什么必须用求值网格（evaluated_get）而不是 ob.data：带 Boolean 修改器且未 Apply 的母件，
    ob.data 里**根本没有孔** —— 拿 ob.data 会把"销已经穿模"读成"销悬在空中"。求值网格才是最终形状。
    体积用散度定理（上游 computeMeshVolume 同式）：|Σ a·(b×c)/6|，对闭合网格是体积、对开网格是废数
    → 所以闭合性与体积一起返回，调用方不许把开网格的体积当真。
    """
    import numpy as np
    dg = bpy.context.evaluated_depsgraph_get()
    tris_all, idx_all, base = [], [], 0
    per_object, skipped = {}, []
    for ob in objs:
        if ob.type != "MESH":
            skipped.append({"object": ob.name, "why": "非 MESH 对象（type=%s）" % ob.type})
            continue
        obe = None
        try:
            obe = ob.evaluated_get(dg)
            me = obe.to_mesh()
            if me is None:
                skipped.append({"object": ob.name, "why": "无求值网格"})
                continue
            me.calc_loop_triangles()
            n = len(me.loop_triangles)
            if n == 0:
                skipped.append({"object": ob.name, "why": "0 三角面"})
                continue
            v = np.empty(len(me.vertices) * 3, dtype=np.float64)
            me.vertices.foreach_get("co", v)
            v = v.reshape(-1, 3)
            idx = np.empty(n * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", idx)
            idx = idx.reshape(-1, 3)
            mw = np.array(obe.matrix_world, dtype=np.float64)
            t = (v[idx.reshape(-1)] @ mw[:3, :3].T + mw[:3, 3]).reshape(-1, 3, 3)
            tris_all.append(t)
            idx_all.append(idx + base)      # 各对象顶点区间不重叠 → 闭合性判据不会跨对象缝合
            base += len(v)
            per_object[ob.name] = n
        except Exception as e:
            skipped.append({"object": ob.name, "why": "求值失败: %s" % e})
        finally:
            try:
                if obe is not None:
                    obe.to_mesh_clear()
            except Exception:
                pass
    if not tris_all:
        return None
    tris = np.concatenate(tris_all, axis=0)
    idx = np.concatenate(idx_all, axis=0)
    edges = np.concatenate([idx[:, [0, 1]], idx[:, [1, 2]], idx[:, [2, 0]]], axis=0)
    _u, cnt = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    boundary = int((cnt == 1).sum())
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    vol = float(abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0))
    return {"tris": tris, "tris_n": int(len(tris)), "per_object": per_object,
            "objects": [o.name for o in objs if o.type == "MESH"], "skipped": skipped,
            "boundary_edges": boundary, "closed": boundary == 0, "volume_m3": vol}


def _tris_diagonal(tris):
    import numpy as np
    lo = tris.min(axis=(0, 1))
    hi = tris.max(axis=(0, 1))
    d = hi - lo
    return float(np.sqrt(float((d * d).sum()))), lo, hi


def _sample_surface(tris, n):
    """按面积加权在三角面上取 n 个点（世界坐标）—— 确定性、不用随机数。

    为什么面积加权：上游 sampleContactPoints 是"顶点 + 重心按 stride 抽"，采样密度跟着**顶点密度**
    走 —— 一个大平面（2 个三角）和一根细销（几百个三角）会拿到不成比例的点数，"接触点占比"于是
    随细分程度漂移。这里改成：第 j 个样本落在累积面积区间 (j+0.5)/n 内 → 每个样本代表等量面积，
    占比就是**面积**占比（与尺度和细分无关）。
    三角内的位置用 R2 低差异序列（不是随机数）：确定性 → 自检可重复、同一模型两次跑数一致。
    """
    import numpy as np
    if tris is None or len(tris) == 0 or n <= 0:
        return None
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
    tot = float(area.sum())
    if tot <= 0:
        return None
    cum = np.cumsum(area) / tot
    j = np.arange(int(n), dtype=np.float64)
    u = (j + 0.5) / float(n)
    pick = np.clip(np.searchsorted(cum, u, side="left"), 0, len(tris) - 1)
    r1 = np.mod((j + 0.5) * 0.7548776662466927, 1.0)     # R2 序列的两个系数
    r2 = np.mod((j + 0.5) * 0.5698402909980532, 1.0)
    s = np.sqrt(r1)
    w0, w1, w2 = 1.0 - s, s * (1.0 - r2), s * r2
    return v0[pick] * w0[:, None] + v1[pick] * w1[:, None] + v2[pick] * w2[:, None]


def _bvh(tris):
    """三角面 → BVHTree（mathutils，无新依赖）。顶点不去重：每个三角独立三点，够用且最快。"""
    from mathutils.bvhtree import BVHTree
    import numpy as np
    if tris is None or len(tris) == 0:
        return None
    verts = tris.reshape(-1, 3).tolist()
    polys = np.arange(len(verts), dtype=np.int64).reshape(-1, 3).tolist()
    return BVHTree.FromPolygons(verts, polys)


def _nearest(tree, P):
    """逐点求最近点 → (Q 命中点, N 法线, D 距离)。求不到的点 D=inf（不静默补 0）。"""
    import numpy as np
    n = len(P)
    Q = np.full((n, 3), np.nan)
    N = np.full((n, 3), np.nan)
    D = np.full(n, np.inf)
    if tree is None or n == 0:
        return Q, N, D
    for i, p in enumerate(P.tolist()):
        loc, nor, _ix, dist = tree.find_nearest(p)
        if loc is None:
            continue
        Q[i] = (loc[0], loc[1], loc[2])
        if nor is not None:
            N[i] = (nor[0], nor[1], nor[2])
        D[i] = dist
    return Q, N, D


def _inside_mask(P, tris):
    """点在闭合网格内：固定微斜方向的射线奇偶（Möller–Trumbore，分块向量化）。

    为什么不用"最近点法线点积"判内外：那是**近表面**局部判据，点在凹腔/深埋处法线点积会给错符号
    （销穿过孔底时正好落在这种区域）；射线奇偶对**闭合**网格是全局精确的。
    为什么方向不取 +X：轴对齐射线会正好穿过共边/共顶点把奇偶打翻 —— 用固定微斜方向
    （确定性，不用随机数，自检可重复）。
    分块：一次算 (chunk × M) 的点-三角对，chunk 按 1e6 元素反推，避免大网格爆内存。
    """
    import numpy as np
    n = len(P)
    if tris is None or len(tris) == 0 or n == 0:
        return np.zeros(n, dtype=bool)
    d = np.array([1.0, 0.00031, 0.00057])
    d = d / np.linalg.norm(d)
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    h = np.cross(d, e2)
    a = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(a) > 1e-14
    f = np.zeros_like(a)
    f[ok] = 1.0 / a[ok]
    M = len(tris)
    chunk = max(1, min(n, int(1000000 / max(1, M))))
    hits = np.zeros(n, dtype=np.int64)
    for i0 in range(0, n, chunk):
        s = P[i0:i0 + chunk][:, None, :] - v0[None, :, :]
        u = np.einsum("nmj,mj->nm", s, h) * f[None, :]
        q = np.cross(s, e1[None, :, :])
        v = np.einsum("nmj,j->nm", q, d) * f[None, :]
        t = np.einsum("nmj,mj->nm", q, e2) * f[None, :]
        hit = ok[None, :] & (u >= -1e-12) & (v >= -1e-12) & ((u + v) <= 1.0 + 1e-12) & (t > 1e-12)
        hits[i0:i0 + chunk] = hit.sum(axis=1)
    return (hits % 2) == 1


def _pt_tri_dist(P, tris):
    """点到三角面最近距离（纯 numpy，无 BVH）—— use_bvh=false 的回退通路 + BVH 结果的独立复核。

    算法：Ericson《Real-Time Collision Detection》§5.1.5 的 closest-point-on-triangle。
    顶点/边/面七个区域互斥，级联掩码逐个覆盖即可（后写覆盖前写）。
    为什么留这条通路：BVH 是主力，主力算错时没人会发现 —— 留一条独立算法的实现互相复核，
    并让 use_bvh=false 也有真数字（本仓库的"防 Goodhart"纪律：换一条计算通路复核）。
    """
    import numpy as np
    n = len(P)
    dmin = np.full(n, np.inf)
    if tris is None or len(tris) == 0 or n == 0:
        return dmin
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    ab, ac, bc = b - a, c - a, c - b
    M = len(tris)
    chunk = max(1, min(n, int(1000000 / max(1, M))))

    def _safe(x):
        return np.where(np.abs(x) > 1e-18, x, 1.0)

    for i0 in range(0, n, chunk):
        Pp = P[i0:i0 + chunk][:, None, :]
        ap, bp, cp = Pp - a[None], Pp - b[None], Pp - c[None]
        d1 = np.einsum("nmj,mj->nm", ap, ab); d2 = np.einsum("nmj,mj->nm", ap, ac)
        d3 = np.einsum("nmj,mj->nm", bp, ab); d4 = np.einsum("nmj,mj->nm", bp, ac)
        d5 = np.einsum("nmj,mj->nm", cp, ab); d6 = np.einsum("nmj,mj->nm", cp, ac)
        m_a = (d1 <= 0) & (d2 <= 0)
        m_b = (~m_a) & (d3 >= 0) & (d4 <= d3)
        m_c = (~m_a) & (~m_b) & (d6 >= 0) & (d5 <= d6)
        pre = m_a | m_b | m_c
        vc = d1 * d4 - d3 * d2
        m_ab = (~pre) & (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        pre = pre | m_ab
        vb = d5 * d2 - d1 * d6
        m_ac = (~pre) & (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        pre = pre | m_ac
        va = d3 * d6 - d5 * d4
        m_bc = (~pre) & (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
        denom = va + vb + vc
        inv = 1.0 / _safe(denom)
        Q = a[None] + ab[None] * (vb * inv)[..., None] + ac[None] * (vc * inv)[..., None]
        Q = np.where(m_ab[..., None], a[None] + ab[None] * (d1 / _safe(d1 - d3))[..., None], Q)
        Q = np.where(m_ac[..., None], a[None] + ac[None] * (d2 / _safe(d2 - d6))[..., None], Q)
        Q = np.where(m_bc[..., None], b[None] + bc[None] * ((d4 - d3) / _safe((d4 - d3) + (d5 - d6)))[..., None], Q)
        Q = np.where(m_c[..., None], c[None], Q)
        Q = np.where(m_b[..., None], b[None], Q)
        Q = np.where(m_a[..., None], a[None], Q)
        dmin[i0:i0 + chunk] = np.linalg.norm(Pp - Q, axis=2).min(axis=1)
    return dmin


def _overlap_estimate(trisA, trisB, volA, volB, closedA, closedB, samples=VOL_SAMPLES):
    """重叠体积估计：A/B 世界 AABB **交集盒**内低差异采样 + 双重内测试。

    上游用 OpenSCAD 的 intersection() 重编译拿精确重叠体积（要再跑一遍 CSG）；本仓库没有 CSG 编译器，
    改成蒙特卡洛：交集盒里取 samples 个低差异点 → 射线奇偶判"同时在 A 内且在 B 内"的比例 × 盒体积
    = 重叠体积的**无偏估计**。同时报二项标准误 stderr —— 这是估计值，不是精确值，别当精确值用。
    体积占比的分母取 min(volA, volB)（"占小件的多少"才有工程意义）；分母不可靠时给 None + 理由。
    """
    import numpy as np
    out = {"samples": int(samples), "box_volume_mm3": None, "overlap_volume_mm3": None,
           "frac_of_box": None, "frac_of_smaller": None, "stderr_frac": None,
           "reliable": False, "why": []}
    if trisA is None or trisB is None or len(trisA) == 0 or len(trisB) == 0:
        out["why"].append("一端没有三角面")
        return out
    loA, hiA = trisA.min(axis=(0, 1)), trisA.max(axis=(0, 1))
    loB, hiB = trisB.min(axis=(0, 1)), trisB.max(axis=(0, 1))
    lo = np.maximum(loA, loB); hi = np.minimum(hiA, hiB)
    if np.any(hi <= lo):
        out["box_volume_mm3"] = 0.0
        out["overlap_volume_mm3"] = 0.0
        out["frac_of_box"] = 0.0
        out["frac_of_smaller"] = 0.0
        out["reliable"] = True
        out["why"].append("两 AABB 不相交 → 重叠体积必为 0（精确，不用采样）")
        return out
    n = int(max(32, samples))
    j = np.arange(1, n + 1, dtype=np.float64)

    def vdc(base):
        x = j.copy(); o = np.zeros(n); f = 1.0 / base
        while x.max() > 0:
            o += f * np.mod(x, base); x = np.floor(x / base); f /= base
        return o
    frac = np.stack([vdc(2), vdc(3), vdc(5)], axis=1)
    P = lo[None, :] + (hi - lo)[None, :] * frac
    inA = _inside_mask(P, trisA)
    inB = _inside_mask(P, trisB)
    both = int((inA & inB).sum())
    f = both / float(n)
    box_vol = float(np.prod(hi - lo))
    ov = box_vol * f
    _m_per_unit, mm_per_unit = _units()
    mm3 = mm_per_unit ** 3   # 体积是长度的**三次方**：这里必须 mm_per_unit³。
    # 实测踩过的坑：写成 mm_per_unit（1000 而不是 1e9）→ 400 mm³ 的重叠被报成 0.0004 mm³，
    # 而占比是对的（无量纲）→ 数字看着"合理"却差 1e6 倍，自检里专门加了一条断言钉住它。
    out["box_volume_mm3"] = _r4(box_vol * mm3)
    out["overlap_volume_mm3"] = _r4(ov * mm3)
    out["frac_of_box"] = _r4(f)
    out["stderr_frac"] = _r6(math.sqrt(max(f * (1.0 - f), 0.0) / n))
    small = min(float(volA or 0.0), float(volB or 0.0))
    if small > 0:
        out["frac_of_smaller"] = _r4(ov / small)
    else:
        out["why"].append("小件体积为 0 → 不给占比（不给假数）")
    out["reliable"] = bool(closedA and closedB and small > 0)
    if not (closedA and closedB):
        out["why"].append("有一端网格不闭合（有边界边）→ 射线奇偶与体积都只是粗估")
    if f == 0.0:
        out["why"].append("交集盒内 %d 个采样点没有一个同时落在两件内 → 重叠 < 盒体积的 1/%d" % (n, n))
    return out


def _mate_measure(mdA, mdB, mm_per_unit, PA, PB, contact_max_m):
    """两件在**真实网格**上的公共量测：接触点 / 间隙分布(mm) / 穿透(mm) / 重叠体积估计。

    为什么单独抽出来：c_mate_check（判三态）与 c_check_interface（把老的 bbox 口径补上网格口径）
    量的是同一件事，抄两份必然漂移 —— 一份实现、两处调用，改判据只改这里。
    返回 {"used_a","used_b","contact","gap","penetration","overlap","min_a_mm","contact_max_m"}。
    """
    import numpy as np
    QA, _NA, DA = _nearest(_bvh(mdB["tris"]), PA)
    QB, _NB, DB = _nearest(_bvh(mdA["tris"]), PB)
    iA = np.where(DA <= contact_max_m)[0]
    iB = np.where(DB <= contact_max_m)[0]
    fracA = float(len(iA)) / float(len(PA))
    fracB = float(len(iB)) / float(len(PB))
    min_a_mm = (float(np.nanmin(DA)) * mm_per_unit) if np.isfinite(DA).any() else None
    cA = PA[iA] if len(iA) else None
    cB = PB[iB] if len(iB) else None
    if cA is not None:
        anchor, anchor_src = cA.mean(axis=0), "A侧接触点均值"
    elif cB is not None:
        anchor, anchor_src = cB.mean(axis=0), "B侧接触点均值（A侧无接触点）"
    else:
        anchor, anchor_src = None, "无接触点"
    normal = None
    if cA is not None and cB is not None:
        dv = cA.mean(axis=0) - cB.mean(axis=0)
        nv = float(np.linalg.norm(dv))
        normal = (dv / nv) if nv > 1e-9 else None
    gap_units = np.concatenate([DA[iA], DB[iB]]) if (len(iA) or len(iB)) else np.zeros(0)
    gap_mm = gap_units * mm_per_unit
    if len(gap_mm):
        p10, p50, p90 = np.percentile(gap_mm, [10, 50, 90])
        gap = {"n": int(len(gap_mm)), "median_mm": _r4(p50), "mean_mm": _r4(gap_mm.mean()),
               "min_mm": _r4(gap_mm.min()), "max_mm": _r4(gap_mm.max()),
               "p10_mm": _r4(p10), "p90_mm": _r4(p90),
               "median_a_mm": _r4(np.median(DA[iA] * mm_per_unit)) if len(iA) else None,
               "median_b_mm": _r4(np.median(DB[iB] * mm_per_unit)) if len(iB) else None,
               "source": "A+B 双向接触点距离合并"}
    else:
        gap = {"n": 0, "median_mm": None, "mean_mm": None, "min_mm": None, "max_mm": None,
               "p10_mm": None, "p90_mm": None, "median_a_mm": None, "median_b_mm": None,
               "source": "无接触点 → 没有间隙可报"}
    pen = {"status": "skipped", "tested": False, "points": 0, "depth_max_mm": None,
           "depth_mean_mm": None, "surface_inside_frac": None, "direction": "A→B + B→A",
           "rule": "内外=射线奇偶（闭合网格全局精确）；深度=最近点距离（SDF 近似）"}
    if mdA["tris_n"] > INSIDE_MAX_TRIS or mdB["tris_n"] > INSIDE_MAX_TRIS:
        pen["status"] = "skipped_too_big"
        pen["why"] = "网格 %d/%d 面 > 内测试上限 %d → 未做侵入判定" % (mdA["tris_n"], mdB["tris_n"], INSIDE_MAX_TRIS)
    elif not (mdA["closed"] and mdB["closed"]):
        pen["status"] = "unreliable_mesh"
        pen["why"] = "网格不闭合（边界边 A=%d / B=%d）→ 射线奇偶不可靠，侵入结论不成立" % (
            mdA["boundary_edges"], mdB["boundary_edges"])
    else:
        inB = _inside_mask(PA, mdB["tris"])
        inA = _inside_mask(PB, mdA["tris"])
        pa = np.where(inB & np.isfinite(DA))[0]
        pb = np.where(inA & np.isfinite(DB))[0]
        dep = np.concatenate([DA[pa], DB[pb]]) if (len(pa) or len(pb)) else np.zeros(0)
        pen["status"] = "ok"
        pen["tested"] = True
        pen["points"] = int(len(dep))
        pen["points_a_in_b"] = int(len(pa))
        pen["points_b_in_a"] = int(len(pb))
        pen["surface_inside_frac"] = _r6(float(len(dep)) / float(len(PA) + len(PB)))
        pen["depth_max_mm"] = _r4(dep.max() * mm_per_unit) if len(dep) else 0.0
        pen["depth_mean_mm"] = _r4(dep.mean() * mm_per_unit) if len(dep) else 0.0
        pen["mesh_closed"] = {"a": mdA["closed"], "b": mdB["closed"]}
    contact = {"close_a": int(len(iA)), "close_b": int(len(iB)),
               "area_fraction_a": _r6(fracA), "area_fraction_b": _r6(fracB),
               "contact_area_fraction": _r6(max(fracA, fracB)),
               "rule": "双向各算一次取较大者（上游：按小件归一化，避免被大件表面积稀释）",
               "anchor_world": ([_r6(x) for x in anchor] if anchor is not None else None),
               "anchor_source": anchor_src,
               "contact_normal": ([_r6(x) for x in normal] if normal is not None else None),
               "contact_normal_rule": "A/B 两团接触点均值之差归一化（两团重合会退化，此时给 null）"}
    ov = None
    if mdA["tris_n"] + mdB["tris_n"] <= VOL_MAX_TRIS:
        ov = _overlap_estimate(mdA["tris"], mdB["tris"], mdA["volume_m3"], mdB["volume_m3"],
                               mdA["closed"], mdB["closed"])
    return {"used_a": int(len(PA)), "used_b": int(len(PB)), "contact": contact, "gap": gap,
            "penetration": pen, "overlap": ov, "min_a_mm": min_a_mm, "contact_max_m": contact_max_m}


def _fit_value(cls):
    """asm_fit(cls) 四档（原样搬运 assembly_features.json 的数值）；未知档位按上游回落 location。"""
    c = str(cls) if cls is not None else None
    if c in FIT_TABLE:
        return FIT_TABLE[c], (None if c == FIT_DEFAULT_CLS else "档位=%s" % c)
    return FIT_TABLE[FIT_DEFAULT_CLS], "未知 fit 档 '%s' → 按上游 asm_fit 回落 location(0.15 mm)" % cls


def _aabb_pairs(objs):
    """AABB 沿 x 扫描剪枝 → 候选下标对。与 check_interference 同一套粗筛（老 op 未改动）。"""
    import numpy as np
    n = len(objs)
    if n < 2:
        return []
    mn = np.empty((n, 3)); mx = np.empty((n, 3))
    for i, ob in enumerate(objs):
        (a, b) = _bbox(ob)
        mn[i] = a; mx[i] = b
    order = np.argsort(mn[:, 0])
    out = []
    for ii in range(n):
        i = int(order[ii]); jj = ii + 1
        while jj < n:
            j = int(order[jj])
            if mn[j][0] > mx[i][0]:
                break
            if (mn[i][1] <= mx[j][1] and mx[i][1] >= mn[j][1]
                    and mn[i][2] <= mx[j][2] and mx[i][2] >= mn[j][2]):
                out.append((i, j))
            jj += 1
    return out


def _iface_hint(contact, gap, pen):
    """check_interface 的一行判读提示（不下结论，只把数字翻译成人话 —— 结论归 mate_check）。"""
    if gap.get("n") == 0:
        return "没搭上：接触带内 0 个采样点（两件在网格上根本没靠近）"
    dep = pen.get("depth_max_mm")
    if pen.get("tested") and dep is not None and dep > MATE_DEPTH_EPS_MM:
        return "有侵入：最大穿透 %.3f mm > %.2f mm 门限（销压进母材，不是配合）" % (dep, MATE_DEPTH_EPS_MM)
    return "有接触：单边中位间隙 %.4f mm，接触面积占比 %.3f，最大穿透 %s mm" % (
        gap.get("median_mm") or 0.0, contact.get("contact_area_fraction") or 0.0,
        pen.get("depth_max_mm") if pen.get("tested") else "未测")


def c_mate_check(conn_id, fit=None, nominal=None, tol_mm=None, samples=4000,
                 contact_max_mm=None, depth_eps_mm=None):
    """配合门（网格级）：在**实际网格**上量接触 / 间隙 / 侵入，按 fit 档判三态。

    量法（原理写在这里，改算法先改这段）：
      1. 采样：A 侧按**面积加权**取 ≤4000 点（_sample_surface，确定性低差异序列）
         —— 每个样本代表等量面积，所以"接触点占比"就是面积占比。
      2. 最近点：mathutils BVHTree.FromPolygons 建 B 的**曲面** BVH → 逐点 find_nearest
         → 距离 ≤ contact_max（默认 1.5% × 两件合并 bbox 对角线）的算接触点。
      3. 内外：射线奇偶（_inside_mask，Möller–Trumbore 向量化）—— 全局判据，深埋在凹腔里也不判反；
         深度量值用最近点距离（SDF 近似）。两者合起来才是"侵入多深"。
      4. 单边间隙：接触点距离的均值/中位数（mm 口径，按场景 scale_length 换算，返回带 units 块）。

    判据顺序（每一步的为什么都写在这里；要放宽判据必须先改这段注释并给出实测依据）：
      0) 连接未注册 / 组件空 / 网格超上限 / contact_max≤0 → ok:false + verdict=unresolved（空产出不静默成功）
      1) 采样 < 8 点 → unresolved（上游 CONTACT_MIN_POINTS；2 个点也能算出数字，但那是噪声）
      2) fit 与 nominal 都没给 → unresolved（没有期望值就没法判"对不对"，不许二选一）
      3) 接触点 0 个 → **refuted**，点名"没搭上"。这条先于间隙判定：完全没接触时中位数无意义
      4) 穿透深度 > 允许深度 → **refuted**（销压进母材不叫配合）
         允许深度 = depth_eps_mm（默认 0.2 mm）；fit=press（过盈）时放宽到 max(eps, |nominal|+tol)
      5) 单边中位间隙 ∈ [nominal-tol, nominal+tol] → supported；否则 refuted（附实测数字）
      附加诚实条款：没做成侵入判定（网格超 INSIDE_MAX_TRIS / 网格不闭合）时，间隙达标也只能给
         unresolved（"没查穿模"不能读成"通过" —— 与 audit.py "未分析绝不能读成已连通"同一纪律）。

    tol_mm 默认 0.15 mm 的取值理由：四档 fit 的最小间距只有 0.05 mm（location 0.15 ↔ snap 0.20），
      而网格实测的噪声底（三角化弦差 + 采样稀疏）就在这个量级 —— 所以默认容差**不是**用来分辨档位的，
      而是"测量 + 工艺"带：FDM 尺寸精度典型 ±0.1~0.2 mm，取中值 0.15；它恰好又等于 location 的标称值，
      于是 supported 带 = [0.00, 0.30] mm，读作"贴住而未压死"。要分辨档位请显式传更小的 tol_mm
      （如 0.03），并接受"网格噪声可能先把档位判错"这个前提。

    **共享标称纪律**（assembly_features.json 明文的第 1 条）：公母必须从同一个 nominal 派生
      （母 = nominal + 2×asm_fit(cls)，公 = nominal），禁止两处各写一个字面量。
      本 op 量的"单边中位间隙"就是这条纪律的体检指标 —— 偏离 nominal ± tol 就是它被破了。
      返回体 verdict 里带 shared_nominal_rule 原文，供调用方原样转述给人。

    只读：不改连接状态（要落状态用 verify/advance），只记一条 log。
    """
    import numpy as np
    st = _store()
    conn = st["connections"].get(str(conn_id))
    if not conn:
        return _j({"ok": False, "verdict": "unresolved", "connection": conn_id, "measured": False,
                   "error": "连接未注册: %s" % conn_id,
                   "why": ["连接未注册 → 没有可测的两端（空产出，不给判定）"],
                   "free_params": [{"param": "conn_id", "why": "先 register_component + register_connection 建契约"}]})
    allA, allB = _component_objects(conn["a"]), _component_objects(conn["b"])
    A = [o for o in allA if o.type == "MESH"]
    B = [o for o in allB if o.type == "MESH"]
    m_per_unit, mm_per_unit = _units()
    ub = _units_block(m_per_unit, mm_per_unit)
    if not A or not B:
        return _j({"ok": False, "verdict": "unresolved", "connection": conn_id, "measured": False,
                   "error": "组件没有可测网格对象（空产出 → 不给判定）", "units": ub,
                   "a": {"id": conn["a"], "registered": len(allA), "mesh": len(A)},
                   "b": {"id": conn["b"], "registered": len(allB), "mesh": len(B)},
                   "why": ["a=%s 网格对象 %d 个 / b=%s 网格对象 %d 个 → 量不了，不许二选一"
                           % (conn["a"], len(A), conn["b"], len(B))],
                   "free_params": [{"param": "组件 objects", "why": "把实际参与配合的对象名写进 register_component"}]})
    mdA, mdB = _mesh_data(A), _mesh_data(B)
    if mdA is None or mdB is None or mdA["tris_n"] == 0 or mdB["tris_n"] == 0:
        return _j({"ok": False, "verdict": "unresolved", "connection": conn_id, "measured": False,
                   "error": "两端之一没有三角面（求值后为空）",
                   "a_tris": (mdA or {}).get("tris_n"), "b_tris": (mdB or {}).get("tris_n"),
                   "skipped": (mdA or {}).get("skipped"), "units": ub})
    if mdA["tris_n"] > MATE_MAX_TRIS or mdB["tris_n"] > MATE_MAX_TRIS:
        return _j({"ok": False, "verdict": "unresolved", "connection": conn_id, "measured": False,
                   "error": "网格过大，不判（大网格一律 analyzed=false，绝不静默降级）",
                   "a_tris": mdA["tris_n"], "b_tris": mdB["tris_n"], "limit": MATE_MAX_TRIS, "units": ub})
    diag_m, _lo, _hi = _tris_diagonal(np.concatenate([mdA["tris"], mdB["tris"]], axis=0))
    contact_max_m = (float(contact_max_mm) / mm_per_unit) if contact_max_mm is not None \
        else DEFAULT_CONTACT_DISTANCE_FRAC * diag_m
    if not (contact_max_m > 0):
        return _j({"ok": False, "verdict": "unresolved", "connection": conn_id, "measured": False,
                   "error": "contact_max ≤ 0（合并 bbox 对角线为 0 或入参非法）", "units": ub})
    used_req = int(samples) if samples else 0
    n = int(max(1, min(used_req, CONTACT_SAMPLE_CAP)))
    PA = _sample_surface(mdA["tris"], n)
    PB = _sample_surface(mdB["tris"], n)
    if PA is None or PB is None:
        return _j({"ok": False, "verdict": "unresolved", "connection": conn_id, "measured": False,
                   "error": "采样失败（零面积网格？）", "units": ub})
    meas = _mate_measure(mdA, mdB, mm_per_unit, PA, PB, contact_max_m)
    contact, gap, pen, ov = meas["contact"], meas["gap"], meas["penetration"], meas["overlap"]
    fracA, fracB = contact["area_fraction_a"], contact["area_fraction_b"]
    used_min = min(meas["used_a"], meas["used_b"])
    free = []
    if n < CONTACT_SAMPLE_CAP:
        free.append({"param": "samples", "now": n, "max": CONTACT_SAMPLE_CAP,
                     "why": "采样越少，接触占比与中位间隙的方差越大"})
    if fit is None and nominal is None:
        free.append({"param": "nominal/fit", "why": "给 fit 档（clearance|location|press|snap）或直接给期望单边间隙 nominal(mm)"})
    if tol_mm is None:
        free.append({"param": "tol_mm", "now": FIT_DEFAULT_TOL_MM,
                     "why": "默认是测量+工艺带；要分辨 fit 档位需更小（如 0.03），但网格噪声可能先到"})
    if contact_max_mm is None:
        free.append({"param": "contact_max_mm", "now": _r4(contact_max_m * mm_per_unit),
                     "why": "默认 1.5%×合并对角线；细小特征（薄壁/小孔）想更严就调小"})
    if pen["status"] != "ok":
        free.append({"param": "网格（闭合/降面数）", "why": pen.get("why", "未做侵入判定")})
    # ---- 三态判定 ----
    why, verdict = [], None
    if nominal is not None:
        expected_mm, fit_note = float(nominal), "caller 直接给的期望单边间隙"
    elif fit is not None:
        expected_mm, fit_note = _fit_value(fit)
    else:
        expected_mm, fit_note = None, None
    tol_used = float(tol_mm) if tol_mm is not None else FIT_DEFAULT_TOL_MM
    tol_src = "caller" if tol_mm is not None else "default"
    eps_mm = float(depth_eps_mm) if depth_eps_mm is not None else MATE_DEPTH_EPS_MM
    allowed_mm = eps_mm if (expected_mm is None or expected_mm >= 0) else max(eps_mm, abs(expected_mm) + tol_used)
    if used_min < CONTACT_MIN_POINTS:
        verdict = "unresolved"
        why.append("采样不足 %d 点（实测 A=%d / B=%d 点）→ 证据不足，不许二选一（上游 CONTACT_MIN_POINTS）"
                   % (CONTACT_MIN_POINTS, len(PA), len(PB)))
    elif expected_mm is None:
        verdict = "unresolved"
        why.append("既没给 fit 也没给 nominal → 没有期望单边间隙可比，不许二选一")
    elif gap["n"] == 0:
        verdict = "refuted"
        why.append("**完全没搭上**：A/B 双向在 %.3f mm 接触带内一个采样点都没有（接触点 0/%d，A 侧最近 %s mm）"
                   % (contact_max_m * mm_per_unit, used_min,
                      ("%.3f" % meas["min_a_mm"]) if meas.get("min_a_mm") is not None else "∞"))
    elif pen["tested"] and pen["depth_max_mm"] is not None and pen["depth_max_mm"] > allowed_mm:
        verdict = "refuted"
        why.append("侵入过深：最大穿透 %.3f mm > 允许 %.3f mm（%s）→ 销压进母材，这不是配合"
                   % (pen["depth_max_mm"], allowed_mm,
                      "fit 过盈档放宽后" if allowed_mm > eps_mm else "depth_eps 默认 0.2 mm"))
    else:
        lo_mm, hi_mm = expected_mm - tol_used, expected_mm + tol_used
        med = gap["median_mm"]
        if pen["status"] != "ok":
            verdict = "unresolved"
            why.append("实测单边中位间隙 %.4f mm 落在 [%.4f, %.4f]（期望 %.4f ± %.4f）内，但%s → "
                       "只判了间隙、没判穿模，不能读成 supported"
                       % (med, lo_mm, hi_mm, expected_mm, tol_used, pen.get("why", "未做侵入判定")))
        elif lo_mm <= med <= hi_mm:
            verdict = "supported"
            why.append("实测单边中位间隙 %.4f mm（均值 %.4f，n=%d）落在期望 %.4f ± %.4f mm 内 → supported；"
                       "接触面积占比 %.3f，最大穿透 %.3f mm ≤ 允许 %.3f mm"
                       % (med, gap["mean_mm"], gap["n"], expected_mm, tol_used, max(fracA, fracB),
                          pen["depth_max_mm"], allowed_mm))
        else:
            verdict = "refuted"
            why.append("实测单边中位间隙 %.4f mm（均值 %.4f，n=%d）落在 [%.4f, %.4f]（期望 %.4f ± %.4f）之外 → refuted"
                       % (med, gap["mean_mm"], gap["n"], lo_mm, hi_mm, expected_mm, tol_used))
    if fit_note:
        why.append(fit_note)
    if nominal is None and fit is not None:
        why.append("nominal 由 fit 表推导（assembly_features.json: fits.%s.per_side_mm = %.2f mm，单边）"
                   % (fit if str(fit) in FIT_TABLE else FIT_DEFAULT_CLS, expected_mm))
    srule = "共享标称纪律：公母必须从同一个 nominal 派生（母 = nominal + 2×asm_fit(cls)，公 = nominal）" \
            "，禁止两处各写一个字面量 —— 本 op 量的单边中位间隙就是这条纪律的体检指标。"
    ev = {"units": ub,
          "contact_max_m": _r6(contact_max_m), "contact_max_mm": _r4(contact_max_m * mm_per_unit),
          "contact_max_rule": "1.5%% × 两件合并 bbox 对角线（当前 %.3f mm）%s"
                              % (diag_m * mm_per_unit, "，被调用方覆盖" if contact_max_mm is not None else ""),
          "merged_diagonal_mm": _r4(diag_m * mm_per_unit),
          "samples": {"requested": used_req, "cap": CONTACT_SAMPLE_CAP, "used_a": int(len(PA)),
                      "used_b": int(len(PB)), "samples_used": int(used_min),
                      "rule": "面积加权 + R2 低差异序列（确定性）；硬上限 %d 点（上游同口径）" % CONTACT_SAMPLE_CAP},
          "contact": contact,
          "gap_mm": gap, "penetration": pen,
          "overlap_volume": (ov or {"skipped": True, "why": "三角面 > %d，跳过蒙特卡洛体积估计" % VOL_MAX_TRIS}),
          "fit": {"class": (str(fit) if fit is not None else None),
                  "table_per_side_mm": dict(FIT_TABLE),
                  "expected_gap_mm": expected_mm, "expected_source": fit_note or "caller/none",
                  "tol_mm": tol_used, "tol_source": tol_src,
                  "band_mm": ([_r4(expected_mm - tol_used), _r4(expected_mm + tol_used)] if expected_mm is not None else None),
                  "allowed_penetration_mm": _r4(allowed_mm)},
          "shared_nominal_rule": srule,
          "verdict_inputs": {"samples_used": int(used_min), "contact_points": gap["n"],
                             "median_gap_mm": gap["median_mm"], "depth_max_mm": pen.get("depth_max_mm")},
          "objects": {"a": mdA["objects"], "b": mdB["objects"], "a_tris": mdA["tris_n"], "b_tris": mdB["tris_n"],
                      "closed": {"a": mdA["closed"], "b": mdB["closed"]},
                      "volume_mm3": {"a": _r4(mdA["volume_m3"] * (mm_per_unit ** 3)),
                                     "b": _r4(mdB["volume_m3"] * (mm_per_unit ** 3))}}}
    res = {"ok": True, "measured": True, "connection": conn_id, "a": conn["a"], "b": conn["b"],
           "verdict": verdict, "why": why, "confidence": ("full" if pen["status"] == "ok" else "reduced"),
           "evidence": ev, "free_params": free, "shared_nominal_rule": srule,
           "readonly": "本 op 不改连接状态（要落状态用 verify/advance），只记 log",
           "drift_guard": "几何改了要重跑：本 op 每次都在当前求值网格上实测，不缓存旧数字"}
    _log("mate_check", {"id": conn_id, "verdict": verdict, "samples": int(used_min),
                        "median_gap_mm": gap["median_mm"], "depth_max_mm": pen.get("depth_max_mm")})
    return _j(res)


def _features_path():
    """定位同目录的 assembly_features.json。

    为什么不能只写 os.path.dirname(__file__)：本模块在**源码注入**通道下运行（引擎 ensureContract /
    headless preload 都是把源码拼进临时脚本 exec），此时 __file__ 指向那个临时脚本
    （实测踩坑：D:\\DSH\\blender\\tmp\\dsh_headless_*.py），dirname 出来的目录里当然没有特征库。
    所以依次试：__file__ 同目录 → K.runtime_dir（引擎注入的权威 runtime 目录，qc.py 加载同目录
    模块用的就是它）→ 相对本文件 ../runtime（tests/ 下直接跑时）。
    """
    cands = []
    if _HERE:
        cands.append(_HERE)
        cands.append(os.path.join(os.path.dirname(_HERE), "runtime"))
    K = _kernel()
    rd = getattr(K, "runtime_dir", None) if K is not None else None
    if rd:
        cands.insert(0, str(rd))
    for c in cands:
        try:
            if c and os.path.isfile(os.path.join(c, FEATURES_JSON_NAME)):
                return os.path.join(c, FEATURES_JSON_NAME)
        except Exception:
            continue
    return FEATURES_JSON if cands else FEATURES_JSON_NAME


def c_fit_help(fit=None, feature=None, m=None, d=None):
    """装配特征库速查（读同目录 assembly_features.json）。

    为什么做成数据文件而不是把数字抄进代码：配合件对不上的头号原因不是公差选错，而是公母两处各写了
    一个字面量（改一处忘一处）。mate_check / fit_help / 生成器读**同一份**数值，才不会各自漂移。
    返回里每个特征都带「Blender 侧建议怎么造」（cylinder/cube + boolean 的对应关系）。
    """
    path = _features_path()
    if not os.path.isfile(path):
        return _j({"ok": False, "error": "特征库文件不存在: %s" % path,
                   "tried": [FEATURES_JSON, "K.runtime_dir"], "file": FEATURES_JSON_NAME,
                   "hint": "runtime/assembly_features.json 应与 contract.py 同目录"})
    try:
        with open(path, encoding="utf-8") as f:
            lib = json.load(f)
    except Exception as e:
        return _j({"ok": False, "error": "特征库读取失败: %s" % e, "path": path})
    out = {"ok": True, "path": path, "schema": lib.get("schema"), "units": lib.get("units"), "schema": lib.get("schema"), "units": lib.get("units"),
           "shared_nominal_rule": lib.get("shared_nominal_rule"),
           "fits": lib.get("fits"), "lead_chamfer": lib.get("lead_chamfer"),
           "iso273_clearance_diameter": lib.get("iso273_clearance_diameter"),
           "fit_default_cls": lib.get("fit_default_cls"),
           "features_index": [{"name": x.get("name"), "kind": x.get("kind"), "cn": x.get("cn"),
                               "pairs_with": x.get("pairs_with"), "blender": x.get("blender")}
                              for x in (lib.get("features") or [])],
           "blender_general": lib.get("blender_general"),
           "source": lib.get("source")}
    if m is not None:
        try:
            mm_ = float(m)
        except Exception:
            return _j({"ok": False, "error": "m 必须是数字（公制螺纹标称直径 mm）", "m": m})
        br = lib.get("iso273_clearance_diameter", {}).get("branches") or []
        off = None
        for b in br:
            if b.get("m_le") is None or mm_ <= float(b["m_le"]):
                off = float(b["offset_mm"]); break
        out["iso273_query"] = {"m": mm_, "offset_mm": off, "clearance_diameter_mm": (mm_ + off) if off is not None else None,
                               "formula": lib.get("iso273_clearance_diameter", {}).get("formula"),
                               "blender": "cutter = primitive_cylinder_add(radius=clearance_d/2, depth=depth) + cone 入口漏斗（lead=0.6）→ BOOLEAN/DIFFERENCE"}
    if d is not None:
        try:
            dd = float(d)
        except Exception:
            return _j({"ok": False, "error": "d 必须是数字（被倒角处的直径 mm）", "d": d})
        out["lead_query"] = {"d": dd, "lead_mm": round(max(0.6, 0.15 * dd), 4),
                             "formula": "asm_lead(d) = max(0.6, 0.15*d)",
                             "blender": "公件尖端 cone(radius1=d/2, radius2=max(0.2,d-2*lead)/2, depth=lead)；母件口部 cone(radius1=bore/2, radius2=(bore+2*lead)/2)"}
    if fit is not None:
        v, note = _fit_value(fit)
        out["fit_query"] = {"class": str(fit), "per_side_mm": v, "note": note,
                            "bore_rule": "母件孔径 = nominal + 2×%.2f mm（单边 %.2f）" % (v, v),
                            "mate_check_usage": "mate_check(conn_id, fit=\"%s\") → 期望单边间隙 %.2f mm"
                                                % (str(fit), v),
                            "tol_default_mm": FIT_DEFAULT_TOL_MM}
    if feature is not None:
        hit = [x for x in (lib.get("features") or []) if str(x.get("name")) == str(feature)]
        if not hit:
            return _j({"ok": False, "error": "特征不存在: %s" % feature,
                       "available": [x.get("name") for x in (lib.get("features") or [])]})
        out["feature"] = hit[0]
    out["usage"] = "fit_help() 全量速查 · fit_help(fit=\"location\") 单档 · fit_help(feature=\"socket\") 单特征 · fit_help(m=3) ISO 间隙孔 · fit_help(d=8) 导向倒角"
    return _j(out)


def _declared_index():
    """设计意图索引：对象名 → [(连接 id, 端)]，以及 (a名,b名) 对 → [连接 id]。只读 K.dsh_contract。"""
    st = _store()
    side, pairs, comp_of = {}, {}, {}
    for cid, comp in st["components"].items():
        for nm in comp.get("objects", []):
            comp_of.setdefault(nm, []).append(cid)
    for cid, conn in st["connections"].items():
        na = [o.name for o in _component_objects(conn["a"])]
        nb = [o.name for o in _component_objects(conn["b"])]
        for nm in na:
            side.setdefault(nm, []).append({"connection": cid, "side": "a", "component": conn["a"]})
        for nm in nb:
            side.setdefault(nm, []).append({"connection": cid, "side": "b", "component": conn["b"]})
        for x in na:
            for y in nb:
                pairs.setdefault((x, y), []).append(cid)
                pairs.setdefault((y, x), []).append(cid)
    return side, pairs, comp_of


def _severity(depth_mm, frac):
    """严重度三档。阈值理由：2 mm ≈ 常见打印壁厚（穿透 2 mm 就是打穿一层壁）；
    0.5 mm ≈ 肉眼可见的缝隙量级；体积占比 25% ≈ 小件被埋掉四分之一；2% ≈ 一小条。"""
    hi = (depth_mm is not None and depth_mm > SEV_DEPTH_HIGH_MM) or (frac is not None and frac > SEV_FRAC_HIGH)
    med = (depth_mm is not None and depth_mm > SEV_DEPTH_MED_MM) or (frac is not None and frac > SEV_FRAC_MED)
    return "high" if hi else ("medium" if med else "low")


def c_interference_report(scope=None, use_bvh=True, limit=60, declared_ok=True,
                          depth_eps=None, include_hidden=False, samples=VOL_SAMPLES):
    """逐对干涉报告：穿透深度(mm) + 重叠体积占比 + 严重度排序 + 分组 + 设计意图白名单。

    与老 op check_interference 的分工：老 op 只回答"哪两对相交"（**返回结构一字未动**，向后兼容）；
    本 op 回答"相交多深 / 占多少体积 / 哪对最该管 / 哪些本来就是设计内的"。

    穿透深度怎么量（原理）：
      * 内外：射线奇偶（_inside_mask）—— 对闭合网格全局精确，深埋在凹腔里也不会判反。
      * 量值：BVH 最近点距离（SDF 近似）。方向先 A→B；A 侧表面没埋进去、但 BVH 确实报相交时，
        再量 B→A（小件整个被大件包住时只有 B→A 有读数）。
      * use_bvh=false：退回纯 numpy 的点-三角面最近距离（_pt_tri_dist）—— 独立算法，也用来复核 BVH。
    重叠体积怎么量：没有 CSG 编译器，就在 A/B 世界 AABB 的**交集盒**里低差异采样，
      射线奇偶判"同时在两者内"的比例 × 盒体积 = 无偏估计，同时报二项标准误（是估计值不是精确值）。

    设计意图白名单（declared）：a、b 分属某条已注册连接的两端 → declared=true（计划内的接触），
      默认从 offenders 移到 exempted；但穿透深度 > depth_eps 时**仍然要报**（计划内接触 ≠ 可以随便穿）。
      depth_eps 默认 0.2 mm 的理由：fit 四档里最大的是 clearance 0.25（让料，本就不该穿透），
      真正会过盈的 press 只有 0.05 mm → 0.2 mm 足够放过所有"设计内"的压配；
      它又比 audit.py 的 MICRO_GAP_MM=0.3（贴而未重合的报告线）紧一档 —— 等它涨到可见缺陷量级之前就报；
      同时远高于测量噪声（三角化弦差 ~0.005–0.02 mm），不会因网格噪声误报。

    严重度：score = 体积占比×1000 + 深度mm×10（体积为主序、深度为次序），档位见 _severity()。
    groups：按"同一组件 > 同一父件 > 自身"归一出的组键配对聚类（同组的多对合并成一条，看最坏的那对）。

    只读：不改场景、不改契约状态。空产出（scope 里没有可见网格）→ ok:false。
    """
    import numpy as np
    t0 = time.perf_counter()
    objs = _scope_objects(scope, include_hidden)
    if not objs:
        return _j({"ok": False, "error": "scope 里没有可见网格对象（空产出 → 不静默成功）",
                   "scope": scope, "include_hidden": bool(include_hidden)})
    m_per_unit, mm_per_unit = _units()
    ub = _units_block(m_per_unit, mm_per_unit)
    eps_mm = float(depth_eps) if depth_eps is not None else MATE_DEPTH_EPS_MM
    side, dpairs, comp_of = _declared_index()
    cand = _aabb_pairs(objs)
    tested = cand[:max(0, int(limit))]
    cache = {}

    def MD(ob):
        if ob.name not in cache:
            cache[ob.name] = _mesh_data([ob])
        return cache[ob.name]

    def gkey(name):
        if name in comp_of:
            return "comp:" + comp_of[name][0]
        ob = bpy.data.objects.get(name)
        if ob is not None and ob.parent is not None:
            return "parent:" + ob.parent.name
        return "self:" + name

    offenders, exempted, skipped = [], [], []
    checked = overlapping = 0
    t1 = time.perf_counter()
    for (i, j) in tested:
        oa, ob_ = objs[i], objs[j]
        ma, mb = MD(oa), MD(ob_)
        if not ma or not mb or ma["tris_n"] == 0 or mb["tris_n"] == 0:
            skipped.append({"a": oa.name, "b": ob_.name, "why": "一端没有三角面"})
            continue
        if ma["tris_n"] > MATE_MAX_TRIS or mb["tris_n"] > MATE_MAX_TRIS:
            skipped.append({"a": oa.name, "b": ob_.name, "why": "网格过大（> %d 面）不判" % MATE_MAX_TRIS})
            continue
        checked += 1
        tri_hit = None
        if use_bvh:
            ta, tb = _bvh(ma["tris"]), _bvh(mb["tris"])
            try:
                tri_hit = bool(ta.overlap(tb)) if (ta is not None and tb is not None) else None
            except Exception as e:
                tri_hit = None
                skipped.append({"a": oa.name, "b": ob_.name, "why": "BVH overlap 失败: %s（按 AABB 候选继续）" % e})
            if tri_hit is False:
                continue
        overlapping += 1
        # --- 穿透深度 ---
        n = int(max(16, min(int(samples), CONTACT_SAMPLE_CAP)))
        PA = _sample_surface(ma["tris"], n)
        PB = _sample_surface(mb["tris"], n)
        if PA is None or PB is None:
            skipped.append({"a": oa.name, "b": ob_.name, "why": "零面积网格，采样失败"})
            continue
        closed = bool(ma["closed"] and mb["closed"])
        if ma["tris_n"] <= INSIDE_MAX_TRIS and mb["tris_n"] <= INSIDE_MAX_TRIS:
            inB = _inside_mask(PA, mb["tris"])
            inA = _inside_mask(PB, ma["tris"])
            if use_bvh:
                _q, _nn, DA = _nearest(_bvh(mb["tris"]), PA)
                _q2, _nn2, DB = _nearest(_bvh(ma["tris"]), PB)
                how = "BVH 最近点距离 + 射线奇偶内外"
            else:
                DA = _pt_tri_dist(PA, mb["tris"])
                DB = _pt_tri_dist(PB, ma["tris"])
                how = "纯 numpy 点-三角面最近距离 + 射线奇偶内外（use_bvh=false）"
            dep = np.concatenate([DA[inB & np.isfinite(DA)], DB[inA & np.isfinite(DB)]])
            depth_max = float(dep.max()) if len(dep) else 0.0
            depth_mean = float(dep.mean()) if len(dep) else 0.0
            dir_used = "A→B" if (inB & np.isfinite(DA)).any() else ("B→A" if (inA & np.isfinite(DB)).any() else "无埋入点")
            inside_frac = float(len(dep)) / float(len(PA) + len(PB))
            depth_ok = True
        else:
            depth_max = depth_mean = None
            inside_frac = None
            dir_used = "skipped"
            how = "网格超内测试上限 %d → 未判穿透" % INSIDE_MAX_TRIS
            depth_ok = False
        ovr = _overlap_estimate(ma["tris"], mb["tris"], ma["volume_m3"], mb["volume_m3"],
                                ma["closed"], mb["closed"], samples=int(max(32, min(int(samples), 4000))))
        frac = ovr.get("frac_of_smaller") if ovr.get("reliable") else None
        frac_proxy = frac if frac is not None else inside_frac
        depth_mm = (depth_max * mm_per_unit) if depth_max is not None else None
        sev = _severity(depth_mm, frac if frac is not None else inside_frac)
        score = _r4((frac_proxy or 0.0) * 1000.0 + (depth_mm or 0.0) * 10.0)
        dby = dpairs.get((oa.name, ob_.name), []) + dpairs.get((ob_.name, oa.name), [])
        dby = sorted(set(dby))
        declared = bool(dby)
        rec = {"a": oa.name, "b": ob_.name, "declared": declared, "declared_by": dby,
               "declared_sides": [side.get(oa.name, []), side.get(ob_.name, [])] if declared else None,
               "depth_mm": _r4(depth_mm) if depth_mm is not None else None,
               "depth_mean_mm": _r4(depth_mean * mm_per_unit) if depth_mean is not None else None,
               "depth_measured": bool(depth_ok), "depth_direction": dir_used,
               "overlap_volume_mm3": ovr.get("overlap_volume_mm3"),
               "overlap_frac_of_smaller": frac,
               "overlap_frac_of_box": ovr.get("frac_of_box"),
               "overlap_stderr": ovr.get("stderr_frac"),
               "surface_inside_frac": _r6(inside_frac) if inside_frac is not None else None,
               "volume_reliable": bool(ovr.get("reliable")),
               "severity": sev, "severity_score": score,
               "aabb_tri_overlap": tri_hit, "method": how,
               "a_tris": ma["tris_n"], "b_tris": mb["tris_n"], "closed": closed,
               "volume_mm3": {"a": _r4(ma["volume_m3"] * (mm_per_unit ** 3)),
                              "b": _r4(mb["volume_m3"] * (mm_per_unit ** 3))}}
        if declared and declared_ok and depth_mm is not None and depth_mm <= eps_mm:
            rec["exempt_reason"] = ("设计内的接触：%s 是连接 %s 的两端（declared），"
                                    "实测穿透 %.3f mm ≤ 门限 %.3f mm" % (oa.name, "/".join(dby), depth_mm, eps_mm))
            exempted.append(rec)
        else:
            if declared and declared_ok and depth_mm is not None:
                rec["exempt_reason"] = None
                rec["note"] = ("declared 但穿透 %.3f mm > 门限 %.3f mm → 仍要报（计划内接触不等于可以随便穿）"
                               % (depth_mm, eps_mm))
            offenders.append(rec)
    offenders.sort(key=lambda r: (-(r.get("severity_score") or 0.0), str(r["a"]), str(r["b"])))
    for k, r in enumerate(offenders):
        r["rank"] = k + 1
    groups = {}
    for r in offenders:
        sig = tuple(sorted([gkey(r["a"]), gkey(r["b"])]))
        g = groups.setdefault(sig, {"group_key": list(sig), "members": [], "worst_severity": "low",
                                    "worst_score": 0.0, "max_depth_mm": 0.0, "objects": set(),
                                    "declared_all": True})
        g["members"].append({"a": r["a"], "b": r["b"], "rank": r["rank"], "severity": r["severity"],
                             "depth_mm": r["depth_mm"], "declared": r["declared"]})
        g["objects"].update([r["a"], r["b"]])
        g["declared_all"] = g["declared_all"] and r["declared"]
        order = {"low": 0, "medium": 1, "high": 2}
        if order[r["severity"]] > order[g["worst_severity"]]:
            g["worst_severity"] = r["severity"]
        g["worst_score"] = max(g["worst_score"], r.get("severity_score") or 0.0)
        g["max_depth_mm"] = max(g["max_depth_mm"], r.get("depth_mm") or 0.0)
    gout = []
    for sig, g in groups.items():
        gout.append({"group_key": g["group_key"], "objects": sorted(g["objects"]),
                     "pairs": len(g["members"]), "worst_severity": g["worst_severity"],
                     "worst_score": _r4(g["worst_score"]), "max_depth_mm": _r4(g["max_depth_mm"]),
                     "declared_all": g["declared_all"],
                     "why": "同组：%s（按 同一组件 > 同一父件 > 自身 归一）" % " / ".join(g["group_key"]),
                     "members": g["members"]})
    gout.sort(key=lambda g: -g["worst_score"])
    ms_total = (time.perf_counter() - t0) * 1000
    res = {"ok": True, "objects": len(objs), "pairs_aabb": len(cand), "pairs_tested": len(tested),
           "pairs_checked": checked, "pairs_overlapping": overlapping,
           "units": ub, "depth_eps_mm": _r4(eps_mm), "declared_ok": bool(declared_ok),
           "use_bvh": bool(use_bvh), "samples": int(samples),
           "count": len(offenders), "offenders": offenders, "groups": gout,
           "exempted_count": len(exempted), "exempted": exempted, "skipped": skipped,
           "severity_rules": {"depth_high_mm": SEV_DEPTH_HIGH_MM, "depth_medium_mm": SEV_DEPTH_MED_MM,
                              "frac_high": SEV_FRAC_HIGH, "frac_medium": SEV_FRAC_MED,
                              "score": "体积占比×1000 + 深度mm×10（体积为主序）"},
           "ms": {"total": round(ms_total, 1), "measure": round((time.perf_counter() - t1) * 1000, 1)},
           "note": "老 op check_interference 未改动（返回结构不变）；本 op 只多给信息，不替代它",
           "readonly": True}
    if checked == 0 and cand:
        res["warning"] = "所有 AABB 候选都被跳过（网格过大/无三角面）→ 本次没有任何可用结论，不要读成'无干涉'"
    elif not cand:
        res["note_clean"] = "AABB 粗筛就没有候选对 → 无干涉（这是结论，不是空产出）"
    _log("interference_report", {"objects": len(objs), "offenders": len(offenders),
                                 "exempted": len(exempted), "eps_mm": _r4(eps_mm)})
    return _j(res)


def c_help():
    return _j({"version": CONTRACT_VERSION,
               "S1_ops": {"register_component": "id, objects, tags, note",
                          "register_connection": "id, a, b, candidates, params, forbidden, confidence, evidence_required, status",
                          "register_envelope": "id, min, max, scope, note",
                          "check_envelope": "id(可选) → 越界清单",
                          "check_interference": "scope, use_bvh, limit → AABB 粗筛 + BVH 精查",
                          "check_interface": "conn_id, mesh(true), samples(1500) → 两组件间隙/侵入：bbox 级老字段（gap_axis_m/gap_m/overlap_bbox）口径不变 + mesh 块（网格级单边间隙 mm/接触占比/穿透 mm）",
                          "destructive_guard": "op, targets, conn_ids → 允许/拦下 + 理由",
                          "evidence": "label, view → 出图 + md5 记账",
                          "ledger/status/report": "证据账本 / 概览 / Markdown 报告"},
               "S2_ops": {"verify": "conn_id, err, tolerance, identifiable, probe_err, probe_tolerance → 三态",
                          "flip": "conn_id, to, why → 翻转候选并记历史",
                          "advance": "conn_id, status(proposed|testing|supported|refuted), why"},
               "V090_ops": {"fingerprint": "objects|scope → 几何指纹（对象/面数/点数/世界 bbox + 12 位 digest）；改前改后各取一次即知源动没动",
                            "ledger": "with_stale=true（默认）→ 逐条证据与当前指纹对拍，源变过的标 stale=true + stale_labels",
                            "drift_guard": "verify 会记 bbox 快照：中心漂移 >10% 对角线 或 尺寸漂移 >20% → supported 在 status/ledger 读取时自动降级 unresolved（verdict.demoted 留理由；带动画数据的对象跳过）"},
               "S3_ops": {"mate_check": "conn_id, fit(clearance|location|press|snap), nominal(期望单边间隙 mm), tol_mm(默认 0.15), samples(≤4000), contact_max_mm, depth_eps_mm"
                                        " → 网格级配合门：接触面积占比/接触锚点/接触法向/单边间隙(mm)/穿透深度(mm) → supported|refuted|unresolved",
                          "fit_help": "fit|feature|m|d → 装配特征库速查（9 特征 + asm_fit 四档 + asm_lead + ISO 273 间隙孔 + Blender 侧造法）",
                          "interference_report": "scope, use_bvh(true), limit(60), declared_ok(true), depth_eps(默认 0.2 mm), include_hidden"
                                                 " → 逐对：穿透深度 mm + 重叠体积占比 + 严重度排序 + groups 聚类 + exempted（设计意图白名单）"},
               "S3_rules": "配合门三态：采样 < 8 点 / 组件空 / 没给 fit·nominal / 没做成侵入判定 → unresolved；接触点 0 个（没搭上）或穿透超门限或间隙出带 → refuted；"
                           "间隙落在 nominal ± tol 且无过深穿透 → supported。接触距离 = 1.5% × 合并 bbox 对角线。共享标称纪律：公母从同一个 nominal ± asm_fit 派生，禁止两处独立字面量。",
               "rules": "外部证据不足（参数不可辨识）必须报 unresolved；探针误差 > 容差则 refuted；破坏性操作在未判别前拦住；"
                        "几何在判定之后漂移 → 判定自动失效（V0.9）；装配级量测一律走求值网格（Boolean 未 Apply 也算数）、数值必带单位、空产出必须 ok:false",
               "docs": "docs/假设驱动建模-cookbook.md · runtime/assembly_features.json（特征库数据）"})


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
            "verify": c_verify, "flip": c_flip, "advance": c_advance,
            "fingerprint": c_fingerprint,
            "mate_check": c_mate_check, "fit_help": c_fit_help,
            "interference_report": c_interference_report}


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
        "fingerprint": c_fingerprint,
        "mate_check": c_mate_check, "fit_help": c_fit_help,
        "interference_report": c_interference_report,
    }
