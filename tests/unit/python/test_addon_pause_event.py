"""Unit checks for the add-on -> Gateway pause event.

Pause is owned by the human in the Blender N-panel. The add-on mirrors the
toggle to the Gateway once per change so the session write gate can answer
before an agent command reaches Blender.
"""

from __future__ import annotations

import importlib
import json

import bpy_stub

bpy = bpy_stub.install()

server_module = importlib.import_module("extension.dsh_blender_bridge.server")


class _Connection:
    def __init__(self):
        self.messages = []

    def send_text(self, text):
        self.messages.append(json.loads(text))


def _server(monkeypatch, paused):
    server = server_module.DSHBlenderBridgeServer()
    server.connected = True
    connection = _Connection()
    server._connection = connection
    monkeypatch.setattr(server_module, "ai_paused", lambda: paused)
    return server, connection


def test_reports_only_pause_changes(monkeypatch):
    server, connection = _server(monkeypatch, False)
    server._report_pause_change()
    assert connection.messages == []

    server._reported_pause = None
    monkeypatch.setattr(server_module, "ai_paused", lambda: True)
    server._report_pause_change()
    server._report_pause_change()
    assert connection.messages == [
        {
            "type": "event",
            "name": "pause",
            "paused": True,
            "instanceId": server_module._INSTANCE_ID,
        }
    ]

    monkeypatch.setattr(server_module, "ai_paused", lambda: False)
    server._report_pause_change()
    assert connection.messages[-1]["paused"] is False
    assert len(connection.messages) == 2


def test_skips_reporting_while_disconnected(monkeypatch):
    server, connection = _server(monkeypatch, True)
    server.connected = False
    server._report_pause_change()
    assert connection.messages == []
    assert server._reported_pause is True
