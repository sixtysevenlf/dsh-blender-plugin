# -*- coding: utf-8 -*-
"""qc_impl/api.py 不得再 exec(compile(...)) 现场加载 qc_render.py。

旧兜底会在内核没有 K.dsh_qc_render_api 时，去 runtime 目录找一份 qc_render.py
执行 —— 领域包布局下它绕过了 preload 注入，可能和真正注入的模块版本不一致
（静默的错值比明确失败更糟）。现在的契约：只认内核注册的 API，缺失时
qc_render_views / qc_render_help 返回带 preload hint 的错误。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "runtime" / "py" / "render" / "qc_impl" / "api.py"


def source():
    return API.read_text(encoding="utf-8")


def function(name):
    for node in ast.walk(ast.parse(source())):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name + " missing from qc_impl/api.py")


def called_names(node):
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            names.add(sub.func.id)
    return names


def test_loader_does_not_exec_or_compile_a_second_copy():
    loader = function("_load_qc_render")
    assert "exec" not in called_names(loader)
    assert "compile" not in called_names(loader)


def test_loader_does_not_search_runtime_dir_for_qc_render():
    loader = function("_load_qc_render")
    segment = ast.get_source_segment(source(), loader) or ""
    assert "find_runtime_file" not in segment
    # 旧兜底会 open(...) 并读源码；新实现只从内核取已注册的 API。
    assert "open" not in called_names(loader)
    assert "runtime_dir" not in ast.dump(loader)


def test_missing_api_returns_error_with_preload_hint():
    text = source()
    view = ast.get_source_segment(text, function("qc_render_views")) or ""
    assert "qc_render 模块不可用" in view
    assert "preload='qc,qc_render'" in view
