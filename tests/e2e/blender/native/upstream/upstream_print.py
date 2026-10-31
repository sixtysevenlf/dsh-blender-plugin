# -*- coding: utf-8 -*-
"""上游整合 A3 打印检查（原 upstream_integration_selftest.py 拆分）.

Split from the archived upstream_integration_selftest.py by T11a; T11b.8 runs it through
the native runner. The support module binds K and the original helper names to ctx.
"""
from __future__ import annotations

import json
import math

import bmesh
import bpy

import support as U



def t_print():
    plate = new_plate(PREFIX + "plate")
    w = api("print")("walls", {"objects": [plate.name], "min_mm": 10})
    row = w["objects"][0]
    chk("薄板能测出 5mm 壁厚（±0.5）", abs(float(row["min_mm"]) - 5.0) <= 0.5, row.get("min_mm"))
    chk("薄板 @min_mm=10 判为不合格且无分辨率警告",
        w.get("ok") is False and row["thin_samples"] > 0 and not row.get("warning"), row)
    o = api("print")("overhang", {"objects": [plate.name], "max_angle_deg": 45})
    orow = o["objects"][0]
    # 解析解：200×200×5mm 薄板总表面积 = 2*200*200 + 4*200*5 = 84000 mm²，正下方底面 40000 mm²。
    # v1.0.5（issue #14）：底面贴在平台上 ⇒ 归 bed_area_mm2，净悬垂 = 0；老口径字段仍给 40000 供对照。
    chk("平底薄板底面判为贴床面（40000 mm² / 净悬垂 0 / 老口径仍 40000）",
        abs(float(orow["bed_area_mm2"]) - 40000.0) <= 100.0
        and abs(float(orow["overhang_area_mm2"])) <= 1.0
        and abs(float(orow["overhang_area_incl_bed_mm2"]) - 40000.0) <= 100.0, orow)
    chk("贴床面占比 0.476（bed_ratio）", abs(float(orow["bed_ratio"]) - 0.476) <= 0.01, orow)
    chk("平底薄板默认过悬垂门（issue #14：不再恒判 false）", orow.get("ok") is True, orow)

    # 真悬垂不许被平台面吞掉：同一批里再放一块 100mm 见方、离台 50mm 的薄板（底面 10000 mm²）
    lifted = new_plate(PREFIX + "plate_lifted", size=0.05, lift=0.05)
    o2 = api("print")("overhang", {"objects": [plate.name, lifted.name], "max_angle_deg": 45})
    lrow = o2["objects"][1]
    chk("离台 50mm 的薄板底面仍计入悬垂（10000 mm²、贴床面 0）",
        abs(float(lrow["overhang_area_mm2"]) - 10000.0) <= 100.0
        and abs(float(lrow["bed_area_mm2"])) <= 1.0 and lrow.get("ok") is False, lrow)

    coarse = new_sphere(PREFIX + "coarse", segs=24, rings=12)
    w2 = api("print")("walls", {"objects": [coarse.name], "min_mm": 1})
    row2 = w2["objects"][0]
    chk("粗网格必须给 resolution_mm 与'结论不可信'警告",
        row2.get("resolution_mm") and row2.get("warning"), row2)

    # v1.0.5（issue #14）：把模块自检也纳入真机套件 —— 4 种形状（平底立方体 / 悬臂 / 平底碎块 / 蘑菇帽）
    st = api("print")("selftest", {})
    chk("print_selftest 自证：贴床面被排除、真悬垂仍计入、蘑菇帽不许假绿",
        bool(st.get("ok")), st.get("fails") or st.get("error"))



def test_print(ctx):
    U.install(globals(), ctx)
    U.run([t_print], "上游整合 A3 打印检查（原 upstream_integration_selftest.py 拆分）")
