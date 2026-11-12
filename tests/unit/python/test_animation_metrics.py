# -*- coding: utf-8 -*-
"""animation metrics contract: root motion, joints, contacts, trajectory, intersections.

Every rate metric takes `dt` (sample spacing in seconds = frame_step / effective_fps),
never the frame rate, so a `frame_step>1` report is not scaled by the step.
"""
import pytest

DT24 = 1.0 / 24.0


@pytest.fixture(scope="module")
def A(booted_runtime):
    from runtime.py.animation.animation_impl import common, metrics

    class Mods:
        pass

    m = Mods()
    m.common = common
    m.metrics = metrics
    return m


def _err(fn, *args, **kwargs):
    with pytest.raises(Exception) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value


# ── root motion ──────────────────────────────────────────────────────────────

def test_translation_two_metres_along_x(A):
    positions = [(2.0 * i / 23.0, 0.0, 0.0) for i in range(24)]
    m = A.metrics.root_motion_metrics(positions, None, list(range(1, 25)), DT24)
    assert m["displacement_m"] == pytest.approx(2.0)
    assert m["path_length_m"] == pytest.approx(2.0)
    assert m["displacement_vector_m"] == pytest.approx([2.0, 0.0, 0.0], abs=1e-9)
    assert m["loop_closure_m"] == pytest.approx(2.0)
    assert m["max_speed_m_s"] == pytest.approx(2.0 * 24.0 / 23.0)
    assert m["max_accel_m_s2"] == pytest.approx(0.0, abs=1e-9)
    assert m["rotation_delta_deg"] is None
    assert m["rotation_path_deg"] is None


def test_frame_step_two_reports_true_speed_and_acceleration(A):
    # 2 m over 24 frames at 24 fps, sampled every other frame: dt = 2/24 s, so the
    # true speed is 2 m/s. Dividing by 1/fps instead reported 4 m/s.
    dt = 2.0 / 24.0
    positions = [(2.0 * i / 12.0, 0.0, 0.0) for i in range(13)]
    frames = [1 + 2 * i for i in range(13)]
    m = A.metrics.root_motion_metrics(positions, None, frames, dt)
    assert m["displacement_m"] == pytest.approx(2.0)
    assert m["path_length_m"] == pytest.approx(2.0)
    assert m["max_speed_m_s"] == pytest.approx(2.0)
    assert m["max_accel_m_s2"] == pytest.approx(0.0, abs=1e-9)


def test_frame_step_two_does_not_square_acceleration(A):
    # x = 0.5 * a * t^2 with a = 1 m/s^2: finite differences give exactly a per sample,
    # so dt = 2/24 must still report 1 m/s^2 (the 1/fps form reported 4 m/s^2).
    dt = 2.0 / 24.0
    positions = []
    for i in range(6):
        t = i * dt
        positions.append((0.5 * t * t, 0.0, 0.0))
    m = A.metrics.root_motion_metrics(positions, None, [1 + 2 * i for i in range(6)], dt)
    assert m["max_accel_m_s2"] == pytest.approx(1.0)


def test_insufficient_samples_return_null_not_zero(A):
    m = A.metrics.root_motion_metrics([(0.0, 0.0, 0.0)], None, [1], DT24)
    assert m["max_speed_m_s"] is None
    assert m["max_accel_m_s2"] is None
    assert m["rotation_delta_deg"] is None
    assert m["rotation_path_deg"] is None
    assert m["path_length_m"] == pytest.approx(0.0)


def test_rotation_delta_is_shortest_arc_and_path_accumulates(A):
    c = A.common
    quats = [c.mat3_to_quat(c.mat3(c.mat_rot_z(deg))) for deg in (0.0, 90.0, 180.0, 270.0)]
    m = A.metrics.root_motion_metrics([(0, 0, 0)] * 4, quats, [1, 2, 3, 4], DT24)
    assert m["rotation_path_deg"] == pytest.approx(270.0, abs=1e-6)
    assert m["rotation_delta_deg"] == pytest.approx(90.0, abs=1e-6)
    assert any(w["code"] == "rotation_shortest_arc" for w in m["warnings"])


# ── joints ───────────────────────────────────────────────────────────────────

def test_parent_rotation_does_not_become_child_joint_angle(A):
    c = A.common
    ident = c.mat_identity()
    rot90 = c.mat_rot_z(90.0)
    poses = {"root": [rot90], "child": [rot90]}
    rests = {"root": [ident], "child": [ident]}
    rows = A.metrics.joint_metrics([["root", "child"]], poses, rests, [1], DT24)
    by_name = {r["name"]: r for r in rows}
    assert by_name["root"]["max_deg"] == pytest.approx(90.0, abs=1e-6)
    assert by_name["child"]["max_deg"] == pytest.approx(0.0, abs=1e-6)
    assert by_name["child"]["parent"] == "root"


def test_chained_rest_matrices_divide_the_parent_rest_once(A):
    c = A.common
    parent_local = c.mat_rot_z(30.0)
    child_local = c.mat_rot_z(50.0)
    rests = A.metrics.chained_rest_matrices([["root", "child"]], {"root": parent_local, "child": child_local})
    # Chain root keeps its armature-space rest; the child becomes parent-relative (50 - 30).
    assert c.quat_angle_deg(c.mat3_to_quat(c.mat3(rests["root"]))) == pytest.approx(30.0, abs=1e-6)
    assert c.quat_angle_deg(c.mat3_to_quat(c.mat3(rests["child"]))) == pytest.approx(20.0, abs=1e-6)
    # A child carried rigidly by its parent rotates by the parent amount, so its local
    # joint angle is 0 - but only with the parent rest divided out.
    poses = {"root": [parent_local], "child": [child_local]}
    rows = {r["name"]: r for r in A.metrics.joint_metrics([["root", "child"]], poses, rests, [1], DT24)}
    assert rows["child"]["max_deg"] == pytest.approx(0.0, abs=1e-6)


def test_skipping_the_parent_rest_division_would_report_a_fake_angle(A):
    # Counter-assertion for the test above: with armature-space rests (no
    # mat_inverse(parent_local)) the same rigid pose reads a non-zero local angle.
    c = A.common
    parent_local = c.mat_rot_z(30.0)
    child_local = c.mat_rot_z(50.0)
    poses = {"root": [parent_local], "child": [child_local]}
    armature_space = {"root": parent_local, "child": child_local}
    rows = {r["name"]: r for r in A.metrics.joint_metrics([["root", "child"]], poses, armature_space, [1], DT24)}
    assert rows["child"]["max_deg"] == pytest.approx(30.0, abs=1e-6)


def test_chain_parent_mismatches_report_non_parent_chains(A):
    chains = [["root", "child"], ["root", "tail"], ["a", "b"]]
    parents = {"child": "root", "tail": "spine", "b": "a"}
    assert A.metrics.chain_parent_mismatches(chains, parents) == [("tail", "root", "spine")]


def test_joint_metrics_report_min_max_mean_and_rates(A):
    c = A.common
    ident = c.mat_identity()
    poses = {"arm": [c.mat_rot_z(10.0), c.mat_rot_z(20.0), c.mat_rot_z(40.0)]}
    rests = {"arm": [ident]}
    rows = A.metrics.joint_metrics([["arm"]], poses, rests, [1, 2, 3], DT24)
    row = rows[0]
    assert row["min_deg"] == pytest.approx(10.0, abs=1e-6)
    assert row["max_deg"] == pytest.approx(40.0, abs=1e-6)
    assert row["mean_deg"] == pytest.approx(70.0 / 3.0, abs=1e-6)
    assert row["max_angular_speed_deg_s"] == pytest.approx(20.0 * 24.0, abs=1e-6)
    assert row["max_angular_accel_deg_s2"] == pytest.approx(240.0 * 24.0, abs=1e-6)


def test_joint_angular_rate_uses_sample_dt(A):
    c = A.common
    poses = {"arm": [c.mat_rot_z(10.0), c.mat_rot_z(20.0)]}
    rows = A.metrics.joint_metrics([["arm"]], poses, {"arm": [c.mat_identity()]}, [1, 3], 2.0 / 24.0)
    assert rows[0]["max_angular_speed_deg_s"] == pytest.approx(10.0 / (2.0 / 24.0), abs=1e-6)


def test_joint_metrics_accepts_rest_pose(A):
    c = A.common
    rot90 = c.mat_rot_z(90.0)
    rows = A.metrics.joint_metrics([["arm"]], {"arm": [rot90]}, {"arm": [rot90]}, [1], DT24)
    assert rows[0]["max_deg"] == pytest.approx(0.0, abs=1e-6)


# ── contacts ─────────────────────────────────────────────────────────────────

def test_contact_intervals_signed_penetration_and_slide(A):
    frames = [1, 2, 3, 4]
    points = {"foot": [(0.0, 0, -0.005), (0.1, 0, -0.012), (0.4, 0, 0.5), (0.6, 0, 0.2)]}
    rows = A.metrics.contact_intervals(points, frames, DT24, 0.0, 0.01)
    row = rows[0]
    assert row["bone"] == "foot"
    assert row["contact_samples"] == 2
    assert len(row["intervals"]) == 1
    iv = row["intervals"][0]
    assert (iv["start_frame"], iv["end_frame"]) == (1, 2)
    assert iv["contact_samples"] == 2
    assert iv["horizontal_slide_m"] == pytest.approx(0.1)
    assert iv["max_slide_speed_m_s"] == pytest.approx(0.1 * 24.0)
    assert iv["max_penetration_m"] == pytest.approx(0.012)


def test_contact_slide_speed_uses_sample_dt(A):
    points = {"foot": [(0.0, 0, 0.0), (0.1, 0, 0.0)]}
    rows = A.metrics.contact_intervals(points, [1, 3], 2.0 / 24.0, 0.0, 0.01)
    assert rows[0]["intervals"][0]["max_slide_speed_m_s"] == pytest.approx(0.1 / (2.0 / 24.0))


def test_no_contact_is_not_an_error(A):
    rows = A.metrics.contact_intervals({"foot": [(0, 0, 1.0)]}, [1], DT24, 0.0, 0.01)
    assert rows[0]["contact_samples"] == 0
    assert rows[0]["intervals"] == []


def test_contact_at_threshold_counts_and_step_is_approximate(A):
    rows = A.metrics.contact_intervals({"foot": [(0, 0, 0.01)]}, [1], DT24, 0.0, 0.01)
    assert rows[0]["contact_samples"] == 1


def test_contact_truncation_recomputes_row_stats(A):
    points = {"foot": [(0, 0, 0.0), (0.1, 0, -0.01), (0.2, 0, 1.0),
                       (0.3, 0, -0.05), (0.4, 0, -0.05)]}
    rows = A.metrics.contact_intervals(points, [1, 2, 3, 4, 5], DT24, 0.0, 0.01)
    assert len(rows[0]["intervals"]) == 2
    assert rows[0]["contact_samples"] == 4
    assert rows[0]["max_penetration_m"] == pytest.approx(0.05)
    kept, total = A.metrics.truncate_contact_intervals(rows, 1)
    assert total == 2
    assert len(kept[0]["intervals"]) == 1
    assert kept[0]["intervals_total"] == 2
    assert kept[0]["truncated"] is True
    # Recomputed from the kept interval only: the dropped interval held the deep contact.
    assert kept[0]["contact_samples"] == 2
    assert kept[0]["max_penetration_m"] == pytest.approx(0.01)


# ── trajectory ───────────────────────────────────────────────────────────────

def test_trajectory_matches_exact_frames_and_counts_missing_and_unused(A):
    import runtime.py.animation.animation_impl.plan as plan
    ref = plan.validate_reference({"version": 1, "fps": 24.0, "frames": [
        {"frame": 1, "objects": {"Cube": [0.0, 0.0, 0.0]}},
        {"frame": 2, "objects": {"Cube": [0.1, 0.0, 0.0]}},
        {"frame": 3, "objects": {"Cube": [0.2, 0.0, 0.0]}},
    ]}, 24.0)
    samples = {"Cube": {1: (0.0, 0.0, 0.0), 2: (0.1, 0.0, 0.0), 4: (0.3, 0.0, 0.0)}}
    rows = A.metrics.trajectory_metrics(ref, samples, [1, 2, 4])
    row = rows["Cube"]
    assert row["matched"] == 2
    assert row["missing"] == 1
    assert row["unused"] == 1
    assert row["rmse_m"] == pytest.approx(0.0)
    assert row["max_deviation_m"] == pytest.approx(0.0)


def test_trajectory_deviation_is_rmse_not_zero_filled(A):
    import runtime.py.animation.animation_impl.plan as plan
    ref = plan.validate_reference({"version": 1, "fps": 24.0, "frames": [
        {"frame": 1, "objects": {"Cube": [0.1, 0.0, 0.0]}},
        {"frame": 2, "objects": {"Cube": [0.2, 0.0, 0.0]}},
    ]}, 24.0)
    samples = {"Cube": {1: (0.0, 0.0, 0.0), 2: (0.0, 0.0, 0.0)}}
    row = A.metrics.trajectory_metrics(ref, samples, [1, 2])["Cube"]
    assert row["rmse_m"] == pytest.approx((0.01 + 0.04) ** 0.5 / (2 ** 0.5), rel=1e-3)
    assert row["max_deviation_m"] == pytest.approx(0.2, rel=1e-3)


# ── intersections ────────────────────────────────────────────────────────────

def test_pair_selection_is_deterministic_and_truncated(A):
    pairs, total = A.metrics.select_intersection_pairs(["a", "b", "c"])
    assert pairs == [("a", "b"), ("a", "c"), ("b", "c")]
    assert total == 3
    many, many_total = A.metrics.select_intersection_pairs(["o%02d" % i for i in range(16)])
    assert len(many) == 64
    assert many_total == 120
    assert many[0] == ("o00", "o01")


def test_triangle_budget_is_per_frame_total(A):
    assert A.metrics.triangle_budget_ok([100000, 100000]) is True
    assert A.metrics.triangle_budget_ok([100000, 100001]) is False


def test_intersection_aggregation_marks_skipped_frames(A):
    pairs = [("a", "b"), ("a", "c"), ("b", "c")]
    frames = [1, 2, 3]
    out = A.metrics.aggregate_intersections([{0, 2}, set(), {0}], pairs, frames)
    assert out["status"] == "measured"
    assert out["skipped_frames"] == []
    assert out["pairs"][0]["overlap_frames"] == [1, 3]
    assert out["pairs"][1]["overlap_frames"] == []
    degraded = A.metrics.aggregate_intersections([{0}, None, {1}], pairs, frames)
    assert degraded["status"] == "degraded"
    assert degraded["skipped_frames"] == [2]
    skipped = A.metrics.aggregate_intersections([None, None, None], pairs, frames)
    assert skipped["status"] == "skipped"
    assert skipped["skipped_frames"] == [1, 2, 3]


def test_intersection_aggregation_reports_pairs_total(A):
    pairs = [("a", "b")]
    out = A.metrics.aggregate_intersections([{0}], pairs, [1], 120)
    assert out["pairs_total"] == 120
    assert out["pairs_truncated"] is True
    assert out["status"] == "measured"


# ── orthonormalization ───────────────────────────────────────────────────────

def test_orthonormalize_drops_scale_before_quaternion_extraction(A):
    c = A.common
    rot90 = c.mat_rot_z(90.0)
    scaled = ((2.0 * rot90[0][0], rot90[0][1], 0.0),
              (2.0 * rot90[1][0], rot90[1][1], 0.0),
              (0.0, 0.0, 1.0))
    clean = c.mat3_orthonormalize(scaled)
    angle_without = c.quat_angle_deg(c.mat3_to_quat(c.mat3(scaled)))
    angle_with = c.quat_angle_deg(c.mat3_to_quat(clean))
    assert angle_with == pytest.approx(90.0, abs=1e-6)
    # The un-orthonormalized matrix carries the scale into the quaternion and skews the angle.
    assert abs(angle_without - 90.0) > 1e-3
