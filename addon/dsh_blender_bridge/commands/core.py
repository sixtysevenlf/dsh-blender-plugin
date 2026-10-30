""" Core inspection, code execution and export commands. """

import bpy
import mathutils
import json
import traceback
import os
import io
from contextlib import contextmanager, redirect_stdout, suppress

from ..protocol import ADDON_NAME, ADDON_PROTOCOL_VERSION, ADDON_VERSION

class CoreCommands:
    def get_addon_info(self):
        """Version/capability handshake for the MCP server (and install tooling)."""
        return {
            "name": ADDON_NAME,
            "addon_version": list(ADDON_VERSION),
            "protocol_version": ADDON_PROTOCOL_VERSION,
            "capabilities": sorted([
                "get_scene_info",
                "get_world_state_snapshot",
                "get_addon_info",
                "get_object_info",
                "get_viewport_screenshot",
                "execute_code",
                "describe_node_type",
                "bpy_api_lookup",
                "drain_human_activity",
                "get_activity_state",
                "get_selection",
                "send_selection_to_dsh",
                "get_collections",
                "send_collection_to_dsh",
                "get_telemetry_consent",
                "set_telemetry_consent",
            ]),
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
        """ Returns the world-space axis-aligned bounding box (AABB) of an object. """
        if obj.type != 'MESH':
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))

        return [
            [*min_corner], [*max_corner]
        ]

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
        for slot in obj.material_slots:
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

    def get_viewport_screenshot(self, max_size=800, filepath=None, format="png"):
        """
        Capture a screenshot of the current 3D viewport and save it to the specified path.

        Parameters:
        - max_size: Maximum size in pixels for the largest dimension of the image
        - filepath: Path where to save the screenshot file
        - format: Image format (png, jpg, etc.)

        Returns success/error status
        """
        # screen.screenshot_area captures the OS window framebuffer, which is
        # all-black whenever the Blender window is not composited in the
        # foreground (the normal case when Blender is driven headless-style via
        # MCP). Render the viewport with gpu.types.GPUOffScreen.draw_view3d
        # instead, which is independent of window compositing state, and fall
        # back to the window grab if offscreen rendering is unavailable (e.g. no
        # GPU context). The response reports which path produced the image.
        try:
            if not filepath:
                return {"error": "No filepath provided"}

            area = region = space = None
            for a in bpy.context.screen.areas:
                if a.type == 'VIEW_3D':
                    area = a
                    space = a.spaces.active
                    region = next((r for r in a.regions if r.type == 'WINDOW'), None)
                    break

            if not area or region is None or space is None:
                return {"error": "No 3D viewport found"}

            method = "offscreen"
            try:
                import gpu
                import numpy as np

                r3d = space.region_3d
                src_w, src_h = region.width, region.height
                if max(src_w, src_h) > max_size:
                    s = max_size / max(src_w, src_h)
                    width, height = max(1, int(src_w * s)), max(1, int(src_h * s))
                else:
                    width, height = src_w, src_h

                offscreen = gpu.types.GPUOffScreen(width, height)
                try:
                    offscreen.draw_view3d(
                        bpy.context.scene, bpy.context.view_layer, space, region,
                        r3d.view_matrix, r3d.window_matrix, do_color_management=True,
                    )
                    buf = offscreen.texture_color.read()
                finally:
                    offscreen.free()

                buf.dimensions = width * height * 4
                pixels = np.asarray(buf, dtype=np.float32) / 255.0  # GPU buffer is 0..255

                image = bpy.data.images.new("mcp_viewport", width, height, alpha=True)
                image.pixels.foreach_set(pixels.ravel())
                image.filepath_raw = filepath
                image.file_format = format.upper()
                image.save()
                bpy.data.images.remove(image)

            except Exception as offscreen_err:
                print(f"[DSH Blender Bridge] offscreen capture failed ({offscreen_err}); "
                      "falling back to window grab", flush=True)
                method = "window_grab"
                with bpy.context.temp_override(area=area):
                    bpy.ops.screen.screenshot_area(filepath=filepath)
                img = bpy.data.images.load(filepath)
                width, height = img.size
                if max(width, height) > max_size:
                    s = max_size / max(width, height)
                    width, height = int(width * s), int(height * s)
                    img.scale(width, height)
                    img.file_format = format.upper()
                    img.save()
                bpy.data.images.remove(img)

            return {
                "success": True,
                "width": width,
                "height": height,
                "filepath": filepath,
                "method": method,
            }

        except Exception as e:
            return {"error": str(e)}

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

    @staticmethod
    def _describe_property(prop):
        """Structured description of a single bpy RNA property."""
        entry = {
            "identifier": prop.identifier,
            "name": prop.name,
            "type": prop.type,  # FLOAT, INT, BOOLEAN, STRING, ENUM, POINTER, COLLECTION
            "description": prop.description,
        }
        for attr in ("is_required", "is_readonly", "is_argument_optional", "array_length"):
            value = getattr(prop, attr, None)
            if value is not None:
                entry[attr] = value

        if prop.type == 'ENUM':
            try:
                entry["enum_items"] = [item.identifier for item in prop.enum_items]
            except Exception:
                pass
            try:
                entry["default"] = prop.default
            except Exception:
                pass
        elif prop.type in ('FLOAT', 'INT'):
            try:
                entry["default"] = (
                    list(prop.default_array) if getattr(prop, "array_length", 0) else prop.default
                )
            except Exception:
                pass
            for attr in ("hard_min", "hard_max", "soft_min", "soft_max", "subtype", "unit", "step"):
                value = getattr(prop, attr, None)
                if value is not None:
                    entry[attr] = value
        elif prop.type == 'BOOLEAN':
            try:
                entry["default"] = prop.default
            except Exception:
                pass
        elif prop.type == 'STRING':
            try:
                entry["default"] = prop.default
            except Exception:
                pass
            max_length = getattr(prop, "max_length", None)
            if max_length:
                entry["max_length"] = max_length
        elif prop.type == 'POINTER':
            fixed_type = getattr(prop, "fixed_type", None)
            if fixed_type is not None:
                entry["pointer_type"] = fixed_type.identifier
        elif prop.type == 'COLLECTION':
            fixed_type = getattr(prop, "fixed_type", None)
            if fixed_type is not None:
                entry["collection_type"] = fixed_type.identifier
        return entry

    def describe_node_type(self, bl_idname, property_overrides=None):
        """Describe a node type's properties and socket schema.

        This is the fix for the single most common failure mode: guessing
        socket names/indices and enum values instead of looking them up.
        Since a node's sockets are only known once instantiated (and can
        depend on mode-like properties, e.g. Mix's `data_type`), this
        creates a throwaway node in a scratch node tree, optionally applies
        `property_overrides` first (e.g. {"data_type": "RGBA"}) so the
        caller can see the exact socket layout for the mode they intend to
        use, then reports its properties/inputs/outputs, and finally
        deletes the scratch tree. Nothing in the user's actual scene is
        touched.
        """
        node_cls = getattr(bpy.types, bl_idname, None)
        if node_cls is None or not (isinstance(node_cls, type) and issubclass(node_cls, bpy.types.Node)):
            candidates = [
                name for name in dir(bpy.types)
                if "Node" in name and bl_idname.lower() in name.lower()
            ]
            return {
                "error": f"Unknown node type: {bl_idname}",
                "did_you_mean": sorted(candidates)[:15],
            }

        tree_type_candidates = [
            "ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree", "TextureNodeTree",
        ]
        node = None
        tree = None
        used_tree_type = None
        attempts = []
        for tree_type in tree_type_candidates:
            tmp_tree = None
            try:
                tmp_tree = bpy.data.node_groups.new(name="__mcp_introspect_tmp__", type=tree_type)
                node = tmp_tree.nodes.new(type=bl_idname)
                tree = tmp_tree
                used_tree_type = tree_type
                break
            except Exception as e:
                attempts.append(f"{tree_type}: {e}")
                if tmp_tree is not None:
                    try:
                        bpy.data.node_groups.remove(tmp_tree)
                    except Exception:
                        pass

        if node is None:
            return {
                "error": f"Could not instantiate node '{bl_idname}' in any node tree type",
                "attempts": attempts,
            }

        try:
            warnings = []
            if property_overrides:
                for key, value in property_overrides.items():
                    try:
                        setattr(node, key, value)
                    except Exception as e:
                        warnings.append(f"Could not set property '{key}' = {value!r}: {e}")

            base_props = set(bpy.types.Node.bl_rna.properties.keys())
            properties = [
                self._describe_property(prop)
                for prop in node.bl_rna.properties
                if prop.identifier not in base_props
            ]

            def describe_sockets(sockets):
                out = []
                for index, socket in enumerate(sockets):
                    entry = {
                        "index": index,
                        "identifier": socket.identifier,
                        "name": socket.name,
                        "type": socket.type,
                        "is_multi_input": getattr(socket, "is_multi_input", False),
                        "hide_value": getattr(socket, "hide_value", False),
                        "is_linked": socket.is_linked,
                    }
                    if hasattr(socket, "default_value"):
                        try:
                            default_value = socket.default_value
                            if hasattr(default_value, "__len__") and not isinstance(default_value, str):
                                entry["default_value"] = list(default_value)
                            else:
                                entry["default_value"] = default_value
                        except Exception:
                            pass
                    out.append(entry)
                return out

            result = {
                "bl_idname": bl_idname,
                "label": node.bl_label,
                "instantiated_in": used_tree_type,
                "properties": properties,
                "inputs": describe_sockets(node.inputs),
                "outputs": describe_sockets(node.outputs),
                "applied_property_overrides": property_overrides or {},
                "note": (
                    "Sockets reflect the node's current property values (after any "
                    "property_overrides applied above). Enum/mode-like properties "
                    "(e.g. data_type, blend_type) can add, remove or reorder sockets - "
                    "pass the mode you intend to use via property_overrides to see the "
                    "real layout before writing code that indexes these sockets."
                ),
            }
            if warnings:
                result["warnings"] = warnings
            return result
        finally:
            try:
                bpy.data.node_groups.remove(tree)
            except Exception:
                pass

    def bpy_api_lookup(self, query):
        """Structured RNA reference lookup: types, properties, functions, operators.

        Accepts things like:
          - "ShaderNodeTexSky" or "bpy.types.ShaderNodeTexSky"       -> full type schema
          - "ShaderNodeTexSky.sky_type"                              -> one property, with enum items
          - "Object.ray_cast"                                        -> one method's parameters/returns
          - "bpy.ops.mesh.primitive_cube_add"                        -> operator parameters
        This replaces scraping `help()` text: every answer is structured
        JSON with real type names, enum identifiers, and required/optional
        flags, not something that has to be re-parsed out of a text blob.
        """
        query = (query or "").strip()
        if not query:
            return {"error": "Empty query"}

        q = query[4:] if query.startswith("bpy.") else query

        # bpy.ops.<category>.<operator_name>
        if q.startswith("ops."):
            op_parts = q[len("ops."):].split(".")
            op_parts = [p.split("(")[0] for p in op_parts if p]
            if len(op_parts) < 2:
                return {"error": f"Incomplete operator path: bpy.{q}. Expected bpy.ops.<category>.<name>"}
            category, op_name = op_parts[0], op_parts[1]
            op_group = getattr(bpy.ops, category, None)
            op = getattr(op_group, op_name, None) if op_group is not None else None
            if op is None:
                return {"error": f"Unknown operator: bpy.ops.{category}.{op_name}"}
            try:
                rna = op.get_rna_type()
            except Exception as e:
                return {"error": f"Could not introspect operator bpy.ops.{category}.{op_name}: {e}"}
            parameters = [
                self._describe_property(prop)
                for prop in rna.properties
                if prop.identifier != "rna_type"
            ]
            return {
                "kind": "operator",
                "idname": f"bpy.ops.{category}.{op_name}",
                "label": rna.name,
                "description": rna.description,
                "parameters": parameters,
            }

        parts = [p for p in q.split(".") if p and p != "types"]
        if not parts:
            return {"error": "Empty query"}

        type_name = parts[0]
        node_cls = getattr(bpy.types, type_name, None)
        if node_cls is None:
            matches = sorted(
                name for name in dir(bpy.types)
                if type_name.lower() in name.lower()
            )
            return {
                "error": f"Unknown type: {type_name}",
                "did_you_mean": matches[:15],
            }

        if len(parts) == 1:
            properties = [
                self._describe_property(prop)
                for prop in node_cls.bl_rna.properties
                if prop.identifier != "rna_type"
            ]
            functions = []
            for func in node_cls.bl_rna.functions:
                functions.append({
                    "identifier": func.identifier,
                    "description": func.description,
                    "parameters": [
                        self._describe_property(p) for p in func.parameters if not p.is_output
                    ],
                    "returns": [
                        self._describe_property(p) for p in func.parameters if p.is_output
                    ],
                })
            return {
                "kind": "type",
                "bl_idname": type_name,
                "description": node_cls.bl_rna.description,
                "properties": properties,
                "functions": functions,
            }

        # Type.member - could be a property or a function/method
        member_name = parts[1]
        prop = node_cls.bl_rna.properties.get(member_name)
        if prop is not None:
            entry = self._describe_property(prop)
            entry["kind"] = "property"
            entry["owner_type"] = type_name
            return entry

        func = node_cls.bl_rna.functions.get(member_name)
        if func is not None:
            return {
                "kind": "function",
                "owner_type": type_name,
                "identifier": func.identifier,
                "description": func.description,
                "parameters": [self._describe_property(p) for p in func.parameters if not p.is_output],
                "returns": [self._describe_property(p) for p in func.parameters if p.is_output],
            }

        available = sorted(
            list(node_cls.bl_rna.properties.keys()) + list(node_cls.bl_rna.functions.keys())
        )
        return {
            "error": f"'{type_name}' has no property or function named '{member_name}'",
            "did_you_mean": [name for name in available if member_name.lower() in name.lower()][:15],
        }

    def export_scene(self, filepath, format="glb", object_names=None, selection_only=False, apply_modifiers=True):
        """Export the whole scene, the current selection, or the named objects to a GLB or FBX file.

        Named objects are exported together with their children. GLB carries PBR
        materials, emission, skins, shape keys and animation; FBX is the fallback for
        tools that need Unity's built-in importer. apply_modifiers=False keeps rigs
        and shape keys intact. The file is written where the caller asked, so other
        applications (game engines, viewers) can pick it up without going through
        execute_code.
        """
        if not filepath:
            return {"error": "filepath is required"}
        fmt = (format or "glb").lower()
        if fmt not in ("glb", "fbx"):
            return {"error": f"format must be glb or fbx, got '{format}'"}

        names = [n for n in (object_names or []) if n]
        use_selection = False
        exported = []
        if names:
            missing = [n for n in names if bpy.data.objects.get(n) is None]
            if missing:
                return {"error": "Objects not found in Blender: " + ", ".join(missing)}
            bpy.ops.object.select_all(action='DESELECT')
            for n in names:
                obj = bpy.data.objects[n]
                for o in [obj, *obj.children_recursive]:
                    o.select_set(True)
                    if o.name not in exported:
                        exported.append(o.name)
            bpy.context.view_layer.objects.active = bpy.data.objects[names[0]]
            use_selection = True
        elif selection_only:
            if not bpy.context.selected_objects:
                return {"error": "Nothing is selected in Blender and no object_names were given"}
            exported = [o.name for o in bpy.context.selected_objects]
            use_selection = True
        else:
            exported = [o.name for o in bpy.context.scene.objects]

        try:
            if bpy.context.object and getattr(bpy.context.object, "mode", 'OBJECT') != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        directory = os.path.dirname(filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

        if fmt == "glb":
            bpy.ops.export_scene.gltf(
                filepath=filepath, export_format='GLB', use_selection=use_selection,
                use_active_scene=True, export_apply=apply_modifiers,
                export_animations=True, export_skins=True, export_morph=True, export_yup=True)
        else:
            bpy.ops.export_scene.fbx(
                filepath=filepath, use_selection=use_selection, apply_unit_scale=True,
                bake_space_transform=apply_modifiers, use_mesh_modifiers=apply_modifiers,
                path_mode='COPY', embed_textures=True)

        return {
            "path": filepath,
            "bytes": os.path.getsize(filepath),
            "selection_only": use_selection,
            "exported": exported,
        }
