# -*- coding: utf-8 -*-
"""Shared helper for runtime.py public facades.

Every facade under runtime/py/<domain>/<name>.py does the same three things:
find the plugin root, put it on sys.path, and import
runtime.py.<domain>.<name>_impl. Keeping that in one place avoids 13 copies of
_dsh_root and, more importantly, avoids platform-specific path strings: the
marker is built with os.path.join, so POSIX and Windows both work.

Contract: the facade keeps the public module path and re-exports __all__; the
implementation package remains the single implementation.
"""
import importlib
import os
import sys


def _candidates():
    """Candidate plugin roots, most reliable first."""
    out = []
    kernel = sys.modules.get("dsh_rt_kernel")
    runtime_dir = getattr(kernel, "runtime_dir", None) if kernel is not None else None
    if runtime_dir:
        p = os.path.abspath(str(runtime_dir))
        for _ in range(5):
            out.append(p)
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    here = os.path.dirname(os.path.abspath(__file__))
    p = here
    for _ in range(6):
        out.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return out


def find_root(domain, name):
    """Return the plugin root containing runtime/py/<domain>/<name>_impl/."""
    if not domain or not name:
        return None
    for cand in _candidates():
        marker = os.path.join(cand, "runtime", "py", domain, name + "_impl", "__init__.py")
        if os.path.isfile(marker):
            return cand
    return None


def load_impl(domain, name):
    """Import runtime.py.<domain>.<name>_impl, or raise a readable facade error."""
    root = find_root(domain, name)
    if root is None:
        raise RuntimeError(
            "%s facade cannot find plugin root (runtime/py/%s/%s_impl/__init__.py)"
            % (name, domain, name)
        )
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("runtime.py.%s.%s_impl" % (domain, name))
