# -*- coding: utf-8 -*-
"""runtime.py.export — T8 领域包（Phase E）：交付 / 打印检查 / Open3D 外部检查

模块列表见 __all__。**本包自身 import 安全**：不 eager import 子模块（子模块需要
bpy/numpy 与共享内核 K.dsh_kit，只在 Blender 注入路径或显式访问时才加载）。
访问子模块：import runtime.py.export.kit  或  runtime.py.export.kit（惰性属性）。
"""
__all__ = ['deliver', 'printcheck', 'open3d_check']


def __getattr__(name):
    # 惰性：from runtime.py.export import kit / runtime.py.export.kit 均可；
    # 不会在 import 包时拉起 bpy。
    if name in __all__:
        import importlib
        mod = importlib.import_module(__name__ + "." + name)
        globals()[name] = mod
        return mod
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
