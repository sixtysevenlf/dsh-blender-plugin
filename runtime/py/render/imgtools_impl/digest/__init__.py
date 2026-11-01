# -*- coding: utf-8 -*-
"""Implementation package for the img_digest stage modules; public names unchanged."""
from . import visual, prepare, shape, features, numbers, verdict, result, index

__all__ = []
for _module in (visual, prepare, shape, features, numbers, verdict, result, index,):
    for _name, _value in vars(_module).items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value
        if _name not in __all__:
            __all__.append(_name)
del _module, _name, _value
