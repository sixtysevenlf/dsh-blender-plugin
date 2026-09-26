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
    img_selftest / img_help

坐标约定：**左上原点**（图像习惯），返回的 bbox 是 [x, y, w, h]。像素↔毫米要调用方自己标定（img_scan 会给像素数）。
局限：照片类参考图没有 alpha 时靠 Otsu 亮度分割，前景/背景对比低会不准 —— 那种情况先抠图（rembg）再喂。
"""
import json
import math
import os

import bpy
import numpy as np

IMG_VERSION = 1


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


def _load(path):
    """读图 → (H, W, 4) float 数组（**上下翻转成正向**，即 row 0 = 图像顶部）。"""
    p = _win(path)
    if not os.path.exists(p):
        raise ValueError("图不存在：%s" % p)
    img = bpy.data.images.load(p, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        arr = buf.reshape(h, w, 4)[::-1]          # Blender 是下原点 → 翻成上原点
        return arr, p
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass


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
                              "selftest": img_selftest, "help": img_help})
