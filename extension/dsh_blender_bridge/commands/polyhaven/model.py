""" Poly Haven model download, .blend append, and glTF import. """

import os
import shutil
import tempfile
import traceback
from contextlib import suppress

import bpy

from ...integrations.polyhaven_client import (
    POLYHAVEN_MODEL_FALLBACK_FORMAT,
    _polyhaven_asset_url,
    _polyhaven_authors,
    _polyhaven_available,
    _polyhaven_blend_version,
    _polyhaven_download,
    _polyhaven_tag,
)


class PolyHavenModelMixin:
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
        with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):  # pyright: ignore[reportGeneralTypeIssues]
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
            if main_file_path is None:
                raise RuntimeError("model download returned no main file")
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
