"""Unit tests for the split DSH Blender Bridge add-on package.

These tests import the add-on with a small bpy stub; they prove the package is
self-consistent without claiming a real Blender session was exercised.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

pkg = importlib.import_module("addon.dsh_blender_bridge")
protocol = importlib.import_module("addon.dsh_blender_bridge.protocol")
config = importlib.import_module("addon.dsh_blender_bridge.config")
compat = importlib.import_module("addon.dsh_blender_bridge.compat")
selection = importlib.import_module("addon.dsh_blender_bridge.commands.selection")
activity = importlib.import_module("addon.dsh_blender_bridge.recorders.activity")
premium_client = importlib.import_module("addon.dsh_blender_bridge.premium.client")
assets_io = importlib.import_module("addon.dsh_blender_bridge.assets.io")

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


def test_bl_info_and_protocol_are_frozen():
    assert pkg.bl_info["name"] == "DSH Blender Bridge"
    assert pkg.bl_info["version"] == (1, 11)
    assert pkg.bl_info["blender"] == (3, 0, 0)
    assert pkg.ADDON_VERSION == (1, 11)
    assert pkg.ADDON_PROTOCOL_VERSION == 11
    assert protocol.ADDON_NAME == "DSH Blender Bridge"


def test_package_layout_and_module_size_budget():
    root = Path(pkg.__file__).resolve().parent
    assert not (root.parent / "dsh_blender_bridge.py").exists(), "single-file install entry must be gone"
    expected = [
        "__init__.py", "config.py", "protocol.py", "server.py", "compat.py",
        "commands/__init__.py", "commands/core.py", "commands/scene.py", "commands/selection.py",
        "commands/activity.py", "commands/material.py", "commands/telemetry.py",
        "commands/assets_polyhaven.py", "commands/assets_sketchfab.py", "commands/assets_polypizza.py",
        "commands/generate_hyper3d.py", "commands/generate_hunyuan.py", "commands/generate_premium.py",
        "recorders/__init__.py", "recorders/user_edit.py", "recorders/activity.py", "recorders/edit_capture.py",
        "assets/__init__.py", "assets/io.py",
        "integrations/__init__.py", "integrations/polyhaven_client.py",
        "integrations/sketchfab_client.py", "integrations/polypizza_client.py",
        "integrations/hyper3d_client.py", "integrations/hunyuan_client.py",
        "premium/__init__.py", "premium/client.py", "premium/jobs.py", "premium/ui.py",
        "ui/__init__.py", "ui/preferences.py", "ui/panels.py", "ui/operators.py", "ui/lists.py",
    ]
    for relative in expected:
        assert (root / relative).is_file(), relative
    for path in root.rglob("*.py"):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 1000, f"{path.relative_to(root)} has {lines} lines"


def test_command_registry_has_exactly_39_contract_names():
    assert len(protocol.COMMAND_NAMES) == 39
    assert len(set(protocol.COMMAND_NAMES)) == 39
    assert list(protocol.COMMAND_NAMES) == BASE_COMMANDS + GATED_COMMANDS
    assert "ping" not in protocol.COMMAND_NAMES
    assert protocol.PAUSE_SAFE_COMMANDS == {
        "ping", "drain_human_activity", "describe_node_type", "bpy_api_lookup",
        "get_activity_state", "get_selection", "get_collections",
    }
    for group, names in protocol.COMMAND_GROUPS:
        assert group in ("base", *protocol.GATED_COMMAND_GROUPS)
        assert names
    assert sum(len(names) for _group, names in protocol.COMMAND_GROUPS) == 39


def test_pause_policy_keeps_reads_and_ping_safe():
    assert protocol._is_pause_safe_command("ping")
    assert protocol._is_pause_safe_command("get_scene_info")
    assert protocol._is_pause_safe_command("get_polyhaven_status")
    assert protocol._is_pause_safe_command("describe_node_type")
    assert not protocol._is_pause_safe_command("execute_code")
    assert not protocol._is_pause_safe_command("set_texture")
    assert not protocol._is_pause_safe_command(None)
    # Mirror the old top-level __name__: a plain install is dsh_blender_bridge,
    # while Blender 5.x extension imports use bl_ext.<repo>.dsh_blender_bridge.
    assert compat.ADDON_MODULE == compat.__package__
    assert compat.ADDON_MODULE.endswith("dsh_blender_bridge")


def test_stop_server_sets_the_shared_auto_start_flag():
    ui_operators = importlib.import_module("addon.dsh_blender_bridge.ui.operators")
    previous = config._user_stopped_server
    try:
        config._user_stopped_server = False
        ui_operators.DSHBB_OT_StopServer().execute(bpy.context)
        assert config._user_stopped_server is True
    finally:
        config._user_stopped_server = previous


def test_config_reads_new_env_then_legacy_and_backend_url(monkeypatch):
    monkeypatch.setenv("DSHBB_HYPER3D_API_KEY", "new-value")
    monkeypatch.setenv("BLENDERMCP_HYPER3D_API_KEY", "old-value")
    assert config.read_env_var("DSHBB_HYPER3D_API_KEY") == "new-value"
    monkeypatch.delenv("DSHBB_HYPER3D_API_KEY")
    assert config.read_env_var("DSHBB_HYPER3D_API_KEY") == "old-value"
    monkeypatch.delenv("BLENDERMCP_HYPER3D_API_KEY")
    assert config.read_env_var("DSHBB_HYPER3D_API_KEY") is None
    assert config.read_env_var("DSHBB_NOT_MAPPED") is None

    bpy.context.scene.dshbb_backend_url = ""
    assert config._dshbb_backend_url() == config.DEFAULT_DSH_BACKEND
    bpy.context.scene.dshbb_backend_url = "http://127.0.0.1:9999/"
    assert config._dshbb_backend_url() == "http://127.0.0.1:9999"


def _mesh(name, collections=("SetA",)):
    return SimpleNamespace(
        name=name,
        type="MESH",
        dimensions=SimpleNamespace(x=1.0, y=2.0, z=3.0),
        users_collection=[SimpleNamespace(name=value) for value in collections],
        location=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        rotation_euler=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        scale=SimpleNamespace(x=1.0, y=1.0, z=1.0),
    )


def test_selection_payload_shape_caps_and_collection_payload():
    objects = [_mesh(f"Obj{i:03d}", ("SetA", "SetB") if i % 2 else ("SetA",)) for i in range(60)]
    bpy.context.selected_objects = objects
    bpy.context.active_object = objects[0]
    bpy.context.mode = "OBJECT"

    payload = selection.build_selection_payload(include_current_command=False)
    assert payload["v"] == 1
    assert payload["source"] == "blender-addon"
    assert isinstance(payload["sent_at"], int)
    body = payload["selection"]
    assert body["kind"] == "objects"
    assert body["count"] == 60
    assert len(body["names"]) == 60
    assert body["truncated"] is False
    assert body["active"] == "Obj000"
    assert body["mode"] == "OBJECT"
    assert len(body["objects"]) == compat.SELECTION_PAYLOAD_OBJECTS == 50
    assert body["objects"][0] == {
        "name": "Obj000", "type": "MESH", "dimensions_mm": [1000.0, 2000.0, 3000.0],
    }
    assert body["collections"][0] == {"name": "SetA", "count": 60}
    assert "sub" not in body
    assert payload["scene"] == {"name": "Scene", "file": None}
    assert payload["activity"]["source"] == "addon"

    bpy.data.collections = {"SetA": SimpleNamespace(objects=objects[:3])}
    collection_payload = selection.build_collection_payload("SetA", include_current_command=False)
    assert collection_payload["selection"]["kind"] == "collection"
    assert collection_payload["selection"]["collection"] == "SetA"
    assert collection_payload["selection"]["count"] == 3
    assert collection_payload["selection"]["truncated"] is False
    assert selection.build_collection_payload("Missing", include_current_command=False) is None
    assert selection.build_collection_payload("", include_current_command=False) is None


def test_selection_cap_and_sub_selection_object_mode():
    values, truncated = compat._cap_selection(range(5), cap=3)
    assert values == [0, 1, 2] and truncated is True
    values, truncated = compat._cap_selection([1, 2], cap=3)
    assert values == [1, 2] and truncated is False
    bpy.context.mode = "OBJECT"
    assert compat._read_sub_selection() is None


def test_activity_state_machine_and_snapshot_windows():
    bpy.context.selected_objects = []
    bpy.context.active_object = None
    bpy.context.mode = "OBJECT"
    tracker = activity.ActivityTracker()
    now = 1000.0

    idle = tracker.snapshot(now=now, include_current_command=False)
    assert idle["state"] == "idle"
    assert idle["human_active"] is False
    assert idle["agent_active"] is False
    assert idle["human_idle_ms"] is None
    assert idle["agent_idle_ms"] is None
    assert idle["windows_ms"] == {"human": 1500, "agent": 1500}

    tracker.note_human("test-human", now=now)
    human = tracker.snapshot(now=now + 0.1, include_current_command=False)
    assert human["state"] == "human"
    assert human["human_active"] is True
    assert human["agent_active"] is False
    assert human["last_human_kind"] == "test-human"
    assert human["human_idle_ms"] == 100

    tracker.note_agent_end(now=now + 0.2)
    both = tracker.snapshot(now=now + 0.3, include_current_command=False)
    assert both["state"] == "both"
    assert both["human_active"] is True and both["agent_active"] is True

    later = tracker.snapshot(now=now + 10.0, include_current_command=False)
    assert later["state"] == "idle"
    assert later["human_active"] is False and later["agent_active"] is False


def test_premium_helpers_and_protocol_headers(monkeypatch):
    assert premium_client.premium_error("INVALID_KEY")["code"] == "INVALID_KEY"
    assert premium_client.premium_error("INVALID_KEY")["message"] == premium_client.PREMIUM_ERROR_MESSAGES["INVALID_KEY"]
    assert premium_client._premium_format_date("2026-01-02T03:04:05Z") == "Jan 2"
    assert premium_client._premium_format_date("not-a-date") is None
    assert premium_client._premium_remaining({"standard": {"remaining": 3}}, "standard") == 3
    assert premium_client._premium_remaining({"standard": {"total": 5, "used": 2}}, "standard") == 3
    assert premium_client._premium_remaining({}, "standard") == 0

    monkeypatch.setattr(premium_client, "_premium_status", {
        "tier": "pro",
        "standard": {"total": 10, "used": 1},
        "premium": {"total": 5, "used": 2},
        "resets_at": "2026-01-02T00:00:00Z",
    })
    assert premium_client.premium_summary() == (
        "Premium (Pro): 9 standard and 3 high-quality generations left, resets Jan 2"
    )
    assert premium_client._premium_quality("rodin", None, False) == "high"
    assert premium_client._premium_quality("tripo", None, True) == "high"
    assert premium_client._premium_quality("tripo", None, False) == "standard"
    assert premium_client._premium_quality("hyper3d", "standard", False) == "standard"

    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {"tier": "pro"}

    def fake_request(method, url, headers, json, timeout):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(premium_client.requests, "request", fake_request)
    monkeypatch.setenv("DSHBB_PREMIUM_LICENSE_KEY", "license-1")
    monkeypatch.setenv("DSHBB_PREMIUM_INSTANCE_ID", "instance-1")
    monkeypatch.setenv("DSHBB_PREMIUM_API_URL", "https://premium.example/api/")
    result = premium_client.premium_request("POST", "/status", payload={"x": 1})
    assert result == {"tier": "pro"}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://premium.example/api/status"
    assert captured["headers"]["X-Addon-Version"] == "1.11"
    assert captured["headers"]["X-Addon-Protocol"] == "11"
    assert captured["headers"]["Authorization"] == "Bearer license-1"
    assert captured["headers"]["X-Instance-Id"] == "instance-1"

    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY")
    monkeypatch.setattr(premium_client.requests, "request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request")))
    missing = premium_client.premium_request("GET", "/status")
    assert missing["code"] == "INVALID_KEY"


def test_assets_io_safe_names_and_texture_matching(tmp_path):
    assert assets_io._safe_file_name("../../evil.zip", "fallback") == "evil.zip"
    assert assets_io._safe_file_name("a/../b.glb", "fallback") == "b.glb"
    assert assets_io._safe_file_name("hello world.png", "fallback") == "hello_world.png"
    assert assets_io._safe_file_name("", "fallback") == "fallback"
    assert assets_io._safe_file_name("..", "fallback") == "fallback"
    assert assets_io.MODEL_DOWNLOAD_TIMEOUT == 120

    (tmp_path / "model.mtl").write_text("newmtl mat\nmap_Kd missing.png\n", encoding="utf-8")
    (tmp_path / "spare.jpg").write_bytes(b"image-bytes")
    assets_io._match_texture_names(str(tmp_path))
    assert (tmp_path / "missing.png").read_bytes() == b"image-bytes"

    (tmp_path / "model.mtl").write_text("newmtl mat\nmap_Kd missing2.png\n", encoding="utf-8")
    (tmp_path / "spare2.jpg").write_bytes(b"other")
    assets_io._match_texture_names(str(tmp_path))
    assert not (tmp_path / "missing2.png").exists()
