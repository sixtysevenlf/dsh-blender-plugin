# -*- coding: utf-8 -*-
"""animation_report: bpy adapter that samples evaluated scene state.

All Blender access happens inside functions so the module imports against the
bpy stub and outside Blender. Sampling reads evaluated depsgraph state
(Object.matrix_world / PoseBone.matrix), never Action F-Curves, so actions, NLA,
drivers, constraints and IK all contribute through Blender's own evaluation.
Names, object types and the armature type are resolved before the per-frame loop,
so a typo or a non-mesh intersection object costs no `frame_set` calls. Rates use
`dt = frame_step / effective_fps`, never `1 / fps`. Sampling restores
frame_current and frame_subframe together via `frame_set(frame, subframe=...)` and
never saves the .blend file.
"""
from . import common, metrics, plan


def _to_m4(matrix):
    return tuple(tuple(float(matrix[r][c]) for c in range(4)) for r in range(4))


def _to_vec(vector):
    return (float(vector[0]), float(vector[1]), float(vector[2]))


def _require_object(name):
    import bpy
    obj = bpy.data.objects.get(name)
    if obj is None:
        common.fail("animation_target_missing", "input", "对象不存在：%s" % name,
                    "先用 blender_inspect / blender_session 确认对象名后重试", missing=[name])
    return obj


def _bone_collections(data):
    collections = getattr(data, "collections_all", None)
    if collections is None:
        collections = getattr(data, "collections", None)
    return list(collections or [])


def _bone_collection_names(bone):
    names = []
    for coll in getattr(bone, "collections", None) or []:
        try:
            names.append(coll.name)
        except AttributeError:
            pass
    return names


def _select_bones(arm, plan_args, warnings):
    data = arm.data
    explicit = list(plan_args["bones"])
    for chain in plan_args["bone_chains"]:
        for bone in chain:
            if bone not in explicit:
                explicit.append(bone)
    if explicit:
        names = explicit
        source = "explicit"
    elif plan_args["bone_collections"]:
        found = {coll.name: coll for coll in _bone_collections(data)}
        names = []
        for cname in plan_args["bone_collections"]:
            coll = found.get(cname)
            if coll is None:
                common.fail("animation_target_missing", "input", "骨骼 collection 不存在：%s" % cname,
                            "用 animation_help 查看选择方式，或确认 collection 名", missing=[cname])
            for bone in coll.bones:
                if bone.name not in names:
                    names.append(bone.name)
        source = "collections"
    else:
        names = [bone.name for bone in data.bones
                 if plan_args["bone_filter"] == "all" or bool(bone.use_deform)]
        source = "filter:" + plan_args["bone_filter"]
    missing = [name for name in names if data.bones.get(name) is None]
    if missing:
        common.fail("animation_target_missing", "scene", "骨骼不存在：%s" % ", ".join(missing[:6]),
                    "确认骨骼名；bones/bone_chains/contact_bones/root_bone 都要属于 armature", missing=missing[:16])
    bounded, cut = common.truncated(names, common.MAX_BONES)
    if cut:
        warnings.append(common.warning("truncated", "参与骨超过 %d，已截断" % common.MAX_BONES))
    return bounded, source


def _bone_facts(data, names, include_inventory, warnings):
    facts = []
    for name in names:
        bone = data.bones[name]
        facts.append({
            "name": name,
            "parent": bone.parent.name if bone.parent else None,
            "use_deform": bool(bone.use_deform),
            "collections": _bone_collection_names(bone),
        })
    flags = {row["use_deform"] for row in facts}
    if facts and len(flags) == 1:
        warnings.append(common.warning(
            "deform_flag_uninformative",
            "参与骨的 use_deform 全部为 %r，无法据此区分角色；改用 bone_chains/bone_collections 选择"
            % (list(flags)[0],)))
    inventory = None
    if include_inventory:
        inventory = []
        for coll in _bone_collections(data):
            members = list(coll.bones)
            deform = [bone for bone in members if bone.use_deform]
            sample = [bone.name for bone in members[:5]]
            inventory.append({
                "name": coll.name,
                "bone_count": len(members),
                "deform_bone_count": len(deform),
                "non_deform_bone_count": len(members) - len(deform),
                "mixed": 0 < len(deform) < len(members),
                "sample_bones": sample,
            })
    return facts, inventory


def _contact_point(ev_arm, bone_name, contact_point):
    from mathutils import Vector
    pose_bone = ev_arm.pose.bones.get(bone_name)
    if pose_bone is None:
        common.fail("animation_target_missing", "scene", "contact 骨不存在：%s" % bone_name,
                    "contact_bones 必须是 armature 的骨", missing=[bone_name])
    head = pose_bone.matrix.translation
    tail = pose_bone.matrix @ Vector((0.0, float(pose_bone.length), 0.0))
    if contact_point == "head":
        point = head
    elif contact_point == "center":
        point = (head + tail) * 0.5
    else:
        point = tail
    return ev_arm.matrix_world @ point


def _mesh_triangles(ev_obj):
    mesh = ev_obj.to_mesh()
    try:
        if not mesh.loop_triangles:
            mesh.calc_loop_triangles()
        return mesh, len(mesh.loop_triangles)
    except Exception:
        ev_obj.to_mesh_clear()
        raise


def _frame_intersections(depsgraph, mesh_objects, pairs, budget):
    from mathutils.bvhtree import BVHTree
    meshes = []
    try:
        # Objects are resolved and type-checked once in _resolve_intersection_objects;
        # only the evaluated mesh has to be re-taken per frame.
        for obj in mesh_objects:
            ev = obj.evaluated_get(depsgraph)
            mesh, tris = _mesh_triangles(ev)
            meshes.append((obj.name, ev, mesh, tris))
        total = sum(item[3] for item in meshes)
        if not metrics.triangle_budget_ok([total], budget):
            return None, total
        trees = {}
        for name, ev, mesh, _tris in meshes:
            matrix = ev.matrix_world
            verts = [matrix @ vertex.co for vertex in mesh.vertices]
            polys = [tuple(tri.vertices) for tri in mesh.loop_triangles]
            trees[name] = BVHTree.FromPolygons(verts, polys, all_triangles=True)
        hits = set()
        for idx, (a, b) in enumerate(pairs):
            if trees[a].overlap(trees[b]):
                hits.add(idx)
        return hits, total
    finally:
        for _name, ev, _mesh, _tris in meshes:
            ev.to_mesh_clear()


def _resolve_intersection_objects(names, warnings):
    """Resolve intersection objects before sampling; drop non-mesh ones once.

    A non-mesh object cannot overlap anything, so treating it as "no overlap" would
    silently turn an unmeasured pair into a measured one. It is dropped here with a
    single aggregated warning (never one warning per sampled frame), and the caller
    reports `status="skipped"` when fewer than two meshes remain.
    """
    meshes = []
    dropped = []
    for name in names or []:
        obj = _require_object(name)
        if getattr(obj, "type", None) != "MESH":
            if name not in dropped:
                dropped.append(name)
        elif name not in [mesh.name for mesh in meshes]:
            meshes.append(obj)
    if dropped:
        warnings.append(common.warning(
            "intersection_non_mesh",
            "intersection_objects 里的非 mesh 对象已跳过：%s" % ", ".join(dropped[:6])))
    return meshes, dropped


def _check_reference_fps(plan_args, effective):
    if not plan_args["reference"]:
        return
    fps = float(plan_args.get("reference_fps") or 0.0)
    if abs(fps - effective) > 1e-6:
        common.fail("animation_reference_invalid", "input",
                    "reference.fps=%r 与场景 effective_fps=%r 不一致" % (fps, effective),
                    "用场景 effective_fps 生成 reference，或修正 reference.fps",
                    reference_fps=fps, effective_fps=effective)


def _sample_scene_frames(bpy, scene, frames, target_objects, arm, chain_bones, contact_bones,
                         root_bone, contact_point, mesh_objects, pairs):
    target_names = list(target_objects)
    object_pos = {name: [] for name in target_names}
    object_quat = {name: [] for name in target_names}
    bone_pose = {name: [] for name in chain_bones}
    contact_points = {name: [] for name in contact_bones}
    root_positions = []
    root_quats = []
    frame_hits = []
    previous_frame = int(scene.frame_current)
    previous_subframe = float(scene.frame_subframe)
    try:
        for frame in frames:
            scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            for name in target_names:
                ev = target_objects[name].evaluated_get(depsgraph)
                matrix = ev.matrix_world
                object_pos[name].append(_to_vec(matrix.translation))
                object_quat[name].append(tuple(matrix.to_quaternion()))
            if arm is not None:
                ev_arm = arm.evaluated_get(depsgraph)
                for bone in chain_bones:
                    pose_bone = ev_arm.pose.bones.get(bone)
                    if pose_bone is None:
                        common.fail("animation_target_missing", "scene", "骨骼不存在：%s" % bone,
                                    "bone_chains 必须是 armature 的骨", missing=[bone])
                    bone_pose[bone].append(_to_m4(pose_bone.matrix))
                for bone in contact_bones:
                    contact_points[bone].append(_to_vec(_contact_point(ev_arm, bone, contact_point)))
                if root_bone:
                    pose_bone = ev_arm.pose.bones[root_bone]
                    world = ev_arm.matrix_world @ pose_bone.matrix
                    root_positions.append(_to_vec(world.translation))
                    root_quats.append(tuple(world.to_quaternion()))
            if pairs:
                hits, _total = _frame_intersections(depsgraph, mesh_objects, pairs, common.TRIANGLE_BUDGET)
                frame_hits.append(hits)
    finally:
        # frame_subframe alone would leave the depsgraph evaluated at subframe 0 once
        # the next frame_set runs, so both are restored in one call.
        scene.frame_set(previous_frame, subframe=previous_subframe)
    return object_pos, object_quat, bone_pose, contact_points, root_positions, root_quats, frame_hits


def _detect_animation(target_objects, object_pos, object_quat, arm, root_bone, root_positions, root_quats):
    static_targets = []
    animated = False
    for name in target_objects:
        obj = target_objects[name]
        if getattr(obj, "animation_data", None) is not None:
            animated = True
        positions = object_pos[name]
        quats = object_quat[name]
        moved = any(common.vec_distance(positions[0], point) > 1e-9 for point in positions[1:])
        turned = any(common.quat_angle_deg(common.quat_delta_shortest(quats[0], q)) > 1e-6 for q in quats[1:])
        if not moved and not turned:
            static_targets.append(name)
    if arm is not None and getattr(arm, "animation_data", None) is not None:
        animated = True
    if root_bone and len(root_positions) >= 2:
        if any(common.vec_distance(root_positions[0], p) > 1e-9 for p in root_positions[1:]):
            animated = True
        if any(common.quat_angle_deg(common.quat_delta_shortest(root_quats[0], q)) > 1e-6 for q in root_quats[1:]):
            animated = True
    return static_targets, animated


def _root_motion_report(target_names, object_pos, object_quat, frames, dt,
                        static_targets, root_bone, root_positions, root_quats):
    root_motion = {}
    for name in target_names:
        row = metrics.root_motion_metrics(object_pos[name], object_quat[name], frames, dt)
        row["source"] = "object"
        row["static"] = name in static_targets
        root_motion[name] = row
    if root_bone:
        row = metrics.root_motion_metrics(root_positions, root_quats, frames, dt)
        row["source"] = "root_bone"
        row["bone"] = root_bone
        root_motion[root_bone] = row
    return root_motion


def _joint_report(plan_args, arm, bone_pose, frames, dt, warnings):
    if not plan_args["bone_chains"] or arm is None:
        warnings.append(common.warning("joints_empty", "没有 bone_chains，joints 为空"))
        return []
    bones = arm.data.bones
    mismatches = metrics.chain_parent_mismatches(
        plan_args["bone_chains"],
        {bone.name: (bone.parent.name if bone.parent else None) for bone in bones})
    if mismatches:
        warnings.append(common.warning(
            "chain_parent_not_bone_parent",
            "链上相邻骨不是 Bone.parent 关系（关节角按链上父骨计算）：%s"
            % ", ".join("%s<-%s(实际 %s)" % (bone, chained, actual or "无") for bone, chained, actual in mismatches[:6])))
    rests = metrics.chained_rest_matrices(
        plan_args["bone_chains"], {bone.name: _to_m4(bone.matrix_local) for bone in bones})
    return metrics.joint_metrics(plan_args["bone_chains"], bone_pose, rests, frames, dt)


def _contact_report(contact_bones, contact_points, frames, dt, plan_args, warnings, truncated):
    if not contact_bones:
        return [], truncated
    contacts = metrics.contact_intervals(contact_points, frames, dt,
                                         plan_args["ground_z"], plan_args["contact_epsilon"])
    if not any(row["intervals"] for row in contacts):
        warnings.append(common.warning(
            "no_contact_in_band",
            "所有 contact 骨都在接触带外：ground_z=%.4f contact_epsilon=%.4f"
            % (plan_args["ground_z"], plan_args["contact_epsilon"])))
    contacts, intervals_total = metrics.truncate_contact_intervals(contacts, common.MAX_CONTACT_INTERVALS)
    if intervals_total > common.MAX_CONTACT_INTERVALS:
        warnings.append(common.warning(
            "truncated", "接触区间 %d 个超过 %d，已按输入顺序截断（统计已按截断后的区间重算）"
            % (intervals_total, common.MAX_CONTACT_INTERVALS)))
        truncated = True
    return contacts, truncated


def _intersection_report(pairs, pairs_total, frame_hits, frames, dropped, warnings, truncated):
    if not pairs:
        requested = bool(dropped)
        if requested:
            warnings.append(common.warning(
                "intersection_insufficient_meshes",
                "intersection_objects 里可用 mesh 少于 2 个，intersections=skipped"))
            truncated = True
        return ({"status": "skipped" if requested else "not_requested", "pairs": [], "pairs_total": int(pairs_total),
                 "pairs_truncated": False, "skipped_frames": [], "sample_frames": 0,
                 "budget_triangles_per_frame": common.TRIANGLE_BUDGET, "non_mesh": list(dropped)}, truncated)
    intersections = metrics.aggregate_intersections(frame_hits, pairs, frames, pairs_total)
    if intersections["status"] != "measured":
        warnings.append(common.warning("intersection_" + intersections["status"],
                                       "部分或全部采样帧超出三角形预算，intersections=%s" % intersections["status"]))
        truncated = True
    if intersections["pairs_truncated"]:
        warnings.append(common.warning(
            "intersection_pairs_truncated",
            "相交对 %d 超过 %d，按输入顺序截断" % (pairs_total, len(pairs))))
        truncated = True
    intersections["non_mesh"] = list(dropped)
    return intersections, truncated


def _trajectory_report(plan_args, target_names, object_pos, frames, warnings):
    if not plan_args["reference"]:
        return None
    referenced = []
    for objects_at_frame in plan_args["reference"].values():
        for name in objects_at_frame:
            if name not in referenced:
                referenced.append(name)
    unsampled = [name for name in referenced if name not in target_names]
    if unsampled:
        warnings.append(common.warning(
            "reference_object_unused",
            "reference 里的对象没有参与采样：%s" % ", ".join(unsampled[:6])))
    samples = {name: {frames[i]: object_pos[name][i] for i in range(len(frames))} for name in target_names}
    trajectory = metrics.trajectory_metrics(plan_args["reference"], samples, frames)
    for name, row in trajectory.items():
        if row["missing"]:
            warnings.append(common.warning("reference_missing_frames",
                                           "%s 有 %d 个参考帧没有采样" % (name, row["missing"])))
    return trajectory


def _build_report_dict(verdict, scene, effective, window, frames, objects, arm,
                       bone_facts, inventory, root_motion, joints, contacts,
                       intersections, trajectory, warnings, truncated):
    return {
        "ok": True,
        "module": "animation",
        "version": common.ANIMATION_VERSION,
        "action": "animation_report",
        "units": dict(common.UNITS),
        "verdict": verdict,
        "scene": {"frame_start": int(scene.frame_start), "frame_end": int(scene.frame_end),
                  "effective_fps": effective},
        "sampling": {
            "requested": {"frame_start": window["requested_frame_start"], "frame_end": window["requested_frame_end"]},
            "actual": {"frame_start": frames[0], "frame_end": frames[-1]},
            "frame_step": window["frame_step"],
            "sample_count": window["sample_count"],
            "last_sample_frame": window["last_sample_frame"],
            "effective_fps": effective,
        },
        "targets": {"objects": [obj.name for obj in objects],
                    "armature": arm.name if arm is not None else None},
        "bones": bone_facts,
        "bone_inventory": inventory,
        "root_motion": root_motion,
        "joints": joints,
        "contacts": contacts,
        "intersections": intersections,
        "trajectory": trajectory,
        "warnings": warnings,
        "truncated": truncated,
    }


def _resolve_armature(plan_args, warnings):
    """Resolve the armature object, select bones and validate the root bone.

    Stage split: a missing object or collection name is `input` (the argument itself is
    wrong), a missing bone name is `scene` because only the live armature can answer it.
    Type checking is a direct `type != ARMATURE` test: `Object.pose` exists on every
    object (and is None for non-armatures), so `hasattr` can never detect this.
    """
    if not plan_args["armature"]:
        warnings.append(common.warning("no_armature", "未提供 armature：reports 只有对象级 root motion"))
        return None, [], None
    arm = _require_object(plan_args["armature"])
    if getattr(arm, "type", None) != "ARMATURE":
        common.fail("animation_input_invalid", "input",
                    "armature 不是骨架对象：%s（type=%r）" % (plan_args["armature"], getattr(arm, "type", None)),
                    "armature 传 ARMATURE 类型对象；普通物体放 objects")
    bone_names, bone_source = _select_bones(arm, plan_args, warnings)
    if not bone_names:
        warnings.append(common.warning("no_bones_selected", "按 %s 没有选中骨骼" % bone_source))
    root_bone = plan_args["root_bone"]
    if root_bone and arm.data.bones.get(root_bone) is None:
        common.fail("animation_target_missing", "scene", "root_bone 不存在：%s" % root_bone,
                    "root_bone 必须是 armature 的骨", missing=[root_bone])
    return arm, bone_names, root_bone


def animation_report(**args):
    plan_args = plan.validate_report_args(args)
    import bpy
    scene = bpy.context.scene
    render = scene.render
    effective = plan.effective_fps(render.fps, render.fps_base)
    _check_reference_fps(plan_args, effective)
    window = plan.select_frame_window(
        scene.frame_start, scene.frame_end,
        frame_start=plan_args.get("frame_start"), frame_end=plan_args.get("frame_end"),
        frame_step=plan_args.get("frame_step"))
    frames = window["frames"]
    # Sample spacing in seconds. Dividing by 1/effective_fps instead would report
    # frame_step times the true speed and frame_step ** 2 times the true acceleration.
    dt = float(window["frame_step"]) / effective
    warnings = list(plan_args["warnings"])
    truncated = bool(plan_args["truncated"])

    # ---- resolve names and types before the first frame_set: a typo must not cost one
    # frame_set per sample frame, and a non-mesh intersection object must not be sampled.
    objects = [_require_object(name) for name in plan_args["objects"]]
    arm, bone_names, root_bone = _resolve_armature(plan_args, warnings)
    contact_bones = []
    if plan_args["contact_bones"]:
        if arm is None:
            common.fail("animation_input_invalid", "input", "contact_bones 需要 armature", "同时给 armature")
        contact_bones = list(plan_args["contact_bones"])
        missing = [name for name in contact_bones if arm.data.bones.get(name) is None]
        if missing:
            common.fail("animation_target_missing", "scene", "contact 骨不存在：%s" % ", ".join(missing[:6]),
                        "确认骨骼名", missing=missing[:16])
    # Bone facts cover every bone the receipt mentions (selection + contact bones + root
    # bone), so a contact or root bone is never absent from `bones`.
    fact_names = list(bone_names)
    if arm is not None:
        for name in contact_bones + ([root_bone] if root_bone else []):
            if name not in fact_names:
                fact_names.append(name)
    fact_names, fact_cut = common.truncated(fact_names, common.MAX_BONES)
    if fact_cut:
        warnings.append(common.warning("truncated", "骨骼事实超过 %d，已截断" % common.MAX_BONES))
    bone_facts, inventory = [], None
    if arm is not None:
        bone_facts, inventory = _bone_facts(arm.data, fact_names, plan_args["include_bone_inventory"], warnings)

    # Non-mesh intersection objects are dropped in one place, with one aggregated
    # warning: recording them as "this frame has no overlap" would turn an unmeasured
    # pair into a measured one, and one warning per frame is unreadable.
    mesh_objects, dropped_meshes = _resolve_intersection_objects(list(plan_args["intersection_objects"]), warnings)
    pairs, pairs_total = metrics.select_intersection_pairs([obj.name for obj in mesh_objects])

    # Objects were resolved before the first frame_set , so the sampling loop
    # only looks up the prepared dict and never re-resolves a name per frame.
    target_objects = {obj.name: obj for obj in objects}
    if arm is not None:
        target_objects[arm.name] = arm
    target_names = list(target_objects)
    chain_bones = []
    for chain in plan_args["bone_chains"]:
        for bone in chain:
            if bone not in chain_bones:
                chain_bones.append(bone)
    if not plan_args["bone_chains"] and bone_names:
        warnings.append(common.warning("no_bone_chains", "未提供 bone_chains：joints 为空，不从 Bone.parent 自动推导"))

    object_pos, object_quat, bone_pose, contact_points, root_positions, root_quats, frame_hits = \
        _sample_scene_frames(bpy, scene, frames, target_objects, arm, chain_bones, contact_bones,
                             root_bone, plan_args["contact_point"], mesh_objects, pairs)

    static_targets, animated = _detect_animation(
        target_objects, object_pos, object_quat, arm, root_bone, root_positions, root_quats)
    if static_targets:
        warnings.append(common.warning("static", "目标在采样窗口内无变换变化：%s" % ", ".join(static_targets[:6])))
    if not animated:
        common.fail("animation_no_data", "animation",
                    "所有目标都没有 animation_data，且采样变换无变化",
                    "确认场景帧范围包含动画；或提供有动画的对象/骨架",
                    fallback="先用 blender_op op=motion_status/audit_scene 确认动画数据")

    root_motion = _root_motion_report(target_names, object_pos, object_quat, frames, dt,
                                      static_targets, root_bone, root_positions, root_quats)
    joints = _joint_report(plan_args, arm, bone_pose, frames, dt, warnings)
    contacts, truncated = _contact_report(contact_bones, contact_points, frames, dt,
                                          plan_args, warnings, truncated)
    intersections, truncated = _intersection_report(pairs, pairs_total, frame_hits, frames,
                                                    dropped_meshes, warnings, truncated)
    trajectory = _trajectory_report(plan_args, target_names, object_pos, frames, warnings)

    # Anything requested but not measured degrades the verdict instead of passing:
    # skipped/non-mesh intersections, truncated pairs/contacts, static targets, joints
    # that were never collected, and reference objects that were never sampled.
    degraded_codes = {"static", "truncated", "intersection_degraded", "intersection_skipped",
                      "intersection_non_mesh", "intersection_insufficient_meshes",
                      "intersection_pairs_truncated", "reference_missing_frames",
                      "reference_object_unused", "no_armature", "deform_flag_uninformative",
                      "no_bones_selected"}
    verdict = "degraded" if any(w["code"] in degraded_codes for w in warnings) else "pass"
    return _build_report_dict(verdict, scene, effective, window, frames, objects, arm,
                              bone_facts, inventory, root_motion, joints, contacts,
                              intersections, trajectory, warnings, truncated)
