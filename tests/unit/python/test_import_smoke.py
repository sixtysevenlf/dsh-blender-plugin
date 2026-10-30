# -*- coding: utf-8 -*-
"""Import smoke checks replacing the legacy modules_selftest.py (no Blender).

modules_selftest.py used to run inside Blender and call K.dsh_*_api for nine
modules. That is a Blender-integration concern (the APIs are only assembled by
the kernel inside Blender) and it is now covered by the Blender E2E layer and by
per-module unit tests. What remains valuable on every PR is cheaper and stricter:

  1. every Python file in the repo compiles (syntax gate, no bpy import);
  2. every top-level runtime/*.py compatibility shim is a pure re-export, so
     the T8 split cannot silently grow a second implementation;
  3. the modules that only need bpy + the kernel import cleanly against the
     maintained bpy stub, and expose their documented entry points;
  4. modules that genuinely need bmesh / numpy keep failing with that exact
     dependency name -- the boundary is pinned, not papered over with skips.

Real behaviour is exercised by tests/unit/python/test_*.py (logic) and
tests/e2e/blender (inside Blender). This file never claims Blender coverage.
"""
import ast
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

# Two top-level runtime modules still own their implementation: they are not
# domain modules of the T8 split (generator: standalone code-based modeling
# channel; uv_tools: bpy-native UV helper). Everything else under runtime/*.py
# must be a shim.
FLAT_MODULES = {"generator.py", "uv_tools.py"}

IMPORTABLE = {
    "runtime.py.core.contract": ["CONTRACT_VERSION", "c_check_interface", "c_evidence", "c_ledger"],
    "runtime.py.core.gate": ["GATE_VERSION", "gate_dispatch", "gate_help", "gate_run", "_eval_node"],
    "runtime.py.core.txn": ["TXN_VERSION", "t_dispatch", "t_help", "t_ops"],
    "runtime.py.render.view": ["VIEW_VERSION", "host_scene_items", "host_capture", "capture"],
    "runtime.py.export.deliver": ["DELIVER_VERSION", "d_dispatch", "d_ops", "deliver_export"],
    "runtime.generator": ["GENERATOR_VERSION", "generator_dispatch", "generator_help", "generator_run"],
    "runtime.uv_tools": ["UV_VERSION", "uv_dispatch", "uv_help", "uv_unwrap"],
    "runtime.py.core.runner": ["RUNNER_VERSION", "dsh_loop_start", "dsh_loop_status", "dsh_loop_help"],
    "runtime.py.core.planner": ["PLAN_VERSION", "p_dispatch", "p_help", "p_build"],
    "runtime.py.core.presets": ["PRESET_VERSION", "p_dispatch", "p_help", "p_apply"],
    "runtime.py.core.perf": ["PERF_VERSION", "dsh_perf_analyze", "dsh_perf_apply", "dsh_perf_help"],
    "runtime.py.core.offline_bootstrap": ["boot"],
}

BLENDER_ONLY = {
    "runtime.py.geometry.geo": "bmesh",
    "runtime.py.geometry.mesh_fix": "bmesh",
    "runtime.py.render.montage": "numpy",
    "runtime.py.render.qc": "numpy",
}


@pytest.fixture(scope="module")
def booted_runtime():
    """Install the bpy stub, a stub kernel and the real kernel kit once."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests" / "helpers"))
    import bpy_stub
    bpy_stub.install()
    kernel = types.ModuleType("dsh_rt_kernel")
    sys.modules.setdefault("dsh_rt_kernel", kernel)
    kit_path = RUNTIME / "py" / "core" / "kit.py"
    exec(compile(kit_path.read_text(encoding="utf-8"), str(kit_path), "exec"), {})
    # Keep the bpy stub installed: other add-on test modules import it at module
    # import time and rely on sys.modules["bpy"] staying mapped for the session.
    yield


def python_files():
    for base in (RUNTIME, ROOT / "addon", ROOT / "tests"):
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def test_every_python_file_compiles():
    files = list(python_files())
    assert len(files) > 100, f"only {len(files)} python files found under runtime/addon/tests"
    for path in files:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")


def test_runtime_top_level_shims_only_reexport():
    shims = []
    for path in sorted(RUNTIME.glob("*.py")):
        if path.name in FLAT_MODULES:
            continue
        shims.append(path)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        assert not definitions, f"{path.name} is a compatibility shim but defines {definitions[0].name}"
        assert "from runtime.py." in source or "from py." in source, f"{path.name} does not import the moved implementation"
        assert len(source.splitlines()) <= 40, f"{path.name} shim grew to {len(source.splitlines())} lines"
    assert len(shims) >= 30, f"expected the T8 shim set, found {len(shims)}"


def test_importable_modules_expose_documented_entry_points(booted_runtime):
    for name, expected in IMPORTABLE.items():
        module = importlib.import_module(name)
        missing = [attr for attr in expected if not hasattr(module, attr)]
        assert not missing, f"{name} lost {missing}"


def test_blender_only_dependencies_fail_by_name(booted_runtime):
    # Honest boundary: these import bmesh/numpy and cannot run outside Blender.
    # The assertion pins the dependency name instead of skipping silently.
    for name, dependency in BLENDER_ONLY.items():
        with pytest.raises(ModuleNotFoundError) as excinfo:
            importlib.import_module(name)
        assert excinfo.value.name == dependency, f"{name} failed for {excinfo.value.name!r}, expected {dependency!r}"


def test_compatibility_shim_imports_real_implementation(booted_runtime):
    shim = importlib.import_module("runtime.gate")
    assert not hasattr(shim, "_eval_pass_if")  # star re-export: public surface only
    assert shim.gate_dispatch.__module__ == "runtime.py.core.gate"
    assert shim.gate_run.__module__ == "runtime.py.core.gate"
