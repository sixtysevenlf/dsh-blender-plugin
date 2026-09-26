# -*- coding: utf-8 -*-
"""DSH 内置 QC（v0.7.0）—— 参考图 vs 渲染图的客观比对：掩膜 / IoU / 逐层宽度剖面 / 对照图。

API 挂 K.dsh_qc_api；内环 measure 预置挂 K.dsh_measure。
原型来自 plush-build 会话自建的 7 个脚本（profile/sheet/ref_analysis/ref_measure，共 539 行），
把"每项目重写"收敛成通用能力；阈值与填充逻辑沿用他们调好的默认值，全部参数化。

只做**几何/轮廓类客观比对**；审美判断仍归人/模型（这条边界写进 help）。

典型用法：
    arr, alpha = K.dsh_qc_api["load"]("//ref/ref_all_views.png")      # 路径辅助来自 K
    m = K.dsh_qc_api["mask"](arr)                                     # 前景掩膜
    print(K.dsh_qc_api["iou"](m, m2))
    r = K.dsh_qc_api["compare"](ref_path, [9,38,444,545], render_png, label="front")
    # 内环里当目标函数（越小越好）：
    #   ns["score"] = K.dsh_measure["silhouette_iou"]({"from":[0,-6,1],"look_at":[0,0,0.5]}, ref_path, [9,38,444,545])
"""
import bpy
import json
import math
import os
import struct
import time
import zlib

import numpy as np

QC_VERSION = 5
PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("qc 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _out_dir():
    K = _kernel()
    return getattr(K, "out_dir", None) or os.path.join(os.path.expanduser("~"), "dsh_out")


# ---------------------------------------------------------------- PNG 直写（不经过 image.pixels，避免二次 gamma）

def _chunk(t, body):
    return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 4294967295)


def write_png(path, w, h, rgb_bytes):
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += rgb_bytes[y * stride:(y + 1) * stride]
    png = (PNG_MAGIC + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return path



# ---------------------------------------------------------------- v0.8.10（C6）纯 Python GIF 解码
def _gif_lzw(min_code, data, expected):
    """GIF LZW 解码（返回索引序列）。"""
    clear = 1 << min_code
    end = clear + 1
    code_size = min_code + 1
    table = [bytes([i]) for i in range(clear)] + [b"", b""]
    out = bytearray()
    prev = None
    bitpos = 0
    total = len(data) * 8
    while bitpos + code_size <= total:
        byte_i = bitpos >> 3
        chunk = int.from_bytes(data[byte_i:byte_i + 3].ljust(3, b"\x00"), "little")
        code = (chunk >> (bitpos & 7)) & ((1 << code_size) - 1)
        bitpos += code_size
        if code == clear:
            table = [bytes([i]) for i in range(clear)] + [b"", b""]
            code_size = min_code + 1
            prev = None
            continue
        if code == end:
            break
        if prev is None:
            entry = table[code]
        elif code < len(table):
            entry = table[code]
            table.append(prev + entry[:1])
        else:
            entry = prev + prev[:1]
            table.append(entry)
        out += entry
        if prev is not None and len(table) < 4096 and code_size < 12 and len(table) >= (1 << code_size):
            code_size += 1
        prev = entry
        if expected and len(out) >= expected:
            break
    return bytes(out[:expected]) if expected else bytes(out)


def _wp(path):
    """路径归一化（WSL→Windows / 反斜杠 UNC），Blender 侧 open() 才能用。"""
    K = _kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            return K.win_path(path)
        except Exception:
            pass
    return str(path)


def _gif_first_frame(path):
    """GIF 首帧 → (h, w, 4) uint8 RGBA。只支持常见情形（GCT/LCT、隔行、透明索引）。"""
    path = _wp(path)
    with open(path, "rb") as f:
        data = f.read()
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise RuntimeError("不是 GIF 文件")
    i = 6
    w, h = struct.unpack("<HH", data[i:i + 4]); i += 4
    flags, _bg, _asp = data[i], data[i + 1], data[i + 2]; i += 3
    gct = None
    if flags & 0x80:
        n = 1 << ((flags & 7) + 1)
        gct = np.frombuffer(data[i:i + n * 3], dtype=np.uint8).reshape(n, 3).copy(); i += n * 3
    trans_idx = None
    canvas = np.zeros((h, w, 4), dtype=np.uint8)
    while i < len(data):
        b = data[i]
        if b == 0x3B:                                     # trailer
            break
        if b == 0x21:                                     # extension
            label = data[i + 1]; i += 2
            if label == 0xF9 and i + 6 < len(data):       # graphic control
                # 结构：21 F9 <blockSize=4> <packed> <delay:2> <transparentIdx> 00
                i += 1                                        # ← 旧版漏了 blockSize 这一字节，导致后面全部错位
                packed = data[i]; i += 1
                i += 2                                        # delay
                tidx = data[i]; i += 1
                if packed & 0x01:
                    trans_idx = tidx
                while i < len(data) and data[i]:
                    i += 1 + data[i]
                i += 1
            else:
                while i < len(data) and data[i]:
                    i += 1 + data[i]
                i += 1
            continue
        if b == 0x2C:                                     # image descriptor
            i += 1
            left, top, iw, ih = struct.unpack("<HHHH", data[i:i + 8]); i += 8
            lflags = data[i]; i += 1
            lct = gct
            if lflags & 0x80:
                n2 = 1 << ((lflags & 7) + 1)
                lct = np.frombuffer(data[i:i + n2 * 3], dtype=np.uint8).reshape(n2, 3).copy(); i += n2 * 3
            interlaced = bool(lflags & 0x40)
            min_code = data[i]; i += 1
            chunks = bytearray()
            while i < len(data) and data[i]:
                ln = data[i]; i += 1
                chunks += data[i:i + ln]; i += ln
            i += 1
            idx = np.frombuffer(_gif_lzw(min_code, bytes(chunks), iw * ih), dtype=np.uint8).reshape(ih, iw)
            if interlaced:
                rows = np.zeros(ih, dtype=int)
                k = 0
                for start, step in ((0, 8), (4, 8), (2, 4), (1, 2)):
                    for r in range(start, ih, step):
                        rows[r] = k; k += 1
                idx = idx[rows]
            if lct is None:
                raise RuntimeError("GIF 没有调色板")
            rgb = lct[np.clip(idx.astype(int), 0, len(lct) - 1)]
            alpha = np.full((ih, iw), 255, dtype=np.uint8)
            if trans_idx is not None:
                alpha[idx == trans_idx] = 0
            y0, x0 = top, left
            canvas[y0:y0 + ih, x0:x0 + iw, :3] = rgb[:max(0, min(ih, h - y0)), :max(0, min(iw, w - x0))]
            canvas[y0:y0 + ih, x0:x0 + iw, 3] = alpha[:max(0, min(ih, h - y0)), :max(0, min(iw, w - x0))]
            return canvas
        i += 1
    raise RuntimeError("GIF 里没有找到图像帧")


def gif_to_png(path, out=None):
    """GIF 首帧 → PNG（应急通道：本机没有 PIL/pip/ffmpeg 时用它把 GIF 变成可读图）。"""
    arr = _gif_first_frame(path)
    h, w = arr.shape[0], arr.shape[1]
    rgb = np.ascontiguousarray(arr[:, :, :3]).tobytes()
    out = out or (os.path.splitext(_wp(path))[0] + "_frame0.png")
    # macOS/Linux 的绝对路径以 / 开头，也要算「已绝对」，否则会被 _wp 再走一遍
    out_s = str(out)
    _abs = (out_s[1:2] == ":" or out_s.startswith(chr(92) * 2) or out_s.startswith("/"))
    out = out if _abs else _wp(out)
    write_png(out, w, h, rgb)
    return _j({"ok": True, "path": out, "size": [w, h], "bytes": os.path.getsize(out),
               "note": "只取首帧；GIF 里其余帧没有解"})


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
        # v0.8.10（C6）：GIF 走内置纯 Python 解码兜底（本机无 PIL/pip/ffmpeg）
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
    # S2：度量收敛到共享内核（唯一实现）
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
    # S2：度量收敛到共享内核（唯一实现）；空并集时 kit 给 None，这里按旧契约返回 0.0
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
    """布尔掩膜绕中心旋转（最近邻）。v0.8.10（C3）：外部反馈里"同一轮廓被取向差压到 0.62 假低分"。"""
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


def qc_self_check() -> str:
    """算法自检（不需要参考图）：把参考图与自己的旋转/平移/缩放版本比，检查 IoU 的单调性与 1.0 上界。"""
    import numpy as _np
    H = W = 200
    yy, xx = _np.mgrid[0:H, 0:W]
    base = (((xx - 100) / 60.0) ** 2 + ((yy - 140) / 40.0) ** 2) < 1.0
    base |= ((_np.abs(xx - 100) < 14) & (yy > 30) & (yy < 140))
    same = _align_search(base, base)
    shift = _np.roll(base, 12, axis=1)
    shifted = _align_search(base, shift)
    scale = _scale_bool(base, int(W * 1.15), int(H * 1.15))[:H, :W]
    scaled = _align_search(base, scale)
    m_ok = qc_metrics(base, base, same["iou"], same.get("inter"), same.get("union"))
    return _j({"ok": True, "iou_self": round(float(same["iou"]), 4),
               "iou_shift12px": round(float(shifted["iou"]), 4),
               "iou_scale115": round(float(scaled["iou"]), 4),
               "self_metrics": m_ok,
               "expect": "iou_self 应=1.0；shift/scale 应被对齐搜索找回高 IoU（>0.95）"})
def qc_compare_auto(ref_path, ref_box=None, render_path=None, label="qc", out_dir=None, render_box=None,
                    search=True, work=384, bins=24, sat=0.13, v=0.74, mask_mode="auto", rotate=False):
    """优化版比对（推荐默认）：
      ① 掩膜自适应（alpha 智能判定 / 背景色+Otsu，不再手调阈值）
      ② 对齐搜索（尺度±10% + 平移±5%，两轮细化，最大化 IoU）
      ③ 可行动指标：IoU / Dice / 缺面积 / 多面积 / 边界距离 / 逐层剖面差
      ④ 产出：叠加图 + 三联对照图（参考 | 渲染 | 叠加）
    """
    t0 = time.perf_counter()
    d = out_dir or _out_dir()
    os.makedirs(d, exist_ok=True)
    ref_srgb, ref_alpha = qc_load(ref_path)
    ref = qc_crop(ref_srgb, ref_box) if ref_box else ref_srgb
    # v0.8.10（C3 口径统一）：ref 也要走 alpha 判定 —— 旧版 ref 用 Otsu、render 用 alpha，
    # 同一张图自比只有 0.565（外部反馈里"同一轮廓被掩膜口径压到 0.62 假低分"就是这个）
    rm, rminfo = qc_mask_auto(ref, ref_alpha) if mask_mode == "auto" else (qc_mask(ref, sat, v), {"source": "satv"})
    # v0.8.3：auto 掩膜适用性检查 —— 前景占比异常（满构图海报 / 几乎无前景）时回落阈值口径并显式标注
    _fg = float(rm.mean())
    if mask_mode == "auto" and (_fg > 0.8 or _fg < 0.02):
        rm = qc_mask(ref, sat, v)
        rminfo = {"source": "satv-fallback", "auto_fg_frac": round(_fg, 4),
                  "reason": "auto 前景占比 %.1f%% 不可信（>80%% 或 <2%%）→ 回落阈值口径 sat=%.2f v=%.2f" % (_fg * 100, sat, v)}
    ren_srgb, ren_alpha = qc_load(render_path)
    if render_box:
        ren_srgb = qc_crop(ren_srgb, render_box)
        ren_alpha = qc_crop(ren_alpha, render_box)
    rn, rninfo = qc_mask_auto(ren_srgb, ren_alpha) if mask_mode == "auto" else (qc_mask(ren_srgb, sat, v), {"source": "satv"})
    fixed = _align_search(rm, rn, work=work, coarse=False) if True else None
    align = _align_search(rm, rn, work=work, coarse=True) if search else fixed
    base = fixed if fixed is not None else align
    if base is not None:
        mref, mren = align["mask_ref"], align["mask_render"]
        m = qc_metrics(mref, mren, base["iou"], base.get("inter"), base.get("union"))
        m["scale"] = round(base["scale"], 4)
        m["scale_base"] = base.get("base_scale")
        m["shift_px"] = base.get("shift")
        m["canvas"] = base["canvas"]
        # v0.8.10（C3）：可选角度细化（默认关；开了就报 rotation_deg 与增益）
        if rotate:
            try:
                _rot = qc_align_rotate(rm, rn, work=work)
                if _rot is not None:
                    m["rotation_deg"] = round(float(_rot["deg"]), 2)
                    m["iou_rotated"] = round(float(_rot["iou"]), 4)
                    m["iou_rotate_gain"] = float(_rot.get("iou_gain_vs_no_rot") or 0.0)
                    if _rot["iou"] > float(base["iou"]):
                        mref, mren = _rot["mask_ref"], _rot["mask"]
                        m2 = qc_metrics(mref, mren, _rot["iou"])
                        for _k in ("iou", "dice", "inter_px", "ref_px", "render_px", "missing_px", "extra_px", "boundary"):
                            if _k in m2 and _k in m:
                                m[_k] = m2[_k]
            except Exception as _e:
                m["rotate_error"] = "%s: %s" % (type(_e).__name__, str(_e)[:100])
    else:
        H = min(rm.shape[0], rn.shape[0]); W = min(rm.shape[1], rn.shape[1])
        mref = rm[:H, :W]; mren = rn[:H, :W]
        m = qc_metrics(mref, mren)
        m["canvas"] = [H, W]
    if fixed is not None and align is not None and fixed is not align:
        m["iou_fixed"] = m["iou"]
        m["iou_search"] = round(float(align["iou"]), 4)
        m["iou_search_gain"] = round(float(align["iou"]) - float(m["iou"]), 4)
    else:
        m["iou_fixed"] = m["iou"]
    warn = []
    if m.get("scale") and m.get("scale_base"):
        drift = float(m["scale"]) / max(1e-6, float(m["scale_base"]))
        m["scale_drift"] = round(drift, 4)
        if abs(drift - 1.0) > 0.05:
            warn.append("对齐搜索把尺度拉开 %.1f%%：可能是在补偿取景/裁切差，也可能模型比例确实不对 —— 请结合 iou_fixed 一起看" % ((drift - 1.0) * 100.0))
    if (m.get("iou_search_gain") or 0) > 0.05:
        warn.append("搜索对齐比固定对齐高 %.3f：单看 IoU 会偏乐观，建议报告里两个都给" % (float(m["iou"]) - float(m["iou_fixed"])))
    # ---- v0.8.10（B3）「太漂亮」守卫：IoU 恒等 1.0000 / 面积完全相同 → 疑似同一张图或参数没生效
    try:
        _iou = float(m.get("iou") or 0)
        _a, _b = m.get("a_px"), m.get("b_px")
        if _iou >= 0.9999:
            warn.append("IoU ≥ 0.9999（%.4f）：疑似两张图是同一份、或参数没生效 —— 请核对输入路径/取景参数" % _iou)
        if _iou > 0.999 and _a and _b and int(_a) == int(_b):
            warn.append("掩膜面积完全相同（%s px）且 IoU≈1：强烈怀疑参考图与渲染图是同一张" % _a)
        if _iou >= 0.999 and abs(float(m.get("scale", 1.0)) - 1.0) < 1e-6 and abs(float(m.get("shift_px", [0, 0])[0] or 0)) < 0.01:
            warn.append("尺度/平移都是零位移却 IoU≈1：典型的「两个 view 其实渲了同一张图」")
    except Exception:
        pass
    m["warnings"] = warn
    pa = qc_profile(mref, bins)
    pb = qc_profile(mren, bins)
    m["profile_diff"] = qc_profile_diff(pa, pb)
    ov, h, w = _overlay_bytes(mref, mren)
    ref_panel = _to_u8(_scale_rgb(np.clip(ref, 0.0, 1.0), w, h))
    a4 = np.clip(ren_alpha if ren_alpha is not None else np.ones_like(ren_srgb[:, :, 0]), 0.0, 1.0)
    ren_rgb = np.clip(ren_srgb, 0.0, 1.0) * a4[:, :, None] + (1.0 - a4[:, :, None])
    ren_panel = _to_u8(_scale_rgb(ren_rgb, w, h))
    stamp = time.strftime("%H%M%S")
    base = "%s_%s_%s" % (str(label), stamp, os.path.basename(str(render_path)).replace(".png", ""))
    ov_path = os.path.join(d, "qc_overlay_" + base + ".png")
    sh_path = os.path.join(d, "qc_sheet_" + base + ".png")
    write_png(ov_path, w, h, ov.tobytes())
    sheet, sh, sw = _hstack([ref_panel, ren_panel, ov])
    write_png(sh_path, sw, sh, sheet.tobytes())
    _eng = None
    try:
        _eng = bpy.context.scene.render.engine
        if "EEVEE" in str(_eng):
            _eng = str(_eng) + ("+RT" if getattr(bpy.context.scene.eevee, "use_raytracing", False) else "")
    except Exception:
        _eng = None
    out = {"ok": True, "label": str(label), "mode": "auto" if mask_mode == "auto" else mask_mode,
           "metrics": m, "masks": {"ref": rminfo, "render": rninfo},
           "overlay": ov_path, "sheet": sh_path, "ms": int((time.perf_counter() - t0) * 1000),
           "note": "score 建议用 iou（越高越好）；缺/多面积指出方向；boundary.mean_px 是边界平均偏差（像素）",
           "engine_at_qc": _eng,
           "engine_note": "判据只在同一引擎内可比（换引擎后历史分数不可直接对比）"}
    out["iou"] = m["iou"]
    out["profile"] = {"ref": pa, "render": pb, "diff": m["profile_diff"]}
    out["render_px"] = m["render_px"]
    return _j(out)

def qc_robustness_check(ref_path, ref_box=None, render_path=None, dx=12, dy=-8, zoom=1.06, work=384):
    """鲁棒性自测：把渲染掩膜人为平移 + 缩放（模拟"构图/裁切/取景差异"），
       对比「直接比」与「对齐搜索后比」的 IoU —— 用来证明优化②确实在补偿外部差异。
    """
    ref_srgb, _ = qc_load(ref_path)
    ref = qc_crop(ref_srgb, ref_box) if ref_box else ref_srgb
    rm, rinfo = qc_mask_auto(ref, None)
    if render_path:
        ren_srgb, ren_alpha = qc_load(render_path)
        rn, ninfo = qc_mask_auto(ren_srgb, ren_alpha)
    else:
        rn, ninfo = rm.copy(), {"source": "self"}
    a0 = _align_search(rm, rn, work=work)
    zw = max(8, int(rn.shape[1] * float(zoom)))
    zh = max(8, int(rn.shape[0] * float(zoom)))
    zp = _scale_bool(rn, zw, zh)
    rp = np.zeros_like(rn)
    h = min(rn.shape[0], zp.shape[0])
    w = min(rn.shape[1], zp.shape[1])
    rp[:h, :w] = zp[:h, :w]
    rp = np.roll(np.roll(rp, int(dx), axis=1), int(dy), axis=0)
    direct = qc_metrics(rm, rp)
    searched = _align_search(rm, rp, work=work)
    sm = qc_metrics(searched["mask_ref"], searched["mask_render"], searched["iou"], searched.get("inter"), searched.get("union"))
    return _j({"ok": True, "base_iou": round(float(a0["iou"]), 4),
               "perturbed_direct_iou": direct["iou"], "perturbed_searched_iou": sm["iou"],
               "recovered": round(float(sm["iou"] - direct["iou"]), 4),
               "perturb": {"dx": dx, "dy": dy, "zoom": zoom}, "masks": {"ref": rinfo, "render": ninfo},
               "expect": "searched 应显著高于 direct（对齐搜索在补偿构图差异）"})

def qc_mask_sweep(ref_path, ref_box=None, render_path=None, sats=(0.10, 0.13, 0.16), vs=(0.68, 0.74, 0.80), label="sweep"):
    """掩膜口径敏感度扫描：固定对齐下逐组 (sat, v) 算 IoU → 给区间 + 推荐口径。"""
    ref_srgb, _ = qc_load(ref_path)
    ref = qc_crop(ref_srgb, ref_box) if ref_box else ref_srgb
    ren_srgb, _a = qc_load(render_path)
    cells = []
    for s in sats:
        for v in vs:
            rm = qc_mask(ref, float(s), float(v)); rn = qc_mask(ren_srgb, float(s), float(v))
            fx = _align_search(rm, rn, work=384, coarse=False)
            if fx is None:
                continue
            iou, _i, _u = _iou_pair(fx["mask_ref"], fx["mask_render"])
            cells.append({"sat": float(s), "v": float(v), "iou": round(float(iou), 4)})
    if not cells:
        return _j({"ok": False, "error": "扫描失败（图读不到或掩膜为空）"})
    ious = sorted(c["iou"] for c in cells)
    med = ious[len(ious) // 2]
    best = min(cells, key=lambda c: abs(c["iou"] - med))
    return _j({"ok": True, "label": str(label), "cells": cells, "count": len(cells),
               "iou_min": ious[0], "iou_median": med, "iou_max": ious[-1],
               "spread": round(ious[-1] - ious[0], 4), "mask_used": best,
               "note": "区间 = 掩膜口径敏感度；报告应给 区间 + 推荐口径，不要只给一个数"})

def qc_help():
    return _j({
        "version": QC_VERSION,
        "ops": {"load": "load(path) → (srgb[h,w,3], alpha[h,w])（路径支持 //rel、/mnt/...、D:/...）",
                "crop": "crop(arr, [x0,y0,x1,y1])", "mask": "mask(srgb, sat=0.13, v=0.74, fill=True)",
                "iou": "iou(a_mask, b_mask) → {iou,inter,union,a_px,b_px}",
                "profile": "profile(mask, bins=24) → 逐层宽度", "profile_diff": "profile_diff(pa,pb)",
                "compare": "compare(ref_path, ref_box, render_path, label) → iou+剖面差+两张对照图",
                "resize_mask": "resize_mask(mask,h,w)", "write_png": "write_png(path,w,h,rgb_bytes)"},
        "measures": {"aabb_err": "aabb_err(obj_name, target_size) → 包围盒尺寸误差（内环 score 用）",
                     "silhouette_iou": "silhouette_iou(view_spec, ref_path, ref_box) → 1-IoU（越小越好；会出图再比对）",
                     "profile_err": "profile_err(view_spec, ref_path, ref_box, bins) → 平均剖面差（像素）"},
        "boundary": "只做几何/轮廓类客观比对；审美判断仍归人/模型。阈值默认值来自 plush-build 会话的调参结果，可按项目覆盖。",
    })


# ---------------------------------------------------------------- 内环 measure 预置（P2）

def m_aabb_err(obj_name, target_size):
    ob = bpy.data.objects.get(str(obj_name))
    if ob is None:
        return 1e9
    d = ob.dimensions
    t = [float(x) for x in target_size]
    return float(abs(d[0] - t[0]) + abs(d[1] - t[1]) + abs(d[2] - t[2]))


def _render_view(view_spec, label="measure"):
    K = _kernel()
    api = getattr(K, "dsh_view_api", None) if K is not None else None
    if api is None:
        raise RuntimeError("需要 view.py（K.dsh_view_api）才能出图；插件里 preload='view' 或已由引擎注入")
    spec = dict(view_spec or {})
    spec.setdefault("width", 480)
    spec.setdefault("height", 480)
    d = os.path.join(_out_dir(), "qc_renders")
    os.makedirs(d, exist_ok=True)
    spec["path"] = os.path.join(d, "%s_%s.png" % (str(label), time.strftime("%H%M%S")))
    info = json.loads(api["capture"](json.dumps(spec)))
    if not info.get("ok"):
        raise RuntimeError("出图失败: %s" % info)
    return info["path"]


def m_silhouette_iou(view_spec, ref_path, ref_box, sat=0.13, v=0.74):
    """内环用：渲染指定视角 → 与参考图比 → 返回 1-IoU（越小越好）。',
    **故意用固定对齐（search=False）**：让目标函数可被搜索对齐"刷分"是对优化的污染
    （模型只要把物体缩放就能迁就轮廓）—— 内环要的是诚实指标，离线 QC 才用搜索对齐。"""
    png = _render_view(view_spec, "silhouette")
    res = json.loads(qc_compare_auto(ref_path, ref_box, png, label="silhouette", search=False))
    v2 = res.get("metrics", {}).get("iou_fixed", res.get("iou", 0.0))
    return float(1.0 - float(v2))


def m_profile_err(view_spec, ref_path, ref_box, bins=24, sat=0.13, v=0.74):
    png = _render_view(view_spec, "profile")
    res = json.loads(qc_compare_auto(ref_path, ref_box, png, label="profile", search=False, bins=bins))
    return float(res["profile"]["diff"]["mean_diff_px"])


# ---------------------------------------------------------------- 渲染 harness 转接（v0.8.8 / P2-1）

def _load_qc_render():
    """拿 qc_render 模块的 API：优先 K.dsh_qc_render_api，其次从 K.runtime_dir 现场加载同目录 qc_render.py。

    这样即使进程里只注入了 qc.py（例如 headless 只 preload 了 qc），也能用渲染 harness。
    """
    K = _kernel()
    api = getattr(K, "dsh_qc_render_api", None) if K is not None else None
    if api:
        return api
    d = getattr(K, "runtime_dir", None) if K is not None else None
    if d:
        p = os.path.join(str(d), "qc_render.py")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                src = fh.read()
            exec(compile(src, p, "exec"), {"__name__": "dsh_qc_render", "__file__": p})
            api = getattr(K, "dsh_qc_render_api", None)
    return api


def qc_render_views(**kw):
    """qc.py 侧转接：把参数整包转给 qc_render.views（多视角渲染 harness）。

    用法（工具侧）：blender_rt_plan(op="qc_render_views", args={views:["iso","front"], budget_s:60, ...})
    """
    api = _load_qc_render()
    if api is None:
        return _j({"ok": False, "error": "qc_render 模块不可用：K.dsh_qc_render_api 缺失且 K.runtime_dir 下没有 qc_render.py",
                   "hint": "headless 用 preload='qc,qc_render'；GUI 走 blender_rt_plan(op='qc_render_views')"})
    return api["render_views"](kw)


def qc_render_help():
    api = _load_qc_render()
    if api is None:
        return _j({"ok": False, "error": "qc_render 模块不可用"})
    return api["help"]()


def qc_dispatch(op, args=None):
    """统一入口：工具侧用 qc_<op> 调用（load/crop/mask/iou/profile/compare/help...）"""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    fn = qc_ops().get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown qc op", "op": op, "ops": sorted(qc_ops())})
    try:
        r = fn(**kw)
        return r if isinstance(r, str) else _j({"ok": True, "result": r})
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(kw), "sig_hint": qc_help()})


def qc_ops():
    return {"load": qc_load, "crop": qc_crop, "mask": qc_mask, "mask_auto": qc_mask_auto, "iou": qc_iou,
            "profile": qc_profile, "profile_diff": qc_profile_diff,
            "compare": qc_compare_auto, "compare_basic": qc_compare,
            "align_search": _align_search, "align_rotate": qc_align_rotate, "rotate_mask": _rotate_mask,
            "gif_to_png": gif_to_png, "metrics": qc_metrics, "self_check": qc_self_check,
            "robustness_check": qc_robustness_check, "mask_sweep": qc_mask_sweep,
            "resize_mask": qc_resize_mask, "write_png": write_png, "help": qc_help,
            # v0.8.8：渲染 harness（真实现在 qc_render.py，这里转接）
            "render_views": qc_render_views, "qc_render_views": qc_render_views, "render_help": qc_render_help}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
def qc_selftest():
    # QC 自检：IoU/缩放/剖面都用**已知答案**的合成掩码验（收敛到 kit 后数字必须仍然对）
    import json

    def _d(x):
        return json.loads(x) if isinstance(x, str) else x

    import numpy as np
    ev = {}
    try:
        a = np.zeros((10, 10), bool)
        a[:5, :] = True            # 50 px
        b = np.zeros((10, 10), bool)
        b[:, :5] = True            # 50 px，交集 25、并集 75 => IoU = 1/3
        r = _d(qc_iou(a, b))
        ev["iou_known"] = abs(float(r.get("iou")) - (25.0 / 75.0)) < 0.002 and int(r.get("inter")) == 25 and int(r.get("union")) == 75
        m = qc_resize_mask(a, 5, 5)
        ev["resize_shape"] = tuple(np.asarray(m).shape) == (5, 5)
        prof = qc_profile(a, bins=5)
        ev["profile_len"] = len(prof) == 5
        return _j({"ok": all(x is True for x in ev.values()), "evidence": ev, "iou": r.get("iou")})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})



if _K is not None:
    _K.dsh_qc_api = _KIT.Api({"version": QC_VERSION, "dispatch": _KIT.wrap_dispatch(qc_dispatch, {"selftest": qc_selftest}), "compare": qc_compare_auto,
                     "compare_basic": qc_compare, "mask_auto": qc_mask_auto, "metrics": qc_metrics,
                     "self_check": qc_self_check, "mask_sweep": qc_mask_sweep, "load": qc_load, "crop": qc_crop, "mask": qc_mask,
                     "iou": qc_iou, "profile": qc_profile, "profile_diff": qc_profile_diff,
                     "compare_basic": qc_compare, "resize_mask": qc_resize_mask, "write_png": write_png,
                     "render_views": qc_render_views, "align_search": _align_search, "iou_pair": _iou_pair,
                     "align_rotate": qc_align_rotate, "rotate_mask": _rotate_mask,
                     "gif_first_frame": _gif_first_frame, "gif_to_png": gif_to_png,
                     "help": qc_help,
        "selftest": qc_selftest})
    _K.dsh_measure = {"aabb_err": m_aabb_err, "silhouette_iou": m_silhouette_iou,
                      "profile_err": m_profile_err}
