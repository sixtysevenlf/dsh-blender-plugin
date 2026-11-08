"""Unit checks for the add-on instance identity (3.1 virtual-gui handshake).

A virtual-gui launch sets DSHBB_INSTANCE_ID, so the Gateway can wait for the
exact handshake it spawned. Every other launch keeps the pid-derived identity.
"""

from __future__ import annotations

import importlib

import bpy_stub

bpy_stub.install()

server_module = importlib.import_module("extension.dsh_blender_bridge.server")


def test_instance_id_uses_the_environment_override(monkeypatch):
    monkeypatch.setenv("DSHBB_INSTANCE_ID", "vgui-abc123")
    assert server_module._instance_id() == "vgui-abc123"


def test_instance_id_ignores_an_invalid_override(monkeypatch):
    monkeypatch.setenv("DSHBB_INSTANCE_ID", "bad id!")
    value = server_module._instance_id()
    assert value != "bad id!"
    assert len(value) == 32
    assert all(ch in "0123456789abcdef" for ch in value)


def test_instance_id_is_stable_without_an_override(monkeypatch):
    monkeypatch.delenv("DSHBB_INSTANCE_ID", raising=False)
    assert server_module._instance_id() == server_module._instance_id()

def test_session_kind_defaults_to_user_gui(monkeypatch):
    monkeypatch.delenv("DSHBB_SESSION_KIND", raising=False)
    assert server_module._session_kind() == "user-gui"


def test_session_kind_uses_a_virtual_gui_launch(monkeypatch):
    monkeypatch.setenv("DSHBB_SESSION_KIND", "virtual-gui")
    assert server_module._session_kind() == "virtual-gui"


def test_session_kind_ignores_an_unknown_value(monkeypatch):
    monkeypatch.setenv("DSHBB_SESSION_KIND", "headless")
    assert server_module._session_kind() == "user-gui"

