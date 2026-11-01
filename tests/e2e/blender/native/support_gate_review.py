# -*- coding: utf-8 -*-
"""Shared scene fixtures and gate presets for gate_review native E2E."""

import mathutils
import bmesh
import bpy


PREFIX = "DSHGATE_"

_parked = []          # [(name, hide_viewport, hide_render)] —— 临时藏起来的"别人的" mesh

TAIL = "assembly"     # preset 名（错误信息里用）


def mk_cube(name, loc=(0.0, 0.0, 0.0), size=2.0):
    me = bpy.data.meshes.new(PREFIX + name)
    ob = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size, matrix=mathutils.Matrix.Translation(loc))
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob.name


def mk_fine_cube(name, loc=(0.0, 0.0, 0.0), size=1.0, cuts=6):
    """细网格立方体（面尺寸 ≪ 壁厚）：F8-b 的**可信**一侧 —— 分辨率 ≤ min_mm 时 print 门才该 pass。"""
    me = bpy.data.meshes.new(PREFIX + name)
    ob = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size, matrix=mathutils.Matrix.Translation(loc))
    bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=cuts, use_grid_fill=True)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob.name


def mk_two_shells(name):
    """一个对象里放两块**互不相接**的壳（单件内部的浮壳）。"""
    me = bpy.data.meshes.new(PREFIX + name)
    ob = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.create_cube(bm, size=2.0, matrix=mathutils.Matrix.Translation((10.0, 0.0, 0.0)))
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob.name


def park_foreign():
    """把别的 mesh 对象临时隐藏：audit_connectivity 按 hide_render/hide_viewport 过滤，
    这样"范围内只有我的探针"才是真的（不动几何、跑完 unpark 复原）。"""
    _parked.clear()
    for ob in bpy.data.objects:
        if ob.type == "MESH" and not ob.name.startswith(PREFIX):
            _parked.append((ob.name, bool(ob.hide_viewport), bool(ob.hide_render)))
            ob.hide_viewport = True
            ob.hide_render = True


def unpark_foreign():
    for name, hv, hr in _parked:
        ob = bpy.data.objects.get(name)
        if ob is not None:
            ob.hide_viewport, ob.hide_render = hv, hr
    _parked.clear()


def purge_prefix():
    for ob in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        me = ob.data if ob.type == "MESH" else None
        bpy.data.objects.remove(ob, do_unlink=True)
        if me is not None and me.users == 0:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
    for me in [m for m in bpy.data.meshes if m.name.startswith(PREFIX) and m.users == 0]:
        try:
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass
    for mat in [m for m in bpy.data.materials if m.name.startswith(PREFIX) and m.users == 0]:
        try:
            bpy.data.materials.remove(mat, do_unlink=True)
        except Exception:
            pass


def run_preset(ctx, preset=TAIL, **kw):
    return ctx.api("gate")("run", dict({"preset": preset}, **kw))


def rows_of(r):
    return {x.get("id"): x for x in (r.get("gates") or [])}


def states(r):
    return {x.get("id"): x.get("state") for x in (r.get("gates") or [])}

