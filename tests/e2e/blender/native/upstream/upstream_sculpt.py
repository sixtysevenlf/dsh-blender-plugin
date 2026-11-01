# -*- coding: utf-8 -*-
"""上游整合 A1 雕刻自检（原 upstream_integration_selftest.py 拆分）.

Split from the archived upstream_integration_selftest.py by T11a; T11b.8 runs it through
the native runner. The support module binds K and the original helper names to ctx.
"""
from __future__ import annotations

import json


import support as U
from support import PREFIX, api, chk, new_sphere



def t_sculpt():
    ob = new_sphere(PREFIX + "sphere")
    scan = api("sculpt")("scan", {"objects": [ob.name]})
    chk("sculpt_scan 基本读数", scan.get("ok") and scan["objects"][0]["verts"] > 1000, scan)
    chk("sculpt_scan 体素预算有解", scan["objects"][0].get("voxel_auto"), scan["objects"][0])

    r = api("sculpt")("apply", {"object": ob.name, "strokes": [
        {"brush": "draw", "points": [[0, 0, 1.0], [0.12, 0, 1.05]], "radius": 0.45, "strength": 0.7}]})
    s0 = r["results"][0]["strokes"][0]
    chk("sculpt_apply draw 真的动了网格", s0["affected_verts"] > 0 and s0["max_delta"] > 0, s0)
    chk("sculpt_apply 对称=独立笔触（不是串成假路径）",
        api("sculpt")("apply", {"object": ob.name, "strokes": [
            {"brush": "crease", "points": [[0.7, 0, 0.7]], "radius": 0.15, "strength": 0.5,
             "symmetry": ["x"]}]})["results"][0]["strokes"][0]["stroke_paths"] == 2)

    rm = api("sculpt")("remesh", {"objects": [ob.name], "mode": "VOXEL", "voxel_size": "auto"})
    chk("sculpt_remesh auto 体素重构", rm["objects"][0]["verts_after"] > 0, rm["objects"][0])
    bad = api("sculpt")("remesh", {"objects": [ob.name], "mode": "VOXEL", "voxel_size": 0.0005})
    chk("体素预算守卫拦下过小 voxel_size（并给可用尺寸）",
        bad.get("ok") is False and "会卡住 Blender" in json.dumps(bad, ensure_ascii=False), bad)
    chk("笔刷名写错要报错", api("sculpt")("apply", {"object": ob.name, "strokes": [
        {"brush": "nope", "points": [[0, 0, 1]], "radius": 0.2}]}).get("ok") is False)
    chk("参数名拼错要报错（不静默）",
        "不认识的参数" in json.dumps(api("sculpt")("apply", {"object": ob.name, "storkes": []}), ensure_ascii=False))



def test_sculpt(ctx):
    U.bind(ctx)
    U.run([t_sculpt], "上游整合 A1 雕刻自检（原 upstream_integration_selftest.py 拆分）")
