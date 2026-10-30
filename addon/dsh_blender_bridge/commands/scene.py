""" World state snapshot and scene fingerprints. """

import bpy
import mathutils
import traceback
import uuid
import zlib
import hashlib, hmac, base64
from contextlib import contextmanager, redirect_stdout, suppress

from ..protocol import MAX_SNAPSHOT_OBJECTS, MAX_SNAPSHOT_SELECTED

class SceneCommands:
    @staticmethod
    def _snapshot_geometry(obj):
        """World-space AABB + dimensions for one object, or None.

        Without these, downstream analysis cannot compute contact, containment
        or collision: `scale` alone is a multiplier on unknown base geometry.
        Uses obj.bound_box (8 cached local corners) rather than mesh vertices,
        so cost is constant per object regardless of poly count.
        """
        bound_box = getattr(obj, "bound_box", None)
        if not bound_box:
            return None
        try:
            matrix_world = obj.matrix_world
            xs, ys, zs = [], [], []
            for corner in bound_box:
                world = matrix_world @ mathutils.Vector(corner)
                xs.append(world.x)
                ys.append(world.y)
                zs.append(world.z)
            return {
                "aabb_min": [round(min(xs), 3), round(min(ys), 3), round(min(zs), 3)],
                "aabb_max": [round(max(xs), 3), round(max(ys), 3), round(max(zs), 3)],
                "dimensions": [
                    round(float(obj.dimensions.x), 3),
                    round(float(obj.dimensions.y), 3),
                    round(float(obj.dimensions.z), 3),
                ],
            }
        except Exception:
            return None

    @staticmethod
    def _snapshot_relations(obj):
        """Parent and constraint targets, so hierarchies read correctly.

        World `location` alone misreports parented objects, whose authored
        values are parent-relative.
        """
        relations = {}
        parent = getattr(obj, "parent", None)
        if parent:
            relations["parent"] = parent.name
            relations["parent_type"] = obj.parent_type
            loc = obj.matrix_local.translation
            relations["local_location"] = [
                round(float(loc.x), 3),
                round(float(loc.y), 3),
                round(float(loc.z), 3),
            ]
        constraints = []
        for constraint in getattr(obj, "constraints", None) or []:
            entry = {"type": constraint.type}
            target = getattr(constraint, "target", None)
            if target:
                entry["target"] = target.name
            constraints.append(entry)
            if len(constraints) >= 8:
                break
        if constraints:
            relations["constraints"] = constraints
        modifiers = [m.type for m in (getattr(obj, "modifiers", None) or [])[:8]]
        if modifiers:
            relations["modifiers"] = modifiers
        return relations

    @staticmethod
    def _snapshot_animation(obj):
        """Action name and per-channel keyframe summary for one object, or {}.

        Static transforms alone cannot distinguish an authored edit from
        playback landing on a different frame. Reads F-curve metadata
        (`data_path`, `array_index`, `len(keyframe_points)`) rather than
        individual keyframes, so cost stays proportional to channel count
        rather than to animation length.
        """
        try:
            anim_data = getattr(obj, "animation_data", None)
            if not anim_data:
                return {}

            animation = {}
            action = getattr(anim_data, "action", None)
            if action:
                animation["action"] = action.name
                channels = []
                total_keyframes = 0
                frame_min, frame_max = None, None
                for fcurve in action.fcurves:
                    keyframe_points = fcurve.keyframe_points
                    count = len(keyframe_points)
                    total_keyframes += count
                    if count and len(channels) < 16:
                        channels.append({
                            "data_path": fcurve.data_path,
                            "array_index": fcurve.array_index,
                            "keyframes": count,
                        })
                    if count:
                        first = keyframe_points[0].co.x
                        last = keyframe_points[-1].co.x
                        frame_min = first if frame_min is None else min(frame_min, first)
                        frame_max = last if frame_max is None else max(frame_max, last)
                if channels:
                    animation["channels"] = channels
                animation["keyframe_count"] = total_keyframes
                if frame_min is not None:
                    animation["frame_range"] = [round(float(frame_min), 3),
                                                round(float(frame_max), 3)]

            drivers = getattr(anim_data, "drivers", None)
            if drivers and len(drivers):
                animation["driver_count"] = len(drivers)

            nla_tracks = [
                track.name
                for track in (getattr(anim_data, "nla_tracks", None) or [])[:8]
            ]
            if nla_tracks:
                animation["nla_tracks"] = nla_tracks

            return {"animation": animation} if animation else {}
        except Exception:
            return {}

    @staticmethod
    def _shader_fingerprint(id_block):
        """Stable short hash of a node tree (material or world), or None.

        Node identities plus rounded input values, so tweaking a color or
        rewiring a link changes the fingerprint. Lets downstream deltas see
        shader edits that leave every object transform untouched.
        """
        try:
            if id_block is None:
                return None
            tree = id_block.node_tree if getattr(id_block, "use_nodes", False) else None
            if tree is None:
                color = getattr(id_block, "diffuse_color", None) or getattr(id_block, "color", None)
                basis = str([round(float(v), 3) for v in color]) if color is not None else ""
            else:
                parts = []
                for node in tree.nodes:
                    values = []
                    for sock in node.inputs:
                        dv = getattr(sock, "default_value", None)
                        if isinstance(dv, (int, float)):
                            values.append(round(float(dv), 3))
                        elif dv is not None:
                            with suppress(TypeError, ValueError):
                                values.extend(round(float(v), 3) for v in dv)
                    parts.append(f"{node.bl_idname}{values}")
                parts.sort()
                parts.append(str(len(tree.links)))
                basis = "|".join(parts)
            return format(zlib.crc32(basis.encode("utf-8")), "08x")
        except Exception:
            return None

    @staticmethod
    def _project_id():
        """Salted hash linking sessions on the same .blend without storing its path."""
        try:
            filepath = bpy.data.filepath
            if not filepath:
                return None
            return hashlib.sha256(f"{uuid.getnode()}:{filepath}".encode("utf-8")).hexdigest()[:16]
        except Exception:
            return None

    def get_world_state_snapshot(self):
        """Compact world-state snapshot for trajectory capture (no mesh/shader detail)."""
        try:
            scene = bpy.context.scene
            selected = [obj.name for obj in bpy.context.selected_objects]
            selected_count = len(selected)
            selected_truncated = selected_count > MAX_SNAPSHOT_SELECTED
            if selected_truncated:
                # Sorted so before/after snapshots keep the same subset.
                selected = sorted(selected)[:MAX_SNAPSHOT_SELECTED]
            objects = []

            all_objects = list(scene.objects)
            truncated = len(all_objects) > MAX_SNAPSHOT_OBJECTS
            if truncated:
                # scene.objects iterates in an order that shifts as objects are
                # created, so an arbitrary prefix would leave the before/after
                # snapshots of one step holding different subsets and the delta
                # reporting phantom adds/removes. Sorting keeps them aligned.
                all_objects = sorted(all_objects, key=lambda o: o.name)[:MAX_SNAPSHOT_OBJECTS]

            for obj in all_objects:
                materials = []
                if getattr(obj, "material_slots", None):
                    materials = [
                        slot.material.name
                        for slot in obj.material_slots
                        if slot.material
                    ]

                entry = {
                    "name": obj.name,
                    "type": obj.type,
                    "location": [
                        round(float(obj.location.x), 3),
                        round(float(obj.location.y), 3),
                        round(float(obj.location.z), 3),
                    ],
                    "rotation": [
                        round(float(obj.rotation_euler.x), 3),
                        round(float(obj.rotation_euler.y), 3),
                        round(float(obj.rotation_euler.z), 3),
                    ],
                    "scale": [
                        round(float(obj.scale.x), 3),
                        round(float(obj.scale.y), 3),
                        round(float(obj.scale.z), 3),
                    ],
                    "visible": bool(obj.visible_get()),
                    "materials": materials,
                }
                geometry = self._snapshot_geometry(obj)
                if geometry:
                    entry.update(geometry)
                entry.update(self._snapshot_relations(obj))
                entry.update(self._snapshot_animation(obj))
                data = getattr(obj, "data", None)
                if obj.type == "MESH" and data is not None:
                    entry["mesh"] = {
                        "vertices": len(data.vertices),
                        "polygons": len(data.polygons),
                    }
                objects.append(entry)

            camera = scene.camera
            camera_info = None
            if camera:
                camera_info = {
                    "name": camera.name,
                    "location": [
                        round(float(camera.location.x), 3),
                        round(float(camera.location.y), 3),
                        round(float(camera.location.z), 3),
                    ],
                    "rotation": [
                        round(float(camera.rotation_euler.x), 3),
                        round(float(camera.rotation_euler.y), 3),
                        round(float(camera.rotation_euler.z), 3),
                    ],
                }
                if camera.type == "CAMERA" and camera.data:
                    camera_info["lens"] = round(float(camera.data.lens), 3)
                    camera_info["sensor_width"] = round(float(camera.data.sensor_width), 3)

            lights = []
            for obj in scene.objects:
                if obj.type != "LIGHT":
                    continue
                light_entry = {
                    "name": obj.name,
                    "location": [
                        round(float(obj.location.x), 3),
                        round(float(obj.location.y), 3),
                        round(float(obj.location.z), 3),
                    ],
                }
                if obj.data:
                    light_entry["light_type"] = obj.data.type
                    light_entry["energy"] = round(float(obj.data.energy), 3)
                lights.append(light_entry)
                if len(lights) >= 20:
                    break

            return {
                "name": scene.name,
                "object_count": len(scene.objects),
                # Explicit, so consumers never have to infer truncation from a
                # hardcoded cap they might disagree with.
                "objects_listed": len(objects),
                "objects_truncated": truncated,
                "selected": selected,
                "selected_count": selected_count,
                "selected_truncated": selected_truncated,
                "frame_current": scene.frame_current,
                "frame_start": scene.frame_start,
                "frame_end": scene.frame_end,
                "fps": round(float(scene.render.fps) / scene.render.fps_base, 3),
                "objects": objects,
                "active_camera": camera.name if camera else None,
                "camera": camera_info,
                "lights": lights,
                "materials_count": len(bpy.data.materials),
                "material_fps": {
                    m.name: self._shader_fingerprint(m)
                    for m in list(bpy.data.materials)[:200]
                },
                "world_fp": self._shader_fingerprint(scene.world),
                "project_id": self._project_id(),
                "blender_version": bpy.app.version_string,
                "snapshot_source": "native",
            }
        except Exception as e:
            print(f"Error in get_world_state_snapshot: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}
