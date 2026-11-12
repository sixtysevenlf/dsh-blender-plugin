# -*- coding: utf-8 -*-
"""animation metrics: pure functions over sampled positions/poses.

Inputs are plain tuples produced by report.py's bpy adapter:
positions are world-space (x, y, z) tuples, rotations are normalized (w, x, y, z)
quaternions, pose matrices are 4x4 row-major tuples. Output keys are the ones
documented in the change design; missing finite-difference samples are None,
never 0.
"""
import math

from . import common


def _dist(a, b):
    return common.vec_distance(a, b)


def root_motion_metrics(positions, quaternions, frames, dt):
    """Root-motion rows: path length, displacement (vector/scalar), speeds, rotation.

    `dt` is the wall-clock spacing between two *samples* in seconds, i.e.
    `frame_step / effective_fps` — never `1 / fps`. Sampling every other frame and
    still dividing by `1 / fps` reports a speed that is `frame_step` times too high
    (and an acceleration `frame_step ** 2` times too high).

    `loop_closure_m` and `displacement_m` are the same quantity (start-to-end
    distance); both keys stay for compatibility.
    """
    warnings = []
    dt = float(dt)
    pts = [tuple(float(v) for v in p) for p in (positions or [])]
    qs = list(quaternions or [])
    n = len(pts)
    path = sum(_dist(pts[i], pts[i + 1]) for i in range(n - 1)) if n >= 2 else 0.0
    disp_vec = (0.0, 0.0, 0.0)
    disp = 0.0
    loop = 0.0
    max_speed = None
    max_accel = None
    if n >= 2:
        disp_vec = common.vec_sub(pts[-1], pts[0])
        disp = common.vec_norm(disp_vec)
        loop = _dist(pts[0], pts[-1])
        velocities = [common.vec_scale(common.vec_sub(pts[i + 1], pts[i]), 1.0 / dt) for i in range(n - 1)]
        max_speed = max(common.vec_norm(v) for v in velocities)
        if len(velocities) >= 2:
            accels = [common.vec_norm(common.vec_sub(velocities[i + 1], velocities[i])) / dt
                      for i in range(len(velocities) - 1)]
            max_accel = max(accels)
    rotation_delta = None
    rotation_path = None
    if qs and n >= 2 and len(qs) >= 2:
        norm = [common.quat_normalize(q) for q in qs[:n]]
        steps = [common.quat_angle_deg(common.quat_delta_shortest(norm[i], norm[i + 1])) for i in range(len(norm) - 1)]
        rotation_path = float(sum(steps))
        rotation_delta = common.quat_angle_deg(common.quat_delta_shortest(norm[0], norm[-1]))
        if rotation_path > 180.0 + 1e-9:
            warnings.append(common.warning(
                "rotation_shortest_arc",
                "旋转路径累计 %.3f°，delta 用最短弧压缩为 %.3f°" % (rotation_path, rotation_delta)))
    return {
        "path_length_m": float(path),
        "displacement_m": float(disp),
        "displacement_vector_m": [float(v) for v in disp_vec],
        "max_speed_m_s": None if max_speed is None else float(max_speed),
        "max_accel_m_s2": None if max_accel is None else float(max_accel),
        "loop_closure_m": float(loop),
        "rotation_delta_deg": rotation_delta,
        "rotation_path_deg": rotation_path,
        "warnings": warnings,
    }


def _is_matrix4(value):
    """A 4x4 matrix is a sequence of four rows of four numbers (not a list of matrices)."""
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(isinstance(row, (list, tuple)) and len(row) == 4 for row in value)
    )


def _rest(rests, name):
    value = rests.get(name)
    if not value:
        return common.mat_identity()
    if _is_matrix4(value):
        return value
    # Per-frame rest matrices: joint_metrics works from the bind pose, i.e. the first one.
    if isinstance(value, (list, tuple)) and value and _is_matrix4(value[0]):
        return value[0]
    return value


def chained_rest_matrices(chains, matrix_local):
    """Parent-relative rest matrices for explicit chains (pure, shared with report.py).

    `matrix_local` is `{bone: 4x4 armature-space rest matrix}` (Bone.matrix_local); the
    result is `parent_local ^ -1 @ bone_local`, with an identity parent for the first
    bone of a chain. Dividing by the parent rest exactly once is what makes a child that
    never rotates relative to its parent read ~0 degrees.
    """
    rests = {}
    for chain in chains or []:
        for idx, bone in enumerate(chain):
            parent = chain[idx - 1] if idx > 0 else None
            bone_local = matrix_local.get(bone) or common.mat_identity()
            parent_local = matrix_local.get(parent) if parent else None
            rests[bone] = common.mat_mul(common.mat_inverse(parent_local or common.mat_identity()), bone_local)
    return rests


def chain_parent_mismatches(chains, bone_parent):
    """(bone, chained parent, actual Bone.parent) rows where a chain is not parent-child."""
    rows = []
    for chain in chains or []:
        for idx in range(1, len(chain)):
            bone = chain[idx]
            chained = chain[idx - 1]
            actual = bone_parent.get(bone)
            if actual != chained:
                rows.append((bone, chained, actual))
    return rows


def joint_metrics(chains, poses, rests, frames, dt):
    """Per-bone local joint angles for explicit chains only.

    pose_local = parent_pose^-1 @ pose; rest_local is the bind pose already expressed
    relative to the previous bone of the same chain; delta = pose_local.to_3x3 @
    rest_local.to_3x3^-1 (orthonormalized); angle = delta quaternion angle.
    The first bone of a chain uses the identity parent transform; subsequent
    bones use the previous bone in the same chain (never Bone.parent).
    Angular speed/acceleration divide by `dt` (sample spacing in seconds), so a
    `frame_step>1` report is not scaled by the frame rate.
    """
    rows = []
    dt = float(dt)
    for chain in chains or []:
        if not chain:
            continue
        for idx, name in enumerate(chain):
            parent = chain[idx - 1] if idx > 0 else None
            pose_list = poses.get(name) or []
            # `rests[name]` is already parent-relative (the caller divides by the
            # previous bone's rest). Composing the parent's rest again squares that
            # rotation: a child that never rotates then reports a constant offset
            # equal to its rest angle (90° in the selftest rig).
            rest_local_matrix = common.mat3(_rest(rests, name))
            angles = []
            for i in range(len(pose_list)):
                parent_pose = poses.get(parent, [])[i] if parent and i < len(poses.get(parent, [])) else common.mat_identity()
                pose_local = common.mat_mul(common.mat_inverse(parent_pose), pose_list[i])
                delta = common.mat3_mul(common.mat3(pose_local), common.mat3_inverse(rest_local_matrix))
                angles.append(common.quat_angle_deg(common.mat3_to_quat(common.mat3_orthonormalize(delta))))
            if not angles:
                continue
            speeds = [abs(angles[i + 1] - angles[i]) / dt for i in range(len(angles) - 1)]
            accels = [abs(speeds[i + 1] - speeds[i]) / dt for i in range(len(speeds) - 1)]
            rows.append({
                "name": name,
                "parent": parent,
                "min_deg": float(min(angles)),
                "max_deg": float(max(angles)),
                "mean_deg": float(sum(angles) / len(angles)),
                "max_angular_speed_deg_s": float(max(speeds)) if speeds else None,
                "max_angular_accel_deg_s2": float(max(accels)) if accels else None,
                "sample_count": len(angles),
            })
    return rows


def contact_intervals(points_by_bone, frames, dt, ground_z, epsilon):
    """Signed ground contact intervals per bone; penetration counts as contact.

    Slide speed divides by `dt` (sample spacing in seconds) so `frame_step>1` reports
    the true sampled speed instead of `frame_step` times it.
    """
    dt = float(dt)
    rows = []
    for bone, points in (points_by_bone or {}).items():
        pts = [tuple(float(v) for v in p) for p in (points or [])]
        flags = [p[2] <= float(ground_z) + float(epsilon) for p in pts]
        intervals = []
        i = 0
        while i < len(flags):
            if not flags[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(flags) and flags[j + 1]:
                j += 1
            run = list(range(i, j + 1))
            slide = 0.0
            max_speed = 0.0
            for a, b in zip(run, run[1:]):
                step = math.hypot(pts[b][0] - pts[a][0], pts[b][1] - pts[a][1])
                slide += step
                max_speed = max(max_speed, step / dt)
            penetration = max([float(ground_z) - p[2] for p in (pts[k] for k in run)] + [0.0])
            intervals.append({
                "start_frame": int(frames[i]),
                "end_frame": int(frames[j]),
                "contact_samples": len(run),
                "horizontal_slide_m": float(slide),
                "max_slide_speed_m_s": float(max_speed),
                "max_penetration_m": float(penetration),
            })
            i = j + 1
        rows.append({
            "bone": bone,
            "intervals": intervals,
            "contact_samples": sum(iv["contact_samples"] for iv in intervals),
            "max_penetration_m": max([iv["max_penetration_m"] for iv in intervals] + [0.0]),
        })
    return rows


def trajectory_metrics(reference_frames, samples_by_object, sample_frames):
    """Per-object reference error at exact frame numbers only.

    reference_frames: {frame: {object: (x, y, z)}} from plan.validate_reference.
    samples_by_object: {object: {frame: (x, y, z)}} from the sampled report.
    missing frames are counted, never filled with 0. unused counts sampled
    frames that the reference does not mention for that object.
    """
    sample_set = [int(f) for f in sample_frames or []]
    ref_by_object = {}
    for frame, objects in (reference_frames or {}).items():
        for name, pos in (objects or {}).items():
            ref_by_object.setdefault(name, {})[int(frame)] = pos
    out = {}
    for name, ref_map in ref_by_object.items():
        devs = []
        matched = 0
        missing = 0
        for frame in sorted(ref_map):
            sampled = (samples_by_object.get(name) or {}).get(frame)
            if sampled is None:
                missing += 1
                continue
            devs.append(_dist(sampled, ref_map[frame]))
            matched += 1
        known = set(ref_map)
        unused = len([f for f in sample_set if f not in known])
        rmse = math.sqrt(sum(d * d for d in devs) / len(devs)) if devs else None
        out[name] = {
            "rmse_m": None if rmse is None else float(rmse),
            "max_deviation_m": float(max(devs)) if devs else None,
            "matched": matched,
            "missing": missing,
            "unused": unused,
        }
    return out


def select_intersection_pairs(names):
    """Deterministic unordered pairs in input order, capped at 64.

    Returns `(pairs, total)`: `total` is the number of pairs before the cap so the
    caller can report `pairs_total` and mark the truncation instead of silently
    dropping pairs.
    """
    names = list(names or [])
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pairs.append((names[i], names[j]))
    return pairs[:common.MAX_INTERSECTION_PAIRS], len(pairs)


def triangle_budget_ok(triangle_counts, budget=common.TRIANGLE_BUDGET):
    """Budget covers all participating objects of one sampled frame in total."""
    return sum(int(v) for v in triangle_counts or []) <= int(budget)


def aggregate_intersections(frame_hits, pairs, sample_frames, pairs_total=None):
    """frame_hits: per sample frame a set of pair indices, or None when skipped."""
    hits = list(frame_hits or [])
    frames = [int(f) for f in sample_frames or []]
    skipped = [frames[i] for i, h in enumerate(hits) if h is None]
    rows = []
    total = len(pairs) if pairs_total is None else int(pairs_total)
    for idx, (a, b) in enumerate(pairs):
        overlap_frames = [frames[i] for i, h in enumerate(hits) if h is not None and idx in h]
        rows.append({"a": a, "b": b, "overlap_frames": overlap_frames,
                     "sample_frames": len([h for h in hits if h is not None])})
    if hits and all(h is None for h in hits):
        status = "skipped"
    elif skipped:
        status = "degraded"
    else:
        status = "measured"
    return {
        "status": status,
        "pairs": rows,
        "pairs_total": total,
        "pairs_truncated": total > len(rows),
        "skipped_frames": skipped,
        "sample_frames": len([h for h in hits if h is not None]),
        "budget_triangles_per_frame": common.TRIANGLE_BUDGET,
    }


def truncate_contact_intervals(rows, limit):
    """Keep the first `limit` intervals across rows and recompute the row statistics.

    Truncating without recomputing leaves `contact_samples` / `max_penetration_m`
    describing intervals that are no longer in the receipt, so those two fields are
    recomputed from the kept intervals. Returns `(rows, intervals_total)`.
    """
    kept = 0
    total = 0
    out = []
    for row in rows or []:
        intervals = list(row.get("intervals") or [])
        total += len(intervals)
        room = max(0, int(limit) - kept)
        keep = intervals[:room]
        kept += len(keep)
        new_row = dict(row)
        new_row["intervals"] = keep
        new_row["intervals_total"] = len(intervals)
        new_row["truncated"] = len(keep) < len(intervals)
        new_row["contact_samples"] = sum(int(iv["contact_samples"]) for iv in keep)
        new_row["max_penetration_m"] = max([float(iv["max_penetration_m"]) for iv in keep] + [0.0])
        out.append(new_row)
    return out, total
