# -*- coding: utf-8 -*-
"""Split from imgtools.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .common import _KIT
from .analysis import _erode1, _label_cc, _rnd



# ── 轮廓（Moore 跟踪 + Douglas-Peucker + 曲率极值 + 凸包）────────────────
DIRS8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def _trace_contour(m):
    """Moore 邻域跟踪 → 有序外轮廓 [[x,y],...]（像素中心坐标）。失败返回 None。"""
    m = np.asarray(m, dtype=bool)
    H, W = m.shape
    ys, xs = np.nonzero(m)
    if len(ys) == 0:
        return None
    top = int(ys.min())
    row_xs = xs[ys == top]
    start = (top, int(row_xs.min()))     # 最上最左（左侧必为背景）
    p = start
    dprev = 6                             # 6 = W：来自西侧背景
    pts = [(start[1], start[0])]
    seen = set()
    seen.add((p, dprev))
    limit = min(4 * (H + W) + 4000, 1200000)
    for _ in range(limit):
        found = None
        for k in range(1, 9):
            d = (dprev + k) % 8
            q = (p[0] + DIRS8[d][0], p[1] + DIRS8[d][1])
            if 0 <= q[0] < H and 0 <= q[1] < W and m[q[0], q[1]]:
                found = (q, d)
                break
        if found is None:
            break
        p, d = found
        nd = (d + 4) % 8
        pts.append((p[1], p[0]))
        state = (p, nd)
        if p == start:
            break
        if state in seen:
            break
        seen.add(state)
        dprev = nd
    if len(pts) < 3:
        return None
    return np.asarray(pts, dtype=np.float64)


def _dp_simplify(points, tol):
    """Douglas-Peucker（开折线）。"""
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n <= 2:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = pts[j] - pts[i]
        L = float(math.hypot(seg[0], seg[1]))
        rel = pts[i + 1:j] - pts[i]
        if L < 1e-9:
            d = np.hypot(rel[:, 0], rel[:, 1])
        else:
            d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / L
        k = int(np.argmax(d))
        if float(d[k]) > float(tol):
            mid = i + 1 + k
            keep[mid] = True
            stack.append((i, mid))
            stack.append((mid, j))
    return pts[keep]


def _simplify_closed(pts, max_points=64):
    """闭合轮廓 DP 简化（二分容差，点数 ≤ max_points）。"""
    if pts is None or len(pts) < 4:
        return pts
    base = np.asarray(pts, dtype=np.float64)
    if np.allclose(base[0], base[-1]):
        base = base[:-1]
    if len(base) < 4:
        return base
    d0 = np.hypot(base[:, 0] - base[0, 0], base[:, 1] - base[0, 1])
    k = int(np.argmax(d0))
    if k < 2:
        k = len(base) // 2
    c1 = base[:k + 1]
    c2 = np.vstack([base[k:], base[0:1]])
    span = max(float(np.ptp(base[:, 0])), float(np.ptp(base[:, 1])), 1.0)
    lo, hi = 0.01, span / 4.0 + 1.0
    best = None
    for _ in range(18):
        mid = 0.5 * (lo + hi)
        s1 = _dp_simplify(c1, mid)
        s2 = _dp_simplify(c2, mid)
        count = len(s1) + len(s2) - 2
        if count <= int(max_points):
            best = np.vstack([s1, s2[1:-1]]) if len(s2) > 2 else s1
            hi = mid
        else:
            lo = mid
    if best is None:
        s1 = _dp_simplify(c1, span)
        s2 = _dp_simplify(c2, span)
        best = np.vstack([s1, s2[1:-1]]) if len(s2) > 2 else s1
    return best


def _curvature_landmarks(poly, min_turn=18.0, top=8):
    """简化多边形顶点的转角极值（角点/折点）。"""
    if poly is None or len(poly) < 3:
        return []
    n = len(poly)
    rows = []
    for i in range(n):
        p0 = poly[(i - 1) % n]
        p1 = poly[i]
        p2 = poly[(i + 1) % n]
        v1 = p1 - p0
        v2 = p2 - p1
        l1 = float(math.hypot(v1[0], v1[1]))
        l2 = float(math.hypot(v2[0], v2[1]))
        if l1 < 1e-6 or l2 < 1e-6:
            continue
        cosv = float(np.clip((v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2), -1.0, 1.0))
        turn = math.degrees(math.acos(cosv))
        if turn >= min_turn:
            rows.append({"xy_px": [int(round(float(p1[0]))), int(round(float(p1[1])))],
                         "turn_deg": round(turn, 1),
                         "edge_len_px": [round(l1, 1), round(l2, 1)]})
    rows.sort(key=lambda r: -r["turn_deg"])
    return rows[:int(top)]


def _hull(points):
    """Andrew 单调链凸包 → (hull_pts [[x,y]], area_px, perimeter_px)。"""
    if points is None or len(points) == 0:
        return [], 0.0, 0.0
    pts = sorted(set((int(round(float(p[0]))), int(round(float(p[1])))) for p in np.asarray(points)))
    if len(pts) <= 2:
        return [list(p) for p in pts], 0.0, 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return [list(p) for p in hull], 0.0, 0.0
    area = 0.0
    per = 0.0
    for i in range(len(hull)):
        j = (i + 1) % len(hull)
        area += hull[i][0] * hull[j][1] - hull[j][0] * hull[i][1]
        per += math.hypot(hull[j][0] - hull[i][0], hull[j][1] - hull[i][1])
    return [list(p) for p in hull], abs(area) / 2.0, per


# ── 孔洞 / 圆拟合 ──────────────────────────────────────────────────────
def _holes_of(m_cc, bbox, min_px=4):
    """silhouette 内的孔洞（bbox 内**不接触 bbox 边界**的背景连通域）。

    返回 (holes, filled)：holes 每项含 mask（bbox 内局部 bool，调用方用完要 pop 掉）。
    """
    m_full = np.asarray(m_cc, dtype=bool)
    if not bbox:
        return [], m_full.copy()
    x, y, w, h = [int(v) for v in bbox]
    sub = m_full[y:y + h, x:x + w]
    # 先把 bbox 外的 cc 补成 True，避免边界判定把外部背景当孔
    bg = ~sub
    if not bool(bg.any()):
        return [], m_full.copy()
    labels, sizes = _label_cc(bg)
    border = np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])
    border_set = set(int(v) for v in np.unique(border).tolist())
    holes = []
    filled = sub.copy()
    for root, sz in sorted(sizes.items(), key=lambda kv: -kv[1]):
        if int(sz) < int(min_px) or int(root) in border_set:
            continue
        hm = labels == int(root)
        filled |= hm
        hys, hxs = np.nonzero(hm)
        if len(hys) == 0:
            continue
        hb = [int(hxs.min()), int(hys.min()), int(hxs.max() - hxs.min() + 1), int(hys.max() - hys.min() + 1)]
        holes.append({"area_px": int(sz),
                      "bbox_px": [int(x + hb[0]), int(y + hb[1]), int(hb[2]), int(hb[3])],
                      "center_px": _rnd([float(x + hxs.mean()), float(y + hys.mean())], 2),
                      "mask": hm})
    # 回**全图坐标**的 filled（bbox 内补洞）；holes 的 mask 仍是 bbox 局部（调用方自己加偏移）
    full = m_full.copy()
    full[y:y + h, x:x + w] = filled
    return holes, full


def _kasa_circle(ys, xs):
    """Kasa 最小二乘圆 → {center_px, radius_px, diameter_px, residual_px, n_px}；点太少返回 None。"""
    if xs is None or len(xs) < 5:
        return None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    A = np.stack([2.0 * x, 2.0 * y, np.ones_like(x)], axis=1)
    b = x * x + y * y
    try:
        sol, _res, _rank, _sv = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None
    cx, cy, c = float(sol[0]), float(sol[1]), float(sol[2])
    r2 = c + cx * cx + cy * cy
    if r2 <= 0:
        return None
    r = math.sqrt(r2)
    d = np.hypot(x - cx, y - cy)
    resid = float(np.sqrt(np.mean((d - r) ** 2)))
    return {"center_px": [cx, cy], "radius_px": r, "diameter_px": 2.0 * r,
            "residual_px": resid, "n_px": int(len(x))}


def _circle_from_hole(hm, x0, y0):
    """孔洞 → 圆：**边界环**做 Kasa（残差<2px 才判圆），直径用**面积等效**（离散圆的偏差最小）。

    为什么不在整块孔像素上做 Kasa：代数拟合对"填实圆盘"的最优解是 r/√2（实测 79px 孔会被量成
    56.5px）—— 这是 Kasa 的固有性质，不是数值问题。边界环才代表圆周。
    """
    hm = np.asarray(hm, dtype=bool)
    area = int(hm.sum())
    if area < 5:
        return None
    ring = hm & ~_erode1(hm)
    ry, rx = np.nonzero(ring)
    if len(rx) < 6:
        ry, rx = np.nonzero(hm)
    fit = _kasa_circle(ry, rx)
    if fit is None:
        return None
    r_eq = math.sqrt(float(area) / math.pi)
    return {"center_px": [float(x0) + float(fit["center_px"][0]),
                          float(y0) + float(fit["center_px"][1])],
            "radius_px": float(r_eq), "diameter_px": 2.0 * float(r_eq),
            "kasa_radius_px": float(fit["radius_px"]),
            "residual_px": float(fit["residual_px"]),
            "n_edge_px": int(len(rx)), "n_px": area,
            "is_circle": bool(float(fit["residual_px"]) < 2.0 and int(len(rx)) >= 6)}


# ── 对称性 ──────────────────────────────────────────────────────────────
def _symmetry(m_filled, bbox):
    """镜像对称：候选轴 vertical / horizontal，score=镜像 IoU，mirror_err_px=逐行中心偏移均值。"""
    if not bbox:
        return {"axis": None, "score": None, "mirror_err_px": None, "candidates": []}
    x, y, w, h = [int(v) for v in bbox]
    sub = np.asarray(m_filled, dtype=bool)[y:y + h, x:x + w]
    if sub.size == 0:
        return {"axis": None, "score": None, "mirror_err_px": None, "candidates": []}
    cands = []
    for axis in ("vertical", "horizontal"):
        mir = sub[:, ::-1] if axis == "vertical" else sub[::-1, :]
        iou, _i, _u = _KIT.iou(sub, mir)  # pyright: ignore[reportOptionalMemberAccess]  # KIT is injected before any runtime op
        errs = []
        if axis == "vertical":
            for r in range(h):
                idx = np.flatnonzero(sub[r])
                if idx.size:
                    errs.append(abs(0.5 * float(idx.min() + idx.max()) - 0.5 * (w - 1)))
        else:
            for c in range(w):
                idx = np.flatnonzero(sub[:, c])
                if idx.size:
                    errs.append(abs(0.5 * float(idx.min() + idx.max()) - 0.5 * (h - 1)))
        err = float(sum(errs) / len(errs)) if errs else None
        cands.append({"axis": axis, "score": (round(float(iou), 4) if iou is not None else None),
                      "mirror_err_px": (round(err, 3) if err is not None else None)})
    if not cands:
        return {"axis": None, "score": None, "mirror_err_px": None, "candidates": []}
    best = max(cands, key=lambda r: ((r["score"] or 0.0), -(r["mirror_err_px"] or 1e9)))
    return {"axis": best["axis"], "score": best["score"], "mirror_err_px": best["mirror_err_px"],
            "at_px": (round(float(x + (w - 1) / 2.0), 2) if best["axis"] == "vertical"
                      else round(float(y + (h - 1) / 2.0), 2)),
            "candidates": cands}


# ── 直线：Sobel 梯度方向 Hough（θ 0.5° × ρ 1px）+ NMS + 内点 PCA 精修 ────
