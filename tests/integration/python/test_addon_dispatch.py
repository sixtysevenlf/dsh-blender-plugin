"""Dispatch-level integration tests for the split add-on package.

Uses a bpy stub and calls the server's command executor directly. This checks
the 25-name dispatch table and pause gate without a Gateway connection.
"""

from __future__ import annotations

import importlib
import json

import bpy_stub

bpy = bpy_stub.install()

server_module = importlib.import_module("addon.dsh_blender_bridge.server")
protocol = importlib.import_module("addon.dsh_blender_bridge.protocol")
command_registry = importlib.import_module("addon.dsh_blender_bridge.command_registry")

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


def _server(monkeypatch):
    return server_module.DSHBlenderBridgeServer()


def _patch_dispatch_targets(server):
    for name in ALL_COMMANDS:
        setattr(server, name, lambda _name=name, **kwargs: {"handled": _name})


def test_real_get_addon_info_contract():
    server = server_module.DSHBlenderBridgeServer()
    info = server.get_addon_info()
    assert info["name"] == "DSH Blender Bridge"
    assert info["addon_version"] == [2, 0, 0]
    assert info["protocol_version"] == 12
    assert info["blender_version"] == "4.2.10"
    # the handshake derives capabilities from the registry, so it is the full
    # registered command set (25) instead of the old hardcoded 16-name subset.
    assert info["capabilities"] == sorted(command_registry.COMMAND_NAMES)
    assert len(info["capabilities"]) == 25


def test_all_25_contract_commands_dispatch(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    for name in ("dshbb_use_polyhaven", "dshbb_use_hyper3d", "dshbb_use_sketchfab",
                 "dshbb_use_polypizza", "dshbb_use_hunyuan3d"):
        setattr(bpy.context.scene, name, True)

    for name in ALL_COMMANDS:
        response = server.execute_command({"type": name, "params": {}})
        assert response["status"] == "success", (name, response)
        if name == "ping":
            assert response["result"] == {"pong": True}
        else:
            assert response["result"] == {"handled": name}, name

    assert len(ALL_COMMANDS) == len(command_registry.COMMAND_NAMES) == 25


def test_unknown_command_and_handler_exception_shapes(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    unknown = server.execute_command({"type": "definitely_not_a_command", "params": {}})
    assert unknown == {
        "status": "error",
        "message": "Unknown command type: definitely_not_a_command",
    }

    def boom(**kwargs):
        raise RuntimeError("handler boom")

    server.execute_code = boom
    failed = server.execute_command({"type": "execute_code", "params": {}})
    assert failed["status"] == "error"
    assert failed["message"] == "handler boom"


def test_pause_gate_blocks_writes_but_allows_reads_and_ping(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    bpy.context.scene.dshbb_ai_paused = True
    try:
        blocked = server.execute_command({"type": "execute_code", "params": {}})
        assert blocked["status"] == "error"
        assert blocked["error"] == "AI_PAUSED"
        assert "继续 AI 操作" in blocked["message"]

        assert server.execute_command({"type": "ping", "params": {}})["status"] == "success"
        assert server.execute_command({"type": "get_scene_info", "params": {}})["result"] == {
            "handled": "get_scene_info",
        }
        assert server.execute_command({"type": "get_polyhaven_status", "params": {}})["status"] == "success"
        assert server.execute_command({"type": "send_selection_to_dsh", "params": {}})["error"] == "AI_PAUSED"
    finally:
        bpy.context.scene.dshbb_ai_paused = False


def test_gateway_message_framing_and_dispatch(monkeypatch):
    """A Gateway JSON frame becomes one queued command and one JSON response."""
    server = _server(monkeypatch)
    server.running = True
    sent = []
    try:
        server._handle_gateway_message(json.dumps({"type": "ping"}), sent.append)
        assert server.command_queue.qsize() == 1
        assert server._drain_command_queue() == 0.05
        assert [json.loads(raw) for raw in sent] == [
            {"status": "success", "result": {"pong": True}},
        ]
    finally:
        server.running = False


def test_gateway_request_id_is_echoed_on_the_response(monkeypatch):
    server = _server(monkeypatch)
    server.running = True
    sent = []
    try:
        server._handle_gateway_message(
            json.dumps({"type": "ping", "requestId": "req-42"}), sent.append)
        server._drain_command_queue()
        assert json.loads(sent[0])["requestId"] == "req-42"
    finally:
        server.running = False


def test_malformed_gateway_messages_are_ignored(monkeypatch):
    server = _server(monkeypatch)
    sent = []
    server._handle_gateway_message("not json", sent.append)
    server._handle_gateway_message("[]", sent.append)
    assert server.command_queue.empty()
    assert sent == []


def test_gated_commands_are_unknown_while_their_toggle_is_off(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    for name in ("dshbb_use_polyhaven", "dshbb_use_hyper3d", "dshbb_use_sketchfab",
                 "dshbb_use_polypizza", "dshbb_use_hunyuan3d"):
        setattr(bpy.context.scene, name, False)

    for name in GATED_COMMANDS:
        response = server.execute_command({"type": name, "params": {}})
        assert response == {"status": "error", "message": f"Unknown command type: {name}"}, name

def test_params_are_forwarded_as_handler_keyword_arguments(monkeypatch):
    server = _server(monkeypatch)
    seen = {}

    def record(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    server.get_object_info = record
    response = server.execute_command({
        "type": "get_object_info",
        "params": {"name": "Cube", "unused": 1},
    })
    assert response == {"status": "success", "result": {"ok": True}}
    assert seen == {"name": "Cube", "unused": 1}


def test_missing_and_non_mapping_params_are_tolerated(monkeypatch):
    server = _server(monkeypatch)
    server.get_scene_info = lambda: {"ok": True}
    assert server.execute_command({"type": "get_scene_info"})["status"] == "success"
    assert server.execute_command({"type": "get_scene_info", "params": {}})["status"] == "success"
    non_mapping = server.execute_command({"type": "get_scene_info", "params": None})
    assert non_mapping["status"] == "error"
    assert server.execute_command({}) == {
        "status": "error", "message": "Unknown command type: None",
    }


def test_success_and_error_envelopes_are_json_serialisable(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    success = server.execute_command({"type": "get_addon_info"})
    error = server.execute_command({"type": "nope"})
    assert set(success) == {"status", "result"}
    assert set(error) == {"status", "message"}
    assert json.loads(json.dumps(success)) == success
    assert json.loads(json.dumps(error)) == error


def test_pause_gate_does_not_run_the_agent_recorder(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    events = []

    class Recorder:
        def agent_command(self):
            events.append("enter")

            class _Ctx:
                def __enter__(self_inner):
                    return None

                def __exit__(self_inner, *exc):
                    events.append("exit")
                    return False

            return _Ctx()

    monkeypatch.setattr(server_module, "begin_agent_command", lambda: Recorder().agent_command())
    bpy.context.scene.dshbb_ai_paused = True
    try:
        blocked = server.execute_command({"type": "execute_code", "params": {}})
        assert blocked["error"] == "AI_PAUSED"
        assert events == []
        allowed = server.execute_command({"type": "get_scene_info"})
        assert allowed["status"] == "success"
        assert events == ["enter", "exit"]
    finally:
        bpy.context.scene.dshbb_ai_paused = False


def test_every_read_safe_command_survives_the_pause_gate(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    for name in ("dshbb_use_polyhaven", "dshbb_use_hyper3d", "dshbb_use_sketchfab",
                 "dshbb_use_polypizza", "dshbb_use_hunyuan3d"):
        setattr(bpy.context.scene, name, True)
    bpy.context.scene.dshbb_ai_paused = True
    try:
        for name in sorted(protocol.PAUSE_SAFE_COMMANDS):
            if name == "ping":
                continue
            response = server.execute_command({"type": name, "params": {}})
            assert response["status"] == "success", (name, response)
        for name in command_registry.COMMAND_NAMES:
            if name.startswith("get_"):
                assert server.execute_command({"type": name, "params": {}})["status"] == "success", name
    finally:
        bpy.context.scene.dshbb_ai_paused = False
