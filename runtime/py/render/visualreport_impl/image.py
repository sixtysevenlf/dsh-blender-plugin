# -*- coding: utf-8 -*-
"""Split from visualreport.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

# ────────────────────────────── 读写图 / 掩膜 ──────────────────────────────
def _srgb_from_linear(rgb):
    rgb = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(np.maximum(rgb, 0.0), 1 / 2.4) - 0.055)


def _load_rgb_alpha(path):
    """读图 → (srgb[h,w,3], alpha[h,w])；行序上→下，颜色口径与 qc.load 一致。"""
    p = _norm_path(path)
    if not os.path.isfile(p):
        raise ValueError("图不存在：%s" % p)
    try:
        img = bpy.data.images.load(p, check_existing=False)
    except Exception:
        st = getattr(_K, "stage", None)
        staged = None
        if callable(st):
            try:
                staged = st(path)
            except Exception:
                staged = None
        if not (isinstance(staged, dict) and staged.get("ok")):
            raise ValueError("读图失败：%s（Blender 支持 PNG/JPEG/WebP/BMP/TGA/TIFF/EXR）" % p)
        p = _norm_path(staged.get("staged") or p)
        img = bpy.data.images.load(p, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        if w <= 0 or h <= 0:
            raise ValueError("图尺寸非法（0×0）：%s" % p)
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
    a = buf.reshape(h, w, 4)[::-1].copy()
    return _srgb_from_linear(a[:, :, :3]), np.clip(a[:, :, 3], 0.0, 1.0)


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


def _bg_from_border(srgb):
    edge = np.concatenate([srgb[0, :, :], srgb[-1, :, :], srgb[:, 0, :], srgb[:, -1, :]], axis=0)
    return np.median(edge, axis=0).astype(np.float32)


def _otsu(vals):
    hist, _edges = np.histogram(np.clip(vals, 0.0, 1.0), bins=256, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    total = float(hist.sum())
    if total <= 0:
        return 0.10
    p = hist / total
    omega = np.cumsum(p)
    idx = np.arange(256, dtype=np.float64)
    mu = np.cumsum(p * idx)
    mu_t = float(mu[-1])
    sigma = (mu_t * omega - mu) ** 2 / np.maximum(omega * (1.0 - omega), 1e-12)
    k = int(np.nanargmax(sigma))
    return float((k + 0.5) / 256.0)


def _mask_auto_own(srgb, alpha=None):
    """numpy 兜底掩膜：可信 alpha 优先；否则边框背景色 + Otsu（与 qc.mask_auto 同思路）。"""
    h, w = srgb.shape[:2]
    if alpha is not None and getattr(alpha, "shape", None) == (h, w):
        a = np.asarray(alpha, dtype=np.float32)
        frac_mid = float(np.mean((a > 0.02) & (a < 0.98)))
        frac_on = float(np.mean(a > 0.5))
        if 0.0005 < frac_mid < 0.9 and 0.002 < frac_on < 0.998:
            return _fill_rc(a > 0.5), {"source": "alpha", "frac_mid": round(frac_mid, 5),
                                       "frac_on": round(frac_on, 5)}
    bg = _bg_from_border(srgb)
    dist = np.abs(srgb - bg.reshape(1, 1, 3)).max(axis=2)
    thr = float(min(max(_otsu(dist), 0.03), 0.45))
    m = dist > thr
    return _fill_rc(m), {"source": "otsu", "bg": [round(float(x), 4) for x in bg],
                         "thr": round(thr, 4), "fg_frac": round(float(m.mean()), 4)}


def _resize_mask(m, h, w):
    mh, mw = m.shape
    yi = np.clip((np.arange(h) * (mh / float(h))).astype(np.int32), 0, mh - 1)
    xi = np.clip((np.arange(w) * (mw / float(w))).astype(np.int32), 0, mw - 1)
    return m[yi][:, xi]


def _resize_rgb(arr, h, w):
    ah, aw = arr.shape[:2]
    yi = np.clip((np.arange(h) * (ah / float(h))).astype(np.int32), 0, ah - 1)
    xi = np.clip((np.arange(w) * (aw / float(w))).astype(np.int32), 0, aw - 1)
    return arr[yi][:, xi]


def _dilate1(m):
    p = np.pad(m, 1)
    out = m.copy()
    h, w = m.shape
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            out |= p[dy:dy + h, dx:dx + w]
    return out


def _erode1(m):
    return ~_dilate1(~m)


def _boundary_approx(a, b, max_k=8):
    """边界平均偏差（像素）的膨胀近似（没有 qc 时的兜底；粒度 1px，上限 max_k）。"""
    def edge(m):
        return m & ~_erode1(m)

    def one(src_edge, dst_mask):
        if not bool(src_edge.any()):
            return None
        cur = dst_mask.copy()
        rem = src_edge.copy()
        total = int(rem.sum())
        acc = 0.0
        covered = 0
        for k in range(1, max_k + 1):
            cur = _dilate1(cur)
            hit = rem & cur
            n = int(hit.sum())
            if n:
                acc += k * n
                covered += n
                rem &= ~cur
            if covered >= total:
                break
        acc += max_k * int(rem.sum())
        return acc / float(max(1, total))

    vals = [x for x in (one(edge(a), b), one(edge(b), a)) if x is not None]
    if not vals:
        return {"mean_px": None, "p95_px": None, "source": "none", "note": "有一侧没有边界"}
    return {"mean_px": round(float(sum(vals) / len(vals)), 3), "p95_px": None,
            "source": "dilate_approx", "max_k": max_k}


def _iou_pair(a, b):
    inter = int(np.logical_and(a, b).sum())
    union = int(np.logical_or(a, b).sum())
    return ((inter / float(union)) if union else 0.0), inter, union


def _metrics_own(rm, rn):
    iou, inter, union = _iou_pair(rm, rn)
    sa, sb = int(rm.sum()), int(rn.sum())
    missing = int(np.logical_and(rm, ~rn).sum())
    extra = int(np.logical_and(rn, ~rm).sum())
    dice = (2.0 * inter / float(sa + sb)) if (sa + sb) else 0.0
    return {"iou": round(float(iou), 4), "dice": round(float(dice), 4), "ref_px": sa, "render_px": sb,
            "inter_px": int(inter), "union_px": int(union), "missing_px": missing, "extra_px": extra,
            "missing_frac": round(missing / float(max(1, sa)), 4),
            "extra_frac": round(extra / float(max(1, sb)), 4),
            "boundary": _boundary_approx(rm, rn)}


def _components(mask):
    """二值掩膜的连通域（行游程 + 并查集；8 邻接）→ [{bbox, area_px, centroid_px}]，按面积降序。"""
    h = mask.shape[0]
    parent, runs = [], []

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    prev = []
    for y in range(h):
        row = np.asarray(mask[y], dtype=bool)
        if not row.any():
            prev = []
            continue
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        cur = []
        for x0, x1 in zip(starts.tolist(), ends.tolist()):
            idx = len(runs)
            runs.append((int(y), int(x0), int(x1)))
            parent.append(idx)
            cur.append((int(x0), int(x1), idx))
        i = j = 0
        while i < len(prev) and j < len(cur):
            px0, px1, pi = prev[i]
            cx0, cx1, ci = cur[j]
            if px0 <= cx1 and cx0 <= px1:
                union(pi, ci)
                if px1 < cx1:
                    i += 1
                elif cx1 < px1:
                    j += 1
                else:
                    i += 1
                    j += 1
            elif px1 < cx0:
                i += 1
            else:
                j += 1
        prev = cur
    groups = {}
    for idx, (y, x0, x1) in enumerate(runs):
        r = find(idx)
        g = groups.get(r)
        if g is None:
            g = groups[r] = {"x0": 10 ** 9, "y0": 10 ** 9, "x1": -1, "y1": -1, "area": 0, "sx": 0.0, "sy": 0.0}
        g["x0"] = min(g["x0"], x0)
        g["x1"] = max(g["x1"], x1 - 1)
        g["y0"] = min(g["y0"], y)
        g["y1"] = max(g["y1"], y)
        n = x1 - x0
        g["area"] += n
        g["sx"] += ((x0 + x1 - 1) / 2.0) * n
        g["sy"] += float(y) * n
    out = []
    for g in groups.values():
        area = int(g["area"])
        out.append({"bbox": [int(g["x0"]), int(g["y0"]), int(g["x1"]), int(g["y1"])],
                    "area_px": area,
                    "centroid_px": [round(g["sx"] / max(1, area), 2), round(g["sy"] / max(1, area), 2)]})
    out.sort(key=lambda c: -c["area_px"])
    return out


def _bbox_of(m):
    if not bool(m.any()):
        return None
    ys, xs = np.nonzero(m)
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


# ────────────────────────────── 存图（overlay / sheet / crop）──────────────────────────────
_PNG_SEQ = [0]


def _save_png_rgba(arr, path):
    p = _norm_path(path)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    h, w = int(arr.shape[0]), int(arr.shape[1])
    _PNG_SEQ[0] += 1
    img = bpy.data.images.new("VR_TMP_%d" % _PNG_SEQ[0], width=w, height=h, alpha=True)
    try:
        buf = np.ascontiguousarray(np.clip(arr[::-1], 0.0, 1.0).astype(np.float32))
        img.pixels.foreach_set(buf.ravel())
        img.filepath_raw = p
        img.file_format = "PNG"
        img.save()
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
    if not os.path.isfile(p):
        raise RuntimeError("PNG 写盘失败：%s" % p)
    rec = _file_info(p)
    rec["md5"] = _md5(p)
    return rec


def _rgba_from_rgb(rgb):
    h, w = rgb.shape[:2]
    arr = np.ones((h, w, 4), dtype=np.float32)
    arr[:, :, :3] = np.clip(rgb, 0.0, 1.0)
    return arr


def _make_overlay(rm, rn):
    h, w = rm.shape[:2]
    ov = np.ones((h, w, 4), dtype=np.float32)
    both = np.logical_and(rm, rn)
    miss = np.logical_and(rm, ~rn)
    extra = np.logical_and(rn, ~rm)
    ov[both, 0], ov[both, 1], ov[both, 2] = 0.85, 0.82, 0.45
    ov[miss, 0], ov[miss, 1], ov[miss, 2] = 0.92, 0.18, 0.18
    ov[extra, 0], ov[extra, 1], ov[extra, 2] = 0.18, 0.38, 0.92
    return ov


def _make_sheet(panels, gap=6):
    if not panels:
        raise ValueError("没有可拼的面板")
    H = max(int(p.shape[0]) for p in panels)
    W = sum(int(p.shape[1]) for p in panels) + gap * (len(panels) - 1)
    out = np.ones((H, W, 4), dtype=np.float32)
    x = 0
    for p in panels:
        if int(p.shape[0]) != H:
            raise ValueError("面板高度不一致：%s vs %s" % (p.shape[0], H))
        out[:, x:x + int(p.shape[1]), :] = p
        x += int(p.shape[1]) + gap
    return out


def _write_defect_crop(overlay, defect, run_dir, index):
    h, w = overlay.shape[:2]
    x0, y0, x1, y1 = defect["bbox_px"]
    bw, bh = max(1, x1 - x0 + 1), max(1, y1 - y0 + 1)
    pad = max(6, int(0.35 * max(bw, bh)))
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(w, x1 + 1 + pad), min(h, y1 + 1 + pad)
    sub = overlay[cy0:cy1, cx0:cx1].copy()
    for t in range(2):
        if sub.shape[0] > t:
            sub[t, :, :3] = 0.05
            sub[sub.shape[0] - 1 - t, :, :3] = 0.05
        if sub.shape[1] > t:
            sub[:, t, :3] = 0.05
            sub[:, sub.shape[1] - 1 - t, :3] = 0.05
    name = "defect_%02d_%s_%s.png" % (index, _safe_token(defect.get("view"), "view", 24),
                                     defect.get("kind", "x"))
    rec = _save_png_rgba(sub, os.path.join(run_dir, name))
    rec.update({"view": defect.get("view"), "kind": defect.get("kind"),
                "bbox_px": list(defect.get("bbox_px") or []), "index": index})
    return rec

