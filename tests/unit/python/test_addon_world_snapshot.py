"""Unit checks for world-state animation snapshots across the Action API split.

Blender 4.4 made actions layered and slotted, and 5.2 removed the
``Action.fcurves`` shortcut. The snapshot used to read ``action.fcurves`` and
the surrounding ``except Exception`` swallowed the resulting AttributeError, so
animation metadata disappeared silently instead of failing. These tests pin
both API generations, the per-object slot scope, and the de-duplication rule.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

import bpy_stub

bpy = bpy_stub.install()

world = importlib.import_module("addon.dsh_blender_bridge.commands.scene.world")


class _Keyframe:
    def __init__(self, frame):
        self.co = SimpleNamespace(x=frame)


class _FCurve:
    """F-curve double whose identity is a pointer, not a data path."""

    def __init__(self, data_path, array_index, frames, pointer):
        self.data_path = data_path
        self.array_index = array_index
        self.keyframe_points = [_Keyframe(frame) for frame in frames]
        self._pointer = pointer

    def as_pointer(self):
        return self._pointer


class _Slot:
    """Action-slot double; hashable by identity, the way a bpy struct is."""

    def __init__(self, name):
        self.name = name


class _Channelbag:
    def __init__(self, fcurves=None):
        self.fcurves = list(fcurves or [])


class _Strip:
    def __init__(self, channelbags):
        self._channelbags = channelbags

    def channelbag(self, slot):
        return self._channelbags.get(slot)


class _Layer:
    def __init__(self, strips):
        self.strips = strips


class _AnimUtils:
    """Double for ``bpy_extras.anim_utils`` (Blender 4.4+)."""

    def __init__(self, explode=False):
        self.explode = explode
        self.calls = []

    def action_get_channelbag_for_slot(self, action, slot):
        self.calls.append(slot)
        if self.explode:
            raise RuntimeError("anim_utils exploded")
        return action.channelbags.get(slot)


class _LayeredAction:
    """Layered action double: layers -> strips -> channelbag(slot).

    It deliberately has no ``fcurves`` attribute, which is the 5.x shape.
    """

    def __init__(self, *slot_names):
        self.name = "Anim"
        self.slots = [_Slot(name) for name in slot_names]
        self.channelbags = {slot: _Channelbag() for slot in self.slots}
        self.layers = [_Layer([_Strip(self.channelbags)])]

    def slot(self, name):
        return next(slot for slot in self.slots if slot.name == name)

    def curves(self, slot_name, *fcurves):
        slot = self.slot(slot_name)
        self.channelbags[slot].fcurves.extend(fcurves)
        return slot


def _legacy_action(*fcurves):
    """Pre-4.4 action double: a flat ``fcurves`` collection, no layers."""
    return SimpleNamespace(name="Legacy", fcurves=list(fcurves))


def _object_with(action, slot):
    anim_data = SimpleNamespace(action=action, action_slot=slot, drivers=[], nla_tracks=[])
    return SimpleNamespace(animation_data=anim_data)


@pytest.fixture
def anim_utils(monkeypatch):
    double = _AnimUtils()
    monkeypatch.setattr(world, "_ANIM_UTILS", double)
    monkeypatch.setattr(world, "_ANIM_UTILS_PROBED", True)
    return double


def test_action_fcurves_uses_the_assigned_slot(anim_utils):
    action = _LayeredAction("OBHand", "OBCtrl")
    own = action.curves("OBHand", _FCurve("rotation_euler", 0, [1, 2], 11))
    action.curves("OBCtrl", _FCurve("location", 0, [1, 2], 22))

    assert [fcurve.as_pointer() for fcurve in world.action_fcurves(action, own)] == [11]
    assert [slot.name for slot in anim_utils.calls] == ["OBHand"]


def test_action_fcurves_without_slot_unions_slots_and_dedupes_shared_curves(anim_utils):
    action = _LayeredAction("A", "B")
    shared = _FCurve("location", 0, [1], 7)
    action.curves("A", shared, _FCurve("location", 1, [1], 8))
    action.curves("B", shared, _FCurve("rotation_euler", 0, [1], 9))

    assert [fcurve.as_pointer() for fcurve in world.action_fcurves(action)] == [7, 8, 9]


def test_action_fcurves_keeps_equal_paths_from_different_slots(anim_utils):
    """Two objects sharing one action: same path, different data, both kept."""
    path = 'pose.bones["hand"].rotation_euler'
    action = _LayeredAction("OBLeft", "OBRight")
    left = action.curves("OBLeft", _FCurve(path, 0, [1], 101))
    action.curves("OBRight", _FCurve(path, 0, [1, 2], 202))

    assert len(world.action_fcurves(action)) == 2
    assert [fcurve.as_pointer() for fcurve in world.action_fcurves(action, left)] == [101]


def test_action_fcurves_walks_layers_when_anim_utils_is_missing(monkeypatch):
    monkeypatch.setattr(world, "_ANIM_UTILS", None)
    monkeypatch.setattr(world, "_ANIM_UTILS_PROBED", True)
    action = _LayeredAction("OnlySlot")
    slot = action.curves("OnlySlot", _FCurve("location", 0, [1], 5))

    assert [fcurve.as_pointer() for fcurve in world.action_fcurves(action, slot)] == [5]


def test_action_fcurves_walks_layers_when_anim_utils_raises(monkeypatch):
    monkeypatch.setattr(world, "_ANIM_UTILS", _AnimUtils(explode=True))
    monkeypatch.setattr(world, "_ANIM_UTILS_PROBED", True)
    action = _LayeredAction("OnlySlot")
    slot = action.curves("OnlySlot", _FCurve("location", 0, [1], 6))

    assert [fcurve.as_pointer() for fcurve in world.action_fcurves(action, slot)] == [6]


def test_action_fcurves_returns_empty_for_a_slot_the_action_does_not_hold(anim_utils):
    action = _LayeredAction("A")

    assert world.action_fcurves(action, _Slot("ghost")) == []


def test_action_fcurves_reads_legacy_actions_without_layers(monkeypatch):
    monkeypatch.setattr(world, "_ANIM_UTILS", None)
    monkeypatch.setattr(world, "_ANIM_UTILS_PROBED", True)
    legacy = _legacy_action(_FCurve("location", 0, [1], 31))

    assert world.action_fcurves(legacy) == legacy.fcurves


def test_snapshot_animation_reads_layered_action(anim_utils):
    """The 5.x regression: no AttributeError, real channel data in the snapshot."""
    action = _LayeredAction("OBCube")
    slot = action.curves(
        "OBCube",
        _FCurve("location", 0, [1.0, 21.0], 1),
        _FCurve("location", 1, [1.0, 11.0, 21.0], 2),
    )

    animation = world.WorldSnapshotCommands._snapshot_animation(_object_with(action, slot))["animation"]

    assert animation["action"] == "Anim"
    assert animation["keyframe_count"] == 5
    assert animation["frame_range"] == [1.0, 21.0]
    assert [channel["array_index"] for channel in animation["channels"]] == [0, 1]


def test_snapshot_animation_scope_is_the_assigned_slot(anim_utils):
    action = _LayeredAction("OBCube", "OBCube.001")
    own = action.curves("OBCube", _FCurve("location", 0, [1.0, 2.0], 1))
    action.curves("OBCube.001", _FCurve("location", 0, [5.0], 2))

    animation = world.WorldSnapshotCommands._snapshot_animation(_object_with(action, own))["animation"]

    assert animation["keyframe_count"] == 2
    assert animation["frame_range"] == [1.0, 2.0]


def test_snapshot_animation_still_reads_legacy_actions(monkeypatch):
    monkeypatch.setattr(world, "_ANIM_UTILS", None)
    monkeypatch.setattr(world, "_ANIM_UTILS_PROBED", True)
    legacy = _legacy_action(_FCurve("location", 0, [1.0, 3.0], 1))

    animation = world.WorldSnapshotCommands._snapshot_animation(_object_with(legacy, None))["animation"]

    assert animation["keyframe_count"] == 2
    assert animation["frame_range"] == [1.0, 3.0]
