# -*- coding: utf-8 -*-
"""DSH 参考图量具（v0.9.6 · 整合方案 P0）—— 把"看不清"变成"不用看"。

现场问题（flash 层级）：参考图细节看不见 —— 视觉编码器先降采样，小于某个像素尺度的特征在编码阶段就丢了，
计数与小目标识别也是 VLM 的已知弱项。**提高分辨率没用**；有效的是：把参考图**量成数字**、把要判的区域
**裁出来放大**、把量到的东西**画回去**、把"像不像"变成**差分图 + IoU**。

ops（都走 blender_rt_plan）：
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
import hashlib
import json
import math
import os
import time

import bpy
import numpy as np

IMG_VERSION = 2


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("imgtools 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
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


def img_scan(path, rois=None, invert=False, threshold=None, top_lines=5, sample_points=None):
    """只读：把参考图量成数字（轮廓 bbox / 长宽比 / 填充率 / 主轴角 / 主要直线角度 / 指定点取色）。"""
    arr, p = _load(path)
    h, w = arr.shape[0], arr.shape[1]
    m = _mask_of(arr, invert=invert, threshold=threshold)
    bb = _bbox(m)
    out = {"ok": True, "path": p, "size": [w, h], "foreground_pixels": int(m.sum()),
           "foreground_fraction": round(float(m.mean()), 4)}
    if float(m.mean()) > 0.95:
        out["mask_warning"] = ("前景占了 %.0f%% 的画面 —— 很可能没分出背景（照片请先抠图 rembg，或显式给 threshold）；"
                               "这种输入下的 bbox / 长宽比不可信" % (m.mean() * 100))
    if bb is None:
        out.update({"empty": True, "note": "没有分出前景 —— 照片类参考图请先抠背景（rembg），或显式给 threshold"})
        return _j(out)
    x, y, bw, bh = bb
    sub = m[y:y + bh, x:x + bw]
    # 主轴角（图像矩）
    ys, xs = np.nonzero(m)
    cx, cy = xs.mean(), ys.mean()
    vxx, vyy = ((xs - cx) ** 2).mean(), ((ys - cy) ** 2).mean()
    vxy = ((xs - cx) * (ys - cy)).mean()
    ang = 0.5 * math.atan2(2 * vxy, (vxx - vyy) + 1e-12)
    # 主要直线角度：梯度方向直方图（0–180°，峰值即主边方向）
    g = _gray(arr)
    gx = np.zeros_like(g); gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    mag = np.hypot(gx, gy) * m
    ori = (np.degrees(np.arctan2(gy, gx)) + 180.0) % 180.0
    hist, edges = np.histogram(ori, bins=36, range=(0, 180), weights=mag)
    tops = sorted(range(36), key=lambda i: -hist[i])[:max(1, int(top_lines))]
    out.update({
        "bbox": bb, "aspect_wh": round(bw / float(bh), 4), "fill_ratio": round(float(sub.mean()), 4),
        "centroid": [round(float(cx), 2), round(float(cy), 2)],
        "principal_axis_deg": round(math.degrees(ang), 2),
        "edge_orientation_top": [{"deg": int((edges[i] + edges[i + 1]) / 2), "weight": round(float(hist[i]), 1)} for i in tops],
        "note": "bbox 是像素；要毫米就先标定一条已知长度（给标尺或 sample_points 取两端点）",
    })
    # 逐列上下边界（站表原料：外表面还原用）——在 bbox 内按列采样，最多 512 列
    step_c = max(1, bw // 512)
    col_top, col_bot, col_w = [], [], []
    for c in range(0, bw, step_c):
        colm = m[y:y + bh, x + c]
        idxs = np.nonzero(colm)[0]
        if len(idxs) == 0:
            col_top.append(None); col_bot.append(None); col_w.append(0)
        else:
            col_top.append(int(idxs.min())); col_bot.append(int(idxs.max())); col_w.append(int(len(idxs)))
    out["col_top"] = col_top          # 相对 bbox 顶部的行号（None = 该列空）
    out["col_bottom"] = col_bot
    out["col_fill"] = col_w
    out["col_px_per_step"] = step_c
    # 每行前景宽度（人形/轮廓比例用）：在 bbox 内按行采样，最多 256 条
    step = max(1, bh // 256)
    out["row_profile"] = [int(sub[r].sum()) for r in range(0, bh, step)]
    if rois:
        rows = []
        for r in rois:
            rx, ry, rw, rh = [int(v) for v in (list(r) + [0, 0, 0, 0])[:4]]
            rm = m[max(0, ry):ry + rh, max(0, rx):rx + rw]
            rows.append({"roi": [rx, ry, rw, rh], "foreground_fraction": round(float(rm.mean()), 4) if rm.size else None,
                         "foreground_pixels": int(rm.sum())})
        out["rois"] = rows
    if sample_points:
        rows = []
        for pt in sample_points:
            px, py = int(pt[0]), int(pt[1])
            px = min(max(px, 0), w - 1); py = min(max(py, 0), h - 1)
            c = arr[py, px]
            rows.append({"point": [px, py], "rgba": [round(float(v), 4) for v in c],
                         "foreground": bool(m[py, px])})
        out["samples"] = rows
    return _j(out)


def img_crop(path, x, y, w, h, scale=2.0, out=None, label=None):
    """ROI 裁剪 + 上采样后落盘 —— "中央凹式看图"：一次只看一个区域，且尽量占满视觉编码器的输入。"""
    arr, p = _load(path)
    H, W = arr.shape[0], arr.shape[1]
    x, y, w, h = int(x), int(y), int(w), int(h)
    if w <= 0 or h <= 0:
        raise ValueError("w/h 必须 > 0")
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("ROI 完全在图外：图是 %dx%d" % (W, H))
    sub = arr[y0:y1, x0:x1]
    s = float(scale or 1.0)
    if s > 1.0:
        sh, sw = sub.shape[0], sub.shape[1]
        nh, nw = int(round(sh * s)), int(round(sw * s))
        yi = np.clip((np.arange(nh) / s).astype(int), 0, sh - 1)
        xi = np.clip((np.arange(nw) / s).astype(int), 0, sw - 1)
        sub = sub[yi][:, xi]                     # 最近邻：不引入插值假细节
    outp = out or (os.path.splitext(p)[0] + "_crop_%d_%d_%d_%d_x%s.png" % (x0, y0, x1 - x0, y1 - y0, str(s).replace(".", "p")))
    saved = _save(sub, outp)
    return _j({"ok": True, "source": p, "roi": [x0, y0, x1 - x0, y1 - y0], "scale": s,
               "out": saved, "out_size": [int(sub.shape[1]), int(sub.shape[0])], "label": label,
               "note": "把 out 路径交给看图工具；一次只看这一块，并带上明确问题（yes/no 或数字）"})


def img_annotate(path, lines=None, boxes=None, crosses=None, grid=None, out=None, thickness=2):
    """把量到的坐标/比例线/参考框**画回图**上 —— 让模型"读标注"而不是"猜形状"。
    颜色语义固定在回执 legend 里（图上看颜色、数字看回执）。"""
    arr, p = _load(path)
    H, W = arr.shape[0], arr.shape[1]
    out_arr = arr.copy()
    t = max(1, int(thickness))
    legend = []

    def put(x, y, color):
        if 0 <= x < W and 0 <= y < H:
            out_arr[y, x, 0:3] = color

    def hline(x0, x1, y, color):
        for x in range(max(0, int(x0)), min(W, int(x1))):
            for d in range(t):
                put(x, int(y) + d, color)

    def vline(x, y0, y1, color):
        for y in range(max(0, int(y0)), min(H, int(y1))):
            for d in range(t):
                put(int(x) + d, y, color)

    for i, ln in enumerate(lines or []):
        d = dict(ln)
        col = d.get("color") or ([1.0, 0.2, 0.2] if i % 3 == 0 else ([0.2, 1.0, 0.3] if i % 3 == 1 else [0.3, 0.5, 1.0]))
        x1, y1, x2, y2 = [float(d.get(k, 0)) for k in ("x1", "y1", "x2", "y2")]
        n = int(max(abs(x2 - x1), abs(y2 - y1))) + 1
        for k in range(n):
            xa = x1 + (x2 - x1) * k / max(1, n - 1)
            ya = y1 + (y2 - y1) * k / max(1, n - 1)
            put(int(round(xa)), int(round(ya)), col)
            for dd in range(1, t):
                put(int(round(xa)) + dd, int(round(ya)), col)
        legend.append({"kind": "line", "label": d.get("label"), "coords": [x1, y1, x2, y2],
                       "rgb": [round(float(c), 3) for c in col],
                       "length_px": round(math.hypot(x2 - x1, y2 - y1), 2)})
    for i, bx in enumerate(boxes or []):
        d = dict(bx)
        col = d.get("color") or [1.0, 0.85, 0.1]
        x, y, w, h = [float(d.get(k, 0)) for k in ("x", "y", "w", "h")]
        hline(x, x + w, y, col); hline(x, x + w, y + h, col)
        vline(x, y, y + h, col); vline(x + w, y, y + h, col)
        legend.append({"kind": "box", "label": d.get("label"), "box": [x, y, w, h],
                       "rgb": [round(float(c), 3) for c in col]})
    for i, cr in enumerate(crosses or []):
        d = dict(cr)
        col = d.get("color") or [1.0, 0.1, 1.0]
        x, y = float(d.get("x", 0)), float(d.get("y", 0))
        arm = float(d.get("arm", 8))
        hline(x - arm, x + arm, y, col); vline(x, y - arm, y + arm, col)
        legend.append({"kind": "cross", "label": d.get("label"), "at": [x, y], "rgb": [round(float(c), 3) for c in col]})
    if grid:
        gd = dict(grid) if isinstance(grid, dict) else {}
        step = int(gd.get("step") or max(8, min(W, H) // 10))
        col = [0.15, 0.85, 0.95]
        for x in range(0, W, step):
            for y in range(0, H, 2):
                put(x, y, col)
        for y in range(0, H, step):
            for x in range(0, W, 2):
                put(x, y, col)
        legend.append({"kind": "grid", "step_px": step, "rgb": col,
                       "note": "网格用于读数：格数×step 就是像素距离"})
    outp = out or (os.path.splitext(p)[0] + "_annot.png")
    saved = _save(out_arr, outp)
    return _j({"ok": True, "source": p, "out": saved, "size": [W, H], "legend": legend,
               "note": "图上看颜色、数字看 legend；比例线建议按参考图的已知尺寸放置"})


def img_diff(a, b, out=None, align=True, invert_a=False, invert_b=False):
    """两张图的轮廓差分：IoU + 各自独有像素 + 剖面差 + 红蓝叠加图（模型对"哪里多/少"远比"像不像"敏感）。"""
    aa, pa = _load(a)
    ab, pb = _load(b)
    ma = _mask_of(aa, invert=invert_a)
    mb = _mask_of(ab, invert=invert_b)
    if align:
        ba, bb2 = _bbox(ma), _bbox(mb)
        if ba and bb2:
            ma = ma[ba[1]:ba[1] + ba[3], ba[0]:ba[0] + ba[2]]
            mb = mb[bb2[1]:bb2[1] + bb2[3], bb2[0]:bb2[0] + bb2[2]]
    H = max(ma.shape[0], mb.shape[0]); W = max(ma.shape[1], mb.shape[1])
    A = np.zeros((H, W), bool); B = np.zeros((H, W), bool)
    A[:ma.shape[0], :ma.shape[1]] = ma
    B[:mb.shape[0], :mb.shape[1]] = mb
    # S2：度量收敛到共享内核（唯一实现）
    iou, inter, union = _KIT.iou(A, B)
    a_only = int((A & ~B).sum()); b_only = int((B & ~A).sum())
    # 各轴剖面（每列/每行前景比例差）——给出"哪一段胖了/瘦了"
    col_a, col_b = A.mean(axis=0), B.mean(axis=0)
    row_a, row_b = A.mean(axis=1), B.mean(axis=1)
    over = np.zeros((H, W, 4), np.float32)
    over[:, :, 3] = 1.0
    over[:, :, 0] = np.where(A & B, 0.25, 0.0) + np.where(A & ~B, 1.0, 0.0)
    over[:, :, 1] = np.where(A & B, 0.35, 0.0)
    over[:, :, 2] = np.where(A & B, 0.30, 0.0) + np.where(B & ~A, 1.0, 0.0)
    outp = out or (os.path.splitext(pa)[0] + "_vs_" + os.path.splitext(os.path.basename(pb))[0] + "_diff.png")
    saved = _save(over, outp)
    return _j({"ok": True, "a": pa, "b": pb, "aligned_bbox": bool(align), "size": [W, H],
               "iou": (round(float(iou), 4) if iou is not None else None),
               "intersection_px": inter, "union_px": union, "a_only_px": a_only, "b_only_px": b_only,
               "worst_columns": [{"x": int(i), "delta": round(float(col_a[i] - col_b[i]), 4)}
                                 for i in np.argsort(-np.abs(col_a - col_b))[:5]],
               "worst_rows": [{"y": int(i), "delta": round(float(row_a[i] - row_b[i]), 4)}
                              for i in np.argsort(-np.abs(row_a - row_b))[:5]],
               "out": saved,
               "legend": {"white/gray": "重合", "red": "A 独有（多了）", "blue": "B 独有（少了）"},
               "note": "IoU 与像素数都是数字判据；叠加图给「哪里不一样」；对齐是按各自轮廓 bbox 归一"})

def img_rectify(path=None, quad=None, out=None, out_w=None, out_h=None, aspect=None,
                known_w_mm=None, invert=False, threshold=None):
    """四角点 → 矩形（**单应校正**）：把照片里的斜视四边形拉成正视矩形，之后再量尺寸才准。

    quad = [[x,y], [x,y], [x,y], [x,y]]，按「左上 → 右上 → 右下 → 左下」顺序给（图像像素坐标）。
    输出尺寸：给 out_w/out_h；或只给 out_w + aspect（宽/高）；给了 known_w_mm 则回 mm_per_px。

    纯 numpy：4 点 DLT 解 8 参数单应（把矩形坐标映射回原图）+ 双线性采样，**不依赖 OpenCV**。
    ⚠ 校正用的四点必须是**同一个平面上的矩形**（例如车侧面的轮距/车长矩形，或地面上的标定框）；
      斜视越强，四点的取点误差被放得越大 —— 建议先用 img_scan 看轮廓再取点。
    """
    import numpy as np
    if not path:
        return _j({"ok": False, "error": "给 path"})
    if not quad or len(quad) != 4:
        return _j({"ok": False, "error": "quad 必须是 4 个点 [[x,y]×4]（左上/右上/右下/左下）"})
    try:
        loaded = _load(path)          # ⚠ _load 返回 (arr, path)，不是裸数组
        arr = loaded[0] if isinstance(loaded, (tuple, list)) else loaded
        if arr is None:
            return _j({"ok": False, "error": "读不到图像: %s" % path})
        src_h, src_w = arr.shape[0], arr.shape[1]
        wo = int(out_w or 0)
        ho = int(out_h or 0)
        if wo <= 0 and ho <= 0:
            return _j({"ok": False, "error": "给 out_w（或 out_w+aspect / out_h）"})
        if wo <= 0:
            wo = int(round(ho * float(aspect or 1.0)))
        if ho <= 0:
            ho = int(round(wo / float(aspect))) if aspect else int(round(wo * 0.5))
        wo, ho = max(8, wo), max(8, ho)
        # 目标矩形角点（左上/右上/右下/左下）→ 原图对应四点，解「矩形→原图」的单应
        rect = [(0.0, 0.0), (float(wo - 1), 0.0), (float(wo - 1), float(ho - 1)), (0.0, float(ho - 1))]
        A, b = [], []
        for (u, v), (x, y) in zip(rect, quad):
            u, v, x, y = float(u), float(v), float(x), float(y)
            A.append([u, v, 1, 0, 0, 0, -u * x, -v * x]); b.append(x)
            A.append([0, 0, 0, u, v, 1, -u * y, -v * y]); b.append(y)
        h = np.linalg.solve(np.array(A, dtype=np.float64), np.array(b, dtype=np.float64))
        yy, xx = np.mgrid[0:ho, 0:wo].astype(np.float64)
        den = h[6] * xx + h[7] * yy + 1.0
        den = np.where(np.abs(den) < 1e-12, 1e-12, den)
        sx = (h[0] * xx + h[1] * yy + h[2]) / den
        sy = (h[3] * xx + h[4] * yy + h[5]) / den
        x0 = np.floor(sx).astype(np.int64); y0 = np.floor(sy).astype(np.int64)
        fx = (sx - x0)[..., None]; fy = (sy - y0)[..., None]
        x0c = np.clip(x0, 0, src_w - 2); y0c = np.clip(y0, 0, src_h - 2)
        x1c = x0c + 1; y1c = y0c + 1
        a00 = arr[y0c, x0c]; a01 = arr[y0c, x1c]; a10 = arr[y1c, x0c]; a11 = arr[y1c, x1c]
        outarr = (a00 * (1 - fx) * (1 - fy) + a01 * fx * (1 - fy) +
                  a10 * (1 - fx) * fy + a11 * fx * fy).astype(np.float32)
        op = out or (str(path).rsplit(".", 1)[0] + "_rect.png")
        p = _save(outarr, op)
        res = {"ok": True, "out": p, "out_size": [wo, ho], "src_size": [src_w, src_h],
               "homography_rect_to_src": [round(float(t), 6) for t in h],
               "note": "校正后的图直接喂 vehicle_sections / img_scan 才准；斜视越小越准"}
        if known_w_mm:
            res["mm_per_px"] = round(float(known_w_mm) / float(wo), 6)
        return _j(res)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})

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
        iou, _i, _u = _KIT.iou(sub, mir)
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
def _hough_lines(gray, m_region, max_lines=12, point_cap=60000, min_span=8.0):
    """返回 [{theta_deg,rho_px,endpoints_px,inliers,span_px,residual_px,confidence,direction_deg}]。

    θ 是**法向角**（x·cosθ + y·sinθ = ρ）；direction_deg = (θ-90) mod 180 是线方向，便于人读。
    """
    H, W = gray.shape
    gx, gy = _sobel(gray)
    mag = np.hypot(gx, gy)
    region = _dilate(np.asarray(m_region, dtype=bool), 2)
    vals = mag[region]
    if vals.size == 0:
        return []
    # 阈值取 p85 的**一半**：合成/平色图的边缘梯度是量化平台（p85 恰好落在平台上），
    # 用 mag > p85 会把所有直边都排除掉（实测 45° 杆 n_edge 只剩 8）
    thr = max(0.02, 0.5 * float(np.percentile(vals, 85)))
    ys, xs = np.nonzero((mag > thr) & region)
    n = int(len(xs))
    if n < 10:
        return []
    if n > point_cap:
        pick = np.linspace(0, n - 1, point_cap).astype(np.int64)
        xs = xs[pick]
        ys = ys[pick]
        n = point_cap
    n_theta = 360
    theta = np.deg2rad(np.arange(n_theta, dtype=np.float64) * 0.5)
    ct = np.cos(theta).astype(np.float32)
    st = np.sin(theta).astype(np.float32)
    diag = float(math.hypot(W, H))
    n_rho = int(2.0 * diag) + 5
    off = int(diag) + 2
    hist = np.zeros(n_theta * n_rho, dtype=np.float64)
    xf = xs.astype(np.float32)
    yf = ys.astype(np.float32)
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
        return []
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
    out = []
    for (ti_, ri_, votes) in peaks:
        th0 = math.radians(ti_ * 0.5)
        rho0 = float(ri_ - off)
        c0, s0 = math.cos(th0), math.sin(th0)
        dist = np.abs(xf * c0 + yf * s0 - rho0)
        sel = dist <= 2.0
        if int(sel.sum()) < 8:
            continue
        px = xf[sel].astype(np.float64)
        py = yf[sel].astype(np.float64)
        mx, my = float(px.mean()), float(py.mean())
        cov = np.cov(np.stack([px - mx, py - my]))
        try:
            evals, evecs = np.linalg.eigh(cov)
        except Exception:
            continue
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
            continue
        # 用内点重新拟合（一次迭代）
        pinx = px[inl]
        piny = py[inl]
        mx2, my2 = float(pinx.mean()), float(piny.mean())
        cov2 = np.cov(np.stack([pinx - mx2, piny - my2]))
        try:
            ev2, evec2 = np.linalg.eigh(cov2)
        except Exception:
            continue
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
            continue
        e1 = [mx2 + lo * math.cos(dir2), my2 + lo * math.sin(dir2)]
        e2 = [mx2 + hi * math.cos(dir2), my2 + hi * math.sin(dir2)]
        density = float(inl.sum()) / max(1.0, span)
        conf = float(np.clip(density, 0.0, 1.0) * math.exp(-resid / 2.0))
        theta_deg = math.degrees(thn2) % 180.0
        out.append({"theta_deg": round(theta_deg, 2), "rho_px": round(rho2, 2),
                    "direction_deg": round((theta_deg - 90.0) % 180.0, 2),
                    "endpoints_px": [_rnd(e1, 2), _rnd(e2, 2)],
                    "inliers": int(inl.sum()), "span_px": round(span, 2),
                    "residual_px": round(resid, 3), "confidence": round(conf, 3),
                    "_votes": votes})
    # 近重复合并（同向、同 rho、长度相近 → 留 span 大的）
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


def _choose_scale_bar(ppm, W):
    """选比例尺长度：有标定优先整 mm 且像素落在 50..0.35W；无标定给 1/4 宽（px 单位）。"""
    if ppm and float(ppm) > 0:
        for mm_v in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000, 2000, 5000):
            px = float(mm_v) * float(ppm)
            if 50.0 <= px <= 0.35 * float(W):
                return px, float(mm_v), "mm"
        mm_v = 1.0
        while float(mm_v) * float(ppm) < 40.0 and mm_v < 100000.0:
            mm_v *= 10.0
        return float(mm_v) * float(ppm), float(mm_v), "mm"
    return float(max(20.0, min(0.25 * float(W), 120.0))), None, "px"


def _overlay_digest(arr, bbox, features, lines, scale, scale_bar=True, grid=True):
    """把量到的框/圆/线/网格/比例尺画回图；文字留空，legend 回 JSON（模型读数字即可）。"""
    out = arr.copy()
    H, W = out.shape[0], out.shape[1]
    legend = {"coordinate_system": "left-top origin, x right, y down",
              "boxes": [], "circles": [], "lines": [], "grid": None, "scale_bar": None}
    if grid:
        step = int(max(16, round(max(W, H) / 24.0)))
        col = [0.1, 0.55, 0.65]
        for x in range(0, W, max(1, step)):
            out[::2, x, :3] = col
        for y in range(0, H, max(1, step)):
            out[y, ::2, :3] = col
        legend["grid"] = {"step_px": step, "rgb": col, "note": "一个格 = step_px 像素"}
    if bbox:
        _draw_box(out, bbox, PALETTE[3], 2)
        legend["boxes"].append({"id": 0, "kind": "silhouette_bbox",
                                "box_px": [int(v) for v in bbox], "rgb": PALETTE[3]})
    for i, f in enumerate((features or [])[:10]):
        col = PALETTE[(i + 1) % len(PALETTE)]
        g = f.get("geom") or {}
        if g.get("bbox_px"):
            _draw_box(out, g["bbox_px"], col, 2)
            legend["boxes"].append({"id": i + 1, "kind": f.get("kind"),
                                    "box_px": [int(v) for v in g["bbox_px"]], "rgb": col})
        elif g.get("center_px") and (g.get("radius_px") or g.get("diameter_px")):
            r = float(g.get("radius_px") or (float(g.get("diameter_px") or 0.0) / 2.0))
            _draw_circle(out, g["center_px"], r + 1.0, col, 1)
            legend["circles"].append({"id": i + 1, "kind": f.get("kind"),
                                      "center_px": _rnd(g["center_px"], 2),
                                      "radius_px": round(r, 2), "rgb": col})
        elif g.get("xy_px"):
            x, y = int(g["xy_px"][0]), int(g["xy_px"][1])
            _draw_line(out, [x - 5, y], [x + 5, y], col, 1)
            _draw_line(out, [x, y - 5], [x, y + 5], col, 1)
            legend["lines"].append({"id": i + 1, "kind": f.get("kind"),
                                    "at_px": [x, y], "rgb": col})
    for i, ln in enumerate((lines or [])[:6]):
        col = PALETTE[(i + 2) % len(PALETTE)]
        pts = ln.get("endpoints_px")
        if pts and len(pts) == 2:
            _draw_line(out, pts[0], pts[1], col, 2)
            legend["lines"].append({"id": "L%d" % (i + 1), "theta_deg": ln.get("theta_deg"),
                                    "span_px": ln.get("span_px"), "rgb": col})
    if scale_bar:
        px_len, mm_v, unit = _choose_scale_bar((scale or {}).get("px_per_mm"), W)
        y = int(H - 12)
        x0 = 12
        col = [1.0, 0.1, 1.0]
        _draw_line(out, [x0, y], [x0 + px_len, y], col, 2)
        _draw_line(out, [x0, y - 5], [x0, y + 5], col, 2)
        _draw_line(out, [x0 + px_len, y - 5], [x0 + px_len, y + 5], col, 2)
        legend["scale_bar"] = {"x0_px": x0, "y_px": y, "length_px": round(px_len, 2),
                               "mm": mm_v, "unit": unit, "rgb": col,
                               "note": "这条横线的长度就是上面的 mm（无标定时是像素参考）"}
    return out, legend


def _digest_dir(p, out_dir):
    base = os.path.dirname(str(p)) or "."
    stem = os.path.splitext(os.path.basename(str(p)))[0] or "img"
    d = out_dir if out_dir else os.path.join(base, stem + "_digest")
    return str(d), stem


def _write_digest_crops(arr, bbox, features, rois, out_dir, stem, crop_max_px):
    """原生分辨率裁剪（**不缩放**）：优先用户 ROI，其次特征邻域，最后轮廓 bbox；单边 ≤crop_max_px。"""
    H, W = arr.shape[0], arr.shape[1]
    max_side = max(16, int(crop_max_px))
    boxes = []
    for i, r in enumerate(rois or []):
        box = _norm_roi(r, W, H)
        if box:
            boxes.append(("roi%d" % (i + 1), box))
    for i, f in enumerate((features or [])[:4]):
        g = f.get("geom") or {}
        if g.get("bbox_px"):
            bx = [int(v) for v in g["bbox_px"]]
            pad = max(6, int(0.35 * max(bx[2], bx[3])))
            boxes.append(("feat%d_%s" % (i + 1, f.get("kind") or "f"),
                          [bx[0] - pad, bx[1] - pad, bx[2] + 2 * pad, bx[3] + 2 * pad]))
        elif g.get("center_px") and (g.get("radius_px") or g.get("diameter_px")):
            cx, cy = float(g["center_px"][0]), float(g["center_px"][1])
            d = float(g.get("diameter_px") or (2.0 * float(g.get("radius_px") or 6.0)))
            side = max(24.0, d * 2.4)
            boxes.append(("feat%d_%s" % (i + 1, f.get("kind") or "circle"),
                          [cx - side / 2.0, cy - side / 2.0, side, side]))
    if bbox and not boxes:
        boxes.append(("bbox", [bbox[0], bbox[1], bbox[2], bbox[3]]))
    rows = []
    for (name, bx) in boxes[:6]:
        try:
            x, y, w, h = [float(v) for v in bx]
        except Exception:
            continue
        if w > max_side:
            x = x + (w - max_side) / 2.0
            w = float(max_side)
        if h > max_side:
            y = y + (h - max_side) / 2.0
            h = float(max_side)
        x0 = int(max(0, min(W - 1, round(x))))
        y0 = int(max(0, min(H - 1, round(y))))
        x1 = int(max(x0 + 1, min(W, round(x + w))))
        y1 = int(max(y0 + 1, min(H, round(y + h))))
        row = {"name": str(name), "box_px": [x0, y0, x1 - x0, y1 - y0], "scale": 1.0,
               "size_px": [int(x1 - x0), int(y1 - y0)], "path": None}
        try:
            row["path"] = _save(arr[y0:y1, x0:x1], os.path.join(out_dir, "%s_%s.png" % (stem, name)))
        except Exception as e:
            row["error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
        rows.append(row)
    return rows


def _number(key, value, unit, err, conf, method, src):
    return {"key": str(key), "value": _rnd(value, 4), "unit": str(unit), "err": _rnd(err, 4),
            "confidence": _rnd(conf, 3), "method": str(method), "src": src}


def _view_count(views):
    if views is None:
        return None
    if isinstance(views, str):
        return 1
    try:
        return len(list(views))
    except Exception:
        return None


def img_digest(path, views=None, known_len=None, scale_bar=True, rois=None, invert=False,
               threshold=None, max_px=16000000, out_dir=None, crop_max_px=900, attach=1):
    """**一次量完参考图**：轮廓/对称/直线/特征/比例尺/可读性 + 原生裁剪图册 + 标注总图 + verdict。

    输出纪律：没有 known_len ⇒ 所有 mm 字段为 null（**绝不编**）；px 是 loaded 尺度（原图超 max_px
    会先降采样，用 image.native_px / loaded_px 换算回原生）。verdict ∈ ready_for_spec / needs_scale /
    needs_views / unreadable / degraded。
    """
    t0 = time.time()
    if path is None or str(path).strip() == "":
        return _j({"ok": False, "error": "path 必填（参考图路径）",
                   "hint": "img_digest(path='D:/ref/front.png', known_len={'mm':100,'p1_px':[100,80],'p2_px':[420,80]})"})
    path = str(path)
    try:
        max_px = int(max_px) if max_px else 0
    except Exception:
        return _j({"ok": False, "error": "max_px 必须是整数", "max_px": max_px})
    try:
        crop_max_px = int(crop_max_px or 900)
    except Exception:
        return _j({"ok": False, "error": "crop_max_px 必须是整数", "crop_max_px": crop_max_px})
    if known_len is not None:
        known_items = _parse_known_lengths(known_len)
        if not known_items:
            return _j({"ok": False, "error": "known_len 解析不出 (px, mm) 对",
                       "hint": "给 {'mm':100,'px':250} 或 {'mm':100,'p1_px':[x,y],'p2_px':[x,y]}（可列表）",
                       "known_len": known_len})
    else:
        known_items = []
    try:
        arr, p, native_px, loaded_px = _load_ex(path, max_px=max_px)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "path": path})
    H, W = int(arr.shape[0]), int(arr.shape[1])
    md5 = _md5_cached(p)
    image_id = md5[:12]
    image_block = {"path": p, "md5": md5, "image_id": image_id,
                   "native_px": [int(native_px[0]), int(native_px[1])],
                   "loaded_px": [W, H],
                   "downscaled": bool(int(native_px[0]) * int(native_px[1]) > W * H),
                   "max_px": int(max_px)}
    m, g, source, conf, minfo = _mask_v2(arr, invert=invert, threshold=threshold)
    if int(m.sum()) == 0:
        return _j({"ok": False, "error": "没有分出前景（掩膜为空）", "verdict": "unreadable",
                   "image": image_block,
                   "hint": "照片先抠图（rembg）或给 threshold；alpha 图检查透明通道是否有效"})
    cc, cc_frac, cc_info = _largest_cc_mask(m)
    bbox = _bbox(cc) if int(cc.sum()) > 0 else None
    if bbox is None or int(cc.sum()) < 16:
        return _j({"ok": False, "error": "前景太小/太散（最大连通域 <16px）", "verdict": "unreadable",
                   "image": image_block, "hint": "确认参考图是目标物特写；或给 rois 只量一块"})
    holes_raw, filled = _holes_of(cc, bbox)
    if filled is None:
        filled = cc
    contour = _trace_contour(filled)
    poly = _simplify_closed(contour, 64) if contour is not None else None
    corners = _curvature_landmarks(poly) if poly is not None else []
    pcx, pcy, pdeg = _principal_axis(filled)
    crop_filled = filled[bbox[1]:bbox[1] + bbox[3], bbox[0]:bbox[0] + bbox[2]]
    fill_ratio = float(crop_filled.mean()) if crop_filled.size else 0.0
    aspect = float(bbox[2]) / float(max(1, bbox[3]))
    cand_hull_src = contour if contour is not None else np.column_stack(np.nonzero(filled)[::-1])
    hull_pts, hull_area, hull_per = _hull(cand_hull_src)
    outline = ([[int(round(float(xy[0]))), int(round(float(xy[1])))] for xy in np.asarray(poly)[:64].tolist()]
               if poly is not None else [])
    silhouette = {"bbox_px": [int(v) for v in bbox], "aspect_wh": round(aspect, 4),
                  "fill_ratio": round(fill_ratio, 4),
                  "principal_axis_deg": (round(float(pdeg), 2) if pdeg is not None else None),
                  "centroid_px": _rnd([pcx, pcy], 2),
                  "hull_px": hull_pts[:24], "hull_area_px": round(hull_area, 2),
                  "hull_perimeter_px": round(hull_per, 2),
                  "outline_px": outline, "outline_points": int(len(poly) if poly is not None else 0),
                  "contour_points": int(len(contour) if contour is not None else 0),
                  "curvature_landmarks": corners}
    sym = _symmetry(filled, bbox)
    es_p50 = _edge_strength_p50(g, cc)
    scale = _digest_scale(known_items)
    ppm = scale.get("px_per_mm")
    lines = _hough_lines(g, cc, max_lines=12)
    # 特征：孔/圆 + 角点；<8px 进 unreadable
    features = []
    unreadable_rows = []
    conf_r = float(conf) * (0.5 + 0.5 * float(cc_frac))
    for hi_, hrow in enumerate(holes_raw):
        hm = hrow.pop("mask", None)
        if hm is None:
            continue
        hys = np.nonzero(hm)[0] + int(bbox[1])
        hxs = np.nonzero(hm)[1] + int(bbox[0])
        fit = _circle_from_hole(hm, int(bbox[0]), int(bbox[1]))
        circular = bool(fit and fit.get("is_circle") and int(fit["n_px"]) >= 6)
        if circular:
            dpx = float(fit["diameter_px"])
            geom = {"center_px": _rnd(fit["center_px"], 2), "radius_px": round(float(fit["radius_px"]), 3),
                    "diameter_px": round(dpx, 3), "residual_px": round(float(fit["residual_px"]), 3),
                    "kasa_radius_px": round(float(fit["kasa_radius_px"]), 3),
                    "n_edge_px": int(fit["n_edge_px"]),
                    "area_px": int(hrow["area_px"])}
            kind = "circle"
            conf_f = conf_r * max(0.2, min(1.0, 1.0 - float(fit["residual_px"]) / 2.0))
            size_px = dpx
            mm = ({"diameter_mm": round(dpx / ppm, 4)} if ppm else None)
        else:
            bx = hrow["bbox_px"]
            geom = {"bbox_px": [int(v) for v in bx], "center_px": hrow["center_px"],
                    "area_px": int(hrow["area_px"]),
                    "residual_px": (round(float(fit["residual_px"]), 3) if fit else None)}
            kind = "hole"
            conf_f = conf_r * 0.5
            size_px = float(max(bx[2], bx[3]))
            mm = ({"width_mm": round(bx[2] / ppm, 4), "height_mm": round(bx[3] / ppm, 4)} if ppm else None)
        readable = bool(size_px >= MIN_READABLE_PX)
        key = "hole%d" % (hi_ + 1)
        why = "size %.1f px %s 8px" % (size_px, ">=" if readable else "<")
        features.append({"key": key, "kind": kind, "geom": geom, "mm": mm,
                         "size_px": round(size_px, 3), "readable": readable,
                         "confidence": round(float(conf_f), 3), "why": why})
        if not readable:
            unreadable_rows.append({"feature": key, "kind": kind, "size_px": round(size_px, 2),
                                    "why": "<8px：计数/形状不可靠，不要写进 spec"})
    for ci_, corner in enumerate(corners[:6]):
        el = corner.get("edge_len_px") or [0.0, 0.0]
        size_px = float(min(el[0], el[1])) if len(el) >= 2 else 0.0
        readable = bool(size_px >= MIN_READABLE_PX)
        mm = ({"edge_a_mm": round(float(el[0]) / ppm, 4), "edge_b_mm": round(float(el[1]) / ppm, 4)}
              if ppm else None)
        features.append({"key": "corner%d" % (ci_ + 1), "kind": "corner",
                         "geom": {"xy_px": corner["xy_px"], "turn_deg": corner["turn_deg"],
                                  "edge_len_px": el},
                         "mm": mm, "size_px": round(size_px, 2), "readable": readable,
                         "confidence": round(float(conf_r * min(1.0, float(corner["turn_deg"]) / 90.0)), 3),
                         "why": "turn %.0f deg, short edge %.1f px" % (corner["turn_deg"], size_px)})
        if not readable:
            unreadable_rows.append({"feature": "corner%d" % (ci_ + 1), "kind": "corner",
                                    "size_px": round(size_px, 2),
                                    "why": "<8px：角点位置受离散化影响，别当精确特征"})
    readability = [{"feature": f["key"], "kind": f["kind"], "size_px": f["size_px"],
                    "threshold_px": MIN_READABLE_PX, "readable": bool(f["readable"]),
                    "verdict": ("readable" if f["readable"] else "unreadable"),
                    "why": f.get("why")} for f in features]
    # 数字表（全部带 provenance）
    numbers = []
    src_bbox = {"image_md5": md5, "px_points": [[int(bbox[0]), int(bbox[1])],
                                                [int(bbox[0] + bbox[2] - 1), int(bbox[1] + bbox[3] - 1)]],
                "px_per_mm": ppm}
    numbers.append(_number("width_px", bbox[2], "px", 0.5, conf_r, "silhouette_bbox_pixels", src_bbox))
    numbers.append(_number("height_px", bbox[3], "px", 0.5, conf_r, "silhouette_bbox_pixels", src_bbox))
    numbers.append(_number("aspect_wh", aspect, "1", None, conf_r, "bbox_w/bbox_h", src_bbox))
    numbers.append(_number("fill_ratio", fill_ratio, "1", None, conf_r, "filled_silhouette/bbox_area", src_bbox))
    if pdeg is not None:
        numbers.append(_number("principal_axis_deg", pdeg, "deg", None, conf_r, "image_moments", src_bbox))
    if ppm:
        err_w = (0.5 / ppm) + float(scale.get("err_mm_at_300px") or 0.0) * float(bbox[2]) / 300.0
        err_h = (0.5 / ppm) + float(scale.get("err_mm_at_300px") or 0.0) * float(bbox[3]) / 300.0
        numbers.append(_number("width_mm", float(bbox[2]) / ppm, "mm", err_w, conf_r,
                               "bbox_w_px/px_per_mm", src_bbox))
        numbers.append(_number("height_mm", float(bbox[3]) / ppm, "mm", err_h, conf_r,
                               "bbox_h_px/px_per_mm", src_bbox))
        for f in features[:6]:
            g2 = f.get("geom") or {}
            if g2.get("diameter_px"):
                dpx = float(g2["diameter_px"])
                numbers.append(_number(f["key"] + "_diameter_mm", dpx / ppm, "mm",
                                       (0.5 / ppm) + float(scale.get("err_mm_at_300px") or 0.0) * dpx / 300.0,
                                       f.get("confidence"), "kasa_circle_diameter/px_per_mm",
                                       {"image_md5": md5, "px_points": g2.get("center_px"),
                                        "px_per_mm": ppm}))
        for i, ln in enumerate(lines[:3]):
            numbers.append(_number("line%d_span_mm" % (i + 1), float(ln["span_px"]) / ppm, "mm",
                                   (0.5 / ppm) + float(scale.get("err_mm_at_300px") or 0.0) * float(ln["span_px"]) / 300.0,
                                   ln.get("confidence"), "hough_line_span/px_per_mm",
                                   {"image_md5": md5, "px_points": ln.get("endpoints_px"), "px_per_mm": ppm}))
    # ROI 统计
    roi_rows = []
    for i, r in enumerate(rois or []):
        box = _norm_roi(r, W, H)
        if not box:
            roi_rows.append({"roi": r, "error": "bad roi"})
            continue
        sub = m[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
        roi_rows.append({"roi": box, "fg_frac": round(float(sub.mean()), 4), "fg_px": int(sub.sum())})
    # 裁剪 + 标注总图
    ddir, stem = _digest_dir(p, out_dir)
    try:
        crops = _write_digest_crops(arr, bbox, features, rois, ddir, stem, crop_max_px)
    except Exception as e:
        crops = [{"error": "%s: %s" % (type(e).__name__, str(e)[:120]), "why": "裁剪目录写不进去"}]
    overlay = None
    overlay_path = None
    try:
        ov_arr, ov_legend = _overlay_digest(arr, bbox, features, lines, scale,
                                            scale_bar=bool(scale_bar))
        overlay_path = _save(ov_arr, os.path.join(ddir, stem + "_overlay.png"))
        overlay = {"path": overlay_path, "labels": ov_legend}
    except Exception as e:
        overlay = {"path": None, "labels": {}, "warning": "%s: %s" % (type(e).__name__, str(e)[:120])}
    try:
        attach_n = int(attach or 0)
    except Exception:
        attach_n = 0
    attach_paths = []
    if overlay_path:
        attach_paths.append(overlay_path)
    attach_paths.extend([c.get("path") for c in crops if c.get("path")])
    attach_paths = attach_paths[:max(0, attach_n)]
    # verdict + 未知/待确认/下一步
    unknown, to_confirm, nexts = [], [], []
    n_views = _view_count(views)
    if ppm is None:
        verdict = "needs_scale"
        unknown.append("px_per_mm 未标定：所有 mm 字段为 null（不猜）")
        nexts.append("给 known_len（标尺两端点或已知边长）重跑 img_digest，或先拍标尺")
    elif n_views == 1:
        verdict = "needs_views"
        nexts.append("再补一个正交视图（front/side/top）后重跑，verdict 才会到 ready_for_spec")
    elif float(cc_frac) < 0.5 or float(conf) < 0.3 or float(filled.mean()) > 0.98:
        verdict = "degraded"
        to_confirm.append("掩膜可疑（最大连通域占 %.0f%%，置信度 %.2f）：先抠图或给 threshold 再量"
                          % (cc_frac * 100.0, conf))
    elif contour is None or len(poly) < 3:
        verdict = "degraded"
        to_confirm.append("轮廓跟踪失败：图可能过小/过碎")
    else:
        verdict = "ready_for_spec"
        nexts.append("mm 数可直接进 spec.py；用 img_measure 对期望值出 delta 表")
    if int(native_px[0]) * int(native_px[1]) > W * H:
        unknown.append("原图 %dx%d > max_px，已降采样到 %dx%d：px 坐标是 loaded 尺度（native = loaded × %s）"
                       % (native_px[0], native_px[1], W, H,
                          round(float(native_px[0]) / float(W), 4)))
        to_confirm.append("关键特征请用 crops（原生裁剪、不缩放）复核")
    if float(cc_frac) < 0.85:
        to_confirm.append("前景分成多块（最大连通域占 %.0f%%）：可能有分离件或被遮断" % (cc_frac * 100.0))
    if float(conf) < 0.5:
        to_confirm.append("掩膜置信度 %.2f 偏低：照片先抠图（rembg）或显式给 threshold" % float(conf))
    if not lines:
        to_confirm.append("没有稳定直线：图可能模糊/低对比，或全是曲线")
    if not holes_raw:
        unknown.append("没检到内部孔洞（若有孔，可能被 alpha 吞掉或对比不足）")
    if unreadable_rows:
        to_confirm.append("有 %d 个特征 <8px：计数/形状不可靠，别写进 spec" % len(unreadable_rows))
    if sym.get("score") is not None and float(sym["score"]) < 0.8:
        unknown.append("对称性弱（mirror IoU=%.2f）：可能不是镜像件，或取景有透视" % float(sym["score"]))
    nexts.append("要某高度宽度：img_probe(op='scan_y', y_px=…)；要圆孔：img_probe(op='circle', x_px=…, y_px=…)")
    # 缓存 + image_id 注册（img_probe / img_measure / img_calib 复用）
    entry = {"md5": md5, "path": p, "shape": [W, H],
             "native_px": [int(native_px[0]), int(native_px[1])],
             "gray": g, "mask": cc, "filled": filled, "bbox_px": [int(v) for v in bbox],
             "source": source, "confidence": round(float(conf), 4), "px_per_mm": ppm,
             "silhouette": silhouette, "features": features}
    try:
        _cache_put(entry)
        _register_id(image_id, entry, silhouette, features, numbers)
    except Exception:
        pass
    return _j({
        "ok": True, "version": IMG_VERSION, "verdict": verdict,
        "image": image_block,
        "mask": {"source": source, "fg_frac": round(float(m.mean()), 4),
                 "confidence": round(float(conf), 4),
                 "self_check": {"largest_cc_frac": round(float(cc_frac), 4),
                                "holes": int(len(holes_raw)),
                                "edge_strength_p50": round(float(es_p50), 3)},
                 "components": int(cc_info.get("n_components") or 0), "info": minfo},
        "silhouette": silhouette,
        "symmetry": sym,
        "lines": lines,
        "features": features,
        "scale": scale,
        "numbers": numbers,
        "readability": readability,
        "unreadable": unreadable_rows,
        "rois": (roi_rows if roi_rows else None),
        "crops": crops,
        "overlay": overlay,
        "attach": attach_paths,
        "unknown": unknown, "to_confirm": to_confirm, "next": nexts,
        "provenance": {"image_md5": md5, "image_id": image_id, "method": "alpha/border_otsu mask + "
                       "moore contour/DP + sobel-hough + kasa circle + least-squares scale",
                       "px_per_mm": ppm, "coordinate_system": "left-top origin; px are loaded_px scale",
                       "elapsed_ms": round((time.time() - t0) * 1000.0, 1)},
        "honest_limits": ["不用深度学习抠图：复杂照片请先 rembg 或给 threshold",
                          "Hough 只抓强直线；曲线/弱对比边不出线",
                          "无 known_len 时 mm 全为 null（绝不靠猜）",
                          "对称性只测垂直/水平镜像轴"],
    })


# ── img_probe：单点/单线量测（亚像素边缘、圆孔、距离）────────────────────
def _subpixel_edge(prof, i_edge):
    """梯度峰值三点抛物线 → 亚像素边界位置（边界在像素 i 与 i+1 之间时返回 i+0.5±0.5）。"""
    prof = np.asarray(prof, dtype=np.float64)
    n = int(len(prof))
    if n < 4:
        return None
    try:
        i_edge = int(i_edge)
    except Exception:
        return None
    d = np.diff(prof)
    m = np.abs(d)
    if m.size < 3:
        return None
    lo = max(1, i_edge - 3)
    hi = min(len(m) - 2, i_edge + 2)
    if hi < lo:
        return None
    k = lo + int(np.argmax(m[lo:hi + 1]))
    a = float(m[k - 1])
    b = float(m[k])
    c = float(m[k + 1]) if k + 1 < len(m) else b
    den = a - 2.0 * b + c
    off = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
    off = max(-0.5, min(0.5, off))
    return float(k) + 0.5 + off


def _runs_1d(row, x0):
    """一维布尔 → [(start_abs, end_abs)]（含端点）。"""
    row = np.asarray(row, dtype=bool)
    if row.size == 0:
        return []
    pad = np.zeros(row.size + 2, dtype=np.int8)
    pad[1:-1] = row.astype(np.int8)
    d = np.diff(pad)
    starts = np.flatnonzero(d == 1) + int(x0)
    ends = np.flatnonzero(d == -1) - 1 + int(x0)
    return [(int(s), int(e)) for s, e in zip(starts.tolist(), ends.tolist())]


def img_probe(image_id=None, path=None, op="scan_y", roi=None, y_px=None, x_px=None,
              p1_px=None, p2_px=None):
    """按需量测（读缓存，热路径快）：scan_y / scan_x 亚像素左右(上下)边缘与多段；circle/holes 圆孔；
    distance 两点距离；profile 灰度剖面。px_per_mm 只认 img_digest 的标定（不编）。"""
    t0 = time.time()
    op = str(op or "scan_y").strip().lower()
    ops_doc = {"scan_y": "水平扫描线：y_px 处左右边缘/宽度/多段（亚像素）",
               "scan_x": "垂直扫描线：x_px 处上下边缘/高度/多段（亚像素）",
               "circle": "圆孔：x_px/y_px 附近或 roi 内最大孔，Kasa 拟合直径/残差",
               "holes": "roi 内全部孔洞的圆拟合列表",
               "distance": "p1_px↔p2_px 距离（含 mm，若已标定）",
               "profile": "沿扫描线的灰度/掩膜剖面（降采样 ≤256 点）"}
    if op in ("help", "ops", "list"):
        return _j({"ok": True, "ops": ops_doc, "version": IMG_VERSION})
    ent, err = _img_entry(image_id, path)
    if ent is None:
        return _j({"ok": False, "error": err,
                   "hint": "先 img_digest(path=…)（会缓存 mask/gray），或直接给 path"})
    H, W = ent["mask"].shape
    ppm = ent.get("px_per_mm")
    prov = {"image_md5": ent.get("md5"), "path": ent.get("path"), "px_per_mm": ppm, "shape_px": [W, H]}
    box = _norm_roi(roi, W, H)
    if box is None:
        return _j({"ok": False, "error": "roi 坏：需要 [x,y,w,h] 且 w/h>0 且与图相交",
                   "roi": roi, "shape_px": [W, H]})
    x0, y0, bw, bh = box
    out = {"ok": True, "version": IMG_VERSION, "op": op, "provenance": prov, "roi": box}
    if op in ("scan_y", "scan_x"):
        if op == "scan_y":
            pos = int(round(float(y_px))) if y_px is not None else int(y0 + bh // 2)
            lim = H
            if not (0 <= pos < lim):
                return _j({"ok": False, "error": "y_px 越界", "y_px": y_px, "shape_px": [W, H]})
            line = ent["mask"][pos, :]
            prof = ent["gray"][pos, :]
            runs = _runs_1d(line[x0:x0 + bw], x0)
            segs = []
            for (s, e) in runs:
                lsub = _subpixel_edge(prof, int(s))
                rsub = _subpixel_edge(prof, int(e))
                segs.append({"x0_px": (lsub if lsub is not None else float(s) - 0.5),
                             "x1_px": (rsub if rsub is not None else float(e) + 0.5),
                             "width_px": ((rsub if rsub is not None else float(e) + 0.5)
                                          - (lsub if lsub is not None else float(s) - 0.5))})
            if segs:
                left = segs[0]["x0_px"]
                right = segs[-1]["x1_px"]
                out.update({"y_px": pos, "segments": segs, "n_segments": len(segs),
                            "left_px": _rnd(left, 3), "right_px": _rnd(right, 3),
                            "width_px": _rnd(right - left, 3), "empty": False,
                            "width_mm": (round((right - left) / ppm, 4) if ppm else None)})
            else:
                out.update({"y_px": pos, "segments": [], "n_segments": 0, "left_px": None,
                            "right_px": None, "width_px": None, "empty": True,
                            "note": "该扫描线没有前景（换 y_px 或检查 roi）"})
        else:
            pos = int(round(float(x_px))) if x_px is not None else int(x0 + bw // 2)
            lim = W
            if not (0 <= pos < lim):
                return _j({"ok": False, "error": "x_px 越界", "x_px": x_px, "shape_px": [W, H]})
            line = ent["mask"][:, pos]
            prof = ent["gray"][:, pos]
            runs = _runs_1d(line[y0:y0 + bh], y0)
            segs = []
            for (s, e) in runs:
                tsub = _subpixel_edge(prof, int(s))
                bsub = _subpixel_edge(prof, int(e))
                segs.append({"y0_px": (tsub if tsub is not None else float(s) - 0.5),
                             "y1_px": (bsub if bsub is not None else float(e) + 0.5),
                             "height_px": ((bsub if bsub is not None else float(e) + 0.5)
                                           - (tsub if tsub is not None else float(s) - 0.5))})
            if segs:
                top = segs[0]["y0_px"]
                bot = segs[-1]["y1_px"]
                out.update({"x_px": pos, "segments": segs, "n_segments": len(segs),
                            "top_px": _rnd(top, 3), "bottom_px": _rnd(bot, 3),
                            "height_px": _rnd(bot - top, 3), "empty": False,
                            "height_mm": (round((bot - top) / ppm, 4) if ppm else None)})
            else:
                out.update({"x_px": pos, "segments": [], "n_segments": 0, "top_px": None,
                            "bottom_px": None, "height_px": None, "empty": True})
    elif op in ("circle", "holes"):
        hbox = list(ent.get("bbox_px") or box)
        holes, _filled = _holes_of(ent["mask"], hbox)
        rows = []
        for hrow in holes:
            hm = hrow.pop("mask", None)
            if hm is None:
                continue
            fit = _circle_from_hole(hm, int(hbox[0]), int(hbox[1]))
            row = {"bbox_px": hrow["bbox_px"], "center_px": hrow["center_px"],
                   "area_px": int(hrow["area_px"])}
            if fit:
                dpx = float(fit["diameter_px"])
                row.update({"fit_center_px": _rnd(fit["center_px"], 3),
                            "radius_px": round(float(fit["radius_px"]), 3),
                            "diameter_px": round(dpx, 3),
                            "kasa_radius_px": round(float(fit["kasa_radius_px"]), 3),
                            "residual_px": round(float(fit["residual_px"]), 3),
                            "n_edge_px": int(fit["n_edge_px"]),
                            "is_circle": bool(fit.get("is_circle")),
                            "diameter_mm": (round(dpx / ppm, 4) if ppm else None)})
            rows.append(row)
        pick = None
        if op == "circle" and rows:
            if x_px is not None and y_px is not None:
                tx, ty = float(x_px), float(y_px)
                pick = min(rows, key=lambda r: (float(r["fit_center_px"][0]) - tx) ** 2
                           + (float(r["fit_center_px"][1]) - ty) ** 2) if rows and rows[0].get("fit_center_px") else None
            if pick is None:
                pick = max(rows, key=lambda r: float(r.get("diameter_px") or 0.0))
        out.update({"found": bool(len(rows)), "holes": rows[:12], "holes_count": len(rows)})
        if op == "circle":
            out["circle"] = pick
            if pick is None:
                out["hint"] = "roi/图里没检到内部孔洞；给更紧的 roi 或检查 alpha/对比"
    elif op == "distance":
        if p1_px is None or p2_px is None:
            return _j({"ok": False, "error": "distance 需要 p1_px 与 p2_px", "op": op})
        try:
            ax, ay = float(p1_px[0]), float(p1_px[1])
            bx, by = float(p2_px[0]), float(p2_px[1])
        except Exception:
            return _j({"ok": False, "error": "p1_px/p2_px 必须是 [x,y]", "p1_px": p1_px, "p2_px": p2_px})
        dist = math.hypot(bx - ax, by - ay)
        out.update({"p1_px": [ax, ay], "p2_px": [bx, by], "distance_px": _rnd(dist, 3),
                    "distance_mm": (round(dist / ppm, 4) if ppm else None)})
    elif op == "profile":
        pos = int(round(float(y_px))) if y_px is not None else int(y0 + bh // 2)
        if not (0 <= pos < H):
            return _j({"ok": False, "error": "y_px 越界", "y_px": y_px, "shape_px": [W, H]})
        prof = ent["gray"][pos, x0:x0 + bw]
        step = max(1, int(bw // 256))
        vals = prof[::step]
        mvals = ent["mask"][pos, x0:x0 + bw][::step]
        out.update({"axis": "x", "y_px": pos, "x0_px": x0, "step_px": step,
                    "gray": [round(float(v), 4) for v in vals.tolist()],
                    "mask": [int(1 if v else 0) for v in mvals.tolist()]})
    else:
        return _j({"ok": False, "error": "unknown probe op", "op": op, "ops": sorted(ops_doc)})
    out["cached"] = True
    out["elapsed_ms"] = round((time.time() - t0) * 1000.0, 2)
    return _j(out)


# ── img_measure：像素 → mm，并与期望比（rows + delta + verdict）──────────
def _feature_px(feature, ent):
    """特征名 → (px_len, label, p1, p2)。支持 bbox_w/bbox_h/bbox_diag/circle[:n]/number:<key>。"""
    if feature is None:
        return None, None, None, None
    f = str(feature).strip().lower().replace("-", "_").replace(" ", "_")
    sil = (ent or {}).get("silhouette") or {}
    bbox = sil.get("bbox_px") or (ent or {}).get("bbox_px")
    if bbox:
        x, y, w, h = [float(v) for v in bbox[:4]]
        if f in ("bbox_w", "width", "w", "outer_w"):
            return w, "bbox_w", [x, y], [x + w, y]
        if f in ("bbox_h", "height", "h", "outer_h"):
            return h, "bbox_h", [x, y], [x, y + h]
        if f in ("bbox_diag", "diag", "diagonal"):
            return math.hypot(w, h), "bbox_diag", [x, y], [x + w, y + h]
    if f.startswith("number:"):
        key = f.split(":", 1)[1]
        for nrow in (ent or {}).get("numbers") or []:
            if str(nrow.get("key")) == key and nrow.get("value") is not None and nrow.get("unit") == "px":
                return float(nrow["value"]), "number:" + key, None, None
        return None, None, None, None
    if f.startswith("circle") or f.startswith("hole") or f.startswith("feature"):
        parts = f.replace(":", "_").replace(".", "_").split("_")
        idx = 0
        for p in parts:
            if p.isdigit():
                idx = int(p)
                break
        feats = (ent or {}).get("features") or []
        cand = [ft for ft in feats
                if (ft.get("geom") or {}).get("diameter_px") or (ft.get("geom") or {}).get("radius_px")]
        if not cand:
            return None, None, None, None
        ft = cand[min(idx, len(cand) - 1)]
        g = ft.get("geom") or {}
        dpx = float(g.get("diameter_px") or (2.0 * float(g.get("radius_px") or 0.0)))
        c = g.get("center_px") or [0.0, 0.0]
        return dpx, "circle_diam", [float(c[0]) - dpx / 2.0, float(c[1])], [float(c[0]) + dpx / 2.0, float(c[1])]
    return None, None, None, None


def img_measure(image_id=None, path=None, px_per_mm=None, targets=None, pairs=None, out_dir=None):
    """把像素量换算成 mm，并与期望值比：rows[{key,value_mm,expect_mm,delta_mm,delta_pct,verdict}]。

    targets/pairs 每条：{key, p1_px, p2_px} 或 {key, px} 或 {key, feature:"bbox_w"|"circle:0"}，
    可选 expect_mm / expect_px / tol_mm / tol_pct（默认 2%）。没有 px_per_mm ⇒ ok:false + needs_scale。
    """
    ent = None
    ent_err = None
    if image_id is not None or (path is not None and str(path).strip() != ""):
        ent, ent_err = _img_entry(image_id, path)
        if ent is None:
            return _j({"ok": False, "error": ent_err,
                       "hint": "path 要存在；image_id 要先 img_digest 生成"})
    ppm = None
    scale_src = None
    if px_per_mm is not None:
        try:
            ppm = float(px_per_mm)
        except Exception:
            return _j({"ok": False, "error": "px_per_mm 必须是数字", "px_per_mm": px_per_mm})
        if ppm <= 0:
            return _j({"ok": False, "error": "px_per_mm 必须 > 0", "px_per_mm": px_per_mm})
        scale_src = "arg"
    elif ent is not None and ent.get("px_per_mm"):
        ppm = float(ent["px_per_mm"])
        scale_src = "cached_digest"
    if ppm is None:
        return _j({"ok": False, "error": "没有 px_per_mm：无法把像素换成 mm", "verdict": "needs_scale",
                   "hint": "先 img_digest(path=…, known_len={'mm':…,'p1_px':…,'p2_px':…}) 标定，或直接给 px_per_mm"})
    specs = []
    for src_list in (targets, pairs):
        if src_list is None:
            continue
        if isinstance(src_list, dict):
            for k, v in src_list.items():
                if isinstance(v, dict):
                    d = dict(v)
                    d.setdefault("key", k)
                    specs.append(d)
                else:
                    specs.append({"key": k, "px": v})
        elif isinstance(src_list, (list, tuple)):
            specs.extend(list(src_list))
    specs = [s for s in specs if s is not None]
    if not specs:
        return _j({"ok": False, "error": "targets / pairs 至少给一条（否则没有可量的东西）",
                   "hint": "targets=[{key:'width', p1_px:[x1,y1], p2_px:[x2,y2], expect_mm:160}]"})
    rows = []
    bad = 0
    for i, raw in enumerate(specs):
        d = raw if isinstance(raw, dict) else {}
        key = str(d.get("key") or d.get("id") or ("m%d" % (i + 1)))
        p1 = d.get("p1_px") or d.get("p1") or d.get("a")
        p2 = d.get("p2_px") or d.get("p2") or d.get("b")
        px_len = None
        method = None
        if d.get("px") is not None or d.get("dist_px") is not None:
            try:
                px_len = float(d.get("px") if d.get("px") is not None else d.get("dist_px"))
                method = "given_px"
            except Exception:
                px_len = None
        elif isinstance(d.get("points"), (list, tuple)) and len(d["points"]) >= 2:
            try:
                p1, p2 = d["points"][0], d["points"][1]
                px_len = math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))
                method = "two_points"
            except Exception:
                px_len = None
        elif p1 is not None and p2 is not None:
            try:
                px_len = math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))
                method = "two_points"
            except Exception:
                px_len = None
        elif d.get("feature") is not None:
            px_len, _lbl, fp1, fp2 = _feature_px(d["feature"], ent)
            if px_len is not None:
                method = "feature:" + str(d["feature"])
                p1 = p1 or fp1
                p2 = p2 or fp2
        if px_len is None:
            rows.append({"key": key, "verdict": "error",
                         "error": "解析不出像素长度（给 p1_px/p2_px、px、points 或 feature）"})
            bad += 1
            continue
        value_mm = float(px_len) / ppm
        expect_mm = None
        if d.get("expect_mm") is not None:
            try:
                expect_mm = float(d["expect_mm"])
            except Exception:
                expect_mm = None
        if expect_mm is None and d.get("expect_px") is not None:
            try:
                expect_mm = float(d["expect_px"]) / ppm
            except Exception:
                expect_mm = None
        delta_mm = None
        delta_pct = None
        verdict = "measured"
        why = None
        if expect_mm is not None:
            delta_mm = value_mm - expect_mm
            delta_pct = (100.0 * delta_mm / expect_mm) if expect_mm else None
            try:
                tol = abs(float(d["tol_mm"])) if d.get("tol_mm") is not None \
                    else abs(expect_mm) * float(d.get("tol_pct", 2.0)) / 100.0
            except Exception:
                tol = abs(expect_mm) * 0.02
            verdict = "pass" if abs(delta_mm) <= tol + 1e-9 else "fail"
            why = "|delta|=%.4f mm %s tol %.4f mm" % (abs(delta_mm), "<=" if verdict == "pass" else ">", tol)
        rows.append({"key": key, "value_px": round(float(px_len), 4),
                     "value_mm": round(value_mm, 4),
                     "expect_mm": (round(expect_mm, 4) if expect_mm is not None else None),
                     "delta_mm": (round(delta_mm, 4) if delta_mm is not None else None),
                     "delta_pct": (round(delta_pct, 3) if delta_pct is not None else None),
                     "verdict": verdict, "why": why, "method": method,
                     "p1_px": _rnd(p1, 3), "p2_px": _rnd(p2, 3)})
    fails = [r for r in rows if r.get("verdict") == "fail"]
    errors = [r for r in rows if r.get("verdict") == "error"]
    if errors or fails:
        verdict = "fail"
    elif any(r.get("verdict") == "pass" for r in rows):
        verdict = "pass"
    else:
        verdict = "measured"
    annotated = None
    if out_dir and ent is not None:
        try:
            arr, p2_, _n, _l = _load_ex(ent.get("path"), max_px=16000000)
            ov = arr.copy()
            for r in rows:
                if r.get("p1_px") and r.get("p2_px"):
                    _draw_line(ov, r["p1_px"], r["p2_px"], [1.0, 0.2, 0.2], 2)
            annotated = _save(ov, os.path.join(str(out_dir), "measure_annotated.png"))
        except Exception:
            annotated = None
    return _j({"ok": bool(verdict != "fail"), "verdict": verdict, "rows": rows, "count": len(rows),
               "provenance": {"image_md5": (ent or {}).get("md5"), "px_per_mm": ppm,
                              "scale_source": scale_src, "units": "mm",
                              "image_path": ((ent or {}).get("path") or (str(path) if path else None))},
               "annotated": annotated,
               "note": "expect_mm 给了才判 pass/fail；delta_pct 是相对期望值的百分比"})


# ── img_calib：多视逐视标定 + 跨视共享尺寸一致性 ────────────────────────
def img_calib(views=None, shared=None, auto_ortho=False, assert_same_scale=False):
    """多视标定：每视用已知长度做最小二乘 px_per_mm；shared 尺寸跨视换算成 mm 后两两比。

    views=[{id, known_len:[{mm,px}|{mm,p1_px,p2_px}], px_per_mm?}]；
    shared=[{key, mm?, views:{视名: 像素值|{px}|{p1_px,p2_px}}, tol_pct?, tol_mm?}]。
    不做自动消失点：perspective_score 恒 null / unresolved（要正交先 img_rectify 四角点校正）。
    """
    if views is None:
        return _j({"ok": False, "error": "views 必填（每视至少一个已知长度或 px_per_mm）",
                   "hint": "views=[{id:'front', known_len:[{mm:160, px:320}]}, {id:'side', known_len:[{mm:100,px:200}]}]"})
    if isinstance(views, dict):
        views = [views]
    if not isinstance(views, (list, tuple)) or len(views) == 0:
        return _j({"ok": False, "error": "views 必须是非空列表", "views": views})
    per = []
    ppm_by = {}
    cx_by = {}
    for i, v in enumerate(views):
        if not isinstance(v, dict):
            return _j({"ok": False, "error": "第 %d 个 view 不是 dict" % (i + 1), "view": v})
        name = str(v.get("id") or v.get("name") or ("view%d" % (i + 1)))
        items = _parse_known_lengths(v.get("known_len") or v.get("known") or v.get("lengths"))
        ppm = None
        n_points = 0
        resid_px = None
        resid_pct = None
        source = None
        why = None
        verdict = "needs_data"
        if v.get("px_per_mm") is not None:
            try:
                ppm = float(v["px_per_mm"])
            except Exception:
                ppm = None
            if ppm and ppm > 0:
                source = "given"
                n_points = len(items)
                verdict = "ok"
                why = "调用方直接给了 px_per_mm"
            else:
                ppm = None
        if ppm is None and items:
            fit = _fit_scale(items)
            if fit:
                ppm = float(fit["px_per_mm"])
                n_points = int(fit["n_points"])
                resid_px = fit.get("resid_px")
                resid_pct = fit.get("resid_pct")
                source = "ls_known_len"
                if resid_pct is not None and float(resid_pct) > 2.0:
                    verdict = "fail"
                    why = "多组已知长度不一致：残差 %.2f%% > 2%%（取点错/透视/不是同一平面）" % float(resid_pct)
                else:
                    verdict = "ok"
                    why = "最小二乘 px_per_mm=%.4f（%d 组已知长度）" % (ppm, n_points)
        if ppm is None:
            verdict = "needs_data"
            why = "没给 known_len / px_per_mm：这一视没法标定"
        mids = [it.get("mid") for it in items if it.get("mid")]
        cx = sum(float(m[0]) for m in mids) / float(len(mids)) if mids else None
        cy = sum(float(m[1]) for m in mids) / float(len(mids)) if mids else None
        if cx is None:
            try:
                if v.get("size_px") and len(v["size_px"]) >= 2:
                    cx, cy = float(v["size_px"][0]) / 2.0, float(v["size_px"][1]) / 2.0
            except Exception:
                pass
        per.append({"id": name, "px_per_mm": (round(ppm, 6) if ppm else None),
                    "cx_px": (round(cx, 2) if cx is not None else None),
                    "cy_px": (round(cy, 2) if cy is not None else None),
                    "n_points": int(n_points), "resid_px": resid_px, "resid_pct": resid_pct,
                    "verdict": verdict, "source": source, "why": why})
        if ppm:
            ppm_by[name] = float(ppm)
            cx_by[name] = cx
    # 跨视一致性
    shared_list = []
    if isinstance(shared, dict):
        shared_list = [shared]
    elif isinstance(shared, (list, tuple)):
        shared_list = [s for s in shared if isinstance(s, dict)]
    cons = {"pairwise": [], "mm_max_delta": None, "same_scale": None,
            "keys": [], "tol_pct": None}
    ppms = [p for p in ppm_by.values() if p]
    if len(ppms) >= 2:
        same = (max(ppms) / min(ppms)) <= 1.01
        cons["same_scale"] = bool(same)
        cons["ppm_min"] = round(min(ppms), 6)
        cons["ppm_max"] = round(max(ppms), 6)
        cons["ppm_spread_pct"] = round(100.0 * (max(ppms) / min(ppms) - 1.0), 3)
    inconsistent = False
    deltas = []
    for si, sh in enumerate(shared_list):
        key = str(sh.get("key") or sh.get("id") or ("shared%d" % (si + 1)))
        tol_pct = float(sh.get("tol_pct") or 1.0)
        tol_mm_arg = sh.get("tol_mm")
        expected = sh.get("mm", sh.get("expect_mm", sh.get("value_mm")))
        try:
            expected = float(expected) if expected is not None else None
        except Exception:
            expected = None
        vmap = sh.get("views") or sh.get("per_view") or {}
        if not isinstance(vmap, dict) or not vmap:
            vmap = {k: v for k, v in sh.items()
                    if k not in ("key", "id", "mm", "expect_mm", "value_mm", "tol_pct", "tol_mm")
                    and isinstance(v, (dict, int, float))}
        vals = {}
        for vn, spec in vmap.items():
            px = _spec_px(spec)
            ppm = ppm_by.get(str(vn))
            mm = (px / ppm) if (px is not None and ppm) else None
            vals[str(vn)] = {"px": (round(float(px), 3) if px is not None else None),
                             "mm": (round(float(mm), 4) if mm is not None else None)}
        names = list(vals.keys())
        combos = []
        for ai in range(len(names)):
            for bi_ in range(ai + 1, len(names)):
                a, b = names[ai], names[bi_]
                ma, mb = vals[a]["mm"], vals[b]["mm"]
                if ma is None or mb is None:
                    continue
                mean = 0.5 * (ma + mb)
                tol = abs(mean) * tol_pct / 100.0 if tol_mm_arg is None \
                    else max(abs(float(tol_mm_arg)), abs(mean) * tol_pct / 100.0)
                delta = ma - mb
                combo = {"key": key, "a": a, "b": b, "mm_a": ma, "mm_b": mb,
                         "delta_mm": round(delta, 4),
                         "delta_pct": (round(100.0 * delta / mean, 3) if mean else None),
                         "tol_mm": round(tol, 4),
                         "consistent": bool(abs(delta) <= tol + 1e-9)}
                combos.append(combo)
                deltas.append(abs(delta))
                if not combo["consistent"]:
                    inconsistent = True
        if expected is not None:
            for nm, vv in vals.items():
                if vv["mm"] is None:
                    continue
                delta = vv["mm"] - expected
                tol = abs(expected) * tol_pct / 100.0 if tol_mm_arg is None \
                    else max(abs(float(tol_mm_arg)), abs(expected) * tol_pct / 100.0)
                combo = {"key": key, "a": nm, "b": "expected", "mm_a": vv["mm"],
                         "mm_b": round(expected, 4), "delta_mm": round(delta, 4),
                         "delta_pct": (round(100.0 * delta / expected, 3) if expected else None),
                         "tol_mm": round(tol, 4),
                         "consistent": bool(abs(delta) <= tol + 1e-9)}
                combos.append(combo)
                deltas.append(abs(delta))
                if not combo["consistent"]:
                    inconsistent = True
        cons["pairwise"].extend(combos)
        cons["keys"].append(key)
    if deltas:
        cons["mm_max_delta"] = round(max(deltas), 4)
    verdict = "ok"
    any_fail = any(p["verdict"] == "fail" for p in per)
    any_data = any(p["verdict"] == "needs_data" for p in per)
    if any_fail:
        verdict = "fail"
    elif inconsistent:
        verdict = "inconsistent"
    elif assert_same_scale and cons.get("same_scale") is False:
        verdict = "inconsistent"
    elif any_data:
        verdict = "needs_data"
    ortho = {"enabled": bool(auto_ortho), "perspective_score": None, "status": "unresolved",
             "note": "不做自动消失点：单张/未知标定图无法可靠推 perspective；perspective_score=null。"
                     "要正交假设，先用 img_rectify 给四角点做单应校正，再 img_calib"}
    return _j({"ok": bool(verdict == "ok"), "verdict": verdict, "per_view": per,
               "consistency": cons, "auto_ortho": ortho, "units": "mm",
               "provenance": {"method": "per-view least squares over known lengths; "
                                        "shared dims converted with each view's px_per_mm then compared",
                              "views": [p["id"] for p in per],
                              "shared_keys": cons["keys"]},
               "honest_limits": ["cx_px 是标定参考点的像素中心，不是相机主点（本模块不做内参标定）",
                                 "同视多组已知长度残差 >2% 判 fail（取点错/透视/不共面）",
                                 "same_scale 只在多视且都标定出 px_per_mm 时给（假设正交同尺度）"]})


def img_selftest():
    """自检：合成两张已知几何的图（实心块 + 平移块），断言 scan/crop/annotate/diff 的数字都对得上。"""
    ev = {}
    try:
        import tempfile
        d = tempfile.mkdtemp(prefix="dsh-img-selftest-")
        W, H = 200, 120
        base = np.zeros((H, W, 4), np.float32); base[:, :, 3] = 1.0
        base[30:90, 20:100, 0:3] = 0.1          # 深色块 80x60
        p1 = _save(base, os.path.join(d, "a.png"))
        shift = np.zeros((H, W, 4), np.float32); shift[:, :, 3] = 1.0
        shift[30:90, 40:120, 0:3] = 0.1          # 同尺寸、右移 20px
        p2 = _save(shift, os.path.join(d, "b.png"))
        s = json.loads(img_scan(p1))
        ev["scan"] = {"bbox": s.get("bbox"), "aspect": s.get("aspect_wh"), "fill": s.get("fill_ratio"),
                      "axis": s.get("principal_axis_deg")}
        ok_scan = s.get("bbox") == [20, 30, 80, 60] and abs(s.get("aspect_wh", 0) - 80 / 60.0) < 0.01
        c = json.loads(img_crop(p1, 20, 30, 80, 60, scale=2.0, out=os.path.join(d, "crop.png")))
        ev["crop"] = {"out_size": c.get("out_size")}
        ok_crop = c.get("out_size") == [160, 120] and os.path.exists(c.get("out"))
        an = json.loads(img_annotate(p1, lines=[{"x1": 0, "y1": 0, "x2": 199, "y2": 0, "label": "top"}],
                                     boxes=[{"x": 20, "y": 30, "w": 80, "h": 60, "label": "silhouette"}],
                                     grid={"step": 20}, out=os.path.join(d, "ann.png")))
        ev["annotate"] = {"legend": [x.get("kind") for x in an.get("legend", [])]}
        ok_an = os.path.exists(an.get("out")) and len(an.get("legend", [])) == 3
        df = json.loads(img_diff(p1, p2, out=os.path.join(d, "diff.png")))
        ev["diff"] = {"iou": df.get("iou"), "a_only": df.get("a_only_px"), "b_only": df.get("b_only_px")}
        # 对齐后 bbox 都归一到 80x60：完全重合 ⇒ IoU≈1、a_only=b_only=0
        ok_diff = abs((df.get("iou") or 0) - 1.0) < 0.02 and df.get("a_only_px") == 0 and df.get("b_only_px") == 0
        df2 = json.loads(img_diff(p1, p2, align=False))
        ev["diff_unaligned"] = {"iou": df2.get("iou")}
        ok_diff2 = (df2.get("iou") or 1) < 0.85      # 不对齐时明显更低 —— 证明它真的在量差异
        return _j({"ok": bool(ok_scan and ok_crop and ok_an and ok_diff and ok_diff2), "evidence": ev,
                   "tmpdir": d})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})


def img_help():
    return _j({
        "module": "imgtools.py", "version": IMG_VERSION,
        "what": "参考图量具：把'看不清'变成'不用看'（量成数字 / 裁出来放大 / 画回去 / 差分）",
        "ops": {
            "img_scan": "只读：轮廓 bbox / 长宽比 / 填充率 / 主轴角 / 主要直线角度 / ROI 前景率 / 指定点取色",
            "img_crop": "ROI 裁剪 + 上采样落盘（一次只看一块，尽量占满视觉输入）",
            "img_annotate": "把线/框/十字/网格画回图（颜色看图、数字看 legend）",
            "img_diff": "两张图轮廓差分：IoU + a_only/b_only + 剖面差 + 红蓝叠加图",
            "img_digest": "v2 主力：一次给轮廓/对称/直线/Hough/孔圆/比例尺/可读性 + 原生裁剪 + 标注总图 + verdict（ready_for_spec|needs_scale|needs_views|unreadable|degraded）；无 known_len 时 mm 全 null",
            "img_probe": "v2 按需量测（读缓存快）：scan_y/scan_x 亚像素边缘与多段、circle/holes 圆孔、distance 两点距离、profile 剖面",
            "img_measure": "v2：像素→mm 并和期望值比，回 rows[{key,value_mm,expect_mm,delta_mm,delta_pct,verdict}]",
            "img_calib": "v2：多视逐视最小二乘 px_per_mm + shared 尺寸跨视换算 mm 两两比（不做自动消失点）",
            "img_selftest": "自检（合成已知几何，断言 bbox/尺寸/IoU 都对）",
        },
        "protocol": ["看图必须产出数字（写进 spec.py），否则算白看",
                     "每次 ≤2 张图，且每张带明确问题（yes/no 或数字）",
                     "每张图先 img_crop 到 ROI 再放大；不要给整张",
                     "判断'像不像'用 img_diff / qc_compare 的数字，不靠肉眼",
                     "像素↔毫米必须标定（给标尺或已知长度）"],
        "limits": ["照片背景复杂时先抠图（rembg）再 img_scan",
                   "img_crop 用最近邻上采样：不制造假细节",
                   "这套是「量具」不是「眼睛」：数值可比，观感仍需人/强模型判"],
    })


def img_dispatch(op, args_json):
    args = {}
    if isinstance(args_json, str) and args_json.strip():
        try:
            args = json.loads(args_json)
        except Exception as e:
            return _j({"ok": False, "error": "args 不是合法 JSON: %s" % str(e)[:120]})
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    try:
        kw = _KIT.resolve_refs(kw)   # S5-b：单步调用也支持 "@工件"
    except KeyError as e:
        return _j({"ok": False, "error": "引用解析失败: %s" % str(e)[:160]})
    ops = {"scan": img_scan, "rectify": img_rectify, "crop": img_crop, "annotate": img_annotate, "diff": img_diff,
           "digest": img_digest, "probe": img_probe, "measure": img_measure, "calib": img_calib,
           "selftest": img_selftest, "help": img_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown img op", "op": op, "ops": sorted(ops)})
    import inspect
    try:
        allowed = set(inspect.signature(fn).parameters)
        unknown = sorted(set(kw) - allowed)
        if unknown:
            return _j({"ok": False, "error": "不认识的参数 %s" % unknown, "op": op, "allowed": sorted(allowed)})
    except (TypeError, ValueError):
        pass
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_img_api = _DshApi({"version": IMG_VERSION, "dispatch": img_dispatch, "scan": img_scan,
                              "crop": img_crop, "annotate": img_annotate, "diff": img_diff,
                              "digest": img_digest, "probe": img_probe, "measure": img_measure,
                              "calib": img_calib, "selftest": img_selftest, "help": img_help})
