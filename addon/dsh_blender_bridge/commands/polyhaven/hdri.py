""" Poly Haven HDRI download and world import. """

import os
import shutil
import tempfile
import traceback

import bpy

from ...integrations.polyhaven_client import (
    _polyhaven_asset_url,
    _polyhaven_authors,
    _polyhaven_available,
    _polyhaven_download,
    _polyhaven_tag,
)


class PolyHavenHdriMixin:
    def _polyhaven_import_hdri(self, asset_id, files_data, resolution, file_format):
        """Download an HDRI and set it up as the scene's world."""
        file_info = files_data.get("hdri", {}).get(resolution, {}).get(file_format)
        if not file_info:
            return {
                "error": f"HDRI '{asset_id}' has no {resolution} {file_format} - "
                         f"{_polyhaven_available(files_data, 'hdris')}"
            }

        dest_dir = tempfile.mkdtemp(prefix="dsh_blender_bridge_polyhaven_")
        dest_path = os.path.join(dest_dir, f"{asset_id}_{resolution}.{file_format}")

        try:
            _polyhaven_download(file_info, dest_path)
        except Exception as e:
            shutil.rmtree(dest_dir, ignore_errors=True)
            return {"error": f"Failed to download HDRI: {str(e)}"}

        try:
            # A new world every time, rather than clearing the nodes of whatever
            # world is already there. The old code took bpy.data.worlds[0] - the
            # alphabetically first world datablock, very often somebody else's -
            # wiped its nodes and made it active, destroying hand-built setups
            # with no undo step to recover them. Using the scene's own world
            # instead would still have wiped it. This leaves the previous world
            # intact and simply unused; without a fake user Blender clears it up
            # on save if nothing else references it, and it is recoverable from
            # the outliner's orphan data until then.
            world = bpy.data.worlds.new(f"PolyHaven {asset_id}")
            bpy.context.scene.world = world

            world.use_nodes = True
            node_tree = world.node_tree
            node_tree.nodes.clear()

            tex_coord = node_tree.nodes.new(type='ShaderNodeTexCoord')
            tex_coord.location = (-800, 0)

            mapping = node_tree.nodes.new(type='ShaderNodeMapping')
            mapping.location = (-600, 0)

            env_tex = node_tree.nodes.new(type='ShaderNodeTexEnvironment')
            env_tex.location = (-400, 0)
            env_tex.image = bpy.data.images.load(dest_path, check_existing=True)
            env_tex.image.name = f"{asset_id}_{resolution}"
            # Colorspace is deliberately left as Blender's loader set it. It
            # already tags .hdr/.exr as scene-linear, and forcing "Non-Color"
            # here would mark radiance data as raw - identical under the stock
            # OCIO config, a colour shift under any config whose working space
            # is not Linear Rec.709.

            # Pack before anything can remove the file underneath it. Without
            # this the world points at a path in the OS temp directory for the
            # life of the .blend: it renders now, and is a missing image the
            # next time the file is opened here - or the first time it is opened
            # anywhere else.
            env_tex.image.pack()

            background = node_tree.nodes.new(type='ShaderNodeBackground')
            background.location = (-200, 0)

            output = node_tree.nodes.new(type='ShaderNodeOutputWorld')
            output.location = (0, 0)

            node_tree.links.new(tex_coord.outputs['Generated'], mapping.inputs['Vector'])
            node_tree.links.new(mapping.outputs['Vector'], env_tex.inputs['Vector'])
            node_tree.links.new(env_tex.outputs['Color'], background.inputs['Color'])
            node_tree.links.new(background.outputs['Background'], output.inputs['Surface'])

            bpy.context.scene.world = world

            authors = _polyhaven_authors(asset_id)
            _polyhaven_tag([world, env_tex.image], asset_id, resolution, authors)

            return {
                "success": True,
                "message": f"HDRI {asset_id} imported successfully",
                "image_name": env_tex.image.name,
                "world": world.name,
                "authors": authors,
                "url": _polyhaven_asset_url(asset_id),
            }
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Failed to set up HDRI in Blender: {str(e)}"}
        finally:
            # The image is packed, so nothing needs the file any more.
            shutil.rmtree(dest_dir, ignore_errors=True)
