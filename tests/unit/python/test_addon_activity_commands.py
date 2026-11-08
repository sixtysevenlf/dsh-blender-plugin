"""Unit checks for the activity command wrapper.

The real tracker is replaced by a double; these tests only assert the command
envelope and the paused flag. There is no telemetry consent or drain path.
"""

from __future__ import annotations

import importlib

import bpy_stub

bpy = bpy_stub.install()

activity_commands = importlib.import_module("extension.dsh_blender_bridge.commands.activity")


def _install_doubles(snapshot=None, paused=False, raise_on=None):
    module = activity_commands
    original = (module.get_activity_tracker, module.ai_paused)

    class _Tracker:
        def snapshot(self, include_current_command=True):  # noqa: ARG002
            if raise_on == "snapshot":
                raise RuntimeError("tracker exploded")
            return dict(snapshot or {"state": "idle"})

    module.get_activity_tracker = lambda: _Tracker()
    module.ai_paused = lambda: paused
    return module.ActivityCommands, original


def _restore(original):
    activity_commands.get_activity_tracker, activity_commands.ai_paused = original


def test_get_activity_state_adds_paused_flag():
    commands, original = _install_doubles(snapshot={"state": "idle"}, paused=True)
    try:
        result = commands().get_activity_state()
    finally:
        _restore(original)
    assert result == {"state": "idle", "paused": True}


def test_get_activity_state_reports_errors():
    commands, original = _install_doubles(raise_on="snapshot")
    try:
        result = commands().get_activity_state()
    finally:
        _restore(original)
    assert result == {"error": "tracker exploded"}


def test_drain_human_activity_is_gone():
    assert not hasattr(activity_commands.ActivityCommands, "drain_human_activity")
    assert not hasattr(activity_commands.ActivityCommands, "get_telemetry_consent")
