# -*- coding: utf-8 -*-
"""Implementation package for assembly.py; public facade keeps the original module path."""
from . import common, manifest, scan, gate, merge, repair, export, selftest, dispatch

__all__ = []
for _module in (common, manifest, scan, gate, merge, repair, export, selftest, dispatch,):
    for _name, _value in vars(_module).items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value
        if _name not in __all__:
            __all__.append(_name)
del _module, _name, _value
