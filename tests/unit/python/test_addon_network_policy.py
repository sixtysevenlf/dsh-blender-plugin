"""Online-access policy: network asset commands fail closed, local commands keep working."""

from __future__ import annotations

import importlib

import bpy_stub

bpy = bpy_stub.install()

server_module = importlib.import_module("extension.dsh_blender_bridge.server")
registry = importlib.import_module("extension.dsh_blender_bridge.command_registry")
config = importlib.import_module("extension.dsh_blender_bridge.config")


def test_network_commands_fail_closed_when_online_access_is_off(monkeypatch):
    server = server_module.DSHBlenderBridgeServer()
    scene = bpy.context.scene
    for gate in registry.GATE_PROPERTIES.values():
        setattr(scene, gate, True)
    monkeypatch.setattr(bpy.app, "online_access", False)

    blocked = server.execute_command(
        {"type": "search_polyhaven_assets", "params": {"asset_type": "models"}}
    )
    assert blocked["status"] == "error"
    assert blocked["code"] == "network_policy"
    assert "online access" in blocked["message"].lower()
    assert "Allow Online Access" in blocked["fix"]

    categories = server.execute_command(
        {"type": "get_polyhaven_categories", "params": {"asset_type": "models"}}
    )
    assert categories["status"] == "error"
    assert categories["code"] == "network_policy"

    local = server.execute_command({"type": "get_addon_info", "params": {}})
    assert local["status"] == "success"
    assert local["result"]["protocol_version"] == 12


def test_network_commands_still_reach_handlers_when_online_access_is_on(monkeypatch):
    server = server_module.DSHBlenderBridgeServer()
    scene = bpy.context.scene
    for gate in registry.GATE_PROPERTIES.values():
        setattr(scene, gate, True)
    monkeypatch.setattr(bpy.app, "online_access", True)

    called = []
    monkeypatch.setattr(
        server,
        "search_polyhaven_assets",
        lambda **params: called.append(params) or {"assets": []},
    )
    out = server.execute_command(
        {"type": "search_polyhaven_assets", "params": {"asset_type": "models"}}
    )
    assert out == {"status": "success", "result": {"assets": []}}
    assert called == [{"asset_type": "models"}]


def test_online_access_defaults_to_enabled_when_blender_has_no_switch(monkeypatch):
    monkeypatch.delattr(bpy.app, "online_access", raising=False)
    assert config.online_access_enabled() is True
