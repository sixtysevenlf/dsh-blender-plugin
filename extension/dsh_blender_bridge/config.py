""" Configuration, environment variables, preferences and auto-start. """

import bpy
import requests
import os
from bpy.app.handlers import persistent

from .compat import ADDON_MODULE
from .gateway_schema import ADDON_WS_PATH, GATEWAY_DEFAULT_PORT

# 兼容回退（旧环境变量名）：先读新名，取不到再读改名前的旧名。
# 旧名只出现在下面这张表里，用户已经设置的环境变量不会失效。
_LEGACY_ENV_VARS = {
    # These are environment variable names, not credentials.
    "DSHBB_HYPER3D_API_KEY": "BLENDERMCP_HYPER3D_API_KEY",  # gitleaks:allow
    "DSHBB_SKETCHFAB_API_KEY": "BLENDERMCP_SKETCHFAB_API_KEY",  # gitleaks:allow
    "DSHBB_POLYPIZZA_API_KEY": "BLENDERMCP_POLYPIZZA_API_KEY",  # gitleaks:allow
    "DSHBB_HUNYUAN3D_SECRET_ID": "BLENDERMCP_HUNYUAN3D_SECRET_ID",  # gitleaks:allow
    "DSHBB_HUNYUAN3D_SECRET_KEY": "BLENDERMCP_HUNYUAN3D_SECRET_KEY",  # gitleaks:allow
    "DSHBB_HUNYUAN3D_API_URL": "BLENDERMCP_HUNYUAN3D_API_URL",  # gitleaks:allow
}


def read_env_var(name):
    """读环境变量：新名优先，兼容回退到旧名（改名前的版本）。"""
    value = os.getenv(name)
    if value:
        return value
    legacy = _LEGACY_ENV_VARS.get(name)
    return os.getenv(legacy) if legacy else None


# ---------------------------------------------------------------- credentials
# 凭据单一所有者（4.5）：add-on preferences 是唯一的持久化位置，环境变量仍是
# 后备（DSHBB_* 新名优先，旧名 BLENDERMCP_* 兼容，见 read_env_var）。历史上每个
# 凭据还在 Scene 上镜像了一份（随 .blend 保存），server 与面板都要写"有 prefs 用
# prefs、否则用 scene"的分支，导致同一把 key 用哪个取决于当前打开的文件。现在
# Scene 副本已删除；免费试用按钮用进程内 override（reload 后失效），不覆盖用户
# 已保存的私钥。
_session_credentials = {}


def set_session_credential(name, value):
    """Set a session-only credential override (empty value clears it)."""
    if value:
        _session_credentials[name] = value
    else:
        _session_credentials.pop(name, None)


def clear_session_credentials():
    """Drop every session-only credential override (add-on reload / reset)."""
    _session_credentials.clear()


def get_credential(pref_attr, env_var):
    """Read one credential: session override -> add-on preferences -> env var."""
    override = _session_credentials.get(pref_attr)
    if override:
        return override
    prefs = get_dshbb_addon_preferences()
    if prefs:
        value = getattr(prefs, pref_attr, "")
        if value:
            return value
    return read_env_var(env_var) or ""


RODIN_FREE_TRIAL_KEY = "vibecoding"
# Public site link shown by the N-panel; kept here so UI code does not import
# the integration clients.
POLYHAVEN_SITE = "https://polyhaven.com"

# Add User-Agent as required by Poly Haven API
REQ_HEADERS = requests.utils.default_headers()
REQ_HEADERS.update({"User-Agent": "dsh-blender-bridge"})

# 3.1 network policy: asset integrations call external APIs. Blender exposes a
# global online-access switch; keep local scene/bridge commands usable when it
# is off, but refuse network commands with a structured policy error.
def online_access_enabled():
    try:
        return bool(bpy.app.online_access)
    except Exception:
        return True


def online_access_error():
    return {
        "status": "error",
        "code": "network_policy",
        "message": "Blender online access is disabled; this command needs network access.",
        "fix": "Enable Edit > Preferences > System > Network > Allow Online Access, then retry. "
               "Local scene and bridge commands keep working while it is off.",
    }


# Set when the user disconnects so opening another blend file does not restart
# the server behind their back. A manual connect or add-on reload clears it.
_user_stopped_server = False


# ------------------------------------------------------------- gateway
# 3.1: the add-on dials the DSH Gateway; it no longer listens on a port.
# Resolution order: env DSHBB_GATEWAY_PORT -> add-on preference -> default.
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PATH = ADDON_WS_PATH


def get_gateway_host():
    """Gateway host is fixed to loopback; the Gateway is always local."""
    return GATEWAY_HOST


def get_gateway_port():
    """Resolve the Gateway port: env var, then add-on preference, then default."""
    raw = read_env_var("DSHBB_GATEWAY_PORT")
    if raw:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if 1 <= value <= 65535:
            return value
        print(f"DSH Blender Bridge: ignoring invalid DSHBB_GATEWAY_PORT={raw!r}")

    preferences = get_dshbb_addon_preferences()
    preferred = int(getattr(preferences, "gateway_port", 0) or 0) if preferences else 0
    if 1 <= preferred <= 65535:
        return preferred
    return GATEWAY_DEFAULT_PORT


def gateway_url(port=None):
    """WebSocket URL the add-on dials (for logs and the N-panel)."""
    return "ws://%s:%d%s" % (GATEWAY_HOST, int(port or get_gateway_port()), GATEWAY_PATH)


def _dshbb_ensure_server_running():
    """Start the Gateway connection after Blender's UI and scene context exist.

    Returning a delay asks Blender's timer system to retry when a launch-time
    context race prevented the first attempt.
    """
    if bpy.app.background:
        return None

    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return 0.5

    server = getattr(bpy.types, "dshbb_server", None)
    if not scene.dshbb_auto_start_server or _user_stopped_server:
        scene.dshbb_server_running = bool(server is not None and server.running)
        return None

    if server is None:
        from .server import DSHBlenderBridgeServer

        server = DSHBlenderBridgeServer()
        bpy.types.dshbb_server = server  # pyright: ignore[reportAttributeAccessIssue]

    if not server.running:
        server.start()

    scene.dshbb_server_running = server.running

    return None if server.running else 1.0


def _dshbb_schedule_auto_start(delay=0.5):
    """Schedule one persistent startup callback if none is already pending."""
    if not bpy.app.timers.is_registered(_dshbb_ensure_server_running):
        bpy.app.timers.register(
            _dshbb_ensure_server_running,
            first_interval=delay,
            persistent=True,
        )


@persistent
def _dshbb_load_post(_unused):
    """Retry auto-start after Blender loads a startup file or another blend."""
    _dshbb_schedule_auto_start()


def register_auto_start():
    """Install the load handler and defer the initial startup attempt."""
    global _user_stopped_server
    _user_stopped_server = False
    if _dshbb_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_dshbb_load_post)
    _dshbb_schedule_auto_start()


def unregister_auto_start():
    """Remove callbacks owned by the add-on before it is disabled."""
    if _dshbb_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_dshbb_load_post)
    if bpy.app.timers.is_registered(_dshbb_ensure_server_running):
        bpy.app.timers.unregister(_dshbb_ensure_server_running)


def get_dshbb_addon_preferences(context=None):
    """Get add-on preferences object if available."""
    if context is None:
        context = bpy.context
    addon = context.preferences.addons.get(ADDON_MODULE)  # pyright: ignore[reportAttributeAccessIssue]
    return addon.preferences if addon else None


DEFAULT_DSH_BACKEND = "http://127.0.0.1:39877"


def dshbb_backend_url():
    try:
        url = bpy.context.scene.dshbb_backend_url  # pyright: ignore[reportAttributeAccessIssue]
    except Exception:
        url = ""
    return (url or DEFAULT_DSH_BACKEND).rstrip("/")
