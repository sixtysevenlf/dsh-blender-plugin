"""Unit checks for selection/collection payload building and posting (selection.py)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

compat = importlib.import_module("addon.dsh_blender_bridge.compat")
selection = importlib.import_module("addon.dsh_blender_bridge.commands.selection")


def _mesh(name, collections=("SetA",), dimensions=(1.0, 2.0, 3.0)):
    return SimpleNamespace(
        name=name,
        type="MESH",
        dimensions=SimpleNamespace(x=dimensions[0], y=dimensions[1], z=dimensions[2]),
        users_collection=[SimpleNamespace(name=value) for value in collections],
        location=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        rotation_euler=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        scale=SimpleNamespace(x=1.0, y=1.0, z=1.0),
    )


def _reset_scene():
    bpy.context.selected_objects = []
    bpy.context.active_object = None
    bpy.context.mode = "OBJECT"
    bpy.context.scene.name = "Scene"
    bpy.data.filepath = ""
    bpy.data.collections = {}


class _CollectionMap(dict):
    """dict lookups plus Blender-style iteration over the collection values."""

    def __iter__(self):
        return iter(self.values())


class _BoomDimensions(SimpleNamespace):
    @property
    def dimensions(self):
        raise RuntimeError("no dimensions")


def test_selection_payload_shape_caps_and_collection_summary():
    objects = [_mesh(f"Obj{i:03d}", ("SetA", "SetB") if i % 2 else ("SetA",)) for i in range(60)]
    bpy.context.selected_objects = objects
    bpy.context.active_object = objects[0]
    bpy.context.mode = "OBJECT"
    bpy.context.scene.name = "Scene"
    bpy.data.filepath = "/tmp/scene.blend"
    try:
        payload = selection.build_selection_payload(include_current_command=False)
        assert payload["v"] == 1
        assert payload["source"] == "blender-addon"
        assert isinstance(payload["sent_at"], int)
        body = payload["selection"]
        assert body["kind"] == "objects"
        assert body["count"] == 60
        assert len(body["names"]) == 60
        assert body["truncated"] is False
        assert body["active"] == "Obj000"
        assert body["mode"] == "OBJECT"
        assert len(body["objects"]) == compat.SELECTION_PAYLOAD_OBJECTS == 50
        assert body["objects"][0] == {
            "name": "Obj000", "type": "MESH", "dimensions_mm": [1000.0, 2000.0, 3000.0],
        }
        assert body["collections"][0] == {"name": "SetA", "count": 60}
        assert "sub" not in body
        assert payload["scene"] == {"name": "Scene", "file": "scene.blend"}
        assert payload["activity"]["source"] == "addon"
    finally:
        _reset_scene()


def test_selection_payload_caps_names_and_marks_truncation():
    objects = [_mesh(f"Obj{i:03d}") for i in range(250)]
    bpy.context.selected_objects = objects
    bpy.context.active_object = objects[249]
    try:
        body = selection.build_selection_payload(include_current_command=False)["selection"]
        assert body["count"] == 250
        assert len(body["names"]) == compat.SELECTION_PAYLOAD_NAMES == 200
        assert body["truncated"] is True
        assert body["active"] == "Obj249"
    finally:
        _reset_scene()


def test_empty_selection_payload_is_well_formed():
    _reset_scene()
    body = selection.build_selection_payload(include_current_command=False)["selection"]
    assert body == {
        "kind": "objects", "count": 0, "names": [], "truncated": False,
        "active": None, "mode": "OBJECT", "objects": [], "collections": [],
    }


def test_missing_dimensions_are_omitted_not_faked():
    broken = _BoomDimensions(name="Broken", type="MESH", users_collection=[])
    bpy.context.selected_objects = [broken]
    try:
        body = selection.build_selection_payload(include_current_command=False)["selection"]
        assert body["objects"] == [{"name": "Broken", "type": "MESH"}]
    finally:
        _reset_scene()


def test_sub_selection_is_attached_when_present(monkeypatch):
    _reset_scene()
    sub = {"type": "MESH", "counts": {"vertices": 1}, "indices": {"vertices": [0]},
           "truncated": False}
    monkeypatch.setattr(selection, "_read_sub_selection", lambda: sub)
    payload = selection.build_selection_payload(include_current_command=False)
    assert payload["selection"]["sub"] == sub


def test_collection_payload_and_missing_collection():
    objects = [_mesh(f"Obj{i:03d}") for i in range(3)]
    bpy.data.collections = {"SetA": SimpleNamespace(objects=objects)}
    try:
        payload = selection.build_collection_payload("SetA", include_current_command=False)
        assert payload["selection"]["kind"] == "collection"
        assert payload["selection"]["collection"] == "SetA"
        assert payload["selection"]["count"] == 3
        assert payload["selection"]["names"] == ["Obj000", "Obj001", "Obj002"]
        assert payload["selection"]["truncated"] is False
        assert payload["selection"]["collections"] == [{"name": "SetA", "count": 3}]
        assert selection.build_collection_payload("Missing", include_current_command=False) is None
        assert selection.build_collection_payload("", include_current_command=False) is None
        assert selection.build_collection_payload(None, include_current_command=False) is None
    finally:
        _reset_scene()


def test_collection_payload_caps_member_names():
    objects = [_mesh(f"Obj{i:03d}") for i in range(205)]
    bpy.data.collections = {"Big": SimpleNamespace(objects=objects)}
    try:
        body = selection.build_collection_payload("Big", include_current_command=False)["selection"]
        assert body["count"] == 205
        assert len(body["names"]) == 200
        assert body["truncated"] is True
    finally:
        _reset_scene()


def test_best_collection_for_selection_and_mode_update():
    first = _mesh("A", ("Rig",))
    second = _mesh("B", ("Rig", "Set"))
    bpy.context.selected_objects = [first, second]
    rig = SimpleNamespace(name="Rig")
    other = SimpleNamespace(name="Set")
    bpy.data.collections = {"Rig": rig, "Set": other}
    try:
        assert selection._best_collection_for_selection() is rig

        scene = SimpleNamespace(dshbb_selection_mode="MULTI", dshbb_collection_target=None)
        selection._on_selection_mode_update(scene, bpy.context)
        assert scene.dshbb_collection_target is None

        scene = SimpleNamespace(dshbb_selection_mode="COLLECTION", dshbb_collection_target=None)
        selection._on_selection_mode_update(scene, bpy.context)
        assert scene.dshbb_collection_target is rig

        existing = SimpleNamespace(name="Keep")
        scene = SimpleNamespace(dshbb_selection_mode="COLLECTION", dshbb_collection_target=existing)
        selection._on_selection_mode_update(scene, bpy.context)
        assert scene.dshbb_collection_target is existing

        bpy.context.selected_objects = []
        assert selection._best_collection_for_selection() is None
    finally:
        _reset_scene()


def test_build_collections_payload_counts_selected_members():
    a = _mesh("A")
    b = _mesh("B")
    bpy.context.selected_objects = [a]
    parent = SimpleNamespace(name="Root")
    bpy.data.collections = _CollectionMap({
        "Loose": SimpleNamespace(name="Loose", objects=[a, b], parent=None),
        "Nested": SimpleNamespace(name="Nested", objects=[b], parent=parent),
    })
    try:
        payload = selection.build_collections_payload(limit=10)
        assert payload["count"] == 2
        assert payload["returned"] == 2
        rows = {row["name"]: row for row in payload["collections"]}
        assert rows["Loose"]["count"] == 2
        assert rows["Loose"]["selected_count"] == 1
        assert rows["Loose"]["parent"] is None
        assert rows["Nested"]["parent"] == "Root"
        limited = selection.build_collections_payload(limit=1)
        assert limited["count"] == 2 and limited["returned"] == 1
        invalid = selection.build_collections_payload(limit="not-a-number")
        assert invalid["returned"] == 2
    finally:
        _reset_scene()


class _Response:
    def __init__(self, status_code=200, body=None, json_error=False):
        self.status_code = status_code
        self._body = body
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._body


def test_post_selection_payload_posts_to_backend_and_reports_seq(monkeypatch):
    calls = {}
    response = _Response(200, {"ok": True, "seq": 7, "received_at": "now"})

    def fake_post(url, json=None, timeout=None):
        calls.update({"url": url, "json": json, "timeout": timeout})
        return response

    monkeypatch.setattr(selection.requests, "post", fake_post)
    bpy.context.scene.dshbb_backend_url = ""
    try:
        result = selection._post_selection_payload({"v": 1}, timeout=1.5)
        assert calls["url"] == "http://127.0.0.1:9877/host/selection"
        assert calls["json"] == {"v": 1}
        assert calls["timeout"] == 1.5
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["seq"] == 7
        assert result["received_at"] == "now"
        assert result["payload"] == {"v": 1}
    finally:
        bpy.context.scene.dshbb_backend_url = ""


def test_post_selection_payload_reports_http_and_body_errors(monkeypatch):
    bpy.context.scene.dshbb_backend_url = ""
    try:
        monkeypatch.setattr(
            selection.requests, "post",
            lambda *a, **k: _Response(200, {"ok": False, "error": "rejected"}),
        )
        rejected = selection._post_selection_payload({})
        assert rejected["ok"] is False
        assert rejected["error"] == "rejected"

        monkeypatch.setattr(selection.requests, "post", lambda *a, **k: _Response(500, {"ok": False}))
        failed = selection._post_selection_payload({})
        assert failed["ok"] is False
        assert failed["error"] == "HTTP 500"

        monkeypatch.setattr(selection.requests, "post", lambda *a, **k: _Response(200, None, json_error=True))
        garbage = selection._post_selection_payload({})
        assert garbage["ok"] is False
        assert "响应不是 JSON" in garbage["error"]
    finally:
        bpy.context.scene.dshbb_backend_url = ""


def test_post_selection_payload_network_failure_has_hint(monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("backend down")

    bpy.context.scene.dshbb_backend_url = ""
    monkeypatch.setattr(selection.requests, "post", boom)
    try:
        result = selection._post_selection_payload({})
        assert result["ok"] is False
        assert result["error"] == "backend down"
        assert "127.0.0.1:9877" in result["hint"]
    finally:
        bpy.context.scene.dshbb_backend_url = ""


def test_send_collection_to_dsh_does_not_post_an_unknown_collection(monkeypatch):
    def no_post(*args, **kwargs):
        raise AssertionError("must not post when the collection is unknown")

    monkeypatch.setattr(selection.requests, "post", no_post)
    bpy.data.collections = {}
    result = selection.send_collection_to_dsh(collection_name="Missing")
    assert result["ok"] is False
    assert result["error"] == "collection_not_found"
    assert result["collection"] == "Missing"


def test_record_selection_send_tracks_last_result():
    result = {
        "ok": True, "seq": 3, "backend_url": "http://127.0.0.1:9877",
        "payload": {"selection": {"count": 2, "kind": "objects"}},
    }
    returned = selection._record_selection_send(result)
    state = selection._selection_send_state
    assert returned is result
    assert state["ok"] is True
    assert state["seq"] == 3
    assert state["count"] == 2
    assert state["kind"] == "objects"
    assert isinstance(state["at"], float)


def test_selection_commands_delegate_with_scene_and_file(monkeypatch):
    objects = [_mesh("Cube")]
    bpy.context.selected_objects = objects
    bpy.context.active_object = objects[0]
    bpy.context.scene.name = "Scene"
    bpy.data.filepath = "/tmp/model.blend"
    try:
        commands = selection.SelectionCommands()
        selection_info = commands.get_selection()
        assert selection_info["count"] == 1
        assert selection_info["names"] == ["Cube"]
        assert selection_info["scene"] == "Scene"
        assert selection_info["file"] == "model.blend"
        assert "activity" in selection_info

        monkeypatch.setattr(selection, "_post_selection_payload", lambda payload, timeout=3.0: {"ok": True})
        sent = commands.send_selection_to_dsh(timeout=2.0)
        assert sent["ok"] is True

        listed = commands.get_collections(limit=5)
        assert listed["returned"] <= 5
    finally:
        _reset_scene()
