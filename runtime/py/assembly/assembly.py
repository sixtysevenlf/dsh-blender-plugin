# -*- coding: utf-8 -*-
"""装配层 public facade—— 实现已拆到 runtime/py/assembly/assembly_impl/。

公开契约不变：headless preload="assembly"、blender_op(op="asm_*")、平铺
runtime/assembly.py shim、K.dsh_assembly_api 仍从本文件进入。实现包保持原函数顺序与
全局名，避免把 4k 行单文件继续堆在领域包根。
"""
import importlib
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_impl = importlib.import_module("runtime.py._facade").load_impl("assembly", "assembly")
for _name in getattr(_impl, "__all__", ()):
    globals()[_name] = getattr(_impl, _name)
del _name
