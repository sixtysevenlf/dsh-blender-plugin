# -*- coding: utf-8 -*-
"""上游整合 A3 扫掠（原 upstream_integration_selftest.py 拆分）.

Split from cases/upstream_integration_selftest.py by T11a; T11b.8 runs it through
the native runner. The support module binds K and the original helper names to ctx.
"""
from __future__ import annotations

import json
import math

import bmesh
import bpy

import support as U



def t_sweep():
    r = api("sweep")("build", {"name": PREFIX + "pipe", "path": [[0, 0, 0], [1, 0, 0], [2, 0, 0]],
                               "profile": {"type": "circle", "radius": 0.1, "segments": 12}})
    chk("直管 3 站 × 12 边形 ⇒ 38 顶点 / 48 面", r.get("verts") == 38 and r.get("faces") == 48, r)
    tight = api("sweep")("analyze", {"path": [[0, 0, 0], [0.02, 0, 0], [0.02, 0.02, 0]],
                                    "profile": {"type": "rect", "width": 0.4, "height": 0.4}})
    chk("过紧路径被拦下并指出第几点",
        tight.get("ok") is False and tight.get("min_radius_at") == 1 and tight.get("hint"), tight)
    refuse = api("sweep")("build", {"name": PREFIX + "bad", "path": [[0, 0, 0], [0.02, 0, 0], [0.02, 0.02, 0]],
                                   "profile": {"type": "rect", "width": 0.4, "height": 0.4}})
    chk("sweep_build 默认先算后建（过紧直接拒）", refuse.get("ok") is False, refuse.get("error"))



def test_sweep(ctx):
    U.install(globals(), ctx)
    U.run([t_sweep], "上游整合 A3 扫掠（原 upstream_integration_selftest.py 拆分）")
