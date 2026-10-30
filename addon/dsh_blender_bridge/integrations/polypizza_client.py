""" Poly Pizza validation and summary helpers. """

POLYPIZZA_API_BASE = "https://api.poly.pizza/v1.1"

# The MCP server resolves human-friendly category/licence names to the numeric
# ids the API filters on, so only ids arrive here. Every query parameter of
# the API is Capitalized (Limit, Page, Category, License, Animated — see
# poly.pizza/apispec/v1.1.yaml): lowercase variants are accepted with HTTP 200
# and then silently ignored, so the capitalisation is load-bearing.


def _polypizza_category_id(category):
    """Validate a numeric category id (names are resolved by the MCP server)."""
    if category is None or category == "":
        return None
    if isinstance(category, bool) or not (
        isinstance(category, int)
        or (isinstance(category, str) and category.strip().lstrip("-").isdigit())
    ):
        raise ValueError(f"Poly Pizza category must be a numeric id in 0-11, got {category!r}")
    value = int(category)
    if not 0 <= value <= 11:
        raise ValueError(f"Poly Pizza category id {value} is out of range (valid ids are 0-11)")
    return value


def _polypizza_licence_id(licence):
    """Validate a numeric licence id (names are resolved by the MCP server)."""
    if licence is None or licence == "":
        return None
    if isinstance(licence, bool) or not (
        isinstance(licence, int)
        or (isinstance(licence, str) and licence.strip().lstrip("-").isdigit())
    ):
        raise ValueError(f"Poly Pizza licence must be 0 (CC-BY) or 1 (CC0), got {licence!r}")
    value = int(licence)
    if value not in (0, 1):
        raise ValueError(f"Poly Pizza licence id {value} is invalid (0 = CC-BY, 1 = CC0)")
    return value


def _polypizza_filter_params(category=None, licence=None, animated=False):
    """Build the query filters for a Poly Pizza search.

    Keys are Capitalized and values numeric because the API silently ignores
    anything else. `Animated` is omitted unless animated-only results were asked
    for: the server treats `Animated=0` as falsy and does not filter on it.
    """
    params = {}
    category_id = _polypizza_category_id(category)
    if category_id is not None:
        params["Category"] = category_id
    licence_id = _polypizza_licence_id(licence)
    if licence_id is not None:
        params["License"] = licence_id
    if animated:
        params["Animated"] = 1
    return params


def _polypizza_summarize_model(model):
    """Trim an API record down to the fields worth sending back over MCP."""
    creator = model.get("Creator") or {}
    return {
        "ID": model.get("ID"),
        "Title": model.get("Title"),
        "Creator": creator.get("Username") if isinstance(creator, dict) else None,
        "Licence": model.get("Licence"),
        "Tri Count": model.get("Tri Count"),
        "Animated": bool(model.get("Animated")),
        "Category": model.get("Category"),
        "Tags": model.get("Tags") or [],
        "Thumbnail": model.get("Thumbnail"),
    }


def _polypizza_cdn_error(status_code, headers, content):
    """Describe a CDN response that is not a GLB, or None when it is one.

    static.poly.pizza sits behind Cloudflare bot management and answers 403 with
    an HTML challenge from datacenter IPs. That is neither an auth failure nor a
    missing model, so it gets its own message.
    """
    if status_code == 200 and content[:4] == b"glTF":
        return None

    headers = headers or {}
    content_type = ""
    for key in ("Content-Type", "content-type"):
        value = headers.get(key)
        if value:
            content_type = str(value).lower()
            break

    challenged = bool(headers.get("cf-mitigated") or headers.get("Cf-Mitigated"))
    looks_like_html = "text/html" in content_type or content[:1] == b"<"

    if challenged or (looks_like_html and status_code != 200):
        return (
            f"Poly Pizza's CDN returned a Cloudflare bot-protection challenge (HTTP {status_code}) "
            "instead of the model file. This is not an API key problem - static.poly.pizza takes no "
            "API key - and the model exists. The CDN blocks datacenter, VPN and cloud IPs; retry from "
            "a residential connection, or download the .glb by hand from https://poly.pizza and import "
            "it with File > Import > glTF 2.0."
        )
    if status_code != 200:
        return f"Poly Pizza model file download failed with status code {status_code}"
    if looks_like_html:
        return (
            "Poly Pizza's CDN returned an HTML page instead of a GLB file. The download link may have "
            "expired; search again to get a fresh one."
        )
    return "Poly Pizza returned a file that is not a valid GLB (missing glTF magic bytes)"
