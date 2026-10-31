# -*- coding: utf-8 -*-
"""GUI native E2E: OpenGL view capture and diagnostics (T11b.7).

native/view_diag.py covers projection/coverage/empty-frame without OpenGL.
bpy.ops.render.opengl needs a real window, so the capture path lives here and
runs in a non-background Blender.
"""

from __future__ import annotations

import json


def _cube():
    import bmesh
    import bpy
    me = bpy.data.meshes.new("DSH_VIEW_SELFTEST")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("DSH_VIEW_SELFTEST", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    return ob, me


def test_view_capture(ctx):
    import bpy
    api = ctx.api("view")
    pre = set(bpy.data.objects.keys())
    ob, me = _cube()
    try:
        ctx.check("VIEW_VERSION = 3", api.get("version") == 3, api.get("version"))
        diag = api["diagnostics"]
        thr = diag.get("empty_png_bytes")
        ctx.check("阈值已暴露给自检（empty_png_bytes）", isinstance(thr, int) and thr > 0, thr)

        p = str(ctx.test_dir / "capture_lean.png")
        spec = {"from": [2.5, 2.5, 2.5], "look_at": [0, 0, 0], "width": 160, "height": 90, "path": p}
        r_lean = ctx.parse(api["capture"](json.dumps(spec)))
        ctx.check("capture 成功（OpenGL 真出图）",
                  bool(r_lean.get("ok")) and int(r_lean.get("bytes") or 0) > 0, r_lean.get("error") or r_lean)
        ctx.check("PNG 文件真实落盘", (ctx.test_dir / "capture_lean.png").is_file(), p)
        want_diag = bool(int(r_lean.get("bytes") or 0) < thr)
        ctx.check("默认（未显式要求）：diagnostics 标志 == (字节数 < 阈值)",
                  bool(r_lean.get("diagnostics")) == want_diag,
                  {"bytes": r_lean.get("bytes"), "thr": thr, "flag": r_lean.get("diagnostics")})
        if not want_diag:
            ctx.check("默认路径：三项诊断全部为 None",
                      r_lean.get("coverage_estimate") is None and r_lean.get("scene_bbox") is None
                      and r_lean.get("objects_in_frame") is None and r_lean.get("warning") is None,
                      r_lean.get("coverage_estimate"))

        r_forced = ctx.parse(api["capture"](json.dumps(dict(spec, diagnostics=True))))
        ctx.check("显式 diagnostics=true：三项必须真算出来",
                  r_forced.get("diagnostics") is True
                  and isinstance(r_forced.get("coverage_estimate"), float)
                  and isinstance(r_forced.get("scene_bbox"), dict)
                  and r_forced.get("objects_in_frame") is not None,
                  {"diag": r_forced.get("diagnostics"), "cov": r_forced.get("coverage_estimate")})

        r_far = ctx.parse(api["capture"](json.dumps({"from": [500, 500, 500], "look_at": [900, 900, 900],
                                                     "width": 160, "height": 90, "path": p,
                                                     "diagnostics": True})))
        ctx.check("强制诊断 + 瞄空 ⇒ frame_looks_empty 告警照旧",
                  (r_far.get("warning") or {}).get("code") == "frame_looks_empty"
                  and bool((r_far.get("warning") or {}).get("suggest")), r_far.get("warning"))

        r = ctx.parse(api["gui_shading"]("WIREFRAME"))
        well_formed = ((r.get("ok") is True and r.get("shading") == "WIREFRAME")
                       or (r.get("ok") is False and bool(r.get("error"))))
        ctx.check("gui_shading 行为明确（要么生效要么报错，不许静默）", well_formed, r)
        if r.get("ok") is True:
            api["gui_shading"]("SOLID")
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass
    left = set(bpy.data.objects.keys()) - pre
    ctx.check("自检不留下对象", left == set(), sorted(left))