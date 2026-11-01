# -*- coding: utf-8 -*-
"""上游整合 A5 形状还原（原 upstream_integration_selftest.py 拆分）.

Split from the archived upstream_integration_selftest.py by T11a; T11b.8 runs it through
the native runner. The support module binds K and the original helper names to ctx.
"""
from __future__ import annotations

import math

import bpy

import support as U
from support import PREFIX, chk



def t_rectify_and_polyline():
    """照片透视校正（四角单应）与折线族拟合（更强形状族 + K 扫描）。"""
    img = getattr(U.K, "dsh_img_api", None)
    v = getattr(U.K, "dsh_vehicle_api", None)
    if img is None or v is None:
        raise RuntimeError("模块没注入：K.dsh_img_api / K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    f2 = v("fit", {"stations": sts, "family": "polyline", "control_points": [6, 12], "iterations": 120})
    chk("折线族（闭合剖面）拟合跑通", bool(f2.get("ok")) and f2.get("mode") == "polyline_closed", f2.get("mode"))
    curve = f2.get("curve") or []
    chk("控制点数越多 IoU 越高（K↔IoU 曲线单调）",
        len(curve) == 2 and float(curve[1]["iou"]) >= float(curve[0]["iou"]) - 1e-6,
        [(q.get("control_points"), q.get("iou")) for q in curve])
    cargen = v("fit", {"stations": sts, "iterations": 60})
    chk("折线族天花板高于 cargen 族（0.67 → 0.72+）",
        float((curve[-1] if curve else {}).get("iou") or 0) > float(cargen.get("iou") or 0),
        {"polyline": (curve[-1] if curve else {}).get("iou"), "cargen": cargen.get("iou")})


def t_feature_lines():
    """特征线层：折痕落在指定高度/位置；凹陷按 A/B 量出真实收窄；横向线自动插站。"""
    v = getattr(U.K, "dsh_vehicle_api", None)
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    base = {"stations": sts, "section_pts": 24, "subsurf": 1}
    a = v("loft", dict(base, name=PREFIX + "fl_a"))
    b = v("loft", dict(base, name=PREFIX + "fl_b",
                       crease_lines=[{"z_mm": 560, "tol_mm": 45, "x_mm": [400, 3900], "weight": 0.85},
                                     {"x_mm": 1500, "tol_mm": 70, "weight": 1.0}],
                       inset_lines=[{"z_mm": 470, "band_mm": 60, "inset_mm": 12, "x_mm": [500, 3800]}]))
    chk("特征线（腰线 + 横向）都命中了环边",
        all(int(r.get("edges") or 0) > 0 for r in (b.get("crease_lines") or [])),
        [(r.get("spec"), r.get("edges")) for r in (b.get("crease_lines") or [])])
    chk("横向特征线不在站位上时自动插站", int(b.get("stations_inserted") or 0) >= 1, b.get("stations_inserted"))

    def band_w(name, zc, tol=0.02):
        ob = bpy.data.objects.get(name)
        if ob is None:
            return None
        ys = [abs(x.co.y) for x in ob.data.vertices if abs(x.co.z - zc) <= tol]
        return (max(ys) * 1000.0) if ys else None

    wa, wb = band_w(a.get("object"), 0.470), band_w(b.get("object"), 0.470)
    chk("凹陷在指定高度带真的收窄（A/B 同带对比 ≥5mm）",
        wa is not None and wb is not None and (wa - wb) >= 5.0, {"without": wa, "with": wb})
    chk("凹陷点数 > 0（记录在案）", int(b.get("inset_hits") or 0) > 0, b.get("inset_hits"))


def t_crease_radius():
    """圆角特征线：radius_mm 走 Bevel 权重 + Bevel 修改器（真半径圆弧，而非折痕权重近似）。"""
    v = getattr(U.K, "dsh_vehicle_api", None)
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    base = {"stations": sts, "section_pts": 20, "subsurf": 1, "n_top": 5.0, "n_bot": 3.0, "tumblehome_mm": 120.0}
    hard = v("loft", dict(base, name=PREFIX + "cr_hard", crease_lines=[{"frac": 0.55, "weight": 0.8}]))
    rnd = v("loft", dict(base, name=PREFIX + "cr_rnd",
                         crease_lines=[{"frac": 0.55, "radius_mm": 6.0, "segments": 3},
                                       {"x_mm": 1500, "radius_mm": 4.0, "segments": 2}]))
    chk("圆角特征线命中（bevel 权重边 > 0）",
        bool(rnd.get("ok")) and int((rnd.get("bevel") or {}).get("edges") or 0) > 0, rnd.get("bevel"))
    chk("圆角真的加了几何（顶点数 > 硬折线版）",
        int(rnd.get("vertices") or 0) > int(hard.get("vertices") or 0),
        {"hard": hard.get("vertices"), "rounded": rnd.get("vertices")})
    ob = bpy.data.objects.get(rnd.get("object"))
    chk("Bevel 修改器已落地（栈里不残留）", ob is not None and len(ob.modifiers) == 0,
        [m.name for m in (ob.modifiers if ob else [])])
    chk("半径/段数回执正确", (rnd.get("bevel") or {}).get("radius_mm") == 6.0
        and (rnd.get("bevel") or {}).get("segments") == 3, rnd.get("bevel"))


def t_shape_layer():
    """通用还原方法层：类别协议齐全 + 旋转体通路 + 委托量具/放样仍可用。"""
    sh = getattr(U.K, "dsh_shape_api", None)
    if sh is None:
        raise RuntimeError("模块没注入：K.dsh_shape_api（preload 里加 shapegen？）")
    st = sh("selftest", {})
    chk("shape 自检（类别协议无缺项 + 旋转体）", bool(st.get("ok")), (st.get("evidence") or {}).get("missing_fields"))
    chk("类别数 >= 8（vehicle/humanoid/furniture/hull/aircraft/rotational/weapon/generic…）",
        int((st.get("evidence") or {}).get("classes") or 0) >= 8, (st.get("evidence") or {}).get("classes"))
    for cls in ("vehicle", "humanoid", "furniture", "rotational"):
        p = sh("plan", {"object_class": cls})
        chk("协议 %s 给全视图/比例/算子/验收/坑" % cls,
            bool(p.get("ok")) and all((p.get("plan") or {}).get(k) for k in ("views", "ratios", "ops", "gates", "traps")),
            cls)
    # 选路协议：每类都必须给"用哪条截面通路"，且决策规则齐全（防以后加类别漏配）
    ev = st.get("evidence") or {}
    chk("每类都有截面通路条目（section_path 无缺项）", not (ev.get("missing_section_path") or []),
        ev.get("missing_section_path"))
    chk("截面通路决策规则 >= 4 条", int(ev.get("decision_rules") or 0) >= 4, ev.get("decision_rules"))
    air = sh("plan", {"object_class": "aircraft"})
    chk("翼型类明确指向 section_outline（不是镜像对称那条路）",
        "section_outline" in str((air.get("plan") or {}).get("section_path") or ""),
        (air.get("plan") or {}).get("section_path"))
    rot = sh("plan", {"object_class": "rotational"})
    chk("轴对称类明确指向 shape_revolve（并提示不要放样）",
        "shape_revolve" in str((rot.get("plan") or {}).get("section_path") or ""),
        (rot.get("plan") or {}).get("section_path"))
    rv = sh("revolve", {"profile": [[0, 0], [25, 0], [25, 80], [12, 110], [0, 120]], "name": PREFIX + "rv"})
    chk("旋转体通路（瓶/罐/轮毂）出几何且高度吻合",
        bool(rv.get("ok")) and abs((rv.get("size_mm") or {}).get("h", 0) - 120.0) < 1.0, rv.get("size_mm"))


def t_shape_extensions():
    """通用还原扩展：自定义/非对称截面（翼型）· 圆角 G2 控制 · 旋转体多体量装配。"""
    import bpy  # noqa: F401
    v = getattr(U.K, "dsh_vehicle_api", None)
    sh = getattr(U.K, "dsh_shape_api", None)
    if v is None or sh is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api / K.dsh_shape_api")
    prof = [(0, 400, 110, 600), (900, 470, 289, 900), (1900, 760, 110, 900), (2900, 560, 110, 900), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    outl = []
    for i in range(21):
        xx = abs(2 * (i / 20.0) - 1)
        yt = 5 * 0.12 * (0.2969 * math.sqrt(max(1e-9, 1 - xx ** 2)) - 0.1260 * (1 - xx ** 2)
                         - 0.3516 * (1 - xx ** 2) ** 2 + 0.2843 * (1 - xx ** 2) ** 3)
        outl.append([round((yt + 0.04 * (1 - xx ** 2)) * 1000, 2), round(xx * 1000, 1)])
    for i in range(19, -1, -1):
        xx = abs(2 * (i / 20.0) - 1)
        yt = 5 * 0.12 * (0.2969 * math.sqrt(max(1e-9, 1 - xx ** 2)) - 0.1260 * (1 - xx ** 2)
                         - 0.3516 * (1 - xx ** 2) ** 2 + 0.2843 * (1 - xx ** 2) ** 3)
        outl.append([round((-yt + 0.04 * (1 - xx ** 2)) * 1000, 2), round(xx * 1000, 1)])
    a = v("loft", {"stations": sts, "name": PREFIX + "airfoil", "section_outline": outl, "subsurf": 1})
    chk("翼型/自定义截面被采纳（section_source=custom_outline）",
        bool(a.get("ok")) and a.get("section_source") == "custom_outline", a.get("section_source"))
    ob = bpy.data.objects.get(a.get("object"))
    ys = [q.co.y * 1000.0 for q in ob.data.vertices] if ob else []
    chk("截面左右**不对称**（翼型有弯度 ⇒ |ymin| ≠ |ymax|）",
        ys and abs(abs(min(ys)) - abs(max(ys))) > 20.0, {"ymin": round(min(ys), 1), "ymax": round(max(ys), 1)})
    b = v("loft", {"stations": sts, "name": PREFIX + "g2", "section_pts": 20, "subsurf": 1,
                   "crease_lines": [{"frac": 0.55, "radius_mm": 8.0, "segments": 4, "profile": 0.75}]})
    chk("圆角剖面可控（profile 0.75 = 偏方/G2 近似）",
        bool(b.get("ok")) and abs(float((b.get("bevel") or {}).get("profile") or 0) - 0.75) < 1e-6, b.get("bevel"))
    c = sh("revolve", {"name": PREFIX + "rv", "parts": [
        {"profile": [[0, 0], [40, 0], [40, 30], [0, 30]], "at": [0, 0, 0], "name": PREFIX + "hub"},
        {"profile": [[0, 0], [30, 0], [30, 20], [0, 20]], "at": [200, 0, 0], "name": PREFIX + "tire"}]})
    chk("旋转体多体量装配（2 件独立对象 + 各自落点）",
        bool(c.get("ok")) and int(c.get("count") or 0) == 2, [q.get("object") for q in (c.get("parts") or [])])
    tire = bpy.data.objects.get(PREFIX + "tire")
    chk("落点写进对象（200mm → 0.2m）", tire is not None and abs(tire.location.x - 0.2) < 1e-6,
        [round(x, 3) for x in (tire.location if tire else [])])



def test_shape(ctx):
    U.bind(ctx)
    U.run([t_rectify_and_polyline, t_feature_lines, t_crease_radius, t_shape_layer, t_shape_extensions], "上游整合 A5 形状还原（原 upstream_integration_selftest.py 拆分）")
