# -*- coding: utf-8 -*-
"""Split from audit.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .conn_helpers import _box_fields, _conn_max_tris, _exc_body, _file_extras, _named_boxes, _nm, _objs_with_optional_file, _r, _units, _world_tris
from .drift import BVH_MAX_TRIS, _sample

def _components(tris, precision=5):
    """按共享顶点（四舍五入去重）做连通分量 → (comp_of_tri, 顶点数)。"""
    import numpy as np
    flat = tris.reshape(-1, 3)
    uniq, inv = np.unique(np.round(flat, int(precision)), axis=0, return_inverse=True)
    f = inv.reshape(-1, 3).astype(np.int64)
    nv = int(uniq.shape[0])
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]], axis=0)
    e.sort(axis=1)
    e = np.unique(e, axis=0)
    parent = np.arange(nv, dtype=np.int64)
    a, b = e[:, 0], e[:, 1]
    for _ in range(64):
        before = parent.copy()
        np.minimum.at(parent, b, parent[a])
        np.minimum.at(parent, a, parent[b])
        parent = parent[parent]
        if np.array_equal(parent, before):
            break
    _u, comp = np.unique(parent[f[:, 0]], return_inverse=True)
    return comp.reshape(-1).astype(np.int64), nv


def _confirm_micro_with_mesh(tris, comp, cid, radius_units, tree_cache=None, max_pts=400, big_factor=50.0):
    """把「bbox 判 micro」的分量拿到网格上复核：真最近距离到底有没有小于阈值。

    为什么必须复核（本插件与上游的关键差异）：上游是**单网格合并体**，分量之间的 bbox 几乎不重叠，
    bbox 间隙是有信息量的；而我们这里每个部件是独立对象，密集装配里 bbox 普遍互相重叠 → bbox 间隙恒为 0
    → 全被判 micro → 门一律放行（fail-open）。所以多对象场景必须补一次三角级确认：
      对分量采样 ≤max_pts 个点，用全局 BVH 的 find_nearest_range(半径=阈值) 找命中；
      命中三角形属于**别的分量** → 确认贴上了（真 micro）；半径内一无所获 → 其实是浮块（bbox 骗人）。
    返回 (confirmed_micro, true_gap_units|None, note)；true_gap 只在"更大半径里找到了别的分量"时给值。
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    sel = np.nonzero(comp == int(cid))[0]
    if len(sel) == 0:
        return False, None, "空分量"
    # 采样必须落在**面上**（面积加权），不能只取顶点：大平面之间的 0.2 mm 缝，顶点可能相距几米
    uniq = _sample(tris[sel], int(max_pts), np.random.default_rng(0))
    if len(uniq) > int(max_pts):
        step = max(1, len(uniq) // int(max_pts))
        uniq = uniq[::step][:int(max_pts)]
    if tree_cache is not None and tree_cache.get("tree") is None:
        V = tris.reshape(-1, 3).tolist()
        polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
        tree_cache["tree"] = BVHTree.FromPolygons(V, polys, all_triangles=True)
    tree = (tree_cache or {}).get("tree")
    if tree is None:
        return None, None, "BVH 未建立"
    r = float(radius_units)
    r_big = r * float(big_factor)
    hit_other, best = False, None
    for p in uniq:
        pt = (float(p[0]), float(p[1]), float(p[2]))
        for (loc, nor, idx, dist) in tree.find_nearest_range(pt, r):
            if int(comp[int(idx)]) != int(cid):
                hit_other = True
                break
        if hit_other:
            break
        for (loc, nor, idx, dist) in tree.find_nearest_range(pt, r_big):   # 顺手量一下到底漂多远
            if int(comp[int(idx)]) != int(cid):
                d = float(dist)
                if best is None or d < best:
                    best = d
    if hit_other:
        return True, best, "网格确认：阈值半径内有其它分量的面（真贴上）"
    return False, best, "网格复核：阈值半径内没有其它分量的面 → bbox 判的 micro 不成立%s" % (
        "" if best is None else "（更大半径内最近 %.6f 单位）" % best)


def _snap_pair_mesh(tris, comp, cid, tree_cache=None, max_pts=400):
    """网格级最近点对：返回 (delta 向量[指向最近邻], 距离, note)。

    bbox 级间隙在多对象场景会读成 0（所以 snap 必须能走网格级），这也是"贴而未重合"唯一可靠的收口依据。
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    sel = np.nonzero(comp == int(cid))[0]
    if len(sel) == 0:
        return None, None, "空分量"
    pts = _sample(tris[sel], int(max_pts), np.random.default_rng(0))   # 面上采样（见 _confirm_micro_with_mesh）
    if len(pts) > int(max_pts):
        pts = pts[::max(1, len(pts) // int(max_pts))][:int(max_pts)]
    if tree_cache is not None and tree_cache.get("tree") is None:
        V = tris.reshape(-1, 3).tolist()
        polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
        tree_cache["tree"] = BVHTree.FromPolygons(V, polys, all_triangles=True)
    tree = (tree_cache or {}).get("tree")
    if tree is None:
        return None, None, "BVH 未建立"
    best = None
    for p in pts:
        pp = (float(p[0]), float(p[1]), float(p[2]))
        hit = tree.find_nearest(pp)
        if hit is None or hit[0] is None:
            continue
        loc, nor, idx, dist = hit
        if int(comp[int(idx)]) == int(cid):
            continue
        if best is None or float(dist) < best[0]:
            best = (float(dist), (float(loc[0]), float(loc[1]), float(loc[2])), pp)
    if best is None:
        return None, None, "网格近邻里没有别的分量（孤立件）"
    return [best[1][k] - best[2][k] for k in range(3)], best[0], "网格级最近点对（点到面）"


def _ov(a, b):
    """两个 bbox 的重叠体积（a/b 都是 (lo, hi)）。"""
    v = 1.0
    for k in range(3):
        d = min(float(a[1][k]), float(b[1][k])) - max(float(a[0][k]), float(b[0][k]))
        if d <= 0:
            return 0.0
        v *= d
    return v


def _boxvol(b):
    return max(0.0, float(b[1][0]) - float(b[0][0])) * max(0.0, float(b[1][1]) - float(b[0][1])) \
        * max(0.0, float(b[1][2]) - float(b[0][2]))


def _conn_header(objs, name_of, micro_gap_mm, mm_per_unit, m_per_unit):
    """回执头（单位/对象名/boxes 之外的部分）。"""
    return {"units": {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6),
                      "scene_unit": "m" if abs(m_per_unit - 1.0) < 1e-12 else "scaled",
                      "micro_gap_mm": _r(micro_gap_mm, 4),
                      "micro_gap_units": _r(micro_gap_mm / mm_per_unit, 8)},
            "objects": len(objs), "object_names": [_nm(o, name_of) for o in objs],
            "gap_note": "gap 是 bbox 级 → 低估真实间隙（贴而未重合会读成 0），偏向判 micro 放行（与上游同口径）"}


def _conn_stats(tris, comp, ncomp, owner, objs, name_of, mm_per_unit):
    """逐连通分量统计（bbox/体积/owner）→ 排序 + rank + span_fraction；返回 (stats, nv, model_span)。"""
    import numpy as np
    stats = []
    for c in range(ncomp):
        sel = np.nonzero(comp == c)[0]
        t = tris[sel]
        p = t.reshape(-1, 3)
        lo, hi = p.min(axis=0), p.max(axis=0)
        size = hi - lo
        vol = abs(float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0))
        stats.append({"comp": c, "tris": int(len(sel)), "min": [_r(x) for x in lo], "max": [_r(x) for x in hi],
                      "size": [_r(x) for x in size], "max_dim": _r(size.max()), "volume": _r(vol, 8),
                      "owners": {_nm(objs[int(i)], name_of): int((owner[sel] == i).sum())
                                 for i in np.unique(owner[sel])}})
    all_lo = np.array([s["min"] for s in stats]).min(axis=0)
    all_hi = np.array([s["max"] for s in stats]).max(axis=0)
    model_span = float((all_hi - all_lo).max())
    for s in stats:
        s["span_fraction"] = _r(s["max_dim"] / model_span, 6) if model_span > 0 else 0.0
    stats.sort(key=lambda s: s["volume"], reverse=True)
    for rank, s in enumerate(stats):
        s["rank"] = rank
    return stats, model_span


def _conn_gaps(stats, mm_per_unit, micro_gap_mm, visible_frac):
    """邻域 bbox 间隙（mm）：每个分量到最近其它分量的距离；并写 gap/visible/attached 初值。"""
    gaps = []
    for i, s in enumerate(stats):
        best, bj = None, None
        for j, t2 in enumerate(stats):
            if i == j:
                continue
            d2 = 0.0
            for k in range(3):
                g = max(0.0, max(s["min"][k] - t2["max"][k], t2["min"][k] - s["max"][k]))
                d2 += g * g
            if best is None or d2 < best:
                best, bj = d2, j
        gaps.append({"gap_units": _r((best or 0.0) ** 0.5, 8), "gap_mm": _r(((best or 0.0) ** 0.5) * mm_per_unit, 5),
                     "nearest_rank": (stats[bj]["rank"] if bj is not None else None)})
    # 分类在下面完成：attached=False → 真浮块；True → 阈值内有邻居（容忍）；None → 未确认（降级）
    for s, g in zip(stats, gaps):
        s["gap_mm"] = g["gap_mm"]
        s["gap_units"] = g["gap_units"]
        s["bbox_micro"] = bool(g["gap_mm"] < float(micro_gap_mm))
        s["visible"] = bool(s["span_fraction"] >= float(visible_frac))
        s["attached"] = None
        s["true_gap_units"] = None
        s["mesh_confirmed"] = None


def _conn_mesh(stats, tris, comp, ncomp, micro_units, mesh_mode, micro_gap_mm):
    """逐分量判「是否与其它分量相接」：单分量/非 micro/bbox-only/网格复核。"""
    tree_cache = {}
    for s in stats:
        if ncomp == 1:
            # v0.9.6（现场反馈 #2 收口）：浮块的定义是"这个分量与其它分量都不相接"——
            # 整个范围**只有 1 个连通分量**时这条判据无从成立（内含的壳要么共享顶点、要么**正好相贴**
            # 被 _components 按 precision 焊接在一起）。原先会把"整体焊成一块"误报成 1 个可见浮块：
            # 实测两块正好相贴（gap=0.00 mm）⇒ 旧逻辑 fail；现在是 pass。
            s["attached"] = True
            s["evidence"] = "single-body"
            s["confirm_note"] = ("范围内只有 1 个连通分量（%d 三角面）→ 不存在「与其它分量不相接」，不判浮块；"
                                 "要查内部壳是否贴合，用 audit_mesh(island_split=true)" % len(tris))
            continue
        if not s["bbox_micro"]:
            s["attached"] = False
            s["evidence"] = "bbox"
            s["confirm_note"] = "bbox 间隙 %.4f mm 已超阈值 %.2f mm → 漂着（无需网格复核）" % (s["gap_mm"], micro_gap_mm)
            continue
        if mesh_mode == "bbox-only":
            s["attached"] = None
            s["evidence"] = "bbox-only"
            s["confirm_note"] = "面数 %d 超过 %d，跳过网格复核 → 不能当作已贴上（门按降级处理）" % (len(tris), BVH_MAX_TRIS)
            continue
        try:
            conf, true_gap, note = _confirm_micro_with_mesh(tris, comp, s["comp"], micro_units, tree_cache)
        except Exception as e:
            conf, true_gap, note = None, None, "网格复核失败：%s" % str(e)[:120]
        s["mesh_confirmed"] = conf
        s["true_gap_units"] = true_gap
        s["confirm_note"] = note
        s["evidence"] = "bbox+mesh"
        s["attached"] = conf


def _conn_attrib(stats, boxes):
    """只对真浮块做归因（bbox 重叠率 → 改哪个对象）。"""
    for s in stats:
        if s["attached"] is not False:
            continue
        # 归因：浮块 bbox 与每个对象 bbox 的重叠率（Procedura: matchFloatersToBoxes）
        fbox = (s["min"], s["max"])
        fvol = _boxvol(fbox) or 1.0
        scored = []
        for name, b in boxes.items():
            if b is None:
                continue
            frac = _ov(fbox, (list(b[0]), list(b[1]))) / fvol
            if frac > 0.01:
                scored.append((frac, name, _boxvol((list(b[0]), list(b[1])))))
        scored.sort(reverse=True)
        best = scored[0] if scored else None
        s["attribution"] = {
            "object": best[1] if best else None,
            "confidence": _r(min(1.0, best[0]) if best else 0.0, 4),
            "object_fraction": _r(min(1.0, fvol / best[2]) if best and best[2] > 0 else 0.0, 4),
            "also_overlaps": [n for f, n, _ in scored[1:] if f >= 0.2],
            "contains": s["owners"],
            "note": "object_fraction≈1 ⇒ 该浮块就是整个对象（可整件平移）；很小 ⇒ 只是焊接体的一块（平移会撕开焊点）",
        }


def _conn_gap_hist(stats, floaters, micro_gap_mm, mm_per_unit):
    """间隙分布（口径由调用者选）；返回 (hist, gap_range)。"""
    def _gap_mm_of(s):
        if s.get("true_gap_units") is not None:
            return float(s["true_gap_units"]) * mm_per_unit
        return float(s.get("gap_mm") or 0.0)

    hist = {}
    for s in stats:
        g = _gap_mm_of(s)
        key = ("<%.2f mm(相接口径)" % micro_gap_mm) if g < micro_gap_mm else (
            "%.2f-1 mm" % micro_gap_mm if g < 1 else ("1-5 mm" if g < 5 else ("5-20 mm" if g < 20 else ">20 mm")))
        hist[key] = hist.get(key, 0) + 1
    fgaps = [_gap_mm_of(s) for s in floaters]
    gap_range = (round(min(fgaps), 4), round(max(fgaps), 4)) if fgaps else None
    return hist, gap_range


def _conn_result(out, tris, nv, stats, floaters, tolerated, unconfirmed, objs, owner, name_of,
                 mesh_mode, micro_gap_mm, hist, gap_range, visible_f, model_span, limit):
    """最终回执（计数/直方图/口径说明）。"""
    return dict(out, analyzed=True, tris=int(len(tris)), verts=int(nv), components=stats, floaters=floaters,
                tolerated=tolerated, unconfirmed=unconfirmed,
                _raw={"tris": tris, "comp": None},     # 给 snap 复用（公开 op 的 JSON 里不会出现）
                obj_tris={_nm(objs[k], name_of): int((owner == k).sum()) for k in range(len(objs))},
                micro_confirm=mesh_mode,
                floater_count=len(floaters), visible_floater_count=len(visible_f),
                real_floater_count=len(visible_f),
                micro_floater_count=len([s for s in tolerated if s["visible"]]),
                unconfirmed_floater_count=len([s for s in unconfirmed if s["visible"]]),
                attached_count=len(tolerated),
                gap_histogram=hist, floater_gap_range_mm=gap_range,
                gate_caliber={"micro_gap_mm": _r(micro_gap_mm, 4),
                              "meaning": "『算作相接』的距离口径：0.3mm=单一实体/3D 打印（上游口径）；"
                                         "带设计间隙的装配件请按工艺给（如 1–2mm）",
                              "how_to_change": "audit_gate(args={micro_gap_mm: 2.0})"},
                max_floater_span_fraction=_r(max([s["span_fraction"] for s in floaters] or [0.0]), 6),
                model_span=_r(model_span), limit=int(limit))


def _conn_analyze(objs, precision=5, visible_frac=VISIBLE_SPAN_FRACTION, micro_gap_mm=MICRO_GAP_MM,
                  max_tris=None, limit=24, name_of=None):
    """连通分量分析内核：返回 dict（含 numpy 细节，供 snap 复用；公开 op 只暴露格式化结果）。

    name_of（v0.9.1，可选）：{会话内名字 → 上报名}。file= 模式下加载进来的对象会被 Blender 改名
    （重名加 .001），而反馈要的是"对象名与文件内一致" → 所有对外名字都经它映射；会话模式下为 None，
    行为与老版本逐字节一致。
    """
    m_per_unit, mm_per_unit = _units()
    cap = int(max_tris or _conn_max_tris())
    tris, owner, boxes = _world_tris(objs)
    boxes = _named_boxes(objs, boxes, name_of)
    out = _conn_header(objs, name_of, micro_gap_mm, mm_per_unit, m_per_unit)
    out["boxes"] = boxes
    if tris is None or len(tris) == 0:
        return dict(out, analyzed=False, reason="范围内没有三角面（空产出必须失败）", tris=0)
    if cap > 0 and len(tris) > cap:   # 默认 cap=0 → 全跑
        return dict(out, analyzed=False, tris=int(len(tris)),
                    reason="三角面 %d 超过上限 %d（env DSH_CONN_MAX_TRIS 可调）→ 跳过分析" % (len(tris), cap),
                    note="未分析 ≠ 已连通：门保持 degraded，不给通过结论")
    p_all = tris.reshape(-1, 3)
    out["aabb"] = _box_fields(p_all.min(axis=0), p_all.max(axis=0), mm_per_unit)
    comp, nv = _components(tris, precision)
    ncomp = int(comp.max()) + 1
    stats, model_span = _conn_stats(tris, comp, ncomp, owner, objs, name_of, mm_per_unit)
    _conn_gaps(stats, mm_per_unit, micro_gap_mm, visible_frac)
    # 判据（v0.9.0 修正）：不再用「最大体积分量=主体、其余都是浮块」—— 两个等大零件时那条规则会
    # 随便挑一个当浮体（自检抓到过）。物理上要问的是：**这个分量有没有跟别的分量相接**。
    micro_units = float(micro_gap_mm) / mm_per_unit
    mesh_mode = "mesh(BVH)" if (BVH_MAX_TRIS <= 0 or len(tris) <= BVH_MAX_TRIS) else "bbox-only"
    _conn_mesh(stats, tris, comp, ncomp, micro_units, mesh_mode, micro_gap_mm)
    floaters = [s for s in stats if s["attached"] is False]
    tolerated = [s for s in stats if s["attached"] is True]
    unconfirmed = [s for s in stats if s["attached"] is None]
    # 间隙分布：口径（micro_gap_mm）该给多少，取决于你要什么 —— 单一实体/3D 打印用 0.3mm（上游口径）；
    # 带设计间隙的装配件按自己的工艺给（1–2mm 很常见）。这里把分布摊开，让调用者自己选口径。
    hist, gap_range = _conn_gap_hist(stats, floaters, micro_gap_mm, mm_per_unit)
    _conn_attrib(stats, boxes)
    visible_f = [s for s in floaters if s["visible"]]
    res = _conn_result(out, tris, nv, stats, floaters, tolerated, unconfirmed, objs, owner, name_of,
                       mesh_mode, micro_gap_mm, hist, gap_range, visible_f, model_span, limit)
    res["_raw"]["comp"] = comp     # 给 snap 复用（公开 op 的 JSON 里不会出现）
    return res


def audit_connectivity(objects=None, scope=None, include_hidden=False, visible_frac=VISIBLE_SPAN_FRACTION,
                       micro_gap_mm=MICRO_GAP_MM, precision=5, limit=24, max_tris=None, file=None,
                       per_object=False):
    """连通分量体检：这堆对象到底连成几块？哪几块是"看得见的浮块"？归因到哪个对象？

    v0.9.1：新增 file=<.blend 路径> —— 把该文件里的对象**临时**追加进会话，跑同一条分析链路，
    跑完（无论成败）在 finally 里删干净；返回体的 source / file_load / cleanup 说明来源、加载了什么、
    删了什么。此时 objects/scope 当"文件内的**对象名**"筛选（append 不带集合成员关系）。
    报告里的对象名一律是**文件里的原名**（重名追加时 Blender 会加 .001，用 object_names 核对）。
    老签名与老返回键一个没动，只做加法。
    """
    if per_object and not file:
        # v0.9.6（现场反馈 #2）：整体 3M 面 ⇒ 全局只能 bbox-only（degraded）。逐对象跑，每件通常
        # 在 BVH_MAX_TRIS 之内，于是拿得回 mesh(BVH) 级结论，而不是一句"降级"。
        objs0, missing0, err0 = _pick(objects, scope)
        if err0:
            return _j({"ok": False, "error": err0, "missing": missing0})
        rows = []
        for ob in objs0:
            if ob.type != "MESH":
                continue
            sub = json.loads(audit_connectivity(objects=[ob.name], include_hidden=include_hidden,
                                                visible_frac=visible_frac, micro_gap_mm=micro_gap_mm,
                                                precision=precision, limit=limit, max_tris=max_tris))
            g = _gate_from(sub)
            rows.append({"object": ob.name, "state": g.get("state"), "ok": g.get("ok"),
                         "verdict": g.get("verdict"), "tris": sub.get("tris"),
                         "mesh_mode": sub.get("micro_confirm"),
                         "visible_floaters": g.get("visible_floater_count"),
                         "real_floaters": g.get("real_floater_count"),
                         "reason": (g.get("reason") or "")[:200]})
        if not rows:
            return _j({"ok": False, "error": "范围内没有 mesh 对象", "missing": missing0})
        states = [r["state"] for r in rows]
        verdict = ("fail" if "fail" in states else ("degraded" if any(x != "pass" for x in states) else "pass"))
        payload = {"ok": verdict == "pass", "verdict": verdict, "per_object": True, "count": len(rows),
                   "objects": rows,
                   "failed_objects": [r["object"] for r in rows if r["state"] == "fail"],
                   "degraded_objects": [r["object"] for r in rows if r["state"] == "degraded"],
                   "note": "逐对象复核：整体面数超 BVH 上限时用它；verdict 三态，degraded ≠ 通过"}
        if verdict == "degraded":
            payload["warn"] = "DEGRADED ≠ 通过：有对象未给出通过结论（看 objects[].reason）"
        return _j(payload)
    S = _objs_with_optional_file(objects, scope, include_hidden, file)
    objs, missing, err, name_of = S["objs"], S["missing"], S["err"], S["name_of"]
    try:
        if err:
            res = {"ok": False, "error": err, "missing": missing, "file": (S["load"] or {}).get("path"),
                   "hint": "file= 模式：文件要能打开、里面有可见 mesh；空产出一律 ok:false"}
        elif not objs:
            res = {"ok": False, "error": "范围内 0 个 mesh", "missing": missing,
                   "hint": "确认对象名/集合名，或 include_hidden=True；静默空产出必须失败"}
        else:
            a = _conn_analyze(objs, precision=precision, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm,
                              max_tris=max_tris, limit=limit, name_of=name_of)
            gate = _gate_from(a)
            res = {"ok": True, "analyzed": a["analyzed"], "units": a["units"], "objects": a["objects"],
                   "object_names": a["object_names"], "tris": a.get("tris"), "missing": missing,
                   "aabb": a.get("aabb")}
            if not a["analyzed"]:
                res.update({"reason": a.get("reason"), "note": a.get("note"), "gate": gate})
            else:
                res.update({
                    "components": a["components"][:int(limit)],
                    "floater_count": a["floater_count"],
                    "visible_floater_count": a["visible_floater_count"],
                    "real_floater_count": a["real_floater_count"],
                    "micro_floater_count": a["micro_floater_count"],
                    "unconfirmed_floater_count": a.get("unconfirmed_floater_count", 0),
                    "micro_confirm": a.get("micro_confirm"),
                    "max_floater_span_fraction": a["max_floater_span_fraction"],
                    "model_span": a["model_span"],
                    "gap_note": a.get("gap_note"),
                    "gap_histogram": a.get("gap_histogram"),
                    "floater_gap_range_mm": a.get("floater_gap_range_mm"),
                    "gate_caliber": a.get("gate_caliber"),
                    "attached_count": a.get("attached_count"),
                    "floaters": [{"rank": s["rank"], "tris": s["tris"], "span_fraction": s["span_fraction"],
                                  "gap_mm": s["gap_mm"], "visible": s["visible"],
                                  "bbox_micro": s.get("bbox_micro"), "mesh_confirmed": s.get("mesh_confirmed"),
                                  "true_gap_units": s.get("true_gap_units"), "evidence": s.get("evidence"),
                                  "confirm_note": s.get("confirm_note"),
                                  "bbox_min": s["min"], "bbox_max": s["max"], "size": s["size"],
                                  "attribution": s["attribution"]} for s in a["floaters"][:int(limit)]],
                    "tolerated": [{"rank": s["rank"], "span_fraction": s["span_fraction"],
                                   "gap_mm": s["gap_mm"], "bbox_micro": s.get("bbox_micro"),
                                   "mesh_confirmed": s.get("mesh_confirmed"),
                                   "true_gap_units": s.get("true_gap_units"),
                                   "objects": sorted((s.get("owners") or {}).keys()),
                                   "confirm_note": s.get("confirm_note")}
                                  for s in a.get("tolerated", [])[:int(limit)]],
                    "unconfirmed": [{"rank": s["rank"], "span_fraction": s["span_fraction"],
                                     "objects": sorted((s.get("owners") or {}).keys()),
                                     "confirm_note": s.get("confirm_note")}
                                    for s in a.get("unconfirmed", [])[:int(limit)]],
                    "gate": gate,
                    "verdict_line": _verdict_line(a, visible_frac, micro_gap_mm),
                })
    except Exception as e:
        res = _exc_body(e, "connectivity")
    finally:
        crep = S["cleanup"]() if S.get("cleanup") else None
    return _j(_file_extras(res, S, crep))


def _verdict_of(ok, state):
    """v0.9.6（现场反馈 #2）：三态**显式**成一个字段，消费方永远读 verdict，别读 ok/state 再自己推。"""
    if ok is True or state == "pass":
        return {"verdict": "pass"}
    if ok is None or state == "degraded":
        return {"verdict": "degraded",
                "warn": "DEGRADED ≠ 通过：未分析 / 未确认 / 超出复核能力 —— 这一门没有给出通过结论"}
    return {"verdict": "fail"}


def _gate_from(a):
    """连通门：只看"可见且网格复核确认非贴合"的浮块。未分析/未确认 → ok=null（degraded）。"""
    if not a.get("analyzed"):
        return dict({"ok": None, "state": "degraded", "reason": a.get("reason") or "未分析",
                     "note": "未分析不得读作已连通"}, **_verdict_of(None, "degraded"))
    real = a.get("real_floater_count", 0)          # 可见且与任何分量都不相接
    micro = a.get("micro_floater_count", 0)        # 可见但已确认相接（贴而未重合/互穿）
    unconf = a.get("unconfirmed_floater_count", 0)
    if real == 0 and unconf > 0:
        return dict({"ok": None, "state": "degraded", "visible_floater_count": a.get("visible_floater_count"),
                "unconfirmed_floater_count": unconf, "micro_tolerated": micro,
                "reason": "%d 个可见分量无法确认是否相接（%s）→ 降级，不给通过结论" % (unconf, a.get("micro_confirm")),
                "note": "上游在单网格里可以 fail-open；多对象装配不行 —— 未确认就是未确认"}, **_verdict_of(None, "degraded"))
    if real == 0:
        return dict({"ok": True, "state": "pass", "visible_floater_count": 0, "real_floater_count": 0,
                "unconfirmed_floater_count": 0, "micro_tolerated": micro,
                "reason": "" if micro == 0 else "%d 个可见分量经网格确认与邻居相接（贴而未重合/互穿）→ 容忍" % micro},
                    **_verdict_of(True, "pass"))
    worst = [s for s in a.get("floaters", []) if s.get("visible")][:6]
    return dict({"ok": False, "state": "fail", "visible_floater_count": a.get("visible_floater_count"),
            "real_floater_count": real, "micro_tolerated": micro, "unconfirmed_floater_count": unconf,
            "offenders": [{"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                           "true_gap_units": s.get("true_gap_units"), "evidence": s.get("evidence"),
                           "edit_hint": (s["attribution"] or {}).get("object")} for s in worst],
            "reason": "%d 个可见浮块：与任何其它分量都不相接（口径 %.2f mm 内找不到邻居%s）" % (
                real, MICRO_GAP_MM if not a.get("gate_caliber") else a["gate_caliber"].get("micro_gap_mm", MICRO_GAP_MM),
                ("；最近 %.2f–%.2f mm" % (a["floater_gap_range_mm"][0], a["floater_gap_range_mm"][1]))
                if a.get("floater_gap_range_mm") else "")}, **_verdict_of(False, "fail"))


def _verdict_line(a, visible_frac, micro_gap_mm):
    if not a.get("analyzed"):
        return "连通：未分析（%s）—— 不得当作已连通" % (a.get("reason") or "")
    real = a.get("real_floater_count", 0)
    micro = a.get("micro_floater_count", 0)
    unconf = a.get("unconfirmed_floater_count", 0)
    rng = a.get("floater_gap_range_mm")
    if real == 0 and unconf == 0:
        return "连通：通过（%d 个分量；%d 个可见分量经网格确认与邻居相接；阈值 可见 %.1f%%／相接 %.2f mm）" % (
            len(a["components"]), micro, visible_frac * 100, micro_gap_mm)
    if real == 0:
        return "连通：降级 —— %d 个分量无法确认相接（%s），不给通过结论" % (unconf, a.get("micro_confirm"))
    names = [((s["attribution"] or {}).get("object") or "UNMATCHED") for s in a["floaters"] if s["visible"]]
    extra = ("；与最近邻的距离 %.2f–%.2f mm —— 若这是设计间隙，把口径调大（micro_gap_mm）" % (rng[0], rng[1])) if rng else ""
    return "连通：不通过 —— %d 个可见浮块与任何分量都不相接%s，优先改 %s" % (real, extra, ", ".join(names[:6]))


def audit_gate(objects=None, scope=None, include_hidden=False, envelope=None, visible_frac=VISIBLE_SPAN_FRACTION,
               micro_gap_mm=MICRO_GAP_MM, max_floater_span_fraction=None, max_tris=None, file=None):
    """出厂门：连通 + （可选）包络 + 三态。任何"未分析"都降级为 degraded，绝不给通过。

    v0.9.1：新增 file=<.blend 路径>（临时加载 → 跑同一条链路 → finally 删净；source/file_load/
    cleanup 上报来源与收尾）。包络检查用的 per-object bbox 在 file= 模式下以**文件原名**上报。
    老签名与老返回键一个没动，只做加法（新增 object_names / aabb / source / file_load / cleanup）。
    """
    S = _objs_with_optional_file(objects, scope, include_hidden, file)
    objs, missing, err, name_of = S["objs"], S["missing"], S["err"], S["name_of"]
    try:
        if err:
            res = {"ok": False, "error": err, "missing": missing, "file": (S["load"] or {}).get("path"),
                   "hint": "file= 模式：文件要能打开、里面有可见 mesh；空产出一律 ok:false"}
        elif not objs:
            res = {"ok": False, "error": "范围内 0 个 mesh", "missing": missing}
        else:
            a = _conn_analyze(objs, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm, max_tris=max_tris,
                              name_of=name_of)
            conn = _gate_from(a)
            verdicts = {"connectivity": conn}
            ok = conn["ok"]
            if conn["ok"] is None:
                ok = None
            if max_floater_span_fraction is not None and a.get("analyzed"):
                lim = float(max_floater_span_fraction)
                got = float(a.get("max_floater_span_fraction") or 0.0)
                good = got <= lim
                verdicts["floater_span"] = {"ok": good, "max_floater_span_fraction": got, "limit": lim}
                if ok is True and not good:
                    ok = False
            if envelope is not None and len(list(envelope)) == 6:
                lo = [float(x) for x in list(envelope)[:3]]
                hi = [float(x) for x in list(envelope)[3:]]
                bad = []
                for name, b in a["boxes"].items():
                    if b is None:
                        continue
                    over = []
                    for k in range(3):
                        if float(b[0][k]) < lo[k] - 1e-9:
                            over.append(["%s_min" % "xyz"[k], _r(float(b[0][k]) - lo[k])])
                        if float(b[1][k]) > hi[k] + 1e-9:
                            over.append(["%s_max" % "xyz"[k], _r(float(b[1][k]) - hi[k])])
                    if over:
                        bad.append({"name": name, "out_of_envelope": over})
                good = len(bad) == 0
                verdicts["envelope"] = {"ok": good, "violations": len(bad), "objects": bad[:20]}
                if ok is True and not good:
                    ok = False
            res = dict({"ok": True, "ship_ok": ok,
                   "state": ("pass" if ok is True else ("degraded" if ok is None else "fail")),
                   "verdicts": verdicts, "units": a["units"], "objects": len(objs),
                   "object_names": a.get("object_names"), "tris": a.get("tris"), "analyzed": a.get("analyzed"),
                   "aabb": a.get("aabb"),
                   "verdict_line": _verdict_line(a, visible_frac, micro_gap_mm),
                   "note": "ok=true 表示分析都跑完了（不是门通过）；门结论看 verdict（ship_ok 保留兼容）"},
                   **_verdict_of(ok, "pass" if ok is True else ("degraded" if ok is None else "fail")))
    except Exception as e:
        res = _exc_body(e, "gate")
    finally:
        crep = S["cleanup"]() if S.get("cleanup") else None
    return _j(_file_extras(res, S, crep))
