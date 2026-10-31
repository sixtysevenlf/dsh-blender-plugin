# -*- coding: utf-8 -*-
"""上游整合 A5 装配与车辆（原 upstream_integration_selftest.py 拆分）.

Split from cases/upstream_integration_selftest.py by T11a. The runner injects K;
the shared helper supplies the original chk/api/clean/new_sphere/new_plate names.
"""
from __future__ import annotations

import json
import math

import bmesh
import bpy

import _upstream_common as U

U.bind(K)
U.install(globals())


def t_conn():
    """反馈 #2：三态显式成 verdict（degraded 不再像绿）+ 逐对象复核（整体超面数上限时的正解）。"""
    import bmesh as _bm
    from mathutils import Matrix as _M
    names = {}
    # solid = 两块**互插** 0.1 的立方体（工艺要求拼件互插 5–15mm ⇒ 连通门该过）；
    # float = 同一 mesh 里两块相距 5 的立方体（孤立的那块是可见浮块 ⇒ 该挂）
    # solid = 两块**互插** 0.1（工艺要求）；touch = 两块**正好相贴**（gap=0.00 mm，被焊接成一体）；
    # float = 同一 mesh 里两块相距 5（孤立的那块是可见浮块 ⇒ 该挂）
    for tag, parts in (("solid", [(0, 0, 0), (0.9, 0, 0)]), ("touch", [(0, 0, 0), (1.0, 0, 0)]),
                       ("float", [(0, 0, 0), (5, 0, 0)])):
        me = bpy.data.meshes.new(PREFIX + tag)
        ob = bpy.data.objects.new(PREFIX + tag, me)
        bpy.context.scene.collection.objects.link(ob)
        bm = _bm.new()
        for p in parts:
            _bm.ops.create_cube(bm, size=1.0, matrix=_M.Translation(p))
        bm.to_mesh(me)
        bm.free()
        names[tag] = ob.name
    g1 = api("audit")("gate", {"objects": [names["solid"]]})
    chk("连通门带 verdict 字段（三态显式化）", g1.get("verdict") == "pass", g1.get("verdict"))
    gt = api("audit")("gate", {"objects": [names["touch"]]})
    gc = gt.get("verdicts", {}).get("connectivity") or {}
    chk("正好相贴（gap=0.00 mm）算相接、不判浮块 ⇒ pass 且可见浮块=0",
        gt.get("verdict") == "pass" and gc.get("visible_floater_count") == 0,
        {"v": gt.get("verdict"), "visible": gc.get("visible_floater_count")})
    gf = api("audit")("gate", {"objects": [names["float"]]})
    chk("留出大间隙的孤立块仍判浮块 ⇒ fail", gf.get("verdict") == "fail", gf.get("verdict"))
    r = api("audit")("connectivity", {"objects": [names["solid"], names["float"]], "per_object": True})
    chk("per_object：逐对象出 verdict，且点名哪件挂了",
        r.get("per_object") is True and r.get("verdict") == "fail"
        and names["float"] in (r.get("failed_objects") or []),
        {"v": r.get("verdict"), "failed": r.get("failed_objects")})
    chk("per_object：每件都带 state/verdict/tris 与原因位",
        bool(r.get("objects")) and all(("state" in o and "verdict" in o and "tris" in o and "reason" in o)
                                       for o in r["objects"]),
        (r.get("objects") or [{}])[0])
    g2 = api("audit")("gate", {"objects": [names["solid"]], "max_tris": 1})
    chk("超复核上限 ⇒ state=degraded 且 verdict=degraded（不是 pass）",
        g2.get("state") == "degraded" and g2.get("verdict") == "degraded", 
        {"state": g2.get("state"), "verdict": g2.get("verdict")})
    chk("degraded 带醒目 warn：DEGRADED ≠ 通过", "DEGRADED" in (g2.get("warn") or ""), g2.get("warn"))


def t_human():
    """人形素体：高度误差、几何头身比、关节标记、头型与五官标记（零素材路线）。"""
    g = getattr(K, "dsh_human_api", None)  # noqa: F821
    if g is None:
        raise RuntimeError("模块没注入：K.dsh_human_api（preload 里漏了 human？）")
    r = g("base", {"height_mm": 1700.0, "heads": 7.5, "name": PREFIX + "human"})
    hre = r.get("height_rel_err")
    chk("human_base 高度误差 <0.5%", bool(r.get("ok")) and hre is not None and hre < 0.005,
        {"height_mm": r.get("height_mm"), "rel_err": hre})
    hm = r.get("heads_measured")
    chk("human_base 几何头身比在目标 ±5%", hm is not None and abs(hm - 7.5) / 7.5 <= 0.05, hm)
    chk("human_base 出关节标记 >=12 个", int(r.get("marker_count") or 0) >= 12, r.get("marker_count"))
    chk("human_base 顶点量在合理区间（2k–200k）",
        2000 <= int(r.get("vertices") or 0) <= 200000, r.get("vertices"))
    m = g("measure", {"name": PREFIX + "human"})
    chk("human_measure 读得到头高与头身比", bool(m.get("head_mm")) and bool(m.get("heads")),
        {"head_mm": m.get("head_mm"), "heads": m.get("heads")})
    lm = {"top": [0, 0], "chin": [0, 400], "face_l": [-150, 200], "face_r": [150, 200],
          "eye_l": [-70, 200], "eye_r": [70, 200], "nose_base": [0, 290]}
    h = g("head", {"landmarks": lm, "head_mm": 230.0, "name": PREFIX + "head"})
    chk("human_head 出五官定位标记 >=5 个", bool(h.get("ok")) and len(h.get("markers") or []) >= 5,
        len(h.get("markers") or []))
    hh = (h.get("size_mm") or {}).get("h") or 0
    chk("human_head 头高误差 <5%", abs(hh - 230.0) / 230.0 <= 0.05, hh)
    chk("human_head 从 landmark 推出眼线位置（≈0.50）",
        abs((h.get("landmark_fracs") or {}).get("eye_line_frac", 0) - 0.5) <= 0.05,
        (h.get("landmark_fracs") or {}).get("eye_line_frac"))


def t_vehicle():
    """车辆外壳：比例门（WBR 标定）+ 放样 + 轮与壳零互穿（布尔轮眉的构造保证）。"""
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    c = getattr(K, "dsh_clearance_api", None)  # noqa: F821
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api（preload 里漏了 vehicle？）")
    sp = v("spec", {"type": "sports", "length_mm": 4300.0, "width_mm": 1850.0, "height_mm": 1250.0,
                    "wheelbase_mm": 2600.0, "front_axle_from_front_mm": 900.0, "wheel_r_mm": 340.0,
                    "wheel_w_mm": 245.0, "track_mm": 1580.0})
    pk = v("package", {"spec": sp})
    chk("车辆比例门（真车标定的 WBR / 轴距比 / 高长比）该过", bool(pk.get("ok")), pk.get("failed"))
    bad = v("spec", {"type": "sports", "wheel_r_mm": 150.0})
    chk("轮径明显偏小 ⇒ 比例门点名 wheel_to_body_ratio",
        "wheel_to_body_ratio" in (v("package", {"spec": bad}).get("failed") or []))
    r = v("base", {"spec": sp, "name": PREFIX + "veh", "stations": 20, "section_pts": 12})
    chk("vehicle_base 建壳（尺寸吻合车长 ±60mm）",
        bool(r.get("ok")) and abs((r.get("size_mm") or {}).get("len", 0) - 4300.0) < 60.0, r.get("size_mm"))
    chk("四个轮都建出来了", len(r.get("wheels") or []) == 4, len(r.get("wheels") or []))
    if c is not None and r.get("wheel_objects"):
        cl = c("check", {"pairs": [{"id": "wheel_vs_shell", "a": [r["wheel_objects"][0]],
                                    "b": [r["object"]], "min_mm": 0.0}]})
        p0 = (cl.get("pairs") or [{}])[0]
        chk("轮与壳零互穿（布尔轮眉 ⇒ 构造保证）", p0.get("interpenetrating") is False,
            {"gap_mm": p0.get("gap_mm"), "inter": p0.get("interpenetrating")})
        chk("轮与壳间隙 ≈ arch_clear（默认 12mm 量到 6–14mm）",
            p0.get("gap_mm") is not None and 6.0 <= float(p0["gap_mm"]) <= 14.0, p0.get("gap_mm"))
    else:
        chk("clearance 模块可用（零互穿验收）", False, "未注入 clearance")


def t_clearance():
    """成对间隙门：10mm 过 / 2mm 挂 / 互穿挂 / 允许互插过。"""
    c = getattr(K, "dsh_clearance_api", None)  # noqa: F821
    if c is None:
        raise RuntimeError("模块没注入：K.dsh_clearance_api（preload 里漏了 clearance？）")
    r = c("selftest", {})
    chk("clearance 自检（四类规则一次判完）", bool(r.get("ok")), (r.get("evidence") or {}).get("top"))


def t_vehicle_shell():
    """外壳还原：站表放样（截面参数/轮眉外扩/腰线折痕）+ 按缝切分件（缝是真几何）。"""
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    c = getattr(K, "dsh_clearance_api", None)  # noqa: F821
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    r = v("loft", {"stations": sts, "name": PREFIX + "shell", "section_pts": 20, "n_top": 5.0, "n_bot": 3.0,
                   "tumblehome_mm": 120.0, "shoulder_inset_mm": 25.0, "sill_tuck_mm": 60.0,
                   "flare_mm": 30.0, "subsurf": 1, "crease_shoulder": 0.7})
    chk("站表放样成壳（车长吻合 ±80mm）",
        bool(r.get("ok")) and abs((r.get("size_mm") or {}).get("len", 0) - 4300.0) < 80.0, r.get("size_mm"))
    chk("腰线折痕生效（SubD 折痕环边 > 0）", int(r.get("crease_edges") or 0) > 0, r.get("crease_edges"))
    chk("轮眉外扩落在轮拱站位（不是最低车唇）",
        bool((r.get("flare") or {}).get("at_x_mm")), (r.get("flare") or {}).get("at_x_mm"))
    p = v("panels", {"object_name": r.get("object"), "cuts_mm": [1500.0, 3000.0], "gap_mm": 4.0})
    chk("按缝切分件成 3 件", bool(p.get("ok")) and len(p.get("parts") or []) == 3, p.get("parts"))
    if c is not None and len(p.get("parts") or []) >= 2:
        cl = c("check", {"pairs": [{"id": "seam", "a": [p["parts"][0]], "b": [p["parts"][1]], "min_mm": 0.0}]})
        q = (cl.get("pairs") or [{}])[0]
        chk("缝是真几何（≈4mm 且零互穿）",
            q.get("interpenetrating") is False and q.get("gap_mm") is not None
            and 3.0 <= float(q["gap_mm"]) <= 5.0, {"gap": q.get("gap_mm")})


def t_vehicle_fit_regions():
    """站表 → IoU 拟合闭环 + 多区域装配 + 前视截面轮廓（三件的数值门）。"""
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    ft = v("fit", {"stations": sts, "iterations": 120, "target_iou": 0.95})
    chk("站表→IoU 拟合闭环跑通并给出 IoU", bool(ft.get("ok")) and ft.get("iou") is not None, ft.get("iou"))
    chk("拟合不劣于解析初值（iou >= iou_start）",
        ft.get("iou") is not None and ft.get("iou_start") is not None
        and ft.get("iou") >= float(ft["iou_start"]) - 1e-6,
        {"start": ft.get("iou_start"), "end": ft.get("iou")})
    chk("解析反推给的初值高于默认初值（>0.5）",
        ft.get("iou_start") is not None and float(ft["iou_start"]) > 0.5, ft.get("iou_start"))
    rg = v("regions", {"stations": sts, "name": PREFIX + "reg",
                       "regions": [{"name": "a", "x0_mm": 0, "x1_mm": 1500},
                                   {"name": "b", "x0_mm": 1450, "x1_mm": 3000},
                                   {"name": "c", "x0_mm": 2950, "x1_mm": 4300}],
                       "loft_kw": {"subsurf": 1, "section_pts": 14}})
    chk("多区域装配成 3 个独立体量", bool(rg.get("ok")) and int(rg.get("count") or 0) == 3,
        [m.get("object") for m in (rg.get("regions") or [])])
    chk("每个区域各自有尺寸（互不粘连）",
        all((m.get("size_mm") or {}).get("len") for m in (rg.get("regions") or [])),
        [m.get("size_mm") for m in (rg.get("regions") or [])])
    shape = [{"z_frac": q, "hw_frac": 1.0 - 0.5 * q} for q in (0.0, 0.25, 0.5, 0.75, 1.0)]
    lo = v("loft", {"stations": sts, "name": PREFIX + "shape", "section_shape": shape, "subsurf": 1})
    chk("前视截面轮廓（section_shape）被放样采纳",
        bool(lo.get("ok")) and lo.get("section_source") == "front_view_shape", lo.get("section_source"))


if __name__ == "__main__":
    U.run([t_conn, t_human, t_vehicle, t_clearance, t_vehicle_shell, t_vehicle_fit_regions], "上游整合 A5 装配与车辆（原 upstream_integration_selftest.py 拆分）")
