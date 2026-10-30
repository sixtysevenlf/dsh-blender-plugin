"""Packaging checks: zip manifest, package-root import and register/unregister."""

from __future__ import annotations

import importlib
import sys
import zipfile
from pathlib import Path

import bpy_stub

bpy = bpy_stub.install()

REPO_ROOT = Path(__file__).resolve().parents[3]
ADDON_DIR = REPO_ROOT / "addon" / "dsh_blender_bridge"
ZIP_PATH = REPO_ROOT / "addon" / "dsh_blender_bridge.zip"
PACKAGE_ROOT = "dsh_blender_bridge"


def _package_python_files():
    return sorted(
        path.relative_to(ADDON_DIR).as_posix()
        for path in ADDON_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_zip_manifest_has_package_root_and_all_python_files():
    assert ZIP_PATH.is_file(), "run scripts/build-addon.mjs first"
    with zipfile.ZipFile(ZIP_PATH) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert PACKAGE_ROOT + "/__init__.py" in names
        assert all(name.startswith(PACKAGE_ROOT + "/") for name in names)
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        assert not any(name.lower() == PACKAGE_ROOT + ".py" for name in names)
        assert names == sorted(names)
        expected = [PACKAGE_ROOT + "/" + relative for relative in _package_python_files()]
        assert names == expected
        assert archive.read(PACKAGE_ROOT + "/__init__.py").decode("utf-8").find('"version": (1, 11)') != -1
        assert archive.read(PACKAGE_ROOT + "/protocol.py").decode("utf-8").find("ADDON_PROTOCOL_VERSION = 11") != -1


def test_zip_installs_as_package_and_registers_from_any_cwd(tmp_path, monkeypatch):
    with zipfile.ZipFile(ZIP_PATH) as archive:
        archive.extractall(tmp_path)
    package_init = tmp_path / PACKAGE_ROOT / "__init__.py"
    assert package_init.is_file()

    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in list(sys.modules):
        if name == PACKAGE_ROOT or name.startswith(PACKAGE_ROOT + "."):
            del sys.modules[name]

    package = importlib.import_module(PACKAGE_ROOT)
    try:
        assert Path(package.__file__).resolve() == package_init.resolve()
        assert package.bl_info["version"] == (1, 11)
        assert package.ADDON_PROTOCOL_VERSION == 11

        package.register()
        assert bpy.utils.registered, "register() did not register any classes"
        assert bpy.types.Scene.dshbb_port is not None
        assert bpy.types.Scene.dshbb_ai_paused is not None
        assert bpy.types.Scene.dshbb_use_polyhaven is not None
        assert bpy.types.Scene.dshbb_use_hunyuan3d is not None
        assert any(
            getattr(cls, "bl_idname", "") == PACKAGE_ROOT
            for cls in bpy.utils.registered
        ), "add-on preferences bl_idname must be the package module name"

        package.unregister()
        assert bpy.utils.registered == []
        assert not hasattr(bpy.types.Scene, "dshbb_port")
        assert not hasattr(bpy.types.Scene, "dshbb_ai_paused")
        assert not hasattr(bpy.types.Scene, "dshbb_use_polyhaven")
        assert not hasattr(bpy.types.Scene, "dshbb_use_hunyuan3d")
        assert not bpy.app.timers._registered
    finally:
        try:
            package.unregister()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PACKAGE_ROOT or name.startswith(PACKAGE_ROOT + "."):
                del sys.modules[name]


def test_zip_contains_the_39_module_package_without_the_old_single_file():
    with zipfile.ZipFile(ZIP_PATH) as archive:
        names = archive.namelist()
    expected = [PACKAGE_ROOT + "/" + relative for relative in _package_python_files()]
    assert len(expected) == 39
    assert names == expected
    assert PACKAGE_ROOT + ".py" not in names
    assert not any(name.endswith("blender_manifest.toml") for name in names)
    assert not any(name.endswith(".pyc") for name in names)


def test_register_unregister_leaves_no_residue_and_can_run_twice(tmp_path, monkeypatch):
    with zipfile.ZipFile(ZIP_PATH) as archive:
        archive.extractall(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in list(sys.modules):
        if name == PACKAGE_ROOT or name.startswith(PACKAGE_ROOT + "."):
            del sys.modules[name]
    for handler_list in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post,
                         bpy.app.handlers.redo_post, bpy.app.handlers.depsgraph_update_post):
        handler_list[:] = []
    bpy.app.timers._registered.clear()
    bpy.utils.registered.clear()

    package = importlib.import_module(PACKAGE_ROOT)
    try:
        for _round_index in (1, 2):
            package.register()
            assert bpy.utils.registered, "register() did not register any classes"
            assert bpy.types.Scene.dshbb_port is not None
            assert bpy.types.Scene.dshbb_ai_paused is not None
            config = importlib.import_module(PACKAGE_ROOT + ".config")
            assert config._dshbb_load_post in bpy.app.handlers.load_post
            assert bpy.app.timers.is_registered(config._dshbb_ensure_server_running)

            package.unregister()
            assert bpy.utils.registered == []
            assert bpy.app.timers._registered == {}
            assert bpy.app.handlers.load_post == []
            for attribute in ("dshbb_port", "dshbb_server_running", "dshbb_auto_start_server",
                              "dshbb_backend_url", "dshbb_ai_paused", "dshbb_selection_mode",
                              "dshbb_selection_items", "dshbb_use_polyhaven", "dshbb_use_hunyuan3d"):
                assert not hasattr(bpy.types.Scene, attribute), attribute
    finally:
        try:
            package.unregister()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PACKAGE_ROOT or name.startswith(PACKAGE_ROOT + "."):
                del sys.modules[name]

