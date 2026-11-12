"""Static contract for the honest surface_fit receipt.

This test never imports bpy: it parses runtime/py/geometry/surface.py plus its
surface_impl package and asserts the catalog-declared `fit` op is actually
registered with an explicit M3 refusal.
"""
import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
SURFACE = ROOT / "runtime" / "py" / "geometry" / "surface.py"
SURFACE_IMPL = ROOT / "runtime" / "py" / "geometry" / "surface_impl"
LOCK = ROOT / "tests" / "fixtures" / "capability.lock.json"


def _sources():
    files = [SURFACE]
    if SURFACE_IMPL.is_dir():
        files.extend(sorted(SURFACE_IMPL.glob("*.py")))
    return [(path, path.read_text(encoding="utf-8")) for path in files]


def _trees():
    return [(path, ast.parse(source)) for path, source in _sources()]


def _tree():
    return ast.parse(SURFACE.read_text(encoding="utf-8"))


def test_surface_fit_is_registered():
    found = False
    for _, tree in _trees():
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
    source = "\n".join(text for _, text in _sources())
    assert "def surface_fit(" in source
    assert "M3_NOT_IMPLEMENTED" in source
    assert '"fit": {' in source


def test_catalog_and_lock_agree_on_surface_ops():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    surface = next(f for f in lock["families"] if f["f"] == "surface")
    assert surface["ops"] == 11
    assert lock["ops_count"] == 239


def _surface_fit_function():
    """Compile the real surface_fit body without importing bpy/numpy."""
    for path, tree in _trees():
        fn = next(
            (node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "surface_fit"),
            None,
        )
        if fn is None:
            continue
        message = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_M3_FIT_MSG" for target in node.targets)
        )
        namespace = {
            "_err": lambda msg, **kw: {
                "ok": False,
                "error": str(msg),
                "hint": kw.pop("hint", None),
                **kw,
            }
        }
        exec(compile(ast.Module(body=[message, fn], type_ignores=[]), str(path), "exec"), namespace)
        return namespace["surface_fit"]
    raise AssertionError("surface_fit not found in surface.py or surface_impl")


def test_surface_fit_executes_the_honest_refusal():
    """The op must actually refuse with ok:false + reason + next_action."""
    receipt = _surface_fit_function()(points=[])
    assert receipt["ok"] is False
    assert receipt["stage"] == "M3"
    assert receipt["op"] == "fit"
    assert receipt["code"] == "M3_NOT_IMPLEMENTED"
    assert "fit not implemented" in receipt["error"]
    assert receipt["next_action"].strip()
    assert receipt["hint"].strip()
