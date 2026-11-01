# -*- coding: utf-8 -*-
"""Implementation package for the motion api stage modules; public names unchanged."""
from . import joints, selftest, index

__all__ = []
for _module in (joints, selftest, index,):
    for _name, _value in vars(_module).items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value
        if _name not in __all__:
            __all__.append(_name)
del _module, _name, _value
