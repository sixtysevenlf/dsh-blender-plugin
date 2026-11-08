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
registration = importlib.import_module("addon.dsh_blender_bridge.registration")


EXPECTED_PACKAGE_FILES = [
    "__init__.py", "assets/__init__.py", "assets/geometry.py",
    "assets/io.py", "command_registry.py", "commands/__init__.py",
    "commands/activity.py", "commands/assets_polyhaven.py", "commands/core/__init__.py",
    "commands/core/bpy_lookup.py", "commands/core/describe.py", "commands/core/screenshot.py",
    "commands/hunyuan/__init__.py", "commands/hunyuan/client.py", "commands/hunyuan/importing.py",
    "commands/hunyuan/poll.py", "commands/hunyuan/submit.py", "commands/hyper3d/__init__.py",
    "commands/hyper3d/client.py", "commands/hyper3d/importing.py", "commands/hyper3d/poll.py",
    "commands/hyper3d/submit.py", "commands/material.py", "commands/polyhaven/__init__.py",
    "commands/polyhaven/download.py", "commands/polyhaven/hdri.py", "commands/polyhaven/material.py",
    "commands/polyhaven/model.py", "commands/polypizza/__init__.py", "commands/polypizza/client.py",
    "commands/polypizza/download.py", "commands/polypizza/importer.py", "commands/scene/__init__.py",
    "commands/scene/export.py", "commands/scene/world.py", "commands/selection.py",
    "commands/sketchfab/__init__.py", "commands/sketchfab/client.py", "commands/sketchfab/download.py",
    "commands/sketchfab/importer.py", "commands/telemetry.py", "compat.py",
    "config.py", "integrations/__init__.py", "integrations/hunyuan_client.py",
    "integrations/hyper3d_client.py", "integrations/polyhaven_client.py", "integrations/polyhaven_spec.py",
    "integrations/polypizza_client.py", "integrations/sketchfab_client.py", "protocol.py",
    "recorders/__init__.py", "recorders/activity.py", "recorders/edit_capture.py",
    "recorders/user_edit.py", "registration.py", "selection/__init__.py",
    "selection/caps.py", "selection/payload.py", "server.py",
    "ui/__init__.py", "ui/lists.py", "ui/operators.py",
    "ui/panels/__init__.py", "ui/panels/api.py", "ui/panels/env.py",
    "ui/panels/index.py", "ui/panels/model.py", "ui/preferences.py",
]


def test_bl_info_and_protocol_versions_are_frozen():
    assert pkg.bl_info == {
        "name": "DSH Blender Bridge",
        "author": "Siddharth Ahuja",
        "version": (1, 11),
        "blender": (4, 2, 0),
        "location": "View3D > Sidebar > DSH Blender Bridge",
        "description": "Connect Blender to DSH via a local TCP bridge",
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
    assert len(actual) == 69
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
    assert pkg.register is registration.register
    assert pkg.unregister is registration.unregister
    source = Path(pkg.__file__).read_text(encoding="utf-8")
    registration_source = Path(registration.__file__).read_text(encoding="utf-8")
    assert "from .registration import register, unregister" in source
    assert registration_source.count("def register():") == 1
    assert registration_source.count("def unregister():") == 1
    assert 'if __name__ == "__main__":' in source
