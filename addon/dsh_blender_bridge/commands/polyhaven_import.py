""" Poly Haven asset download and Blender import commands. """

import bpy
import requests
import tempfile
import traceback
import os
import shutil
import hashlib, hmac, base64
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl
from contextlib import contextmanager, redirect_stdout, suppress

from ..integrations.polyhaven_client import POLYHAVEN_API_TIMEOUT, POLYHAVEN_COLOR_ROLES, POLYHAVEN_DEFAULT_FORMATS, POLYHAVEN_HEADERS, POLYHAVEN_MODEL_FALLBACK_FORMAT, POLYHAVEN_SEARCH_LIMIT, POLYHAVEN_SEARCH_MAX_LIMIT, POLYHAVEN_SUPPORTED_FORMATS, POLYHAVEN_TAXONOMY_DEPTH_ALL, PolyHavenAPIError, _polyhaven_api_get, _polyhaven_asset_record, _polyhaven_asset_url, _polyhaven_authors, _polyhaven_available, _polyhaven_blend_version, _polyhaven_dimensions_mm, _polyhaven_download, _polyhaven_keyword_match, _polyhaven_mapping_node, _polyhaven_preview_url, _polyhaven_search, _polyhaven_select_texture_maps, _polyhaven_set_colorspace, _polyhaven_summarize_asset, _polyhaven_tag, _polyhaven_taxonomy, _polyhaven_valid_slug



class PolyHavenImportMixin:
    def download_polyhaven_asset(self, asset_id, asset_type, resolution="1k", file_format=None):
        try:
            if asset_type not in POLYHAVEN_SUPPORTED_FORMATS:
                return {"error": f"Unsupported asset type: {asset_type}. Must be one of: hdris, textures, models"}

            if not _polyhaven_valid_slug(asset_id):
                return {"error": f"Invalid asset id: {asset_id!r}. Poly Haven slugs are "
                                 "letters, digits, underscores and hyphens."}

            supported = POLYHAVEN_SUPPORTED_FORMATS[asset_type]
            file_format = (file_format or POLYHAVEN_DEFAULT_FORMATS[asset_type]).lower()
            if file_format not in supported:
                # Rejected before any transfer. `usd` is listed for every model
                # and used to be downloaded in full before reaching the
                # "unsupported format" branch at the end of the import.
                return {
                    "error": f"Unsupported {asset_type} format: {file_format}. "
                             f"Supported formats: {', '.join(supported)}"
                }

            try:
                files_data = _polyhaven_api_get(f"files/{quote(asset_id, safe='')}")
            except Exception as e:
                return {"error": f"Failed to get asset files for '{asset_id}': {str(e)}"}

            if asset_type == "hdris":
                return self._polyhaven_import_hdri(asset_id, files_data, resolution, file_format)
            if asset_type == "textures":
                return self._polyhaven_import_texture(asset_id, files_data, resolution, file_format)
            return self._polyhaven_import_model(asset_id, files_data, resolution, file_format)

        except Exception as e:
            traceback.print_exc()
            return {"error": f"Failed to download asset: {str(e)}"}

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

    def _polyhaven_fetch_model_files(self, files_data, resolution, file_format, dest_dir):
        """Download a model's main file and its sidecar textures into dest_dir."""
        file_info = files_data.get(file_format, {}).get(resolution, {}).get(file_format)
        if not file_info:
            return None

        main_file_path = os.path.join(dest_dir, os.path.basename(file_info["url"].split("?")[0]))
        _polyhaven_download(file_info, main_file_path)

        for include_path, include_info in (file_info.get("include") or {}).items():
            # Validate include_path - the API response controls these
            # dict keys; a malicious or MITM'd response could request an
            # absolute path or one containing ".." to escape dest_dir
            # and write arbitrary files (e.g. ~/.bashrc, authorized_keys).
            # Mirrors the zip-slip check in download_sketchfab_model.
            target_path = os.path.join(dest_dir, os.path.normpath(include_path))
            abs_dest_dir = os.path.abspath(dest_dir)
            abs_target_path = os.path.abspath(target_path)
            if (os.path.isabs(include_path)
                    or ".." in include_path
                    or not abs_target_path.startswith(abs_dest_dir + os.sep)):
                print(f"Skipping include with unsafe path: {include_path}")
                continue

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            _polyhaven_download(include_info, target_path)

        return main_file_path

    def _polyhaven_append_blend(self, blend_path, asset_id):
        """Append the asset's own collection out of a Poly Haven model .blend.

        Every published model holds a collection named exactly the slug - it is
        an error in Poly Haven's own asset checker if it does not - and models
        with levels of detail carry them as `<slug>_LOD0`, `_LOD1` and so on
        beneath it. Appending `data_from.objects` wholesale, as this used to,
        linked every LOD on top of each other plus whatever else the file
        happened to hold, which for some assets is a second model.
        """
        with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
            available = list(data_from.collections)
            # LOD0 is the full-detail version. Taking it directly leaves the
            # coarser ones in the file rather than in the scene.
            wanted = next(
                (name for name in (f"{asset_id}_LOD0", asset_id) if name in available), None)
            if wanted:
                data_to.collections = [wanted]
            else:
                # Nothing to key off. Fall back to the old behaviour rather than
                # importing nothing at all.
                data_to.objects = data_from.objects

        linked = []
        for collection in data_to.collections:
            if collection is not None:
                bpy.context.scene.collection.children.link(collection)
                linked.append(collection)
        if not linked:
            for obj in data_to.objects:
                if obj is not None:
                    bpy.context.collection.objects.link(obj)
        return linked

    def _polyhaven_import_model(self, asset_id, files_data, resolution, file_format):
        """Download a model and its textures, then import it."""
        if not files_data.get(file_format, {}).get(resolution, {}).get(file_format):
            return {
                "error": f"Model {asset_id!r} has no {resolution} {file_format} - "
                         f"{_polyhaven_available(files_data, 'models')}"
            }

        dest_dir = tempfile.mkdtemp(prefix="dsh_blender_bridge_polyhaven_")
        fallback_note = ""
        collections = []

        try:
            main_file_path = self._polyhaven_fetch_model_files(
                files_data, resolution, file_format, dest_dir)
        except Exception as e:
            traceback.print_exc()
            shutil.rmtree(dest_dir, ignore_errors=True)
            return {"error": f"Failed to download model: {str(e)}"}

        # By name: bpy hands out a fresh Python wrapper per access, so holding on
        # to the datablocks themselves invites identity bugs.
        before = {obj.name for obj in bpy.data.objects}

        try:
            if file_format == "blend":
                written_by = _polyhaven_blend_version(main_file_path)
                if written_by and written_by > bpy.app.version[:2]:
                    raise RuntimeError("written by Blender %d.%d" % written_by)
                collections = self._polyhaven_append_blend(main_file_path, asset_id)
            else:
                bpy.ops.import_scene.gltf(filepath=main_file_path)
        except Exception as blend_error:
            if file_format != "blend":
                traceback.print_exc()
                shutil.rmtree(dest_dir, ignore_errors=True)
                return {"error": f"Failed to import model: {str(blend_error)}"}

            # A .blend written by a newer Blender than this one cannot be opened
            # at all, and Poly Haven's oldest models were saved in 2.93 while its
            # newest were saved in 5.0. glTF is a poorer record of the material,
            # but it is the difference between a worse model and no model.
            print(f"Poly Haven: .blend import failed ({blend_error}), falling back to glTF")
            shutil.rmtree(dest_dir, ignore_errors=True)
            dest_dir = tempfile.mkdtemp(prefix="dsh_blender_bridge_polyhaven_")
            fallback_note = (
                f" Imported from glTF rather than .blend, because the .blend was {blend_error}"
                f" and this is Blender {bpy.app.version_string.split()[0]}. Its materials are a"
                " conversion rather than the ones the artist built."
            )
            try:
                before = {obj.name for obj in bpy.data.objects}
                fallback_path = self._polyhaven_fetch_model_files(
                    files_data, resolution, POLYHAVEN_MODEL_FALLBACK_FORMAT, dest_dir)
                if not fallback_path:
                    raise RuntimeError(f"no {resolution} glTF is published for it")
                bpy.ops.import_scene.gltf(filepath=fallback_path)
            except Exception as e:
                traceback.print_exc()
                shutil.rmtree(dest_dir, ignore_errors=True)
                return {
                    "error": f"Model {asset_id!r} is {blend_error}, which this Blender cannot "
                             f"open, and the glTF fallback failed too: {str(e)}"
                }

        try:
            imported = [obj for obj in bpy.data.objects if obj.name not in before]
            imported_objects = [obj.name for obj in imported]
            if not imported_objects:
                return {"error": f"Imported {asset_id} but nothing arrived in the scene. "
                                 "The .blend may not hold the collection this expects."}

            # Appended and glTF-imported images still reference the files in the
            # temporary directory this deletes on the way out. A .glb carries its
            # textures inside it, but a .gltf with sidecar files does not, and an
            # appended .blend never does.
            materials = []
            for obj in imported:
                for slot in getattr(obj, "material_slots", []):
                    if slot.material is None:
                        continue
                    if slot.material not in materials:
                        materials.append(slot.material)
                    if not slot.material.use_nodes:
                        continue
                    for node in slot.material.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image and not node.image.packed_file:
                            with suppress(Exception):
                                node.image.pack()

            authors = _polyhaven_authors(asset_id)
            _polyhaven_tag(imported + collections + materials, asset_id, resolution, authors)

            return {
                "success": True,
                "message": f"Model {asset_id} imported successfully.{fallback_note}",
                "imported_objects": imported_objects,
                "authors": authors,
                "url": _polyhaven_asset_url(asset_id),
            }
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Failed to import model: {str(e)}"}
        finally:
            shutil.rmtree(dest_dir, ignore_errors=True)
