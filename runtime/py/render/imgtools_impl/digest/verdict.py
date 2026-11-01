# -*- coding: utf-8 -*-
"""img_digest 阶段 5：verdict 与 未知/待确认/下一步（行为与拆分前一致）。"""
from ..common import *  # noqa: F401,F403


def _view_count(views):
    if views is None:
        return None
    if isinstance(views, str):
        return 1
    try:
        return len(list(views))
    except Exception:
        return None


def _digest_verdict(ctx, views):
    """verdict 三态 + unknown/to_confirm/next；返回 (verdict, unknown, to_confirm, nexts)。"""
    ppm = ctx["ppm"]
    unknown, to_confirm, nexts = [], [], []
    n_views = _view_count(views)
    if ppm is None:
        verdict = "needs_scale"
        unknown.append("px_per_mm 未标定：所有 mm 字段为 null（不猜）")
        nexts.append("给 known_len（标尺两端点或已知边长）重跑 img_digest，或先拍标尺")
    elif n_views == 1:
        verdict = "needs_views"
        nexts.append("再补一个正交视图（front/side/top）后重跑，verdict 才会到 ready_for_spec")
    elif float(ctx["cc_frac"]) < 0.5 or float(ctx["conf"]) < 0.3 or float(ctx["filled"].mean()) > 0.98:
        verdict = "degraded"
        to_confirm.append("掩膜可疑（最大连通域占 %.0f%%，置信度 %.2f）：先抠图或给 threshold 再量"
                          % (ctx["cc_frac"] * 100.0, ctx["conf"]))
    elif ctx["contour"] is None or len(ctx["poly"]) < 3:
        verdict = "degraded"
        to_confirm.append("轮廓跟踪失败：图可能过小/过碎")
    else:
        verdict = "ready_for_spec"
        nexts.append("mm 数可直接进 spec.py；用 img_measure 对期望值出 delta 表")
    native_px, W, H = ctx["native_px"], ctx["W"], ctx["H"]
    if int(native_px[0]) * int(native_px[1]) > W * H:
        unknown.append("原图 %dx%d > max_px，已降采样到 %dx%d：px 坐标是 loaded 尺度（native = loaded × %s）"
                       % (native_px[0], native_px[1], W, H,
                          round(float(native_px[0]) / float(W), 4)))
        to_confirm.append("关键特征请用 crops（原生裁剪、不缩放）复核")
    if float(ctx["cc_frac"]) < 0.85:
        to_confirm.append("前景分成多块（最大连通域占 %.0f%%）：可能有分离件或被遮断" % (ctx["cc_frac"] * 100.0))
    if float(ctx["conf"]) < 0.5:
        to_confirm.append("掩膜置信度 %.2f 偏低：照片先抠图（rembg）或显式给 threshold" % float(ctx["conf"]))
    if not ctx["lines"]:
        to_confirm.append("没有稳定直线：图可能模糊/低对比，或全是曲线")
    if not ctx["holes_raw"]:
        unknown.append("没检到内部孔洞（若有孔，可能被 alpha 吞掉或对比不足）")
    if ctx["unreadable_rows"]:
        to_confirm.append("有 %d 个特征 <8px：计数/形状不可靠，别写进 spec" % len(ctx["unreadable_rows"]))
    sym = ctx["sym"]
    if sym.get("score") is not None and float(sym["score"]) < 0.8:
        unknown.append("对称性弱（mirror IoU=%.2f）：可能不是镜像件，或取景有透视" % float(sym["score"]))
    nexts.append("要某高度宽度：img_probe(op='scan_y', y_px=…)；要圆孔：img_probe(op='circle', x_px=…, y_px=…)")
    return verdict, unknown, to_confirm, nexts
