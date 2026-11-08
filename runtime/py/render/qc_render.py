# -*- coding: utf-8 -*-
"""QC 渲染 public facade—— 实现已拆到 runtime/py/render/qc_render_impl/。

公开契约不变：headless preload="qc_render"、blender_op(op="qc_render_*")、
平铺 runtime/qc_render.py shim、K.dsh_qc_render_api 仍从本文件进入。原文件 3.4k 行，
按视角/锁/投影/设备/逐部件/主流程/自检/dispatch 拆开，避免单文件继续膨胀。
"""
import importlib
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_impl = importlib.import_module("runtime.py._facade").load_impl("render", "qc_render")
for _name in getattr(_impl, "__all__", ()):
    globals()[_name] = getattr(_impl, _name)
del _name
