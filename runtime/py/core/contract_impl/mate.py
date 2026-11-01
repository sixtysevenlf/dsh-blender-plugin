# -*- coding: utf-8 -*-
"""Split from contract.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .fingerprint import _r4

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
    if INSIDE_MAX_TRIS > 0 and (mdA["tris_n"] > INSIDE_MAX_TRIS or mdB["tris_n"] > INSIDE_MAX_TRIS):
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
    if VOL_MAX_TRIS <= 0 or (mdA["tris_n"] + mdB["tris_n"]) <= VOL_MAX_TRIS:
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


def _c_mate_check_p1(_state):
    conn_id, contact_max_mm, depth_eps_mm, fit, nominal = (
        _state["conn_id"], _state["contact_max_mm"], _state["depth_eps_mm"], _state["fit"], _state["nominal"])
    samples, tol_mm = _state["samples"], _state["tol_mm"]
    expected_mm = (_state.get("expected_mm"))
    try:
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
        if MATE_MAX_TRIS > 0 and (mdA["tris_n"] > MATE_MAX_TRIS or mdB["tris_n"] > MATE_MAX_TRIS):
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
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def _c_mate_check_p2(_state):
    PA, PB, allowed_mm, conn, conn_id = (
        _state.get("PA"), _state.get("PB"), _state.get("allowed_mm"), _state.get("conn"), _state["conn_id"])
    contact, contact_max_m, contact_max_mm, diag_m, eps_mm = (
        _state.get("contact"), _state.get("contact_max_m"), _state["contact_max_mm"], _state.get("diag_m"), _state.get("eps_mm"))
    expected_mm, fit, fit_note, fracA, fracB = (
        _state.get("expected_mm"), _state["fit"], _state.get("fit_note"), _state.get("fracA"), _state.get("fracB"))
    free, gap, mdA, mdB, meas = (
        _state.get("free"), _state.get("gap"), _state.get("mdA"), _state.get("mdB"), _state.get("meas"))
    mm_per_unit, nominal, ov, pen, tol_src = (
        _state.get("mm_per_unit"), _state["nominal"], _state.get("ov"), _state.get("pen"), _state.get("tol_src"))
    tol_used, ub, used_min, used_req, why = (
        _state.get("tol_used"), _state.get("ub"), _state.get("used_min"), _state.get("used_req"), _state.get("why"))
    hi_mm, lo_mm, med, verdict = (_state.get("hi_mm"), _state.get("lo_mm"), _state.get("med"), _state.get("verdict"))
    try:
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
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

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
    _state = dict(locals())
    for _fn_ in (_c_mate_check_p1, _c_mate_check_p2):
        _r = _fn_(_state)
        if _r is not None:
            return _r
