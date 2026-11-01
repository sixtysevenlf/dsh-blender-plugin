# -*- coding: utf-8 -*-
"""img_digest 阶段 6：缓存登记与最终回执（行为与拆分前一致）。"""
from ..common import *  # noqa: F401,F403
from ..common import _j
from ..cache import _cache_put, _register_id


def _digest_result(ctx, t0):
    """缓存 + image_id 注册，并组装最终 JSON 回执。"""
    md5 = ctx["md5"]
    image_id = ctx["image_id"]
    m = ctx["m"]
    entry = {"md5": md5, "path": ctx["p"], "shape": [ctx["W"], ctx["H"]],
             "native_px": [int(ctx["native_px"][0]), int(ctx["native_px"][1])],
             "gray": ctx["g"], "mask": ctx["cc"], "filled": ctx["filled"],
             "bbox_px": [int(v) for v in ctx["bbox"]],
             "source": ctx["source"], "confidence": round(float(ctx["conf"]), 4), "px_per_mm": ctx["ppm"],
             "silhouette": ctx["silhouette"], "features": ctx["features"]}
    try:
        _cache_put(entry)
        _register_id(image_id, entry, ctx["silhouette"], ctx["features"], ctx["numbers"])
    except Exception:
        pass
    return _j({
        "ok": True, "version": IMG_VERSION, "verdict": ctx["verdict"],
        "image": ctx["image_block"],
        "mask": {"source": ctx["source"], "fg_frac": round(float(m.mean()), 4),
                 "confidence": round(float(ctx["conf"]), 4),
                 "self_check": {"largest_cc_frac": round(float(ctx["cc_frac"]), 4),
                                "holes": int(len(ctx["holes_raw"])),
                                "edge_strength_p50": round(float(ctx["es_p50"]), 3)},
                 "components": int(ctx["cc_info"].get("n_components") or 0), "info": ctx["minfo"]},
        "silhouette": ctx["silhouette"],
        "symmetry": ctx["sym"],
        "lines": ctx["lines"],
        "features": ctx["features"],
        "scale": ctx["scale"],
        "numbers": ctx["numbers"],
        "readability": ctx["readability"],
        "unreadable": ctx["unreadable_rows"],
        "rois": (ctx["roi_rows"] if ctx["roi_rows"] else None),
        "crops": ctx["crops"],
        "overlay": ctx["overlay"],
        "attach": ctx["attach_paths"],
        "unknown": ctx["unknown"], "to_confirm": ctx["to_confirm"], "next": ctx["nexts"],
        "provenance": {"image_md5": md5, "image_id": image_id, "method": "alpha/border_otsu mask + "
                       "moore contour/DP + sobel-hough + kasa circle + least-squares scale",
                       "px_per_mm": ctx["ppm"], "coordinate_system": "left-top origin; px are loaded_px scale",
                       "elapsed_ms": round((time.time() - t0) * 1000.0, 1)},
        "honest_limits": ["不用深度学习抠图：复杂照片请先 rembg 或给 threshold",
                          "Hough 只抓强直线；曲线/弱对比边不出线",
                          "无 known_len 时 mm 全为 null（绝不靠猜）",
                          "对称性只测垂直/水平镜像轴"],
    })
