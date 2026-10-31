# -*- coding: utf-8 -*-
"""上游整合 A2 网格修复/空网格审计（原 upstream_integration_selftest.py 拆分）.

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


def t_fix_and_audit():
    ob = new_sphere(PREFIX + "broken")
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    dup = tuple(bm.verts[2].co)
    f = bm.faces[0]
    f.verts[1].co = f.verts[0].co.copy()          # 零面积面
    bm.verts.new((5, 5, 5))                       # 孤立点
    bm.verts.new(dup)                             # 重复点
    bm.to_mesh(me)
    bm.free()
    me.update()
    a0 = api("audit")("mesh", {"objects": [ob.name]})
    chk("audit_mesh 能看见缺陷", (a0["totals"]["loose_verts"] or 0) >= 1, a0["totals"])
    fx = api("fix")("repair", {"objects": [ob.name],
                               "actions": ["merge_doubles", "dissolve_degenerate", "delete_loose", "recalc_normals"]})
    t = fx["objects"][0]
    chk("fix_repair 修完自证（前后计数 + 真非流形=0）",
        fx.get("ok") and t["after"]["loose_verts"] == 0 and t["real_nonmanifold"] == 0, t)
    a1 = api("audit")("mesh", {"objects": [ob.name]})
    chk("audit_mesh 复检干净", (a1["totals"]["loose_verts"] or 0) == 0, a1["totals"])

    empty = bpy.data.meshes.new(PREFIX + "empty")
    eo = bpy.data.objects.new(PREFIX + "empty", empty)
    bpy.context.scene.collection.objects.link(eo)
    a2 = api("audit")("mesh", {"objects": [eo.name]})
    chk("空网格不再让 audit_mesh 崩（回归：empty_objects）",
        a2.get("ok") is True and (a2["totals"].get("empty_objects") or 0) == 1 and a2.get("clean") is False, a2.get("totals"))


if __name__ == "__main__":
    U.run([t_fix_and_audit], "上游整合 A2 网格修复/空网格审计（原 upstream_integration_selftest.py 拆分）")
