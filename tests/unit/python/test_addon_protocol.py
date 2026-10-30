"""Unit checks for the add-on command registry and pause policy (protocol.py)."""

from __future__ import annotations

import importlib

import bpy_stub

bpy = bpy_stub.install()

protocol = importlib.import_module("addon.dsh_blender_bridge.protocol")
compat = importlib.import_module("addon.dsh_blender_bridge.compat")

BASE_COMMANDS = [
    "get_scene_info", "get_world_state_snapshot", "get_addon_info", "get_object_info",
    "get_viewport_screenshot", "execute_code", "describe_node_type", "bpy_api_lookup",
    "drain_human_activity", "get_activity_state", "get_selection", "send_selection_to_dsh",
    "get_collections", "send_collection_to_dsh", "get_telemetry_consent", "set_telemetry_consent",
    "get_polyhaven_status", "get_hyper3d_status", "get_sketchfab_status", "get_polypizza_status",
    "get_hunyuan3d_status", "get_tripo_status", "export_scene",
]
GATED_COMMANDS = [
    "get_polyhaven_categories", "search_polyhaven_assets", "download_polyhaven_asset",
    "get_polyhaven_asset_preview", "set_texture",
    "create_rodin_job", "poll_rodin_job_status", "import_generated_asset",
    "search_sketchfab_models", "get_sketchfab_model_preview", "download_sketchfab_model",
    "search_polypizza_models", "download_polypizza_model",
    "create_hunyuan_job", "poll_hunyuan_job_status", "import_generated_asset_hunyuan",
]
ALL_COMMANDS = BASE_COMMANDS + GATED_COMMANDS
GATED_GROUPS = {
    "polyhaven": {"get_polyhaven_categories", "search_polyhaven_assets",
                  "download_polyhaven_asset", "get_polyhaven_asset_preview", "set_texture"},
    "hyper3d": {"create_rodin_job", "poll_rodin_job_status", "import_generated_asset"},
    "sketchfab": {"search_sketchfab_models", "get_sketchfab_model_preview",
                  "download_sketchfab_model"},
    "polypizza": {"search_polypizza_models", "download_polypizza_model"},
    "hunyuan": {"create_hunyuan_job", "poll_hunyuan_job_status", "import_generated_asset_hunyuan"},
}


def test_registry_has_exactly_39_ordered_unique_commands():
    assert len(protocol.COMMAND_NAMES) == 39
    assert len(set(protocol.COMMAND_NAMES)) == 39
    assert list(protocol.COMMAND_NAMES) == ALL_COMMANDS
    assert "ping" not in protocol.COMMAND_NAMES
    assert sum(len(names) for _group, names in protocol.COMMAND_GROUPS) == 39


def test_command_groups_are_base_plus_five_gates():
    groups = dict(protocol.COMMAND_GROUPS)
    assert list(groups) == ["base", "polyhaven", "hyper3d", "sketchfab", "polypizza", "hunyuan"]
    assert set(protocol.GATED_COMMAND_GROUPS) == set(groups) - {"base"}
    assert list(groups["base"]) == BASE_COMMANDS
    for group, expected in GATED_GROUPS.items():
        assert set(groups[group]) == expected
        assert list(groups[group]) == [name for name in GATED_COMMANDS if name in expected]
    base_set = set(BASE_COMMANDS)
    for names in GATED_GROUPS.values():
        assert not (base_set & names)


def test_pause_safe_commands_are_exactly_the_read_only_whitelist():
    assert protocol.PAUSE_SAFE_COMMANDS == frozenset({
        "ping", "drain_human_activity", "describe_node_type", "bpy_api_lookup",
        "get_activity_state", "get_selection", "get_collections",
    })
    for name in ALL_COMMANDS:
        expected = name.startswith("get_") or name in protocol.PAUSE_SAFE_COMMANDS
        assert protocol._is_pause_safe_command(name) is expected, name
    assert protocol._is_pause_safe_command("ping") is True
    assert protocol._is_pause_safe_command("drain_human_activity") is True
    assert protocol._is_pause_safe_command("describe_node_type") is True
    assert protocol._is_pause_safe_command("bpy_api_lookup") is True
    assert protocol._is_pause_safe_command("execute_code") is False
    assert protocol._is_pause_safe_command("set_texture") is False
    assert protocol._is_pause_safe_command("send_selection_to_dsh") is False
    assert protocol._is_pause_safe_command("export_scene") is False
    assert protocol._is_pause_safe_command(None) is False
    assert protocol._is_pause_safe_command("") is False


def test_ai_paused_reads_the_scene_flag_and_fails_open():
    previous = getattr(bpy.context.scene, "dshbb_ai_paused", False)
    try:
        bpy.context.scene.dshbb_ai_paused = False
        assert protocol._ai_paused() is False
        bpy.context.scene.dshbb_ai_paused = True
        assert protocol._ai_paused() is True
    finally:
        bpy.context.scene.dshbb_ai_paused = previous


def test_redraw_panels_tolerates_headless_context():
    previous = bpy.context.window_manager
    try:
        bpy.context.window_manager = None
        protocol._redraw_view3d_panels()
    finally:
        bpy.context.window_manager = previous


def test_compat_module_identity_and_version_helpers():
    assert compat.ADDON_MODULE == compat.__package__
    assert compat.ADDON_MODULE.endswith("dsh_blender_bridge")
    assert compat.bpy_version() == (4, 2, 10)
    assert compat.is_bpy_at_least(3, 0, 0) is True
    assert compat.is_bpy_at_least(4, 2, 10) is True
    assert compat.is_bpy_at_least(4, 3, 0) is False
