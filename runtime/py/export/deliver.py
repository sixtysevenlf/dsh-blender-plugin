# -*- coding: utf-8 -*-
"""交付导出/校验 public facade。

公开契约不变：headless preload 仍按文件名加载本文件，平铺 runtime/deliver.py shim、
K.dsh_deliver_api 仍从本文件进入。实现已拆到 runtime/py/export/deliver_impl/，避免单文件继续膨胀。
"""
import importlib
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_impl = importlib.import_module("runtime.py._facade").load_impl("export", "deliver")
for _name in getattr(_impl, "__all__", ()):
    globals()[_name] = getattr(_impl, _name)
del _name
