# -*- coding: utf-8 -*-
"""F2.0/F2.3: runtime/py/modules.json is the single Python module registry.

Consumed by runtime/paths.ts (injection), runtime/py/core/offline_bootstrap.py
(offline escape hatch) and the layout contract tests. No second hard-coded
table may survive; every entry must point at a real file under
runtime/py/<domain>/. F2.3 removed the flat runtime/*.py fallback.
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RT = os.path.join(ROOT, "runtime")
PY = os.path.join(RT, "py")

with open(os.path.join(PY, "modules.json"), encoding="utf-8") as _fh:
    MANIFEST = json.load(_fh)


def test_schema_and_domains():
    assert MANIFEST["version"] == 1
    assert MANIFEST["domains"] == ["core", "geometry", "assembly", "render", "export"]
    modules = MANIFEST["modules"]
    assert isinstance(modules, dict) and len(modules) == 41
    for name, dom in modules.items():
        assert name.isidentifier(), name
        assert dom in MANIFEST["domains"], (name, dom)
    assert "generator" in modules and "uv_tools" in modules
    assert modules["generator"] == modules["uv_tools"] == "geometry"


def test_registry_matches_domain_directories():
    for dom in MANIFEST["domains"]:
        expected = {n for n, d in MANIFEST["modules"].items() if d == dom}
        present = {f[:-3] for f in os.listdir(os.path.join(PY, dom))
                   if f.endswith(".py") and f != "__init__.py"}
        assert present == expected, dom
    flat = sorted(p for p in os.listdir(RT) if p.endswith(".py"))
    assert flat == [], "runtime/*.py flat modules must be gone: %r" % flat


def test_every_registered_module_file_exists():
    for name, dom in MANIFEST["modules"].items():
        p = os.path.join(PY, dom, name + ".py")
        assert os.path.isfile(p), p


def test_paths_ts_has_no_second_table():
    with open(os.path.join(RT, "paths.ts"), encoding="utf-8") as fh:
        src = fh.read()
    assert "'py', 'modules.json'" in src
    assert "PY_MODULE_DOMAIN: Record<string, string> = {" not in src
    assert "d === '.'" not in src


def test_offline_bootstrap_prefers_manifest():
    sys.path.insert(0, ROOT)
    from runtime.py.core import offline_bootstrap as ob
    modules = ob._load_manifest(RT)
    assert modules == MANIFEST["modules"]
    for name, dom in list(modules.items())[:8]:
        expected = os.path.join(PY, dom, name + ".py")
        assert ob._find_module(name, RT) == expected
    for name in ("generator", "uv_tools"):
        assert ob._find_module(name, RT) == os.path.join(PY, "geometry", name + ".py")
    assert ob._find_module("../evil", RT) is None
    assert ob._find_module("no_such_module_xyz", RT) is None
