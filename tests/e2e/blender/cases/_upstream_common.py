# -*- coding: utf-8 -*-
"""Shared helpers for the split upstream integration E2E cases.

The legacy case was one 856-line script. T11a splits it into domain cases under
the cases/ directory. Each case calls bind(K) and install(globals()) so the
function bodies keep their original vocabulary (PASS/FAILS/SKIP, PREFIX,
chk/api/clean/new_sphere/new_plate) while the implementation lives in one place.
"""
from __future__ import annotations

import json

import bmesh
import bpy

PASS, FAILS, SKIP = [], [], []
PREFIX = "DSHST_"
K = None


def bind(kernel):
    global K
    K = kernel


def chk(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [OK]   " + name)
    else:
        FAILS.append({"clause": name, "detail": str(detail)[:400]})
        print("  [FAIL] " + name + "  <- " + str(detail)[:300])


def api(name):
    a = getattr(K, "dsh_" + name + "_api", None)
    if a is None:
        raise RuntimeError("模块没注入：dsh_%s_api（preload 里漏了 %s？）" % (name, name))
    return a


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


def install(namespace):
    namespace.update({
        "PASS": PASS,
        "FAILS": FAILS,
        "SKIP": SKIP,
        "PREFIX": PREFIX,
        "chk": chk,
        "api": api,
        "clean": clean,
        "new_sphere": new_sphere,
        "new_plate": new_plate,
        "json": json,
    })


def run(functions, title):
    before = len(bpy.data.objects)
    print("== " + title + " ==")
    for fn in functions:
        try:
            fn()
        except Exception as e:
            chk(fn.__name__ + " 未抛异常", False, "%s: %s" % (type(e).__name__, str(e)[:200]))
        clean()
    chk("临时对象已清干净（场景对象数复原）", len(bpy.data.objects) == before,
        "%d -> %d" % (before, len(bpy.data.objects)))
    out = {"pass": len(PASS), "fail": len(FAILS), "fails": FAILS, "skipped": SKIP,
           "objects_left": [o.name for o in bpy.data.objects if o.name.startswith(PREFIX)]}
    print("HEADLESS " + json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    if FAILS:
        raise SystemExit(1)
