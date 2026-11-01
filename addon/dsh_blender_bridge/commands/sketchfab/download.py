""" Sketchfab model download and import command. """

import json
import os
import shutil
import tempfile
import zipfile
from contextlib import suppress

import bpy
import requests

from ...integrations.sketchfab_client import SKETCHFAB_DOWNLOAD_URL
from .client import SketchfabClientMixin
from .importer import build_import_result, split_imported_objects


class _UnsafeArchiveError(Exception):
    """Raised when a downloaded archive tries to write outside its temp dir."""


def _request_download_url(uid, api_key):
    """Return (download_url, error_response); exactly one is set."""
    response = requests.get(
        SKETCHFAB_DOWNLOAD_URL.format(uid=uid),
        headers={"Authorization": f"Token {api_key}"},
        timeout=30,  # Add timeout of 30 seconds
    )

    if response.status_code == 401:
        return None, {"error": "Authentication failed (401). Check your API key."}
    if response.status_code != 200:
        return None, {"error": f"Download request failed with status code {response.status_code}"}

    data = response.json()
    if data is None:
        return None, {"error": "Received empty response from Sketchfab API for download request"}

    gltf_data = data.get("gltf")
    if not gltf_data:
        return None, {"error": "No gltf download URL available for this model. Response: " + str(data)}

    download_url = gltf_data.get("url")
    if not download_url:
        return None, {"error": "No download URL available for this model. Make sure the model is downloadable and you have access."}
    return download_url, None


def _extract_archive_securely(zip_file_path, temp_dir):
    """Extract a zip after proving every entry stays below temp_dir."""
    with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
        # More secure zip slip prevention
        for file_info in zip_ref.infolist():
            # Convert directory separators to the current OS style
            file_path = file_info.filename
            target_path = os.path.join(temp_dir, os.path.normpath(file_path))

            abs_temp_dir = os.path.abspath(temp_dir)
            abs_target_path = os.path.abspath(target_path)

            # Ensure the normalized path does not escape the target directory
            if not abs_target_path.startswith(abs_temp_dir):
                raise _UnsafeArchiveError("Security issue: Zip contains files with path traversal attempt")

            # Additional explicit check for directory traversal
            if ".." in file_path:
                raise _UnsafeArchiveError("Security issue: Zip contains files with directory traversal sequence")

        # If all files passed security checks, extract them
        zip_ref.extractall(temp_dir)


def _find_gltf_file(temp_dir):
    """Return the first glTF/GLB entry in temp_dir, or None."""
    gltf_files = [f for f in os.listdir(temp_dir) if f.endswith('.gltf') or f.endswith('.glb')]
    if not gltf_files:
        return None
    return os.path.join(temp_dir, gltf_files[0])


class SketchfabCommands(SketchfabClientMixin):
    def download_sketchfab_model(self, uid, normalize_size=False, target_size=1.0):
        """Download a model from Sketchfab by its UID

        Parameters:
        - uid: The unique identifier of the Sketchfab model
        - normalize_size: If True, scale the model so its largest dimension equals target_size
        - target_size: The target size in Blender units (meters) for the largest dimension
        """
        temp_dir = None
        try:
            api_key = self._get_sketchfab_api_key()
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            download_url, error = _request_download_url(uid, api_key)
            if error:
                return error
            if not download_url:
                return {"error": "Sketchfab returned no download URL"}

            # Download the model (already has timeout)
            model_response = requests.get(download_url, timeout=60)  # 60 second timeout

            if model_response.status_code != 200:
                return {"error": f"Model download failed with status code {model_response.status_code}"}

            # Save to temporary file
            temp_dir = tempfile.mkdtemp()
            zip_file_path = os.path.join(temp_dir, f"{uid}.zip")

            with open(zip_file_path, "wb") as f:
                f.write(model_response.content)

            try:
                _extract_archive_securely(zip_file_path, temp_dir)
            except _UnsafeArchiveError as exc:
                with suppress(Exception):
                    shutil.rmtree(temp_dir)
                return {"error": str(exc)}

            main_file = _find_gltf_file(temp_dir)
            if main_file is None:
                with suppress(Exception):
                    shutil.rmtree(temp_dir)
                return {"error": "No glTF file found in the downloaded model"}

            # Import the model
            bpy.ops.import_scene.gltf(filepath=main_file)

            # Get the imported objects
            imported_objects = list(bpy.context.selected_objects)
            imported_object_names = [obj.name for obj in imported_objects]

            # Clean up temporary files
            with suppress(Exception):
                shutil.rmtree(temp_dir)
            temp_dir = None

            root_objects, all_meshes = split_imported_objects(imported_objects)
            return build_import_result(
                imported_object_names, root_objects, all_meshes,
                normalize_size, target_size,
            )

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection and try again with a simpler model."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Sketchfab API: {str(e)}"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"Failed to download model: {str(e)}"}
        finally:
            if temp_dir:
                with suppress(Exception):
                    shutil.rmtree(temp_dir)
