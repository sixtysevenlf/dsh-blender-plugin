""" Premium API status, license and request helpers. """

import bpy
import requests
from datetime import datetime

from ..protocol import ADDON_VERSION
from ..config import get_dshbb_addon_preferences, read_env_var
from ..protocol import ADDON_PROTOCOL_VERSION

PREMIUM_API_URL_DEFAULT = "https://dbunjhuejdwwcafedslw.supabase.co/functions/v1/premium"
PREMIUM_PRICING_URL = "https://mcp-for-blender.com/premium"
PREMIUM_MANAGE_URL_DEFAULT = "https://mcp-for-blender.com/account"
PREMIUM_TIMEOUT = 30
PREMIUM_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # fal's image-to-3D input limit

# Fallback wording for codes the addon raises itself or that arrive without a
# message. The server's message wins when it sends one.
PREMIUM_ERROR_MESSAGES = {
    "INVALID_KEY": "License key not recognised. Check it in Preferences, or switch to your own API keys.",
    "NOT_ACTIVATED": "This device isn't activated. Click Activate in Preferences.",
    "ACTIVATION_LIMIT": "Key is active on 3 devices. Deactivate one from its Blender Preferences.",
    "NO_ACTIVE_SUBSCRIPTION": "Your Premium subscription isn't active. Manage it at the account link.",
    "NOT_IN_PLAN": "High-quality models are included in Pro. Use standard quality, or upgrade.",
    "QUOTA_EXHAUSTED": "You've used all generations this month, or upgrade to Pro.",
    "TOO_MANY_IN_FLIGHT": "Wait for the current generation to finish, then try again.",
    "SERVICE_PAUSED": "Premium generation is paused briefly. Your own API keys still work.",
    "PROVIDER_ERROR": "The model provider failed. This attempt wasn't counted; try again.",
    "ADDON_OUTDATED": "Update the addon to keep using Premium.",
    "NETWORK_ERROR": "Couldn't reach the Premium server. Your own API keys still work.",
    "BAD_RESPONSE": "The Premium server sent an unexpected reply. Try again shortly.",
}

# The scene checkboxes that turn each generator on, in both modes.
PREMIUM_INTEGRATIONS = {
    "hyper3d": ("Hyper3D Rodin", "dshbb_use_hyper3d"),
    "hunyuan3d": ("Hunyuan3D", "dshbb_use_hunyuan3d"),
    "tripo": ("Tripo", "dshbb_use_tripo"),
}

# Last usage summary the server sent. draw() reads only this, never the network.
_premium_status = {}
# Hunyuan's import tool takes a URL, but an OBJ result is three files, so
# remember each finished job's full file list by its model URL.
_premium_files_by_url = {}


def _premium_prefs():
    try:
        return get_dshbb_addon_preferences()
    except Exception:
        return None


def _premium_setting(pref_attr, env_var):
    prefs = _premium_prefs()
    value = getattr(prefs, pref_attr, "") if prefs else ""
    return value or (read_env_var(env_var) or "")


def premium_license_key():
    return _premium_setting("premium_license_key", "DSHBB_PREMIUM_LICENSE_KEY").strip()


def premium_instance_id():
    return _premium_setting("premium_instance_id", "DSHBB_PREMIUM_INSTANCE_ID").strip()


def premium_api_url():
    return (read_env_var("DSHBB_PREMIUM_API_URL") or PREMIUM_API_URL_DEFAULT).rstrip("/")


def premium_active():
    """Premium replaces the user's own keys when selected in Preferences, or
    for headless runs that supply a license key by env var."""
    prefs = _premium_prefs()
    if prefs is not None and getattr(prefs, "generation_source", "BYOK") == "PREMIUM":
        return True
    return bool(read_env_var("DSHBB_PREMIUM_LICENSE_KEY"))


def premium_error(code, message=None):
    message = message or PREMIUM_ERROR_MESSAGES.get(code, code)
    return {"error": message, "code": code, "message": message}


def _premium_tag_redraw():
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {"VIEW_3D", "PREFERENCES"}:
                    area.tag_redraw()
    except Exception:
        pass


def _premium_remember_usage(data):
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict):
        _premium_status.clear()
        _premium_status.update(usage)
        _premium_tag_redraw()


def premium_request(method, path, payload=None, auth=True):
    """Send one request to the Premium server. Never raises: transport
    failures and non-JSON replies come back as {error, code, message}."""
    headers = {
        "X-Addon-Version": ".".join(str(part) for part in ADDON_VERSION),
        "X-Addon-Protocol": str(ADDON_PROTOCOL_VERSION),
    }
    if auth:
        key = premium_license_key()
        if not key:
            return premium_error("INVALID_KEY", "No Premium license key set. Paste it in Preferences, or switch to your own API keys.")
        instance_id = premium_instance_id()
        if not instance_id:
            return premium_error("NOT_ACTIVATED")
        headers["Authorization"] = f"Bearer {key}"
        headers["X-Instance-Id"] = instance_id
    try:
        response = requests.request(
            method, premium_api_url() + path, headers=headers, json=payload, timeout=PREMIUM_TIMEOUT,
        )
    except Exception as e:
        return premium_error("NETWORK_ERROR", f"{PREMIUM_ERROR_MESSAGES['NETWORK_ERROR']} ({e})")
    try:
        data = response.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return premium_error("BAD_RESPONSE", f"{PREMIUM_ERROR_MESSAGES['BAD_RESPONSE']} (HTTP {response.status_code})")
    _premium_remember_usage(data)
    if response.status_code >= 400 or data.get("code"):
        code = data.get("code") or "BAD_RESPONSE"
        return premium_error(code, data.get("message"))
    return data


def _premium_format_date(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return f"{moment.strftime('%b')} {moment.day}"


def _premium_remaining(status, pool):
    counts = status.get(pool) or {}
    return int(counts.get("remaining", max(0, counts.get("total", 0) - counts.get("used", 0))))


def premium_short_status():
    """One line for the sidebar, from the cache only. The cache is empty after
    a restart until the next request, which isn't the same as no plan."""
    if not premium_instance_id():
        return "Premium: not activated"
    if not _premium_status:
        return "Premium: activated"
    if not _premium_status.get("tier"):
        return "Premium: subscription not active"
    return (f"Premium: {str(_premium_status['tier']).title()}, "
            f"{_premium_remaining(_premium_status, 'standard')} + "
            f"{_premium_remaining(_premium_status, 'premium')} left")


def premium_summary(status=None):
    status = _premium_status if status is None else status
    tier = status.get("tier")
    if not tier:
        return "Premium: no active subscription."
    text = (f"Premium ({str(tier).title()}): {_premium_remaining(status, 'standard')} standard and "
            f"{_premium_remaining(status, 'premium')} high-quality generations left")
    reset = _premium_format_date(status.get("resets_at"))
    return f"{text}, resets {reset}" if reset else text


def _premium_high_locked():
    """True once the server has said this plan has no high-quality allowance."""
    return bool(_premium_status.get("tier")) and (_premium_status.get("premium") or {}).get("total", 0) == 0


def premium_default_quality():
    prefs = _premium_prefs()
    value = getattr(prefs, "premium_default_quality", "standard") if prefs else "standard"
    if value not in ("standard", "high") or (value == "high" and _premium_high_locked()):
        return "standard"
    return value


def _premium_quality(model, quality, from_image):
    """An explicit quality wins. Otherwise Rodin, and Tripo from an image, use
    high (their only option), and everything else the user's default."""
    if quality:
        return quality
    if model == "rodin" or (model == "tripo" and from_image):
        return "high"
    return premium_default_quality()


def premium_integration_status(integration, enabled):
    """Reply for get_hyper3d_status / get_hunyuan3d_status / get_tripo_status."""
    label, prop = PREMIUM_INTEGRATIONS[integration]
    if not enabled:
        return {"enabled": False, "mode": "PREMIUM", "message": (
            f"{label} is turned off. To use it through Premium, check '{label}' in the DSH Blender Bridge "
            "sidebar (press N in the 3D Viewport).")}
    result = premium_request("GET", "/status")
    if result.get("code") in {"NETWORK_ERROR", "BAD_RESPONSE"} and _premium_status.get("tier"):
        summary = premium_summary() + " (cached; the Premium server is unreachable right now)"
    elif result.get("code"):
        return {"enabled": False, "mode": "PREMIUM", "message": result["message"]}
    else:
        summary = premium_summary()
    quality = (f"Default quality: {premium_default_quality()}. Pass quality='high' only when the user asks "
               "for more detail; it uses a high-quality generation.")
    flow = {
        "hyper3d": ("Mode: PREMIUM. Behaves like FAL_AI mode: pass request_id to poll_rodin_job_status and "
                    "import_generated_asset. Every Rodin generation counts as high-quality."),
        "hunyuan3d": ("Mode: PREMIUM. Follow the OFFICIAL_API flow: generate_hunyuan3d_model, then "
                      "poll_hunyuan_job_status, then import_generated_asset_hunyuan with the ResultFile3Ds URL. "
                      + quality),
        "tripo": "Mode: PREMIUM. From an image, Tripo is high-quality only. " + quality,
    }[integration]
    return {"enabled": True, "mode": "PREMIUM", "message": f"{summary}. {flow}"}
