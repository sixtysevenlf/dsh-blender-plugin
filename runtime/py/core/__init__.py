# -*- coding: utf-8 -*-
"""runtime.py.core — T8 领域包（Phase E）：共享内核 / 契约 / 门 / 事务 / runner / worker / 规划 / 配方 / 管线 / 性能 / 运动 / 扫描 / 审计 / 离线引导

模块列表见 __all__。**本包自身 import 安全**：不 eager import 子模块（子模块需要
bpy/numpy 与共享内核 K.dsh_kit，只在 Blender 注入路径或显式访问时才加载）。
访问子模块：import runtime.py.core.kit  或  runtime.py.core.kit（惰性属性）。
"""
__all__ = ['kit', 'contract', 'gate', 'txn', 'runner', 'worker', 'planner', 'presets', 'pipeline', 'perf', 'evalharness', 'motion', 'sweep', 'audit', 'offline_bootstrap']


def __getattr__(name):
    # 惰性：from runtime.py.core import kit / runtime.py.core.kit 均可；
    # 不会在 import 包时拉起 bpy。
    if name in __all__:
        import importlib
        mod = importlib.import_module(__name__ + "." + name)
        globals()[name] = mod
        return mod
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
