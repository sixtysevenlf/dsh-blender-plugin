# -*- coding: utf-8 -*-
"""网格体检 public facade—— 实现已拆到 runtime/py/core/audit_impl/。

公开契约不变：headless preload="audit"、blender_op(op="audit_*")、K.dsh_audit_api 仍从本文件进入。原文件 3.2k 行，
按网格/连通性/漂移/测量/重叠与干涉/API 拆开，避免单文件继续膨胀。
"""
import importlib
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_impl = importlib.import_module("runtime.py._facade").load_impl("core", "audit")
for _name in getattr(_impl, "__all__", ()):
    globals()[_name] = getattr(_impl, _name)
del _name
