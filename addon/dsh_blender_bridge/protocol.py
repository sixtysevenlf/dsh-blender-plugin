""" Protocol version, pause policy and the command registry. """

import bpy

# Keep in sync with dsh_blender_bridge.addon_manager.EXPECTED_ADDON_PROTOCOL_VERSION.
ADDON_PROTOCOL_VERSION = 11

# Per-snapshot object cap for get_world_state_snapshot. Keep in sync with
# dsh_blender_bridge.trajectory.MAX_SNAPSHOT_OBJECTS.
MAX_SNAPSHOT_OBJECTS = 4000

# Selected-name cap for get_world_state_snapshot: select-all in a large scene
# would otherwise make `selected` the dominant field of both step snapshots.
# Keep in sync with dsh_blender_bridge.trajectory.MAX_SNAPSHOT_SELECTED.
MAX_SNAPSHOT_SELECTED = 1000


PAUSE_SAFE_COMMANDS = frozenset({
    "ping", "drain_human_activity", "describe_node_type", "bpy_api_lookup",
    "get_activity_state", "get_selection", "get_collections",
})


def _ai_paused():
    try:
        return bool(getattr(bpy.context.scene, "dshbb_ai_paused", False))
    except Exception:
        return False


def _is_pause_safe_command(cmd):
    """只读命令在白名单里；其余（execute_code / 资产下载 / 生成 / 导入）在暂停时拒绝。"""
    cmd = str(cmd or "")
    if cmd in PAUSE_SAFE_COMMANDS:
        return True
    if cmd.startswith("get_"):
        return True
    return False


def _redraw_view3d_panels():
    try:
        for wm in bpy.context.window_manager.windows:
            for area in wm.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception:
        pass


ADDON_NAME = "DSH Blender Bridge"
ADDON_VERSION = (1, 11)

COMMAND_GROUPS = (
    ("base", (
        "get_scene_info", "get_world_state_snapshot", "get_addon_info", "get_object_info",
        "get_viewport_screenshot", "execute_code", "describe_node_type", "bpy_api_lookup",
        "drain_human_activity", "get_activity_state", "get_selection", "send_selection_to_dsh",
        "get_collections", "send_collection_to_dsh", "get_telemetry_consent", "set_telemetry_consent",
        "get_polyhaven_status", "get_hyper3d_status", "get_sketchfab_status", "get_polypizza_status",
        "get_hunyuan3d_status", "get_tripo_status", "export_scene",
    )),
    ("polyhaven", ("get_polyhaven_categories", "search_polyhaven_assets", "download_polyhaven_asset", "get_polyhaven_asset_preview", "set_texture")),
    ("hyper3d", ("create_rodin_job", "poll_rodin_job_status", "import_generated_asset")),
    ("sketchfab", ("search_sketchfab_models", "get_sketchfab_model_preview", "download_sketchfab_model")),
    ("polypizza", ("search_polypizza_models", "download_polypizza_model")),
    ("hunyuan", ("create_hunyuan_job", "poll_hunyuan_job_status", "import_generated_asset_hunyuan")),
)

GATED_COMMAND_GROUPS = frozenset({"polyhaven", "hyper3d", "sketchfab", "polypizza", "hunyuan"})
COMMAND_NAMES = tuple(name for _group, names in COMMAND_GROUPS for name in names)
