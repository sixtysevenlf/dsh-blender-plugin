# -*- coding: utf-8 -*-
"""上游整合 A2 UV 自检（原 upstream_integration_selftest.py 拆分）.

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


def t_uv():
    me = bpy.data.meshes.new(PREFIX + "box")
    ob = bpy.data.objects.new(PREFIX + "box", me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me)
    bm.free()
    r = api("uv")("smart_project", {"objects": [ob.name], "angle_limit": 66, "island_margin": 0.01})
    row = r["objects"][0]
    chk("uv_smart_project 产出 UV 且零面积面=0",
        r.get("ok") and row.get("uv_layers") and row.get("degenerate_uv_faces") == 0, row)
    chk("uv_unwrap 传错 method 回允许列表",
        "allowed" in api("uv")("unwrap", {"objects": [ob.name], "method": "NOT_A_METHOD"}))
    st = api("uv")("stats", {"objects": [ob.name]})
    chk("uv_stats 判据（有层 + 零面积=0 ⇒ ok）", st.get("ok") is True, st.get("objects"))


if __name__ == "__main__":
    U.run([t_uv], "上游整合 A2 UV 自检（原 upstream_integration_selftest.py 拆分）")
