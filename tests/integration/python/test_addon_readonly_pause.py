"""Pause-safe read-only see/presence path.

Sessions spec: pause blocks agent writes but not reads, see, or presence. The
add-on keeps execute_code blocked and exposes both read-only payloads on the
already pause-safe internal command get_viewport_screenshot, so no command is
added and the 25 registered / 26 wire counts stay intact.
"""

from __future__ import annotations

import importlib
import json
import sys
import types

import bpy_stub

bpy = bpy_stub.install()

server_module = importlib.import_module("extension.dsh_blender_bridge.server")
protocol = importlib.import_module("extension.dsh_blender_bridge.protocol")


def _server():
    return server_module.DSHBlenderBridgeServer()


def test_read_only_capture_and_presence_stay_available_while_paused():
    assert protocol.is_pause_safe_command("get_viewport_screenshot") is True
    assert protocol.is_pause_safe_command("execute_code") is False
    server = _server()
    bpy.context.scene.dshbb_ai_paused = True
    try:
        blocked = server.execute_command({"type": "execute_code", "params": {"code": "1+1"}})
        assert blocked["error"] == "AI_PAUSED"

        presence = server.execute_command({
            "type": "get_viewport_screenshot",
            "params": {"presence": json.dumps({"agents": [], "paused": True})},
        })
        assert presence["status"] == "success", presence
        assert presence["result"] == {"success": True, "presence": True}
    finally:
        bpy.context.scene.dshbb_ai_paused = False


def test_full_window_area_capture_stays_available_while_paused(monkeypatch, tmp_path):
    shot = tmp_path / "area.png"
    screen = types.SimpleNamespace(areas=[types.SimpleNamespace(type="VIEW_3D")])
    window = types.SimpleNamespace(screen=screen)

    def screenshot_area(filepath=None):
        with open(filepath, "wb") as handle:
            handle.write(b"\x89PNG\r\n")

    monkeypatch.setattr(bpy.ops.screen, "screenshot_area", screenshot_area, raising=False)
    monkeypatch.setattr(bpy.context, "screen", screen, raising=False)
    monkeypatch.setattr(bpy.context.window_manager, "windows", [window], raising=False)
    monkeypatch.setattr(bpy.data, "images", types.SimpleNamespace(
        load=lambda path: types.SimpleNamespace(size=(40, 20)),
        remove=lambda image: None,
    ), raising=False)

    server = _server()
    bpy.context.scene.dshbb_ai_paused = True
    try:
        response = server.execute_command({
            "type": "get_viewport_screenshot",
            "params": {"area": 0, "filepath": str(shot), "format": "png"},
        })
    finally:
        bpy.context.scene.dshbb_ai_paused = False

    assert response["status"] == "success", response
    result = response["result"]
    assert result["success"] is True
    assert result["method"] == "screenshot_area"
    assert (result["width"], result["height"]) == (40, 20)
    assert shot.is_file()


def test_read_only_capture_delegates_to_the_shared_kernel(monkeypatch):
    spec = {"mode": "camera", "max_size": 544, "path": "C:/tmp/dsh.png"}
    seen = {}

    def capture(spec_json):
        seen.update(json.loads(spec_json))
        return {
            "ok": True,
            "path": "C:/tmp/dsh.png",
            "width": 544,
            "height": 306,
            "matrix": {"view": [], "proj": []},
            "meta": {},
            "ms": 1,
        }

    kernel = types.ModuleType("dsh_rt_kernel")
    kernel.dsh_view_api = {"capture": capture}
    monkeypatch.setitem(sys.modules, "dsh_rt_kernel", kernel)
    server = _server()
    response = server.execute_command({
        "type": "get_viewport_screenshot",
        "params": {"spec": spec},
    })
    assert response["status"] == "success", response
    assert response["result"]["ok"] is True
    assert seen == spec


def test_read_only_capture_without_a_kernel_is_an_explicit_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "dsh_rt_kernel", raising=False)
    server = _server()
    response = server.execute_command({
        "type": "get_viewport_screenshot",
        "params": {"spec": {"mode": "current"}},
    })
    assert response["status"] == "success"
    assert response["result"]["ok"] is False
    assert response["result"]["code"] == "kernel_not_loaded"


def test_invalid_capture_spec_is_rejected():
    server = _server()
    response = server.execute_command({
        "type": "get_viewport_screenshot",
        "params": {"spec": "not json"},
    })
    assert response["result"]["ok"] is False
    assert response["result"]["code"] == "invalid_spec"
