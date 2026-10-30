"""Unit checks for the human/agent activity state machine (recorders/activity.py)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

activity = importlib.import_module("addon.dsh_blender_bridge.recorders.activity")


class _Recorder:
    def __init__(self, suppressed=False, depth=0):
        self._suppressed = suppressed
        self._agent_depth = depth


def _reset_scene():
    bpy.context.selected_objects = []
    bpy.context.active_object = None
    bpy.context.mode = "OBJECT"
    bpy.context.scene.name = "Scene"
    bpy.context.window = None


def _tracker(recorder=None):
    tracker = activity.ActivityTracker()
    if recorder is not None:
        tracker.attach(recorder)
    return tracker


def test_idle_then_human_then_agent_then_both_then_idle():
    _reset_scene()
    tracker = _tracker()
    now = 1000.0

    idle = tracker.snapshot(now=now, include_current_command=False)
    assert idle["state"] == "idle"
    assert idle["human_active"] is False
    assert idle["agent_active"] is False
    assert idle["human_idle_ms"] is None
    assert idle["agent_idle_ms"] is None
    assert idle["windows_ms"] == {"human": 1500, "agent": 1500}
    assert idle["source"] == "addon"
    assert idle["scene"] == "Scene"

    tracker.note_human("test-human", now=now)
    human = tracker.snapshot(now=now + 0.1, include_current_command=False)
    assert human["state"] == "human"
    assert human["human_active"] is True
    assert human["agent_active"] is False
    assert human["last_human_kind"] == "test-human"
    assert human["human_idle_ms"] == 100

    tracker.note_agent_end(now=now + 0.2)
    both = tracker.snapshot(now=now + 0.3, include_current_command=False)
    assert both["state"] == "both"
    assert both["human_active"] is True and both["agent_active"] is True
    assert both["agent_idle_ms"] == 100

    later = tracker.snapshot(now=now + 10.0, include_current_command=False)
    assert later["state"] == "idle"
    assert later["human_active"] is False and later["agent_active"] is False


def test_human_and_agent_windows_expire_at_one_point_five_seconds():
    _reset_scene()
    tracker = _tracker()
    tracker.note_human("human", now=10.0)
    tracker.note_agent_end(now=10.0)
    assert tracker.human_active(now=11.5) is True
    assert tracker.agent_active(now=11.5) is True
    assert tracker.human_active(now=11.51) is False
    assert tracker.agent_active(now=11.51) is False


def test_agent_depth_drives_agent_state_and_query_excludes_itself():
    _reset_scene()
    tracker = _tracker(_Recorder(depth=2))
    now = 500.0
    working = tracker.snapshot(now=now, include_current_command=True)
    assert working["state"] == "agent"
    assert working["agent_active"] is True
    assert working["agent_depth"] == 2

    query = tracker.snapshot(now=now, include_current_command=False)
    assert query["agent_depth"] == 1
    assert query["state"] == "agent"

    single = _tracker(_Recorder(depth=1))
    excluded = single.snapshot(now=now, include_current_command=False)
    assert excluded["agent_depth"] == 0
    assert excluded["state"] == "idle"


def test_suppressed_recorder_blocks_human_attribution():
    _reset_scene()
    tracker = _tracker(_Recorder(suppressed=True, depth=1))
    tracker.note_human("while-agent")
    assert tracker.human_active() is False
    assert tracker.agent_active() is True


def test_modal_operators_mark_the_human_as_active():
    _reset_scene()
    operation = SimpleNamespace(bl_idname="transform.translate")
    bpy.context.window = SimpleNamespace(modal_operators=[operation, SimpleNamespace(name="unnamed")])
    try:
        tracker = _tracker()
        snapshot = tracker.snapshot(now=20.0, include_current_command=False)
        assert snapshot["modal_operators"] == ["transform.translate", "unnamed"]
        assert snapshot["human_active"] is True

        bpy.context.window = SimpleNamespace(modal_operators=[
            SimpleNamespace(bl_idname="modal_%d" % index) for index in range(12)
        ])
        capped = tracker.snapshot(now=21.0, include_current_command=False)
        assert len(capped["modal_operators"]) == activity.MAX_MODAL_OPERATORS == 8
    finally:
        _reset_scene()


def test_tick_is_throttled_unless_forced():
    _reset_scene()
    tracker = _tracker()
    first = tracker.tick(now=100.0)
    assert first["count"] == 0
    stale = tracker.tick(now=100.1)
    assert stale is tracker._selection

    bpy.context.selected_objects = [SimpleNamespace(name="Cube")]
    refreshed = tracker.tick(now=100.1, force=True)
    assert refreshed["count"] == 1
    assert refreshed["names"] == ["Cube"]


def test_selection_change_is_attributed_to_the_human():
    _reset_scene()
    tracker = _tracker()
    tracker.tick(now=1.0, force=True)
    bpy.context.selected_objects = [SimpleNamespace(name="Cube")]
    tracker.tick(now=2.0, force=True)
    assert tracker._last_human_kind == "selection"
    assert tracker.human_active(now=2.1) is True


def test_selection_change_after_agent_command_is_not_human():
    _reset_scene()
    tracker = _tracker()
    tracker.tick(now=1.0, force=True)
    tracker.note_agent_end(now=1.1)
    bpy.context.selected_objects = [SimpleNamespace(name="Cube")]
    tracker.tick(now=1.2, force=True)
    assert tracker._last_human_kind is None


def test_selection_snapshot_caps_names_and_marks_truncation():
    _reset_scene()
    objects = [SimpleNamespace(name="Obj%03d" % index) for index in range(20)]
    bpy.context.selected_objects = objects
    tracker = _tracker()
    snapshot = tracker.snapshot(now=3.0, include_current_command=False)
    selection = snapshot["selection"]
    assert selection["count"] == 20
    assert len(selection["names"]) == activity.SELECTION_ACTIVITY_NAMES == 8
    assert selection["truncated"] is True
    assert snapshot["mode"] == "OBJECT"


def test_activity_handlers_register_once_and_unregister_cleanly():
    for handler_list in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post,
                         bpy.app.handlers.depsgraph_update_post):
        handler_list[:] = []
    try:
        activity._register_activity_handlers()
        activity._register_activity_handlers()
        assert bpy.app.handlers.undo_post == [activity._dshbb_activity_undo_post]
        assert bpy.app.handlers.redo_post == [activity._dshbb_activity_redo_post]
        assert bpy.app.handlers.depsgraph_update_post == [activity._dshbb_activity_depsgraph_post]
        activity._unregister_activity_handlers()
        activity._unregister_activity_handlers()
        assert bpy.app.handlers.undo_post == []
        assert bpy.app.handlers.redo_post == []
        assert bpy.app.handlers.depsgraph_update_post == []
    finally:
        for handler_list in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post,
                             bpy.app.handlers.depsgraph_update_post):
            handler_list[:] = []


def test_undo_and_redo_handlers_note_the_human():
    _reset_scene()
    tracker = activity.get_activity_tracker()
    previous_kind = tracker._last_human_kind
    try:
        activity._dshbb_activity_undo_post(None)
        assert tracker._last_human_kind == "undo"
        activity._dshbb_activity_redo_post(None)
        assert tracker._last_human_kind == "redo"
    finally:
        tracker._last_human_kind = previous_kind
