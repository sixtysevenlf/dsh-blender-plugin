# -*- coding: utf-8 -*-
"""animation dispatch contract: registration, structured errors, help surface.

These checks use the real kernel kit against the bpy stub; they never claim
Blender behaviour. Real scene sampling is covered by tests/e2e/blender.
"""
import json

import pytest


@pytest.fixture(scope="module")
def api(booted_runtime):
    import runtime.py.animation as animation

    assert hasattr(animation, "animation")
    from runtime.py.animation.animation_impl import api as api_module

    assert hasattr(api_module, "ANIMATION_VERSION")
    kernel = __import__("sys").modules["dsh_rt_kernel"]
    return kernel.dsh_animation_api


def _call(api, op, args):
    return json.loads(api["dispatch"](op, json.dumps(args)))


def test_registers_report_selftest_help(api):
    for name in ("report", "selftest", "help"):
        assert callable(api[name]), name
    assert callable(api["dispatch"])
    assert api["version"] >= 1


def test_help_is_generic_and_contains_selection_methods(api):
    out = _call(api, "help", {})
    assert out["ok"] is True
    assert out["module"] == "animation"
    text = json.dumps(out).upper()
    for forbidden in ("RIGIFY", "MCH-", "DEF-", "ORG-", "CTRL-"):
        assert forbidden not in text
    blob = json.dumps(out)
    for key in ("effective_fps", "bone_chains", "bone_collections", "bone_filter", "contact_point"):
        assert key in blob, key


def test_report_validation_errors_are_structured(api):
    out = _call(api, "report", {"frame_start": 10, "frame_end": 5})
    assert out["ok"] is False
    assert out["code"] == "animation_input_invalid"
    assert out["stage"] == "input"
    assert out["root_cause"]
    assert out["fix"]
    assert out["module"] == "animation"


def test_report_missing_armature_is_input_invalid(api):
    out = _call(api, "report", {"objects": ["Cube"], "bones": ["Bone"]})
    assert out["ok"] is False
    assert out["code"] == "animation_input_invalid"


def test_report_requires_targets(api):
    out = _call(api, "report", {"frame_start": 1, "frame_end": 2})
    assert out["ok"] is False
    assert out["code"] == "animation_target_missing"


def test_report_intersection_objects_need_two(api):
    out = _call(api, "report", {"intersection_objects": ["A"]})
    assert out["ok"] is False
    assert out["code"] == "animation_input_invalid"


def test_unknown_op_is_reported(api):
    out = json.loads(api["dispatch"]("nope", "{}"))
    assert out["ok"] is False
    assert "unknown op" in out["error"]
