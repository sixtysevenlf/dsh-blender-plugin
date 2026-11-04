# -*- coding: utf-8 -*-
"""Split from qc.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .pnggif import _gif_first_frame
from .align import qc_align_iou



# ---------------------------------------------------------------- 读图 / 掩膜

def qc_load(path):
    """读图 → (srgb RGB float [h,w,3], alpha [h,w])。行序已翻转成"上→下"（与图像坐标一致）。"""
    p = path
    K = _kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            p = K.win_path(path)
        except Exception:
            p = path
    try:
        img = bpy.data.images.load(p, check_existing=False)
    except Exception:
        # v0.8.4：UNC/中文读不到时用 K.stage 兜底
        _st = None
        if K is not None and hasattr(K, "stage"):
            try:
                _st = K.stage(path)
            except Exception:
                _st = None
        if not (_st and _st.get("ok")):
            raise
        p = _st["staged"]
        img = bpy.data.images.load(p, check_existing=False)
    # v0.8.9：Blender 对不支持的格式（如 GIF）**静默**返回 0×0 —— 在这里拦住，别让空数组飘到下游
    try:
        _w, _h = int(img.size[0]), int(img.size[1])
    except Exception:
        _w, _h = 0, 0
    if _w <= 0 or _h <= 0:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
        # v0.8.10：GIF 走内置纯 Python 解码兜底（本机无 PIL/pip/ffmpeg）
        if str(p).lower().endswith(".gif"):
            try:
                _a = _gif_first_frame(p)
                _srgb = np.clip(_a[:, :, :3].astype(np.float32) / 255.0, 0.0, 1.0)
                return _srgb, _a[:, :, 3].astype(np.float32) / 255.0
            except Exception as _e:
                raise RuntimeError("GIF 解码失败：%s（%s）" % (str(_e)[:120], str(p)))
        raise RuntimeError("读图失败或格式不支持：%s（Blender 能解 PNG/JPEG/WebP/BMP/TGA/TIFF/EXR；"
                           "GIF 会走内置首帧解码）→ 其它格式请先转成 PNG" % str(p))
    try:
        w, h = int(img.size[0]), int(img.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    a = buf.reshape(h, w, 4)[::-1].copy()
    rgb = np.clip(a[:, :, :3], 0.0, 1.0)
    srgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    return srgb, a[:, :, 3]


def qc_crop(arr, box):
    x0, y0, x1, y1 = [int(v) for v in box]
    return arr[y0:y1, x0:x1]


def qc_mask(srgb, sat=0.13, v=0.74, fill=True):
    """前景掩膜：饱和度高 或 明度低 → 前景（原型脚本的判据）；fill=按行/列补洞。"""
    mx = srgb.max(axis=2)
    mn = srgb.min(axis=2)
    s = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    m = (s > float(sat)) | (mx < float(v))
    if fill:
        for r in range(m.shape[0]):
            ii = np.where(m[r])[0]
            if len(ii) > 1:
                m[r, int(ii.min()):int(ii.max()) + 1] = True
        for c in range(m.shape[1]):
            ii = np.where(m[:, c])[0]
            if len(ii) > 1:
                m[int(ii.min()):int(ii.max()) + 1, c] = True
    return m


def qc_resize_mask(mask, h, w):
    """最近邻缩放到 (h,w)（用于渲染图与参考图尺寸不一致时对齐）。"""
    mh, mw = mask.shape
    yi = np.clip((np.arange(h) * (mh / float(h))).astype(int), 0, mh - 1)
    xi = np.clip((np.arange(w) * (mw / float(w))).astype(int), 0, mw - 1)
    return mask[yi][:, xi]


def qc_iou(a, b):
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    a2 = qc_resize_mask(a, h, w)
    b2 = qc_resize_mask(b, h, w)
    # 度量收敛到共享内核（唯一实现）
    _iou, inter, union = _KIT.iou(a2, b2)
    return {"iou": (0.0 if _iou is None else _iou), "inter": inter, "union": union,
            "a_px": int(a2.sum()), "b_px": int(b2.sum())}


def qc_profile(mask, bins=24, box=None):
    """逐层宽度剖面：自底向上分 bins 条带，每条带的前景宽度（像素）+ 归一化高度。"""
    m = mask if box is None else qc_crop(mask, box)
    h, w = m.shape
    out = []
    for i in range(int(bins)):
        y0 = int(h * i / bins)
        y1 = max(y0 + 1, int(h * (i + 1) / bins))
        band = m[y0:y1]
        xs = np.where(band.any(axis=0))[0]
        width = int(xs.max() - xs.min() + 1) if len(xs) else 0
        out.append({"t": round((i + 0.5) / bins, 4), "y0": y0, "y1": y1, "width_px": width,
                    "x_min": int(xs.min()) if len(xs) else None, "x_max": int(xs.max()) if len(xs) else None})
    return out


def qc_profile_diff(pa, pb):
    """两条剖面的逐层绝对差（像素），返回 (max_diff, mean_diff, per_band)。"""
    bands = []
    for ra, rb in zip(pa, pb):
        d = abs(int(ra["width_px"]) - int(rb["width_px"]))
        bands.append({"t": ra["t"], "a": ra["width_px"], "b": rb["width_px"], "diff": d})
    ds = [b["diff"] for b in bands] or [0]
    return {"max_diff_px": max(ds), "mean_diff_px": round(sum(ds) / len(ds), 2), "bands": bands}


# ---------------------------------------------------------------- 对照图

def _overlay_bytes(ref_mask, render_mask, bg=(38, 38, 38)):
    h = min(ref_mask.shape[0], render_mask.shape[0])
    w = min(ref_mask.shape[1], render_mask.shape[1])
    a = qc_resize_mask(ref_mask, h, w)
    b = qc_resize_mask(render_mask, h, w)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = bg
    img[a] = (220, 60, 60)
    img[b] = (60, 200, 90)
    img[np.logical_and(a, b)] = (235, 210, 70)
    return img, h, w


def _to_u8(rgb):
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _mask_bytes(mask, fg=(235, 235, 235), bg=(25, 25, 25)):
    h, w = mask.shape
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = bg
    img[mask] = fg
    return img


def _hstack(imgs, pad=8, bg=(15, 15, 15)):
    h = max(i.shape[0] for i in imgs)
    total_w = sum(i.shape[1] for i in imgs) + pad * (len(imgs) - 1)
    out = np.zeros((h, total_w, 3), dtype=np.uint8)
    out[:, :] = bg
    x = 0
    for i in imgs:
        out[0:i.shape[0], x:x + i.shape[1]] = i
        x += i.shape[1] + pad
    return out, h, total_w


def qc_compare(ref_path, ref_box=None, render_path=None, label="qc", sat=0.13, v=0.74, bins=24, out_dir=None, render_box=None, mode="align"):
    """参考图（+裁切框）vs 渲染图（+可选裁切框）→ IoU + 剖面差 + 对照图（ref | render | overlay）。
    注意：ref_box 用来从参考大图里裁出某个视图；渲染图通常是单视图，render_box 一般不用给。"""
    t0 = time.perf_counter()
    d = out_dir or _out_dir()
    os.makedirs(d, exist_ok=True)
    ref_srgb, _ = qc_load(ref_path)
    ref = qc_crop(ref_srgb, ref_box) if ref_box else ref_srgb
    rm = qc_mask(ref, sat, v)
    ren_srgb, ren_alpha = qc_load(render_path)
    if render_box:
        ren_srgb = qc_crop(ren_srgb, render_box)
        ren_alpha = qc_crop(ren_alpha, render_box)
    align = None
    if mode == "align":
        rgba = np.dstack([ren_srgb, ren_alpha])
        align = qc_align_iou(ref, rm, rgba)
    if align is not None:
        rn = align["mask_render"]
        rm = align["mask_ref"]
        res = {"iou": align["iou"], "inter": align["inter"], "union": align["union"],
               "a_px": align["ref_px"], "b_px": align["render_px"]}
        canvas_ref = align["canvas_ref"]
        canvas_ren = align["canvas_render"]
    else:
        rn = qc_resize_mask(qc_mask(ren_srgb, sat, v), rm.shape[0], rm.shape[1])
        res = qc_iou(rm, rn)
        canvas_ref = np.clip(ref, 0.0, 1.0)
        canvas_ren = np.clip(ren_srgb, 0.0, 1.0)
    pa = qc_profile(rm, bins)
    pb = qc_profile(rn, bins)
    diff = qc_profile_diff(pa, pb)
    ov, h, w = _overlay_bytes(rm, rn)
    stamp = time.strftime("%H%M%S")
    base = "%s_%s_%s" % (str(label), stamp, os.path.basename(str(render_path)).replace(".png", ""))
    ov_path = os.path.join(d, "qc_overlay_" + base + ".png")
    sh_path = os.path.join(d, "qc_sheet_" + base + ".png")
    write_png(ov_path, w, h, ov.tobytes())
    sheet, sh, sw = _hstack([_to_u8(canvas_ref), _to_u8(canvas_ren), ov])
    write_png(sh_path, sw, sh, sheet.tobytes())
    return _j({"ok": True, "label": str(label), "iou": round(res["iou"], 4), "inter_px": res["inter"],
               "ref_px": res["a_px"], "render_px": res["b_px"],
               "profile": {"ref": pa, "render": pb, "diff": diff},
               "overlay": ov_path, "sheet": sh_path,
               "size": [h, w], "ms": int((time.perf_counter() - t0) * 1000),
               "note": "iou 越高越像；剖面单位是像素（同一裁切框下可比）；sheet 左=参考 中=渲染 右=叠加(红参考/绿渲染/黄重合)"})

