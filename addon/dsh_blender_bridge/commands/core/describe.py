""" Node-type introspection (describe_node_type). """

import bpy


class DescribeCommands:
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
                tmp_tree = bpy.data.node_groups.new(name="__bridge_introspect_tmp__", type=tree_type)  # pyright: ignore[reportArgumentType]
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
            if tree is not None:
                try:
                    bpy.data.node_groups.remove(tree)
                except Exception:
                    pass
