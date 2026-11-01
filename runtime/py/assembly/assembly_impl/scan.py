# -*- coding: utf-8 -*-
"""Split from assembly.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .manifest import _allow_match, _effective_spec, _load_manifest, _manifest_counts, _manifest_md5, _manifest_validate, _obj_part_map

def _manifest_float(manifest, md, names, mmu, contact_eps_mm):
    """manifest 连接图可达性 + 逐接口接触验证（装配口径，替代 1%/0.3mm 单件口径）。"""
    floaters, unresolved, rows = [], [], []
    if not isinstance(manifest, dict):
        return {"floaters": floaters, "unresolved": unresolved, "interfaces": rows, "graph": None}
    obj_part = _obj_part_map(manifest, names)
    parts = [str(p.get("id")) for p in (manifest.get("parts") or [])
             if isinstance(p, dict) and p.get("id")]
    part_names = {pid: sorted([n for n in names if obj_part.get(n) == pid]) for pid in parts}
    present = [pid for pid in parts if part_names.get(pid)]
    for pid in parts:
        if not part_names.get(pid):
            unresolved.append({"reason": "manifest_part_not_in_scene", "part": pid,
                               "why": "manifest 的 part %s 在扫描范围里没有对应对象（合并漏件？）" % pid})
    interfaces = [it for it in (manifest.get("interfaces") or []) if isinstance(it, dict)]
    deg = {pid: 0 for pid in parts}
    adj = {pid: set() for pid in parts}
    for it in interfaces:
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
    root = str(manifest.get("root_part") or "")
    unreachable = []
    if len(parts) > 1:
        if root and root in adj:
            seen, stack = {root}, [root]
            while stack:
                cur = stack.pop()
                for nxt in adj[cur]:
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            unreachable = [pid for pid in present if pid not in seen]
        else:
            unreachable = [pid for pid in present if deg.get(pid, 0) == 0]
    for pid in unreachable:
        floaters.append({"object": (part_names.get(pid) or [pid])[0], "part": pid,
                         "objects": part_names.get(pid) or [pid],
                         "reason": "unconnected_in_manifest", "mesh_gap_mm": None,
                         "why": "part %s 在 manifest 连接图里没有任何声明接口（也没有 root_part 可达路径）" % pid})
    for it in interfaces:
        iid = str(it.get("id") or "")
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        oa = [n for n in names if obj_part.get(n) == a]
        ob = [n for n in names if obj_part.get(n) == b]
        if not oa or not ob:
            unresolved.append({"reason": "interface_part_missing", "interface": iid,
                               "why": "interface %s 的端在扫描范围里缺对象（a=%s:%d b=%s:%d）"
                                      % (iid, a, len(oa), b, len(ob))})
            rows.append({"id": iid, "a": a, "b": b, "state": "unresolved", "gap_mm": None})
            continue
        best = None
        for na in oa:
            for nb in ob:
                if na in md and nb in md:
                    g = _surface_gap(md[na], md[nb], samples=400)
                    if g is not None:
                        best = g if best is None else min(best, g)
        if best is None:
            unresolved.append({"reason": "interface_measure_failed", "interface": iid,
                               "why": "interface %s 两端量不到表面距离" % iid})
            rows.append({"id": iid, "a": a, "b": b, "state": "unresolved", "gap_mm": None})
            continue
        gap_mm = best * mmu
        tol = _num(it.get("tol_mm"))
        nom = _num(it.get("nominal_mm"))
        allow_ov = _num(it.get("allow_overlap_mm"))
        limit = max(float(tol if tol is not None else (contact_eps_mm or 0.3)),
                    float(contact_eps_mm or 0.3))
        if allow_ov is not None and allow_ov > 0:
            limit = max(limit, float(allow_ov))
        if nom is not None:
            limit = max(limit, abs(nom) + float(tol if tol is not None else 0.0))
        state = "contact" if gap_mm <= limit + 1e-9 else "float"
        rows.append({"id": iid, "a": a, "b": b, "state": state, "gap_mm": _r(gap_mm, 4),
                     "limit_mm": _r(limit, 4)})
        if state == "float":
            floaters.append({"object": (oa or [a])[0], "part": a, "other_part": b,
                             "objects": sorted(oa + ob), "reason": "interface_not_contact",
                             "interface": iid, "mesh_gap_mm": _r(gap_mm, 4),
                             "why": "接口 %s 两端网格最近距离 %.3f mm > 容许 %.3f mm —— 没搭上"
                                    % (iid, gap_mm, limit)})
    graph = {"parts": len(parts), "present_parts": len(present), "edges": len(interfaces),
             "isolated": [pid for pid in parts if deg.get(pid, 0) == 0],
             "root_part": root or None, "unreachable": unreachable}
    return {"floaters": floaters, "unresolved": unresolved, "interfaces": rows, "graph": graph}


def _connectivity_float(names, contact_eps_mm, include_hidden=False):
    """没有 manifest 时退 audit_connectivity（单件/装配口径混用风险写进 note）。"""
    rec, err = _call_api("audit", "connectivity",
                         {"objects": list(names), "include_hidden": bool(include_hidden),
                          "micro_gap_mm": float(contact_eps_mm or 0.3)})
    out = {"floaters": [], "unresolved": [], "state": "degraded", "receipt": None,
           "note": "audit_connectivity 的 1% 可见浮块 + micro_gap 是**单件**口径；装配请给 manifest 用连接图"}
    if err:
        out["unresolved"].append({"reason": "connectivity_unavailable", "why": err})
        return out
    out["receipt"] = {k: rec.get(k) for k in ("analyzed", "objects", "tris", "real_floater_count",
                                             "visible_floater_count", "unconfirmed_floater_count",
                                             "micro_floater_count", "floater_count", "micro_confirm")}
    if not rec.get("analyzed"):
        out["unresolved"].append({"reason": "connectivity_not_analyzed",
                                  "why": str(rec.get("reason") or "未分析")[:240]})
        return out
    gate = rec.get("gate") or {}
    for f in rec.get("floaters") or []:
        if not isinstance(f, dict):
            continue
        attr = f.get("attribution") or {}
        raw = attr.get("contains")
        if raw is None:
            raw = attr.get("object")
        if isinstance(raw, dict):
            objs = list(raw.keys())
        elif isinstance(raw, (list, tuple, set)):
            objs = list(raw)
        elif raw:
            objs = [raw]
        else:
            objs = []
        out["floaters"].append({"object": (objs or [None])[0], "objects": sorted(str(x) for x in objs if x),
                                "reason": "mesh_float", "visible": bool(f.get("visible")),
                                "span_fraction": f.get("span_fraction"), "gap_mm": f.get("gap_mm"),
                                "bbox_micro": f.get("bbox_micro"), "mesh_confirmed": f.get("mesh_confirmed"),
                                "why": "连通分量与任何其它分量都不相接（audit_connectivity）"})
    unconf = int(gate.get("unconfirmed_floater_count") or rec.get("unconfirmed_floater_count") or 0)
    if unconf:
        out["unresolved"].append({"reason": "connectivity_unconfirmed",
                                  "why": "%d 个可见分量无法确认是否相接（%s）"
                                         % (unconf, rec.get("micro_confirm") or "未说明")})
    real = int(gate.get("real_floater_count") or rec.get("real_floater_count") or 0)
    if real and not out["floaters"]:
        out["unresolved"].append({"reason": "floater_unattributed",
                                  "why": "connectivity 报 %d 个浮块但回执里没有逐块归因" % real})
    out["state"] = "fail" if (real or out["floaters"]) else ("degraded" if out["unresolved"] else "pass")
    return out
# ────────────────────────────── 分类核心 ──────────────────────────────
def _pair_key(a, b):
    x, y = str(a), str(b)
    return (x, y) if x <= y else (y, x)


def _severity(depth_mm, volume_mm3, contained=False):
    if contained:
        return "high" if (volume_mm3 or 0.0) > 1000.0 else "medium"
    d = _num(depth_mm)
    v = _num(volume_mm3)
    if d is None and v is None:
        return "unknown"
    if (d is not None and d > 2.0) or (v is not None and v > 500.0):
        return "high"
    if (d is not None and d > 0.5) or (v is not None and v > 10.0):
        return "medium"
    return "low"


def _cp_allow_ok(depth, vol, max_depth, max_vol, contained_gap=None):
    """spec.allow 覆盖判定（上限按 1.001 容差）。"""
    ok_d = (max_depth is None) or (depth is not None and depth <= max_depth * 1.001 + 1e-9)
    ok_v = (max_vol is None) or (vol is not None and vol <= max_vol * 1.001 + 1e-9)
    if contained_gap is not None and max_depth is not None and max_vol is None:
        return contained_gap <= max_depth * 1.001 + 1e-9
    return bool(ok_d and ok_v)


def _cp_tri_hit(rec, ma, mb, a, b, tri, deep_map, invalid, allow_rec, max_depth, max_vol,
                depth_eps_mm, floor_mm3):
    """三角面确实相交（tri>0）分支：深度/体积测量 + allow/阈值分类。"""
    rec["method"] = "BVH tri-tri"
    if invalid:
        rec["class"] = "unresolved"
        rec["reason"] = ("两件表面确实相交（%d 对三角面），但网格开放/法线朝内 ⇒ 内外与深度不可信，不猜" % tri)
        return rec
    drec = (deep_map or {}).get(_pair_key(a, b))
    depth = _num((drec or {}).get("depth_mm"))
    vol = _num((drec or {}).get("volume_mm3"))
    if drec and drec.get("depth_measured") is False:
        depth = None
    # contract 的 INSIDE_MAX_TRIS=0 实际会跳过侵入判定（depth 恒 None）⇒ 深度必须兜底量
    if depth is None:
        own = _depth_measure(ma, mb)
        own_depth = _num(own.get("depth_mm"))
        if own_depth is not None:
            depth = own_depth
        if vol is None:
            vol = _num(own.get("volume_mm3"))
        rec["method"] = ("contract 体积 + assembly 深度兜底" if drec
                         else own.get("method"))
    else:
        rec["method"] = "contract.interference_report（复用）"
    rec["depth_mm"] = _r(depth, 4)
    rec["depth_measured"] = depth is not None
    rec["volume_mm3"] = _r(vol, 4)
    rec["volume_reliable"] = bool((drec or {}).get("volume_reliable", vol is not None))
    if depth is None and vol is None:
        rec["class"] = "unresolved"
        rec["reason"] = "三角面相交但深度/体积都量不出来（采样失败）"
        return rec
    if allow_rec:
        if _cp_allow_ok(depth, vol, max_depth, max_vol):
            rec["class"] = "hidden_ok"
            rec["reason"] = ("spec.allow 覆盖：深度 %.3f mm / 体积 %.1f mm³ 在允许范围内"
                             % (depth or 0.0, vol or 0.0))
        else:
            rec["class"] = "clash"
            rec["reason"] = ("spec.allow 存在但超限：深度 %s mm（上限 %s）/ 体积 %s mm³（上限 %s）"
                             % (_r(depth, 3), max_depth, _r(vol, 1), max_vol))
    elif depth is not None and depth > float(depth_eps_mm) + 1e-9:
        rec["class"] = "clash"
        rec["reason"] = "穿透深度 %.3f mm > depth_eps %.3f mm" % (depth, float(depth_eps_mm))
    elif vol is not None and vol > float(floor_mm3) + 1e-9:
        rec["class"] = "clash"
        rec["reason"] = "相交体积 %.3f mm³ > floor %.3f mm³" % (vol, float(floor_mm3))
    else:
        rec["class"] = "contact"
        rec["reason"] = ("面接触（深度 %.3f mm ≤ %.3f / 体积 %.3f mm³ ≤ 下限）"
                         % (depth or 0.0, float(depth_eps_mm), vol or 0.0))
    rec["severity"] = _severity(rec["depth_mm"], rec["volume_mm3"])
    return rec


def _cp_contained(rec, ma, mb, a, b, contain_ij, allow_rec, max_depth, max_vol, mmu):
    """AABB 包含分支（三角面不相交）：射线奇偶确认 + 包络间隙；非包含返回 None。"""
    if contain_ij:
        small, big, sm, bg = ma, mb, a, b
    else:
        small, big, sm, bg = mb, ma, b, a
    pts = _sample_surface(small["tris"], 400)
    n_in, tested = _count_inside(pts, big["bvh"])
    frac = (n_in / float(tested)) if tested else 0.0
    if frac < 0.5:
        return None
    vol_est = abs(float(small["volume_signed_m3"])) * (mmu ** 3)
    gap_min = None
    for p in pts:
        r = big["bvh"].find_nearest(Vector(p))
        if r is None or r[0] is None:
            continue
        d = float(r[3])
        gap_min = d if gap_min is None else min(gap_min, d)
    gap_min_mm = (gap_min * mmu) if gap_min is not None else None
    rec["method"] = "AABB 包含 + 射线奇偶内外（包含探针）"
    rec["depth_mm"] = _r(gap_min_mm, 4)
    rec["depth_measured"] = gap_min_mm is not None
    rec["volume_mm3"] = _r(vol_est, 4)
    rec["volume_reliable"] = False
    rec["min_gap_mm"] = _r(gap_min_mm, 4)
    rec["contained_in"] = bg
    rec["contained_part"] = sm
    if allow_rec and _cp_allow_ok(None, vol_est, max_depth, max_vol, contained_gap=gap_min_mm):
        rec["class"] = "hidden_ok"
        rec["reason"] = ("完全包含但 spec.allow 覆盖（小件体积 %.1f mm³ / 包络间隙 %s mm）"
                         % (vol_est, _r(gap_min_mm, 3)))
        rec["severity"] = "low"
    else:
        rec["class"] = "contained"
        rec["reason"] = ("完全包含：%s 的整体被 %s 包住（表面点 %.0f%% 在内部，小件体积 %.1f mm³，"
                         "包络最小间隙 %s mm）—— 面无相交所以旧全对全会漏掉"
                         % (sm, bg, frac * 100.0, vol_est, _r(gap_min_mm, 3)))
        rec["severity"] = _severity(None, vol_est, contained=True)
    return rec


def _cp_candidate_gap(rec, ma, mb, contact_eps_mm, mmu):
    """候选对但三角面不相交、非包含：量表面间隙（接触/间隙/不可测）。"""
    # ---- 其余：量间隙（接触面积不在全对全里算，成本控制） ----
    gap_m = _surface_gap(ma, mb, samples=500)
    gap_mm = (gap_m * mmu) if gap_m is not None else None
    rec["min_gap_mm"] = _r(gap_mm, 4)
    rec["method"] = "表面采样最近距离"
    if gap_mm is None:
        rec["class"] = "unresolved"
        rec["reason"] = "AABB 重叠但表面距离量不出来（网格退化？）"
    elif gap_mm <= float(contact_eps_mm) + 1e-9:
        rec["class"] = "contact"
        rec["reason"] = "表面间隙 %.3f mm ≤ contact_eps %.3f mm" % (gap_mm, float(contact_eps_mm))
    else:
        rec["class"] = "gap"
        rec["reason"] = "AABB 重叠但表面间隙 %.3f mm（分离/互锁）" % gap_mm
    return rec


def _cp_near_gap(rec, ma, mb, aabb_gap_m, contact_eps_mm, report_gap_mm, mmu):
    """AABB 不相交的近对：先用 AABB 距离下界，再表面采样。"""
    rec["method"] = "AABB 距离下界"
    if aabb_gap_m is not None and (aabb_gap_m * mmu) > float(contact_eps_mm) + 1e-9:
        rec["class"] = "gap"
        rec["min_gap_mm"] = _r(aabb_gap_m * mmu, 4)
        rec["reason"] = "AABB 距离下界 %.3f mm > contact_eps（真实间隙只会更大）" % (aabb_gap_m * mmu)
        return rec
    gap_m = _surface_gap(ma, mb, samples=300)
    gap_mm = (gap_m * mmu) if gap_m is not None else None
    rec["min_gap_mm"] = _r(gap_mm, 4)
    rec["method"] = "AABB 近对 + 表面采样最近距离"
    if gap_mm is None:
        rec["class"] = "unresolved"
        rec["reason"] = "近对但表面距离量不出来"
    elif gap_mm <= float(contact_eps_mm) + 1e-9:
        rec["class"] = "contact"
        rec["reason"] = "表面间隙 %.3f mm ≤ contact_eps %.3f mm" % (gap_mm, float(contact_eps_mm))
    elif gap_mm <= float(report_gap_mm) + 1e-9:
        rec["class"] = "gap"
        rec["reason"] = "表面间隙 %.3f mm（在 report_gap %.3f mm 内）" % (gap_mm, float(report_gap_mm))
    else:
        rec["class"] = "gap"
        rec["reason"] = "表面间隙 %.3f mm（超出 report_gap，按 gap 统计不展开）" % gap_mm
    return rec


def _classify_pair(i, j, names, md, contain_ij, contain_ji, aabb_gap_m, is_candidate,
                   deep_map, allow_list, obj_part, depth_eps_mm, contact_eps_mm,
                   report_gap_mm, floor_mm3, mmu):
    """单对分类（返回 rec，不抛）。所有数字 mm；深度/体积不可测时绝不臆造。"""
    a, b = names[i], names[j]
    ma, mb = md[a], md[b]
    rec = {"a": a, "b": b, "class": None, "declared": False, "spec_ref": None,
           "tri_overlap_pairs": 0, "depth_mm": None, "depth_measured": False,
           "volume_mm3": None, "volume_reliable": False, "min_gap_mm": None,
           "closed": {"a": bool(ma["closed"]), "b": bool(mb["closed"])},
           "normals_ok": {"a": bool(ma["normals_ok"]), "b": bool(mb["normals_ok"])},
           "severity": "unknown", "method": None, "reason": ""}
    invalid = not (ma["closed"] and ma["normals_ok"] and mb["closed"] and mb["normals_ok"])
    allow_rec = _allow_match(allow_list, obj_part, a, b)
    max_depth = _num((allow_rec or {}).get("max_depth_mm"))
    max_vol = _num((allow_rec or {}).get("max_volume_mm3"))
    if allow_rec:
        rec["declared"] = True
        rec["spec_ref"] = str(allow_rec.get("id") or allow_rec.get("reason") or "spec.allow")

    if is_candidate:
        tri = 0
        try:
            tri = len(ma["bvh"].overlap(mb["bvh"]) or [])
        except Exception as e:
            rec["reason"] = "BVH.overlap 失败: %s" % str(e)[:120]
            rec["class"] = "unresolved"
            return rec
        rec["tri_overlap_pairs"] = int(tri)
        if tri > 0:
            return _cp_tri_hit(rec, ma, mb, a, b, tri, deep_map, invalid, allow_rec,
                               max_depth, max_vol, depth_eps_mm, floor_mm3)
        # ---- 三角面不相交：先补"完全包含"分支（旧实现会在这里 continue，漏判） ----
        if (contain_ij or contain_ji) and not invalid:
            got = _cp_contained(rec, ma, mb, a, b, contain_ij, allow_rec, max_depth, max_vol, mmu)
            if got is not None:
                return got
        return _cp_candidate_gap(rec, ma, mb, contact_eps_mm, mmu)
    return _cp_near_gap(rec, ma, mb, aabb_gap_m, contact_eps_mm, report_gap_mm, mmu)
# ────────────────────────────── contract 深测复用 ──────────────────────────────
def _contract_deep(names, include_hidden, depth_eps_mm, max_pairs):
    """复用 K.dsh_contract_api["interference_report"] 的逐对深度/体积（拿不到返回 None + 原因）。"""
    api = _api("contract")
    if api is None:
        return None, "K.dsh_contract_api 不存在（preload 里带 contract）"
    scope = sorted(set(str(n) for n in names))
    if not scope:
        return None, "scope 为空"
    res, err = _call_api("contract", "interference_report",
                         {"scope": scope, "use_bvh": True, "limit": int(max(1, int(max_pairs))),
                          "declared_ok": True, "depth_eps": float(depth_eps_mm),
                          "include_hidden": bool(include_hidden)})
    if err:
        return None, err
    if not res.get("ok"):
        return None, "interference_report ok=false: %s" % str(res.get("error") or res.get("note"))[:200]
    deep = {}
    for grp in ("offenders", "exempted"):
        for rec in res.get(grp) or []:
            if not isinstance(rec, dict):
                continue
            key = _pair_key(rec.get("a"), rec.get("b"))
            deep[key] = {"depth_mm": rec.get("depth_mm"), "depth_mean_mm": rec.get("depth_mean_mm"),
                         "depth_measured": bool(rec.get("depth_measured", rec.get("depth_mm") is not None)),
                         "volume_mm3": rec.get("overlap_volume_mm3"),
                         "volume_reliable": bool(rec.get("volume_reliable", False)),
                         "surface_inside_frac": rec.get("surface_inside_frac"),
                         "closed": rec.get("closed"), "declared": rec.get("declared"),
                         "severity": rec.get("severity"), "method": rec.get("method")}
    meta = {k: res.get(k) for k in ("objects", "pairs_aabb", "pairs_tested", "pairs_checked",
                                   "pairs_overlapping", "exempted_count") if k in res}
    meta["used"] = True
    return {"pairs": deep, "meta": meta}, None


# ────────────────────────────── scan 核心 ──────────────────────────────
def _scan_core_p1(st):
    depth_eps_mm, include_hidden, limit, manifest, max_pairs = (
        st["depth_eps_mm"], st["include_hidden"], st["limit"], st["manifest"], st["max_pairs"])
    md, names, objs, report_gap_mm, spec = (
        st.get("md"), st.get("names"), st["objs"], st["report_gap_mm"], st["spec"])
    use_contract = st["use_contract"]
    deep, derr, i, j = (st.get("deep"), st.get("derr"), st.get("i"), st.get("j"))
    try:
        t0 = time.perf_counter()
        mmu = float(_KIT.units())
        scale = max(mmu, 1e-9)
        names = [str(n) for n in names]
        md_errors = {}
        if md is None:
            md, md_errors = _mesh_map(objs)
        names = [n for n in names if n in md]
        n = len(names)
        if n == 0:
            return {"ok": False, "error": "没有可扫描的求值网格（0 个对象有三角面）—— 空产出不静默成功",
                    "md_errors": md_errors}
        esp = _effective_spec(manifest, spec)
        allow_list = esp["allow"]
        obj_part = _obj_part_map(manifest, names)
        mn = np.array([md[nm]["aabb"][0] for nm in names], dtype=np.float64)
        mx = np.array([md[nm]["aabb"][1] for nm in names], dtype=np.float64)
        eps = 1e-9 * max(1.0, float(np.abs(np.concatenate([mn, mx])).max()))
        lo = np.maximum(mn[:, None, :], mn[None, :, :])
        hi = np.minimum(mx[:, None, :], mx[None, :, :])
        inter = hi - lo
        # 相贴（inter==0，如零厚度面片切件）也必须进候选：用 -eps 而不是 >0，否则 plane 切立方体会被当成"近对"
        aabb_overlap = (inter >= -eps).all(axis=2)
        np.fill_diagonal(aabb_overlap, False)
        gap_vec = np.maximum(0.0, np.maximum(mn[:, None, :] - mx[None, :, :], mn[None, :, :] - mx[:, None, :]))
        gap = np.sqrt((gap_vec ** 2).sum(axis=2))
        np.fill_diagonal(gap, np.inf)
        contain = ((mn[:, None, :] >= mn[None, :, :] - eps) & (mx[:, None, :] <= mx[None, :, :] + eps)).all(axis=2)
        np.fill_diagonal(contain, False)
        same_box = (((np.abs(mn[:, None, :] - mn[None, :, :]) <= eps).all(axis=2)) &
                    ((np.abs(mx[:, None, :] - mx[None, :, :]) <= eps).all(axis=2)))
        contain = contain & ~same_box
        inter_vol = np.prod(np.maximum(inter, 0.0), axis=2)

        cands, near = [], []
        for i in range(n):
            for j in range(i + 1, n):
                if aabb_overlap[i, j]:
                    cands.append((i, j))
                elif gap[i, j] <= float(report_gap_mm) / scale:
                    near.append((i, j))

        def _ckey(ij):
            i, j = ij
            cont = 1 if bool(contain[i, j] or contain[j, i]) else 0
            return (-cont, -float(inter_vol[i, j]), float(gap[i, j]), names[i], names[j])

        cands.sort(key=_ckey)
        near.sort(key=lambda ij: (float(gap[ij]), names[ij[0]], names[ij[1]]))
        total_avail = len(cands) + len(near)
        cap = int(max_pairs) if int(max_pairs) > 0 else total_avail
        if int(limit) > 0:
            cap = min(cap, int(limit))
        tested_c = cands[:cap]
        tested_n = near[:max(0, cap - len(tested_c))]
        truncated = (len(tested_c) + len(tested_n)) < total_avail

        deep_map, contract_meta = None, {"used": False, "reason": None}
        if use_contract and cands:
            deep, derr = _contract_deep(names, include_hidden, depth_eps_mm, max_pairs)
            if deep is not None:
                deep_map = deep["pairs"]
                contract_meta = deep["meta"]
            else:
                contract_meta = {"used": False, "reason": str(derr)[:240]}

        pairs, matrix_cells = [], []
        budget_hit = False
        checked = 0
        t_loop = time.perf_counter()
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _scan_core_p2(st):
    allow_list, budget_ms, cands, checked, contact_eps_mm = (
        st.get("allow_list"), st["budget_ms"], st.get("cands"), st.get("checked"), st["contact_eps_mm"])
    contain, deep_map, depth_eps_mm, floor_mm3, gap = (
        st.get("contain"), st.get("deep_map"), st["depth_eps_mm"], st["floor_mm3"], st.get("gap"))
    include_hidden, manifest, matrix_cells, md, md_errors = (
        st["include_hidden"], st["manifest"], st.get("matrix_cells"), st.get("md"), st.get("md_errors"))
    mmu, n, names, near, obj_part = (
        st.get("mmu"), st.get("n"), st.get("names"), st.get("near"), st.get("obj_part"))
    pairs, report_gap_mm, t_loop, tested_c, tested_n = (
        st.get("pairs"), st["report_gap_mm"], st.get("t_loop"), st.get("tested_c"), st.get("tested_n"))
    total_avail = st.get("total_avail")
    budget_hit, cf, i, is_cand, j, key, mf, rec, row, seq, truncated = (st.get("budget_hit"), st.get("cf"), st.get("i"), st.get("is_cand"), st.get("j"), st.get("key"), st.get("mf"), st.get("rec"), st.get("row"), st.get("seq"), st.get("truncated"))
    try:
        for is_cand, seq in ((True, tested_c), (False, tested_n)):
            for (i, j) in seq:
                if int(budget_ms) > 0 and (time.perf_counter() - t_loop) * 1000.0 > float(budget_ms):
                    budget_hit = True
                    break
                rec = _classify_pair(i, j, names, md, bool(contain[i, j]), bool(contain[j, i]),
                                     float(gap[i, j]), is_cand, deep_map, allow_list, obj_part,
                                     depth_eps_mm, contact_eps_mm, report_gap_mm, floor_mm3, mmu)
                if rec.get("class") not in CLASSES:
                    rec["class"] = "unresolved"
                    rec["reason"] = "分类器返回未知 class（按证据不足处理）"
                pairs.append(rec)
                matrix_cells.append([int(i), int(j), rec["class"], rec.get("depth_mm"), rec.get("volume_mm3")])
                checked += 1
            if budget_hit:
                break
        if budget_hit:
            truncated = True

        # ---- 浮空：manifest 连接图优先，否则 audit_connectivity ----
        floaters, float_unresolved, float_source = [], [], "none"
        manifest_float_rows, graph = [], None
        if isinstance(manifest, dict):
            mf = _manifest_float(manifest, md, names, mmu, contact_eps_mm)
            floaters = mf["floaters"]
            float_unresolved = mf["unresolved"]
            manifest_float_rows = mf["interfaces"]
            graph = mf["graph"]
            float_source = "manifest"
        else:
            cf = _connectivity_float(names, contact_eps_mm, include_hidden)
            floaters = cf["floaters"]
            float_unresolved = cf["unresolved"]
            float_source = "audit_connectivity"

        existing = {_pair_key(p["a"], p["b"]): p for p in pairs}
        for row in manifest_float_rows:
            if row.get("state") != "float":
                continue
            key = _pair_key(row.get("a"), row.get("b"))
            if key in existing:
                existing[key]["interface_state"] = "float"
                existing[key].setdefault("reason", "manifest 接口未接触")
                continue
            rec = {"a": str(row.get("a")), "b": str(row.get("b")), "class": "float", "declared": True,
                   "spec_ref": str(row.get("id") or ""), "tri_overlap_pairs": 0,
                   "depth_mm": None, "depth_measured": False, "volume_mm3": None,
                   "volume_reliable": False, "min_gap_mm": row.get("gap_mm"),
                   "closed": {}, "normals_ok": {}, "severity": "unknown",
                   "method": "manifest 接口接触验证",
                   "reason": "接口 %s 两端网格间隙 %s mm > 容许 %s mm —— 没搭上"
                             % (row.get("id"), row.get("gap_mm"), row.get("limit_mm"))}
            pairs.append(rec)
            existing[key] = rec

        counts = {c: 0 for c in CLASSES}
        for rec in pairs:
            if rec.get("class") in counts and rec["class"] != "float":
                counts[rec["class"]] += 1
        counts["float"] = len(floaters)
        far = max(0, n * (n - 1) // 2 - total_avail)
        counts["gap_far"] = far
        counts["candidates"] = len(cands)
        counts["near_pairs"] = len(near)
        counts["tested"] = int(checked)
        counts["pairs_total"] = n * (n - 1) // 2

        sev_rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
        offenders = [p for p in pairs if p.get("class") in ("clash", "contained")]
        offenders.sort(key=lambda p: (-sev_rank.get(p.get("severity") or "unknown", 0),
                                      -(p.get("depth_mm") or 0.0), -(p.get("volume_mm3") or 0.0),
                                      str(p.get("a")), str(p.get("b"))))
        unresolved_pairs = [p for p in pairs if p.get("class") == "unresolved"]
        hidden_ok = [p for p in pairs if p.get("class") == "hidden_ok"]
        contacts = [p for p in pairs if p.get("class") == "contact"]
        invalid_objs = sorted([nm for nm in names if not (md[nm]["closed"] and md[nm]["normals_ok"])])
        unresolved_objects = sorted(set(list(md_errors.keys()) + invalid_objs))
        unres_total = counts["unresolved"] + len(float_unresolved)
        bad = counts["clash"] + counts["contained"] + counts["float"]
        if bad:
            verdict = "fail"
        elif unres_total or truncated or budget_hit or unresolved_objects:
            verdict = "degraded"
        else:
            verdict = "pass"
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _scan_core_p3(st):
    allow_list, budget_hit, budget_ms, cands, checked = (
        st.get("allow_list"), st.get("budget_hit"), st["budget_ms"], st.get("cands"), st.get("checked"))
    contact_eps_mm, contract_meta, counts, depth_eps_mm, esp = (
        st["contact_eps_mm"], st.get("contract_meta"), st.get("counts"), st["depth_eps_mm"], st.get("esp"))
    float_source, float_unresolved, floaters, floor_mm3, graph = (
        st.get("float_source"), st.get("float_unresolved"), st.get("floaters"), st["floor_mm3"], st.get("graph"))
    limit, manifest, matrix_cells, max_pairs, md_errors = (
        st["limit"], st["manifest"], st.get("matrix_cells"), st["max_pairs"], st.get("md_errors"))
    n, names, near, offenders, pairs = (
        st.get("n"), st.get("names"), st.get("near"), st.get("offenders"), st.get("pairs"))
    report_gap_mm, summary_only, total_avail, truncated, unres_total = (
        st["report_gap_mm"], st["summary_only"], st.get("total_avail"), st.get("truncated"), st.get("unres_total"))
    unresolved_objects, verdict = st.get("unresolved_objects"), st.get("verdict")
    f, p, why = (st.get("f"), st.get("p"), st.get("why"))
    try:
        coverage = {"objects": n, "aabb_pairs": len(cands), "near_pairs": len(near),
                    "tested": int(checked), "complete": bool(not truncated and not budget_hit and not unresolved_objects),
                    "truncated": bool(truncated), "budget_hit": bool(budget_hit),
                    "unresolved_objects": unresolved_objects[:60], "md_errors": md_errors}
        depths = [p.get("depth_mm") for p in pairs if p.get("depth_mm") is not None]
        gaps = [p.get("min_gap_mm") for p in pairs if p.get("min_gap_mm") is not None]
        repairs = []
        for p in offenders[:5]:
            why = ("完全包含（面无相交，旧全对全会漏）" if p.get("class") == "contained"
                   else "穿透深度 %s mm / 体积 %s mm³ 超门限" % (p.get("depth_mm"), p.get("volume_mm3")))
            repairs.append({"op": "fix_interference",
                            "args": {"a": p.get("a"), "b": p.get("b"), "plan_only": True}, "why": why})
        for f in floaters[:5]:
            repairs.append({"op": "asm_manifest" if float_source == "manifest" else "audit_snap_floaters",
                            "args": ({"action": "add_interface", "note": "把浮空件接进连接图"} if float_source == "manifest"
                                     else {"objects": f.get("objects") or [f.get("object")], "dry_run": True}),
                            "why": f.get("why") or "装配浮空"})
        if float_source == "manifest" and any(u.get("reason") == "manifest_part_not_in_scene" for u in float_unresolved):
            repairs.append({"op": "asm_merge", "args": {"manifest": "<path>", "dry_run": True},
                            "why": "manifest 里有 part 在当前场景找不到 —— 合并漏件，先看 merge 计划"})
        spec_summary = {"allow_pairs": len(allow_list),
                        "allow": [{"a": a.get("a"), "b": a.get("b"),
                                   "max_depth_mm": _num(a.get("max_depth_mm")),
                                   "max_volume_mm3": _num(a.get("max_volume_mm3")),
                                   "spec_ref": a.get("id") or a.get("reason")}
                                  for a in allow_list[:20]],
                        "interfaces": len(esp["interfaces"]), "clearance": len(esp["clearance"])}
        manifest_summary = None
        if isinstance(manifest, dict):
            manifest_summary = {"assembly": manifest.get("assembly"), "sealed": bool(manifest.get("sealed")),
                                "manifest_md5": _manifest_md5(manifest),
                                "counts": _manifest_counts(manifest)}
        max_depth = max(depths) if depths else None
        min_gap = min(gaps) if gaps else None
        verdict_line = ("扫了 %d 件 / %d 对候选：clash=%d contained=%d hidden_ok=%d contact=%d gap=%d float=%d "
                        "unresolved=%d → %s" % (n, total_avail, counts["clash"], counts["contained"],
                                                counts["hidden_ok"], counts["contact"], counts["gap"],
                                                counts["float"], unres_total, verdict))
        out = {"ok": True, "verdict": verdict, "analyzed": True, "readonly": True,
               "objects": n, "object_names": names, "counts": counts,
               "coverage": coverage, "verdict_line": verdict_line,
               "thresholds": {"depth_eps_mm": _r(depth_eps_mm, 4), "contact_eps_mm": _r(contact_eps_mm, 4),
                              "report_gap_mm": _r(report_gap_mm, 4), "floor_mm3": _r(floor_mm3, 4),
                              "limit": int(limit), "max_pairs": int(max_pairs), "budget_ms": int(budget_ms)},
               "max_depth_mm": _r(max_depth, 4), "min_gap_mm": _r(min_gap, 4),
               "offenders": (offenders[:10] if summary_only else offenders[:60]),
               "floaters": (floaters[:10] if summary_only else floaters[:60]),
               "floating_unresolved": float_unresolved[:20], "float_source": float_source,
               "manifest_float_graph": graph,
               "repairs": repairs[:10], "spec": spec_summary, "manifest": manifest_summary,
               "contract": contract_meta,
               "honest_limits": [
                   "深度/体积是采样式估计（低差异序列，非随机；contract 复用时为对方口径），同输入同输出",
                   "完全包含：内/外靠射线奇偶；大件若是**空心壳**，小件被包住不等于材质干涉（本 op 按实心口径报 contained，需人核）",
                   "开网格 / 法线朝内：三角面相交仍是精确证据，但深度与内外结论不可信 → 该对一律 unresolved",
                   "接触面积不在全对全里算（成本控制）；gap 类是采样最近距离，平行贴合可能高估",
                   "AABB 不相交的对子间隙是**下界**（真实只会更大）；> report_gap 的远对只计数不展开",
                   "多壳且各壳朝向相反的件，按总体有符号体积可能漏判朝内（gate 另跑 audit_mesh 逐壳判）",
               ]}
        if not summary_only:
            out["pairs"] = pairs
            out["matrix"] = {"format": "sparse", "n": n, "index": list(names), "cells": matrix_cells,
                             "note": "只列被评估的关系（AABB 重叠 / report_gap 内近对）；远对按 counts.gap_far 计数"}
        else:
            out["pairs"] = []
            out["matrix"] = None
            out["slim"] = {"summary_only": True}
            out["counts_note"] = "counts.float = 浮空对象/接口数（不是 pair 数）；分类明细用 summary_only=false 看 pairs"
        if verdict == "degraded":
            out["warn"] = "DEGRADED ≠ 通过：有 unresolved / 截断 / 没判完的对象"
            out["hint"] = "先修网格/法线，或把预算调大（max_pairs / budget_ms）后重跑；未判完不许读成没问题"
        return out
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _scan_core(objs, names, manifest=None, spec=None, limit=0, max_pairs=20000,
               depth_eps_mm=0.2, contact_eps_mm=0.3, report_gap_mm=5.0, floor_mm3=1.0,
               summary_only=False, use_contract=True, include_hidden=False, md=None,
               budget_ms=0):
    st = dict(locals())
    for _fn_ in (_scan_core_p1, _scan_core_p2, _scan_core_p3):
        _r = _fn_(st)
        if _r is not None:
            return _r
# ────────────────────────────── ops：scan ──────────────────────────────
def asm_scan(scope=None, objects=None, file=None, manifest=None, spec=None, limit=0,
             max_pairs=20000, depth_eps_mm=0.2, contact_eps_mm=0.3, report_gap_mm=5.0,
             summary_only=False, use_contract=True, floor_mm3=1.0,
             include_hidden=False, budget_ms=0):
    """全对全穿模/包含/接触/间隙/浮空分类扫描（只读）。别名 audit_clash_all。"""
    t0 = time.perf_counter()
    try:
        depth_eps_mm = float(depth_eps_mm)
        contact_eps_mm = float(contact_eps_mm)
        report_gap_mm = float(report_gap_mm)
        floor_mm3 = float(floor_mm3)
    except Exception:
        return _err("阈值参数必须是数字（depth_eps_mm / contact_eps_mm / report_gap_mm / floor_mm3）")
    if depth_eps_mm < 0 or contact_eps_mm < 0 or report_gap_mm < 0 or floor_mm3 < 0:
        return _err("阈值参数不能为负", depth_eps_mm=depth_eps_mm, contact_eps_mm=contact_eps_mm,
                    report_gap_mm=report_gap_mm, floor_mm3=floor_mm3)
    m = None
    if manifest is not None:
        m, merr = _load_manifest(manifest)
        if merr:
            return _err(merr)
        val = _manifest_validate(m, check_files=False, check_md5=False)
        if not val["ok"]:
            return _err("manifest 结构校验失败（坏清单上不给结论）", errors=val["errors"][:10],
                        warnings=val["warnings"][:10], manifest_md5=val["manifest_md5"])
    res, err_res, cleanup, file_info = None, None, None, None
    try:
        if file:
            objs, file_info, cleanup = _load_file_objects(file, include_hidden=include_hidden)
            if file_info.get("error"):
                err_res = _err(file_info["error"], file=file_info)
            elif not objs:
                err_res = _err("file 里没有可用 mesh：%s" % file_info.get("path"))
            else:
                names = [str(o.name) for o in objs]
                res = _scan_core(objs, names, m, spec, limit, max_pairs, depth_eps_mm,
                                 contact_eps_mm, report_gap_mm, floor_mm3, summary_only,
                                 use_contract, include_hidden, None, budget_ms)
                if res.get("ok"):
                    res["source"] = {"kind": "file", "path": file_info.get("path")}
                    res["name_of"] = file_info.get("name_of") or {}
                    res["file_load"] = {k: file_info.get(k) for k in
                                        ("path", "loaded", "mesh", "non_mesh", "hidden_skipped", "missing")}
        else:
            info = _pick_objects(scope, objects, include_hidden)
            if info.get("error"):
                err_res = _err(info["error"], objects=info.get("names"), missing=info.get("missing"),
                               scopes_missing=info.get("scopes_missing"),
                               hint="确认对象名/集合名；空产出必须失败（不静默成功）")
            else:
                res = _scan_core(info["objects"], info["names"], m, spec, limit, max_pairs,
                                 depth_eps_mm, contact_eps_mm, report_gap_mm, floor_mm3,
                                 summary_only, use_contract, include_hidden, None, budget_ms)
                res["source"] = {"kind": "session",
                                 "scope": scope if not isinstance(scope, dict) else scope.get("scope")}
                res["hidden_skipped"] = info.get("hidden_skipped", 0)
    except Exception as e:
        import traceback
        err_res = _err("scan 异常: %s: %s" % (type(e).__name__, str(e)[:240]),
                       traceback=traceback.format_exc()[-600:])
    finally:
        cr = cleanup() if cleanup is not None else None
    if err_res is not None:
        return err_res
    if cr is not None:
        res["cleanup"] = cr
    return _j(_receipt(res, t0))
