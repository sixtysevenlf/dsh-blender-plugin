"""Unit checks for the add-on package manifest, layout and module budget.

These run against the bpy stub: they prove the package is self-consistent without
claiming a real Blender session was exercised (that is tests/e2e/blender).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import bpy_stub

bpy = bpy_stub.install()

pkg = importlib.import_module("addon.dsh_blender_bridge")
protocol = importlib.import_module("addon.dsh_blender_bridge.protocol")


EXPECTED_PACKAGE_FILES = [
    "__init__.py", "config.py", "protocol.py", "server.py", "compat.py",
    "commands/__init__.py", "commands/core.py", "commands/scene.py", "commands/selection.py",
    "commands/activity.py", "commands/material.py", "commands/telemetry.py",
    "commands/assets_polyhaven.py", "commands/assets_sketchfab.py", "commands/assets_polypizza.py",
    "commands/generate_hyper3d.py", "commands/generate_hunyuan.py", "commands/generate_premium.py",
    "recorders/__init__.py", "recorders/user_edit.py", "recorders/activity.py", "recorders/edit_capture.py",
    "assets/__init__.py", "assets/io.py",
    "integrations/__init__.py", "integrations/polyhaven_client.py",
    "integrations/sketchfab_client.py", "integrations/polypizza_client.py",
    "integrations/hyper3d_client.py", "integrations/hunyuan_client.py",
    "premium/__init__.py", "premium/client.py", "premium/jobs.py", "premium/ui.py",
    "ui/__init__.py", "ui/preferences.py", "ui/panels.py", "ui/operators.py", "ui/lists.py",
]


def test_bl_info_and_protocol_versions_are_frozen():
    assert pkg.bl_info == {
        "name": "DSH Blender Bridge",
        "author": "Siddharth Ahuja",
        "version": (1, 11),
        "blender": (3, 0, 0),
        "location": "View3D > Sidebar > DSH Blender Bridge",
        "description": "Connect Blender to Claude via MCP",
        "doc_url": "https://github.com/sixtysevenlf/dsh-blender-plugin",
        "category": "Interface",
    }
    assert pkg.ADDON_VERSION == (1, 11) == pkg.bl_info["version"]
    assert pkg.ADDON_PROTOCOL_VERSION == 11
    assert protocol.ADDON_NAME == "DSH Blender Bridge"
    assert protocol.ADDON_VERSION == pkg.ADDON_VERSION
    assert protocol.ADDON_PROTOCOL_VERSION == pkg.ADDON_PROTOCOL_VERSION
    assert pkg.bl_info["name"] == protocol.ADDON_NAME


def test_package_layout_is_exactly_the_two_level_split():
    root = Path(pkg.__file__).resolve().parent
    actual = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.py"))
    assert actual == sorted(EXPECTED_PACKAGE_FILES)
    assert len(actual) == 39
    assert not (root.parent / "dsh_blender_bridge.py").exists(), "single-file install entry must be gone"
    assert not (root / "__pycache__").exists() or (root / "__pycache__").is_dir()


def test_every_module_stays_below_the_line_budget():
    root = Path(pkg.__file__).resolve().parent
    for path in root.rglob("*.py"):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 1000, f"{path.relative_to(root)} has {lines} lines"


def test_entry_points_are_importable_and_registered_once():
    assert callable(pkg.register)
    assert callable(pkg.unregister)
    source = Path(pkg.__file__).read_text(encoding="utf-8")
    assert source.count("def register():") == 1
    assert source.count("def unregister():") == 1
    assert 'if __name__ == "__main__":' in source
