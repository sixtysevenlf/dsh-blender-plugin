"""Static contract for the honest surface_fit receipt (T2).

This test never imports bpy: it parses runtime/surface.py and asserts the
catalog-declared `fit` op is actually registered with an explicit M3 refusal.
"""
import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
SURFACE = ROOT / "runtime" / "surface.py"
LOCK = ROOT / "tests" / "fixtures" / "capability.lock.json"


def _tree():
    return ast.parse(SURFACE.read_text(encoding="utf-8"))


def test_surface_fit_is_registered():
    tree = _tree()
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "register"):
            continue
        if len(node.args) < 3 or not isinstance(node.args[2], ast.Dict):
            continue
        keys = [k.value for k in node.args[2].keys if isinstance(k, ast.Constant)]
        if "fit" in keys:
            found = True
    assert found, "surface.py must register the catalog-declared fit op"


def test_surface_fit_is_honest():
    source = SURFACE.read_text(encoding="utf-8")
    assert "def surface_fit(" in source
    assert "M3_NOT_IMPLEMENTED" in source
    assert '"fit": {' in source


def test_catalog_and_lock_agree_on_surface_ops():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    surface = next(f for f in lock["families"] if f["f"] == "surface")
    assert surface["ops"] == 11
    assert lock["ops_count"] == 228
