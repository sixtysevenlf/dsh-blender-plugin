""" Poly Pizza model download and import command. """

import json
import os
import shutil
import tempfile
from contextlib import suppress
from urllib.parse import quote

import bpy
import requests

from ...config import REQ_HEADERS
from ...integrations.polypizza_client import (
    POLYPIZZA_API_BASE,
    _polypizza_cdn_error,
)
from .client import PolypizzaClientMixin
from .importer import build_download_result, safe_model_id, split_imported_objects


def _request_model_metadata(model_id, headers):
    """Return (model_record, error_response); exactly one is set."""
    response = requests.get(
        f"{POLYPIZZA_API_BASE}/model/{quote(str(model_id), safe='')}",
        headers=headers,
        timeout=30
    )

    if response.status_code in (401, 403):
        return None, {"error": f"Poly Pizza authentication failed ({response.status_code}). Check your API key."}
    if response.status_code == 404:
        return None, {"error": f"No Poly Pizza model found with ID '{model_id}'"}
    if response.status_code != 200:
        return None, {"error": f"Poly Pizza model lookup failed with status code {response.status_code}"}

    model = response.json()
    if not isinstance(model, dict):
        return None, {"error": f"Unexpected response format from Poly Pizza API: {model}"}
    return model, None


def _download_glb_content(download_url):
    """Return (content, error_response); exactly one is set."""
    # The CDN takes no API key and must never be sent one: it is a
    # separate host from the API.
    file_response = requests.get(download_url, headers=dict(REQ_HEADERS), timeout=60)

    cdn_error = _polypizza_cdn_error(
        file_response.status_code,
        getattr(file_response, "headers", None),
        file_response.content or b"",
    )
    if cdn_error:
        return None, {"error": cdn_error}
    return file_response.content, None


class PolypizzaCommands(PolypizzaClientMixin):
    def download_polypizza_model(self, model_id, normalize_size=False, target_size=1.0):
        """Download a model from Poly Pizza by its ID

        Parameters:
        - model_id: The Poly Pizza model ID (from search_polypizza_models)
        - normalize_size: If True, scale the model so its largest dimension equals target_size
        - target_size: The target size in Blender units (meters) for the largest dimension
        """
        temp_dir = None
        try:
            api_key = self._get_polypizza_api_key()
            if not api_key:
                return {"error": "Poly Pizza API key is not configured"}

            headers = dict(REQ_HEADERS)
            headers["x-auth-token"] = api_key

            model, error = _request_model_metadata(model_id, headers)
            if error:
                return error

            download_url = model.get("Download")
            if not download_url:
                return {"error": f"Poly Pizza model '{model_id}' has no downloadable GLB file"}

            content, error = _download_glb_content(download_url)
            if error:
                return error
            if content is None:
                return {"error": "Poly Pizza returned an empty GLB"}

            # Every Poly Pizza model is a single self-contained .glb - no zip,
            # no sidecar textures - so it goes straight to disk and into glTF import.
            temp_dir = tempfile.mkdtemp()
            glb_path = os.path.join(temp_dir, f"{safe_model_id(model_id)}.glb")

            with open(glb_path, "wb") as f:
                f.write(content)

            bpy.ops.import_scene.gltf(filepath=glb_path)

            # Get the imported objects
            imported_objects = list(bpy.context.selected_objects)
            imported_object_names = [obj.name for obj in imported_objects]

            # Clean up temporary files
            with suppress(Exception):
                shutil.rmtree(temp_dir)
            temp_dir = None

            root_objects, all_meshes = split_imported_objects(imported_objects)
            return build_download_result(
                imported_object_names, root_objects, all_meshes, model,
                model_id, normalize_size, target_size,
            )

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection and try again."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Poly Pizza API: {str(e)}"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"Failed to download model: {str(e)}"}
        finally:
            if temp_dir:
                with suppress(Exception):
                    shutil.rmtree(temp_dir)
