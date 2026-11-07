""" Poly Haven asset commands. """

import bpy

from .polyhaven import PolyHavenImportMixin
from ..integrations.polyhaven_client import (
    POLYHAVEN_SEARCH_LIMIT,
    POLYHAVEN_SEARCH_MAX_LIMIT,
    POLYHAVEN_TAXONOMY_DEPTH_ALL,
    PolyHavenAPIError,
    _polyhaven_api_get,
    _polyhaven_keyword_match,
    _polyhaven_search,
    _polyhaven_summarize_asset,
    _polyhaven_taxonomy,
)


def _search_params(asset_type, category, attributes):
    """Build the Poly Haven assets query and validate the asset type."""
    params = {}

    if asset_type and asset_type != "all":
        if asset_type not in ["hdris", "textures", "models"]:
            return None, {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
        params["type"] = asset_type

    # category, not categories. The two are different filters over
    # different vocabularies: categories is the legacy flat tag list
    # ("outdoor", "man made", "floor"), while category takes the
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
    return params, None


def _search_limit(limit):
    try:
        limit = int(limit) if limit else POLYHAVEN_SEARCH_LIMIT
    except (TypeError, ValueError):
        limit = POLYHAVEN_SEARCH_LIMIT
    return max(1, min(limit, POLYHAVEN_SEARCH_MAX_LIMIT))


def _filter_min_size(assets, min_size_m):
    """Apply the client-side real-world size floor. Returns (assets, note, error)."""
    if not min_size_m:
        return assets, None, None
    try:
        floor_mm = float(min_size_m) * 1000
    except (TypeError, ValueError):
        return None, None, {"error": f"min_size_m must be a number, got {min_size_m!r}"}

    # Filtered here rather than at the API, which publishes a real-world
    # size for every texture but takes no filter on it. Free: the records
    # are already in hand. Anything that publishes no size cannot satisfy
    # a floor on it and drops out - HDRIs have none.
    before = len(assets)
    assets = {
        slug: record for slug, record in assets.items()
        if max(record.get("dimensions") or [0]) >= floor_mm
    }
    note = None
    if before and not assets:
        # An empty page reads as "Poly Haven does not have this",
        # which is a different and much worse statement than "the
        # size floor is above everything that matched".
        note = (f"Nothing matching the other filters is {floor_mm / 1000:g}m or "
                "larger. Most textures are 1-4m, and HDRIs have no real-world "
                "size at all. Lower min_size_m or leave it out.")
    return assets, note, None


def _rank_assets(assets, query, asset_type):
    """Return (ordered_slugs, note, error) for the already-filtered assets."""
    if not query:
        # Rank before truncating. The previous order was whatever the API
        # happened to return, which is sorted by slug - and because models
        # are the only assets with capitalised slugs, the first 20 of an
        # unfiltered list were 20 models. asset_type="all" could not return
        # a single HDRI or texture, and the library's most downloaded assets
        # were unreachable by any call.
        ordered = sorted(
            assets, key=lambda slug: assets[slug].get("download_count", 0), reverse=True)
        return ordered, None, None

    try:
        ranked = _polyhaven_search(query, asset_type)
    except PolyHavenAPIError as e:
        if e.status == 429:
            wait = f" Retry in {e.retry_after}s." if e.retry_after else ""
            return None, None, {"error": f"Poly Haven is rate limiting searches from this "
                                         f"address.{wait}"}
        if e.status != 503:
            raise
        # The API documents a 503 as "the query could not be embedded,
        # fall back to your own keyword matching".
        ranked = _polyhaven_keyword_match(query, assets)
        note = ("Poly Haven's semantic search was unavailable, so these are plain "
                "keyword matches and the ranking is weaker than usual.")
        return [slug for slug in ranked if slug in assets], note, None

    # /search knows nothing about the category and attribute filters,
    # so its ranking is intersected with the filtered list here. That
    # is why the whole ranked list is asked for rather than the first
    # limit of it: filtering a page that the server already cut can
    # only shrink it, and the matches would be the ones further down.
    return [slug for slug in ranked if slug in assets], None, None


class PolyHavenCommands(PolyHavenImportMixin):
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
                            3. Restart the DSH Blender Bridge connection"""
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
            params, error = _search_params(asset_type, category, attributes)
            if error:
                return error

            limit = _search_limit(limit)

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

            assets, note, error = _filter_min_size(assets, min_size_m)
            if error:
                return error
            assert assets is not None

            # Trimmed and lower-cased so equivalent queries share a cache entry,
            # both here and at Poly Haven's edge.
            query = (query or "").strip().lower()

            ordered, rank_note, error = _rank_assets(assets, query, asset_type)
            if error:
                return error
            if rank_note is not None:
                note = rank_note
            assert ordered is not None

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

