# -*- coding: utf-8 -*-
"""QC 渲染 public facade（T5d）—— 实现已拆到 runtime/py/render/qc_render_impl/。

公开契约不变：headless preload="qc_render"、blender_rt_plan(op="qc_render_*")、
平铺 runtime/qc_render.py shim、K.dsh_qc_render_api 仍从本文件进入。原文件 3.4k 行，
按视角/锁/投影/设备/逐部件/主流程/自检/dispatch 拆开，避免单文件继续膨胀。
"""
import os
import sys


def _dsh_root():
    """定位插件根（runtime 的上一级），使 runtime.py.* 可 import。"""
    K = sys.modules.get("dsh_rt_kernel")
    runtime_dir = getattr(K, "runtime_dir", None) if K is not None else None
    candidates = []
    if runtime_dir:
        p = os.path.abspath(str(runtime_dir))
        for _ in range(4):
            candidates.append(p)
            p = os.path.dirname(p)
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.extend([here,
                           os.path.dirname(here),
                           os.path.dirname(os.path.dirname(here)),
                           os.path.dirname(os.path.dirname(os.path.dirname(here)))])
    except NameError:
        pass
    for cand in candidates:
        marker = os.path.join(cand, "runtime", "py", "render", "qc_render_impl", "__init__.py")
        if os.path.isfile(marker):
            return cand
    return None


def _dsh_impl():
    root = _dsh_root()
    if root is None:
        raise RuntimeError("qc_render facade 找不到插件根（runtime/py/render/qc_render_impl）")
    if root not in sys.path:
        sys.path.insert(0, root)
    import importlib
    return importlib.import_module("runtime.py.render.qc_render_impl")


_impl = _dsh_impl()
for _name in getattr(_impl, "__all__", ()):
    globals()[_name] = getattr(_impl, _name)
del _name
