"""Unit checks for the viewport presence snapshot (no real GPU or window)."""

from __future__ import annotations

import importlib
import json

import bpy_stub

bpy_stub.install()

presence_draw = importlib.import_module("extension.dsh_blender_bridge.presence_draw")
presence_frustum = importlib.import_module("extension.dsh_blender_bridge.presence_frustum")


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

def test_presence_rows_expose_identity_activity_and_hidden_state():
    payload = {
        "instanceId": "inst-1",
        "agents": [
            {
                "id": "a1b2c3d4e5f6",
                "shortId": "A" + "1",
                "displayName": "agent-main",
                "color": "#4C8DFF",
                "lastActivity": "see:viewport",
                "lastActivityAt": 1760000000000,
                "view": {"viewpoint": "viewport"},
            }
        ],
    }
    presence_draw.set_presence(payload)
    rows = presence_draw.presence_rows()
    assert rows == [
        {
            "id": "a1b2c3d4e5f6",
            "shortId": "A" + "1",
            "displayName": "agent-main",
            "color": "#4C8DFF",
            "lastActivity": "see:viewport",
            "lastActivityAt": 1760000000000.0,
            "activity": "",
            "hidden": False,
        }
    ]
    assert presence_draw.toggle_agent("a1b2c3d4e5f6") is True
    assert presence_draw.presence_rows()[0]["hidden"] is True
    presence_draw.set_presence(payload)
    assert presence_draw.presence_rows()[0]["hidden"] is True
    assert presence_draw.toggle_agent("a1b2c3d4e5f6") is False
    assert presence_draw.presence_rows()[0]["hidden"] is False
    tearDown_function()


def test_preferences_control_color_and_short_id(monkeypatch):
    class FakePrefs:
        presence_color = (0.10, 0.20, 0.30, 1.0)
        presence_color_by_agent = False
        presence_show_ids = True

    monkeypatch.setattr(presence_draw, "_prefs", lambda: FakePrefs())
    row = {"color": "#FF6B6B", "shortId": "A" + "1", "displayName": "agent-main"}
    assert presence_draw._agent_color(row) == (0.10, 0.20, 0.30, 1.0)
    assert presence_draw._short_label(row) == "A" + "1" + " \u00b7 agent-main"

    FakePrefs.presence_color_by_agent = True
    FakePrefs.presence_show_ids = False
    assert presence_draw._agent_color(row) == (1.0, 107 / 255.0, 107 / 255.0, 1.0)
    assert presence_draw._short_label(row) == "agent-main"

def test_label_screen_projects_world_point(monkeypatch):
    class Coords:
        def __init__(self, x, y, w):
            self.x = x
            self.y = y
            self.w = w

    class FakeMatrix:
        def __init__(self, w=1.0):
            self.w = w

        def __matmul__(self, vector):
            assert len(vector) == 4 and vector[3] == 1.0
            return Coords(0.5, 0.25, self.w)

    class Region:
        x = 10
        y = 20
        width = 200
        height = 100

    class Region3D:
        perspective_matrix = FakeMatrix()

    monkeypatch.setattr(presence_draw, "Vector", lambda values: tuple(values))
    assert presence_draw._label_screen(Region(), Region3D(), (1.0, 2.0, 3.0)) == (160.0, 82.5)

    Region3D.perspective_matrix = FakeMatrix(w=-1.0)
    assert presence_draw._label_screen(Region(), Region3D(), (1.0, 2.0, 3.0)) is None


def test_selection_uid_object_prefix_resolves_the_named_object(monkeypatch):
    class FakeVector:
        def __init__(self, values):
            self.x, self.y, self.z = float(values[0]), float(values[1]), float(values[2])

    class FakeMatrix:
        def __matmul__(self, value):
            return value

    class FakeObject:
        type = "MESH"
        matrix_world = FakeMatrix()
        bound_box = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]

    class FakeLight(FakeObject):
        type = "LIGHT"

    class FakeEmpty(FakeObject):
        type = "EMPTY"
        bound_box = []

    drawn = []
    monkeypatch.setattr(presence_draw, "Vector", FakeVector)
    monkeypatch.setattr(presence_draw, "_draw_lines",
                        lambda vertices, color: drawn.append((vertices, color)))
    named = {"Cube": FakeObject, "Lamp": FakeLight, "Plain": FakeEmpty}
    objects = type("Objects", (), {
        "get": staticmethod(lambda name: named[name]() if name in named else None),
    })()
    monkeypatch.setattr(presence_draw.bpy.data, "objects", objects, raising=False)

    presence_draw._selection("object:Cube", (1.0, 1.0, 1.0, 1.0))
    assert len(drawn) == 1
    assert len(drawn[0][0]) == 24

    drawn.clear()
    presence_draw._selection("Cube", (1.0, 1.0, 1.0, 1.0))
    assert len(drawn) == 1

    # Lights and other non-MESH selections still get a box.
    drawn.clear()
    presence_draw._selection("object:Lamp", (1.0, 1.0, 1.0, 1.0))
    assert len(drawn) == 1
    assert len(drawn[0][0]) == 24

    # An object without a usable bounding box is skipped instead of crashing.
    drawn.clear()
    presence_draw._selection("object:Plain", (1.0, 1.0, 1.0, 1.0))
    assert drawn == []

    drawn.clear()
    for uid in ("material:Red", "collection:Cube", "object:Missing", ""):
        presence_draw._selection(uid, (1.0, 1.0, 1.0, 1.0))
    assert drawn == []


def test_fallback_ortho_frustum_draws_parallel_boxes(monkeypatch):
    class FakeVector:
        def __init__(self, values):
            self.x, self.y, self.z = float(values[0]), float(values[1]), float(values[2])

        def __add__(self, other):
            return FakeVector((self.x + other.x, self.y + other.y, self.z + other.z))

        def copy(self):
            return FakeVector((self.x, self.y, self.z))

    class Identity:
        def __matmul__(self, value):
            return value

    class World:
        translation = FakeVector((0.0, 0.0, 0.0))

    monkeypatch.setattr(presence_frustum, "Vector", FakeVector)
    monkeypatch.setattr(presence_frustum, "rotation", lambda world: Identity())
    lines = presence_frustum.fallback_frustum(
        {"ortho": True, "orthoScale": 4.0, "width": 1024, "height": 576}, World())

    assert lines is not None
    assert len(lines) == 24
    points = {tuple(point) for point in lines}
    assert len(points) == 8
    # An orthographic frustum has no apex: every line is a box edge, so the
    # camera origin never appears in the drawn geometry.
    assert (0.0, 0.0, 0.0) not in points

