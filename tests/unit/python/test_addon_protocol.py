"""Unit checks for the add-on command registry and pause policy (protocol.py)."""

from __future__ import annotations

import importlib

import bpy_stub

bpy = bpy_stub.install()

protocol = importlib.import_module("extension.dsh_blender_bridge.protocol")
registry = importlib.import_module("extension.dsh_blender_bridge.command_registry")
compat = importlib.import_module("extension.dsh_blender_bridge.compat")

BASE_COMMANDS = [
    "get_scene_info", "get_world_state_snapshot", "get_addon_info", "get_object_info",
    "get_viewport_screenshot", "execute_code", "describe_node_type", "bpy_api_lookup",
    "get_activity_state", "get_selection", "send_selection_to_dsh",
    "get_collections", "send_collection_to_dsh",
    "get_polyhaven_status", "get_hyper3d_status", "get_sketchfab_status", "get_polypizza_status",
    "get_hunyuan3d_status",
]
GATED_COMMANDS = [
    "get_polyhaven_categories", "search_polyhaven_assets",
    "poll_rodin_job_status",
    "search_sketchfab_models", "get_sketchfab_model_preview",
    "search_polypizza_models",
    "poll_hunyuan_job_status",
]
ALL_COMMANDS = BASE_COMMANDS + GATED_COMMANDS
GATED_GROUPS = {
    "polyhaven": {"get_polyhaven_categories", "search_polyhaven_assets"},
    "hyper3d": {"poll_rodin_job_status"},
    "sketchfab": {"search_sketchfab_models", "get_sketchfab_model_preview"},
    "polypizza": {"search_polypizza_models"},
    "hunyuan": {"poll_hunyuan_job_status"},
}


def test_registry_has_exactly_25_ordered_unique_commands():
    assert len(registry.COMMAND_NAMES) == 25
    assert len(set(registry.COMMAND_NAMES)) == 25
    assert list(registry.COMMAND_NAMES) == ALL_COMMANDS
    assert "ping" not in registry.COMMAND_NAMES
    assert sum(len(names) for _group, names in registry.COMMAND_GROUPS) == 25


def test_command_groups_are_base_plus_five_gates():
    groups = dict(registry.COMMAND_GROUPS)
    assert list(groups) == ["base", "polyhaven", "hyper3d", "sketchfab", "polypizza", "hunyuan"]
    assert set(registry.GATED_COMMAND_GROUPS) == set(groups) - {"base"}
    assert list(groups["base"]) == BASE_COMMANDS
    for group, expected in GATED_GROUPS.items():
        assert set(groups[group]) == expected
        assert list(groups[group]) == [name for name in GATED_COMMANDS if name in expected]
    base_set = set(BASE_COMMANDS)
    for names in GATED_GROUPS.values():
        assert not (base_set & names)


def test_pause_safe_commands_are_exactly_the_read_only_whitelist():
    assert protocol.PAUSE_SAFE_COMMANDS == frozenset({
        "ping", "describe_node_type", "bpy_api_lookup",
        "get_activity_state", "get_selection", "get_collections",
    })
    for name in ALL_COMMANDS:
        expected = name.startswith("get_") or name in protocol.PAUSE_SAFE_COMMANDS
        assert protocol.is_pause_safe_command(name) is expected, name
    assert protocol.is_pause_safe_command("ping") is True
    assert protocol.is_pause_safe_command("describe_node_type") is True
    assert protocol.is_pause_safe_command("bpy_api_lookup") is True
    assert protocol.is_pause_safe_command("execute_code") is False
    assert protocol.is_pause_safe_command("send_selection_to_dsh") is False
    assert protocol.is_pause_safe_command(None) is False
    assert protocol.is_pause_safe_command("") is False


def test_ai_paused_reads_the_scene_flag_and_fails_open():
    previous = getattr(bpy.context.scene, "dshbb_ai_paused", False)
    try:
        bpy.context.scene.dshbb_ai_paused = False
        assert protocol.ai_paused() is False
        bpy.context.scene.dshbb_ai_paused = True
        assert protocol.ai_paused() is True
    finally:
        bpy.context.scene.dshbb_ai_paused = previous


def test_redraw_panels_tolerates_headless_context():
    previous = bpy.context.window_manager
    try:
        bpy.context.window_manager = None
        protocol.redraw_view3d_panels()
    finally:
        bpy.context.window_manager = previous


def test_compat_module_identity():
    assert compat.ADDON_MODULE == compat.__package__
    assert compat.ADDON_MODULE.endswith("dsh_blender_bridge")
