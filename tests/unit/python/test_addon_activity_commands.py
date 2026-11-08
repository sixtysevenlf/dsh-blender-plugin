"""Unit checks for the activity command wrappers.

The real recorder/tracker is replaced by a double; these tests only assert the
command envelope, the paused flag and the consent gate.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

activity_commands = importlib.import_module("addon.dsh_blender_bridge.commands.activity")


def _install_doubles(snapshot=None, paused=False,
                     consent=True, events=None, raise_on=None):
    module = activity_commands
    original = (module.get_activity_tracker, module.ai_paused,
                module.get_edit_recorder)
    state = SimpleNamespace(drained=False)

    class _Tracker:
        def snapshot(self, include_current_command=True):  # noqa: ARG002
            if raise_on == "snapshot":
                raise RuntimeError("tracker exploded")
            return dict(snapshot or {"state": "idle"})

    class _Recorder:
        def drain(self):
            if raise_on == "drain":
                raise RuntimeError("drain exploded")
            state.drained = True
            return list(events or [])

    module.get_activity_tracker = lambda: _Tracker()
    module.ai_paused = lambda: paused
    module.get_edit_recorder = lambda: _Recorder()

    class _Commands(module.ActivityCommands):
        def get_telemetry_consent(self):
            return {"consent": consent}

    return _Commands, state, original


def test_get_activity_state_adds_paused_flag():
    commands, _state, original = _install_doubles(snapshot={"state": "idle"}, paused=True)
    try:
        result = commands().get_activity_state()
    finally:
        (activity_commands.get_activity_tracker, activity_commands.ai_paused,
         activity_commands.get_edit_recorder) = original
    assert result == {"state": "idle", "paused": True}


def test_get_activity_state_reports_errors():
    commands, _state, original = _install_doubles(raise_on="snapshot")
    try:
        result = commands().get_activity_state()
    finally:
        (activity_commands.get_activity_tracker, activity_commands.ai_paused,
         activity_commands.get_edit_recorder) = original
    assert result == {"error": "tracker exploded"}


def test_drain_without_consent_still_drains_and_returns_empty():
    commands, state, original = _install_doubles(consent=False, events=[{"x": 1}])
    try:
        result = commands().drain_human_activity()
    finally:
        (activity_commands.get_activity_tracker, activity_commands.ai_paused,
         activity_commands.get_edit_recorder) = original
    assert result == {"events": []}
    assert state.drained is True


def test_drain_with_consent_returns_events():
    commands, _state, original = _install_doubles(consent=True, events=[{"x": 1}])
    try:
        result = commands().drain_human_activity()
    finally:
        (activity_commands.get_activity_tracker, activity_commands.ai_paused,
         activity_commands.get_edit_recorder) = original
    assert result == {"events": [{"x": 1}]}


def test_drain_reports_errors():
    commands, _state, original = _install_doubles(consent=True, raise_on="drain")
    try:
        result = commands().drain_human_activity()
    finally:
        (activity_commands.get_activity_tracker, activity_commands.ai_paused,
         activity_commands.get_edit_recorder) = original
    assert result == {"error": "drain exploded"}
