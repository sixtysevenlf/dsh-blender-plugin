# -*- coding: utf-8 -*-
"""DSH 图像拼图（v0.8.10 / C4）—— N 张图 → 1 张接触表 + 每格指标表。

为什么有它（外部反馈 92/93）：ref-scout 自己写了 montage.py，**6 次调用覆盖 66 张图，省下约 51 次单图调用**；
但拼图**不省 token**（像素总量不变），省的是调用次数与注意力 —— 且超过 ~1700 px 宽会被 harness 二次缩放，
小格细节先糊。所以默认 tile=420、总宽尽量 ≤1700（cols 自动收敛）。

API 挂 K.dsh_montage_api；qc_render_views 的 args.sheet=N 会自动调用它。

    montage(paths, cols=None, tile=420, out=None, labels=None, grid=None, scale_m=None)
      → {"ok":true, "path":…, "size":[w,h], "cols":…, "rows":…, "tile":…,
         "tiles":[{index,label,path,size,bytes,md5,coverage,bbox,mean}]}

自带：网格线、每格编号（3×5 点阵字）、可选标尺（scale_m=每格宽度代表的米数）与每格指标表。
"""
import bpy
import hashlib
import json
import os
import struct
import zlib

import numpy as np

MONTAGE_VERSION = 1
DEFAULT_TILE = 420
MAX_WIDTH = 1700          # 超过它 harness 会二次缩放（外部反馈实测：1700 px 是甜点上限）

# 3×5 点阵字（数字 + 大写字母 + 常用符号）—— 不依赖 PIL/字体文件
FONT = {
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
    "A": ["111", "101", "111", "101", "101"], "B": ["110", "101", "110", "101", "110"],
    "C": ["111", "100", "100", "100", "111"], "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "111", "100", "111"], "F": ["111", "100", "111", "100", "100"],
    "G": ["111", "100", "101", "101", "111"], "H": ["101", "101", "111", "101", "101"],
    "I": ["111", "010", "010", "010", "111"], "J": ["001", "001", "001", "101", "111"],
    "K": ["101", "101", "110", "101", "101"], "L": ["100", "100", "100", "100", "111"],
    "M": ["101", "111", "111", "101", "101"], "N": ["110", "101", "101", "101", "101"],
    "O": ["111", "101", "101", "101", "111"], "P": ["111", "101", "111", "100", "100"],
    "Q": ["111", "101", "101", "111", "001"], "R": ["111", "101", "111", "110", "101"],
    "S": ["111", "100", "111", "001", "111"], "T": ["111", "010", "010", "010", "010"],
    "U": ["101", "101", "101", "101", "111"], "V": ["101", "101", "101", "101", "010"],
    "W": ["101", "101", "111", "111", "101"], "X": ["101", "101", "010", "101", "101"],
    "Y": ["101", "101", "010", "010", "010"], "Z": ["111", "001", "010", "100", "111"],
    "-": ["000", "000", "111", "000", "000"], "_": ["000", "000", "000", "000", "111"],
    ".": ["000", "000", "000", "000", "010"], "/": ["001", "001", "010", "100", "100"],
    ":": ["000", "010", "000", "010", "000"], " ": ["000", "000", "000", "000", "000"],
}


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("montage 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _win(path):
    K = _kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            return K.win_path(path)
        except Exception:
            pass
    s = str(path)
    if s.startswith("/mnt/") and len(s) > 6:
        return s[5].upper() + ":" + chr(92) + s[7:].replace("/", chr(92))
    return s


def _md5(path):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for c in iter(lambda: f.read(1 << 16), b""):
                h.update(c)
        return h.hexdigest()
    except Exception:
        return None


# ---------------------------------------------------------------- 读写

def _load_rgba(path):
    """PNG/JPEG/… → (h,w,4) float 数组（上→下），失败抛异常。"""
    p = _win(path)
    img = bpy.data.images.load(p, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        if w <= 0 or h <= 0:
            raise RuntimeError("读图失败或格式不支持（Blender 会静默给 0×0）：%s" % p)
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    return buf.reshape(h, w, 4)[::-1].copy()


def _write_png_rgb(path, rgb_u8):
    h, w = rgb_u8.shape[0], rgb_u8.shape[1]
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb_u8[y].tobytes()

    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)

    png = (bytes([137, 80, 78, 71, 13, 10, 26, 10])
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def _resize_nn(rgba, tw, th):
    h, w = rgba.shape[0], rgba.shape[1]
    yi = np.clip((np.arange(th) * (h / float(th))).astype(np.int32), 0, h - 1)
    xi = np.clip((np.arange(tw) * (w / float(tw))).astype(np.int32), 0, w - 1)
    return rgba[yi][:, xi]


def _draw_text(canvas, text, x, y, scale=2, color=(255, 255, 255)):
    """3×5 点阵字（scale 倍放大）。越界自动裁剪。"""
    H, W = canvas.shape[0], canvas.shape[1]
    cx = x
    for ch in str(text).upper():
        g = FONT.get(ch, FONT[" "])
        for ry in range(5):
            for rx in range(3):
                if g[ry][rx] == "1":
                    y0 = y + ry * scale
                    x0 = cx + rx * scale
                    if 0 <= y0 < H - scale and 0 <= x0 < W - scale:
                        canvas[y0:y0 + scale, x0:x0 + scale] = color
        cx += 4 * scale


# ---------------------------------------------------------------- 主入口

def montage(paths, cols=None, tile=DEFAULT_TILE, out=None, labels=None, grid=None, scale_m=None):
    if isinstance(paths, str):
        paths = [p for p in paths.replace(",", chr(10)).split(chr(10)) if p.strip()]
    paths = [str(p) for p in (paths or []) if str(p).strip()]
    if not paths:
        return {"ok": False, "error": "没有输入图片"}
    tile = max(48, min(2048, int(tile or DEFAULT_TILE)))
    n = len(paths)
    # cols：默认按 MAX_WIDTH 收敛（既不太宽被二次缩放，也不至于一列到底）
    if not cols or int(cols) <= 0:
        cols = int(min(n, max(1, MAX_WIDTH // (tile + 8))))
    cols = max(1, min(int(cols), n))
    rows = int((n + cols - 1) // cols)
    pad = 6
    W = cols * tile + (cols + 1) * pad
    H = rows * tile + (rows + 1) * pad + 22      # 底部留给标尺/图注
    canvas = np.zeros((H, W, 3), dtype=np.uint8)
    canvas[:, :] = (22, 24, 28)

    tiles = []
    for i, p in enumerate(paths):
        r = int(i // cols)
        c = int(i % cols)
        x0 = pad + c * (tile + pad)
        y0 = pad + r * (tile + pad)
        cell = canvas[y0:y0 + tile, x0:x0 + tile]
        cell[:, :] = (12, 13, 16)
        item = {"index": i, "label": (labels[i] if isinstance(labels, (list, tuple)) and i < len(labels) else os.path.basename(p)),
                "path": _win(p), "bytes": (os.path.getsize(_win(p)) if os.path.isfile(_win(p)) else None),
                "md5": _md5(_win(p))}
        try:
            rgba = _load_rgba(p)
            h, w = rgba.shape[0], rgba.shape[1]
            sc = min(tile / float(w), tile / float(h))
            tw, th = max(1, int(w * sc)), max(1, int(h * sc))
            small = _resize_nn(rgba, tw, th)
            a = np.clip(small[:, :, 3:4], 0.0, 1.0)
            rgb = np.clip(small[:, :, :3], 0.0, 1.0) * a + (1.0 - a) * 0.06   # 透明底 → 深灰
            u8 = (rgb * 255.0 + 0.5).astype(np.uint8)
            ox, oy = (tile - tw) // 2, (tile - th) // 2
            cell[oy:oy + th, ox:ox + tw] = u8
            cover = float((a > 0.02).mean())
            lum = float((0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]).mean())
            ys, xs = np.nonzero((a > 0.02)[:, :, 0]) if a.ndim == 3 else (np.array([]), np.array([]))
            bbox = ([int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None)
            item.update({"size": [w, h], "tile_px": [tw, th], "coverage": round(cover, 4),
                         "mean": round(lum, 4), "bbox": bbox, "ok": True})
            _draw_text(canvas, str(i + 1), x0 + 4, y0 + 4, 2, (240, 200, 90))
        except Exception as e:
            item.update({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])})
            _draw_text(canvas, "ERR", x0 + 4, y0 + 4, 2, (240, 90, 90))
        # 网格线（格线 + 可选坐标网格）
        canvas[y0:y0 + tile, x0:x0 + 2] = (70, 74, 80)
        canvas[y0:y0 + 2, x0:x0 + tile] = (70, 74, 80)
        try:
            gv = int(grid or 0)
        except Exception:
            gv = 0
        if gv >= 2:
            for k in range(1, gv):
                gx = x0 + int(tile * k / float(gv))
                gy = y0 + int(tile * k / float(gv))
                canvas[y0 + 2:y0 + tile - 2, gx:gx + 1] = (52, 56, 62)
                canvas[gy:gy + 1, x0 + 2:x0 + tile - 2] = (52, 56, 62)
        tiles.append(item)

    # 底部标尺（scale_m = 每格宽度代表的米数 → 画 1/5 格宽的比例尺）
    legend = "n=%d cols=%d tile=%d" % (n, cols, tile)
    if scale_m:
        try:
            sm = float(scale_m)
            bar_px = int(tile / 5)
            bx, by = W - pad - bar_px - 2, H - 12          # 右端，避免与左侧图注重叠
            canvas[by:by + 3, bx:bx + bar_px] = (230, 230, 230)
            for tx in (bx, bx + bar_px // 2, bx + bar_px - 2):
                canvas[by - 5:by, tx:tx + 2] = (230, 230, 230)
            legend = "%s | bar = 1/5 tile = %.3f m (grid=%s)" % (legend, sm / 5.0, grid)
        except Exception:
            pass
    _draw_text(canvas, legend, pad + 8, H - 12, 2, (200, 205, 210))

    out_path = _win(out or os.path.join(os.path.expanduser("~"), "dsh_montage.png"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    nbytes = _write_png_rgb(out_path, canvas)
    return {"ok": True, "path": out_path, "bytes": int(nbytes), "size": [W, H], "cols": cols, "rows": rows,
            "tile": tile, "count": n, "tiles": tiles,
            "note": "tile 默认 420、总宽尽量 ≤1700 px（超过会被 harness 二次缩放，小格细节先糊）；"
                    "拼图省调用次数，不省 token（像素总量不变）"}


def montage_help():
    return _j({"version": MONTAGE_VERSION, "entry": "K.dsh_montage_api['montage'](paths, cols, tile, out, labels, grid, scale_m)",
               "returns": "{ok,path,size,cols,rows,tile,tiles:[{index,label,path,size,coverage,mean,bbox,md5}]}",
               "sweet_spot": "12–16 格 / tile 400–420 / 总宽 ≤1700 px（外部反馈实测）"})


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
def montage_selftest():
    # 拼图自检：两张 8x8 纯色 PNG -> 拼成 2 列 -> 检查产物落盘
    import json

    def _d(x):
        return json.loads(x) if isinstance(x, str) else x

    import bpy
    import os
    import tempfile
    ev = {}
    d = tempfile.mkdtemp(prefix="dsh_montage_")
    ps = []
    try:
        for i, col in enumerate(((1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0))):
            img = bpy.data.images.new("__dsh_mt_%d" % i, width=8, height=8, alpha=True)
            img.pixels[:] = list(col) * 64
            p = os.path.join(d, "t%d.png" % i)
            img.filepath_raw = p
            img.file_format = "PNG"
            img.save()
            bpy.data.images.remove(img)
            ps.append(p)
        ev["inputs_written"] = all(os.path.exists(p) for p in ps)
        out = os.path.join(d, "montage.png")
        r = _d(montage(paths=ps, cols=2, out=out))
        ev["montage_ok"] = isinstance(r, dict) and r.get("ok") is not False
        ev["output_written"] = os.path.exists(out)
        return _j({"ok": all(x is True for x in ev.values()), "evidence": ev, "dir": d})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})



if _K is not None:
    _K.dsh_montage_api = _KIT.Api({
        "dispatch": _KIT.wrap_dispatch(None, {"selftest": montage_selftest}),"version": MONTAGE_VERSION, "montage": montage, "help": montage_help,
        "selftest": montage_selftest})
