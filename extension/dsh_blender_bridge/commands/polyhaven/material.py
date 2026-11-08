""" Poly Haven texture download and material construction. """

import os
import shutil
import tempfile
import traceback
from contextlib import suppress

import bpy

from ...integrations.polyhaven_client import (
    POLYHAVEN_COLOR_ROLES,
    _polyhaven_asset_url,
    _polyhaven_authors,
    _polyhaven_available,
    _polyhaven_dimensions_mm,
    _polyhaven_download,
    _polyhaven_mapping_node,
    _polyhaven_select_texture_maps,
    _polyhaven_set_colorspace,
    _polyhaven_tag,
)


class PolyHavenMaterialMixin:
    def _polyhaven_build_material(self, asset_id, maps):
        """Build a Principled material from {map_key: (role, image)}.

        Shared by download_polyhaven_asset and set_texture so there is exactly
        one place that decides which map drives which input - set_texture used
        to build its own tree in two passes over the same maps, silently
        replacing every link it had just made and leaving the first pass's
        Normal Map and Displacement nodes orphaned in the tree.
        """
        mat = bpy.data.materials.new(name=asset_id)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        output = nodes.new(type='ShaderNodeOutputMaterial')
        output.location = (600, 0)

        principled = nodes.new(type='ShaderNodeBsdfPrincipled')
        principled.location = (300, 0)
        links.new(principled.outputs[0], output.inputs['Surface'])

        tex_coord = nodes.new(type='ShaderNodeTexCoord')
        tex_coord.location = (-1000, 0)

        mapping = nodes.new(type='ShaderNodeMapping')
        mapping.location = (-800, 0)
        # POINT is Blender's default and the mode Poly Haven authors its own
        # materials in - the Mapping node published inside every texture .blend
        # is left at POINT, and the add-on's real-world-scale operator solves for
        # a Scale that grows as the surface grows. TEXTURE is its exact inverse
        # ("transform a texture by inverse mapping the texture coordinate"), so
        # the natural arithmetic - Scale = surface size / texture size - came out
        # upside down, and a 2m texture asked to repeat twice repeated half a
        # time instead. At Scale 1.0 the two modes are identical, so this moves
        # nothing that was not already inverted.
        mapping.vector_type = 'POINT'
        links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])

        y_pos = 300
        wired = []

        for map_key, (role, image) in maps.items():
            tex_node = nodes.new(type='ShaderNodeTexImage')
            tex_node.location = (-500, y_pos)
            tex_node.image = image
            _polyhaven_set_colorspace(image, is_color_data=role in POLYHAVEN_COLOR_ROLES)
            links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
            y_pos -= 300

            if role == "base_color":
                links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
            elif role == "roughness":
                links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
            elif role == "metallic":
                links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
            elif role == "normal":
                normal_map = nodes.new(type='ShaderNodeNormalMap')
                normal_map.location = (-200, tex_node.location[1])
                links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
            elif role == "displacement":
                disp_node = nodes.new(type='ShaderNodeDisplacement')
                disp_node.location = (300, tex_node.location[1])
                # Poly Haven's displacement maps are centred on 0.5, and the
                # output is only used at all once the material is told to
                # displace - otherwise the node sits there connected and inert.
                disp_node.inputs['Midlevel'].default_value = 0.5
                disp_node.inputs['Scale'].default_value = 0.1
                links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                # Moved off material.cycles in Blender 4.1; try both so the
                # node is not left connected but inert on older versions.
                if hasattr(mat, "displacement_method"):
                    mat.displacement_method = 'BOTH'
                else:
                    with suppress(Exception):
                        mat.cycles.displacement_method = 'BOTH'
            else:
                continue

            wired.append(map_key)

        return mat, wired

    def _polyhaven_import_texture(self, asset_id, files_data, resolution, file_format):
        """Download a texture's maps and build a material from them."""
        wanted = _polyhaven_select_texture_maps(files_data, resolution, file_format)
        if not wanted:
            return {
                "error": f"Texture '{asset_id}' has no maps at {resolution} {file_format} - "
                         f"{_polyhaven_available(files_data, 'textures')}"
            }

        dest_dir = tempfile.mkdtemp(prefix="dsh_blender_bridge_polyhaven_")
        maps = {}

        try:
            for map_key, role in wanted.items():
                file_info = files_data[map_key][resolution][file_format]
                dest_path = os.path.join(
                    dest_dir, f"{asset_id}_{map_key}_{resolution}.{file_format}"
                )
                _polyhaven_download(file_info, dest_path)

                image = bpy.data.images.load(dest_path, check_existing=True)
                image.name = f"{asset_id}_{map_key}"
                _polyhaven_set_colorspace(image, is_color_data=role in POLYHAVEN_COLOR_ROLES)
                image.pack()
                maps[map_key] = (role, image)
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Failed to download texture maps: {str(e)}"}
        finally:
            # Every image is packed, so nothing needs the files any more.
            shutil.rmtree(dest_dir, ignore_errors=True)

        try:
            mat, wired = self._polyhaven_build_material(asset_id, maps)

            # Deliberately no fake user. A material nothing has been applied to
            # is not being used, and Blender discarding it on save is the
            # correct outcome rather than a leak to guard against - the same
            # reasoning as the world this no longer keeps alive either. Call
            # set_texture to give it a real user.

            authors = _polyhaven_authors(asset_id)
            dimensions = _polyhaven_dimensions_mm(asset_id)
            _polyhaven_tag(
                [mat] + [image for _role, image in maps.values()],
                asset_id,
                resolution=resolution,
                authors=authors,
                dimensions=dimensions,
            )
            for map_key, (role, image) in maps.items():
                with suppress(Exception):
                    image["polyhaven_map"] = map_key
                    image["polyhaven_role"] = role

            mapping = _polyhaven_mapping_node(mat.node_tree)
            return {
                "success": True,
                "message": f"Texture {asset_id} imported as material",
                "material": mat.name,
                "maps": wired,
                "authors": authors,
                "url": _polyhaven_asset_url(asset_id),
                # What the material has to be told before it is applied to
                # anything, reported next to the material itself rather than
                # left in a search result several steps back.
                "scale_mm": dimensions,
                "mapping_node": None if mapping is None else mapping.name,
            }
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Failed to build material: {str(e)}"}
