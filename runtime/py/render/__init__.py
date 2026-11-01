# -*- coding: utf-8 -*-
"""runtime.py.render — T8 领域包（Phase E）：视口 / QC / 渲染守卫 / 材质 / 同机位报告 / 图像工具 / 标定 / 拼图

模块列表见 __all__。**本包自身 import 安全**：不 eager import 子模块（子模块需要
bpy/numpy 与共享内核 K.dsh_kit，只在 Blender 注入路径或显式访问时才加载）。
访问子模块：import runtime.py.render.kit  或  runtime.py.render.kit（惰性属性）。
"""
__all__ = ['view', 'qc', 'qc_render', 'render_guard', 'material', 'visualreport', 'imgtools', 'calib', 'montage']


def __getattr__(name):
    # 惰性：from runtime.py.render import kit / runtime.py.render.kit 均可；
    # 不会在 import 包时拉起 bpy。
    if name in __all__:
        import importlib
        mod = importlib.import_module(__name__ + "." + name)
        globals()[name] = mod
        return mod
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
