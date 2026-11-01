# -*- coding: utf-8 -*-
"""Split from imgtools.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .analysis import _dilate, _rnd, _sobel
# -*- coding: utf-8 -*-
"""Split from imgtools.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403


def _hough_points(gray, m_region, point_cap):
    """梯度阈值 + 区域掩膜 + 采样；返回点集（无有效点时 None）。"""
    H, W = gray.shape
    gx, gy = _sobel(gray)
    mag = np.hypot(gx, gy)
    region = _dilate(np.asarray(m_region, dtype=bool), 2)
    vals = mag[region]
    if vals.size == 0:
        return None
    # 阈值取 p85 的**一半**：合成/平色图的边缘梯度是量化平台（p85 恰好落在平台上），
    # 用 mag > p85 会把所有直边都排除掉（实测 45° 杆 n_edge 只剩 8）
    thr = max(0.02, 0.5 * float(np.percentile(vals, 85)))
    ys, xs = np.nonzero((mag > thr) & region)
    n = int(len(xs))
    if n < 10:
        return None
    if n > point_cap:
        pick = np.linspace(0, n - 1, point_cap).astype(np.int64)
        xs = xs[pick]
        ys = ys[pick]
        n = point_cap
    return {"W": W, "H": H, "xf": xs.astype(np.float32), "yf": ys.astype(np.float32), "n": n}


def _hough_accumulate(pts, max_lines):
    """(θ, ρ) 累加器向量化投票 + 非极大值抑制取峰；返回 {peaks, off}（无票时 None）。"""
    W, H, n = pts["W"], pts["H"], pts["n"]
    xf, yf = pts["xf"], pts["yf"]
    n_theta = 360
    theta = np.deg2rad(np.arange(n_theta, dtype=np.float64) * 0.5)
    ct = np.cos(theta).astype(np.float32)
    st = np.sin(theta).astype(np.float32)
    diag = float(math.hypot(W, H))
    n_rho = int(2.0 * diag) + 5
    off = int(diag) + 2
    hist = np.zeros(n_theta * n_rho, dtype=np.float64)
    ti = np.arange(n_theta, dtype=np.int32)
    chunk = 8192
    for s in range(0, n, chunk):
        xc = xf[s:s + chunk][:, None]
        yc = yf[s:s + chunk][:, None]
        r = xc * ct[None, :] + yc * st[None, :]
        ri = np.rint(r).astype(np.int32) + off
        np.clip(ri, 0, n_rho - 1, out=ri)
        flat = (ti[None, :] * n_rho + ri).ravel()
        hist += np.bincount(flat, minlength=n_theta * n_rho)
    h2 = hist.reshape(n_theta, n_rho)
    hflat = h2.ravel()
    topk = min(800, int(hflat.size))
    part = np.argpartition(hflat, -topk)[-topk:]
    order = part[np.argsort(-hflat[part])]
    vmax = float(hflat[order[0]]) if order.size else 0.0
    if vmax <= 0:
        return None
    min_votes = max(8.0, 0.04 * vmax)
    peaks = []
    for fi in order.tolist():
        ti_, ri_ = divmod(int(fi), n_rho)
        v = float(h2[ti_, ri_])
        if v < min_votes:
            break
        if any(abs(ti_ - pj[0]) <= 2 and abs(ri_ - pj[1]) <= 2 for pj in peaks):
            continue
        peaks.append((ti_, ri_, v))
        if len(peaks) >= max(24, max_lines * 4):
            break
    return {"peaks": peaks, "off": off}


def _hough_fit(pts, off, peak, min_span):
    """单峰 → 直线记录（中心线 + 内点重拟合 + 端点/残差/置信度）；不够长/拟合失败返回 None。"""
    ti_, ri_, votes = peak
    xf, yf = pts["xf"], pts["yf"]
    th0 = math.radians(ti_ * 0.5)
    rho0 = float(ri_ - off)
    c0, s0 = math.cos(th0), math.sin(th0)
    dist = np.abs(xf * c0 + yf * s0 - rho0)
    sel = dist <= 2.0
    if int(sel.sum()) < 8:
        return None
    px = xf[sel].astype(np.float64)
    py = yf[sel].astype(np.float64)
    mx, my = float(px.mean()), float(py.mean())
    cov = np.cov(np.stack([px - mx, py - my]))
    try:
        evals, evecs = np.linalg.eigh(cov)
    except Exception:
        return None
    v = evecs[:, int(np.argmax(evals))]
    direction = math.atan2(float(v[1]), float(v[0]))
    # 法向规范化：n 的角度落到 [0,π)；越过 π 时 n 反转 ⇒ rho 取负（**不能**只把 rho 翻正，
    # 那会把中心线两侧的两条平行边塌成一条）
    nang = (direction + math.pi / 2.0) % (2.0 * math.pi)
    rho = mx * math.cos(nang) + my * math.sin(nang)
    if nang >= math.pi:
        nang -= math.pi
        rho = -rho
    thn = nang
    c1, s1 = math.cos(thn), math.sin(thn)
    d2 = np.abs(px * c1 + py * s1 - rho)
    inl = d2 <= 2.5
    if int(inl.sum()) < 8:
        return None
    # 用内点重新拟合（一次迭代）
    pinx = px[inl]
    piny = py[inl]
    mx2, my2 = float(pinx.mean()), float(piny.mean())
    cov2 = np.cov(np.stack([pinx - mx2, piny - my2]))
    try:
        ev2, evec2 = np.linalg.eigh(cov2)
    except Exception:
        return None
    v2 = evec2[:, int(np.argmax(ev2))]
    dir2 = math.atan2(float(v2[1]), float(v2[0]))
    nang2 = (dir2 + math.pi / 2.0) % (2.0 * math.pi)
    rho2 = mx2 * math.cos(nang2) + my2 * math.sin(nang2)
    if nang2 >= math.pi:
        nang2 -= math.pi
        rho2 = -rho2
    thn2 = nang2
    resid = float(np.abs(pinx * math.cos(thn2) + piny * math.sin(thn2) - rho2).mean())
    proj = (pinx - mx2) * math.cos(dir2) + (piny - my2) * math.sin(dir2)
    lo = float(np.percentile(proj, 5))
    hi = float(np.percentile(proj, 95))
    span = hi - lo
    if span < float(min_span):
        return None
    e1 = [mx2 + lo * math.cos(dir2), my2 + lo * math.sin(dir2)]
    e2 = [mx2 + hi * math.cos(dir2), my2 + hi * math.sin(dir2)]
    density = float(inl.sum()) / max(1.0, span)
    conf = float(np.clip(density, 0.0, 1.0) * math.exp(-resid / 2.0))
    theta_deg = math.degrees(thn2) % 180.0
    return {"theta_deg": round(theta_deg, 2), "rho_px": round(rho2, 2),
            "direction_deg": round((theta_deg - 90.0) % 180.0, 2),
            "endpoints_px": [_rnd(e1, 2), _rnd(e2, 2)],
            "inliers": int(inl.sum()), "span_px": round(span, 2),
            "residual_px": round(resid, 3), "confidence": round(conf, 3),
            "_votes": votes}


def _hough_dedupe(out, max_lines):
    """近重复合并（同向、同 rho、长度相近 → 留 span 大的），并去掉内部票数字段。"""
    out.sort(key=lambda r: -r["span_px"])
    kept = []
    for r in out:
        dup = False
        for k in kept:
            dt = abs(r["theta_deg"] - k["theta_deg"])
            dt = min(dt, 180.0 - dt)
            if dt <= 1.5 and abs(r["rho_px"] - k["rho_px"]) <= 3.0:
                dup = True
                break
        if not dup:
            kept.append(r)
        if len(kept) >= int(max_lines):
            break
    for r in kept:
        r.pop("_votes", None)
    return kept


def _hough_lines(gray, m_region, max_lines=12, point_cap=60000, min_span=8.0):
    """返回 [{theta_deg,rho_px,endpoints_px,inliers,span_px,residual_px,confidence,direction_deg}]。

    θ 是**法向角**（x·cosθ + y·sinθ = ρ）；direction_deg = (θ-90) mod 180 是线方向，便于人读。
    """
    pts = _hough_points(gray, m_region, point_cap)
    if pts is None:
        return []
    acc = _hough_accumulate(pts, max_lines)
    if acc is None:
        return []
    out = []
    for peak in acc["peaks"]:
        rec = _hough_fit(pts, acc["off"], peak, min_span)
        if rec is not None:
            out.append(rec)
    return _hough_dedupe(out, max_lines)


# ── 标定：known_len 解析 + 逐视最小二乘 ─────────────────────────────────
def _parse_known_lengths(known):
    """known_len → [{px, mm, label, mid}]；兼容 dict / list[dict] / px / p1_px+p2_px。"""
    if known is None:
        return []
    items = known if isinstance(known, (list, tuple)) else [known]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mm = None
        for key in ("mm", "length_mm", "value_mm", "known_mm", "mm_value"):
            if it.get(key) is not None:
                try:
                    mm = float(it[key])
                except Exception:
                    mm = None
                break
        px = None
        mid = None
        for key in ("px", "px_len", "length_px"):
            if it.get(key) is not None:
                try:
                    px = float(it[key])
                except Exception:
                    px = None
                break
        if px is None:
            p1 = it.get("p1_px") or it.get("p1")
            p2 = it.get("p2_px") or it.get("p2")
            if p1 is None and isinstance(it.get("points"), (list, tuple)) and len(it["points"]) >= 2:
                p1, p2 = it["points"][0], it["points"][1]
            if p1 is not None and p2 is not None:
                try:
                    ax, ay = float(p1[0]), float(p1[1])
                    bx, by = float(p2[0]), float(p2[1])
                    px = math.hypot(bx - ax, by - ay)
                    mid = [(ax + bx) / 2.0, (ay + by) / 2.0]
                except Exception:
                    px = None
        if mm is None or px is None or px <= 0 or mm <= 0:
            continue
        out.append({"px": float(px), "mm": float(mm),
                    "label": it.get("label") or it.get("key"),
                    "mid": mid})
    return out


def _fit_scale(items):
    """逐组已知长度 → px_per_mm 最小二乘（过原点）+ 残差。"""
    if not items:
        return None
    num = sum(float(i["px"]) * float(i["mm"]) for i in items)
    den = sum(float(i["mm"]) ** 2 for i in items)
    if den <= 0 or num <= 0:
        return None
    ppm = num / den
    resid = [float(i["px"]) - ppm * float(i["mm"]) for i in items]
    mean_px = sum(float(i["px"]) for i in items) / float(len(items))
    if len(items) > 1:
        rms = math.sqrt(sum(r * r for r in resid) / float(len(resid)))
        resid_pct = 100.0 * rms / mean_px if mean_px > 0 else None
        rel = rms / mean_px if mean_px > 0 else 1.0
    else:
        rms = None
        resid_pct = None
        rel = 1.0 / mean_px if mean_px > 0 else 1.0
    return {"px_per_mm": float(ppm), "n_points": int(len(items)),
            "resid_px": (round(rms, 4) if rms is not None else None),
            "resid_pct": (round(resid_pct, 3) if resid_pct is not None else None),
            "err_mm_at_300px": round((300.0 / ppm) * rel, 4),
            "residuals_px": [round(r, 4) for r in resid],
            "mean_px": round(mean_px, 3)}


def _digest_scale(items):
    if not items:
        return {"px_per_mm": None, "source": "none", "err_mm_at_300px": None, "n_points": 0}
    fit = _fit_scale(items)
    if not fit:
        return {"px_per_mm": None, "source": "none", "err_mm_at_300px": None, "n_points": 0,
                "warning": "known_len 无法解出 px_per_mm"}
    out = dict(fit)
    out["source"] = "known_len"
    out["known"] = [{"px": round(i["px"], 3), "mm": round(i["mm"], 3), "label": i.get("label")} for i in items]
    return out


def _spec_px(spec):
    """共享尺寸的像素值解析：数字 / {"px"} / {"p1_px","p2_px"} / 两点列表。"""
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, dict):
        for key in ("px", "px_len", "length_px", "value_px"):
            if spec.get(key) is not None:
                try:
                    return float(spec[key])
                except Exception:
                    return None
        p1 = spec.get("p1_px") or spec.get("p1")
        p2 = spec.get("p2_px") or spec.get("p2")
        if p1 is not None and p2 is not None and len(p1) >= 2 and len(p2) >= 2:
            try:
                return math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))
            except Exception:
                return None
        pts = spec.get("points")
        if isinstance(pts, (list, tuple)) and len(pts) >= 2:
            try:
                return math.hypot(float(pts[1][0]) - float(pts[0][0]), float(pts[1][1]) - float(pts[0][1]))
            except Exception:
                return None
    if isinstance(spec, (list, tuple)) and len(spec) >= 2:
        try:
            if len(spec) == 2 and all(isinstance(x, (list, tuple)) for x in spec):
                return math.hypot(float(spec[1][0]) - float(spec[0][0]), float(spec[1][1]) - float(spec[0][1]))
            if len(spec) >= 4:
                return math.hypot(float(spec[2]) - float(spec[0]), float(spec[3]) - float(spec[1]))
        except Exception:
            return None
    return None


