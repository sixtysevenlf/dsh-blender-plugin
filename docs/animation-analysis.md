# Animation analysis (animation family)

[简体中文](zh/animation-analysis.md)

The `animation` family measures motion in the live scene: object/armature root motion, local
joint angles on explicit bone chains, ground contact and foot slide, mesh interpenetration, and an
optional reference trajectory. It samples Blender's evaluated depsgraph frame by frame — it never
reads `Action.fcurves` directly, so actions, NLA, drivers, constraints and IK all contribute through
Blender's own evaluation. It does not analyse modifier, geometry-node or particle settings, does not
infer rig roles (control/mechanism/DEF), and does not ship rig-profile examples.

## Ops and session semantics

| Op | Session | Write grant | What it does |
|---|---|---|---|
| `animation_report` | required (session-read) | not required | sample the requested frame window and return metrics |
| `animation_help` | required (session-read) | not required | list parameters, units, limits, methods and error codes |
| `animation_selftest` | required (write gate) | required | build a temporary keyframed object + armature, verify, then clean up |

`animation_report` and `animation_help` are **session-read** ops: they require a ready GUI session
(the scene is live) but never take the write lease or a write scope grant. `animation_selftest`
mutates the scene temporarily, so it goes through the normal write gate. Neither op is in
`READ_ONLY_OPS`; `animation_report` and `animation_help` are in `PLAN_SESSION_READ_OPS`.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `objects` | — | up to 16 object names; object-level root motion and intersections |
| `armature` | — | armature object; required by `bones`, `bone_chains`, `bone_collections`, `contact_bones`, `root_bone` |
| `bones` | — | explicit bone names, up to 64 |
| `bone_chains` | — | explicit joint chains, e.g. `[["upper_arm", "forearm"]]`; joints are never derived from `Bone.parent` |
| `bone_collections` | — | select bones by Blender bone collection |
| `bone_filter` | `deform` | `deform` (only `use_deform`) or `all`; ignored when explicit bones/chains/collections are given |
| `include_bone_inventory` | `false` | per-collection bone counts, deform/non-deform counts, `mixed`, `sample_bones` |
| `contact_bones` | — | up to 16 bones sampled against the ground plane |
| `contact_point` | `tail` | `head`, `tail` or `center` of the pose bone |
| `ground_z` | `0.0` | ground plane height in metres |
| `contact_epsilon` | `0.01` | contact band in metres; signed test is `z <= ground_z + epsilon` |
| `frame_start` / `frame_end` | scene range | closed interval; when either is given the other is required |
| `frame_step` | auto | positive integer; when omitted the smallest integer step is chosen so the sample count is at most 240 |
| `root_bone` | — | use this bone's final world transform as the root-motion source instead of the armature object |
| `intersection_objects` | `objects` | 2..16 mesh objects; pairwise surface overlap |
| `reference` | — | `{version: 1, fps, frames: [{frame, objects: {name: [x, y, z]}}]}`, up to 240 frames / 2 MiB |

Selection priority: explicit `bones`/`bone_chains` → `bone_collections` → `bone_filter`. At least
one of `objects` or `armature` is required.

## Units and sampling

- units: length **m**, angle **deg**, speed **m/s**, angular speed **deg/s**, `effective_fps` in 1/s.
- effective fps = `scene.render.fps / scene.render.fps_base` (24 with `fps_base=1.001` is 23.976…).
- frames are sampled by `scene.frame_set(frame)` and then reading `Object.matrix_world` and
  `PoseBone.matrix` from the evaluated depsgraph. The sample spacing is
  `dt = frame_step / effective_fps`, and every speed, angular speed and slide speed divides
  by that `dt` — so `frame_step > 1` no longer multiplies speeds (or squares accelerations).
- `frame_start..frame_end` is a closed interval; with no explicit step the minimum integer step that
  keeps `sample_count <= 240` is used. An explicit step that would exceed 240 samples returns
  `animation_sampling_invalid` — the request is never silently clamped.
- the receipt reports the requested range, the actual range, `frame_step`, `sample_count`,
  `last_sample_frame` and `effective_fps`.
- `frame_current` and `frame_subframe` are restored in a `finally` block; keyframes, pose, materials
  and the .blend file are not modified.

## Metrics

### Root motion

Per sampled object (and per `root_bone` when given): `path_length_m`, `displacement_m`,
`displacement_vector_m`, `max_speed_m_s`, `max_accel_m_s2`, `loop_closure_m`,
`rotation_delta_deg`, `rotation_path_deg`. Speeds/accelerations use the frame delta and
`effective_fps`; a single sample yields `null`, never `0`. `rotation_delta_deg` is the shortest arc
(over 180° is compressed and raises `rotation_shortest_arc`); `rotation_path_deg` is the accumulated
per-step angle. An object whose transform does not change in the window gets zero metrics plus a
`static` warning; when every target lacks `animation_data` and nothing moves, the op returns
`animation_no_data`.

### Joints

Only explicit `bone_chains` produce joint rows. `pose_local = parent_pose^-1 @ pose`,
`rest_local = parent_rest^-1 @ rest` (identity parent for the first bone of a chain),
`delta = pose_local.to_3x3() @ rest_local.to_3x3().inverted()`; the angle is
`delta.to_quaternion().angle`, and angular speed/acceleration come from adjacent pose quaternion
deltas divided by Δt. A parent rotation that carries the child along therefore reports ≈0 local
joint change for the child.

### Contacts

Each contact point is the pose bone's head/tail/center transformed by the armature object matrix.
A sample is in contact when `z <= ground_z + contact_epsilon`; penetration counts as contact. Rows
carry `intervals` (`start_frame`, `end_frame`, `contact_samples`, `horizontal_slide_m`,
`max_slide_speed_m_s`, `max_penetration_m`). With `frame_step > 1` this is a sampled
approximation. No contact is an empty list, not a failure.

### Intersections

For each sample frame the op builds evaluated world-space meshes and checks surface overlap with
`BVHTree.FromPolygons(..., all_triangles=True)`; each pair reports `overlap_frames` and
`sample_frames`. The per-frame triangle budget is 200,000 across all participating objects: over
budget the frame is skipped and recorded in `skipped_frames` (`degraded` when partial, `skipped`
when every frame was skipped). An intersection that cannot be resolved (fewer than two usable
meshes, non-mesh targets, or skipped frames) is never reported as measured: the result is
`status: "skipped"` with a warning, and the report verdict is `degraded`, never `pass`. At most 64
pairs in input order. Non-mesh objects are skipped with a warning. Triangle/vertex arrays are
never returned.

### Reference trajectory

`reference` is validated strictly: `version: 1`, `fps` within `1e-6` of `effective_fps`, unique
integer frames and finite coordinates. Matching is by exact frame number only (no nearest
neighbour), and a missing frame is reported as `missing`, never filled with zero. Per object the
receipt returns `rmse_m`, `max_deviation_m`, `matched`, `missing`, `unused`.

## Limits and errors

Hard limits: 16 objects, 64 bones, 16 contact bones, 64 contact intervals, 64 intersection pairs,
240 samples, 240 reference frames / 2 MiB. Over-limit inputs are truncated with a warning and
`truncated: true`.

Stage taxonomy: parameter-shape failures are `input` (not `scene`) — invalid combinations, bad
numbers, a non-armature object passed as `armature` (`animation_input_invalid`), and any sampling
arithmetic. Only existence failures that need the live scene (missing object, armature, bone or
bone collection) use `scene` (`animation_target_missing`).

| Code | Stage | Meaning |
|---|---|---|
| `animation_input_invalid` | `input` | invalid parameter combination (missing armature for bone args, a non-armature object in `armature`, <2 intersection objects, bad filter/point/epsilon/ground_z) |
| `animation_sampling_invalid` | `input` | frame_start > frame_end, frame_step <= 0, or explicit step over 240 samples |
| `animation_target_missing` | `scene` | object, armature, bone or bone collection does not exist |
| `animation_reference_invalid` | `input` | bad version, fps mismatch, duplicate frames, non-finite or non-integer data |
| `animation_no_data` | `animation` | no target has animation data and no sampled transform changes |
| `animation_budget_exceeded` | `animation` | a budget cannot be honoured (reported with the skipped evidence) |
| `animation_unsupported` | `animation` | unexpected runtime or API failure |

Every failure carries `stage`, `root_cause`, `fix` and `fallback`, so the caller can act without
guessing.

## Examples

```python
# object-level root motion over an explicit window
blender_op(op="animation_report", args={"objects": ["CHAR_body"], "frame_start": 1, "frame_end": 48})
# explicit chain + contacts against the ground plane
blender_op(op="animation_report", args={
  "armature": "CHAR_rig", "bone_chains": [["thigh_L", "shin_L", "foot_L"]],
  "contact_bones": ["foot_L", "foot_R"], "contact_point": "tail", "ground_z": 0.0,
  "frame_start": 1, "frame_end": 120, "frame_step": 1})
# mesh interpenetration for two objects over the same window
blender_op(op="animation_report", args={"objects": ["door"], "intersection_objects": ["door", "frame"],
  "frame_start": 1, "frame_end": 24})
# generic discovery and a cleanup-verified selftest
blender_op(op="animation_help")
blender_op(op="animation_selftest")
```

Bone names above are placeholders; replace them with the names in your scene. `animation_help` lists
the same fields and limits without any rig-specific example.
