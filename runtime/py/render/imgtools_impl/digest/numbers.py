# -*- coding: utf-8 -*-
"""img_digest 阶段 4：数字表（全部带 provenance，行为与拆分前一致）。"""
from ..common import *  # noqa: F401,F403
from ..analysis import _rnd


def _number(key, value, unit, err, conf, method, src):
    return {"key": str(key), "value": _rnd(value, 4), "unit": str(unit), "err": _rnd(err, 4),
            "confidence": _rnd(conf, 3), "method": str(method), "src": src}


def _digest_numbers(ctx):
    """数字表（全部带 provenance）。"""
    md5 = ctx["md5"]
    bbox = ctx["bbox"]
    aspect = ctx["aspect"]
    fill_ratio = ctx["fill_ratio"]
    pdeg = ctx["pdeg"]
    conf_r = ctx["conf_r"]
    ppm = ctx["ppm"]
    scale = ctx["scale"]
    features = ctx["features"]
    lines = ctx["lines"]
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
    return numbers
