# -*- coding: utf-8 -*-
"""Regression guard for the qc_render selftest G1 NameError.

The selftest calls render_lock() in phase G1 but the split module imported only
_lock_path, so the first G1 run raised NameError. Keep the lock primitive in the
module namespace without importing bpy into this unit test.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SELFTEST = ROOT / "runtime" / "py" / "render" / "qc_render_impl" / "selftest.py"


def imported_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def test_selftest_imports_render_lock():
    tree = ast.parse(SELFTEST.read_text(encoding="utf-8"))
    assert "render_lock" in imported_names(tree)


def test_g1_uses_render_lock_and_lock_path():
    tree = ast.parse(SELFTEST.read_text(encoding="utf-8"))
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert {"render_lock", "_lock_path"} <= used
