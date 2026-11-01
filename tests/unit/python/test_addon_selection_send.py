"""Unit checks for selection/collection posting and state helpers (F3.4).

Payload construction itself lives in test_addon_selection.py; this file covers
the collection pickers, POST envelope, failure branches and the command class.
The HTTP call is replaced by an in-memory double - no socket is opened.
"""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

selection = importlib.import_module("addon.dsh_blender_bridge.commands.selection")


class _Collections(dict):
    """Dict of name -> collection that iterates over the collection values."""

    def __iter__(self):
        return iter(self.values())


class _Collection:
    def __init__(self, name, objects=(), parent=None):
        self.name = name
        self.objects = list(objects)
        self.parent = parent


class _Obj:
    def __init__(self, name, collections=(), obj_type="MESH", dimensions=(1.0, 2.0, 3.0)):
        self.name = name
        self.type = obj_type
        self.users_collection = list(collections)
        self.dimensions = SimpleNamespace(
            x=dimensions[0], y=dimensions[1], z=dimensions[2])


def _tracker(snapshot=None):
    return SimpleNamespace(
        tick=lambda force=False: {"count": 0, "names": [], "active": None,
                                  "mode": "OBJECT", "truncated": False},
        snapshot=lambda include_current_command=True: dict(
            snapshot or {"state": "idle", "human": False}),
    )


def _with_tracker(monkeypatch_target=None):
    original = selection.get_activity_tracker
    selection.get_activity_tracker = lambda: _tracker()
    return original


def test_best_collection_for_selection_picks_the_most_common():
    shared = _Collection("Shared")
    rare = _Collection("Rare")
    first = _Obj("A", [shared, rare])
    second = _Obj("B", [shared])
    bpy.data.collections = _Collections({"Shared": shared, "Rare": rare})
    bpy.context.selected_objects = [first, second]
    try:
        assert selection.best_collection_for_selection() is shared
    finally:
        bpy.data.collections = {}
    bpy.context.selected_objects = []
    assert selection.best_collection_for_selection() is None


def test_best_collection_for_selection_tolerates_broken_objects():
    class _Broken:
        name = "Broken"
        type = "MESH"

        @property
        def users_collection(self):
            raise RuntimeError("no collection access")

    bpy.context.selected_objects = [_Broken()]
    assert selection.best_collection_for_selection() is None
    bpy.context.selected_objects = []


def test_on_selection_mode_update_fills_empty_target():
    shared = _Collection("Shared")
    bpy.data.collections = _Collections({"Shared": shared})
    bpy.context.selected_objects = [_Obj("A", [shared])]
    target = SimpleNamespace(dshbb_selection_mode="COLLECTION",
                             dshbb_collection_target=None)
    original = selection.best_collection_for_selection
    selection.best_collection_for_selection = lambda: shared
    try:
        selection.on_selection_mode_update(target, None)
        assert target.dshbb_collection_target is shared
        target.dshbb_selection_mode = "MULTI"
        target.dshbb_collection_target = None
        selection.on_selection_mode_update(target, None)
        assert target.dshbb_collection_target is None
        selection.best_collection_for_selection = lambda: (_ for _ in ()).throw(
            RuntimeError("boom"))
        selection.on_selection_mode_update(target, None)
    finally:
        selection.best_collection_for_selection = original
        bpy.data.collections = {}
        bpy.context.selected_objects = []


def test_selection_collection_summary_deduplicates_and_sorts():
    shared = _Collection("Shared")
    rare = _Collection("Rare")
    first = _Obj("A", [shared, rare])
    second = _Obj("B", [shared])
    summary = selection.selection_collection_summary([first, second])
    assert summary == [{"name": "Shared", "count": 2}, {"name": "Rare", "count": 1}]
    assert selection.selection_collection_summary([], cap=5) == []


def test_scene_objects_within_skips_missing_dimensions_and_caps():
    good = _Obj("Good")
    broken = SimpleNamespace(name="Broken", type="MESH")
    objects = selection._scene_objects_within([good, broken], cap=1)
    assert objects == [{"name": "Good", "type": "MESH",
                        "dimensions_mm": [1000.0, 2000.0, 3000.0]}]
    assert selection._scene_objects_within([good, broken])[1] == {
        "name": "Broken", "type": "MESH"}


def test_build_collection_payload_requires_an_existing_collection():
    assert selection.build_collection_payload("") is None
    assert selection.build_collection_payload("Missing") is None

    collection = _Collection("SetA", [_Obj("A"), _Obj("B")])
    bpy.data.collections = _Collections({"SetA": collection})
    original = _with_tracker()
    try:
        payload = selection.build_collection_payload("SetA")
    finally:
        selection.get_activity_tracker = original
        bpy.data.collections = {}
    assert payload["selection"]["kind"] == "collection"
    assert payload["selection"]["collection"] == "SetA"
    assert payload["selection"]["count"] == 2
    assert payload["selection"]["names"] == ["A", "B"]
    assert payload["selection"]["truncated"] is False


def test_build_collections_payload_lists_rows_and_caps_limit():
    first = _Collection("SetA", [_Obj("A"), _Obj("B")])
    second = _Collection("SetB", [])
    third = _Collection("SetC", [_Obj("C")], parent=first)
    bpy.data.collections = _Collections({"SetA": first, "SetB": second, "SetC": third})
    bpy.context.selected_objects = [_Obj("A", [first]), _Obj("C", [third])]
    try:
        payload = selection.build_collections_payload(limit=2)
        invalid_limit_payload = selection.build_collections_payload(limit="bad")
    finally:
        bpy.data.collections = {}
        bpy.context.selected_objects = []
    assert payload["count"] == 3
    assert payload["returned"] == 2
    assert payload["collections"][0]["name"] == "SetA"
    assert payload["collections"][0]["selected_count"] == 1
    assert payload["collections"][1]["name"] == "SetC"
    assert payload["collections"][1]["parent"] == "SetA"
    assert invalid_limit_payload["returned"] == 3


class _Response:
    def __init__(self, status_code=200, body=None, json_raises=False):
        self.status_code = status_code
        self._body = body
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._body


def _install_post(response=None, raise_error=None):
    original = (selection.requests, selection.dshbb_backend_url)
    state = SimpleNamespace(calls=[])

    def _post(url, json=None, timeout=None):
        state.calls.append({"url": url, "json": json, "timeout": timeout})
        if raise_error is not None:
            raise raise_error
        return response

    selection.requests = SimpleNamespace(post=_post)
    selection.dshbb_backend_url = lambda: "http://127.0.0.1:9877"
    return state, original


def _restore(original):
    selection.requests, selection.dshbb_backend_url = original


def test_post_selection_payload_success():
    state, original = _install_post(_Response(body={"ok": True, "seq": 4,
                                                    "received_at": 123}))
    try:
        result = selection._post_selection_payload({"kind": "objects"}, timeout=1.5)
    finally:
        _restore(original)
    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["seq"] == 4
    assert result["received_at"] == 123
    assert state.calls[0]["url"] == "http://127.0.0.1:9877/host/selection"
    assert state.calls[0]["timeout"] == 1.5


def test_post_selection_payload_reports_http_errors():
    _state, original = _install_post(_Response(status_code=500, body={"ok": False,
                                                                     "error": "boom"}))
    try:
        result = selection._post_selection_payload({"kind": "objects"})
    finally:
        _restore(original)
    assert result["ok"] is False
    assert result["error"] == "boom"

    _state, original = _install_post(_Response(status_code=502, json_raises=True))
    try:
        result = selection._post_selection_payload({"kind": "objects"})
    finally:
        _restore(original)
    assert result["ok"] is False
    assert "不是 JSON" in result["error"]


def test_post_selection_payload_never_raises_on_connection_errors():
    _state, original = _install_post(raise_error=ConnectionRefusedError("refused"))
    try:
        result = selection._post_selection_payload({"kind": "objects"})
    finally:
        _restore(original)
    assert result["ok"] is False
    assert "refused" in result["error"]
    assert "127.0.0.1:9877" in result["hint"]


def test_send_collection_to_dsh_reports_missing_target():
    bpy.context.scene.dshbb_collection_target = None
    assert selection.send_collection_to_dsh() == {
        "ok": False, "error": "collection_not_found", "collection": None,
        "hint": "先在 N 面板「集合」下拉里选一个集合，或传 collection 名字"}
    assert selection.send_collection_to_dsh("Missing")["error"] == "collection_not_found"


def test_record_selection_send_tracks_the_last_result():
    result = selection.record_selection_send({
        "ok": True, "seq": 7, "backend_url": "http://127.0.0.1:9877",
        "payload": {"selection": {"kind": "collection", "count": 3,
                                  "collection": "SetA"}},
    })
    assert result["ok"] is True
    state = selection._selection_send_state
    assert state["ok"] is True
    assert state["seq"] == 7
    assert state["count"] == 3
    assert state["kind"] == "collection"
    assert state["collection"] == "SetA"
    assert isinstance(state["at"], float)
    assert state["at"] <= time.time()


def test_selection_command_class_wraps_module_functions():
    commands = selection.SelectionCommands()
    originals = (selection.build_selection_payload, selection.send_selection_to_dsh,
                 selection.build_collections_payload, selection.send_collection_to_dsh)

    def _raise(*args, **kwargs):
        raise RuntimeError("wrapped failure")

    try:
        selection.build_selection_payload = lambda include_current_command=True: {
            "selection": {"count": 1}, "scene": {"name": "Scene", "file": None},
            "activity": {"state": "idle"}}
        assert commands.get_selection() == {
            "count": 1, "scene": "Scene", "file": None, "activity": {"state": "idle"}}

        selection.send_selection_to_dsh = lambda timeout=3.0: {
            "ok": True, "seq": 1, "payload": {"selection": {"count": 1}}}
        assert commands.send_selection_to_dsh(timeout=1.0)["ok"] is True

        selection.build_collections_payload = lambda limit=200: {"count": 0}
        assert commands.get_collections(limit=5) == {"count": 0}

        selection.send_collection_to_dsh = lambda collection_name=None, timeout=3.0: {
            "ok": True, "payload": {"selection": {"kind": "collection"}}}
        assert commands.send_collection_to_dsh(collection="SetA")["ok"] is True

        selection.build_selection_payload = _raise
        assert commands.get_selection() == {"error": "wrapped failure"}
        selection.send_selection_to_dsh = _raise
        assert commands.send_selection_to_dsh() == {"ok": False, "error": "wrapped failure"}
        selection.build_collections_payload = _raise
        assert commands.get_collections() == {"error": "wrapped failure"}
        selection.send_collection_to_dsh = _raise
        assert commands.send_collection_to_dsh() == {"ok": False, "error": "wrapped failure"}
    finally:
        (selection.build_selection_payload, selection.send_selection_to_dsh,
         selection.build_collections_payload, selection.send_collection_to_dsh) = originals
