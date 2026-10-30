""" Poly Haven asset commands. """

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

class PolyHavenCommands:
    def get_polyhaven_status(self):
        """Get the current status of PolyHaven integration"""
        enabled = bpy.context.scene.dshbb_use_polyhaven
        if enabled:
            return {"enabled": True, "message": "PolyHaven integration is enabled and ready to use."}
        else:
            return {
                "enabled": False,
                "message": """PolyHaven integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the DSH Blender Bridge panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Poly Haven' checkbox
                            3. Restart the connection to Claude"""
        }

    def get_polyhaven_categories(self, asset_type):
        """Get the category taxonomy and attribute schema for an asset type."""
        try:
            if asset_type not in ["hdris", "textures", "models", "all"]:
                return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}

            if asset_type == "all":
                # Three full trees at once is 30KB of paths, so this one is cut
                # to the top two levels. Filtering is inclusive, so those still
                # select everything beneath them.
                return {
                    "taxonomy": [
                        _polyhaven_taxonomy(one, depth=POLYHAVEN_TAXONOMY_DEPTH_ALL)
                        for one in ("hdris", "textures", "models")
                    ],
                    "truncated": True,
                }

            return {"taxonomy": [_polyhaven_taxonomy(asset_type)], "truncated": False}
        except Exception as e:
            return {"error": str(e)}

    def search_polyhaven_assets(self, asset_type=None, category=None, attributes=None,
                                query=None, limit=None, min_size_m=None):
        """Search for assets from Polyhaven with optional filtering"""
        try:
            params = {}

            if asset_type and asset_type != "all":
                if asset_type not in ["hdris", "textures", "models"]:
                    return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
                params["type"] = asset_type

            # `category`, not `categories`. The two are different filters over
            # different vocabularies: `categories` is the legacy flat tag list
            # ("outdoor", "man made", "floor"), while `category` takes the
            # single-path taxonomy that get_polyhaven_categories now returns
            # ("Metal/Sheet & Corrugated") and matches it inclusively, so a
            # parent selects everything beneath it. Sending a path to the legacy
            # parameter is answered with 200 and an empty object rather than an
            # error, so every filtered search came back silently empty.
            if category:
                params["category"] = category

            for key, value in (attributes or {}).items():
                if value is None or value == "":
                    continue
                if isinstance(value, bool):
                    value = "true" if value else "false"
                elif isinstance(value, (list, tuple)):
                    # Comma-separated values are OR'd together by the API.
                    value = ",".join(str(v) for v in value)
                params[str(key)] = str(value)

            try:
                limit = int(limit) if limit else POLYHAVEN_SEARCH_LIMIT
            except (TypeError, ValueError):
                limit = POLYHAVEN_SEARCH_LIMIT
            limit = max(1, min(limit, POLYHAVEN_SEARCH_MAX_LIMIT))

            try:
                assets = _polyhaven_api_get("assets", params=params, cache=True)
            except PolyHavenAPIError as e:
                if e.status == 400:
                    # The category and attribute filters answer an unrecognised
                    # value with 400 precisely so it is not a silent empty page.
                    return {"error": "Poly Haven did not recognise that category or attribute "
                                     "filter. Call get_polyhaven_categories for the values each "
                                     "asset type accepts."}
                raise

            # Trimmed and lower-cased so equivalent queries share a cache entry,
            # both here and at Poly Haven's edge.
            query = (query or "").strip().lower()
            note = None

            # Filtered here rather than at the API, which publishes a real-world
            # size for every texture but takes no filter on it. Free: the records
            # are already in hand. Anything that publishes no size cannot satisfy
            # a floor on it and drops out - HDRIs have none.
            if min_size_m:
                try:
                    floor_mm = float(min_size_m) * 1000
                except (TypeError, ValueError):
                    return {"error": f"min_size_m must be a number, got {min_size_m!r}"}
                before = len(assets)
                assets = {
                    slug: record for slug, record in assets.items()
                    if max(record.get("dimensions") or [0]) >= floor_mm
                }
                if before and not assets:
                    # An empty page reads as "Poly Haven does not have this",
                    # which is a different and much worse statement than "the
                    # size floor is above everything that matched".
                    note = (f"Nothing matching the other filters is {floor_mm / 1000:g}m or "
                            "larger. Most textures are 1-4m, and HDRIs have no real-world "
                            "size at all. Lower min_size_m or leave it out.")

            if query:
                try:
                    ranked = _polyhaven_search(query, asset_type)
                except PolyHavenAPIError as e:
                    if e.status == 429:
                        wait = f" Retry in {e.retry_after}s." if e.retry_after else ""
                        return {"error": f"Poly Haven is rate limiting searches from this "
                                         f"address.{wait}"}
                    if e.status != 503:
                        raise
                    # The API documents a 503 as "the query could not be
                    # embedded, fall back to your own keyword matching".
                    ranked = _polyhaven_keyword_match(query, assets)
                    note = ("Poly Haven's semantic search was unavailable, so these are plain "
                            "keyword matches and the ranking is weaker than usual.")

                # /search knows nothing about the category and attribute filters,
                # so its ranking is intersected with the filtered list here. That
                # is why the whole ranked list is asked for rather than the first
                # `limit` of it: filtering a page that the server already cut can
                # only shrink it, and the matches would be the ones further down.
                ordered = [slug for slug in ranked if slug in assets]
            else:
                # Rank before truncating. The previous order was whatever the API
                # happened to return, which is sorted by slug - and because models
                # are the only assets with capitalised slugs, the first 20 of an
                # unfiltered list were 20 models. asset_type="all" could not return
                # a single HDRI or texture, and the library's most downloaded assets
                # were unreachable by any call.
                ordered = sorted(
                    assets, key=lambda slug: assets[slug].get("download_count", 0), reverse=True)

            selected = ordered[:limit]

            return {
                "assets": [_polyhaven_summarize_asset(slug, assets[slug]) for slug in selected],
                # Everything matching every filter, so the count and the page it
                # heads describe the same population.
                "total_count": len(ordered),
                "returned_count": len(selected),
                "query": query or None,
                "note": note,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_polyhaven_asset_preview(self, asset_id):
        """Fetch an asset's thumbnail, so it can be looked at before downloading.

        A thumbnail is a few hundred kilobytes against a 4k texture's 24MB, so
        checking one first is cheaper for everybody than importing the wrong rock.
        """
        try:
            if not _polyhaven_valid_slug(asset_id):
                return {"error": f"Invalid asset id: {asset_id!r}. Poly Haven slugs are "
                                 "letters, digits, underscores and hyphens."}

            record = _polyhaven_asset_record(asset_id)
            thumbnail_url = record.get("thumbnail_url")
            if not thumbnail_url:
                return {"error": f"No thumbnail is published for '{asset_id}'"}

            response = requests.get(
                _polyhaven_preview_url(thumbnail_url),
                headers=POLYHAVEN_HEADERS,
                timeout=POLYHAVEN_API_TIMEOUT,
            )
            if response.status_code >= 400:
                return {"error": f"Failed to fetch the thumbnail: HTTP {response.status_code}"}

            content_type = getattr(response, "headers", {}).get("Content-Type", "")
            image_format = "png" if "png" in content_type or ".png" in thumbnail_url else "jpeg"

            authors = record.get("authors") or {}
            return {
                "success": True,
                "image_data": base64.b64encode(response.content).decode("ascii"),
                "format": image_format,
                "asset_id": asset_id,
                "name": record.get("name") or asset_id,
                "authors": sorted(authors) if isinstance(authors, dict) else authors,
                "url": _polyhaven_asset_url(asset_id),
            }
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Failed to get asset preview: {str(e)}"}

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
