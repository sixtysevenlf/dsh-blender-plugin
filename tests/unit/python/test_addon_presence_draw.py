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


def test_pose_vectors_accept_sequences_and_csv():
    assert presence_draw._vec3([1, 2, 3]) == [1.0, 2.0, 3.0]
    assert presence_draw._vec3((1, "2", 3.5)) == [1.0, 2.0, 3.5]
    assert presence_draw._vec3(" 6, -6, 4 ") == [6.0, -6.0, 4.0]
    for invalid in (None, 3, {}, "6,-6", "a,b,c"):
        assert presence_draw._vec3(invalid) is None


def test_matrix4_accepts_nested_rows_and_flat_values(monkeypatch):
    class FakeMatrix:
        def __init__(self, rows):
            self.rows = rows

    monkeypatch.setattr(presence_draw, "Matrix", FakeMatrix)
    nested = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    flat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    assert presence_draw._matrix4(nested).rows == nested
    assert presence_draw._matrix4(flat).rows == nested
    for invalid in ("nope", [[1, 2], [3, 4]], [1, 2, 3], None):
        assert presence_draw._matrix4(invalid) is None


def test_camera_world_inverts_the_published_world_to_view_matrix(monkeypatch):
    class FakeMatrix:
        def __init__(self, rows):
            self.rows = rows

        def inverted(self):
            return {"inverted": self.rows}

    monkeypatch.setattr(presence_draw, "Matrix", FakeMatrix)
    nested = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [4, -6, 3, 1]]
    assert presence_draw._camera_world({"viewMatrix": nested}) == {"inverted": nested}
    assert presence_draw._camera_world({}) is None
