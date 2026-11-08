""" Structured bpy RNA/operator lookup (bpy_api_lookup). """

import bpy

from .describe import DescribeCommands


class BpyApiLookupCommands(DescribeCommands):
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
