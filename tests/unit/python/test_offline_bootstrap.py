# -*- coding: utf-8 -*-
"""Regression tests for the offline bootstrap (T8 follow-up).

Two defects were found by the archived E2E suite after T8 split the Python
runtime into domain packages:

1. every module was exec'd into one shared globals dict, so private helpers
   collided (assembly _num(v) was replaced by mesh_fix _num(v, name, ...) and
   asm_export raised TypeError);
2. the flat runtime directory (which still hosts the real uv_tools.py and
   generator.py) was not on the module search path.

These tests are static / import-only; the real Blender run is covered by
tests/e2e/blender and the T12 E2E job.
"""
import importlib.util
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BOOTSTRAP = os.path.join(ROOT, "runtime", "py", "core", "offline_bootstrap.py")


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("offline_bootstrap_under_test", BOOTSTRAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_boot_uses_one_namespace_per_module():
    with open(BOOTSTRAP, encoding="utf-8") as handle:
        source = handle.read()
    assert "mod_ns = {" in source
    assert 'if not k.startswith("_")' in source
    # The old bug: modules were exec'd straight into the shared namespace.
    assert "_exec(p, ns, name)" not in source


def test_module_search_includes_flat_runtime_for_uv_tools():
    bootstrap = _load_bootstrap()
    assert bootstrap._find_module("uv_tools", bootstrap.RT) is not None
    assert bootstrap._find_module("generator", bootstrap.RT) is not None


def test_domain_lookup_still_wins_over_flat_shim():
    bootstrap = _load_bootstrap()
    found = bootstrap._find_module("assembly", bootstrap.RT)
    assert found is not None
    assert found.replace(os.sep, "/").endswith("runtime/py/assembly/assembly.py")
