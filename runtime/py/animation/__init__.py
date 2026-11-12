# -*- coding: utf-8 -*-
"""runtime.py.animation — animation domain: evaluated-pose sampling, root motion, joints.

Module list is in __all__. Importing this package never imports bpy: submodules
are loaded lazily by an explicit attribute access or import.
"""
__all__ = ["animation"]


def __getattr__(name):
    if name in __all__:
        import importlib
        mod = importlib.import_module(__name__ + "." + name)
        globals()[name] = mod
        return mod
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
