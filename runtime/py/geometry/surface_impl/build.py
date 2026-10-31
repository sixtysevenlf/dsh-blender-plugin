# -*- coding: utf-8 -*-
"""Split from surface_orig.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

# ────────────────────────────── 数学：B-spline / Catmull-Rom / 标架 ──────────────────────────────
def _bspline_knots(n, p):
    """clamped 均匀节点向量（n 个基函数、次数 p）：长度 n+p+1。"""
    interior = n - p - 1
    U = [0.0] * (p + 1)
    for i in range(1, interior + 1):
        U.append(i / float(interior + 1))
    U += [1.0] * (p + 1)
    return np.array(U, dtype=np.float64)


def _bspline_basis_matrix(params, n, p):
    """params(m,) × clamped B-spline 基函数 (n) → (m,n) 矩阵；t 夹到 [0,1)。"""
    U = _bspline_knots(n, p)
    m = n + p + 1
    P = np.asarray(params, dtype=np.float64).reshape(-1)
    out = np.zeros((len(P), n), dtype=np.float64)
    for row, t in enumerate(P):
        t = min(max(float(t), 0.0), 1.0 - 1e-12)
        N = ((U[:-1] <= t) & (t < U[1:])).astype(np.float64)   # 长度 m-1
        N = np.concatenate([N, [0.0]])
        for d in range(1, p + 1):
            # Cox-de Boor 递推：i 的有效上界让 idx+d+1 不越界（m-d-2），末层正好覆盖 n 个基函数
            idx = np.arange(0, m - d - 1, dtype=np.int64)
            d1 = U[idx + d] - U[idx]
            d2 = U[idx + d + 1] - U[idx + 1]
            left = np.where(d1 > 1e-12, (t - U[idx]) / np.maximum(d1, 1e-12), 0.0) * N[idx]
            right = np.where(d2 > 1e-12, (U[idx + d + 1] - t) / np.maximum(d2, 1e-12), 0.0) * N[idx + 1]
            Nn = np.zeros(m, dtype=np.float64)
            Nn[idx] = left + right
            N = Nn
        out[row, :] = N[:n]
    return out


def _bspline_interp(points, degree, sample_params, knot_params=None):
    """过控制点（插值型 clamped B-spline）：返回 (samples, dim) 与 (degree, knot_params)。

    points: (n, dim)；knot_params: (n,) 严格递增、首 0 尾 1（默认均匀）；sample_params: (m,) ∈[0,1]。
    """
    P = np.asarray(points, dtype=np.float64)
    n = int(P.shape[0])
    if n < 2:
        return np.repeat(P[:1], len(np.asarray(sample_params).reshape(-1)), axis=0), 1, np.array([0.0])
    p = int(min(max(1, int(degree)), n - 1))
    if knot_params is None:
        t = np.linspace(0.0, 1.0, n)
    else:
        t = np.asarray(knot_params, dtype=np.float64).reshape(-1)
        if len(t) != n:
            raise ValueError("knot_params 长度必须等于控制点数 %d" % n)
        if t[0] != 0.0 or abs(t[-1] - 1.0) > 1e-9:
            t = (t - t[0]) / max(t[-1] - t[0], 1e-12)
        if np.any(np.diff(t) <= 0):
            raise ValueError("knot_params 必须严格递增（截面/路径参数重复会让插值矩阵奇异）")
    A = _bspline_basis_matrix(t, n, p)
    try:
        C = np.linalg.solve(A, P)
    except np.linalg.LinAlgError:
        raise ValueError("B-spline 插值矩阵奇异（参数点太靠近或重复）；拉开 section/rail 间距")
    B = _bspline_basis_matrix(sample_params, n, p)
    return B @ C, p, t


def _cr_point(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    return 0.5 * ((2.0 * p1) + (-p0 + p2) * t + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                  + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)


def _resample_polyline(pts, count, closed=False):
    """折线/多边形 → count 个居中的 Catmull-Rom 采样点（按弦长均分；closed 环绕）。"""
    P = np.asarray(pts, dtype=np.float64)
    n = int(P.shape[0])
    count = int(max(2, count))
    if n == 0:
        raise ValueError("折线为空")
    if n == 1:
        return np.repeat(P, count, axis=0)
    if closed and n >= 3:
        seg_i = list(range(n))
        seg_j = [(i + 1) % n for i in range(n)]
        s = np.arange(count, dtype=np.float64) / float(count)
    else:
        seg_i = list(range(n - 1))
        seg_j = list(range(1, n))
        s = np.linspace(0.0, 1.0, count)
    lengths = np.array([float(np.linalg.norm(P[j] - P[i])) for i, j in zip(seg_i, seg_j)], dtype=np.float64)
    total = float(lengths.sum())
    if total <= 1e-12:
        return np.repeat(P[:1], count, axis=0)
    cum = np.cumsum(lengths) / total
    idx = np.clip(np.searchsorted(cum, s, side="right"), 0, len(seg_i) - 1)
    prev = np.concatenate([[0.0], cum[:-1]])
    span = np.maximum(cum - prev, 1e-12)
    tl = np.clip((s - prev[idx]) / span[idx], 0.0, 1.0)
    out = np.zeros((count, P.shape[1]), dtype=np.float64)
    for k in range(count):
        si = int(idx[k])
        i, j = seg_i[si], seg_j[si]
        if closed and n >= 3:
            p0, p1, p2, p3 = P[(i - 1) % n], P[i], P[j], P[(j + 1) % n]
        else:
            p0 = P[i - 1] if i > 0 else 2.0 * P[i] - P[j]
            p3 = P[j + 1] if j + 1 < n else 2.0 * P[j] - P[i]
            p1, p2 = P[i], P[j]
        out[k] = _cr_point(p0, p1, p2, p3, float(tl[k]))
    return out


def _frames(rail, orientation="auto", up=None):
    """沿折线采样点的平行传输标架 → (tangents, normals, binormals)，全部单位向量。"""
    P = np.asarray(rail, dtype=np.float64)
    n = int(P.shape[0])
    if n < 2:
        raise ValueError("rail 至少 2 点")
    tang = np.zeros_like(P)
    tang[:-1] = P[1:] - P[:-1]
    tang[-1] = P[-1] - P[-2]
    tang[0] = P[1] - P[0]
    tlen = np.linalg.norm(tang, axis=1)
    if np.any(tlen < 1e-12):
        raise ValueError("rail 切线退化（相邻点重合）")
    tang = tang / tlen[:, None]
    ref = None
    if orientation == "up_z":
        ref = np.array([0.0, 0.0, 1.0])
    elif orientation == "up_y":
        ref = np.array([0.0, 1.0, 0.0])
    elif orientation == "fixed":
        u = np.array(up if up else [0.0, 0.0, 1.0], dtype=np.float64)
        nl = float(np.linalg.norm(u))
        ref = u / nl if nl > 1e-12 else np.array([0.0, 0.0, 1.0])
    normals = np.zeros_like(P)
    prev = None
    for i in range(n):
        t = tang[i]
        cand = prev if (orientation == "auto" and prev is not None) else (ref if ref is not None else None)
        if cand is None:
            cand = np.array([0.0, 0.0, 1.0]) if abs(float(t[2])) < 0.9 else np.array([0.0, 1.0, 0.0])
        nv = cand - t * float(np.dot(cand, t))
        ln = float(np.linalg.norm(nv))
        if ln < 1e-8:
            # 参考轴与切线近平行：换一根全局轴，再不行用上一点法线
            alt = np.array([1.0, 0.0, 0.0]) if abs(float(t[0])) < 0.9 else np.array([0.0, 1.0, 0.0])
            nv = alt - t * float(np.dot(alt, t))
            ln = float(np.linalg.norm(nv))
            if ln < 1e-8 and prev is not None:
                nv = prev - t * float(np.dot(prev, t))
                ln = float(np.linalg.norm(nv))
        if ln < 1e-8:
            raise ValueError("无法为 rail 构造正交标架（切线方向病态）")
        nv = nv / ln
        if prev is not None and float(np.dot(nv, prev)) < 0.0:
            nv = -nv
        normals[i] = nv
        prev = nv
    binormals = np.cross(tang, normals)
    bl = np.linalg.norm(binormals, axis=1)
    binormals = binormals / np.maximum(bl, 1e-12)[:, None]
    return tang, normals, binormals


def _grid_faces(rows, cols, closed_u=False, closed_v=False):
    """(rows × cols) 顶点网格 → 四边形 face 列表（约定：行=v，列=u）。"""
    faces = []
    r_max = rows if closed_v else rows - 1
    c_max = cols if closed_u else cols - 1
    for r in range(r_max):
        r2 = (r + 1) % rows
        for c in range(c_max):
            c2 = (c + 1) % cols
            a = r * cols + c
            b = r * cols + c2
            cc = r2 * cols + c2
            d = r2 * cols + c
            faces.append((a, b, cc, d))
    return faces


# ────────────────────────────── M1：曲面网格生成（mm → Blender 单位） ──────────────────────────────
def _build_loft(norm):
    secs = norm["sections"]
    u = int(norm["tessellation"]["u"])
    v = int(norm["tessellation"]["v"])
    ns = len(secs)
    rings = []
    for sec in secs:
        rings.append(_resample_polyline(np.asarray(sec["points_mm"], dtype=np.float64), u,
                                        closed=bool(norm["closed"])))
    rings = np.asarray(rings, dtype=np.float64)                       # (ns,u,2)
    zs = np.array([s["z_mm"] for s in secs], dtype=np.float64)
    t = (zs - zs[0]) / max(zs[-1] - zs[0], 1e-12)                     # 截面参数（严格递增）
    sp = np.linspace(0.0, 1.0, v)
    flat = rings.reshape(ns, -1)                                       # (ns,u*2)
    xy, p_used, _t = _bspline_interp(flat, int(norm["degree"]), sp, knot_params=t)
    xy = xy.reshape(v, u, 2)
    z = np.interp(sp, t, zs)                                           # z 沿 v 线性（不额外摆动）
    verts = np.concatenate([xy, np.repeat(z[:, None, None], u, axis=1)], axis=2)  # (v,u,3) mm
    faces = _grid_faces(v, u, closed_u=bool(norm["closed"]), closed_v=False)
    return {"verts_mm": verts.reshape(-1, 3), "faces": faces,
            "meta": {"kind": "loft", "u": u, "v": v, "sections": ns, "degree_used": p_used,
                     "closed_u": bool(norm["closed"])}}


def _build_sweep(norm):
    u = int(norm["tessellation"]["u"])
    v = int(norm["tessellation"]["v"])
    prof = _resample_polyline(np.asarray(norm["profile_mm"], dtype=np.float64), u,
                              closed=bool(norm["closed"]))
    rail_raw = np.asarray(norm["rail_mm"], dtype=np.float64)
    closed_rail = bool((norm.get("constraints") or {}).get("closed_rail") or False)
    rail = _resample_polyline(rail_raw, v, closed=closed_rail)
    tang, normals, binormals = _frames(rail, str(norm["orientation"]),
                                       (norm.get("constraints") or {}).get("up"))
    # scale：标量或逐 rail 点列表 → 在弦长参数上插值到 v 个采样
    sc = norm["scale"]
    if isinstance(sc, list):
        src = np.asarray(sc, dtype=np.float64)
        if len(src) != len(rail_raw):
            src = np.repeat(src[:1], len(rail_raw))
        chord = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(rail_raw, axis=0), axis=1))])
        chord = chord / max(chord[-1], 1e-12)
        rs = np.linspace(0.0, 1.0, len(rail))
        scale_v = np.interp(rs, chord, src)
    else:
        scale_v = np.full(len(rail), float(sc), dtype=np.float64)
    twist = np.radians(float(norm["twist_deg"]) or 0.0)
    s_par = np.linspace(0.0, 1.0, len(rail))
    verts = np.zeros((len(rail), u, 3), dtype=np.float64)
    for i in range(len(rail)):
        ang = twist * float(s_par[i])
        ca, sa = math.cos(ang), math.sin(ang)
        for k in range(u):
            px, py = float(prof[k][0]) * float(scale_v[i]), float(prof[k][1]) * float(scale_v[i])
            qx, qy = ca * px - sa * py, sa * px + ca * py
            verts[i, k] = rail[i] + normals[i] * qx + binormals[i] * qy
    faces = _grid_faces(len(rail), u, closed_u=bool(norm["closed"]), closed_v=closed_rail)
    return {"verts_mm": verts.reshape(-1, 3), "faces": faces,
            "meta": {"kind": "sweep", "u": u, "v": v, "profile_closed": bool(norm["closed"]),
                     "rail_closed": closed_rail, "twist_deg": float(norm["twist_deg"] or 0.0),
                     "orientation": str(norm["orientation"]), "scale": ("list" if isinstance(sc, list) else sc)}}


def _build_patch(norm):
    grid = np.asarray(norm["control_grid"], dtype=np.float64)          # (n_u, n_v, 3)
    n_u, n_v = int(grid.shape[0]), int(grid.shape[1])
    u = int(norm["tessellation"]["u"])
    v = int(norm["tessellation"]["v"])
    pu = int(min(int(norm["degree_u"]), n_u - 1))
    pv = int(min(int(norm["degree_v"]), n_v - 1))
    su = np.linspace(0.0, 1.0, u)
    sv = np.linspace(0.0, 1.0, v)
    Bu = _bspline_basis_matrix(su, n_u, pu)
    Bv = _bspline_basis_matrix(sv, n_v, pv)
    pts = np.einsum("ai,ijk,bj->abk", Bu, grid, Bv)                    # (u,v,3)
    faces = []
    for i in range(u - 1):
        for j in range(v - 1):
            a = i * v + j
            b = (i + 1) * v + j
            c = (i + 1) * v + (j + 1)
            d = i * v + (j + 1)
            faces.append((a, b, c, d))
    return {"verts_mm": pts.reshape(-1, 3), "faces": faces,
            "meta": {"kind": "patch", "u": u, "v": v, "degree_u_used": pu, "degree_v_used": pv,
                     "control_grid": [n_u, n_v]}}


def surface_mesh_data(norm):
    """M1：spec 归一化结果 → 内存网格（verts 为 mm；faces 索引）＋ meta。"""
    kind = str(norm.get("kind"))
    if kind == "loft":
        data = _build_loft(norm)
    elif kind == "sweep":
        data = _build_sweep(norm)
    elif kind == "patch":
        data = _build_patch(norm)
    else:
        raise ValueError("未知 kind: %r" % kind)
    verts = np.asarray(data["verts_mm"], dtype=np.float64)
    if verts.size and not np.isfinite(verts).all():
        raise ValueError("生成结果出现 NaN/Inf（参数病态）")
    faces = [tuple(int(i) for i in f) for f in data["faces"]]
    nv = len(verts)
    for f in faces:
        if any(i < 0 or i >= nv for i in f):
            raise ValueError("face 索引越界")
    return {"verts_mm": verts, "faces": faces, "meta": data["meta"],
            "verts": int(nv), "tris": int(sum(max(0, len(f) - 2) for f in faces))}
