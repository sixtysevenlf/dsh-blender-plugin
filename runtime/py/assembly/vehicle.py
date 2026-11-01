# -*- coding: utf-8 -*-
"""车辆建模/放样/fit public facade。

公开契约不变：headless preload 仍按文件名加载本文件，平铺 runtime/vehicle.py shim、
K.dsh_vehicle_api 仍从本文件进入。实现已拆到 runtime/py/assembly/vehicle_impl/，避免单文件继续膨胀。
"""
import importlib
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_impl = importlib.import_module("runtime.py._facade").load_impl("assembly", "vehicle")
for _name in getattr(_impl, "__all__", ()):
    globals()[_name] = getattr(_impl, _name)
del _name
