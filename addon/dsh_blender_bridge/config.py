""" Configuration, environment variables, preferences and auto-start. """

import bpy
import socket
import requests
import os
from bpy.app.handlers import persistent

from .compat import ADDON_MODULE

# 兼容回退（旧环境变量名）：先读新名，取不到再读改名前的旧名。
# 旧名只出现在下面这张表里，用户已经设置的环境变量不会失效。
_LEGACY_ENV_VARS = {
    "DSHBB_HYPER3D_API_KEY": "BLENDERMCP_HYPER3D_API_KEY",
    "DSHBB_SKETCHFAB_API_KEY": "BLENDERMCP_SKETCHFAB_API_KEY",
    "DSHBB_POLYPIZZA_API_KEY": "BLENDERMCP_POLYPIZZA_API_KEY",
    "DSHBB_HUNYUAN3D_SECRET_ID": "BLENDERMCP_HUNYUAN3D_SECRET_ID",
    "DSHBB_HUNYUAN3D_SECRET_KEY": "BLENDERMCP_HUNYUAN3D_SECRET_KEY",
    "DSHBB_HUNYUAN3D_API_URL": "BLENDERMCP_HUNYUAN3D_API_URL",
    "DSHBB_PREMIUM_LICENSE_KEY": "BLENDERMCP_PREMIUM_LICENSE_KEY",
    "DSHBB_PREMIUM_INSTANCE_ID": "BLENDERMCP_PREMIUM_INSTANCE_ID",
    "DSHBB_PREMIUM_API_URL": "BLENDERMCP_PREMIUM_API_URL",
}


def read_env_var(name):
    """读环境变量：新名优先，兼容回退到旧名（改名前的版本）。"""
    value = os.getenv(name)
    if value:
        return value
    legacy = _LEGACY_ENV_VARS.get(name)
    return os.getenv(legacy) if legacy else None


RODIN_FREE_TRIAL_KEY = "vibecoding"
DISCORD_URL = "https://discord.gg/SNqPn4TcKQ"

# Add User-Agent as required by Poly Haven API
REQ_HEADERS = requests.utils.default_headers()
REQ_HEADERS.update({"User-Agent": "dsh-blender-bridge"})

# Set when the user disconnects so opening another blend file does not restart
# the server behind their back. A manual connect or add-on reload clears it.
_user_stopped_server = False


def _dshbb_port_has_listener(host, port):
    """Return True when another process already owns the MCP endpoint."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.15)
    try:
        return probe.connect_ex((host, port)) == 0
    finally:
        probe.close()


def _dshbb_ensure_server_running():
    """Start the bridge after Blender's UI and scene context are ready.

    Returning a delay asks Blender's timer system to retry when a launch-time
    socket or context race prevented the first attempt.
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

    port = scene.dshbb_port
    if server is None:
        from .server import DSHBlenderBridgeServer

        if _dshbb_port_has_listener("localhost", port):
            scene.dshbb_server_running = False
            print(f"DSH Blender Bridge: port {port} is already in use; auto-start skipped.")
            return None
        server = DSHBlenderBridgeServer(port=port)
        bpy.types.dshbb_server = server

    if not server.running:
        # Safe while stopped and necessary when a newly loaded scene selects a
        # different port. Never retarget an active connection.
        server.port = port
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


def _dshbb_register_auto_start():
    """Install the load handler and defer the initial startup attempt."""
    global _user_stopped_server
    _user_stopped_server = False
    if _dshbb_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_dshbb_load_post)
    _dshbb_schedule_auto_start()


def _dshbb_unregister_auto_start():
    """Remove callbacks owned by the add-on before it is disabled."""
    if _dshbb_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_dshbb_load_post)
    if bpy.app.timers.is_registered(_dshbb_ensure_server_running):
        bpy.app.timers.unregister(_dshbb_ensure_server_running)


def get_dshbb_addon_preferences(context=None):
    """Get add-on preferences object if available."""
    if context is None:
        context = bpy.context
    addon = context.preferences.addons.get(ADDON_MODULE)
    return addon.preferences if addon else None


DEFAULT_DSH_BACKEND = "http://127.0.0.1:9877"


def _dshbb_backend_url():
    try:
        url = bpy.context.scene.dshbb_backend_url
    except Exception:
        url = ""
    return (url or DEFAULT_DSH_BACKEND).rstrip("/")
