""" Hunyuan3D generated-asset import commands. """

import os
import os.path as osp
import re
import shutil
import tempfile
import zipfile
from contextlib import suppress

import bpy
import requests


class HunyuanImportCommands:
    def import_generated_asset_hunyuan(self, *args, **kwargs):
        return self.import_generated_asset_hunyuan_ai(*args, **kwargs)

    def _import_hunyuan_glb(self, name: str, glb_url: str):
        temp_dir = tempfile.mkdtemp(prefix="hunyuan_glb_")
        glb_path = osp.join(temp_dir, "model.glb")
        try:
            glb_response = requests.get(glb_url, stream=True, timeout=120)
            glb_response.raise_for_status()
            with open(glb_path, "wb") as f:
                for chunk in glb_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            bpy.ops.import_scene.gltf(filepath=glb_path)
            imported_objs = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
            if not imported_objs:
                return {"succeed": False, "error": "No mesh objects imported from GLB"}
            obj = imported_objs[0]
            if name:
                obj.name = name
            result = {
                "name": obj.name, "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }
            if obj.type == "MESH":
                result["world_bounding_box"] = self._get_aabb(obj)
            return {"succeed": True, **result}
        except Exception as e:
            return {"succeed": False, "error": str(e)}
        finally:
            with suppress(Exception):
                shutil.rmtree(temp_dir)

    def import_generated_asset_hunyuan_ai(self, name: str, zip_file_url: str):
        if not zip_file_url:
            return {"error": "No file URL provided"}

        # Validate URL
        if not re.match(r'^https?://', zip_file_url, re.IGNORECASE):
            return {"error": "Invalid URL format. Must start with http:// or https://"}

        # Prefer GLB (self-contained with materials) over OBJ/ZIP (API 3.0 returns .glb URLs)
        url_path = zip_file_url.split('?', 1)[0].split('#', 1)[0].lower()
        if url_path.endswith('.glb'):
            return self._import_hunyuan_glb(name, zip_file_url)

        # Fallback: ZIP/OBJ import (legacy)
        temp_dir = tempfile.mkdtemp(prefix="tencent_obj_")
        zip_file_path = osp.join(temp_dir, "model.zip")
        obj_file_path = osp.join(temp_dir, "model.obj")
        try:
            zip_response = requests.get(zip_file_url, stream=True, timeout=120)
            zip_response.raise_for_status()
            with open(zip_file_path, "wb") as f:
                for chunk in zip_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
                # Mirror the Sketchfab zip-slip checks before extractall.
                abs_temp_dir = os.path.abspath(temp_dir)
                for file_info in zip_ref.infolist():
                    file_path = file_info.filename
                    target_path = os.path.join(temp_dir, os.path.normpath(file_path))
                    abs_target_path = os.path.abspath(target_path)
                    if not abs_target_path.startswith(abs_temp_dir + os.sep) and abs_target_path != abs_temp_dir:
                        return {
                            "succeed": False,
                            "error": "Security issue: Zip contains files with path traversal attempt",
                        }
                    if ".." in file_path:
                        return {
                            "succeed": False,
                            "error": "Security issue: Zip contains files with directory traversal sequence",
                        }
                zip_ref.extractall(temp_dir)
            for file in os.listdir(temp_dir):
                if file.endswith(".obj"):
                    obj_file_path = osp.join(temp_dir, file)
            if not osp.exists(obj_file_path):
                return {"succeed": False, "error": "OBJ file not found after extraction"}
            if bpy.app.version>=(4, 0, 0):
                bpy.ops.wm.obj_import(filepath=obj_file_path)
            else:
                bpy.ops.import_scene.obj(filepath=obj_file_path)
            imported_objs = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
            if not imported_objs:
                return {"succeed": False, "error": "No mesh objects imported"}
            obj = imported_objs[0]
            if name:
                obj.name = name
            result = {
                "name": obj.name, "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }
            if obj.type == "MESH":
                result["world_bounding_box"] = self._get_aabb(obj)
            return {"succeed": True, **result}
        except Exception as e:
            return {"succeed": False, "error": str(e)}
        finally:
            with suppress(Exception):
                shutil.rmtree(temp_dir)
