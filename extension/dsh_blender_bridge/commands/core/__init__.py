""" Core inspection and code-execution commands, composed from focused mixins. """

import bpy
import json
import traceback
import io
from contextlib import redirect_stdout

from ...assets.geometry import compute_aabb
from ...command_registry import COMMAND_NAMES, wire_spec_rows
from ...protocol import ADDON_NAME, ADDON_PROTOCOL_VERSION, ADDON_VERSION
from ..scene.export import SceneExportMixin
from .bpy_lookup import BpyApiLookupCommands
from .screenshot import ScreenshotCommands


class CoreCommands(SceneExportMixin, BpyApiLookupCommands, ScreenshotCommands):
    def get_addon_info(self):
        """Version/capability handshake for the DSH plugin (and install tooling)."""
        return {
            "name": ADDON_NAME,
            "addon_version": list(ADDON_VERSION),
            "protocol_version": ADDON_PROTOCOL_VERSION,
            # derive from the single command registry instead of a hardcoded
            # subset, so the handshake cannot drift from the dispatch table.
            "capabilities": sorted(COMMAND_NAMES),
            # inspect help: args / gate / pause_safe / network for every wire
            # command, so the host never keeps a second copy of this table.
            "command_specs": wire_spec_rows(),
            "blender_version": bpy.app.version_string,
        }

    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
            print("Getting scene info...")
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }

            # Collect minimal object information (limit to first 10 objects)
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:  # Reduced from 20 to 10
                    break

                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [round(float(obj.location.x), 2),
                                round(float(obj.location.y), 2),
                                round(float(obj.location.z), 2)],
                }
                scene_info["objects"].append(obj_info)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    @staticmethod
    def _get_aabb(obj):
        """World-space AABB; kept as a thin server-facing alias."""
        return compute_aabb(obj)

    def get_object_info(self, name):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box

        # Add material slots
        for slot in obj.material_slots:  # pyright: ignore[reportGeneralTypeIssues]
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        # Add mesh data if applicable
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info
    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        # This is powerful but potentially dangerous - use with caution
        try:
            # Create a local namespace for execution
            namespace = {"bpy": bpy}

            # Capture stdout during execution, and return it as result
            capture_buffer = io.StringIO()
            with redirect_stdout(capture_buffer):
                exec(code, namespace)

            captured_output = capture_buffer.getvalue()
            return {"executed": True, "result": captured_output}
        except Exception as e:
            # Give the caller the same detail we have: exception type, message,
            # and a full traceback (with line numbers into the submitted code),
            # instead of collapsing everything into one string. Callers that ran
            # a multi-line script otherwise cannot tell which line failed.
            tb = traceback.format_exc()
            raise Exception(
                json.dumps({
                    "exception_type": type(e).__name__,
                    "message": str(e),
                    "traceback": tb,
                })
            )
