# -*- coding: utf-8 -*-
"""Shared mesh fixtures and audit API helpers for audit_review native E2E."""

import mathutils
import bmesh
import bpy


PREFIX = "DSHAUDREG_"
SCALE_TOL = 1e-6            # scale_length 是 float32 属性（0.001 存成 0.001000000047…）
CALIB_SCALE = 0.001


def near(a, b, tol=SCALE_TOL):
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))
    except Exception:
        return False


# ------------------------------------------------------------------ 造探针网格

def add_cube(bm, size=2.0, loc=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    """往 bm 里加一个盒子，返回**这次新加的面**（用于只翻转其中一块壳）。"""
    before = set(bm.faces)
    m = mathutils.Matrix.Translation(loc) @ mathutils.Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0))
    bmesh.ops.create_cube(bm, size=size, matrix=m)
    return [f for f in bm.faces if f not in before]


def mk(name, builder):
    me = bpy.data.meshes.new(PREFIX + name)
    ob = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    builder(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob.name


def mesh_info(ctx, name, **kw):
    """audit_mesh 单对象便利封装：返回 (整包回执, 该对象行)。"""
    args = {"objects": [name], "max_issues": 5}
    args.update(kw)
    r = ctx.api("audit")("mesh", args)
    row = (r.get("objects") or [{}])[0]
    return r, row


def shell_of(row, state):
    return [s for s in (row.get("normals_shells") or []) if s.get("state") == state]


