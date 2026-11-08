"""Human edit tracking: only human-originated depsgraph updates are queued."""

from __future__ import annotations

import importlib

import bpy_stub

bpy = bpy_stub.install()

activity = importlib.import_module("addon.dsh_blender_bridge.recorders.activity")
user_edit = importlib.import_module("addon.dsh_blender_bridge.recorders.user_edit")


class _Object(bpy.types.Object):
    pass




class _Collection(bpy.types.Collection):
    pass


class _Update:
    def __init__(self, idblock):
        self.id = idblock


class _Depsgraph:
    def __init__(self, *idblocks):
        self.updates = [_Update(idblock) for idblock in idblocks]


def _named(cls, name):
    block = cls()
    block.name = name
    return block


def test_records_human_object_and_collection_edits_with_revisions():
    tracker = activity.ActivityTracker()
    tracker.note_depsgraph(_Depsgraph(_named(_Object, "Cube"), _named(_Collection, "COL_A")))
    edits = tracker.drain_human_edits()
    assert [(edit["kind"], edit["name"]) for edit in edits] == [
        ("object", "Cube"),
        ("collection", "COL_A"),
    ]
    assert edits[0]["revision"] < edits[1]["revision"]
    assert tracker.drain_human_edits() == []


def test_agent_commands_do_not_count_as_human_edits():
    tracker = activity.ActivityTracker()
    tracker.attach(user_edit.get_edit_recorder())
    with user_edit.get_edit_recorder().agent_command():
        tracker.note_depsgraph(_Depsgraph(_named(_Object, "Cube")))
    assert tracker.drain_human_edits() == []


def test_unknown_id_blocks_are_ignored():
    tracker = activity.ActivityTracker()
    tracker.note_depsgraph(_Depsgraph(_named(bpy.types.Scene, "Scene")))
    assert tracker.drain_human_edits() == []
