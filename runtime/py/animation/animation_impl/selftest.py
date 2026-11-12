# -*- coding: utf-8 -*-
"""animation_selftest: build a temporary animated object + armature, verify, then clean up."""
from . import common, report


def _object_action(obj):
    animation_data = getattr(obj, "animation_data", None)
    return getattr(animation_data, "action", None) if animation_data is not None else None


def _cleanup(made_objects, made_meshes, made_armatures, made_collections, made_actions):
    import bpy
    for obj in made_objects:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass
    for mesh in made_meshes:
        try:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        except Exception:
            pass
    for armature in made_armatures:
        try:
            if armature.users == 0:
                bpy.data.armatures.remove(armature)
        except Exception:
            pass
    for collection in made_collections:
        try:
            bpy.data.collections.remove(collection)
        except Exception:
            pass
    # Actions are removed last: the owning objects had to go first for `users` to reach 0.
    for action in made_actions:
        try:
            if getattr(action, "users", 0) == 0:
                bpy.data.actions.remove(action)
        except Exception:
            pass


def animation_selftest(**kwargs):
    import bpy
    scene = bpy.context.scene
    previous_frame = int(scene.frame_current)
    previous_subframe = float(scene.frame_subframe)
    previous_active = bpy.context.view_layer.objects.active
    made_objects, made_meshes, made_armatures, made_collections, made_actions = [], [], [], [], []
    try:
        collection = bpy.data.collections.new("DSH_Animation_Selftest")
        scene.collection.children.link(collection)
        made_collections.append(collection)

        bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0.0, 0.0, 0.0))
        cube = bpy.context.object
        cube.name = "DSH_Anim_Cube"
        for owner in list(cube.users_collection):
            owner.objects.unlink(cube)
        collection.objects.link(cube)
        made_objects.append(cube)
        made_meshes.append(cube.data)
        cube.keyframe_insert("location", frame=1)
        cube.location = (1.0, 0.0, 0.0)
        cube.keyframe_insert("location", frame=11)

        bpy.ops.object.armature_add(enter_editmode=False, location=(0.0, 0.0, 2.0))
        arm = bpy.context.object
        arm.name = "DSH_Anim_Rig"
        for owner in list(arm.users_collection):
            owner.objects.unlink(arm)
        collection.objects.link(arm)
        made_objects.append(arm)
        made_armatures.append(arm.data)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = arm.data.edit_bones
        root = edit_bones[0]
        root.name = "root"
        child = edit_bones.new("child")
        child.head = root.tail
        child.tail = root.tail + __import__("mathutils").Vector((0.0, 0.0, -0.5))
        child.parent = root
        child.use_connect = False
        bpy.ops.object.mode_set(mode="OBJECT")
        pose_bone = arm.pose.bones["root"]
        pose_bone.rotation_mode = "XYZ"
        pose_bone.rotation_euler = (0.0, 0.0, 0.0)
        pose_bone.keyframe_insert("rotation_euler", frame=1)
        pose_bone.rotation_euler = (0.0, 0.0, 1.0)
        pose_bone.keyframe_insert("rotation_euler", frame=11)

        receipt = report.animation_report(objects=["DSH_Anim_Cube"], armature="DSH_Anim_Rig",
                                          bones=["root", "child"], bone_chains=[["root", "child"]],
                                          frame_start=1, frame_end=11)
        root_motion = (receipt.get("root_motion") or {}).get("DSH_Anim_Cube") or {}
        joints = {row.get("name"): row for row in receipt.get("joints") or []}
        displacement = root_motion.get("displacement_m")
        child_angle = (joints.get("child") or {}).get("max_deg")
        evidence = {
            "receipt_ok": receipt.get("ok") is True,
            "object_displacement_m": displacement,
            "child_joint_max_deg": child_angle,
            "sample_count": (receipt.get("sampling") or {}).get("sample_count"),
            "frame_restored": (int(scene.frame_current) == previous_frame
                               and abs(float(scene.frame_subframe) - previous_subframe) < 1e-6),
        }
        ok = bool(evidence["receipt_ok"]
                  and displacement is not None and abs(float(displacement) - 1.0) < 0.05
                  and child_angle is not None and abs(float(child_angle)) < 1.0
                  and evidence["frame_restored"])
        return {"ok": ok, "module": "animation", "action": "animation_selftest",
                "version": common.ANIMATION_VERSION, "units": dict(common.UNITS), "evidence": evidence}
    finally:
        try:
            if getattr(bpy.context, "mode", "OBJECT") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        # The keyframe_insert calls above created two Actions; `_cleanup` removes them
        # after their objects, so bpy.data.actions returns to its pre-call content.
        for obj in made_objects:
            action = _object_action(obj)
            if action is not None and action not in made_actions:
                made_actions.append(action)
        _cleanup(made_objects, made_meshes, made_armatures, made_collections, made_actions)
        try:
            scene.frame_set(previous_frame, subframe=previous_subframe)
        except Exception:
            pass
        try:
            bpy.context.view_layer.objects.active = previous_active
        except Exception:
            pass
