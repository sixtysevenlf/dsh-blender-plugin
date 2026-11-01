# -*- coding: utf-8 -*-
"""Split from visualreport.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .image import _bbox_of, _components, _load_rgb_alpha, _make_overlay, _make_sheet, _mask_auto_own, _metrics_own, _resize_mask, _resize_rgb, _rgba_from_rgb, _save_png_rgba


# ────────────────────────────── 参数校验 ──────────────────────────────
def _parse_box(v, what, W, H):
    if v is None:
        return None
    if isinstance(v, dict):
        if all(k in v for k in ("x0", "y0", "x1", "y1")):
            vals = [v["x0"], v["y0"], v["x1"], v["y1"]]
        elif all(k in v for k in ("x", "y", "w", "h")):
            x, y, w, h = float(v["x"]), float(v["y"]), float(v["w"]), float(v["h"])
            vals = [x, y, x + w, y + h]
        else:
            raise ValueError("%s 用 dict 时必须给 x0/y0/x1/y1 或 x/y/w/h（收到键 %s）"
                             % (what, sorted(v.keys())))
    elif isinstance(v, (list, tuple)) and len(v) == 4:
        vals = list(v)
    else:
        raise ValueError("%s 必须是 [x0,y0,x1,y1] 或 {x0,y0,x1,y1}（收到 %r）" % (what, v))
    try:
        x0, y0, x1, y1 = [int(round(float(t))) for t in vals]
    except (TypeError, ValueError):
        raise ValueError("%s 的四个值都必须是数字（收到 %r）" % (what, vals))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("%s 是空框：%r（要 x1>x0 且 y1>y0）" % (what, [x0, y0, x1, y1]))
    clip = [max(0, min(x0, W)), max(0, min(y0, H)), max(0, min(x1, W)), max(0, min(y1, H))]
    if clip[2] - clip[0] < 4 or clip[3] - clip[1] < 4:
        raise ValueError("%s 裁到图内后太小：%r（图 %dx%d）" % (what, clip, W, H))
    return {"box": clip, "clipped": clip != [x0, y0, x1, y1], "given": [x0, y0, x1, y1]}


def _parse_targets(raw):
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("targets 必须是 list（收到 %r）" % (type(raw).__name__,))
    out = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ValueError("targets[%d] 必须是 dict（{key,expect_mm,tol_mm}）" % i)
        unknown = sorted(set(str(k) for k in t.keys()) - set(["key", "expect_mm", "tol_mm"]))
        if unknown:
            raise ValueError("targets[%d] 不认识的键 %s（可用：key,expect_mm,tol_mm）" % (i, unknown))
        rk = str(t.get("key") or "").strip().lower()
        key = TARGET_KEY_ALIASES.get(rk)
        if key is None:
            raise ValueError("targets[%d].key 不支持 %r（可用：%s）"
                             % (i, rk, ", ".join(sorted(set(TARGET_KEY_ALIASES.values())))))
        expect = _num(t.get("expect_mm"), "targets[%d].expect_mm" % i, gt=0.0)
        tol = _num(t.get("tol_mm"), "targets[%d].tol_mm" % i, gt=0.0)
        out.append({"raw_key": rk, "key": key, "expect_mm": round(expect, 4), "tol_mm": round(tol, 4)})
    return out


def _measure_targets(render_mask, targets, px_per_mm):
    rows = []
    if not targets:
        return rows
    bb = _bbox_of(render_mask)
    for t in targets:
        row = dict(t)
        if px_per_mm is None:
            row.update({"measured_mm": None, "err_mm": None, "status": "unresolved",
                        "reason": "缺 px_per_mm，无法把像素换成 mm"})
            rows.append(row)
            continue
        if bb is None:
            row.update({"measured_mm": None, "err_mm": None, "status": "unresolved",
                        "reason": "渲染掩膜为空，量不到尺寸"})
            rows.append(row)
            continue
        w_px = float(bb[2] - bb[0] + 1)
        h_px = float(bb[3] - bb[1] + 1)
        if t["key"] == "width":
            measured = w_px / px_per_mm
        elif t["key"] == "height":
            measured = h_px / px_per_mm
        elif t["key"] == "diag":
            measured = math.hypot(w_px, h_px) / px_per_mm
        else:
            measured = float(int(render_mask.sum())) / (px_per_mm * px_per_mm)
        err = measured - float(t["expect_mm"])
        status = "pass" if abs(err) <= float(t["tol_mm"]) else "fail"
        row.update({"measured_mm": round(float(measured), 3), "err_mm": round(float(err), 3),
                    "status": status})
        rows.append(row)
    return rows


# ────────────────────────────── 跨界调用（qc / imgtools）──────────────────────────────
def _api_call(api, op, args):
    """qc/imgtools 的 Api 调用：优先 Api(op,args)（返回 dict），否则直接调 dict 里的函数。"""
    if api is None:
        return None
    try:
        if callable(api):
            r = api(op, args)
        else:
            fn = api.get(op)
            if fn is None:
                return {"ok": False, "error": "op 不存在：%s" % op}
            r = fn(**args)
        if isinstance(r, str):
            r = json.loads(r)
        return r if isinstance(r, dict) else None
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _qc_masks(qc, srgb, alpha):
    m, info = qc["mask_auto"](srgb, alpha)
    return np.asarray(m, dtype=bool), (info if isinstance(info, dict) else {})


def _qc_metrics(qc, rm, rn):
    m = qc["metrics"](rm, rn)
    if not isinstance(m, dict):
        raise RuntimeError("qc.metrics 没返回 dict")
    b = m.get("boundary")
    if isinstance(b, dict):
        m["boundary"] = {"mean_px": b.get("mean_px"), "p95_px": b.get("p95_px"), "source": "qc"}
    else:
        m["boundary"] = {"mean_px": (round(float(b), 3) if isinstance(b, (int, float)) else None),
                         "p95_px": None, "source": "qc"}
    for k in ("iou", "dice", "missing_frac", "extra_frac"):
        if k in m and m[k] is not None:
            m[k] = round(float(m[k]), 4)
    for k in ("ref_px", "render_px", "inter_px", "union_px", "missing_px", "extra_px"):
        if k in m and m[k] is not None:
            m[k] = int(m[k])
    return m


def _qc_crosscheck(qc, ref_path, ref_box, render_path, render_box, run_dir, tag):
    args = {"ref_path": ref_path, "render_path": render_path, "label": tag,
            "out_dir": run_dir, "search": True}
    if ref_box:
        args["ref_box"] = list(ref_box)
    if render_box:
        args["render_box"] = list(render_box)
    r = _api_call(qc, "compare", args)
    if not r or not r.get("ok"):
        return {"ok": False, "source": "qc.compare",
                "error": str((r or {}).get("error") or "qc.compare 无结果")[:240]}
    m = r.get("metrics") or {}
    b = m.get("boundary") or {}
    return {"ok": True, "source": "qc.compare", "iou": r.get("iou"),
            "dice": m.get("dice"), "missing_px": m.get("missing_px"), "extra_px": m.get("extra_px"),
            "missing_frac": m.get("missing_frac"), "extra_frac": m.get("extra_frac"),
            "boundary": {"mean_px": (b.get("mean_px") if isinstance(b, dict) else b), "source": "qc.compare"},
            "overlay": r.get("overlay"), "sheet": r.get("sheet"),
            "warnings": (m.get("warnings") or [])[:6]}


def _img_crosscheck(img, ref_path, render_path, run_dir, tag):
    if img is None:
        return {"ok": False, "source": "imgtools", "error": "imgtools 未注入"}
    out_png = os.path.join(run_dir, "imgdiff_%s.png" % _safe_token(tag, "tag", 32))
    d = _api_call(img, "diff", {"a": ref_path, "b": render_path, "out": out_png, "align": True})
    scan_ref = _api_call(img, "scan", {"path": ref_path})
    scan_ren = _api_call(img, "scan", {"path": render_path})
    if not d or not d.get("ok"):
        return {"ok": False, "source": "imgtools.diff",
                "error": str((d or {}).get("error") or "imgtools.diff 无结果")[:240]}
    return {"ok": True, "source": "imgtools.diff", "iou": d.get("iou"),
            "missing_px": d.get("a_only_px"), "extra_px": d.get("b_only_px"),
            "intersection_px": d.get("intersection_px"), "union_px": d.get("union_px"),
            "out": d.get("out"), "aligned_bbox": d.get("aligned_bbox"),
            "worst_columns": (d.get("worst_columns") or [])[:3],
            "worst_rows": (d.get("worst_rows") or [])[:3],
            "scan": {"ref": (scan_ref or {}), "render": (scan_ren or {})}}


# ────────────────────────────── 单视图分析 ──────────────────────────────
def _pv_crops(ref_srgb, ref_alpha, ren_srgb, ren_alpha, ref_box, opts):
    """参考/渲染按 ref_box / render_box 裁切；返回 (ref_c, ref_a, ren_c, ren_a)。"""
    ref_c, ref_a = ref_srgb, ref_alpha
    ren_c, ren_a = ren_srgb, ren_alpha
    if ref_box:
        x0, y0, x1, y1 = ref_box
        ref_c, ref_a = ref_srgb[y0:y1, x0:x1], ref_alpha[y0:y1, x0:x1]
    if opts.get("render_box"):
        x0, y0, x1, y1 = opts["render_box"]
        ren_c, ren_a = ren_srgb[y0:y1, x0:x1], ren_alpha[y0:y1, x0:x1]
    return ref_c, ref_a, ren_c, ren_a


def _pv_masks(qc, ref_c, ref_a, ren_c, ren_a, warnings):
    """掩膜：qc.mask_auto 优先，失败/未注入走 numpy.otsu 兜底；返回 (rm, rn, rinfo, ninfo, source)。"""
    if qc is not None:
        try:
            rm, rinfo = _qc_masks(qc, ref_c, ref_a)
            rn, ninfo = _qc_masks(qc, ren_c, ren_a)
            return rm, rn, rinfo, ninfo, "qc.mask_auto"
        except Exception as e:
            warnings.append("qc.mask_auto 失败（%s: %s）→ numpy.otsu 兜底"
                            % (type(e).__name__, str(e)[:120]))
            rm, rinfo = _mask_auto_own(ref_c, ref_a)
            rn, ninfo = _mask_auto_own(ren_c, ren_a)
            return rm, rn, rinfo, ninfo, "numpy.otsu"
    rm, rinfo = _mask_auto_own(ref_c, ref_a)
    rn, ninfo = _mask_auto_own(ren_c, ren_a)
    return rm, rn, rinfo, ninfo, "numpy.otsu"


def _pv_align(rm, rn, qc, warnings, ref_c):
    """参考掩膜最近邻缩放到渲染画布（尺寸不同才做）；返回 (rm, resized)。"""
    if rm.shape == rn.shape:
        return rm, False
    if qc is not None:
        try:
            rm = np.asarray(qc["resize_mask"](rm, rn.shape[0], rn.shape[1]), dtype=bool)
        except Exception:
            rm = _resize_mask(rm, rn.shape[0], rn.shape[1])
    else:
        rm = _resize_mask(rm, rn.shape[0], rn.shape[1])
    warnings.append("参考/渲染的比对画布尺寸不同（%s vs %s）：参考掩膜已最近邻缩放到渲染画布"
                    "（同机位 QC 建议同分辨率/同裁切）" % (list(ref_c.shape[:2]), list(rn.shape)))
    return rm, True


def _pv_metrics(qc, rm, rn, warnings):
    """IoU/Dice/边界：qc.metrics 优先，失败/未注入走 numpy 兜底；返回 (metrics, source)。"""
    if qc is not None and bool(rm.any()) and bool(rn.any()):
        try:
            return _qc_metrics(qc, rm, rn), "qc.metrics"
        except Exception as e:
            warnings.append("qc.metrics 失败（%s: %s）→ numpy 兜底" % (type(e).__name__, str(e)[:120]))
            return _metrics_own(rm, rn), "numpy"
    if qc is None:
        warnings.append("qc 未注入：IoU/Dice/边界用 numpy 兜底（boundary 为膨胀近似）")
    return _metrics_own(rm, rn), "numpy"


def _pv_defects(rm, rn, view, canvas):
    """缺/多缺陷连通块（按面积降序）；返回 defects 列表。"""
    defects = []
    sa = max(1, int(rm.sum()))
    sb = max(1, int(rn.sum()))
    for kind, cmask in (("missing", np.logical_and(rm, ~rn)), ("extra", np.logical_and(rn, ~rm))):
        for c in _components(cmask):
            area = int(c["area_px"])
            if area <= 0:
                continue
            defects.append({"kind": kind, "view": view["name"], "bbox_px": c["bbox"],
                            "area_px": area, "centroid_px": c["centroid_px"],
                            "area_frac": round(area / float(sa if kind == "missing" else sb), 4),
                            "canvas": canvas})
    defects.sort(key=lambda d: (-d["area_frac"], -d["area_px"]))
    return defects


def _pv_images(ref_c, ren_c, rm, rn, canvas, run_dir, tag):
    """叠加图 + 三联图（参考/渲染/叠加）；返回 (overlay, ov_rec, sh_rec)。"""
    overlay = _make_overlay(rm, rn)
    ov_rec = _save_png_rgba(overlay, os.path.join(run_dir, "overlay_%s.png" % _safe_token(tag, "tag", 40)))
    ref_panel = _rgba_from_rgb(_resize_rgb(ref_c, canvas[0], canvas[1]))
    ren_panel = _rgba_from_rgb(_resize_rgb(ren_c, canvas[0], canvas[1]))
    sh_rec = _save_png_rgba(_make_sheet([ref_panel, ren_panel, overlay]),
                            os.path.join(run_dir, "sheet_%s.png" % _safe_token(tag, "tag", 40)))
    return overlay, ov_rec, sh_rec


def _pv_crosscheck(qc, img, ref_path, ref_box, view_path, opts, run_dir, tag, rm, rn, metrics, warnings):
    """交叉核验（qc.compare 优先；否则 imgtools diff/scan）；返回 cross。"""
    cross = None
    if not bool(rm.any()) or not bool(rn.any()):
        warnings.append("有一侧掩膜为空：无法判读前景（照片类参考图请先抠背景；渲染图检查透明背景/取景）")
    elif qc is not None:
        cross = _qc_crosscheck(qc, ref_path, list(ref_box) if ref_box else None, view_path,
                               list(opts["render_box"]) if opts.get("render_box") else None,
                               run_dir, tag)
        if not cross.get("ok"):
            warnings.append("qc.compare 交叉核验失败：%s" % str(cross.get("error"))[:180])
    if (cross is None or not cross.get("ok")) and img is not None:
        cross = _img_crosscheck(img, ref_path, view_path, run_dir, tag)
        if not cross.get("ok"):
            warnings.append("imgtools 交叉核验也失败：%s" % str(cross.get("error"))[:180])
    if cross and cross.get("ok"):
        ci = cross.get("iou")
        if isinstance(ci, (int, float)) and metrics.get("iou") is not None:
            gap = abs(float(ci) - float(metrics["iou"]))
            if gap > 0.10:
                warnings.append("交叉核验 IoU（%.4f）与固定像素指标（%.4f）差 %.3f：可能是对齐/掩膜口径差异"
                                "（本报告以固定像素指标判 verdict）"
                                % (float(ci), float(metrics["iou"]), gap))
        for w in (cross.get("warnings") or []):
            warnings.append("交叉核验：%s" % str(w)[:160])
    if cross is None or not cross.get("ok"):
        warnings.append("交叉核验不可用（qc.compare 与 imgtools.diff 都没跑成）—— 指标仍按本模块固定像素口径给")
    return cross


def _process_view(ref_srgb, ref_alpha, ref_path, ref_box, view, run_dir, tag, qc, img, opts):
    t0 = time.perf_counter()
    warnings = []
    view_path = _norm_path(view["path"])
    ren_srgb, ren_alpha = _load_rgb_alpha(view_path)
    ref_c, ref_a, ren_c, ren_a = _pv_crops(ref_srgb, ref_alpha, ren_srgb, ren_alpha, ref_box, opts)
    even = _pv_masks(qc, ref_c, ref_a, ren_c, ren_a, warnings)
    rm, rn, rinfo, ninfo, mask_source = even
    canvas = [int(rn.shape[0]), int(rn.shape[1])]
    rm, resized = _pv_align(rm, rn, qc, warnings, ref_c)
    metrics, metrics_source = _pv_metrics(qc, rm, rn, warnings)
    defects = _pv_defects(rm, rn, view, canvas)
    target_rows = _measure_targets(rn, opts.get("targets") or [], opts.get("px_per_mm"))
    overlay, ov_rec, sh_rec = _pv_images(ref_c, ren_c, rm, rn, canvas, run_dir, tag)
    cross = _pv_crosscheck(qc, img, ref_path, ref_box, view_path, opts, run_dir, tag, rm, rn, metrics, warnings)
    verdict, reasons, degraded = _judge(metrics, target_rows,
                                        {"resized": resized,
                                         "masks_empty": (not bool(rm.any())) or (not bool(rn.any()))},
                                        opts)
    detail = {"name": view["name"], "ref": ref_path, "render": view_path,
              "metrics": metrics, "metrics_source": metrics_source, "mask_source": mask_source,
              "alignment": {"canvas": canvas,
                            "ref_crop": [int(ref_c.shape[1]), int(ref_c.shape[0])],
                            "render_crop": [int(ren_c.shape[1]), int(ren_c.shape[0])],
                            "ref_box_px": (list(ref_box) if ref_box else None),
                            "render_box_px": (list(opts["render_box"]) if opts.get("render_box") else None),
                            "resized_ref_mask": bool(resized)},
              "mask_info": {"ref": rinfo, "render": ninfo},
              "targets": target_rows, "defects": defects, "crosscheck": cross,
              "images": {"overlay": ov_rec, "sheet": sh_rec},
              "verdict": verdict, "reasons": reasons, "degraded_reasons": degraded,
              "warnings": warnings,
              "ms": int(round((time.perf_counter() - t0) * 1000.0))}
    return detail, overlay



def _judge(metrics, target_rows, flags, opts):
    reasons, degraded = [], []
    if flags.get("masks_empty"):
        return "degraded", [], ["有一侧掩膜为空：无法判读前景"]
    try:
        iou = float(metrics.get("iou") or 0.0)
        mf = float(metrics.get("missing_frac") or 0.0)
        ef = float(metrics.get("extra_frac") or 0.0)
    except Exception:
        return "degraded", [], ["metrics 不可解析"]
    if iou < float(opts["iou_fail"]):
        reasons.append("IoU %.4f < fail 阈值 %.2f" % (iou, opts["iou_fail"]))
    if mf > float(opts["max_missing_frac"]):
        reasons.append("缺失面积占比 %.1f%% > 上限 %.1f%%（参考有、渲染无）"
                       % (mf * 100.0, opts["max_missing_frac"] * 100.0))
    if ef > float(opts["max_extra_frac"]):
        reasons.append("多余面积占比 %.1f%% > 上限 %.1f%%（渲染多出）"
                       % (ef * 100.0, opts["max_extra_frac"] * 100.0))
    tf = [t for t in target_rows if t.get("status") == "fail"]
    if tf:
        reasons.append("尺寸 target 不达标：%s"
                       % "；".join("%s 实测 %.2fmm 期望 %.2f±%.2fmm"
                                  % (t["key"], float(t.get("measured_mm") or 0.0),
                                     float(t["expect_mm"]), float(t["tol_mm"])) for t in tf[:3]))
    if any(t.get("status") == "unresolved" for t in target_rows):
        degraded.append("targets 因缺 px_per_mm（或掩膜为空）未判")
    if flags.get("resized"):
        degraded.append("参考/渲染尺寸不同：参考掩膜已缩放对齐")
    if reasons:
        return "fail", reasons, degraded
    if degraded:
        return "degraded", [], degraded
    if iou < float(opts["iou_pass"]):
        return "degraded", [], ["IoU %.4f 落在 fail/pass 阈值之间（%.2f..%.2f）"
                                % (iou, opts["iou_fail"], opts["iou_pass"])]
    return "pass", [], []


def _fix_hint(top_verdict, views, top_defects, all_targets, opts):
    if top_verdict == "pass":
        return ("无明显几何偏差：继续看 overlay/crop 复核边缘与细节；换机位/换分辨率/换引擎后的历史分数不可直接比")
    hints = []
    tf = [t for t in all_targets if t.get("status") == "fail"]
    if tf:
        hints.append("尺寸不达标：%s → 回改对应部件尺寸（targets 是硬判据）"
                     % "；".join("%s 实测 %.2fmm 期望 %.2f±%.2fmm"
                                % (t["key"], float(t.get("measured_mm") or 0.0),
                                   float(t["expect_mm"]), float(t["tol_mm"])) for t in tf[:2]))
    miss = [d for d in top_defects if d.get("kind") == "missing"]
    extra = [d for d in top_defects if d.get("kind") == "extra"]
    if miss:
        hints.append("参考有、渲染无（%d 处，最大 %.1f%%）：按 crop 的 bbox 查对应视图缺失的几何/被裁件"
                     % (len(miss), max(float(d["area_frac"]) for d in miss) * 100.0))
    if extra:
        hints.append("渲染多出（%d 处，最大 %.1f%%）：查多余件/穿模/比例偏大或背景未抠净"
                     % (len(extra), max(float(d["area_frac"]) for d in extra) * 100.0))
    if not hints:
        ious = [(v["name"], float((v.get("metrics") or {}).get("iou") or 0.0)) for v in views]
        low = min((x[1] for x in ious), default=0.0)
        hints.append("整体轮廓偏差（最低 IoU %.3f）：先核对机位/画幅/裁切是否与参考一致（同机位应同分辨率），"
                     "再按 overlay 的红/蓝区域改形" % low)
    if any(t.get("status") == "unresolved" for t in all_targets):
        hints.append("给 px_per_mm（渲染图每毫米像素数）才能把像素差换成 mm 并判 targets")
    return "；".join(hints)[:600]


