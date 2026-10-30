"""Launcher/add-on install contract (T10, plan 8.2 B4).

The DSH launcher injects LAUNCH_BOOT_PY into Blender and refuses anything that
is not the dsh_blender_bridge package with protocol 11 / add-on version >= 1.10.
These tests read the launcher source (the boot script is a string in
runtime/launch.mjs) and compare the contract with the real add-on package so a
version/prorocol drift fails here before a user sees the Connect error.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import bpy_stub

bpy = bpy_stub.install()

protocol = importlib.import_module("addon.dsh_blender_bridge.protocol")
pkg = importlib.import_module("addon.dsh_blender_bridge")

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCH_SOURCE = (REPO_ROOT / "runtime" / "launch.mjs").read_text(encoding="utf-8")


def test_launcher_requires_the_package_install_not_the_old_single_file():
    assert 'importlib.import_module("dsh_blender_bridge")' in LAUNCH_SOURCE
    assert "is not a package (single-file installs are unsupported)" in LAUNCH_SOURCE
    assert '"addon_version": None, "addon_protocol": None, "addon_ok": False' in LAUNCH_SOURCE


def test_launcher_requires_protocol_11_and_version_at_least_1_10():
    assert "protocol == 11" in LAUNCH_SOURCE
    assert "tuple(version) >= (1, 10)" in LAUNCH_SOURCE
    assert "addon contract mismatch" in LAUNCH_SOURCE
    assert "need >=1.10 / 11" in LAUNCH_SOURCE


def test_real_addon_satisfies_the_launcher_contract():
    assert pkg.ADDON_VERSION == protocol.ADDON_VERSION == (1, 11)
    assert pkg.ADDON_PROTOCOL_VERSION == protocol.ADDON_PROTOCOL_VERSION == 11
    assert tuple(pkg.ADDON_VERSION) >= (1, 10)
    assert pkg.ADDON_PROTOCOL_VERSION == 11
