# -*- coding: utf-8 -*-
"""Shared helpers for the native upstream E2E modules (T11b.8).

T11a split the original 856-line upstream script into 11 topic cases under
cases/.  T11b.8 moves those cases to the native runner: one module per topic,
one test_<topic>(ctx) entry point, and this support module keeps the original
vocabulary (chk/api/clean/new_sphere/new_plate/PREFIX) while binding it to the
native Context instead of a legacy kernel global.
"""
from __future__ import annotations


import bmesh
import bpy

PASS, FAILS, SKIP = [], [], []
PREFIX = "DSHST_"
K = None
CTX = None


def bind(ctx):
    """Bind this support module to one native Context.

    Modules import the helpers explicitly; no globals() injection is used.
    """
    global CTX, K, PASS, FAILS, SKIP
    CTX = ctx
    K = ctx.K
    PASS, FAILS, SKIP = [], [], []


def chk(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAILS.append({"clause": name, "detail": str(detail)[:400]})
    CTX.check(name, cond, detail)


def api(name):
    return CTX.api(name)


def clean():
    for ob in list(bpy.data.objects):
        if ob.name.startswith(PREFIX):
            me = ob.data if ob.type == "MESH" else None
            bpy.data.objects.remove(ob, do_unlink=True)
            if me is not None and me.users == 0:
                try:
                    bpy.data.meshes.remove(me, do_unlink=True)
                except Exception:
                    pass
    for me in list(bpy.data.meshes):
        if me.name.startswith(PREFIX) and me.users == 0:
            bpy.data.meshes.remove(me, do_unlink=True)


def new_sphere(name, segs=64, rings=32, radius=1.0):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=radius)
    except TypeError:
        bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, diameter=radius)
    bm.to_mesh(me)
    bm.free()
    return ob


def new_plate(name, size=0.1, segs=50, thick=0.005, lift=0.0):
    """size=0.1 => 200mm square; thick=0.005 => 5mm; lift raises it along +Z."""
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=segs, y_segments=segs, size=size)
    r = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
    vs = [e for e in r["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=vs, vec=(0.0, 0.0, thick))
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    if lift:
        bmesh.ops.translate(bm, verts=list(bm.verts), vec=(0.0, 0.0, lift))
    bm.to_mesh(me)
    bm.free()
    return ob


def run(functions, title):
    before = len(bpy.data.objects)
    print("== " + title + " ==")
    for fn in functions:
        try:
            fn()
        except Exception as e:
            chk(fn.__name__ + " 未抛异常", False, "%s: %s" % (type(e).__name__, str(e)[:200]))
        clean()
    CTX.check("临时对象已清干净（场景对象数复原）", len(bpy.data.objects) == before,
              "%d -> %d" % (before, len(bpy.data.objects)))
    print("NATIVE_UPSTREAM %s: pass=%d fail=%d skipped=%d" % (title, len(PASS), len(FAILS), len(SKIP)))
