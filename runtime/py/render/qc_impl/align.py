# -*- coding: utf-8 -*-
"""Split from qc.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403


def _bbox(mask):
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def _scale_bool(mask, tw, th):
    h, w = mask.shape[:2]
    yi = (np.arange(th) * (h / float(th))).astype(np.int32).clip(0, h - 1)
    xi = (np.arange(tw) * (w / float(tw))).astype(np.int32).clip(0, w - 1)
    return mask[yi][:, xi]


def _scale_rgb(arr, tw, th):
    h, w = arr.shape[:2]
    yi = (np.arange(th) * (h / float(th))).astype(np.int32).clip(0, h - 1)
    xi = (np.arange(tw) * (w / float(tw))).astype(np.int32).clip(0, w - 1)
    return arr[yi][:, xi]


def qc_align_iou(ref_srgb, ref_mask, render_rgba, pad_frac=0.05):
    """对齐后算 IoU（移植 plush-build qc/sheet.py 的算法）：
      渲染掩膜取 **alpha**（不是阈值）；参考掩膜按 bbox 缩放到渲染目标高度；按 bbox 左上角对齐后再比。
      这是能在"参考图与渲染图分辨率/构图都不同"时仍给出可比 IoU 的关键。
    """
    alpha = render_rgba[:, :, 3] > 0.5
    rb = _bbox(alpha)
    rmb = _bbox(ref_mask)
    if rb is None or rmb is None:
        return None
    rh = max(1, rb[3] - rb[1])
    scale = rh / max(1, (rmb[3] - rmb[1]))
    tw = max(8, int(ref_mask.shape[1] * scale))
    th = max(8, int(ref_mask.shape[0] * scale))
    rm_big = _scale_bool(ref_mask, tw, th)
    rs_big = _scale_rgb(np.clip(ref_srgb, 0.0, 1.0), tw, th)
    nb = (int(rmb[0] * scale), int(rmb[1] * scale), int(rmb[2] * scale), int(rmb[3] * scale))
    pad = int(rh * float(pad_frac))
    rx0 = max(0, rb[0] - pad)
    ry0 = max(0, rb[1] - pad)
    rx1 = min(alpha.shape[1], rb[2] + pad)
    ry1 = min(alpha.shape[0], rb[3] + pad)
    rcrop = render_rgba[ry0:ry1, rx0:rx1]
    rmaps = alpha[ry0:ry1, rx0:rx1]
    ox = rx0 - nb[0]
    oy = ry0 - nb[1]
    W = max(rs_big.shape[1], rcrop.shape[1] + max(0, -ox), nb[2] + rcrop.shape[1])
    H = max(rs_big.shape[0], rcrop.shape[0] + max(0, -oy), nb[3] + rcrop.shape[0])
    y0 = max(0, oy); x0 = max(0, ox)
    mask_ref = np.zeros((H, W), dtype=bool)
    mask_ref[y0:y0 + rm_big.shape[0], x0:x0 + rm_big.shape[1]] = rm_big
    cy0 = max(0, -oy); cx0 = max(0, -ox)
    mask_ren = np.zeros((H, W), dtype=bool)
    mask_ren[cy0:cy0 + rmaps.shape[0], cx0:cx0 + rmaps.shape[1]] = rmaps
    inter = int(np.logical_and(mask_ref, mask_ren).sum())
    uni = int(np.logical_or(mask_ref, mask_ren).sum())
    canvas_ref = np.ones((H, W, 3), dtype=np.float32)
    canvas_ref[y0:y0 + rs_big.shape[0], x0:x0 + rs_big.shape[1]] = rs_big
    canvas_ren = np.ones((H, W, 3), dtype=np.float32)
    a = np.clip(rcrop[:, :, 3:4], 0.0, 1.0)
    canvas_ren[cy0:cy0 + rcrop.shape[0], cx0:cx0 + rcrop.shape[1]] = rcrop[:, :, :3] * a + (1.0 - a)
    return {"iou": (inter / uni) if uni else 0.0, "inter": inter, "union": uni,
            "ref_px": int(mask_ref.sum()), "render_px": int(mask_ren.sum()), "canvas": [H, W],
            "scale": round(scale, 4), "canvas_ref": canvas_ref, "canvas_render": canvas_ren,
            "mask_ref": mask_ref, "mask_render": mask_ren}

# ================================================================ 优化版：自适应掩膜 + 对齐搜索 + 可行动指标

def _otsu(vals, bins=64):
    v = np.asarray(vals, dtype=np.float32).ravel()
    if v.size == 0:
        return 0.0
    hi = float(v.max())
    if hi <= 1e-9:
        return 0.0
    hist, edges = np.histogram(v, bins=bins, range=(0.0, hi * 1.000001))
    total = float(hist.sum())
    if total <= 0:
        return 0.0
    w0 = np.cumsum(hist).astype(np.float64)
    w1 = total - w0
    idx = (np.arange(bins) + 0.5) * (hi / bins)
    s0 = np.cumsum(hist.astype(np.float64) * idx)
    st = s0[-1]
    mu0 = s0 / np.maximum(w0, 1e-9)
    mu1 = (st - s0) / np.maximum(w1, 1e-9)
    var = w0 * w1 * (mu0 - mu1) ** 2
    k = int(np.nanargmax(var))
    return float((edges[k] + edges[k + 1]) / 2.0)


def _bg_from_border(srgb, ring=2):
    h, w = srgb.shape[:2]
    r = max(1, int(ring))
    parts = [srgb[0:r].reshape(-1, 3), srgb[h - r:h].reshape(-1, 3),
             srgb[:, 0:r].reshape(-1, 3), srgb[:, w - r:w].reshape(-1, 3)]
    return np.median(np.vstack(parts), axis=0)


def qc_mask_auto(srgb, alpha=None, fill=True, use_alpha=None):
    """自适应前景掩膜（优化①）：
       - 有可信 alpha（有半透明过渡且不是全 0/全 1）就用 alpha（渲染图首选）；
       - 否则从**图像边框估计背景色**，按"到背景色的颜色距离"做 **Otsu** 自动阈值 ——
         不再需要每张参考图手调 sat/v 阈值（这是原实现最脆的地方）。
    """
    h, w = srgb.shape[:2]
    a = None if alpha is None else np.asarray(alpha, dtype=np.float32)
    if a is not None:
        frac_mid = float(np.mean((a > 0.02) & (a < 0.98)))
        frac_on = float(np.mean(a > 0.5))
        informative = (use_alpha if use_alpha is not None else (0.0005 < frac_mid < 0.9 and 0.002 < frac_on < 0.998))
        if informative:
            m = a > 0.5
            if fill:
                m = _fill_rc(m)
            return m, {"source": "alpha", "frac_mid": round(frac_mid, 5), "frac_on": round(frac_on, 5)}
    bg = _bg_from_border(srgb)
    dist = np.abs(srgb - bg.reshape(1, 1, 3)).max(axis=2)
    thr = _otsu(dist)
    thr = float(min(max(thr, 0.03), 0.45))
    m = dist > thr
    if fill:
        m = _fill_rc(m)
    return m, {"source": "otsu", "bg": [round(float(x), 4) for x in bg], "thr": round(thr, 4),
               "fg_frac": round(float(np.mean(m)), 4)}


def _fill_rc(m):
    m = m.copy()
    for r in range(m.shape[0]):
        ii = np.where(m[r])[0]
        if len(ii) > 1:
            m[r, int(ii.min()):int(ii.max()) + 1] = True
    for c in range(m.shape[1]):
        ii = np.where(m[:, c])[0]
        if len(ii) > 1:
            m[int(ii.min()):int(ii.max()) + 1, c] = True
    return m


def _warp_ref_mask(ref_mask, s, tx, ty, H, W):
    """把参考掩膜按 (缩放 s, 平移 tx/ty) 采样到 HxW 画布（最近邻）。"""
    ys = (np.arange(H, dtype=np.float32) - float(ty)) / max(1e-6, float(s))
    xs = (np.arange(W, dtype=np.float32) - float(tx)) / max(1e-6, float(s))
    yi = np.round(ys).astype(np.int64)
    xi = np.round(xs).astype(np.int64)
    valid_y = (yi >= 0) & (yi < ref_mask.shape[0])
    valid_x = (xi >= 0) & (xi < ref_mask.shape[1])
    out = np.zeros((H, W), dtype=bool)
    if valid_y.any() and valid_x.any():
        out[np.ix_(valid_y, valid_x)] = ref_mask[np.ix_(yi[valid_y], xi[valid_x])]
    return out


def _down(mask, work):
    h, w = mask.shape[:2]
    mx = max(h, w)
    if mx <= work:
        return mask, 1.0
    s = float(work) / float(mx)
    th = max(8, int(h * s))
    tw = max(8, int(w * s))
    return _scale_bool(mask, tw, th), s


def _iou_pair(a, b):
    # 度量收敛到共享内核（唯一实现）；空并集时 kit 给 None，这里按旧契约返回 0.0
    iou, inter, uni = _KIT.iou(a, b)
    return (0.0 if iou is None else iou), inter, uni


def _align_search(ref_mask, ren_mask, work=384, coarse=True):
    """优化②：在基础对齐附近做"尺度 + 平移"局部搜索，取 IoU 最大的一组。

    基础对齐 = 让参考的 bbox 高度与渲染一致、并把两者**质心**重合（原实现是 bbox 左上角，
    遇到裁切/构图差异会系统性偏低）。搜索在 work 画布上做：把全分辨率参考用**单次**重采样
    投到画布，避免两次插值误差。
    """
    h, w = ren_mask.shape[:2]
    mx = max(h, w)
    k_ren = (float(work) / float(mx)) if mx > work else 1.0
    H = max(8, int(h * k_ren))
    W = max(8, int(w * k_ren))
    rn = _scale_bool(ren_mask, W, H)
    br = _bbox(ref_mask)
    bn = _bbox(ren_mask)
    if br is None or bn is None:
        return None
    cx_r = (br[0] + br[2]) / 2.0
    cy_r = (br[1] + br[3]) / 2.0
    cx_n = (bn[0] + bn[2]) / 2.0
    cy_n = (bn[1] + bn[3]) / 2.0
    s_bbox = (bn[3] - bn[1]) / max(1.0, float(br[3] - br[1]))
    a0 = s_bbox * k_ren
    def place(a):
        return (cx_n * k_ren - cx_r * a, cy_n * k_ren - cy_r * a)
    best = None
    rounds = [(0.10, 0.05), (0.03, 0.02)] if coarse else [(0.06, 0.03)]
    for ds, df in rounds:
        base_a = a0 if best is None else best["a"]
        base_t = place(base_a) if best is None else (best["tx"], best["ty"])
        for fs in (1.0 - ds, 1.0, 1.0 + ds):
            a = base_a * fs
            tx0, ty0 = place(a)
            for fx in (-df, 0.0, df):
                for fy in (-df, 0.0, df):
                    tx = tx0 + fx * W
                    ty = ty0 + fy * H
                    cand = _warp_to_canvas(ref_mask, a, tx, ty, H, W)
                    iou0, inter, uni = _iou_pair(cand, rn)
                    out_px = _outside_count(ref_mask, a, tx, ty, H, W)
                    uni_eff = uni + out_px
                    iou = (inter / uni_eff) if uni_eff else 0.0
                    if best is None or iou > best["iou"]:
                        best = {"iou": iou, "a": a, "tx": tx, "ty": ty, "inter": inter, "union": uni_eff,
                                "outside_px": out_px, "mask": cand}
    # 用"扩展画布"精确重建最优解：把参考可能超出的部分也放进来，保证并集完整
    a = best["a"]
    tx = best["tx"]
    ty = best["ty"]
    x_off = max(0, int(np.floor(-tx)))
    y_off = max(0, int(np.floor(-ty)))
    W2 = max(W + x_off, int(np.ceil(ref_mask.shape[1] * a + tx)) + 1)
    H2 = max(H + y_off, int(np.ceil(ref_mask.shape[0] * a + ty)) + 1)
    ref2 = np.zeros((H2, W2), dtype=bool)
    ref2[y_off:y_off + H, x_off:x_off + W] = best["mask"]
    ren2 = np.zeros((H2, W2), dtype=bool)
    ren2[y_off:y_off + H, x_off:x_off + W] = rn
    iou_eff, inter_eff, uni_eff = _iou_pair(ref2, ren2)
    return {"iou": iou_eff, "scale": a / k_ren, "canvas": [H2, W2], "searched": True,
            "base_scale": round(s_bbox, 5), "shift": [round(tx, 2), round(ty, 2)],
            "inter": inter_eff, "union": uni_eff, "outside_px": int(best.get("outside_px", 0)),
            "mask_ref": ref2, "mask_render": ren2}


def _rotate_mask(m, deg):
    """布尔掩膜绕中心旋转（最近邻）。v0.8.10：外部反馈里"同一轮廓被取向差压到 0.62 假低分"。"""
    if abs(float(deg)) < 1e-6:
        return m
    h, w = m.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    a = math.radians(float(deg))
    ca, sa = math.cos(a), math.sin(a)
    ys = (yy - cy) * ca + (xx - cx) * sa + cy
    xs = -(yy - cy) * sa + (xx - cx) * ca + cx
    yi = np.clip(np.round(ys).astype(np.int32), 0, h - 1)
    xi = np.clip(np.round(xs).astype(np.int32), 0, w - 1)
    return m[yi, xi]                       # 注意：2D 花式索引必须成对写，m[yi][:, xi] 会变成三维


def qc_align_rotate(ref_mask, ren_mask, angles=None, work=384, coarse=True):
    """对齐搜索 + 角度细化：先 scale/shift，再在 ±8° 内粗扫 + ±1° 细扫，返回最优角度与 IoU。"""
    base = _align_search(ref_mask, ren_mask, work=work, coarse=coarse)
    if base is None:
        return None
    mref = base["mask_ref"]
    mren = base["mask_render"]
    best = {"deg": 0.0, "iou": float(base["iou"]), "mask": mren, "base": base, "searched": True}
    coarse_angles = angles if angles is not None else [-8, -6, -4, -2, -1, 1, 2, 4, 6, 8]
    for deg in coarse_angles:
        iou, _i, _u = _iou_pair(mref, _rotate_mask(mren, deg))
        if iou > best["iou"]:
            best = {"deg": float(deg), "iou": float(iou), "mask": _rotate_mask(mren, deg), "base": base, "searched": True}
    if abs(best["deg"]) >= 1.0:                      # 细扫一圈（±1°，步长 0.5°）
        for d2 in [best["deg"] + x * 0.5 for x in (-2, -1, 1, 2)]:
            iou, _i, _u = _iou_pair(mref, _rotate_mask(mren, d2))
            if iou > best["iou"]:
                best = {"deg": float(d2), "iou": float(iou), "mask": _rotate_mask(mren, d2), "base": base, "searched": True}
    best["iou_gain_vs_no_rot"] = round(best["iou"] - float(base["iou"]), 4)
    best["mask_ref"] = mref
    return best


def _outside_count(ref_mask, s, tx, ty, H, W):
    """参考投影后落在画布外的前景像素数 —— 它们**进不了交集、但必须进并集**，
    否则 IoU 会被系统性抬高（对自己有利的偏差，必须显式计入）。"""
    ys, xs = np.nonzero(ref_mask)
    if ys.size == 0:
        return 0
    ox = np.round(xs.astype(np.float32) * float(s) + float(tx))
    oy = np.round(ys.astype(np.float32) * float(s) + float(ty))
    return int(np.count_nonzero((ox < 0) | (ox >= W) | (oy < 0) | (oy >= H)))


def _warp_to_canvas(ref_mask, s, tx, ty, H, W):
    """把参考掩膜（全分辨率）按 s 缩放、平移到 HxW 画布（最近邻，单次重采样）。"""
    ys = (np.arange(H, dtype=np.float32) - float(ty)) / max(1e-6, float(s))
    xs = (np.arange(W, dtype=np.float32) - float(tx)) / max(1e-6, float(s))
    yi = np.round(ys).astype(np.int64)
    xi = np.round(xs).astype(np.int64)
    vy = (yi >= 0) & (yi < ref_mask.shape[0])
    vx = (xi >= 0) & (xi < ref_mask.shape[1])
    out = np.zeros((H, W), dtype=bool)
    if vy.any() and vx.any():
        out[np.ix_(vy, vx)] = ref_mask[np.ix_(yi[vy], xi[vx])]
    return out


def _dice(a, b):
    inter = float(np.logical_and(a, b).sum())
    sa = float(a.sum())
    sb = float(b.sum())
    return (2.0 * inter / (sa + sb)) if (sa + sb) > 0 else 0.0


def _boundary_dist(a, b, max_k=8):
    """近似边界距离：把各自边界反复膨胀 k 次，统计仍不相交的边界像素比例 → 平均 ~k 像素。"""
    def edge(m):
        e = m & ~_erode(m)
        return e
    ea = edge(a)
    eb = edge(b)
    if not ea.any() or not eb.any():
        return {"mean_px": None, "p95_px": None, "note": "有一侧没有边界"}
    da = None
    db = None
    cur = eb.copy()
    db = np.full(a.shape, max_k + 1, dtype=np.int16)
    for k in range(1, max_k + 1):
        cur = _dilate(cur)
        db[(ea) & (cur) & (db > k)] = k
    cur = ea.copy()
    da = np.full(a.shape, max_k + 1, dtype=np.int16)
    for k in range(1, max_k + 1):
        cur = _dilate(cur)
        da[(eb) & (cur) & (da > k)] = k
    vals = np.concatenate([db[ea].ravel(), da[eb].ravel()]).astype(np.float32)
    vals = np.clip(vals, 0, max_k + 1)
    return {"mean_px": round(float(vals.mean()), 2), "p95_px": round(float(np.percentile(vals, 95)), 2), "max_k": max_k}


def _dilate(m):
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def _erode(m):
    out = m.copy()
    out[1:, :] &= m[:-1, :]
    out[:-1, :] &= m[1:, :]
    out[:, 1:] &= m[:, :-1]
    out[:, :-1] &= m[:, 1:]
    return out


def qc_metrics(ref_mask, ren_mask, iou=None, inter=None, union=None):
    """可行动指标（优化③）：缺面积（参考有渲染无）、多面积（渲染有多余）、Dice、边界距离。"""
    h = min(ref_mask.shape[0], ren_mask.shape[0])
    w = min(ref_mask.shape[1], ren_mask.shape[1])
    a = ref_mask[:h, :w]
    b = ren_mask[:h, :w]
    if iou is None:
        iou, inter, union = _iou_pair(a, b)
    missing = int(np.logical_and(a, ~b).sum())
    extra = int(np.logical_and(b, ~a).sum())
    sa = int(a.sum())
    sb = int(b.sum())
    return {"iou": round(float(iou), 4), "dice": round(float(_dice(a, b)), 4),
            "ref_px": sa, "render_px": sb, "inter_px": int(inter), "union_px": int(union),
            "missing_px": missing, "extra_px": extra,
            "missing_frac": round(missing / max(1, sa), 4), "extra_frac": round(extra / max(1, sb), 4),
            "boundary": _boundary_dist(a, b)}

