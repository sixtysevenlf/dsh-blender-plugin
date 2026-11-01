# -*- coding: utf-8 -*-
"""Shared audit_file fixtures and soft assertions for audit_file native E2E."""

import os as _os
import sys as _sys
from pathlib import Path
from typing import Any

import bmesh as _bmesh
import bpy as _bpy


ST_CTX = "DSH_SELFTEST_CUBE"
ST_PASS, ST_FAILS, ST_SKIP = [], [], []
_CTX: Any = None


def st_check(name: str, cond: object, detail: object = "") -> None:
    ok = bool(cond)
    if _CTX is not None:
        _CTX.check(name, ok, detail)
    if ok:
        ST_PASS.append(name)
        print("  [OK]   " + name)
    else:
        ST_FAILS.append({"clause": name, "detail": str(detail)[:500]})
        print("  [FAIL] " + name + "  <- " + str(detail)[:300])


def st_note(name: str, detail: object) -> None:
    ST_SKIP.append({"clause": name, "detail": str(detail)[:300]})
    print("  [SKIP] " + name + "  " + str(detail)[:200])



def bind(ctx: Any) -> None:
    global _CTX
    _CTX = ctx


def st_get_api() -> tuple[Any, str | None]:
    """Return K.dsh_audit_api, or load runtime/py/core/audit.py as a fallback."""
    K = _sys.modules.get("dsh_rt_kernel")
    api = getattr(K, "dsh_audit_api", None) if K is not None else None
    if api is not None:
        return api, "K.dsh_audit_api"
    root = Path(__file__).resolve().parents[4]
    cands = [str(root / "runtime" / "py" / "core" / "audit.py"),
             "/home/sixtyseven67/DSH/dsh-blender-plugin/runtime/py/core/audit.py",
             "dsh-blender-plugin/runtime/py/core/audit.py"]
    for p in cands:
        for q in (p, _os.path.abspath(p)):
            if _os.path.exists(q):
                ns = {"__name__": "dsh_audit_selftest_inline"}
                exec(compile(open(q, encoding="utf-8").read(), q, "exec"), ns)
                return ns, q
    return None, None


def st_clean_scene():
    for ob in list(_bpy.data.objects):
        try:
            _bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for me in list(_bpy.data.meshes):
        try:
            if me.users == 0:
                _bpy.data.meshes.remove(me)
        except Exception:
            pass


def st_cube(name, loc, drop_face=False):
    """1×1×1 立方体（[-0.5,0.5]³ 再平移到 loc）；drop_face=True 删一个面 → 开放网格。"""
    me = _bpy.data.meshes.new(name + "_mesh")
    bm = _bmesh.new()
    _bmesh.ops.create_cube(bm, size=1.0)
    if drop_face:
        bm.faces.ensure_lookup_table()
        _bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES")
    bm.to_mesh(me)
    bm.free()
    ob = _bpy.data.objects.new(name, me)
    ob.location = tuple(loc)
    _bpy.context.scene.collection.objects.link(ob)
    return ob


def st_save(path):
    _bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, check_existing=False)


def st_counts():
    return {"objects": len(_bpy.data.objects), "collections": len(_bpy.data.collections),
            "libraries": len(_bpy.data.libraries), "meshes": len(_bpy.data.meshes),
            "materials": len(_bpy.data.materials), "images": len(_bpy.data.images)}


def st_boxes_equal(x, y, tol=1e-9):
    """逐项比较 bbox 字典（min/max/size，3 轴都要相等）。"""
    if not x or not y:
        return False
    for k in ("min", "max", "size"):
        a, b = x.get(k), y.get(k)
        if a is None or b is None or len(a) != len(b):
            return False
        for i in range(3):
            if abs(float(a[i]) - float(b[i])) > tol:
                return False
    return True

