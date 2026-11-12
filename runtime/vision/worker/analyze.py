# -*- coding: utf-8 -*-
"""vision_analyze: bounded image statistics for a local file.

Returns size, colour mean/std, sharpness, exposure clipping, a best-effort
foreground bbox, and at most 32 contour / 32 line summaries. No pixel arrays,
no masks and no per-pixel data ever leave the worker.
"""
from __future__ import annotations

from typing import Any

import numpy as np

import common

MAX_CONTOURS = 32
MAX_LINES = 32


def _color_stats(bgr: np.ndarray) -> dict[str, Any]:
    channels = bgr.shape[2] if bgr.ndim == 3 else 1
    flat = bgr.reshape(-1, channels).astype(np.float64)
    mean = flat.mean(axis=0)
    std = flat.std(axis=0)
    names = ["b", "g", "r"] if channels >= 3 else ["gray"]
    return {
        "mean": {name: common.round_num(mean[index], 2) for index, name in enumerate(names[:channels])},
        "std": {name: common.round_num(std[index], 2) for index, name in enumerate(names[:channels])},
        "space": "BGR byte order as decoded, 0-255",
    }


def _exposure(gray: np.ndarray) -> dict[str, Any]:
    total = int(gray.size)
    high = int(np.count_nonzero(gray >= 250))
    low = int(np.count_nonzero(gray <= 5))
    return {
        "mean_luma": common.round_num(gray.mean() / 255.0, 4),
        "clipped_high_pct": common.round_num(high / float(total), 4) if total else 0.0,
        "clipped_low_pct": common.round_num(low / float(total), 4) if total else 0.0,
        "thresholds": {"high": 250, "low": 5, "scale": "0-255 gray"},
    }


def run(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    cv2 = common._cv2()
    path = common.require_file(args.get("path"))
    raw = common.read_image(path)
    bgr, alpha, bit_depth = common.split_bgra(raw)
    height, width = bgr.shape[:2]
    box = common.roi_box((height, width), args.get("roi"))
    max_contours = common.as_int(args.get("maxContours"), 32, 1, MAX_CONTOURS, "maxContours")

    working = common.crop(bgr, box)
    alpha_working = common.crop(alpha, box) if alpha is not None else None
    gray = common.to_gray(working)
    notes: list[str] = []
    if bit_depth > 8:
        notes.append("源图 " + str(bit_depth) + "-bit，统计前线性缩放到 8-bit（预览/统计一致）。")

    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness = {
        "laplacian_variance": common.round_num(float(laplacian.var()), 2),
        "method": "cv2.Laplacian(gray, CV_64F).var()",
        "higher_is_sharper": True,
    }

    foreground = common.foreground_mask(working, alpha_working)
    contours: list[dict[str, Any]] = []
    contour_total = 0
    if foreground["mask"] is not None:
        contours, contour_total = common.contours(foreground["mask"], max_contours)
    else:
        notes.append("未找到可靠前景（无 alpha 且 Otsu+最大连通域不足 0.1% 画面），foreground/contours 为 null 或空。")
    lines, line_total = common.hough_lines(gray, MAX_LINES)

    return {
        "path": path,
        "roi": {"x": box[0], "y": box[1], "w": box[2], "h": box[3], "full_frame": box == (0, 0, width, height)},
        "width": int(working.shape[1]),
        "height": int(working.shape[0]),
        "full_width": int(width),
        "full_height": int(height),
        "channels": int(bgr.shape[2]) if bgr.ndim == 3 else 1,
        "has_alpha": alpha is not None,
        "source_bit_depth": bit_depth,
        "color": _color_stats(working),
        "sharpness": sharpness,
        "exposure": _exposure(gray),
        "foreground": (
            {
                "method": foreground["method"],
                "coverage": common.round_num(foreground["coverage"], 4),
                "bbox": foreground["bbox"],
            }
            if foreground["reliable"]
            else None
        ),
        "contours": contours,
        "contours_truncated": contour_total > len(contours),
        "contours_total": contour_total,
        "lines": lines,
        "lines_truncated": line_total > len(lines),
        "lines_total": line_total,
        "notes": notes,
    }
