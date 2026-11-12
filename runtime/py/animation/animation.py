# -*- coding: utf-8 -*-
"""动画评估 public facade（animation_report/selftest/help）。

公开契约：headless preload 按文件名 animation 加载本文件，K.dsh_animation_api
从本文件进入；实现在 runtime/py/animation/animation_impl/。
"""
import importlib
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_impl = importlib.import_module("runtime.py._facade").load_impl("animation", "animation")
for _name in getattr(_impl, "__all__", ()):
    globals()[_name] = getattr(_impl, _name)
del _name
