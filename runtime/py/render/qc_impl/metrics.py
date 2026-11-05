# -*- coding: utf-8 -*-
"""QC 度量与帮助：qc_compare_auto、自检/鲁棒性/掩膜扫描与 qc_help 文案。"""
from .common import *  # noqa: F401,F403
from .load import _hstack, _overlay_bytes, _to_u8, qc_crop, qc_load, qc_mask, qc_profile, qc_profile_diff
from .align import _align_search, _iou_pair, _scale_bool, _scale_rgb, qc_align_rotate, qc_mask_auto, qc_metrics


def qc_self_check() -> str:
    """算法自检（不需要参考图）：把参考图与自己的旋转/平移/缩放版本比，检查 IoU 的单调性与 1.0 上界。"""
    import numpy as _np
    H = W = 200
    yy, xx = _np.mgrid[0:H, 0:W]
    base = (((xx - 100) / 60.0) ** 2 + ((yy - 140) / 40.0) ** 2) < 1.0
    base |= ((_np.abs(xx - 100) < 14) & (yy > 30) & (yy < 140))
    same = _align_search(base, base)
    shift = _np.roll(base, 12, axis=1)
    shifted = _align_search(base, shift)
    scale = _scale_bool(base, int(W * 1.15), int(H * 1.15))[:H, :W]
    scaled = _align_search(base, scale)
    m_ok = qc_metrics(base, base, same["iou"], same.get("inter"), same.get("union"))
    return _j({"ok": True, "iou_self": round(float(same["iou"]), 4),
               "iou_shift12px": round(float(shifted["iou"]), 4),
               "iou_scale115": round(float(scaled["iou"]), 4),
               "self_metrics": m_ok,
               "expect": "iou_self 应=1.0；shift/scale 应被对齐搜索找回高 IoU（>0.95）"})
def qc_compare_auto(ref_path, ref_box=None, render_path=None, label="qc", out_dir=None, render_box=None,
                    search=True, work=384, bins=24, sat=0.13, v=0.74, mask_mode="auto", rotate=False):
    """优化版比对（推荐默认）：
      ① 掩膜自适应（alpha 智能判定 / 背景色+Otsu，不再手调阈值）
      ② 对齐搜索（尺度±10% + 平移±5%，两轮细化，最大化 IoU）
      ③ 可行动指标：IoU / Dice / 缺面积 / 多面积 / 边界距离 / 逐层剖面差
      ④ 产出：叠加图 + 三联对照图（参考 | 渲染 | 叠加）
    """
    t0 = time.perf_counter()
    d = out_dir or _out_dir()
    os.makedirs(d, exist_ok=True)
    ref_srgb, ref_alpha = qc_load(ref_path)
    ref = qc_crop(ref_srgb, ref_box) if ref_box else ref_srgb
    # v0.8.10（口径统一）：ref 也要走 alpha 判定 —— 旧版 ref 用 Otsu、render 用 alpha，
    # 同一张图自比只有 0.565（外部反馈里"同一轮廓被掩膜口径压到 0.62 假低分"就是这个）
    rm, rminfo = qc_mask_auto(ref, ref_alpha) if mask_mode == "auto" else (qc_mask(ref, sat, v), {"source": "satv"})
    # v0.8.3：auto 掩膜适用性检查 —— 前景占比异常（满构图海报 / 几乎无前景）时回落阈值口径并显式标注
    _fg = float(rm.mean())
    if mask_mode == "auto" and (_fg > 0.8 or _fg < 0.02):
        rm = qc_mask(ref, sat, v)
        rminfo = {"source": "satv-fallback", "auto_fg_frac": round(_fg, 4),
                  "reason": "auto 前景占比 %.1f%% 不可信（>80%% 或 <2%%）→ 回落阈值口径 sat=%.2f v=%.2f" % (_fg * 100, sat, v)}
    ren_srgb, ren_alpha = qc_load(render_path)
    if render_box:
        ren_srgb = qc_crop(ren_srgb, render_box)
        ren_alpha = qc_crop(ren_alpha, render_box)
    rn, rninfo = qc_mask_auto(ren_srgb, ren_alpha) if mask_mode == "auto" else (qc_mask(ren_srgb, sat, v), {"source": "satv"})
    fixed = _align_search(rm, rn, work=work, coarse=False) if True else None
    align = _align_search(rm, rn, work=work, coarse=True) if search else fixed
    base = fixed if fixed is not None else align
    if base is not None:
        mref, mren = align["mask_ref"], align["mask_render"]
        m = qc_metrics(mref, mren, base["iou"], base.get("inter"), base.get("union"))
        m["scale"] = round(base["scale"], 4)
        m["scale_base"] = base.get("base_scale")
        m["shift_px"] = base.get("shift")
        m["canvas"] = base["canvas"]
        # v0.8.10：可选角度细化（默认关；开了就报 rotation_deg 与增益）
        if rotate:
            try:
                _rot = qc_align_rotate(rm, rn, work=work)
                if _rot is not None:
                    m["rotation_deg"] = round(float(_rot["deg"]), 2)
                    m["iou_rotated"] = round(float(_rot["iou"]), 4)
                    m["iou_rotate_gain"] = float(_rot.get("iou_gain_vs_no_rot") or 0.0)
                    if _rot["iou"] > float(base["iou"]):
                        mref, mren = _rot["mask_ref"], _rot["mask"]
                        m2 = qc_metrics(mref, mren, _rot["iou"])
                        for _k in ("iou", "dice", "inter_px", "ref_px", "render_px", "missing_px", "extra_px", "boundary"):
                            if _k in m2 and _k in m:
                                m[_k] = m2[_k]
            except Exception as _e:
                m["rotate_error"] = "%s: %s" % (type(_e).__name__, str(_e)[:100])
    else:
        H = min(rm.shape[0], rn.shape[0]); W = min(rm.shape[1], rn.shape[1])
        mref = rm[:H, :W]; mren = rn[:H, :W]
        m = qc_metrics(mref, mren)
        m["canvas"] = [H, W]
    if fixed is not None and align is not None and fixed is not align:
        m["iou_fixed"] = m["iou"]
        m["iou_search"] = round(float(align["iou"]), 4)
        m["iou_search_gain"] = round(float(align["iou"]) - float(m["iou"]), 4)
    else:
        m["iou_fixed"] = m["iou"]
    warn = []
    if m.get("scale") and m.get("scale_base"):
        drift = float(m["scale"]) / max(1e-6, float(m["scale_base"]))
        m["scale_drift"] = round(drift, 4)
        if abs(drift - 1.0) > 0.05:
            warn.append("对齐搜索把尺度拉开 %.1f%%：可能是在补偿取景/裁切差，也可能模型比例确实不对 —— 请结合 iou_fixed 一起看" % ((drift - 1.0) * 100.0))
    if (m.get("iou_search_gain") or 0) > 0.05:
        warn.append("搜索对齐比固定对齐高 %.3f：单看 IoU 会偏乐观，建议报告里两个都给" % (float(m["iou"]) - float(m["iou_fixed"])))
    # ---- v0.8.10「太漂亮」守卫：IoU 恒等 1.0000 / 面积完全相同 → 疑似同一张图或参数没生效
    try:
        _iou = float(m.get("iou") or 0)
        _a, _b = m.get("a_px"), m.get("b_px")
        if _iou >= 0.9999:
            warn.append("IoU ≥ 0.9999（%.4f）：疑似两张图是同一份、或参数没生效 —— 请核对输入路径/取景参数" % _iou)
        if _iou > 0.999 and _a and _b and int(_a) == int(_b):
            warn.append("掩膜面积完全相同（%s px）且 IoU≈1：强烈怀疑参考图与渲染图是同一张" % _a)
        if _iou >= 0.999 and abs(float(m.get("scale", 1.0)) - 1.0) < 1e-6 and abs(float(m.get("shift_px", [0, 0])[0] or 0)) < 0.01:
            warn.append("尺度/平移都是零位移却 IoU≈1：典型的「两个 view 其实渲了同一张图」")
    except Exception:
        pass
    m["warnings"] = warn
    pa = qc_profile(mref, bins)
    pb = qc_profile(mren, bins)
    m["profile_diff"] = qc_profile_diff(pa, pb)
    ov, h, w = _overlay_bytes(mref, mren)
    ref_panel = _to_u8(_scale_rgb(np.clip(ref, 0.0, 1.0), w, h))
    a4 = np.clip(ren_alpha if ren_alpha is not None else np.ones_like(ren_srgb[:, :, 0]), 0.0, 1.0)
    ren_rgb = np.clip(ren_srgb, 0.0, 1.0) * a4[:, :, None] + (1.0 - a4[:, :, None])
    ren_panel = _to_u8(_scale_rgb(ren_rgb, w, h))
    stamp = time.strftime("%H%M%S")
    base = "%s_%s_%s" % (str(label), stamp, os.path.basename(str(render_path)).replace(".png", ""))
    ov_path = os.path.join(d, "qc_overlay_" + base + ".png")
    sh_path = os.path.join(d, "qc_sheet_" + base + ".png")
    write_png(ov_path, w, h, ov.tobytes())
    sheet, sh, sw = _hstack([ref_panel, ren_panel, ov])
    write_png(sh_path, sw, sh, sheet.tobytes())
    _eng = None
    try:
        _eng = bpy.context.scene.render.engine
        if "EEVEE" in str(_eng):
            _eng = str(_eng) + ("+RT" if getattr(bpy.context.scene.eevee, "use_raytracing", False) else "")
    except Exception:
        _eng = None
    out = {"ok": True, "label": str(label), "mode": "auto" if mask_mode == "auto" else mask_mode,
           "metrics": m, "masks": {"ref": rminfo, "render": rninfo},
           "overlay": ov_path, "sheet": sh_path, "ms": int((time.perf_counter() - t0) * 1000),
           "note": "score 建议用 iou（越高越好）；缺/多面积指出方向；boundary.mean_px 是边界平均偏差（像素）",
           "engine_at_qc": _eng,
           "engine_note": "判据只在同一引擎内可比（换引擎后历史分数不可直接对比）"}
    out["iou"] = m["iou"]
    out["profile"] = {"ref": pa, "render": pb, "diff": m["profile_diff"]}
    out["render_px"] = m["render_px"]
    return _j(out)

def qc_robustness_check(ref_path, ref_box=None, render_path=None, dx=12, dy=-8, zoom=1.06, work=384):
    """鲁棒性自测：把渲染掩膜人为平移 + 缩放（模拟"构图/裁切/取景差异"），
       对比「直接比」与「对齐搜索后比」的 IoU —— 用来证明优化②确实在补偿外部差异。
    """
    ref_srgb, _ = qc_load(ref_path)
    ref = qc_crop(ref_srgb, ref_box) if ref_box else ref_srgb
    rm, rinfo = qc_mask_auto(ref, None)
    if render_path:
        ren_srgb, ren_alpha = qc_load(render_path)
        rn, ninfo = qc_mask_auto(ren_srgb, ren_alpha)
    else:
        rn, ninfo = rm.copy(), {"source": "self"}
    a0 = _align_search(rm, rn, work=work)
    zw = max(8, int(rn.shape[1] * float(zoom)))
    zh = max(8, int(rn.shape[0] * float(zoom)))
    zp = _scale_bool(rn, zw, zh)
    rp = np.zeros_like(rn)
    h = min(rn.shape[0], zp.shape[0])
    w = min(rn.shape[1], zp.shape[1])
    rp[:h, :w] = zp[:h, :w]
    rp = np.roll(np.roll(rp, int(dx), axis=1), int(dy), axis=0)
    direct = qc_metrics(rm, rp)
    searched = _align_search(rm, rp, work=work)
    sm = qc_metrics(searched["mask_ref"], searched["mask_render"], searched["iou"], searched.get("inter"), searched.get("union"))
    return _j({"ok": True, "base_iou": round(float(a0["iou"]), 4),
               "perturbed_direct_iou": direct["iou"], "perturbed_searched_iou": sm["iou"],
               "recovered": round(float(sm["iou"] - direct["iou"]), 4),
               "perturb": {"dx": dx, "dy": dy, "zoom": zoom}, "masks": {"ref": rinfo, "render": ninfo},
               "expect": "searched 应显著高于 direct（对齐搜索在补偿构图差异）"})

def qc_mask_sweep(ref_path, ref_box=None, render_path=None, sats=(0.10, 0.13, 0.16), vs=(0.68, 0.74, 0.80), label="sweep"):
    """掩膜口径敏感度扫描：固定对齐下逐组 (sat, v) 算 IoU → 给区间 + 推荐口径。"""
    ref_srgb, _ = qc_load(ref_path)
    ref = qc_crop(ref_srgb, ref_box) if ref_box else ref_srgb
    ren_srgb, _a = qc_load(render_path)
    cells = []
    for s in sats:
        for v in vs:
            rm = qc_mask(ref, float(s), float(v)); rn = qc_mask(ren_srgb, float(s), float(v))
            fx = _align_search(rm, rn, work=384, coarse=False)
            if fx is None:
                continue
            iou, _i, _u = _iou_pair(fx["mask_ref"], fx["mask_render"])
            cells.append({"sat": float(s), "v": float(v), "iou": round(float(iou), 4)})
    if not cells:
        return _j({"ok": False, "error": "扫描失败（图读不到或掩膜为空）"})
    ious = sorted(c["iou"] for c in cells)
    med = ious[len(ious) // 2]
    best = min(cells, key=lambda c: abs(c["iou"] - med))
    return _j({"ok": True, "label": str(label), "cells": cells, "count": len(cells),
               "iou_min": ious[0], "iou_median": med, "iou_max": ious[-1],
               "spread": round(ious[-1] - ious[0], 4), "mask_used": best,
               "note": "区间 = 掩膜口径敏感度；报告应给 区间 + 推荐口径，不要只给一个数"})

def qc_help():
    return _j({
        "version": QC_VERSION,
        "ops": {"load": "load(path) → (srgb[h,w,3], alpha[h,w])（路径支持 //rel、/mnt/...、D:/...）",
                "crop": "crop(arr, [x0,y0,x1,y1])", "mask": "mask(srgb, sat=0.13, v=0.74, fill=True)",
                "iou": "iou(a_mask, b_mask) → {iou,inter,union,a_px,b_px}",
                "profile": "profile(mask, bins=24) → 逐层宽度", "profile_diff": "profile_diff(pa,pb)",
                "compare": "compare(ref_path, ref_box, render_path, label) → iou+剖面差+两张对照图",
                "compare_auto": "compare_auto(ref_path, ref_box=None, render_path=None, label) → 自适应掩膜 + 对齐搜索的比对（推荐默认）",
                "align_iou": "align_iou(ref_path, ref_box=None, render_path=None) → 掩膜 bbox 紧裁后算 IoU（返回 JSON 标量）",
                "resize_mask": "resize_mask(mask,h,w)", "write_png": "write_png(path,w,h,rgb_bytes)"},
        "measures": {"aabb_err": "aabb_err(obj_name, target_size) → 包围盒尺寸误差（内环 score 用）",
                     "silhouette_iou": "silhouette_iou(view_spec, ref_path, ref_box) → 1-IoU（越小越好；会出图再比对）",
                     "profile_err": "profile_err(view_spec, ref_path, ref_box, bins) → 平均剖面差（像素）"},
        "boundary": "只做几何/轮廓类客观比对；审美判断仍归人/模型。阈值默认值来自 plush-build 会话的调参结果，可按项目覆盖。",
    })


# ---------------------------------------------------------------- 内环 measure 预置

