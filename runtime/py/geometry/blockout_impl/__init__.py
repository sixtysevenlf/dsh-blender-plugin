# -*- coding: utf-8 -*-
"""Implementation package for blockout.py; public facade keeps the original module path."""
from . import common, templates, geometry, ops, present, api

__all__ = []
for _module in (common, templates, geometry, ops, present, api,):
    for _name, _value in vars(_module).items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value
        if _name not in __all__:
            __all__.append(_name)
del _module, _name, _value
