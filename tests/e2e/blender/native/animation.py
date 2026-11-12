# -*- coding: utf-8 -*-
"""Native E2E: animation_report / animation_selftest / animation_help (T7).

Runs inside Blender (native launcher). Covers: evaluated-depsgraph sampling,
root motion, explicit bone chains, ground contact with penetration, mesh
intersections, exact-frame reference matching, effective_fps, frame restore,
structured input errors and selftest cleanup. Every check also reports through
ctx.check so the launcher sees the individual verdicts.
"""

import bpy
import importlib
import json
import math
import os
import sys

FAILS = []
OKS = []
_CTX = None


def check(name, cond, detail=""):
    ok = bool(cond)
    if _CTX is not None:
        _CTX.check(name, ok, detail)
    (OKS if cond else FAILS).append({"name": name, "detail": detail if not cond else ""})
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + json.dumps(detail, ensure_ascii=False)[:400]))


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def _load_api():
    """Use the preloaded kernel API, else boot kit.py + import the facade in place."""
    K = sys.modules.get("dsh_rt_kernel")
    if K is not None and hasattr(K, "dsh_animation_api"):
        return K, K.dsh_animation_api
    import types
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    if not hasattr(K, "dsh_kit"):
        kit = os.path.join(_repo_root(), "runtime", "py", "core", "kit.py")
        with open(kit, encoding="utf-8") as fh:
            exec(compile(fh.read(), kit, "exec"), K.__dict__)
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    importlib.import_module("runtime.py.animation.animation")
    return K, K.dsh_animation_api


def _mk_box(name, size, loc):
    sx, sy, sz = [float(x) for x in size]
    me = bpy.data.meshes.new("MSH_" + name)
    v = [(-sx / 2, -sy / 2, -sz / 2), (sx / 2, -sy / 2, -sz / 2), (sx / 2, sy / 2, -sz / 2), (-sx / 2, sy / 2, -sz / 2),
         (-sx / 2, -sy / 2, sz / 2), (sx / 2, -sy / 2, sz / 2), (sx / 2, sy / 2, sz / 2), (-sx / 2, sy / 2, sz / 2)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me.from_pydata(v, [], f)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = [float(x) for x in loc]
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _action_fcurves(act):
    """Blender 4.4+ layered actions hide `Action.fcurves`; walk the channelbags instead."""
    curves = getattr(act, "fcurves", None)
    if curves is not None:
        return list(curves)
    out = []
    for layer in getattr(act, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out


def _linear(obj):
    ad = getattr(obj, "animation_data", None)
    act = getattr(ad, "action", None) if ad else None
    if act is None:
        return
    for fc in _action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"


def _cleanup(objects, arms):
    for ob in objects:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for arm in arms:
        try:
            if arm.users == 0:
                bpy.data.armatures.remove(arm)
        except Exception:
            pass


def _close(a, b, tol=1e-6):
    return a is not None and abs(float(a) - float(b)) <= tol


def test_animation(ctx):
    global _CTX
    _CTX = ctx
    K, api = _load_api()
    print("ANIMATION_SELFTEST version=%s api=%s" % (api["version"], sorted(api)))

    scene = bpy.context.scene
    previous_frame = int(scene.frame_current)
    previous_subframe = float(scene.frame_subframe)
    previous_active = bpy.context.view_layer.objects.active
    previous_fps = int(scene.render.fps)
    previous_base = float(scene.render.fps_base)
    previous_range = (int(scene.frame_start), int(scene.frame_end))
    made, arms = [], []
    try:
        scene.render.fps = 24
        scene.render.fps_base = 1.0
        scene.frame_start = 1
        scene.frame_end = 25
        scene.frame_set(1)

        # ---- help: generic option discovery, no rig-profile examples
        h = api["help"]()
        check("animation_help ok + ops/units/limits",
              h.get("ok") is True and h.get("ops") == ["animation_report", "animation_selftest", "animation_help"]
              and (h.get("units") or {}).get("length") == "m"
              and (h.get("limits") or {}).get("samples") == 240,
              {"ops": h.get("ops"), "units": h.get("units"), "limits": h.get("limits")})
        check("animation_help 不出现角色/绑定规范示例",
              not any(token in json.dumps(h, ensure_ascii=False) for token in ("Rigify", "MCH-", "DEF-", "ORG-", "CTRL-")),
              {"hint": h.get("selection")})

        check("animation_help 错误列表与实际抛出的 code 一致",
              "animation_budget_exceeded" not in (h.get("errors") or [])
              and "animation_no_data" in (h.get("errors") or [])
              and bool((h.get("error_stages") or {}).get("input"))
              and "intersection_non_mesh" in (h.get("warnings") or []),
              {"errors": h.get("errors"), "warnings": h.get("warnings")})

        # ---- object-level root motion over a linear 2 m move
        cube = _mk_box("ANM_Cube", (0.2, 0.2, 0.2), (0.0, 0.0, 0.0))
        made.append(cube)
        cube.keyframe_insert("location", frame=1)
        cube.location = (2.0, 0.0, 0.0)
        cube.keyframe_insert("location", frame=25)
        _linear(cube)
        bpy.context.view_layer.update()

        scene.frame_set(7, subframe=0.5)
        r = api["report"](objects=["ANM_Cube"], frame_start=1, frame_end=25, frame_step=1)
        root = (r.get("root_motion") or {}).get("ANM_Cube") or {}
        sampling = r.get("sampling") or {}
        check("report ok + 25 帧闭区间采样", r.get("ok") is True and sampling.get("sample_count") == 25
              and (sampling.get("actual") or {}).get("frame_start") == 1
              and (sampling.get("actual") or {}).get("frame_end") == 25,
              {"sampling": sampling, "code": r.get("code")})
        check("对象 root motion 位移/路径/速度", _close(root.get("displacement_m"), 2.0, 1e-4)
              and _close(root.get("path_length_m"), 2.0, 1e-4)
              and _close(root.get("max_speed_m_s"), 2.0, 1e-4)
              and _close((root.get("displacement_vector_m") or [0, 0, 0])[0], 2.0, 1e-4),
              {"root_motion": root})
        check("静态旋转不虚构角速度", root.get("rotation_delta_deg") is None or _close(root.get("rotation_delta_deg"), 0.0, 1e-6),
              {"rotation_delta_deg": root.get("rotation_delta_deg")})
        check("finally 恢复 frame_current + frame_subframe",
              int(scene.frame_current) == 7 and abs(float(scene.frame_subframe) - 0.5) < 1e-6,
              {"frame": int(scene.frame_current), "subframe": float(scene.frame_subframe)})

        # ---- frame_step=2: rates use dt = step / effective_fps, never 1/fps
        r2 = api["report"](objects=["ANM_Cube"], frame_start=1, frame_end=25, frame_step=2)
        root2 = (r2.get("root_motion") or {}).get("ANM_Cube") or {}
        check("frame_step=2 速度/加速度按 dt 换算（不是 2×/4× 偏大）",
              r2.get("ok") is True and (r2.get("sampling") or {}).get("frame_step") == 2
              and _close(root2.get("displacement_m"), 2.0, 1e-4)
              and _close(root2.get("max_speed_m_s"), 2.0, 1e-4)
              and _close(root2.get("max_accel_m_s2"), 0.0, 1e-4),
              {"root_motion": root2, "sampling": r2.get("sampling"), "code": r2.get("code")})

        # ---- reference: exact frame numbers, never nearest-neighbour
        ref = {"version": 1, "fps": 24.0, "frames": [
            {"frame": 1, "objects": {"ANM_Cube": [0.0, 0.0, 0.0]}},
            {"frame": 13, "objects": {"ANM_Cube": [1.0, 0.0, 0.0]}},
            {"frame": 25, "objects": {"ANM_Cube": [2.0, 0.0, 0.0]}},
        ]}
        rr = api["report"](objects=["ANM_Cube"], frame_start=1, frame_end=25, frame_step=1, reference=ref)
        traj = ((rr.get("trajectory") or {}).get("ANM_Cube") or {})
        check("reference 精确帧匹配（3 matched / 22 unused）",
              rr.get("ok") is True and traj.get("matched") == 3 and traj.get("missing") == 0
              and traj.get("unused") == 22 and _close(traj.get("rmse_m"), 0.0, 1e-6),
              {"trajectory": traj, "code": rr.get("code")})

        # ---- armature: explicit chain, root 90 deg, child follows its parent
        bpy.ops.object.select_all(action="DESELECT")
        arm_data = bpy.data.armatures.new("ANM_RigData")
        arm = bpy.data.objects.new("ANM_Rig", arm_data)
        scene.collection.objects.link(arm)
        made.append(arm)
        arms.append(arm_data)
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        root_bone = arm_data.edit_bones.new("root")
        root_bone.head = (0.0, 0.0, 0.3)
        root_bone.tail = (0.0, 0.0, 0.1)
        child = arm_data.edit_bones.new("child")
        child.head = (0.0, 0.0, 0.1)
        child.tail = (0.0, 0.0, -0.02)
        child.parent = root_bone
        child.use_connect = True
        bpy.ops.object.mode_set(mode="OBJECT")
        pose = arm.pose.bones["root"]
        pose.rotation_mode = "XYZ"
        scene.frame_set(1)
        pose.rotation_euler = (0.0, 0.0, 0.0)
        pose.keyframe_insert("rotation_euler", frame=1)
        scene.frame_set(13)
        pose.rotation_euler = (0.0, 0.0, math.radians(90.0))
        pose.keyframe_insert("rotation_euler", frame=13)
        _linear(arm)
        bpy.context.view_layer.update()

        ra = api["report"](armature="ANM_Rig", bone_chains=[["root", "child"]],
                           contact_bones=["child"], contact_point="tail",
                           root_bone="root", frame_start=1, frame_end=13, frame_step=1)
        joints = {row.get("name"): row for row in (ra.get("joints") or [])}
        contacts = ra.get("contacts") or []
        root_rows = ra.get("root_motion") or {}
        check("显式骨链：root 关节 90°，child 局部角 ≈0",
              ra.get("ok") is True
              and _close((joints.get("root") or {}).get("max_deg"), 90.0, 0.5)
              and _close((joints.get("child") or {}).get("max_deg"), 0.0, 0.5),
              {"joints": joints, "code": ra.get("code")})
        # The root rotates about its own local Z, so the child tail swings up and leaves the
        # contact band at frame 4 (z = +0.004 -> +0.023 > epsilon 0.01). Contact therefore
        # covers frames 1..4 with the -0.02 m penetration still counted as contact.
        first_interval = (contacts[0].get("intervals") or [{}])[0] if contacts else {}
        check("contact 有符号判定：穿透 -0.02 m 仍算接触",
              len(contacts) == 1 and contacts[0].get("contact_samples") >= 4
              and first_interval.get("start_frame") == 1
              and _close(contacts[0].get("max_penetration_m"), 0.02, 1e-4),
              {"contacts": contacts})
        check("root_bone 世界旋转 90°（对象位移为 0）",
              _close((root_rows.get("root") or {}).get("rotation_delta_deg"), 90.0, 0.5)
              and _close((root_rows.get("root") or {}).get("displacement_m"), 0.0, 1e-6),
              {"root_motion": root_rows})
        # A separate call without bone_chains: joints must be empty AND explain itself,
        # never fall back to Bone.parent (the old assertion `or bool(joints)` was vacuously
        # true because the call above passed bone_chains).
        rn = api["report"](armature="ANM_Rig", bones=["root", "child"],
                           frame_start=1, frame_end=13, frame_step=1)
        check("无 bone_chains 时 joints == [] 且带 warning",
              rn.get("ok") is True and (rn.get("joints") or []) == []
              and any(w.get("code") in ("no_bone_chains", "joints_empty") for w in (rn.get("warnings") or [])),
              {"joints": rn.get("joints"), "warnings": [w.get("code") for w in (rn.get("warnings") or [])],
               "code": rn.get("code")})

        # ---- constraint-driven final pose: the channel says 90 deg, LIMIT_ROTATION clamps it
        # to 45 deg, and the joint metric must report the evaluated 45 deg, not the channel.
        limit = arm.pose.bones["root"].constraints.new("LIMIT_ROTATION")
        # Owner space defaults to WORLD, where the bone's twist about its own axis is not the
        # Z rotation the limit talks about; LOCAL is what a rigger's limit constraint means here.
        limit.owner_space = "LOCAL"
        limit.use_limit_z = True
        limit.min_z = 0.0
        limit.max_z = math.radians(45.0)
        bpy.context.view_layer.update()
        rcons = api["report"](armature="ANM_Rig", bone_chains=[["root", "child"]],
                              frame_start=13, frame_end=13, frame_step=1)
        joint_cons = {row.get("name"): row for row in (rcons.get("joints") or [])}
        check("约束驱动的最终姿态被测量（channel 90° → 实际 45°）",
              rcons.get("ok") is True
              and _close((joint_cons.get("root") or {}).get("max_deg"), 45.0, 1.0),
              {"joints": joint_cons, "code": rcons.get("code")})
        arm.pose.bones["root"].constraints.remove(limit)
        bpy.context.view_layer.update()

        # ---- intersections: two overlapping static meshes, measured every frame
        hit_a = _mk_box("ANM_HitA", (0.5, 0.5, 0.5), (0.0, 0.0, 0.0))
        hit_b = _mk_box("ANM_HitB", (0.5, 0.5, 0.5), (0.1, 0.0, 0.0))
        made.extend([hit_a, hit_b])
        non_mesh = bpy.data.objects.new("ANM_Empty", None)
        scene.collection.objects.link(non_mesh)
        made.append(non_mesh)
        bpy.context.view_layer.update()
        ri = api["report"](objects=["ANM_Cube"], intersection_objects=["ANM_HitA", "ANM_HitB"],
                           frame_start=1, frame_end=13, frame_step=1)
        inter = ri.get("intersections") or {}
        pairs = inter.get("pairs") or []
        check("intersections 表面重叠 measured + 每帧命中",
              ri.get("ok") is True and inter.get("status") == "measured"
              and len(pairs) == 1 and len(pairs[0].get("overlap_frames") or []) == 13
              and inter.get("sample_frames") == 13,
              {"intersections": inter, "code": ri.get("code")})

        # ---- non-mesh intersection objects: dropped once, not "this frame has no overlap"
        rnm = api["report"](objects=["ANM_Cube"],
                            intersection_objects=["ANM_HitA", "ANM_HitB", "ANM_Empty"],
                            frame_start=1, frame_end=13, frame_step=1)
        inter_nm = rnm.get("intersections") or {}
        non_mesh_warnings = [w for w in (rnm.get("warnings") or []) if w.get("code") == "intersection_non_mesh"]
        check("非 mesh 穿插对象：一条 warning + 仍按 2 个 mesh 测量",
              rnm.get("ok") is True and inter_nm.get("status") == "measured"
              and inter_nm.get("non_mesh") == ["ANM_Empty"]
              and len(non_mesh_warnings) == 1 and len(inter_nm.get("pairs") or []) == 1
              and rnm.get("verdict") == "degraded",
              {"intersections": inter_nm, "warnings": [w.get("code") for w in (rnm.get("warnings") or [])],
               "verdict": rnm.get("verdict")})
        r1m = api["report"](objects=["ANM_Cube"], intersection_objects=["ANM_HitA", "ANM_Empty"],
                            frame_start=1, frame_end=3, frame_step=1)
        check("可用 mesh < 2 → intersections status=skipped + degraded",
              r1m.get("ok") is True and (r1m.get("intersections") or {}).get("status") == "skipped"
              and (r1m.get("intersections") or {}).get("pairs") == []
              and r1m.get("verdict") == "degraded",
              {"intersections": r1m.get("intersections"), "verdict": r1m.get("verdict")})

        # ---- triangle budget: 5 subdivided cubes are ~246k triangles per frame (> 200k),
        # so every sampled frame is skipped and the receipt is degraded, not "no overlap".
        budget_objs = []
        for idx in range(5):
            ob = _mk_box("ANM_Budget%d" % idx, (0.2, 0.2, 0.2), (0.05 * idx, 0.0, 0.0))
            mod = ob.modifiers.new("Subsurf", "SUBSURF")
            mod.levels = 6
            mod.render_levels = 6
            budget_objs.append(ob)
        made.extend(budget_objs)
        bpy.context.view_layer.update()
        rbud = api["report"](objects=["ANM_Cube"], intersection_objects=[ob.name for ob in budget_objs],
                             frame_start=1, frame_end=3, frame_step=1)
        inter_bud = rbud.get("intersections") or {}
        check("每帧三角形超 20 万 → 跳帧 skipped + degraded",
              rbud.get("ok") is True and inter_bud.get("status") == "skipped"
              and len(inter_bud.get("skipped_frames") or []) == 3
              and rbud.get("verdict") == "degraded" and rbud.get("truncated") is True,
              {"intersections": inter_bud, "verdict": rbud.get("verdict"), "code": rbud.get("code")})

        # ---- effective_fps = render.fps / render.fps_base
        scene.render.fps_base = 1.001
        rf = api["report"](objects=["ANM_Cube"], frame_start=1, frame_end=5, frame_step=1)
        eff = ((rf.get("sampling") or {}).get("effective_fps"))
        check("effective_fps 23.976（fps=24, fps_base=1.001）",
              # fps_base round-trips through a float32 (1.001 -> 1.0009999), so compare with a
              # tolerance that survives that quantisation instead of an exact division.
              rf.get("ok") is True and _close(eff, 24.0 / 1.001, 1e-3),
              {"effective_fps": eff, "scene": rf.get("scene")})
        scene.render.fps_base = 1.0

        # ---- structured error for a missing target: stage=input, scene state untouched
        scene.frame_set(7, subframe=0.5)
        re_ = api["report"](objects=["ANM_Missing"])
        check("缺对象 → animation_target_missing(stage=input)",
              re_.get("ok") is False and re_.get("code") == "animation_target_missing"
              and re_.get("stage") == "input" and bool(re_.get("fix"))
              and int(scene.frame_current) == 7 and abs(float(scene.frame_subframe) - 0.5) < 1e-6,
              {"code": re_.get("code"), "stage": re_.get("stage"),
               "frame": int(scene.frame_current), "subframe": float(scene.frame_subframe)})

        # ---- failure inside the sampling loop still restores frame + subframe
        import runtime.py.animation.animation_impl.report as report_mod

        def _boom(*_args, **_kwargs):
            raise RuntimeError("injected sampling failure")

        original_to_vec = report_mod._to_vec
        report_mod._to_vec = _boom
        boom = None
        try:
            scene.frame_set(7, subframe=0.5)
            boom = api["report"](objects=["ANM_Cube"], frame_start=1, frame_end=25, frame_step=1)
        finally:
            report_mod._to_vec = original_to_vec
        check("采样中抛错 → animation_unsupported + frame/subframe 已恢复",
              (boom or {}).get("ok") is False and (boom or {}).get("code") == "animation_unsupported"
              and int(scene.frame_current) == 7 and abs(float(scene.frame_subframe) - 0.5) < 1e-6,
              {"code": (boom or {}).get("code"), "frame": int(scene.frame_current),
               "subframe": float(scene.frame_subframe)})

        # ---- selftest: builds, verifies, cleans up objects, meshes, actions and frames
        before_objects = {ob.name for ob in bpy.data.objects}
        before_meshes = {mesh.name for mesh in bpy.data.meshes}
        before_actions = {act.name for act in bpy.data.actions}
        rs = api["selftest"]()
        after_objects = {ob.name for ob in bpy.data.objects}
        after_meshes = {mesh.name for mesh in bpy.data.meshes}
        after_actions = {act.name for act in bpy.data.actions}
        ev = rs.get("evidence") or {}
        check("animation_selftest ok + 对象/mesh/action 清理干净 + 帧/subframe 恢复",
              rs.get("ok") is True and after_objects == before_objects
              and after_meshes == before_meshes and after_actions == before_actions
              and _close(ev.get("object_displacement_m"), 1.0, 0.05)
              and _close(ev.get("child_joint_max_deg"), 0.0, 1.0)
              and ev.get("frame_restored") is True,
              {"evidence": ev, "leftover_objects": sorted(after_objects - before_objects),
               "leftover_meshes": sorted(after_meshes - before_meshes),
               "leftover_actions": sorted(after_actions - before_actions), "code": rs.get("code")})

        check("native 汇总无失败", not FAILS, {"fails": FAILS[:8]})
    finally:
        _cleanup(made, arms)
        try:
            scene.frame_set(previous_frame, subframe=previous_subframe)
        except Exception:
            pass
        try:
            scene.render.fps = previous_fps
            scene.render.fps_base = previous_base
            scene.frame_start, scene.frame_end = previous_range
            bpy.context.view_layer.objects.active = previous_active
        except Exception:
            pass
