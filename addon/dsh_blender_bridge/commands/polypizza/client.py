""" Poly Pizza status and search commands. """

import json

import bpy
import requests
from urllib.parse import quote

from ...config import REQ_HEADERS
from ...integrations.polypizza_client import (
    POLYPIZZA_API_BASE,
    _polypizza_filter_params,
    _polypizza_summarize_model,
)


def _search_request(keyword, filters, limit, page):
    """Build the (url, params) pair for one Poly Pizza search call."""
    # Limit and Page are Capitalized like the filters: lowercase
    # variants are silently ignored and the API then serves its
    # default page of 32.
    params = dict(filters)
    params["Limit"] = max(1, min(int(limit), 32))
    if page is not None:
        params["Page"] = page

    if keyword:
        url = f"{POLYPIZZA_API_BASE}/search/{quote(keyword, safe='')}"
    else:
        url = f"{POLYPIZZA_API_BASE}/search"
    return url, params


def _search_error_for_status(status_code):
    """Translate a non-200 search status into the command's error envelope."""
    if status_code in (401, 403):
        return {"error": f"Poly Pizza authentication failed ({status_code}). Check your API key."}
    if status_code == 400:
        return {"error": (
            "Poly Pizza rejected the search parameters (400). Category must be an id in "
            "0-11 and licence 0 (CC-BY) or 1 (CC0)."
        )}
    if status_code == 429:
        return {"error": "Poly Pizza rate limit exceeded (100 requests/second). Try again in a moment."}
    if status_code != 200:
        return {"error": f"Poly Pizza API request failed with status code {status_code}"}
    return None


def _search_result(response_data, filters):
    """Reduce a successful search response to the command's result envelope."""
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


class PolypizzaClientMixin:
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
                            5. Restart the DSH Blender Bridge connection"""
            }
        else:
            return {
                "enabled": False,
                "message": """Poly Pizza integration is currently disabled. To enable it:
                            1. Get a free API key at https://poly.pizza/settings/api
                            2. In the 3D Viewport, find the DSH Blender Bridge panel in the sidebar (press N if hidden)
                            3. Check the 'Use assets from Poly Pizza' checkbox
                            4. Enter your Poly Pizza API Key
                            5. Restart the DSH Blender Bridge connection"""
            }

    def search_polypizza_models(self, query=None, category=None, licence=None,
                                animated=False, limit=20, page=None):
        """Search for models on Poly Pizza by keyword and/or filters

        Parameters:
        - query: Keyword to search for. When omitted, at least one filter is
                 required: the bare /search endpoint answers 400 without one.
        - category: Numeric category id (0-11); the DSH plugin resolves names
        - licence: Numeric licence id (0 = CC-BY, 1 = CC0); the DSH plugin resolves names
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

            url, params = _search_request(keyword, filters, limit, page)

            headers = dict(REQ_HEADERS)
            headers["x-auth-token"] = api_key

            response = requests.get(url, headers=headers, params=params, timeout=30)

            error = _search_error_for_status(response.status_code)
            if error:
                return error

            return _search_result(response.json(), filters)

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Poly Pizza API: {str(e)}"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": str(e)}
