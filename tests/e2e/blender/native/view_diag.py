# -*- coding: utf-8 -*-
"""Native E2E: view diagnostics core without capture (T11b.2).

Ported from the archived view_diag_selftest.py up to the OpenGL capture boundary.
The archived wrapper was GUI-only because bpy.ops.render.opengl needs an OpenGL
context; this headless module keeps the projection/coverage/empty-frame
diagnostics, while capture itself moves to gui/native/view_capture.py (T11b.7).
"""

from __future__ import annotations

import json

import bmesh
import bpy


def test_view_diagnostics_core(ctx):
    api = ctx.api("view")
    pre = set(bpy.data.objects.keys())

    me = bpy.data.meshes.new("DSH_VIEW_SELFTEST")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("DSH_VIEW_SELFTEST", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()

    try:
        ctx.check("VIEW_VERSION = 3（v0.9.4：诊断惰性化）", api.get("version") == 3, api.get("version"))
        for k in ("gui_frame", "gui_shading", "gui_open", "gui_help"):
            ctx.check("GUI 原语已注册：%s" % k, callable(api.get(k)))

        diag = api["diagnostics"]
        _scene_bbox = diag["scene_bbox"]
        _coverage = diag["coverage"]
        _objects_in_frame = diag["objects_in_frame"]
        _empty_frame_note = diag["empty_frame_note"]
        bb = _scene_bbox()
        ctx.check("_scene_bbox 能算（center/span；场景可能还有默认方块，只断言 span ≥ 2）",
                  bb and abs(bb["span"] - 2.0) < 1e-6 and bb["objects"] >= 1
                  and bb["min"] == [-1.0, -1.0, -1.0], bb)

        spec_ok = {"from": [2.5, 2.5, 2.5], "look_at": [0, 0, 0], "width": 160, "height": 90,
                   "lens": 50.0, "sensor": 36.0, "clip_start": 0.1, "clip_end": 1000.0,
                   "sensor_fit": "AUTO", "ortho": False, "ortho_scale": 10.0, "shift_x": 0.0, "shift_y": 0.0}
        names_ok, margin_ok = _objects_in_frame(spec_ok)
        ctx.check("投影入框：正对着时能数到被测对象",
                  "DSH_VIEW_SELFTEST" in names_ok and margin_ok is not None,
                  {"names": names_ok, "margin_px": margin_ok})
        names_far, margin_far = _objects_in_frame(dict(spec_ok, **{"from": [8, 8, 8]}))
        ctx.check("投影入框：退远后完全在画内（margin_px > 0）",
                  margin_far is not None and margin_far > 0, {"names": names_far, "margin_px": margin_far})

        spec_bad = dict(spec_ok, **{"from": [10, 10, 10], "look_at": [20, 20, 20]})
        names_bad, _ = _objects_in_frame(spec_bad)
        ctx.check("投影入框：背离场景时数不到被测对象（这就是「瞄空」的判据）",
                  "DSH_VIEW_SELFTEST" not in names_bad, names_bad)
        warn = _empty_frame_note(0.0, bb, spec_bad, names_bad)
        ctx.check("空帧告警：code=frame_looks_empty + 带 scene_bbox + 带 suggest",
                  warn and warn.get("code") == "frame_looks_empty" and warn.get("scene_bbox")
                  and (warn.get("suggest") or {}).get("look_at") == [0.0, 0.0, 0.0], warn)
        ctx.check("空帧告警：非空画面时不误报", _empty_frame_note(0.5, bb, spec_ok, names_ok) is None)

        W, H = 64, 36
        flat = bytearray([40, 40, 40, 255] * (W * H))
        ctx.check("_coverage：整幅同色 ≈ 0", _coverage(bytes(flat), W, H, None) < 1e-6)
        patched = bytearray(flat)
        for y in range(0, 8):
            for x in range(0, 8):
                i = (y * W + x) * 4
                patched[i:i + 3] = b"\xff\xff\xff"
        cov = _coverage(bytes(patched), W, H, None)
        ctx.check("_coverage：局部白块被算进去（≈64/2304）", 0.01 < cov < 0.06, cov)

        sug = (warn or {}).get("suggest") or {}
        if sug:
            spec_sug = dict(spec_ok, **{"from": sug["from"], "look_at": sug["look_at"]})
            names_sug, _ = _objects_in_frame(spec_sug)
            ctx.check("suggest 的 from/look_at 真能看到对象（照它再出一次即可）",
                      "DSH_VIEW_SELFTEST" in names_sug, names_sug)

        thr = diag.get("empty_png_bytes")
        ctx.check("阈值已暴露给自检（empty_png_bytes）", isinstance(thr, int) and thr > 0, thr)

        h = ctx.parse(api["help"]())
        ctx.check("help 里写明诊断的触发条件（默认不跑/近空补跑/可强制）",
                  "diagnostics_v3" in h
                  and "默认不跑" in json.dumps(h.get("diagnostics_v3") or {}, ensure_ascii=False),
                  list(h.keys())[:8])
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass

    left = set(bpy.data.objects.keys()) - pre
    ctx.check("自检不留下对象", left == set(), sorted(left))
