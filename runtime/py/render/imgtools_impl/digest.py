# -*- coding: utf-8 -*-
"""Split from imgtools.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .analysis import MIN_READABLE_PX, PALETTE, _edge_strength_p50, _largest_cc_mask, _mask_v2, _md5_cached, _norm_roi, _principal_axis, _rnd
from .contour import _circle_from_hole, _curvature_landmarks, _holes_of, _hull, _simplify_closed, _symmetry, _trace_contour
from .hough import _digest_scale, _hough_lines, _parse_known_lengths
from .cache import _cache_put, _draw_box, _draw_circle, _draw_line, _register_id



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
