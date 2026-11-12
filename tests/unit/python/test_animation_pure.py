# -*- coding: utf-8 -*-
"""animation pure-logic contract: frame window, effective fps, reference validation, metrics.

No real Blender is needed here: the pure helpers operate on plain tuples so the
behaviour is testable against the bpy stub, and the Blender adapter in
report.py/help.py is only exercised by the native E2E layer.
"""
import pytest


@pytest.fixture(scope="module")
def A(booted_runtime):
    from runtime.py.animation.animation_impl import common, metrics, plan

    class Mods:
        pass

    m = Mods()
    m.common = common
    m.plan = plan
    m.metrics = metrics
    return m


def _err(fn, *args, **kwargs):
    with pytest.raises(Exception) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value


# ── effective fps ────────────────────────────────────────────────────────────

def test_effective_fps_divides_by_base(A):
    assert A.plan.effective_fps(24, 1.001) == pytest.approx(23.976023976023978)
    assert A.plan.effective_fps(30, 1.0) == 30.0


@pytest.mark.parametrize("fps,base", [(0, 1), (24, 0), (-24, 1), (float("inf"), 1), (24, float("nan"))])
def test_effective_fps_rejects_non_positive_or_non_finite(A, fps, base):
    err = _err(A.plan.effective_fps, fps, base)
    assert err.code == "animation_input_invalid"
    assert err.stage == "input"


# ── frame window ─────────────────────────────────────────────────────────────

def test_scene_window_without_step_picks_smallest_step_under_cap(A):
    w = A.plan.select_frame_window(1, 480)
    assert w["frame_step"] == 2
    assert w["sample_count"] == 240
    assert w["last_sample_frame"] == 479
    assert w["frames"][0] == 1
    assert w["frames"][-1] == 479
    assert w["requested_frame_start"] == 1
    assert w["requested_frame_end"] == 480


def test_scene_window_is_closed_interval_and_exact_at_cap(A):
    w = A.plan.select_frame_window(1, 240)
    assert w["frame_step"] == 1
    assert w["sample_count"] == 240
    assert w["last_sample_frame"] == 240
    w2 = A.plan.select_frame_window(1, 100)
    assert w2["sample_count"] == 100
    assert w2["frame_step"] == 1


def test_explicit_step_within_budget(A):
    w = A.plan.select_frame_window(1, 480, frame_start=10, frame_end=20, frame_step=3)
    assert w["frames"] == [10, 13, 16, 19]
    assert w["last_sample_frame"] == 19
    assert w["sample_count"] == 4


def test_explicit_step_over_cap_is_an_error_not_silently_changed(A):
    err = _err(A.plan.select_frame_window, 1, 480, frame_step=1)
    assert err.code == "animation_sampling_invalid"
    assert err.stage == "input"
    assert "240" in err.root_cause


def test_frame_start_after_end_is_input_invalid(A):
    err = _err(A.plan.select_frame_window, 1, 10, frame_start=20, frame_end=5)
    assert err.code == "animation_input_invalid"


@pytest.mark.parametrize("step", [0, -1])
def test_non_positive_step_is_input_invalid(A, step):
    err = _err(A.plan.select_frame_window, 1, 10, frame_step=step)
    assert err.code == "animation_input_invalid"


# ── static argument validation ───────────────────────────────────────────────

def test_bones_require_armature(A):
    err = _err(A.plan.validate_report_args, {"objects": ["Cube"], "bones": ["Bone"]})
    assert err.code == "animation_input_invalid"
    assert "armature" in err.root_cause


def test_bone_chains_root_bone_and_collections_require_armature(A):
    for extra in ({"bone_chains": [["a", "b"]]}, {"root_bone": "root"}, {"bone_collections": ["c"]},
                  {"contact_bones": ["foot"]}):
        err = _err(A.plan.validate_report_args, dict({"objects": ["Cube"]}, **extra))
        assert err.code == "animation_input_invalid"


def test_intersection_objects_need_at_least_two(A):
    err = _err(A.plan.validate_report_args, {"intersection_objects": ["only"]})
    assert err.code == "animation_input_invalid"


@pytest.mark.parametrize("patch", [
    {"bone_filter": "control"}, {"contact_point": "tip"}, {"contact_epsilon": 0},
    {"contact_epsilon": -0.1}, {"ground_z": float("inf")}, {"ground_z": float("nan")},
    {"frame_start": 10, "frame_end": 5}, {"frame_step": 0},
])
def test_invalid_argument_combinations_are_input_invalid(A, patch):
    err = _err(A.plan.validate_report_args, patch)
    assert err.code == "animation_input_invalid"
    assert err.stage == "input"


def test_missing_targets_is_target_missing(A):
    err = _err(A.plan.validate_report_args, {"frame_start": 1, "frame_end": 2})
    assert err.code == "animation_target_missing"


def test_truncates_to_declared_limits_with_warnings(A):
    plan = A.plan.validate_report_args({"objects": ["o%d" % i for i in range(20)]})
    assert len(plan["objects"]) == 16
    assert plan["truncated"] is True
    assert any(w["code"] == "truncated" for w in plan["warnings"])


def test_defaults(A):
    plan = A.plan.validate_report_args({"objects": ["Cube"]})
    assert plan["bone_filter"] == "deform"
    assert plan["contact_point"] == "tail"
    assert plan["ground_z"] == 0.0
    assert plan["contact_epsilon"] == 0.01
    assert plan["include_bone_inventory"] is False


# ── reference validation ─────────────────────────────────────────────────────

def _ref(**kw):
    base = {"version": 1, "fps": 24.0, "frames": [
        {"frame": 1, "objects": {"Cube": [0.0, 0.0, 0.0]}},
        {"frame": 2, "objects": {"Cube": [0.1, 0.0, 0.0]}},
    ]}
    base.update(kw)
    return base


def test_valid_reference_is_indexed_by_exact_frame(A):
    got = A.plan.validate_reference(_ref(), 24.0)
    assert got[1]["Cube"] == (0.0, 0.0, 0.0)
    assert got[2]["Cube"] == (0.1, 0.0, 0.0)


def test_accepted_reference_adds_no_constant_warning(A):
    # A structurally valid reference is not evidence of anything yet, so validation must
    # not inject a constant warning into every reference report.
    plan = A.plan.validate_report_args({"objects": ["Cube"], "reference": _ref()})
    assert plan["reference"] is not None
    assert plan["reference_fps"] == 24.0
    assert not [w for w in plan["warnings"] if w["code"] == "reference_validated"]


def test_reference_fps_mismatch_is_reference_invalid(A):
    err = _err(A.plan.validate_reference, _ref(fps=25.0), 24.0)
    assert err.code == "animation_reference_invalid"


def test_reference_duplicate_frames_invalid(A):
    ref = _ref(frames=[
        {"frame": 1, "objects": {"Cube": [0, 0, 0]}},
        {"frame": 1, "objects": {"Cube": [0.1, 0, 0]}},
    ])
    err = _err(A.plan.validate_reference, ref, 24.0)
    assert err.code == "animation_reference_invalid"


@pytest.mark.parametrize("bad", ["nan", "inf"])
def test_reference_non_finite_coordinate_invalid(A, bad):
    ref = _ref(frames=[{"frame": 1, "objects": {"Cube": [float(bad), 0, 0]}}])
    err = _err(A.plan.validate_reference, ref, 24.0)
    assert err.code == "animation_reference_invalid"


def test_reference_version_must_be_one(A):
    err = _err(A.plan.validate_reference, _ref(version=2), 24.0)
    assert err.code == "animation_reference_invalid"


def test_reference_frame_numbers_must_be_integers(A):
    err = _err(A.plan.validate_reference, _ref(frames=[{"frame": 1.5, "objects": {}}]), 24.0)
    assert err.code == "animation_reference_invalid"


# ── small math helpers ───────────────────────────────────────────────────────

def test_matrix_inverse_and_translation_roundtrip(A):
    c = A.common
    m = c.mat_mul(c.mat_translation(1.0, 2.0, 3.0), c.mat_rot_z(30.0))
    inv = c.mat_inverse(m)
    ident = c.mat_mul(m, inv)
    for r in range(4):
        for col in range(4):
            assert ident[r][col] == pytest.approx(1.0 if r == col else 0.0, abs=1e-9)


def test_quaternion_angle_of_90_degree_rotation(A):
    c = A.common
    q = c.mat3_to_quat(c.mat3(c.mat_rot_z(90.0)))
    assert c.quat_angle_deg(q) == pytest.approx(90.0, abs=1e-6)
    q0 = c.mat3_to_quat(c.mat3(c.mat_rot_z(0)))
    assert c.quat_angle_deg(c.quat_delta_shortest(q0, q)) == pytest.approx(90.0)
