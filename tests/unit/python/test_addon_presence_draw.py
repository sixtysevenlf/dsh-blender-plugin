"""Unit checks for the viewport presence snapshot (no real GPU or window)."""

from __future__ import annotations

import importlib
import json

import bpy_stub

bpy_stub.install()

presence_draw = importlib.import_module("extension.dsh_blender_bridge.presence_draw")


def tearDown_function():
    presence_draw.clear_presence()


def test_snapshot_round_trip():
    presence_draw.set_presence(
        {
            "instanceId": "inst-1",
            "paused": True,
            "agents": [{"displayName": "agent-main", "color": "#4C8DFF"}],
        }
    )
    assert presence_draw.get_presence() == {
        "agents": [{"displayName": "agent-main", "color": "#4C8DFF"}],
        "paused": True,
    }
    tearDown_function()


def test_accepts_json_and_caps_agents():
    agents = [{"displayName": "agent-" + str(index)} for index in range(9)]
    presence_draw.set_presence(json.dumps({"agents": agents, "paused": False}))
    assert len(presence_draw.get_presence()["agents"]) == 8
    tearDown_function()


def test_invalid_payload_keeps_the_empty_snapshot():
    for payload in ("not-json", None, 42, {"agents": "nope"}):
        presence_draw.set_presence(payload)
        assert presence_draw.get_presence() == {"agents": [], "paused": False}
    tearDown_function()


def test_clear_presence_removes_the_snapshot():
    presence_draw.set_presence({"agents": [{"displayName": "agent-main"}]})
    presence_draw.clear_presence()
    assert presence_draw.get_presence() == {"agents": [], "paused": False}


def test_no_draw_handlers_without_a_view3d_type():
    presence_draw.set_presence({"agents": [{"displayName": "agent-main"}]})
    assert presence_draw._HANDLES == []
    tearDown_function()
