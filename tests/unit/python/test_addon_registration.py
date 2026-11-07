"""Unit checks for the registration split.

register()/unregister() moved out of __init__.py into registration.py; these
tests keep the entry point thin and prove a full round-trip leaves no residue
in the bpy stub (the real-Blender round-trip lives in the E2E layer).
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import bpy_stub

bpy = bpy_stub.install()

pkg = importlib.import_module("addon.dsh_blender_bridge")
registration = importlib.import_module("addon.dsh_blender_bridge.registration")


def test_init_is_a_thin_entry_point():
    source = Path(pkg.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert functions == []
    assert "from .registration import register, unregister" in source
    assert '"version": (2, 0, 0)' in source
    assert 'if __name__ == "__main__":' in source
    assert pkg.register is registration.register
    assert pkg.unregister is registration.unregister


def test_package_defines_register_and_unregister_exactly_once():
    root = Path(pkg.__file__).resolve().parent
    definitions = {}
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in ("register", "unregister"):
                definitions.setdefault(node.name, []).append(
                    path.relative_to(root).as_posix())
    assert definitions == {
        "register": ["registration.py"],
        "unregister": ["registration.py"],
    }


def test_registration_is_split_into_helpers():
    expected = (
        "_register_ui_lists",
        "_register_connection_properties",
        "_register_selection_properties",
        "_register_polyhaven_hyper3d_properties",
        "_register_hunyuan_properties",
        "_register_sketchfab_polypizza_properties",
        "_register_integration_properties",
        "_register_classes",
    )
    for name in expected:
        assert callable(getattr(registration, name, None)), name
    register_source = Path(registration.__file__).read_text(encoding="utf-8")
    for name in expected:
        assert name + "()" in register_source


def test_register_unregister_round_trip_leaves_no_residue():
    bpy.utils.registered.clear()
    bpy.app.timers._registered.clear()
    for handler_list in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post,
                         bpy.app.handlers.redo_post, bpy.app.handlers.depsgraph_update_post):
        handler_list[:] = []
    try:
        registration.register()
        assert bpy.utils.registered, "register() did not register any classes"
        assert bpy.types.Scene.dshbb_ai_paused is not None
        assert bpy.app.timers._registered

        registration.unregister()
        assert bpy.utils.registered == []
        assert bpy.app.timers._registered == {}
        for attribute in ("dshbb_server_running", "dshbb_auto_start_server",
                          "dshbb_ai_paused", "dshbb_selection_mode",
                          "dshbb_use_polyhaven", "dshbb_use_hunyuan3d"):
            assert not hasattr(bpy.types.Scene, attribute), attribute
    finally:
        try:
            registration.unregister()
        except Exception:
            pass
        bpy.utils.registered.clear()
        bpy.app.timers._registered.clear()
        for handler_list in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post,
                             bpy.app.handlers.redo_post,
                             bpy.app.handlers.depsgraph_update_post):
            handler_list[:] = []
