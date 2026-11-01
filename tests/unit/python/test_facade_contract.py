# -*- coding: utf-8 -*-
"""F0.1: public facades share one portable helper; no per-facade path markers.

These checks are static and need no bpy / Blender. The marker historically used a
literal backslash ("runtime\\py\\...") in 10 of 13 facades, which is a valid path
separator on Windows but not on POSIX. The shared helper builds it with
os.path.join, so both platforms use the same contract.
"""
import ast
import posixpath
import sys
import types

import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PY = ROOT / "runtime" / "py"
FACADES = sorted(p for p in PY.glob("*/*.py") if "runtime.py._facade" in p.read_text(encoding="utf-8"))


def test_all_domain_facades_use_the_shared_helper():
    assert len(FACADES) == 13, [str(p.relative_to(ROOT)) for p in FACADES]
    for path in FACADES:
        source = path.read_text(encoding="utf-8")
        assert "_dsh_root" not in source, path
        assert "runtime.py._facade" in source, path
        assert len(source.splitlines()) <= 40, (path, len(source.splitlines()))
        tree = ast.parse(source)
        names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert "_dsh_impl" not in names, path


def test_helper_builds_marker_with_os_path_join():
    source = (PY / "_facade.py").read_text(encoding="utf-8")
    assert "os.path.join(cand, \"runtime\", \"py\", domain, name + \"_impl\", \"__init__.py\")" in source
    assert "runtime\\py" not in source


def test_helper_finds_the_plugin_root():
    sys.path.insert(0, str(ROOT))
    import runtime.py._facade as facade

    assert facade.find_root("core", "contract") == str(ROOT)
    assert facade.find_root("render", "qc_render") == str(ROOT)
    assert facade.find_root("core", "no_such_module_xyz") is None


# F4.1: the checks above are static/source-based. The two tests below execute the
# helper instead: POSIX path semantics are simulated with posixpath.join so a
# hard-coded "runtime\py" marker would fail on Windows too.

def _facade_module():
    sys.path.insert(0, str(ROOT))
    import runtime.py._facade as facade

    return facade


def test_find_root_builds_the_marker_with_os_path_join(monkeypatch, tmp_path):
    facade = _facade_module()
    candidate = str(tmp_path)
    expected = posixpath.join(candidate, "runtime", "py", "core", "contract_impl", "__init__.py")
    seen = []

    class FakePath:
        join = staticmethod(posixpath.join)

        @staticmethod
        def isfile(path):
            seen.append(path)
            return path == expected

    monkeypatch.setattr(facade, "os", types.SimpleNamespace(path=FakePath))
    monkeypatch.setattr(facade, "_candidates", lambda: [candidate])

    assert facade.find_root("core", "contract") == candidate
    assert seen == [expected]
    assert "/runtime/py/core/contract_impl/__init__.py" in expected


def test_load_impl_inserts_the_root_and_imports_the_public_module(monkeypatch, tmp_path):
    facade = _facade_module()
    imported = []
    monkeypatch.setattr(facade, "find_root", lambda domain, name: str(tmp_path))
    monkeypatch.setattr(
        facade.importlib,
        "import_module",
        lambda name: imported.append(name) or types.SimpleNamespace(),
    )
    monkeypatch.setattr(facade.sys, "path", [])

    facade.load_impl("core", "contract")

    assert imported == ["runtime.py.core.contract_impl"]
    assert facade.sys.path == [str(tmp_path)]


def test_load_impl_raises_a_readable_error_when_the_root_is_missing(monkeypatch):
    facade = _facade_module()
    monkeypatch.setattr(facade, "find_root", lambda domain, name: None)

    with pytest.raises(RuntimeError, match="contract facade cannot find plugin root"):
        facade.load_impl("core", "contract")
