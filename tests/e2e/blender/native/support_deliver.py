# -*- coding: utf-8 -*-
"""Shared scene builders and deliver API helpers for deliver native E2E."""

import bpy
import os
import sys


def _api(attr, modname):
    """优先 K.<attr>（preload 注入），其次从 K.runtime_dir 现场加载同目录 <modname>.py（与 qc.py 同套写法）。"""
    K = sys.modules.get("dsh_rt_kernel")
    api = getattr(K, attr, None) if K is not None else None
    if api:
        return api, K
    d = getattr(K, "runtime_dir", None) if K is not None else None
    if d and os.path.isfile(os.path.join(str(d), modname + ".py")):
        p = os.path.join(str(d), modname + ".py")
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        exec(compile(src, p, "exec"), {"__name__": "dsh_" + modname, "__file__": p})
        return getattr(K, attr, None), K
    return None, K


COLL = "DSH_DV_COLL"
A, B, C, H = "DSH_DV_BOX_A", "DSH_DV_BOX_B", "DSH_DV_ARR", "DSH_DV_HIDDEN"
MAT = "DSH_DV_MAT_RED"
MTL_TXT = "-" * 60


def _mk_box(name, size=(1.0, 1.0, 1.0), loc=(0.0, 0.0, 0.0), coll=None):
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    vs = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
          (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
    fs = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata(vs, [], fs)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def _mk_red_mat():
    mat = bpy.data.materials.new(MAT)
    try:
        if getattr(mat, "node_tree", None) is None:
            mat.use_nodes = True
    except Exception:
        pass
    pn = None
    try:
        for n in mat.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                pn = n
                break
    except Exception:
        pn = None
    if pn is not None:
        pn.inputs["Base Color"].default_value = (0.9, 0.2, 0.1, 1.0)
    else:
        mat.diffuse_color = (0.9, 0.2, 0.1, 1.0)
    return mat, (pn is not None)


def build_scene():
    """合成场景（全部落在 A 的 [-4,4]³ 包围盒内 → 归一化期望值可手算：最长边 8、缩放 = 2×target/8）。

    A 8³ @原点（红材质，12 面）· B 0.5³ @(3.5,3.5,3.5)（无材质 → 灰阶，12 面）
    C 0.2³ @(0,3.5,0) + Array×3（求值后 36 面）· H 1³ @(0,0,3.5) 且 hide_render（默认排除，12 面）
    """
    coll = bpy.data.collections.new(COLL)
    bpy.context.scene.collection.children.link(coll)
    a = _mk_box(A, (8.0, 8.0, 8.0), (0.0, 0.0, 0.0), coll)
    b = _mk_box(B, (0.5, 0.5, 0.5), (3.5, 3.5, 3.5), coll)
    c = _mk_box(C, (0.2, 0.2, 0.2), (0.0, 3.5, 0.0), coll)
    c.modifiers.new("DSH_DV_ARR", "ARRAY").count = 3
    h = _mk_box(H, (1.0, 1.0, 1.0), (0.0, 0.0, 3.5), coll)
    h.hide_render = True
    mat, node_ok = _mk_red_mat()
    a.data.materials.append(mat)
    bpy.context.view_layer.update()
    return coll, a, b, c, h, node_ok


def cleanup(coll, objs):
    for ob in objs:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    try:
        bpy.data.collections.remove(coll)
    except Exception:
        pass
    for m in list(bpy.data.meshes):
        if m.name.startswith("DSH_DV_"):
            try:
                bpy.data.meshes.remove(m)
            except Exception:
                pass
    for m in list(bpy.data.materials):
        if m.name.startswith("DSH_DV_"):
            try:
                bpy.data.materials.remove(m)
            except Exception:
                pass
    bpy.context.view_layer.update()


def scan_obj(path):
    n_f = n_g = n_u = n_v = n_vn = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("f "):
                n_f += 1
            elif line.startswith("g "):
                n_g += 1
            elif line.startswith("usemtl "):
                n_u += 1
            elif line.startswith("v "):
                n_v += 1
            elif line.startswith("vn "):
                n_vn += 1
    return {"f": n_f, "g": n_g, "usemtl": n_u, "v": n_v, "vn": n_vn}
