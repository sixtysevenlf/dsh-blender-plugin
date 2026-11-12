# -*- coding: utf-8 -*-
"""animation input planning: effective fps, frame window, static validation, reference.

Everything here is pure: it never imports bpy. Blender target resolution happens
in report.py after this layer has validated the shape of the request, so illegal
combinations fail before any scene state is touched.
"""
import math

from . import common


def effective_fps(fps, fps_base):
    try:
        fps = float(fps)
        fps_base = float(fps_base)
    except (TypeError, ValueError):
        common.fail("animation_input_invalid", "input", "fps/fps_base 不是数字", "使用场景 render.fps 与 render.fps_base 的数值")
    if not (math.isfinite(fps) and math.isfinite(fps_base)) or fps <= 0.0 or fps_base <= 0.0:
        common.fail("animation_input_invalid", "input", "fps=%r fps_base=%r 必须为正的有限数" % (fps, fps_base),
                    "传入正数 fps 与 fps_base（Blender 场景一般为 24/1.0 或 24/1.001）")
    value = fps / fps_base
    if not math.isfinite(value) or value <= 0.0:
        common.fail("animation_input_invalid", "input", "effective_fps 计算非法", "检查 fps 与 fps_base")
    return value


def select_frame_window(scene_start, scene_end, frame_start=None, frame_end=None, frame_step=None,
                        cap=common.MAX_SAMPLES):
    """Closed-interval sample window; smallest integer step keeps sample_count <= cap.

    An explicit step that overflows the cap is an error (animation_sampling_invalid),
    never silently widened: the caller asked for a specific sampling density.
    """
    start = int(scene_start if frame_start is None else frame_start)
    end = int(scene_end if frame_end is None else frame_end)
    if start > end:
        common.fail("animation_input_invalid", "input", "frame_start=%d 大于 frame_end=%d" % (start, end),
                    "交换 frame_start/frame_end，或省略它们使用场景帧范围")
    span = end - start
    if frame_step is not None:
        try:
            step = int(frame_step)
        except (TypeError, ValueError):
            common.fail("animation_input_invalid", "input", "frame_step 不是整数", "frame_step 传正整数")
        if step <= 0:
            common.fail("animation_input_invalid", "input", "frame_step=%r 必须为正整数" % (frame_step,),
                        "frame_step 传正整数，或省略它让内核选最小步长")
        count = span // step + 1
        if count > cap:
            common.fail("animation_sampling_invalid", "input",
                        "显式 frame_step=%d 需要 %d 个采样帧，超过上限 %d" % (step, count, cap),
                        "增大 frame_step、缩小 frame_start..frame_end，或分段报告",
                        fallback="省略 frame_step 让内核选择最小步长（结果会标注实际 step）",
                        frame_step=step, sample_count=count, max_samples=cap)
    else:
        step = max(1, span // cap + 1)
        count = span // step + 1
    frames = [start + i * step for i in range(count)]
    return {
        "requested_frame_start": start,
        "requested_frame_end": end,
        "frame_step": step,
        "sample_count": count,
        "last_sample_frame": frames[-1] if frames else None,
        "frames": frames,
    }


def _names(value, field, limit, warnings, state):
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                common.fail("animation_input_invalid", "input", "%s 里有非字符串/空名字" % field,
                            "%s 传对象/骨骼名字的字符串列表" % field)
            items.append(item.strip())
    else:
        common.fail("animation_input_invalid", "input", "%s 必须是字符串或字符串列表" % field,
                    "%s 传字符串列表" % field)
    deduped = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    bounded, cut = common.truncated(deduped, limit)
    if cut:
        warnings.append(common.warning("truncated", "%s 超过上限 %d，已截断" % (field, limit)))
        state["truncated"] = True
    return bounded


def _chains(value, limit, warnings, state):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        common.fail("animation_input_invalid", "input", "bone_chains 必须是链列表",
                    'bone_chains 传 [["parent","child"], ...] 的显式链')
    chains = []
    total = 0
    for chain in value:
        if isinstance(chain, str):
            chain = [chain]
        if not isinstance(chain, (list, tuple)) or not chain:
            common.fail("animation_input_invalid", "input", "bone_chains 里有空链/非法链",
                        "每条链是非空的骨骼名列表")
        names = []
        for bone in chain:
            if not isinstance(bone, str) or not bone.strip():
                common.fail("animation_input_invalid", "input", "bone_chains 里有非字符串/空骨名",
                            "每条链只放骨骼名字符串")
            names.append(bone.strip())
        if len(names) != len(set(names)):
            common.fail("animation_input_invalid", "input", "bone_chains 里同一条链重复骨名",
                        "同一条链内不要重复骨骼")
        total += len(names)
        if total <= limit:
            chains.append(names)
        else:
            warnings.append(common.warning("truncated", "bone_chains 超过骨骼上限 %d，已截断" % limit))
            state["truncated"] = True
            break
    return chains


def _finite(value, field):
    try:
        number = float(value)
    except (TypeError, ValueError):
        common.fail("animation_input_invalid", "input", "%s 不是数字" % field, "%s 传有限数值" % field)
    if not math.isfinite(number):
        common.fail("animation_input_invalid", "input", "%s=%r 不是有限数" % (field, value), "%s 传有限数值" % field)
    return number


def validate_reference(ref, effective_fps_value=None):
    """Strict reference validation; returns {frame: {object: (x, y, z)}}.

    Frames are matched by exact number only (no nearest-neighbour); a reference
    whose fps differs from the scene effective fps by more than 1e-6 is invalid.
    """
    if not isinstance(ref, dict):
        common.fail("animation_reference_invalid", "input", "reference 不是 JSON 对象",
                    "reference 传 {version:1, fps, frames:[{frame, objects}]}")
    size = common.json_size_bytes(ref)
    if size is not None and size > common.MAX_REFERENCE_BYTES:
        common.fail("animation_reference_invalid", "input", "reference 超过 %d 字节" % common.MAX_REFERENCE_BYTES,
                    "缩小 reference（最多 240 帧）")
    if ref.get("version") != 1 or isinstance(ref.get("version"), bool):
        common.fail("animation_reference_invalid", "input", "reference.version 必须是整数 1",
                    "version 固定为 1")
    fps = ref.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(float(fps)) or float(fps) <= 0:
        common.fail("animation_reference_invalid", "input", "reference.fps 必须是正的有限数",
                    "fps 与场景 effective_fps 一致（容差 1e-6）")
    if effective_fps_value is not None and abs(float(fps) - float(effective_fps_value)) > 1e-6:
        common.fail("animation_reference_invalid", "input",
                    "reference.fps=%r 与场景 effective_fps=%r 不一致" % (float(fps), float(effective_fps_value)),
                    "用场景 effective_fps 生成 reference，或修正 reference.fps",
                    reference_fps=float(fps), effective_fps=float(effective_fps_value))
    frames = ref.get("frames")
    if not isinstance(frames, list) or not frames:
        common.fail("animation_reference_invalid", "input", "reference.frames 必须是非空数组",
                    "frames 传 [{frame, objects}, ...]")
    if len(frames) > common.MAX_REFERENCE_FRAMES:
        common.fail("animation_reference_invalid", "input", "reference 帧数超过 %d" % common.MAX_REFERENCE_FRAMES,
                    "最多 240 帧")
    indexed = {}
    for i, row in enumerate(frames):
        if not isinstance(row, dict):
            common.fail("animation_reference_invalid", "input", "reference.frames[%d] 不是对象" % i,
                        "每帧形如 {frame: 1, objects: {Cube: [x,y,z]}}")
        frame = row.get("frame")
        if isinstance(frame, bool) or not isinstance(frame, int):
            common.fail("animation_reference_invalid", "input", "reference 帧号必须是整数",
                        "帧号用整数（不取整、不最近邻）")
        if frame in indexed:
            common.fail("animation_reference_invalid", "input", "reference 帧号 %d 重复" % frame,
                        "每帧只出现一次")
        objects = row.get("objects")
        if not isinstance(objects, dict):
            common.fail("animation_reference_invalid", "input", "reference.frames[%d].objects 不是对象" % i,
                        "objects 形如 {Cube: [x,y,z]}")
        points = {}
        for name, coords in objects.items():
            if not isinstance(name, str) or not name.strip():
                common.fail("animation_reference_invalid", "input", "reference 对象名非法", "对象名用字符串")
            if not isinstance(coords, (list, tuple)) or len(coords) != 3:
                common.fail("animation_reference_invalid", "input", "reference 对象 %s 的坐标不是三元组" % name,
                            "坐标形如 [x, y, z]")
            vals = []
            for v in coords:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                    common.fail("animation_reference_invalid", "input", "reference 对象 %s 的坐标不是有限数" % name,
                                "坐标必须是有限的米制数值")
                vals.append(float(v))
            points[name] = tuple(vals)
        indexed[frame] = points
    return indexed


def validate_report_args(args):
    """Static (bpy-free) validation and normalization of animation_report args."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        common.fail("animation_input_invalid", "input", "args 不是对象", "args 传 JSON 对象")
    warnings = []
    state = {"truncated": False}
    objects = _names(args.get("objects"), "objects", common.MAX_OBJECTS, warnings, state)
    armature = args.get("armature")
    if armature is not None:
        if not isinstance(armature, str) or not armature.strip():
            common.fail("animation_input_invalid", "input", "armature 必须是字符串", "armature 传骨架对象名")
        armature = armature.strip()
    bones = _names(args.get("bones"), "bones", common.MAX_BONES, warnings, state)
    chains = _chains(args.get("bone_chains"), common.MAX_BONES, warnings, state)
    collections = _names(args.get("bone_collections"), "bone_collections", 64, warnings, state)
    contact_bones = _names(args.get("contact_bones"), "contact_bones", common.MAX_CONTACT_BONES, warnings, state)
    root_bone = args.get("root_bone")
    if root_bone is not None:
        if not isinstance(root_bone, str) or not root_bone.strip():
            common.fail("animation_input_invalid", "input", "root_bone 必须是字符串", "root_bone 传骨骼名")
        root_bone = root_bone.strip()
    needs_armature = bool(bones or chains or collections or contact_bones or root_bone)
    if needs_armature and not armature:
        common.fail("animation_input_invalid", "input",
                    "bones/bone_chains/bone_collections/contact_bones/root_bone 需要同时提供 armature",
                    "传入 armature 对象名；对象级运动分析只给 objects")
    bone_filter = args.get("bone_filter", common.DEFAULT_BONE_FILTER)
    if bone_filter not in common.BONE_FILTERS:
        common.fail("animation_input_invalid", "input", "bone_filter=%r 非法" % (bone_filter,),
                    "bone_filter 只能用 deform 或 all")
    contact_point = args.get("contact_point", common.DEFAULT_CONTACT_POINT)
    if contact_point not in common.CONTACT_POINTS:
        common.fail("animation_input_invalid", "input", "contact_point=%r 非法" % (contact_point,),
                    "contact_point 只能用 head/tail/center")
    ground_z = _finite(args.get("ground_z", common.DEFAULT_GROUND_Z), "ground_z")
    epsilon = _finite(args.get("contact_epsilon", common.DEFAULT_CONTACT_EPSILON), "contact_epsilon")
    if epsilon <= 0.0:
        common.fail("animation_input_invalid", "input", "contact_epsilon 必须为正", "contact_epsilon 传米制正数（默认 0.01）")
    frame_start = args.get("frame_start")
    frame_end = args.get("frame_end")
    for field, value in (("frame_start", frame_start), ("frame_end", frame_end)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            common.fail("animation_input_invalid", "input", "%s 必须是整数帧号" % field, "帧号传整数")
    if frame_start is not None and frame_end is not None and frame_start > frame_end:
        common.fail("animation_input_invalid", "input", "frame_start=%d 大于 frame_end=%d" % (frame_start, frame_end),
                    "交换 frame_start/frame_end")
    frame_step = args.get("frame_step")
    if frame_step is not None and (isinstance(frame_step, bool) or not isinstance(frame_step, int) or frame_step <= 0):
        common.fail("animation_input_invalid", "input", "frame_step 必须是正整数", "frame_step 传正整数或省略")
    intersection_raw = _names(args.get("intersection_objects"), "intersection_objects",
                              common.MAX_INTERSECTION_OBJECTS, warnings, state)
    if intersection_raw and len(intersection_raw) < 2:
        common.fail("animation_input_invalid", "input", "intersection_objects 至少需要 2 个对象",
                    "给 2..16 个对象；只做单对象分析时省略该参数")
    reference = None
    reference_fps = None
    if args.get("reference") is not None:
        reference = validate_reference(args.get("reference"))
        reference_fps = float(args["reference"]["fps"])
    if not objects and not armature:
        common.fail("animation_target_missing", "input", "至少需要 objects 或 armature",
                    "给 objects=[对象名] 或 armature=骨架对象名")
    include_inventory = args.get("include_bone_inventory", False)
    if not isinstance(include_inventory, bool):
        common.fail("animation_input_invalid", "input", "include_bone_inventory 必须是布尔值",
                    "include_bone_inventory 传 true/false")
    return {
        "objects": objects,
        "armature": armature,
        "bones": bones,
        "bone_chains": chains,
        "bone_collections": collections,
        "bone_filter": bone_filter,
        "contact_bones": contact_bones,
        "contact_point": contact_point,
        "ground_z": ground_z,
        "contact_epsilon": epsilon,
        "root_bone": root_bone,
        "intersection_objects": intersection_raw,
        "reference": reference,
        "reference_fps": reference_fps,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "frame_step": frame_step,
        "include_bone_inventory": include_inventory,
        "warnings": warnings,
        "truncated": state["truncated"],
    }
