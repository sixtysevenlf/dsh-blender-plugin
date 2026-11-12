# -*- coding: utf-8 -*-
"""animation_help: generic option discovery, no rig-profile or role inference."""
from . import common


def _method_notes():
    """Per-metric method/limits notes, kept out of the receipt-literal function."""
    return {
        "root_motion": "path_length_m/displacement_m/displacement_vector_m/max_speed_m_s/max_accel_m_s2/"
                       "loop_closure_m/rotation_delta_deg（最短弧）/rotation_path_deg（逐步累计）；"
                       "loop_closure_m 与 displacement_m 是同一个量（起点到终点距离），两个键保留仅为兼容",
        "joints": "pose_local=parent_pose^-1@pose；rest_local=parent_rest^-1@rest；"
                  "delta=orthonormalize(pose_local3@rest_local3^-1)；角度取 delta 四元数角；"
                  "角速度/角加速度用相邻姿态角差/dt（dt=frame_step/effective_fps）；"
                  "链上相邻骨不是 Bone.parent 时带 chain_parent_not_bone_parent warning",
        "contacts": "contact_point 世界坐标与地平面做有符号比较；返回区间/contact_samples/horizontal_slide_m/"
                    "max_slide_speed_m_s/max_penetration_m；step>1 时是采样近似；截断后 contact_samples 与"
                    "max_penetration_m 按保留的区间重算；contacts 为空时带 no_contact_in_band warning 与"
                    "ground_z/contact_epsilon",
        "intersections": "先解析 intersection_objects：非 mesh 对象一次性丢弃并带一条 intersection_non_mesh warning，"
                         "可用 mesh 少于 2 个时 status=skipped；evaluated mesh 世界顶点 + BVHTree 表面三角重叠；"
                         "只报表面相交，不承诺实体包含/穿透深度；超预算跳帧记 skipped_frames（degraded/skipped）；"
                         "pairs_total/pairs_truncated 标注 64 对截断，non_mesh 列出被丢弃对象",
        "reference": "只按精确帧号匹配，不最近邻；缺失帧不按 0；返回 RMSE/max/matched/missing/unused；"
                     "reference 里的对象没参与采样时带 reference_object_unused warning",
    }


def animation_help(**kwargs):
    return {
        "ok": True,
        "module": "animation",
        "version": common.ANIMATION_VERSION,
        "what": "动画评估：按 evaluated depsgraph 采样物体/骨骼运动，给 root motion、显式关节链、接触/脚滑、"
                "网格穿插与可选参考轨迹；不解析修改器/GN/粒子参数，不做角色或绑定规范推断。",
        "ops": ["animation_report", "animation_selftest", "animation_help"],
        "params": {
            "objects": "最多 16 个对象名，采对象级 root motion 与穿插",
            "armature": "骨架对象名；bones/bone_chains/bone_collections/contact_bones/root_bone 需要它",
            "bones": "最多 64 根骨名",
            "bone_chains": '显式关节链，例如 [["upper_arm","forearm"]]；不从 Bone.parent 自动推导',
            "bone_collections": "按骨骼 collection 选骨（Blender 骨骼集合）",
            "bone_filter": "deform（默认，仅 use_deform）或 all",
            "include_bone_inventory": "true 时返回每个 collection 的骨数/变形骨/非变形骨/mixed/sample_bones",
            "contact_bones": "最多 16 根接触骨",
            "contact_point": "head/tail/center，默认 tail",
            "ground_z": "地平面高度（米），默认 0.0",
            "contact_epsilon": "接触容差带（米），默认 0.01；有符号判定 z <= ground_z + epsilon",
            "frame_start": "闭区间起始帧；省略用场景 frame_start",
            "frame_end": "闭区间结束帧；省略用场景 frame_end",
            "frame_step": "正整数；省略时取最小整数 step 使采样数 <= 240",
            "root_bone": "用该骨的最终世界位置作为 root motion 来源（默认用骨架对象世界位移）",
            "intersection_objects": "2..16 个 mesh 对象；非 mesh 对象被跳过并带 warning；每采样帧总三角形预算 20 万",
            "reference": "可选 {version:1, fps, frames:[{frame, objects:{name:[x,y,z]}}]}，最多 240 帧/2 MiB",
        },
        "sampling": {
            "effective_fps": "scene.render.fps / scene.render.fps_base",
            "rate": "速度/角速度/滑移速度用采样间隔 dt = frame_step / effective_fps；frame_step>1 时不能除以 1/fps",
            "max_samples": common.MAX_SAMPLES,
            "closure": "frame_start..frame_end 闭区间",
            "data_source": "逐帧 frame_set 后读 evaluated depsgraph 的 Object.matrix_world 与 PoseBone.matrix",
            "not_persistent": "finally 用 frame_set(frame, subframe=...) 同时恢复 frame_current/frame_subframe；"
                              "不改关键帧/pose/材质，不保存 .blend",
        },
        "units": dict(common.UNITS),
        "limits": {
            "objects": common.MAX_OBJECTS,
            "bones": common.MAX_BONES,
            "contact_bones": common.MAX_CONTACT_BONES,
            "intersection_objects": common.MAX_INTERSECTION_OBJECTS,
            "samples": common.MAX_SAMPLES,
            "contact_intervals": common.MAX_CONTACT_INTERVALS,
            "intersection_pairs": common.MAX_INTERSECTION_PAIRS,
            "reference_frames": common.MAX_REFERENCE_FRAMES,
            "triangle_budget_per_frame": common.TRIANGLE_BUDGET,
        },
        "method": _method_notes(),
        "selection": {
            "priority": ["显式 bones/bone_chains", "bone_collections", "bone_filter"],
            "hint": "先确认对象/骨架名与帧范围；需要关节分析时用 bone_chains 明确给出链，避免把父骨旋转误当子骨局部角。",
        },
        "errors": ["animation_input_invalid", "animation_sampling_invalid", "animation_target_missing",
                   "animation_reference_invalid", "animation_no_data", "animation_unsupported"],
        "error_stages": {
            "input": "参数非法、对象/collection 名不存在、armature 不是 ARMATURE 类型",
            "scene": "对象已解析但 armature 内找不到给的名字（骨名、root_bone、contact_bones）",
            "animation": "没有动画数据，或运行时/API 不支持",
        },
        "warnings": ["static", "truncated", "no_armature", "no_bones_selected", "no_bone_chains", "joints_empty",
                     "deform_flag_uninformative", "chain_parent_not_bone_parent", "rotation_shortest_arc",
                     "no_contact_in_band", "intersection_non_mesh", "intersection_insufficient_meshes",
                     "intersection_pairs_truncated", "intersection_degraded", "intersection_skipped",
                     "reference_missing_frames", "reference_object_unused"],
        "verdict": "pass/degraded/fail；requested 但没测到的部分（static、截断、intersections 跳过/非 mesh、"
                   "reference 缺失或未采样、未提供 armature、use_deform 不可区分）一律归 degraded，不冒充 pass",
        "kernel": "共享内核 K.dsh_kit v%s" % getattr(__import__("sys").modules.get("dsh_rt_kernel") and
                                                    getattr(__import__("sys").modules.get("dsh_rt_kernel"), "dsh_kit", None) or
                                                    object(), "version", "?"),
    }
