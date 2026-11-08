"""Launcher/add-on install contract.

The DSH launcher injects LAUNCH_BOOT_PY into Blender and refuses anything that
is not the dsh_blender_bridge package. The launcher source and the real add-on both use the 3.1
wire protocol (12) and add-on version 2.0; these tests keep the two in sync.
These tests read the launcher source (the boot script is a string in
runtime/launch/resolve.ts) and compare the contract with the real add-on package so a
version/prorocol drift fails here before a user sees the Connect error.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import bpy_stub

bpy = bpy_stub.install()

protocol = importlib.import_module("extension.dsh_blender_bridge.protocol")
pkg = importlib.import_module("extension.dsh_blender_bridge")

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCH_SOURCE = (REPO_ROOT / "runtime" / "launch" / "resolve.ts").read_text(encoding="utf-8")


def test_launcher_requires_the_package_install_not_the_old_single_file():
    assert 'importlib.import_module("dsh_blender_bridge")' in LAUNCH_SOURCE
    assert "single-file installs are unsupported" in LAUNCH_SOURCE
    assert (
        '"addon_module": None, "addon_version": None, "addon_protocol": None, "addon_ok": False'
        in LAUNCH_SOURCE
    )


def test_launcher_requires_protocol_12_and_version_at_least_2_0():
    assert "protocol == 12" in LAUNCH_SOURCE
    assert "tuple(version) >= (2, 0)" in LAUNCH_SOURCE
    assert "addon contract mismatch" in LAUNCH_SOURCE
    assert "need >=2.0 / 12" in LAUNCH_SOURCE


def test_real_addon_satisfies_the_launcher_contract():
    assert pkg.ADDON_VERSION == protocol.ADDON_VERSION == (2, 0, 0)
    assert pkg.ADDON_PROTOCOL_VERSION == protocol.ADDON_PROTOCOL_VERSION == 12
    assert tuple(pkg.ADDON_VERSION) >= (2, 0)
    assert pkg.ADDON_PROTOCOL_VERSION == 12
