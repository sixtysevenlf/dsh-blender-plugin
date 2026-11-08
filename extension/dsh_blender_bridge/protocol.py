""" Protocol version, pause policy and the command registry. """

import bpy

from .command_registry import PAUSE_SAFE_COMMANDS
from .gateway_schema import ADDON_PROTOCOL_VERSION as ADDON_PROTOCOL_VERSION
from .gateway_schema import ADDON_VERSION as ADDON_VERSION_TEXT

# Generated from config/gateway.json (scripts/gen-gateway-schema.mjs); never
# hard-code the handshake version here.
ADDON_VERSION = tuple(int(part) for part in ADDON_VERSION_TEXT.split("."))

# Per-snapshot object cap for get_world_state_snapshot.
MAX_SNAPSHOT_OBJECTS = 4000

# Selected-name cap for get_world_state_snapshot: select-all in a large scene
# would otherwise make `selected` the dominant field of both step snapshots.
MAX_SNAPSHOT_SELECTED = 1000


def ai_paused():
    try:
        return bool(getattr(bpy.context.scene, "dshbb_ai_paused", False))
    except Exception:
        return False


def is_pause_safe_command(cmd):
    """只读命令在白名单里；其余（execute_code / 资产下载 / 生成 / 导入）在暂停时拒绝。"""
    cmd = str(cmd or "")
    if cmd in PAUSE_SAFE_COMMANDS:
        return True
    if cmd.startswith("get_"):
        return True
    return False


def redraw_view3d_panels():
    try:
        for wm in bpy.context.window_manager.windows:  # pyright: ignore[reportAttributeAccessIssue]
            for area in wm.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass


ADDON_NAME = "DSH Blender Bridge"
