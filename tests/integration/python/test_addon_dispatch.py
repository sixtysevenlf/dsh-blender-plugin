"""Dispatch-level integration tests for the split add-on package.

Uses a bpy stub and calls the socket server's command executor directly. This
checks the 39-name dispatch table and pause gate without opening a socket.
"""

from __future__ import annotations

import importlib
import json
import socket
import threading
import time

import bpy_stub

bpy = bpy_stub.install()

server_module = importlib.import_module("addon.dsh_blender_bridge.server")
protocol = importlib.import_module("addon.dsh_blender_bridge.protocol")

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


def _server(monkeypatch):
    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.delenv("BLENDERMCP_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.setattr(server_module, "premium_tripo_status", lambda: {"handled": "get_tripo_status"})
    return server_module.DSHBlenderBridgeServer(port=0)


def _patch_dispatch_targets(server):
    for name in ALL_COMMANDS:
        setattr(server, name, lambda _name=name, **kwargs: {"handled": _name})


def test_real_get_addon_info_contract():
    server = server_module.DSHBlenderBridgeServer(port=0)
    info = server.get_addon_info()
    assert info["name"] == "DSH Blender Bridge"
    assert info["addon_version"] == [1, 11]
    assert info["protocol_version"] == 11
    assert info["blender_version"] == "4.2.10"
    assert len(info["capabilities"]) == 16


def test_all_39_contract_commands_dispatch(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    for name in ("dshbb_use_polyhaven", "dshbb_use_hyper3d", "dshbb_use_sketchfab",
                 "dshbb_use_polypizza", "dshbb_use_hunyuan3d", "dshbb_use_tripo"):
        setattr(bpy.context.scene, name, True)

    for name in ALL_COMMANDS:
        response = server.execute_command({"type": name, "params": {}})
        assert response["status"] == "success", (name, response)
        if name == "ping":
            assert response["result"] == {"pong": True}
        else:
            assert response["result"] == {"handled": name}, name

    assert len(ALL_COMMANDS) == len(protocol.COMMAND_NAMES) == 39


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
        assert server.execute_command({"type": "set_texture", "params": {}})["error"] == "AI_PAUSED"
    finally:
        bpy.context.scene.dshbb_ai_paused = False


def test_socket_framing_and_dispatch(monkeypatch):
    """The real socket handler frames JSON and the drain loop answers ping."""
    server = _server(monkeypatch)
    server.running = True
    client, peer = socket.socketpair()
    thread = threading.Thread(target=server._handle_client, args=(client,), daemon=True)
    thread.start()
    try:
        peer.sendall(json.dumps({"type": "ping"}).encode("utf-8"))
        deadline = time.time() + 2
        while server.command_queue.empty() and time.time() < deadline:
            time.sleep(0.01)
        assert not server.command_queue.empty(), "socket handler did not frame the command"
        server._drain_command_queue()
        raw = peer.recv(65536)
        assert json.loads(raw.decode("utf-8")) == {
            "status": "success",
            "result": {"pong": True},
        }
    finally:
        server.running = False
        try:
            peer.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        peer.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_gated_commands_are_unknown_while_their_toggle_is_off(monkeypatch):
    server = _server(monkeypatch)
    _patch_dispatch_targets(server)
    for name in ("dshbb_use_polyhaven", "dshbb_use_hyper3d", "dshbb_use_sketchfab",
                 "dshbb_use_polypizza", "dshbb_use_hunyuan3d", "dshbb_use_tripo"):
        setattr(bpy.context.scene, name, False)

    for name in GATED_COMMANDS:
        response = server.execute_command({"type": name, "params": {}})
        assert response == {"status": "error", "message": f"Unknown command type: {name}"}, name
