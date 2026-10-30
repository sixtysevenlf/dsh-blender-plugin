# -*- coding: utf-8 -*-
"""T8 static layout contract: 5 domain packages + re-export-only flat shims.

No bpy/numpy needed: everything here parses sources. The real import smoke runs
inside Blender (see docs/refactor/evidence/T8/test.log).
"""
import ast
import json
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RT = os.path.join(ROOT, "runtime")
PY = os.path.join(RT, "py")

DOMAINS = {
    "core": ["kit", "contract", "gate", "txn", "runner", "worker", "planner", "presets",
             "pipeline", "perf", "evalharness", "motion", "sweep", "audit", "offline_bootstrap"],
    "geometry": ["geo", "mesh_fix", "sculpt", "shapegen", "shapesolve", "faceeval", "blockout", "surface"],
    "assembly": ["assembly", "clearance", "vehicle", "human"],
    "render": ["view", "qc", "qc_render", "render_guard", "material", "visualreport",
               "imgtools", "calib", "montage"],
    "export": ["deliver", "printcheck", "open3d_check"],
}
ALL_MODULES = [n for names in DOMAINS.values() for n in names]
SHIMS = [n for n in ALL_MODULES if n != "open3d_check"]
UNLISTED = ["generator", "uv_tools"]


def _parse(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _docstring_skipped(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def test_domain_packages_and_real_files():
    assert os.path.isfile(os.path.join(PY, "__init__.py"))
    assert sorted(d for d in os.listdir(PY) if os.path.isdir(os.path.join(PY, d)) and d != "__pycache__") == sorted(DOMAINS)
    for dom, names in DOMAINS.items():
        d = os.path.join(PY, dom)
        assert os.path.isdir(d), d
        assert os.path.isfile(os.path.join(d, "__init__.py")), d
        present = sorted(f[:-3] for f in os.listdir(d) if f.endswith(".py") and f != "__init__.py")
        assert present == sorted(names), (dom, present)
    assert os.path.isfile(os.path.join(PY, "assembly", "assembly_features.json"))
    assert not os.path.exists(os.path.join(RT, "ext", "open3d_check.py"))


def test_flat_paths_are_shims_only():
    for name in SHIMS:
        p = os.path.join(RT, name + ".py")
        assert os.path.isfile(p), p
        tree = _parse(p)
        body = _docstring_skipped(tree.body)
        assert len(body) == 1 and isinstance(body[0], ast.Try), (name, [type(n).__name__ for n in body])
        tr = body[0]
        domain = next(d for d, ns in DOMAINS.items() if name in ns)
        assert len(tr.body) == 1 and isinstance(tr.body[0], ast.ImportFrom), name
        assert tr.body[0].module == "runtime.py.%s.%s" % (domain, name)
        assert len(tr.handlers) == 1
        imports = [n for n in ast.walk(tr) if isinstance(n, ast.ImportFrom)]
        assert len(imports) == 2, name
        assert imports[1].module == "py.%s.%s" % (domain, name)
        assert not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                       for n in ast.walk(tr)), name
        calls = [n for n in ast.walk(tr) if isinstance(n, ast.Call)]
        assert len(calls) == 1 and isinstance(calls[0].func, ast.Attribute) \
            and calls[0].func.attr == "startswith", name
        with open(p, encoding="utf-8") as fh:
            assert len(fh.read().splitlines()) < 40, name


def test_unlisted_modules_stay_flat_and_real():
    for name in UNLISTED:
        p = os.path.join(RT, name + ".py")
        assert os.path.isfile(p), p
        tree = _parse(p)
        assert any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in tree.body), name
        assert not os.path.exists(os.path.join(PY, "core", name + ".py"))
        with open(p, encoding="utf-8") as fh:
            assert len(fh.read().splitlines()) > 60, name


def test_package_init_is_lazy():
    for dom, names in DOMAINS.items():
        p = os.path.join(PY, dom, "__init__.py")
        tree = _parse(p)
        body = _docstring_skipped(tree.body)
        for node in body:
            assert not isinstance(node, (ast.Import, ast.ImportFrom, ast.Call)), (dom, type(node).__name__)
        alls = [n for n in body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets)]
        assert len(alls) == 1
        assert ast.literal_eval(alls[0].value) == names
        assert any(isinstance(n, ast.FunctionDef) and n.name == "__getattr__" for n in body), dom


def test_no_top_level_scene_side_effects():
    # 顶层只允许 import/def/class/文档字符串/纯注册；bpy/bmesh/numpy 一律只在函数内出现。
    for dom, names in DOMAINS.items():
        for name in names:
            p = os.path.join(PY, dom, name + ".py")
            tree = _parse(p)
            for node in _docstring_skipped(tree.body):
                if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                assert not (used & {"bpy", "bmesh", "numpy", "np"}), (dom, name, node.lineno)
                for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                    f = call.func
                    if isinstance(f, ast.Attribute):
                        assert f.attr not in {"render", "write", "save_as_mainfile", "open_mainfile"}, \
                            (dom, name, node.lineno)


def test_open3d_import_has_main_guard():
    p = os.path.join(PY, "export", "open3d_check.py")
    src = open(p, encoding="utf-8").read()
    assert 'if __name__ == "__main__":' in src
    tree = _parse(p)
    top_calls = [n for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Name) and n.value.func.id == "main"]
    assert not top_calls, "main() must not run at import time"


def test_find_runtime_file_reaches_sibling_packages():
    sys.path.insert(0, ROOT)
    import runtime.py.core.kit as kitmod
    K = kitmod._kernel()
    old = getattr(K, "runtime_dir", None)
    try:
        K.runtime_dir = RT
        got = kitmod.find_runtime_file("assembly_features.json")
        assert got == os.path.join(PY, "assembly", "assembly_features.json")
        assert kitmod.find_runtime_file("qc_render.py") == os.path.join(PY, "render", "qc_render.py")
        assert kitmod.find_runtime_file("generator.py") == os.path.join(RT, "generator.py")
        assert kitmod.find_runtime_file("no_such_module_xyz.py") is None
    finally:
        if old is None:
            delattr(K, "runtime_dir")
        else:
            K.runtime_dir = old


def test_repo_root_import_reaches_implementation():
    sys.path.insert(0, ROOT)
    import runtime.kit as shim
    import runtime.py.core.kit as real
    assert shim.KIT_VERSION == real.KIT_VERSION == 1
    assert shim.find_runtime_file is real.find_runtime_file
    import runtime.offline_bootstrap as ob
    import runtime.py.core.offline_bootstrap as ob_real
    assert ob.boot is ob_real.boot


def test_flat_import_fallback_only_runtime_on_path():
    # 旧式 import assembly（sys.path 里只有 runtime/）：shim 必须能走 py.* 兜底。
    env = dict(os.environ)
    env["PYTHONPATH"] = RT
    env["PYTHONIOENCODING"] = "utf-8"
    code = textwrap.dedent(
        """
        import kit, offline_bootstrap, py.core.kit, py.core.offline_bootstrap as real
        assert kit.find_runtime_file is py.core.kit.find_runtime_file
        assert offline_bootstrap.boot is real.boot
        print("FLAT_FALLBACK_OK")
        """
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=RT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLAT_FALLBACK_OK" in r.stdout
