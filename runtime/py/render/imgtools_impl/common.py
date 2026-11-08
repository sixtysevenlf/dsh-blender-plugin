# -*- coding: utf-8 -*-
"""DSH 参考图量具（v0.9.6 · 整合方案）—— 把"看不清"变成"不用看"。

现场问题（flash 层级）：参考图细节看不见 —— 视觉编码器先降采样，小于某个像素尺度的特征在编码阶段就丢了，
计数与小目标识别也是 VLM 的已知弱项。**提高分辨率没用**；有效的是：把参考图**量成数字**、把要判的区域
**裁出来放大**、把量到的东西**画回去**、把"像不像"变成**差分图 + IoU**。

ops（都走 blender_op）：
    img_scan(path, rois=None, invert=False, top_lines=5, sample_points=None)   # 只读：轮廓/bbox/长宽比/主轴角/直线角/取色
    img_crop(path, x, y, w, h, scale=2.0, out=None)                            # ROI 裁剪 + 上采样（喂给看图工具）
    img_annotate(path, lines=[], boxes=[], crosses=[], grid=None, out=None)    # 把量到的坐标/比例线画回图
    img_diff(a, b, out=None)                                                   # 轮廓差分 + IoU + 各轴剖面差
    img_digest(path, views=None, known_len=None, ...)                          # v2：一次量完 → 数字表+裁剪图册+可读性+verdict
    img_probe(image_id|path, op="scan_y", ...)                                 # v2：按需亚像素量测（scan_y/scan_x/circle/distance）
    img_measure(image_id|path, px_per_mm, targets=[...])                       # v2：像素→mm + 与期望比（delta/pass|fail）
    img_calib(views=[...], shared=[...])                                       # v2：多视逐视标定 + 跨视共享尺寸一致性
    img_selftest / img_help

坐标约定：**左上原点**（图像习惯），返回的 bbox 是 [x, y, w, h]。像素↔毫米要调用方自己标定（img_scan 会给像素数）。
局限：照片类参考图没有 alpha 时靠 Otsu 亮度分割，前景/背景对比低会不准 —— 那种情况先抠图（rembg）再喂。
"""
import hashlib as hashlib
import json as json
import math
import os
import time as time

import bpy
import numpy as np

IMG_VERSION = 2


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("imgtools 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删）
def _win(p):
    s = str(p)
    if s.startswith("/") and ":" not in s[:3]:
        K = __import__("sys").modules.get("dsh_rt_kernel")
        if K is not None and hasattr(K, "win_path"):
            try:
                return K.win_path(s)
            except Exception:
                return s
    return s


def _load_ex(path, max_px=None):
    """读图 → (arr, p, native_px, loaded_px)；max_px>0 且原生超过时**先缩放再读**（省内存）。

    坐标口径：arr 的 (H, W) 就是 loaded_px；要把 px 映射回原生像素需乘 native/loaded。
    """
    p = _win(path)
    if not os.path.exists(p):
        raise ValueError("图不存在：%s" % p)
    img = bpy.data.images.load(p, check_existing=False)
    try:
        nw, nh = int(img.size[0]), int(img.size[1])
        if nw <= 0 or nh <= 0:
            raise ValueError("图尺寸为 0：%s" % p)
        lw, lh = nw, nh
        if max_px and int(max_px) > 0 and nw * nh > int(max_px):
            s = math.sqrt(float(max_px) / float(nw * nh))
            lw = max(1, int(round(nw * s)))
            lh = max(1, int(round(nh * s)))
            img.scale(lw, lh)
        buf = np.empty(lw * lh * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        arr = buf.reshape(lh, lw, 4)[::-1]        # Blender 是下原点 → 翻成上原点
        return arr, p, [nw, nh], [lw, lh]
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass


def _load(path, max_px=None):
    """读图 → (H, W, 4) float 数组（**上下翻转成正向**，即 row 0 = 图像顶部）。"""
    arr, p, _n, _l = _load_ex(path, max_px=max_px)
    return arr, p


def _save(arr, path):
    h, w = arr.shape[0], arr.shape[1]
    p = _win(path)
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    img = bpy.data.images.new(os.path.basename(p) or "dsh_img", width=w, height=h, alpha=True)
    try:
        img.pixels.foreach_set(np.ascontiguousarray(arr[::-1].reshape(-1)).astype(np.float32))
        img.filepath_raw = p
        img.file_format = "PNG"
        img.save()
        return p
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass


def _gray(arr):
    return (0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]).astype(np.float32)


def _otsu(g):
    hist, _ = np.histogram(g, bins=256, range=(0.0, 1.0))
    total = hist.sum()
    if total == 0:
        return 0.5
    omega = np.cumsum(hist) / total
    mu = np.cumsum(hist * np.arange(256)) / max(1.0, total)
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    denom[denom <= 0] = 1e-9
    sigma_b = (mu_t * omega - mu) ** 2 / denom
    # 并列平台要取**中位**索引：双峰图里 0..峰前 那一段 sigma_b 完全相等，argmax 会落到 0
    # （阈值 0 ⇒ 掩膜全空 —— 实测踩到过）
    mx = float(np.max(sigma_b))
    idx = np.nonzero(sigma_b >= mx - 1e-12)[0]
    k = int(idx[len(idx) // 2]) if len(idx) else 128
    return k / 255.0


def _mask_of(arr, invert=False, threshold=None, use_alpha=True):
    """前景掩膜：优先用 alpha（有透明通道时最可靠），否则 Otsu 亮度分割。"""
    a = arr[:, :, 3]
    if use_alpha and float(a.min()) < 0.999:
        m = a > 0.5
    else:
        g = _gray(arr)
        thr = float(threshold) if threshold is not None else _otsu(g)
        m = g < thr            # 常见参考图是"亮底深件"
        if float(m.mean()) > 0.5:
            m = ~m
    if invert:
        m = ~m
    # 掩膜自检：空掩膜基本等于「没分出前景」』—— 退回诚实结论，而不是基于空掩膜编数字
    if float(m.mean()) < 0.002:
        inv = ~m
        if float(inv.mean()) > 0.002:
            m = inv
    return m


def _bbox(m):
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]




__all__ = [name for name in list(globals()) if not name.startswith("__")]
