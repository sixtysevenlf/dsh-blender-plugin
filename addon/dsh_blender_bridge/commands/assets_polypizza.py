""" Poly Pizza asset commands. """

import re
import bpy
import mathutils
import json
import requests
import tempfile
import os
import shutil
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl
from contextlib import contextmanager, redirect_stdout, suppress

from ..assets.io import collect_mesh_children
from ..config import REQ_HEADERS
from ..integrations.polypizza_client import POLYPIZZA_API_BASE, _polypizza_cdn_error, _polypizza_filter_params, _polypizza_summarize_model

class PolypizzaCommands:
    def get_polypizza_status(self):
        """Get the current status of Poly Pizza integration"""
        enabled = bpy.context.scene.dshbb_use_polypizza
        api_key = self._get_polypizza_api_key()

        if enabled and api_key:
            return {
                "enabled": True,
                "message": "Poly Pizza integration is enabled and ready to use."
            }
        elif enabled and not api_key:
            return {
                "enabled": False,
                "message": """Poly Pizza integration is currently enabled, but API key is not given. To enable it:
                            1. Get a free API key at https://poly.pizza/settings/api
                            2. In the 3D Viewport, find the DSH Blender Bridge panel in the sidebar (press N if hidden)
                            3. Keep the 'Use Poly Pizza' checkbox checked
                            4. Enter your Poly Pizza API Key
                            5. Restart the connection to Claude"""
            }
        else:
            return {
                "enabled": False,
                "message": """Poly Pizza integration is currently disabled. To enable it:
                            1. Get a free API key at https://poly.pizza/settings/api
                            2. In the 3D Viewport, find the DSH Blender Bridge panel in the sidebar (press N if hidden)
                            3. Check the 'Use assets from Poly Pizza' checkbox
                            4. Enter your Poly Pizza API Key
                            5. Restart the connection to Claude"""
            }

    def search_polypizza_models(self, query=None, category=None, licence=None,
                                animated=False, limit=20, page=None):
        """Search for models on Poly Pizza by keyword and/or filters

        Parameters:
        - query: Keyword to search for. When omitted, at least one filter is
                 required: the bare /search endpoint answers 400 without one.
        - category: Numeric category id (0-11); the MCP server resolves names
        - licence: Numeric licence id (0 = CC-BY, 1 = CC0); the MCP server resolves names
        - animated: When True, return only animated models
        - limit: Maximum number of results to return (the API caps a page at 32)
        - page: Optional 0-based page number
        """
        try:
            api_key = self._get_polypizza_api_key()
            if not api_key:
                return {"error": "Poly Pizza API key is not configured"}

            try:
                filters = _polypizza_filter_params(category, licence, animated)
            except ValueError as e:
                return {"error": str(e)}

            keyword = (query or "").strip()
            if not keyword and not filters:
                return {"error": (
                    "Poly Pizza needs a search keyword or at least one filter "
                    "(category, licence, or animated=True). An unfiltered listing of the "
                    "whole catalogue is rejected by the API with HTTP 400."
                )}

            # Limit and Page are Capitalized like the filters: lowercase
            # variants are silently ignored and the API then serves its
            # default page of 32.
            params = dict(filters)
            params["Limit"] = max(1, min(int(limit), 32))
            if page is not None:
                params["Page"] = page

            headers = dict(REQ_HEADERS)
            headers["x-auth-token"] = api_key

            if keyword:
                url = f"{POLYPIZZA_API_BASE}/search/{quote(keyword, safe='')}"
            else:
                url = f"{POLYPIZZA_API_BASE}/search"

            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code in (401, 403):
                return {"error": f"Poly Pizza authentication failed ({response.status_code}). Check your API key."}

            if response.status_code == 400:
                return {"error": (
                    "Poly Pizza rejected the search parameters (400). Category must be an id in "
                    "0-11 and licence 0 (CC-BY) or 1 (CC0)."
                )}

            if response.status_code == 429:
                return {"error": "Poly Pizza rate limit exceeded (100 requests/second). Try again in a moment."}

            if response.status_code != 200:
                return {"error": f"Poly Pizza API request failed with status code {response.status_code}"}

            response_data = response.json()

            if response_data is None:
                return {"error": "Received empty response from Poly Pizza API"}

            results = response_data.get("results", [])
            if not isinstance(results, list):
                return {"error": f"Unexpected response format from Poly Pizza API: {response_data}"}

            return {
                "total": response_data.get("total", len(results)),
                "results": [_polypizza_summarize_model(m) for m in results if isinstance(m, dict)],
                "filters_applied": filters,
            }

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Poly Pizza API: {str(e)}"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": str(e)}

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

            response = requests.get(
                f"{POLYPIZZA_API_BASE}/model/{quote(str(model_id), safe='')}",
                headers=headers,
                timeout=30
            )

            if response.status_code in (401, 403):
                return {"error": f"Poly Pizza authentication failed ({response.status_code}). Check your API key."}

            if response.status_code == 404:
                return {"error": f"No Poly Pizza model found with ID '{model_id}'"}

            if response.status_code != 200:
                return {"error": f"Poly Pizza model lookup failed with status code {response.status_code}"}

            model = response.json()

            if not isinstance(model, dict):
                return {"error": f"Unexpected response format from Poly Pizza API: {model}"}

            download_url = model.get("Download")
            if not download_url:
                return {"error": f"Poly Pizza model '{model_id}' has no downloadable GLB file"}

            # The CDN takes no API key and must never be sent one: it is a
            # separate host from the API.
            file_response = requests.get(download_url, headers=dict(REQ_HEADERS), timeout=60)

            cdn_error = _polypizza_cdn_error(
                file_response.status_code,
                getattr(file_response, "headers", None),
                file_response.content or b"",
            )
            if cdn_error:
                return {"error": cdn_error}

            # Every Poly Pizza model is a single self-contained .glb - no zip,
            # no sidecar textures - so it goes straight to disk and into glTF import.
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(model_id)) or "model"
            temp_dir = tempfile.mkdtemp()
            glb_path = os.path.join(temp_dir, f"{safe_id}.glb")

            with open(glb_path, "wb") as f:
                f.write(file_response.content)

            bpy.ops.import_scene.gltf(filepath=glb_path)

            # Get the imported objects
            imported_objects = list(bpy.context.selected_objects)
            imported_object_names = [obj.name for obj in imported_objects]

            # Clean up temporary files
            with suppress(Exception):
                shutil.rmtree(temp_dir)
            temp_dir = None

            # Find root objects (objects without parents in the imported set)
            root_objects = [obj for obj in imported_objects if obj.parent is None]

            # 69% of the catalogue is CC-BY, so the credit line has to outlive
            # the session. Custom properties are saved into the .blend.
            attribution = model.get("Attribution") or ""
            licence = model.get("Licence") or ""
            for root in root_objects:
                root["polypizza_attribution"] = attribution
                root["polypizza_id"] = model.get("ID") or str(model_id)
                root["polypizza_licence"] = licence

            # Helper function to recursively get all mesh children
            # Collect ALL meshes from the entire hierarchy (starting from roots)
            all_meshes = []
            for obj in root_objects:
                all_meshes.extend(collect_mesh_children(obj))

            if all_meshes:
                # Calculate combined world bounding box for all meshes
                all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
                all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))

                for mesh_obj in all_meshes:
                    # Get world-space bounding box corners
                    for corner in mesh_obj.bound_box:
                        world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                        all_min.x = min(all_min.x, world_corner.x)
                        all_min.y = min(all_min.y, world_corner.y)
                        all_min.z = min(all_min.z, world_corner.z)
                        all_max.x = max(all_max.x, world_corner.x)
                        all_max.y = max(all_max.y, world_corner.y)
                        all_max.z = max(all_max.z, world_corner.z)

                # Calculate dimensions
                dimensions = [
                    all_max.x - all_min.x,
                    all_max.y - all_min.y,
                    all_max.z - all_min.z
                ]
                max_dimension = max(dimensions)

                # Apply normalization if requested
                scale_applied = 1.0
                if normalize_size and max_dimension > 0:
                    scale_factor = target_size / max_dimension
                    scale_applied = scale_factor

                    # Only apply scale to ROOT objects (not children!)
                    # Child objects inherit parent's scale through matrix_world
                    for root in root_objects:
                        root.scale = (
                            root.scale.x * scale_factor,
                            root.scale.y * scale_factor,
                            root.scale.z * scale_factor
                        )

                    # Update the scene to recalculate matrix_world for all objects
                    bpy.context.view_layer.update()

                    # Recalculate bounding box after scaling
                    all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
                    all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))

                    for mesh_obj in all_meshes:
                        for corner in mesh_obj.bound_box:
                            world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                            all_min.x = min(all_min.x, world_corner.x)
                            all_min.y = min(all_min.y, world_corner.y)
                            all_min.z = min(all_min.z, world_corner.z)
                            all_max.x = max(all_max.x, world_corner.x)
                            all_max.y = max(all_max.y, world_corner.y)
                            all_max.z = max(all_max.z, world_corner.z)

                    dimensions = [
                        all_max.x - all_min.x,
                        all_max.y - all_min.y,
                        all_max.z - all_min.z
                    ]

                world_bounding_box = [[all_min.x, all_min.y, all_min.z], [all_max.x, all_max.y, all_max.z]]
            else:
                world_bounding_box = None
                dimensions = None
                scale_applied = 1.0

            result = {
                "success": True,
                "message": "Model imported successfully",
                "imported_objects": imported_object_names,
                "model_id": model.get("ID") or str(model_id),
                "title": model.get("Title"),
                "licence": licence,
                "attribution": attribution,
                "tri_count": model.get("Tri Count"),
            }

            if world_bounding_box:
                result["world_bounding_box"] = world_bounding_box
            if dimensions:
                result["dimensions"] = [round(d, 4) for d in dimensions]
            if normalize_size:
                result["scale_applied"] = round(scale_applied, 6)
                result["normalized"] = True

            return result

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
