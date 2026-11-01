# -*- coding: utf-8 -*-
"""Native E2E: qc_render callable API contract (T5d.5 / selftest F8).

F8 used to fail because Api.__call__ treated a dict-only argument as a help
request instead of the module's default op.  This test is the Blender-side
regression: it exercises the real K.dsh_qc_render_api, but uses an invalid view
name so no image is rendered and the test stays fast.
"""
from __future__ import annotations

import bpy


def test_qc_render_api_contract(ctx):
    # qc_render_views validates the scene before it parses views; give it a
    # visible mesh so the F8 path is reached without starting a render.
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    api = ctx.api("qc_render")
    args = {
        "views": ["not_a_real_view_name"],
        "res": [32, 32],
        "outdir": str(ctx.test_dir),
        "lock_dir": str(ctx.test_dir),
    }

    direct = api("render_views", args)
    dict_only = api(args)
    raw = api["dispatch"]("render_catalog", "{}")
    help_doc = api("help")

    ctx.check("F8: api(op, args) returns dict", isinstance(direct, dict), type(direct).__name__)
    ctx.check("F8: api(dict) returns dict", isinstance(dict_only, dict), type(dict_only).__name__)
    ctx.check(
        "F8: api(dict) is equivalent to api(render_views, ...)",
        isinstance(dict_only, dict) and dict_only.get("unknown_views") == direct.get("unknown_views"),
        "direct=%r dict_only=%r" % (direct, dict_only),
    )
    ctx.check(
        "F8: api(dict) keeps unknown_views",
        isinstance(dict_only, dict) and dict_only.get("unknown_views") == ["not_a_real_view_name"],
        "direct=%r dict_only=%r" % (direct, dict_only),
    )
    ctx.check("F8: api[dispatch] returns the raw str", isinstance(raw, str), type(raw).__name__)
    ctx.check(
        "F8: api(help) returns a versioned dict",
        isinstance(help_doc, dict) and bool(help_doc.get("version")),
        help_doc.get("version") if isinstance(help_doc, dict) else type(help_doc).__name__,
    )
