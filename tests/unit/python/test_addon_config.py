"""Unit checks for add-on configuration, env fallbacks and auto-start control."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

config = importlib.import_module("addon.dsh_blender_bridge.config")
server_module = importlib.import_module("addon.dsh_blender_bridge.server")


class _FakeProbeSocket:
    def __init__(self, connect_result):
        self.connect_result = connect_result
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def connect_ex(self, address):
        self.address = address
        return self.connect_result

    def close(self):
        self.closed = True


def test_read_env_var_prefers_new_name_then_legacy(monkeypatch):
    monkeypatch.setenv("DSHBB_HYPER3D_API_KEY", "new-value")
    monkeypatch.setenv("BLENDERMCP_HYPER3D_API_KEY", "old-value")
    assert config.read_env_var("DSHBB_HYPER3D_API_KEY") == "new-value"

    monkeypatch.delenv("DSHBB_HYPER3D_API_KEY")
    assert config.read_env_var("DSHBB_HYPER3D_API_KEY") == "old-value"

    monkeypatch.setenv("DSHBB_HYPER3D_API_KEY", "")
    assert config.read_env_var("DSHBB_HYPER3D_API_KEY") == "old-value"

    monkeypatch.delenv("BLENDERMCP_HYPER3D_API_KEY")
    assert config.read_env_var("DSHBB_HYPER3D_API_KEY") is None
    assert config.read_env_var("DSHBB_NOT_MAPPED") is None
    assert set(config._LEGACY_ENV_VARS) == {
        "DSHBB_HYPER3D_API_KEY", "DSHBB_SKETCHFAB_API_KEY", "DSHBB_POLYPIZZA_API_KEY",
        "DSHBB_HUNYUAN3D_SECRET_ID", "DSHBB_HUNYUAN3D_SECRET_KEY", "DSHBB_HUNYUAN3D_API_URL",
        "DSHBB_PREMIUM_LICENSE_KEY", "DSHBB_PREMIUM_INSTANCE_ID", "DSHBB_PREMIUM_API_URL",
    }


def test_backend_url_defaults_and_strips_trailing_slash():
    previous = bpy.context.scene.dshbb_backend_url
    try:
        bpy.context.scene.dshbb_backend_url = ""
        assert config.DEFAULT_DSH_BACKEND == "http://127.0.0.1:9877"
        assert config._dshbb_backend_url() == "http://127.0.0.1:9877"
        bpy.context.scene.dshbb_backend_url = "http://127.0.0.1:9999/"
        assert config._dshbb_backend_url() == "http://127.0.0.1:9999"
        bpy.context.scene.dshbb_backend_url = "http://127.0.0.1:9999///"
        assert config._dshbb_backend_url() == "http://127.0.0.1:9999"
    finally:
        bpy.context.scene.dshbb_backend_url = previous


def test_addon_preferences_are_read_from_the_module_entry():
    preferences = SimpleNamespace(telemetry_consent=True)
    context = SimpleNamespace(
        preferences=SimpleNamespace(
            addons={config.ADDON_MODULE: SimpleNamespace(preferences=preferences)},
        ),
    )
    assert config.get_dshbb_addon_preferences(context) is preferences
    empty = SimpleNamespace(preferences=SimpleNamespace(addons={}))
    assert config.get_dshbb_addon_preferences(empty) is None
    assert config.get_dshbb_addon_preferences() is None


def test_port_listener_probe_closes_the_socket(monkeypatch):
    created = []

    def make_socket(family, kind):
        probe = _FakeProbeSocket(0)
        created.append(probe)
        return probe

    monkeypatch.setattr(config.socket, "socket", make_socket)
    assert config._dshbb_port_has_listener("localhost", 9876) is True
    assert created[0].timeout == 0.15
    assert created[0].address == ("localhost", 9876)
    assert created[0].closed is True

    created.clear()

    def make_busy(family, kind):
        probe = _FakeProbeSocket(111)
        created.append(probe)
        return probe

    monkeypatch.setattr(config.socket, "socket", make_busy)
    assert config._dshbb_port_has_listener("localhost", 9876) is False
    assert created[0].closed is True


def test_auto_start_registers_one_persistent_timer_and_handler(monkeypatch):
    bpy.app.handlers.load_post[:] = []
    bpy.app.timers._registered.clear()
    try:
        config._dshbb_register_auto_start()
        assert config._dshbb_load_post in bpy.app.handlers.load_post
        assert bpy.app.timers.is_registered(config._dshbb_ensure_server_running)
        assert len(bpy.app.timers._registered) == 1
        entry = bpy.app.timers._registered[config._dshbb_ensure_server_running]
        assert entry == {"first_interval": 0.5, "persistent": True}
        assert config._user_stopped_server is False

        config._dshbb_schedule_auto_start()
        assert len(bpy.app.timers._registered) == 1

        config._dshbb_unregister_auto_start()
        assert bpy.app.handlers.load_post == []
        assert not bpy.app.timers.is_registered(config._dshbb_ensure_server_running)
    finally:
        bpy.app.handlers.load_post[:] = []
        bpy.app.timers._registered.clear()


def test_ensure_server_running_does_nothing_in_background(monkeypatch):
    monkeypatch.setattr(bpy.app, "background", True)
    assert config._dshbb_ensure_server_running() is None


def test_ensure_server_running_retries_without_a_scene(monkeypatch):
    monkeypatch.setattr(bpy.app, "background", False)
    previous = bpy.context.scene
    try:
        bpy.context.scene = None
        assert config._dshbb_ensure_server_running() == 0.5
    finally:
        bpy.context.scene = previous


def test_ensure_server_running_respects_the_auto_start_switch(monkeypatch):
    monkeypatch.setattr(bpy.app, "background", False)
    previous_auto = bpy.context.scene.dshbb_auto_start_server
    previous_stopped = config._user_stopped_server
    bpy.types.dshbb_server = SimpleNamespace(running=True)
    try:
        bpy.context.scene.dshbb_auto_start_server = False
        config._user_stopped_server = False
        assert config._dshbb_ensure_server_running() is None
        assert bpy.context.scene.dshbb_server_running is True

        bpy.context.scene.dshbb_server_running = False
        config._user_stopped_server = True
        bpy.context.scene.dshbb_auto_start_server = True
        assert config._dshbb_ensure_server_running() is None
        assert bpy.context.scene.dshbb_server_running is True
    finally:
        bpy.context.scene.dshbb_auto_start_server = previous_auto
        config._user_stopped_server = previous_stopped
        if hasattr(bpy.types, "dshbb_server"):
            del bpy.types.dshbb_server


def test_ensure_server_running_starts_a_server_on_the_scene_port(monkeypatch):
    monkeypatch.setattr(bpy.app, "background", False)
    monkeypatch.setattr(config, "_dshbb_port_has_listener", lambda host, port: False)
    previous_auto = bpy.context.scene.dshbb_auto_start_server
    previous_port = bpy.context.scene.dshbb_port
    previous_stopped = config._user_stopped_server
    started = {}

    class FakeServer:
        def __init__(self, port=9876):
            self.port = port
            self.running = False
            started["created"] = port

        def start(self):
            self.running = True
            started["started"] = self.port

    monkeypatch.setattr(server_module, "DSHBlenderBridgeServer", FakeServer)
    try:
        if hasattr(bpy.types, "dshbb_server"):
            del bpy.types.dshbb_server
        bpy.context.scene.dshbb_auto_start_server = True
        bpy.context.scene.dshbb_port = 19999
        config._user_stopped_server = False
        assert config._dshbb_ensure_server_running() is None
        assert started == {"created": 19999, "started": 19999}
        assert isinstance(bpy.types.dshbb_server, FakeServer)
        assert bpy.context.scene.dshbb_server_running is True
    finally:
        bpy.context.scene.dshbb_auto_start_server = previous_auto
        bpy.context.scene.dshbb_port = previous_port
        config._user_stopped_server = previous_stopped
        if hasattr(bpy.types, "dshbb_server"):
            del bpy.types.dshbb_server


def test_ensure_server_running_skips_when_port_is_already_taken(monkeypatch):
    monkeypatch.setattr(bpy.app, "background", False)
    monkeypatch.setattr(config, "_dshbb_port_has_listener", lambda host, port: True)
    previous_auto = bpy.context.scene.dshbb_auto_start_server
    previous_stopped = config._user_stopped_server
    try:
        if hasattr(bpy.types, "dshbb_server"):
            del bpy.types.dshbb_server
        bpy.context.scene.dshbb_auto_start_server = True
        config._user_stopped_server = False
        assert config._dshbb_ensure_server_running() is None
        assert bpy.context.scene.dshbb_server_running is False
        assert not hasattr(bpy.types, "dshbb_server")
    finally:
        bpy.context.scene.dshbb_auto_start_server = previous_auto
        config._user_stopped_server = previous_stopped
        if hasattr(bpy.types, "dshbb_server"):
            del bpy.types.dshbb_server


def test_stop_server_operator_sets_the_shared_auto_start_flag():
    ui_operators = importlib.import_module("addon.dsh_blender_bridge.ui.operators")
    previous = config._user_stopped_server
    previous_flag = bpy.context.scene.dshbb_server_running
    try:
        config._user_stopped_server = False
        bpy.context.scene.dshbb_server_running = True
        ui_operators.DSHBB_OT_StopServer().execute(bpy.context)
        assert config._user_stopped_server is True
        assert bpy.context.scene.dshbb_server_running is False
    finally:
        config._user_stopped_server = previous
        bpy.context.scene.dshbb_server_running = previous_flag
