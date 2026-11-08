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


def _package_entries():
    """Every file the deterministic builder ships, including metadata and licenses."""
    return sorted(
        [PACKAGE_ROOT + "/" + relative for relative in _package_python_files()]
        + [
            PACKAGE_ROOT + "/blender_manifest.toml",
            PACKAGE_ROOT + "/LICENSE",
            PACKAGE_ROOT + "/NOTICE",
        ]
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
        assert names == _package_entries()
        init_source = archive.read(PACKAGE_ROOT + "/__init__.py").decode("utf-8")
        assert "bl_info" not in init_source
        assert archive.read(PACKAGE_ROOT + "/gateway_schema.py").decode("utf-8").find("ADDON_PROTOCOL_VERSION = 12") != -1
        manifest = archive.read(PACKAGE_ROOT + "/blender_manifest.toml").decode("utf-8")
        assert 'schema_version = "1.0.0"' in manifest
        assert 'id = "dsh_blender_bridge"' in manifest
        assert 'version = "2.0.0"' in manifest
        assert 'license = ["SPDX:GPL-3.0-or-later"]' in manifest
        assert "GNU GENERAL PUBLIC LICENSE" in archive.read(PACKAGE_ROOT + "/LICENSE").decode("utf-8")
        notice = archive.read(PACKAGE_ROOT + "/NOTICE").decode("utf-8")
        assert "github.com/ahujasid/blender-mcp" in notice
        assert "MIT License" in notice


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
        assert package.ADDON_VERSION == (2, 0, 0)
        assert not hasattr(package, "bl_info")
        assert package.ADDON_PROTOCOL_VERSION == 12

        package.register()
        assert bpy.utils.registered, "register() did not register any classes"
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


def test_zip_contains_the_whole_module_package_without_the_old_single_file():
    with zipfile.ZipFile(ZIP_PATH) as archive:
        names = archive.namelist()
    expected = _package_entries()
    assert len(expected) == 76
    assert names == expected
    assert PACKAGE_ROOT + ".py" not in names
    assert PACKAGE_ROOT + "/blender_manifest.toml" in names
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

