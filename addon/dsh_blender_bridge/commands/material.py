""" Material inspection and texture application commands. """

import bpy
import traceback

from ..integrations.polyhaven_client import POLYHAVEN_TEXTURE_MAPS, _polyhaven_asset_url, _polyhaven_authors, _polyhaven_dimensions_mm, _polyhaven_mapping_node, _polyhaven_resolution_rank, _polyhaven_tag, _polyhaven_valid_slug

class MaterialCommands:
    def _polyhaven_material_info(self, mat):
        """Summarise a material's node tree for the caller."""
        texture_nodes = []
        for node in mat.node_tree.nodes:
            if node.type != 'TEX_IMAGE' or node.image is None:
                continue
            connections = []
            for link in mat.node_tree.links:
                if link.from_node == node:
                    connections.append(
                        f"{link.from_socket.name} -> {link.to_node.name}.{link.to_socket.name}"
                    )
            texture_nodes.append({
                "name": node.name,
                "image": node.image.name,
                "colorspace": node.image.colorspace_settings.name,
                "connections": connections,
            })

        # Every image node's Vector input comes from here, so this is the one
        # node that decides the tiling - and it could not appear in this report,
        # which described TEX_IMAGE nodes and nothing else.
        mapping = _polyhaven_mapping_node(mat.node_tree)

        return {
            "has_nodes": mat.use_nodes,
            "node_count": len(mat.node_tree.nodes),
            "texture_nodes": texture_nodes,
            "mapping_node": None if mapping is None else {
                "name": mapping.name,
                "vector_type": mapping.vector_type,
                "scale": list(mapping.inputs['Scale'].default_value),
            },
        }

    def set_texture(self, object_name, texture_id):
        """Apply a previously downloaded Polyhaven texture to an object by creating a new material"""
        try:
            obj = bpy.data.objects.get(object_name)
            if not obj:
                return {"error": f"Object not found: {object_name}"}

            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                return {"error": f"Object {object_name} cannot accept materials"}

            if not _polyhaven_valid_slug(texture_id):
                return {"error": f"Invalid texture id: {texture_id!r}"}

            # Identified by the custom property stamped at download time rather
            # than by parsing the image's name. The old parser took the last
            # underscore-separated token, which turned "nor_gl" into "gl" and
            # left the two functions disagreeing about what a map was called.
            maps = {}
            for img in bpy.data.images:
                if img.get("polyhaven_id") != texture_id:
                    continue
                map_key = img.get("polyhaven_map")
                # Role first: assets whose albedo is not called "Diffuse" are
                # not in the table, but were resolved at download time.
                role = img.get("polyhaven_role") or POLYHAVEN_TEXTURE_MAPS.get(map_key)
                if not role:
                    continue
                if not img.packed_file:
                    img.pack()

                # An asset downloaded at more than one resolution leaves several
                # images per map, all carrying the same id. Take the largest
                # rather than whichever happened to come last.
                existing = maps.get(map_key)
                if existing and _polyhaven_resolution_rank(
                        existing[1].get("polyhaven_resolution")) >= _polyhaven_resolution_rank(
                        img.get("polyhaven_resolution")):
                    continue
                maps[map_key] = (role, img)

            if not maps:
                return {
                    "error": f"No texture images found for: {texture_id}. "
                             "Download it first with download_polyhaven_asset."
                }

            new_mat_name = f"{texture_id}_material_{object_name}"
            existing_mat = bpy.data.materials.get(new_mat_name)
            if existing_mat:
                bpy.data.materials.remove(existing_mat)

            new_mat, wired = self._polyhaven_build_material(texture_id, maps)
            new_mat.name = new_mat_name

            authors = _polyhaven_authors(texture_id)
            _polyhaven_tag([new_mat], texture_id, authors=authors,
                           dimensions=_polyhaven_dimensions_mm(texture_id))

            # Note: this replaces every material slot on the object.
            replaced = len(obj.data.materials)
            while len(obj.data.materials) > 0:
                obj.data.materials.pop(index=0)
            obj.data.materials.append(new_mat)

            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.context.view_layer.update()

            message = f"Created new material and applied texture {texture_id} to {object_name}"
            if replaced:
                message += f" (replaced {replaced} existing material slot{'s' if replaced != 1 else ''})"

            return {
                "success": True,
                "message": message,
                "material": new_mat.name,
                "maps": wired,
                "material_info": self._polyhaven_material_info(new_mat),
                "authors": authors,
                "url": _polyhaven_asset_url(texture_id),
            }

        except Exception as e:
            print(f"Error in set_texture: {str(e)}")
            traceback.print_exc()
            return {"error": f"Failed to apply texture: {str(e)}"}
