# -*- coding: utf-8 -*-
"""Split from imgtools.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403


# ══════════════════ v2：量具升级（digest / probe / measure / calib）══════════════════
# 设计：一次 img_digest 给「可确认数字表 + 原生裁剪图册 + 可读性 + 未知/待确认/下一步」；
# img_probe 按需做亚像素量测；img_measure 把像素换算 mm 并与期望比；img_calib 多视标定。
# 纪律：**没有 known_len 就绝不产 mm**（全部 null）；每个数字都带 provenance（图 md5 / 像素点 / px_per_mm）。
# 纯 bpy + numpy：不引 PIL / scipy / cv2。

MIN_READABLE_PX = 8.0
CACHE_MAX_BYTES = 320 * 1024 * 1024
CACHE_MAX_ITEMS = 8
PALETTE = [[1.0, 0.2, 0.2], [0.2, 1.0, 0.35], [0.35, 0.55, 1.0], [1.0, 0.85, 0.1],
           [1.0, 0.2, 1.0], [0.1, 0.95, 0.95]]


def _kernel_mod():
    return __import__("sys").modules.get("dsh_rt_kernel")


def _rnd(v, n=4):
    """None 安全的四舍五入（list 递归）。"""
    try:
        if v is None:
            return None
        if isinstance(v, (list, tuple)):
            return [_rnd(x, n) for x in v]
        return round(float(v), n)
    except Exception:
        return v


def _md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _md5_cached(path):
    """文件 md5（带 mtime/size 备忘，避免 img_probe 热路径反复读大文件）。"""
    K = _kernel_mod()
    p = _win(path)
    try:
        st = os.stat(p)
        sig = (int(st.st_mtime), int(st.st_size))
    except Exception:
        return _md5_file(p)
    if K is None:
        return _md5_file(p)
    memo = getattr(K, "_dsh_img_md5", None)
    if not isinstance(memo, dict):
        memo = {}
        try:
            setattr(K, "_dsh_img_md5", memo)
        except Exception:
            return _md5_file(p)
    rec = memo.get(p)
    if rec and rec[0] == sig:
        return rec[1]
    h = _md5_file(p)
    memo[p] = (sig, h)
    if len(memo) > 64:
        memo.clear()
    return h


# ── ROI / 几何小工具 ────────────────────────────────────────────────────
def _norm_roi(roi, W, H):
    """ROI 归一化：[x,y,w,h]（或 {x,y,w,h}）→ 裁到图内；坏 ROI 返回 None。"""
    if roi is None:
        return [0, 0, int(W), int(H)]
    try:
        if isinstance(roi, dict):
            x, y, w, h = roi.get("x", 0), roi.get("y", 0), roi.get("w"), roi.get("h")
        else:
            vals = list(roi)[:4]
            if len(vals) < 4:
                return None
            x, y, w, h = vals
        x, y = int(round(float(x))), int(round(float(y)))
        w, h = int(round(float(w))), int(round(float(h)))
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(int(W), x + w), min(int(H), y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1 - x0, y1 - y0]


def _dilate(m, r=1):
    """布尔膨胀（方形结构元，纯 numpy 移位）。"""
    out = np.asarray(m, dtype=bool)
    H, W = out.shape
    cur = out
    for _ in range(max(0, int(r))):
        p = np.zeros((H + 2, W + 2), dtype=bool)
        p[1:-1, 1:-1] = cur
        nxt = np.zeros_like(cur)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                nxt |= p[dy:dy + H, dx:dx + W]
        cur = nxt
    return cur


def _erode1(m):
    """布尔腐蚀（8 邻域；图外视为背景 ⇒ 贴边像素算边界）。"""
    m = np.asarray(m, dtype=bool)
    H, W = m.shape
    p = np.zeros((H + 2, W + 2), dtype=bool)
    p[1:-1, 1:-1] = m
    out = m.copy()
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out &= p[dy:dy + H, dx:dx + W]
    return out


def _sobel(g):
    """3×3 Sobel → (gx, gy)，全尺寸（边缘用 edge 复制）。"""
    gp = np.pad(g, 1, mode="edge")
    gx = ((gp[:-2, 2:] + 2.0 * gp[1:-1, 2:] + gp[2:, 2:])
          - (gp[:-2, :-2] + 2.0 * gp[1:-1, :-2] + gp[2:, :-2]))
    gy = ((gp[2:, :-2] + 2.0 * gp[2:, 1:-1] + gp[2:, 2:])
          - (gp[:-2, :-2] + 2.0 * gp[:-2, 1:-1] + gp[:-2, 2:]))
    return gx, gy


def _label_cc(mask):
    """布尔掩膜 → (labels int32, sizes {root: px})。行-run 并查集，不做逐像素循环。"""
    m = np.asarray(mask, dtype=bool)
    H, W = m.shape
    parent = [0]
    psize = [0]

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            if psize[ra] < psize[rb]:
                ra, rb = rb, ra
            parent[rb] = ra
            psize[ra] += psize[rb]

    rows = []
    prev = []
    zer = np.zeros(W + 2, dtype=np.int8)
    for y in range(H):
        pad = zer.copy()
        pad[1:W + 1] = m[y]
        d = np.diff(pad)
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1) - 1
        cur = []
        pi = 0
        for s, e in zip(starts.tolist(), ends.tolist()):
            lab = len(parent)
            parent.append(lab)
            psize.append(e - s + 1)
            while pi < len(prev) and prev[pi][1] < s:
                pi += 1
            j = pi
            while j < len(prev) and prev[j][0] <= e:
                union(lab, prev[j][2])
                j += 1
            cur.append((s, e, lab))
        rows.append(cur)
        prev = cur
    labels = np.zeros((H, W), dtype=np.int32)
    for y in range(H):
        for (s, e, lab) in rows[y]:
            labels[y, s:e + 1] = lab
    sizes = {}
    if labels.size:
        uniq = np.unique(labels)
        remap = np.zeros(len(parent), dtype=np.int32)
        for lab in uniq.tolist():
            remap[int(lab)] = find(int(lab))
        labels = remap[labels]
        counts = np.bincount(labels.ravel())
        sizes = {int(i): int(c) for i, c in enumerate(counts.tolist()) if int(c) > 0 and int(i) != 0}
    return labels, sizes


def _largest_cc_mask(m):
    """→ (最大连通域 bool, 最大域占前景比例, 信息)。"""
    labels, sizes = _label_cc(m)
    total = int(m.sum())
    if not sizes or total <= 0:
        return np.asarray(m, dtype=bool), 0.0, {"px": 0, "n_components": 0}
    root = max(sizes, key=lambda k: sizes[k])
    cc = labels == root
    return cc, float(sizes[root]) / float(total), {"px": int(sizes[root]), "n_components": int(len(sizes)),
                                                   "label": int(root)}


def _mask_v2(arr, invert=False, threshold=None):
    """v2 掩膜：alpha 优先；否则**边框 Otsu**（用边框亮度定背景侧，避免亮底/暗底二选一错）。

    返回 (mask, gray, source, confidence, info)。
    """
    H, W = arr.shape[0], arr.shape[1]
    a = arr[:, :, 3]
    g = _gray(arr)
    amin, amax = float(a.min()), float(a.max())
    info = {}
    if amin < 0.999 and amax > 0.001:
        m = a > 0.5
        source = "alpha"
        conf = float(np.clip(2.0 * float(np.abs(a - 0.5).mean()), 0.0, 1.0))
        info["alpha_range"] = [round(amin, 4), round(amax, 4)]
    else:
        thr = float(threshold) if threshold is not None else _otsu(g)
        border = np.concatenate([g[0, :], g[-1, :], g[:, 0], g[:, -1]])
        bg = float(np.median(border))
        m = (g < thr) if bg >= thr else (g > thr)
        source = "border_otsu"
        lo = float(g[m].mean()) if bool(m.any()) else 0.0
        hi = float(g[~m].mean()) if bool((~m).any()) else 1.0
        conf = float(np.clip(abs(hi - lo) / 0.25, 0.0, 1.0))
        info["threshold"] = round(thr, 4)
        info["border_median"] = round(bg, 4)
    if invert:
        m = ~m
        source += "+invert"
    fg = float(m.mean())
    # 只在 Otsu 路径允许自动翻转（alpha 是可信输入，小目标不会被"翻"掉）
    if source.startswith("border_otsu") and (fg < 0.002 or fg > 0.998):
        inv = ~m
        fi = float(inv.mean())
        if 0.002 < fi < 0.998:
            m = inv
            source += "+auto_flip"
    return m, g, source, conf, info


def _edge_strength_p50(g, m):
    """轮廓处梯度幅值的中位数（掩膜好坏的自检量）。"""
    gx, gy = _sobel(g)
    mag = np.hypot(gx, gy)
    b = np.asarray(m, dtype=bool) & ~_erode1(m)
    vals = mag[b]
    if vals.size == 0:
        vals = mag[np.asarray(m, dtype=bool)]
    if vals.size == 0:
        return 0.0
    return float(np.percentile(vals, 50))


def _principal_axis(m):
    """图像矩主轴 → (cx, cy, deg∈[0,180))。"""
    ys, xs = np.nonzero(m)
    if len(xs) < 3:
        return None, None, None
    xs = xs.astype(np.float64)
    ys = ys.astype(np.float64)
    cx, cy = xs.mean(), ys.mean()
    vxx = float(((xs - cx) ** 2).mean())
    vyy = float(((ys - cy) ** 2).mean())
    vxy = float(((xs - cx) * (ys - cy)).mean())
    ang = 0.5 * math.atan2(2.0 * vxy, (vxx - vyy) + 1e-12)
    deg = math.degrees(ang) % 180.0
    return float(cx), float(cy), float(deg)
