""" Poly Haven HTTP, cache, parsing and material helpers. """

import re
import time
import requests
import os
import zlib
import hashlib, hmac, base64
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl
from contextlib import contextmanager, redirect_stdout, suppress

from ..protocol import ADDON_VERSION
from ..config import REQ_HEADERS

POLYHAVEN_API_BASE = "https://api.polyhaven.com"

# Versioned, so Poly Haven can tell which integration its traffic is coming from
# and how many people it is serving. Kept separate from the shared REQ_HEADERS
# because Poly Pizza sends that one too.
POLYHAVEN_HEADERS = dict(REQ_HEADERS)
POLYHAVEN_HEADERS["User-Agent"] = (
    "dsh-blender-bridge/" + ".".join(str(part) for part in ADDON_VERSION)
    + " (+https://github.com/ahujasid/blender-mcp)"
)

# (connect, read). The read timeout applies per socket read rather than to the
# whole transfer, so streaming a large HDRI never trips it - but a dead
# connection no longer hangs Blender's main thread indefinitely.
POLYHAVEN_API_TIMEOUT = (10, 30)
POLYHAVEN_FILE_TIMEOUT = (10, 60)

POLYHAVEN_CHUNK_SIZE = 1024 * 1024

# What we can actually import, per asset type. Checked BEFORE downloading
# anything: the API lists a `usd` entry for every model, which used to pass the
# "is this format present?" guard, get downloaded in full, and only then be
# rejected as an unsupported format.
POLYHAVEN_SUPPORTED_FORMATS = {
    "hdris": ("hdr", "exr"),
    "textures": ("jpg", "png", "exr"),
    "models": ("blend",),
}

POLYHAVEN_DEFAULT_FORMATS = {"hdris": "hdr", "textures": "jpg", "models": "blend"}

# Models are imported from the .blend and nothing else. Poly Haven authors its
# models in Blender and generates every other format from that file, so glTF and
# FBX are lossy renderings of a material that is sitting right there - node
# groups collapse to a base colour, and procedural setups do not survive at all.
#
# glTF stays as a fallback for one case only: a .blend written by a newer
# Blender than the one running, which cannot be opened at all. See
# _polyhaven_blend_version.
POLYHAVEN_MODEL_FALLBACK_FORMAT = "gltf"

# Poly Haven's /files map keys, and what each map drives. Their casing is
# inconsistent and load-bearing - "Diffuse", "Rough", "Metal" and
# "Displacement" are capitalised while "nor_gl" and "arm" are not - so these
# are matched exactly instead of being lower-cased and guessed at.
#
# This is the set that drives a Principled BSDF directly, and it covers what
# Poly Haven's own .blend materials use for the large majority of the library.
# Of the rest the API offers, "arm" is an ORM repacking of maps already here,
# "rough_ao" is roughness with AO baked in, and "nor_dx" is the other normal map
# convention. "AO", "spec" and "Bump" need extra nodes to be worth anything.
#
# It is not a complete match for every asset: measured across the 860 published
# textures, 114 ship a .blend referencing a map not in this table - 74 use "AO",
# and 30 fabrics drive Anisotropic, Anisotropic Rotation and IOR from
# "anisotropy_strength", "anisotropy_rotation" and "spec_ior". Those materials
# come out flatter here than the artist built them.
#
# Downloading every map and then leaving most of them unconnected is what cost
# 7.4MB to build a 1k material that connected 1.9MB of it. This table brings
# that asset down to 3.9MB, all of it wired.
POLYHAVEN_TEXTURE_MAPS = {
    "Diffuse": "base_color",
    "Rough": "roughness",
    "Metal": "metallic",
    "Displacement": "displacement",
    "nor_gl": "normal",
    "nor_dx": "normal",
}

# Only the albedo is colour data; every other map is values the shader reads.
POLYHAVEN_COLOR_ROLES = {"base_color"}

POLYHAVEN_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

POLYHAVEN_SITE = "https://polyhaven.com"

# `type` is an integer in the API's asset records.
POLYHAVEN_ASSET_TYPES = {0: "hdris", 1: "textures", 2: "models"}

POLYHAVEN_SEARCH_LIMIT = 20
POLYHAVEN_SEARCH_MAX_LIMIT = 50

# Levels of the category tree returned when every asset type is asked for at
# once. Filtering on a category is inclusive, so a parent still selects
# everything nested beneath it.
POLYHAVEN_TAXONOMY_DEPTH_ALL = 2

# Poly Haven publishes thumbnails at 256px. The CDN resizes from the query
# string, so a preview worth looking at costs no stored file.
POLYHAVEN_PREVIEW_SIZE = 512


class PolyHavenAPIError(Exception):
    """A non-2xx from the Poly Haven API, with the status kept.

    Needed because 429 and 503 want different handling from a generic failure:
    one means back off, the other means the search index is unavailable and the
    API is telling us to fall back to matching keywords ourselves.
    """

    def __init__(self, status, retry_after=None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


# Poly Haven serves the asset list with `Cache-Control: max-age=43200` and an
# ETag, and both were being discarded: every search re-fetched all 2.44MB of it.
# The TTL here matches theirs, and once it lapses the ETag usually turns the
# refetch into a 304.
POLYHAVEN_CACHE_TTL = 12 * 60 * 60

# Bounded so a session that searches every type and taxonomy cannot grow without
# limit. The asset list is by far the largest entry, and there are four of those.
POLYHAVEN_CACHE_MAX_ENTRIES = 16

_polyhaven_cache = {}

# A counter rather than a clock. time.time() has ~15ms resolution on Windows, so
# entries touched inside one burst of calls tie, and min() then evicts whichever
# happens to come first in the dict - which can be the very entry this is
# protecting.
_polyhaven_cache_clock = 0


def _polyhaven_cache_touch():
    global _polyhaven_cache_clock
    _polyhaven_cache_clock += 1
    return _polyhaven_cache_clock


def _polyhaven_cache_key(path, params):
    return path, tuple(sorted((params or {}).items()))


def _polyhaven_api_get(path, params=None, cache=False):
    """GET a Poly Haven API endpoint, raising on anything but a 2xx.

    With cache=True the response is held for POLYHAVEN_CACHE_TTL, and revalidated
    with If-None-Match after that rather than re-downloaded.
    """
    key = _polyhaven_cache_key(path, params)
    entry = _polyhaven_cache.get(key) if cache else None
    headers = dict(POLYHAVEN_HEADERS)

    if entry is not None:
        if time.time() - entry["fetched"] < POLYHAVEN_CACHE_TTL:
            entry["used"] = _polyhaven_cache_touch()
            return entry["payload"]
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]

    response = requests.get(
        f"{POLYHAVEN_API_BASE}/{path}",
        params=params,
        headers=headers,
        timeout=POLYHAVEN_API_TIMEOUT,
    )

    if entry is not None and response.status_code == 304:
        entry["fetched"] = time.time()
        entry["used"] = _polyhaven_cache_touch()
        return entry["payload"]

    if response.status_code >= 400:
        raise PolyHavenAPIError(
            response.status_code,
            getattr(response, "headers", {}).get("Retry-After"),
        )

    payload = response.json()

    if cache:
        if len(_polyhaven_cache) >= POLYHAVEN_CACHE_MAX_ENTRIES:
            # Least recently USED, not least recently fetched. Evicting on fetch
            # time is strictly FIFO, because a hit never refreshes it - and the
            # asset list is by construction the first thing fetched in a session
            # and then only ever read, so it was always the first entry thrown
            # out, displaced by one-shot search payloads a tenth of a percent its
            # size. Its ETag went with it, so the refetch could not revalidate.
            coldest = min(_polyhaven_cache, key=lambda k: _polyhaven_cache[k]["used"])
            _polyhaven_cache.pop(coldest, None)
        _polyhaven_cache[key] = {
            "payload": payload,
            "etag": getattr(response, "headers", {}).get("ETag"),
            "fetched": time.time(),
            "used": _polyhaven_cache_touch(),
        }

    return payload


def _polyhaven_valid_slug(asset_id):
    """Poly Haven slugs are always [A-Za-z0-9_-].

    Asset ids arrive from the model and are used to build the names of the files
    downloaded into the temporary directory, so they are checked once here
    rather than escaped differently in each place.
    """
    return bool(POLYHAVEN_SLUG_RE.match(asset_id or ""))


def _polyhaven_download(file_info, dest_path):
    """Stream one file to dest_path, verifying the md5 the API published.

    Streaming matters: resolution="24k", file_format="exr" is a valid call and
    that file is 2.4GB, which the previous response.content read materialised
    in memory in full before writing it back out again.
    """
    expected = file_info.get("md5")

    response = requests.get(
        file_info["url"],
        headers=POLYHAVEN_HEADERS,
        stream=True,
        timeout=POLYHAVEN_FILE_TIMEOUT,
    )
    response.raise_for_status()

    digest = hashlib.md5()
    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=POLYHAVEN_CHUNK_SIZE):
            if not chunk:
                continue
            digest.update(chunk)
            f.write(chunk)

    if expected and digest.hexdigest() != expected:
        with suppress(OSError):
            os.unlink(dest_path)
        raise ValueError(
            f"Checksum mismatch for {os.path.basename(dest_path)}: "
            "the download was truncated or corrupted"
        )
    return dest_path


def _polyhaven_uncompress_head(raw):
    """The start of a .blend, which is usually compressed on disk.

    Blender wrote gzip up to 2.93 and zstd from 3.0. Both decompressors are
    incremental, so a truncated prefix decompresses to a shorter prefix rather
    than raising.
    """
    if raw[:7] == b"BLENDER":
        return raw
    if raw[:2] == b"\x1f\x8b":
        with suppress(Exception):
            return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw)
        return None
    try:
        import zstandard
    except ImportError:
        # Not bundled with every Blender build. Without it the version cannot be
        # read, and the import falls back to trying the append and handling the
        # failure - which is the same outcome, one download later.
        return None
    with suppress(Exception):
        return zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    return None


def _polyhaven_blend_version(path):
    """(major, minor) of the Blender that wrote this .blend, or None.

    Blender cannot open a file written by a newer version than itself, and Poly
    Haven's models span 2.93 to 5.0 because each was saved by whichever Blender
    compiled it. The version is in the file header, in one of two layouts:

        up to Blender 4.4:   BLENDER-v293
        from Blender 4.5:    BLENDER17-01v0502

    where the digits straight after BLENDER are the header's own length, and the
    version field grows from three characters to four.
    """
    try:
        with open(path, "rb") as f:
            head = _polyhaven_uncompress_head(f.read(1 << 16))
    except OSError:
        return None

    if not head or not head.startswith(b"BLENDER"):
        return None

    try:
        if head[7:9].isdigit():
            return int(head[13:15]), int(head[15:17])
        return int(head[9:10]), int(head[10:12])
    except (ValueError, IndexError):
        return None


def _polyhaven_category_paths(nodes, depth=None, _level=1):
    """Flatten the category tree to its paths, which is what filters take."""
    paths = []
    for node in nodes or []:
        if node.get("path"):
            paths.append(node["path"])
        if depth is None or _level < depth:
            paths.extend(_polyhaven_category_paths(node.get("children"), depth, _level + 1))
    return paths


def _polyhaven_taxonomy(asset_type, depth=None):
    """The category tree and attribute schema for one asset type, trimmed.

    The raw response is 60-80KB per type, most of it descriptions, UUIDs and
    URL slugs that nothing here uses. The paths are what a `categories` filter
    takes, and matching on them is inclusive, so a parent path selects
    everything beneath it.
    """
    payload = _polyhaven_api_get(f"taxonomy/{quote(asset_type, safe='')}", cache=True)

    attributes = {}
    for key, spec in (payload.get("attributes") or {}).items():
        if isinstance(spec, dict):
            attributes[key] = {
                field: spec[field]
                for field in ("type", "enum", "description")
                if field in spec
            }

    return {
        "type": payload.get("type") or asset_type,
        "categories": _polyhaven_category_paths(payload.get("categories"), depth),
        "attributes": attributes,
    }


def _polyhaven_asset_url(slug):
    return f"{POLYHAVEN_SITE}/a/{quote(slug, safe='')}"


def _polyhaven_asset_record(slug):
    """One asset's metadata, taken from the cached asset list where possible.

    /info/{id} is the same record plus a few internal fields, so it is only
    worth a request when the list has not already been fetched.
    """
    for entry in _polyhaven_cache.values():
        payload = entry.get("payload")
        if isinstance(payload, dict):
            record = payload.get(slug)
            if isinstance(record, dict) and "name" in record:
                return record
    return _polyhaven_api_get(f"info/{quote(slug, safe='')}", cache=True)


def _polyhaven_preview_url(thumbnail_url, size=POLYHAVEN_PREVIEW_SIZE):
    """Resize the published thumbnail without losing its cache-busting `v`.

    Poly Haven's CDN resizes from the query string, so a larger preview costs no
    stored file - but `thumbnail_url` also carries a `v` holding a hash of the
    asset's images, and a URL rebuilt by hand without it can be served a
    year-old thumbnail for an asset whose renders have since been replaced.
    """
    parts = urlparse(thumbnail_url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "width" in params or "height" in params:
        params["width"] = str(size)
        params["height"] = str(size)
    return urlunparse(parts._replace(query=urlencode(params)))


def _polyhaven_summarize_asset(slug, record):
    """Trim an /assets record down to what is worth sending back over MCP.

    The full record is around a kilobyte of JSON per asset and the whole page of
    results crosses the socket in one message, so twenty untrimmed records is
    most of what the model then has to read.
    """
    authors = record.get("authors") or {}
    summary = {
        "id": slug,
        "name": record.get("name") or slug,
        "type": POLYHAVEN_ASSET_TYPES.get(record.get("type"), "unknown"),
        "url": _polyhaven_asset_url(slug),
        "authors": sorted(authors) if isinstance(authors, dict) else authors,
        "downloads": record.get("download_count"),
    }

    for key in ("description", "category", "tags", "attributes", "max_resolution"):
        value = record.get(key)
        if value:
            summary[key] = value

    # Real-world size in millimetres, published for every texture. Without it
    # there is no way to know that a wall texture is 1.8m across, and the
    # material gets whatever tiling the object's UVs happen to give it.
    if record.get("dimensions"):
        summary["dimensions_mm"] = record["dimensions"]

    return summary


def _polyhaven_search(query, asset_type):
    """The full ranked list of slugs from Poly Haven's search endpoint.

    The array order IS the ranking - it fuses a vector lane and a keyword lane
    by position - so it must not be re-sorted by `score`, which reports vector
    similarity alone.

    No `limit` is sent. The endpoint returns the whole ranked list by design,
    because callers are expected to intersect it with whatever they already
    hold; asking for the first N and then filtering those would drop matches
    that were simply further down.
    """
    params = {"q": query}
    if asset_type and asset_type != "all":
        params["t"] = asset_type

    payload = _polyhaven_api_get("search", params=params, cache=True)
    return [r["slug"] for r in (payload.get("results") or []) if r.get("slug")]


def _polyhaven_keyword_match(query, assets):
    """The fallback the API asks for when it answers a search with 503."""
    terms = [term for term in query.split() if term]
    scored = []
    for slug, record in assets.items():
        haystack = " ".join([
            slug.replace("_", " "),
            str(record.get("name") or ""),
            " ".join(record.get("tags") or []),
            str(record.get("category") or ""),
        ]).lower()
        hits = sum(1 for term in terms if term in haystack)
        if hits:
            scored.append((hits, record.get("download_count", 0), slug))

    scored.sort(reverse=True)
    return [slug for _hits, _downloads, slug in scored]


def _polyhaven_resolution_rank(resolution):
    """"4k" -> 4, so resolutions sort numerically rather than as strings."""
    try:
        return int(str(resolution).rstrip("k"))
    except (TypeError, ValueError):
        return -1


def _polyhaven_sorted_resolutions(resolutions):
    return sorted(resolutions, key=lambda res: (_polyhaven_resolution_rank(res) < 0,
                                                _polyhaven_resolution_rank(res)))


def _polyhaven_available(files_data, asset_type):
    """Describe what an asset actually offers, for use in error messages.

    The three "not available" errors this replaces were f-strings with nothing
    interpolated into them, so an agent that guessed a resolution wrong had no
    way to correct itself except to guess again - and each guess cost another
    round trip.
    """
    supported = POLYHAVEN_SUPPORTED_FORMATS.get(asset_type, ())
    resolutions, formats = set(), set()
    for by_resolution in files_data.values():
        if not isinstance(by_resolution, dict):
            continue
        for resolution, by_format in by_resolution.items():
            if not isinstance(by_format, dict):
                continue
            present = {fmt for fmt in by_format if fmt in supported}
            if present:
                resolutions.add(resolution)
                formats |= present

    return (
        "available resolutions: "
        + (", ".join(_polyhaven_sorted_resolutions(resolutions)) or "none")
        + "; formats: "
        + (", ".join(sorted(formats)) or "none")
    )


def _polyhaven_select_texture_maps(files_data, resolution, file_format):
    """The map keys worth downloading, in the order they should be laid out."""
    selected = {}
    for key, role in POLYHAVEN_TEXTURE_MAPS.items():
        by_resolution = files_data.get(key)
        if not isinstance(by_resolution, dict):
            continue
        if file_format in by_resolution.get(resolution, {}):
            selected[key] = role

    # OpenGL-convention normals are what Blender's Normal Map node expects.
    # nor_dx is the same map with the green channel flipped, and is only worth
    # fetching for the few assets that ship no nor_gl.
    if "nor_gl" in selected:
        selected.pop("nor_dx", None)

    # A handful of textures name their albedo something other than "Diffuse" -
    # the multi-variant fabrics ship col_1/col_2/col_03 instead of one map.
    # Taking the first is a guess, but a material with no base colour at all is
    # the failure this whole table exists to prevent.
    if "base_color" not in selected.values():
        for key in sorted(files_data):
            if not key.lower().startswith(("col", "diff")):
                continue
            by_resolution = files_data.get(key)
            if isinstance(by_resolution, dict) and file_format in by_resolution.get(resolution, {}):
                selected[key] = "base_color"
                break

    return selected


def _polyhaven_set_colorspace(image, is_color_data):
    """Set a colorspace that exists on this Blender build.

    The names moved around in 4.0, so each candidate is tried in turn rather
    than assuming any one of them is present.
    """
    candidates = ("sRGB",) if is_color_data else ("Non-Color", "Linear Rec.709", "Linear")
    for name in candidates:
        try:
            image.colorspace_settings.name = name
            return name
        except Exception:
            continue
    return image.colorspace_settings.name


def _polyhaven_authors(asset_id):
    """Author names for an asset. Best effort - never fails an import."""
    with suppress(Exception):
        record = _polyhaven_asset_record(asset_id)
        authors = record.get("authors") or {}
        return sorted(authors) if isinstance(authors, dict) else list(authors)
    return []


def _polyhaven_dimensions_mm(asset_id):
    """A texture's real-world size in millimetres. Best effort, like the authors.

    Read from the record _polyhaven_authors has already fetched, so it costs no
    extra request. Length two means a texture: a model's `dimensions` is a
    bounding box, which is a different measurement and is readable from the
    object itself once it is in the scene.
    """
    with suppress(Exception):
        dimensions = _polyhaven_asset_record(asset_id).get("dimensions")
        if isinstance(dimensions, (list, tuple)) and len(dimensions) == 2:
            return [float(value) for value in dimensions]
    return None


def _polyhaven_mapping_node(node_tree):
    """The node every image node's Vector input is routed through, if it is still there."""
    with suppress(Exception):
        for node in node_tree.nodes:
            if node.type == 'MAPPING':
                return node
    return None


def _polyhaven_tag(datablocks, asset_id, resolution=None, authors=None, dimensions=None):
    """Record where a datablock came from, in the file that keeps it.

    Two jobs. It is the lookup key between downloading a texture and applying
    it - the old code recovered the map type by parsing the image's name, taking
    the last underscore-separated token, which turned "nor_gl" into "gl" and
    left the download path and set_texture disagreeing about what a map was
    called.

    It is also where the asset came from, in the same shape the Poly Pizza
    integration writes its polypizza_* properties. Poly Haven's assets are CC0
    and require no attribution, ever - but custom properties are saved into the
    .blend, so whoever opens the file in a year can still find the asset's page,
    who made it, and the resolutions they did not download.
    """
    for block in datablocks:
        if block is None:
            continue
        with suppress(Exception):
            block["polyhaven_id"] = asset_id
            block["polyhaven_url"] = _polyhaven_asset_url(asset_id)
            block["polyhaven_licence"] = "CC0"
            if resolution:
                block["polyhaven_resolution"] = resolution
            if authors:
                block["polyhaven_authors"] = ", ".join(authors)
            elif "polyhaven_authors" in block.keys():
                # The lookup is best-effort and comes back empty on any API
                # failure. Every other field is overwritten regardless, so
                # leaving a previous asset's artist behind on a datablock that
                # is being re-tagged would credit them for somebody else's work.
                del block["polyhaven_authors"]
            # The texture's real-world size, the same measurement Poly Haven's
            # own add-on writes onto the materials it ships. Saved into the
            # .blend because tiling cannot be worked out without it and it is
            # otherwise visible exactly once, in a search result.
            if dimensions:
                block["polyhaven_scale_mm"] = list(dimensions)
            elif "polyhaven_scale_mm" in block.keys():
                del block["polyhaven_scale_mm"]
