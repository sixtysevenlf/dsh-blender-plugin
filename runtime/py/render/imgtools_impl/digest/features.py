# -*- coding: utf-8 -*-
"""img_digest 阶段 3：孔/圆/角点特征与可读性（行为与拆分前一致）。"""
from ..common import *  # noqa: F401,F403
from ..analysis import MIN_READABLE_PX, _rnd
from ..contour import _circle_from_hole


def _digest_features(ctx):
    """孔/圆 + 角点特征；返回 (features, unreadable_rows, conf_r)。"""
    bbox = ctx["bbox"]
    ppm = ctx["ppm"]
    conf_r = float(ctx["conf"]) * (0.5 + 0.5 * float(ctx["cc_frac"]))
    features = []
    unreadable_rows = []
    for hi_, hrow in enumerate(ctx["holes_raw"]):
        hm = hrow.pop("mask", None)
        if hm is None:
            continue
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
            mm = ({"width_mm": round(bx[2] / ppm, 4), "height_mm": round(bx[3] / ppm, 4)}
                  if ppm else None)
        readable = bool(size_px >= MIN_READABLE_PX)
        key = "hole%d" % (hi_ + 1)
        why = "size %.1f px %s 8px" % (size_px, ">=" if readable else "<")
        features.append({"key": key, "kind": kind, "geom": geom, "mm": mm,
                         "size_px": round(size_px, 3), "readable": readable,
                         "confidence": round(float(conf_f), 3), "why": why})
        if not readable:
            unreadable_rows.append({"feature": key, "kind": kind, "size_px": round(size_px, 2),
                                    "why": "<8px：计数/形状不可靠，不要写进 spec"})
    for ci_, corner in enumerate(ctx["corners"][:6]):
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
    return features, unreadable_rows, conf_r


def _digest_readability(features):
    """每个特征的可读性判定（<8px 一律 unreadable）。"""
    return [{"feature": f["key"], "kind": f["kind"], "size_px": f["size_px"],
             "threshold_px": MIN_READABLE_PX, "readable": bool(f["readable"]),
             "verdict": ("readable" if f["readable"] else "unreadable"),
             "why": f.get("why")} for f in features]
