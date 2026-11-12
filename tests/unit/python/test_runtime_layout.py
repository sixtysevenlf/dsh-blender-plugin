# -*- coding: utf-8 -*-
"""Static layout contract: 5 domain packages, no flat runtime shims.

No bpy/numpy needed: everything here parses sources. The real import smoke runs
inside Blender. The 38 one-version re-export shims are gone; the only module
registry is runtime/py/modules.json, so every public name must resolve to a real
file under runtime/py/<domain>/.
"""
import ast
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RT = os.path.join(ROOT, "runtime")
PY = os.path.join(RT, "py")

MANIFEST_FILE = os.path.join(PY, "modules.json")
with open(MANIFEST_FILE, encoding="utf-8") as _fh:
    MANIFEST = json.load(_fh)
# 唯一映射源 runtime/py/modules.json（顺序 = 清单插入顺序，与各 __init__.__all__ 一致）。
DOMAINS = {d: [n for n, dom in MANIFEST["modules"].items() if dom == d] for d in MANIFEST["domains"]}
ALL_MODULES = [n for names in DOMAINS.values() for n in names]
DOMAIN_NAMES = tuple(MANIFEST["domains"])


def _parse(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _docstring_skipped(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def test_no_flat_runtime_modules():
    """runtime/*.py shims and unlisted flat modules are gone; registry-only."""
    flat = sorted(p for p in os.listdir(RT) if p.endswith(".py"))
    assert flat == [], "runtime/*.py must not exist after the domain split: %r" % flat


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
        assert kitmod.find_runtime_file("generator.py") == os.path.join(PY, "geometry", "generator.py")
        assert kitmod.find_runtime_file("uv_tools.py") == os.path.join(PY, "geometry", "uv_tools.py")
        assert kitmod.find_runtime_file("no_such_module_xyz.py") is None
    finally:
        if old is None:
            delattr(K, "runtime_dir")
        else:
            K.runtime_dir = old


def _module_node(rel):
    parts = list(rel)
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    return tuple(p[:-3] if p.endswith(".py") else p for p in parts)


def test_python_import_graph_has_no_cycles_or_cross_domain_edges():
    """Guard: domain packages import within their own domain, without cycles."""
    files = []
    for dirpath, dirnames, filenames in os.walk(PY):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if f.endswith(".py"):
                files.append(tuple(os.path.relpath(os.path.join(dirpath, f), PY).split(os.sep)))
    nodes = {_module_node(rel) for rel in files}
    graph = {n: set() for n in nodes}
    for rel in files:
        node = _module_node(rel)
        base = list(node) if rel[-1] == "__init__.py" else list(node[:-1])
        tree = _parse(os.path.join(PY, *rel))
        for n in ast.walk(tree):
            if not isinstance(n, ast.ImportFrom):
                continue
            target = None
            if n.level:
                up = n.level - 1
                if up > len(base):
                    continue
                pkg = base[:len(base) - up] if up else base
                target = tuple(pkg) + (tuple(n.module.split(".")) if n.module else ())
            elif n.module.startswith("runtime.py."):
                target = tuple(n.module.split(".")[2:])
            elif n.module.startswith("py."):
                target = tuple(n.module.split(".")[1:])
            if not target:
                continue
            dom = node[0] if node else ""
            if target[0] in DOMAIN_NAMES and dom in DOMAIN_NAMES and target[0] != dom:
                raise AssertionError("cross-domain import %r -> %r (%s)" % (
                    "/".join(node), "/".join(target), ast.unparse(n)))
            if target in nodes and target != node:
                graph[node].add(target)
    seen, stack, cycles = set(), [], []
    def dfs(n):
        seen.add(n)
        stack.append(n)
        for m in sorted(graph[n]):
            if m in stack:
                cycles.append(stack[stack.index(m):] + [m])
            elif m not in seen:
                dfs(m)
        stack.pop()
    for n in sorted(nodes):
        if n not in seen:
            dfs(n)
    assert cycles == [], ["/".join(c) for c in cycles]


def test_modules_manifest_is_single_source():
    """modules.json 唯一清单，paths.ts / offline_bootstrap.py 都读它。"""
    assert MANIFEST["version"] >= 1
    assert MANIFEST["domains"] == ["core", "geometry", "assembly", "render", "export", "animation"]
    assert len(MANIFEST["modules"]) == len(set(MANIFEST["modules"])) == 42
    for name, dom in MANIFEST["modules"].items():
        assert dom in MANIFEST["domains"], (name, dom)
        p = os.path.join(PY, dom, name + ".py")
        assert os.path.isfile(p), p
    paths_ts = open(os.path.join(RT, "paths.ts"), encoding="utf-8").read()
    assert "'py', 'modules.json'" in paths_ts
    assert "PY_MODULE_DOMAIN: Record<string, string> = {" not in paths_ts
    assert "d === '.'" not in paths_ts
    boot_py = open(os.path.join(PY, "core", "offline_bootstrap.py"), encoding="utf-8").read()
    assert "modules.json" in boot_py
