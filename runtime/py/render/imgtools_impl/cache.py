# -*- coding: utf-8 -*-
"""Split from imgtools.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .common import _bbox, _load_ex, _win
from .analysis import CACHE_MAX_BYTES, CACHE_MAX_ITEMS, _kernel_mod, _largest_cc_mask, _mask_v2, _md5_cached
from .contour import _holes_of

# ── 缓存（probe/measure 热路径；K 上按 md5 存 mask/gray）────────────────
def _cache_store():
    K = _kernel_mod()
    if K is None:
        return None
    c = getattr(K, "_dsh_img_cache", None)
    if not isinstance(c, dict):
        c = {"items": {}, "order": [], "bytes": 0}
        try:
            setattr(K, "_dsh_img_cache", c)
        except Exception:
            return None
    return c


def _cache_key(md5, shape):
    return "%s@%dx%d" % (str(md5), int(shape[0]), int(shape[1]))


def _cache_get(md5, shape=None):
    c = _cache_store()
    if c is None:
        return None
    items = c.get("items") or {}
    if shape is not None:
        return items.get(_cache_key(md5, shape))
    for k, v in items.items():
        if str(k).split("@")[0] == str(md5):
            return v
    return None


def _cache_put(entry):
    c = _cache_store()
    if c is None:
        return
    key = _cache_key(entry["md5"], entry["shape"])
    items = c.setdefault("items", {})
    order = c.setdefault("order", [])
    nbytes = int(entry["gray"].nbytes + entry["mask"].nbytes + entry.get("filled", entry["mask"]).nbytes)
    entry["bytes"] = nbytes
    old = items.pop(key, None)
    if old:
        c["bytes"] = max(0, int(c.get("bytes", 0)) - int(old.get("bytes") or 0))
        if key in order:
            order.remove(key)
    items[key] = entry
    order.append(key)
    c["bytes"] = int(c.get("bytes", 0)) + nbytes
    while (len(order) > CACHE_MAX_ITEMS or int(c.get("bytes", 0)) > CACHE_MAX_BYTES) and len(order) > 1:
        k0 = order.pop(0)
        gone = items.pop(k0, None)
        if gone:
            c["bytes"] = max(0, int(c.get("bytes", 0)) - int(gone.get("bytes") or 0))


def _register_id(image_id, entry, silhouette, features, numbers):
    K = _kernel_mod()
    if K is None:
        return
    reg = getattr(K, "_dsh_img_ids", None)
    if not isinstance(reg, dict):
        reg = {}
        try:
            setattr(K, "_dsh_img_ids", reg)
        except Exception:
            return
    reg[str(image_id)] = {"image_id": str(image_id), "md5": entry.get("md5"),
                          "path": entry.get("path"), "shape": list(entry.get("shape") or []),
                          "native_px": list(entry.get("native_px") or []),
                          "px_per_mm": entry.get("px_per_mm"),
                          "bbox_px": entry.get("bbox_px"), "silhouette": silhouette,
                          "features": features, "numbers": numbers[:24]}
    while len(reg) > 32:
        reg.pop(next(iter(reg)))


def _img_entry(image_id=None, path=None):
    """取量测入口：{gray,mask,filled,bbox_px,shape,md5,path,px_per_mm,...}；返回 (entry, err)。"""
    K = _kernel_mod()
    reg = getattr(K, "_dsh_img_ids", {}) if K is not None else {}
    meta = None
    if image_id:
        key = str(image_id)
        meta = reg.get(key)
        if meta is None:
            for k, v in reg.items():
                if str(k).startswith(key) or str(v.get("md5") or "").startswith(key):
                    meta = v
                    break
        if meta is None:
            return None, "image_id 不存在：%s（先 img_digest 生成，或直接给 path）" % key
        path = path or meta.get("path")
    if not path:
        return None, "给 path 或 image_id"
    winp = _win(path)
    if not os.path.exists(winp):
        return None, "图不存在：%s" % winp
    md5 = meta.get("md5") if meta else _md5_cached(winp)
    shape = meta.get("shape") if meta else None
    ent = _cache_get(md5, shape)
    if ent is not None and ent.get("gray") is not None:
        if meta and meta.get("px_per_mm") is not None:
            ent = dict(ent)
            ent["px_per_mm"] = meta.get("px_per_mm")
        return ent, None
    try:
        arr, p, native, loaded = _load_ex(path)
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:160])
    m, g, source, conf, _info = _mask_v2(arr)
    cc, cc_frac, _cci = _largest_cc_mask(m)
    bbox = _bbox(cc) if int(cc.sum()) > 0 else None
    filled = None
    if bbox:
        _holes, filled = _holes_of(cc, bbox)
    if filled is None:
        filled = cc
    ent = {"md5": _md5_cached(p), "path": p, "shape": [int(arr.shape[1]), int(arr.shape[0])],
           "native_px": native, "gray": g, "mask": cc, "filled": filled, "bbox_px": bbox,
           "source": source, "confidence": round(float(conf), 4),
           "px_per_mm": (meta or {}).get("px_per_mm")}
    _cache_put(ent)
    return ent, None

# ── 画图（标注总图 / 原生裁剪）：只在 numpy 上画，不依赖 PIL ─────────────
def _draw_box(arr, box, color, t=2):
    H, W = arr.shape[0], arr.shape[1]
    try:
        x, y, w, h = [int(round(float(v))) for v in list(box)[:4]]
    except Exception:
        return
    x0 = max(0, min(W - 1, x))
    y0 = max(0, min(H - 1, y))
    x1 = max(0, min(W - 1, x + w))
    y1 = max(0, min(H - 1, y + h))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    for d in range(max(1, int(t))):
        arr[min(H - 1, y0 + d), x0:x1 + 1, :3] = color
        arr[max(0, y1 - d), x0:x1 + 1, :3] = color
        arr[y0:y1 + 1, min(W - 1, x0 + d), :3] = color
        arr[y0:y1 + 1, max(0, x1 - d), :3] = color


def _draw_line(arr, p1, p2, color, t=2):
    H, W = arr.shape[0], arr.shape[1]
    try:
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
    except Exception:
        return
    n = int(max(abs(x2 - x1), abs(y2 - y1))) + 1
    n = max(2, min(n, 4 * (H + W) + 8))
    xs = np.rint(np.linspace(x1, x2, n)).astype(np.int64)
    ys = np.rint(np.linspace(y1, y2, n)).astype(np.int64)
    ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
    if not bool(ok.any()):
        return
    for dx in range(max(1, int(t))):
        for dy in range(max(1, int(t))):
            xx = np.clip(xs[ok] + dx, 0, W - 1)
            yy = np.clip(ys[ok] + dy, 0, H - 1)
            arr[yy, xx, :3] = color


def _draw_circle(arr, center, radius, color, t=1):
    H, W = arr.shape[0], arr.shape[1]
    try:
        cx, cy, r = float(center[0]), float(center[1]), float(radius)
    except Exception:
        return
    if r <= 0:
        return
    n = max(24, min(4096, int(2.0 * math.pi * r) + 8))
    th = np.linspace(0.0, 2.0 * math.pi, n)
    xs = np.rint(cx + r * np.cos(th)).astype(np.int64)
    ys = np.rint(cy + r * np.sin(th)).astype(np.int64)
    ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
    if not bool(ok.any()):
        return
    for dx in range(max(1, int(t))):
        arr[np.clip(ys[ok], 0, H - 1), np.clip(xs[ok] + dx, 0, W - 1), :3] = color
