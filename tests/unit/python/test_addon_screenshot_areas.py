# -*- coding: utf-8 -*-
"""Area selection for full=true captures (no Blender needed)."""
import importlib.util
import sys
from pathlib import Path

import bpy_stub

ROOT = Path(__file__).resolve().parents[3]
SCREENSHOT = ROOT / "extension" / "dsh_blender_bridge" / "commands" / "core" / "screenshot.py"


def load():
    bpy_stub.install()
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("dsh_screenshot_under_test", SCREENSHOT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_area_picks_the_3d_viewport_instead_of_properties():
    mod = load()
    assert mod._area_index_for(["PROPERTIES", "VIEW_3D", "OUTLINER"], None) == 1


def test_missing_area_falls_back_to_the_first_editor_without_a_3d_viewport():
    mod = load()
    assert mod._area_index_for(["PROPERTIES", "OUTLINER"], None) == 0


def test_explicit_area_is_range_checked():
    mod = load()
    assert mod._area_index_for(["PROPERTIES", "VIEW_3D"], 0) == 0
    assert mod._area_index_for(["PROPERTIES", "VIEW_3D"], 2) == -1
    assert mod._area_index_for(["PROPERTIES", "VIEW_3D"], -1) == -1
    assert mod._area_index_for([], 0) == -1
