# -*- coding: utf-8 -*-
"""上游整合 A4 audit 瘦身与连通岛分栏（原 upstream_integration_selftest.py 拆分）.

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


def t_audit_slim():
    """D3 瘦身：summary_only / top_k 要既省字符又不改判定（clean 与 totals 必须一模一样）。"""
    import bmesh as _bm
    names = []
    for i in range(40):
        me = bpy.data.meshes.new(PREFIX + "s%02d" % i)
        ob = bpy.data.objects.new(PREFIX + "s%02d" % i, me)
        bpy.context.scene.collection.objects.link(ob)
        bm = _bm.new()
        _bm.ops.create_cube(bm, size=0.5)
        if i % 4 == 0:                      # 1/4 带缺陷：孤立点 + 重合点
            bm.verts.new((9, 9, 9))
            bm.verts.ensure_lookup_table()
            bm.verts.new(tuple(bm.verts[0].co))
        bm.to_mesh(me)
        bm.free()
        names.append(ob.name)
    full = api("audit")("scene", {})
    slim = api("audit")("scene", {"summary_only": True, "top_k": 5})
    tiny = api("audit")("scene", {"summary_only": True, "top_k": 2})
    chk("瘦身后判定不变（clean 一致）", full.get("clean") == slim.get("clean") == tiny.get("clean"),
        "%s/%s/%s" % (full.get("clean"), slim.get("clean"), tiny.get("clean")))
    chk("瘦身后聚合不变（totals 一致）", full.get("totals") == slim.get("totals"))
    chk("默认仍是全量（40 行）", len(full.get("worst", [])) == 40, len(full.get("worst", [])))
    chk("top_k 生效（5 / 2 行）", len(slim.get("worst", [])) == 5 and len(tiny.get("worst", [])) == 2,
        [len(slim.get("worst", [])), len(tiny.get("worst", []))])
    f_chars = len(json.dumps(full, ensure_ascii=False))
    s_chars = len(json.dumps(slim, ensure_ascii=False))
    chk("场景瘦身 >=70%% 字符（实测 %d -> %d）" % (f_chars, s_chars), s_chars <= f_chars * 0.3,
        "%d -> %d" % (f_chars, s_chars))
    made = _bm.new()
    _bm.ops.create_cube(made, size=1.0)
    me2 = bpy.data.meshes.new(PREFIX + "selfx")
    _bm.ops.create_cube(made, size=1.2, matrix=__import__("mathutils").Matrix.Translation((0.5, 0, 0)))
    made.to_mesh(me2)
    made.free()
    ob2 = bpy.data.objects.new(PREFIX + "selfx", me2)
    bpy.context.scene.collection.objects.link(ob2)
    mf = api("audit")("mesh", {"objects": [ob2.name], "self_intersect": True, "max_issues": 20})
    ms = api("audit")("mesh", {"objects": [ob2.name], "self_intersect": True, "summary_only": True})
    chk("mesh 瘦身去掉明细字段（self_intersection_pairs）",
        "self_intersection_pairs" not in (ms.get("objects") or [{}])[0]
        and "self_intersection_pairs" in (mf.get("objects") or [{}])[0])
    chk("mesh 瘦身后判据字段仍在（nonmanifold/boundary/closed）",
        all(k in (ms.get("objects") or [{}])[0] for k in ("nonmanifold_edges", "boundary_edges", "closed")))
    dup = api("audit")("duplicates", {"top_k": 1})
    chk("audit_duplicates 的 top_k/limit 不再是静默参数（回 slim）", "slim" in dup, dup.get("slim"))


def t_island():
    """反馈 #1：自交按连通岛分栏 + 小岛过滤（铆钉压入宿主属良性，不该和真互穿混在一起）。"""
    import bmesh as _bm
    from mathutils import Matrix as _M
    me = bpy.data.meshes.new(PREFIX + "isl")
    ob = bpy.data.objects.new(PREFIX + "isl", me)
    bpy.context.scene.collection.objects.link(ob)
    bm = _bm.new()
    _bm.ops.create_cube(bm, size=2.0)
    _bm.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=3, use_grid_fill=True)   # 宿主：顶点多
    _bm.ops.create_cube(bm, size=0.5, matrix=_M.Translation((1.0, 0.0, 0.0)))    # 铆钉：8 顶点小岛，穿宿主面
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.to_mesh(me)
    bm.free()
    n_verts = len(me.vertices)
    r = api("audit")("mesh", {"objects": [ob.name], "self_intersect": True, "island_split": True,
                              "summary_only": True, "max_issues": 5})
    info = (r.get("objects") or [{}])[0]
    chk("分岛：认出两个岛（宿主 + 铆钉）", (info.get("islands") or {}).get("count") == 2,
        info.get("islands"))
    chk("分岛：跨岛相交 > 0 且同岛 = 0", (info.get("self_intersections_cross_island") or 0) > 0
        and (info.get("self_intersections_same_island") or 0) == 0,
        {"same": info.get("self_intersections_same_island"), "cross": info.get("self_intersections_cross_island")})
    r2 = api("audit")("mesh", {"objects": [ob.name], "self_intersect": True, "island_split": True,
                               "min_island_verts": n_verts, "summary_only": True, "max_issues": 5})
    i2 = (r2.get("objects") or [{}])[0]
    chk("小岛过滤：铆钉（8 顶点 < %d）相关的相交全被滤掉、kept=0" % n_verts,
        (i2.get("self_intersections_filtered_small_island") or 0) > 0 and (i2.get("self_intersections_kept") or 0) == 0,
        {"filtered": i2.get("self_intersections_filtered_small_island"), "kept": i2.get("self_intersections_kept"),
         "small_islands": (i2.get("islands") or {}).get("small_islands")})
    chk("小岛过滤开关默认关（不传 min_island_verts 时不过滤）",
        "self_intersections_filtered_small_island" not in (api("audit")("mesh", {"objects": [ob.name],
         "self_intersect": False, "summary_only": True}).get("objects") or [{}])[0])
    # 同岛自交：把两个立方体用一条边连起来 ⇒ 变成一个岛，互穿记成 same_island
    me2 = bpy.data.meshes.new(PREFIX + "isl2")
    ob2 = bpy.data.objects.new(PREFIX + "isl2", me2)
    bpy.context.scene.collection.objects.link(ob2)
    bm2 = _bm.new()
    _bm.ops.create_cube(bm2, size=2.0)
    _bm.ops.create_cube(bm2, size=0.5, matrix=_M.Translation((1.0, 0.0, 0.0)))
    bm2.verts.ensure_lookup_table()
    bm2.edges.new((bm2.verts[0], bm2.verts[8]))     # 一条边把两块壳连成一个岛
    bm2.to_mesh(me2)
    bm2.free()
    i3 = (api("audit")("mesh", {"objects": [ob2.name], "self_intersect": True, "island_split": True,
                                "summary_only": True, "max_issues": 5}).get("objects") or [{}])[0]
    chk("单岛（一条边相连）⇒ 记成同岛自交", (i3.get("islands") or {}).get("count") == 1
        and (i3.get("self_intersections_same_island") or 0) > 0,
        {"islands": i3.get("islands"), "same": i3.get("self_intersections_same_island")})


if __name__ == "__main__":
    U.run([t_audit_slim, t_island], "上游整合 A4 audit 瘦身与连通岛分栏（原 upstream_integration_selftest.py 拆分）")
