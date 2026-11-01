""" Poly Haven asset download dispatch. """

import traceback
from urllib.parse import quote

from ...integrations.polyhaven_client import (
    POLYHAVEN_DEFAULT_FORMATS,
    POLYHAVEN_SUPPORTED_FORMATS,
    _polyhaven_api_get,
    _polyhaven_valid_slug,
)


class PolyHavenDownloadMixin:
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
