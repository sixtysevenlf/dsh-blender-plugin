# -*- coding: utf-8 -*-
"""Regression tests for the offline bootstrap (T8 follow-up, updated for F2.3).

Defect 1 found by the archived E2E suite after T8 split the Python runtime into
domain packages: every module was exec'd into one shared globals dict, so
private helpers collided (assembly _num(v) was replaced by mesh_fix
_num(v, name, ...) and asm_export raised TypeError).

Defect 2 (the flat runtime directory hosting the real uv_tools.py / generator.py
was not on the module search path) was fixed by the F2.0 manifest and is now
moot: F2.3 moved both modules into geometry and deleted every flat file, so the
bootstrap resolves modules only through runtime/py/modules.json.

These tests are static / import-only; the real Blender run is covered by
tests/e2e/blender and the T12 E2E job.
"""
import importlib.util
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BOOTSTRAP = os.path.join(ROOT, "runtime", "py", "core", "offline_bootstrap.py")
PY = os.path.join(ROOT, "runtime", "py")


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


def test_flat_modules_resolve_through_domain_packages_after_f2_3():
    bootstrap = _load_bootstrap()
    assert bootstrap._find_module("uv_tools", bootstrap.RT) == os.path.join(PY, "geometry", "uv_tools.py")
    assert bootstrap._find_module("generator", bootstrap.RT) == os.path.join(PY, "geometry", "generator.py")


def test_domain_lookup_is_registry_only():
    bootstrap = _load_bootstrap()
    found = bootstrap._find_module("assembly", bootstrap.RT)
    assert found is not None
    assert found.replace(os.sep, "/").endswith("runtime/py/assembly/assembly.py")
    # Unknown names and unregistered flat paths are not guessed from directories.
    assert bootstrap._find_module("uv_tools_flat_xyz", bootstrap.RT) is None
    assert bootstrap._find_module("dry_run", bootstrap.RT) is None


def test_manifest_is_primary_registry():
    """F2.0：modules.json（唯一映射源）决定路径；F2.3 起无扁平 fallback。"""
    bootstrap = _load_bootstrap()
    modules = bootstrap._load_manifest(bootstrap.RT)
    assert modules is not None
    assert modules["assembly"] == "assembly"
    assert modules["generator"] == "geometry"
    root = bootstrap._runtime_root(bootstrap.RT)
    for name, dom in modules.items():
        expected = os.path.join(root, "py", dom, name + ".py")
        assert bootstrap._find_module(name, bootstrap.RT) == expected, name
