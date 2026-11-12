# -*- coding: utf-8 -*-
"""Shared fixtures for Python runtime unit tests (no real Blender)."""
import sys
import types

import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"


@pytest.fixture(scope="session")
def booted_runtime():
    """Install the bpy stub, a stub kernel module and the real kernel kit once."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests" / "helpers"))
    import bpy_stub
    bpy_stub.install()
    kernel = types.ModuleType("dsh_rt_kernel")
    sys.modules.setdefault("dsh_rt_kernel", kernel)
    kit_path = RUNTIME / "py" / "core" / "kit.py"
    exec(compile(kit_path.read_text(encoding="utf-8"), str(kit_path), "exec"), {})
    yield
