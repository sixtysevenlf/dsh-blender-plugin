"""Add-on -> Gateway event delivery across disconnects (task-9).

Pause and human-edit mirrors must not be reported while there is no socket,
and must not be marked delivered until the frame was actually written.
"""

from __future__ import annotations

import importlib
import json

import bpy_stub

bpy_stub.install()

server_module = importlib.import_module("extension.dsh_blender_bridge.server")


class _Connection:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def send_text(self, text):
        if self.fail:
            raise OSError("socket gone")
        self.messages.append(json.loads(text))


def _server(monkeypatch, paused, connected=True, fail=False):
    server = server_module.DSHBlenderBridgeServer()
    server.connected = connected
    connection = _Connection(fail=fail)
    server._connection = connection
    monkeypatch.setattr(server_module, "ai_paused", lambda: paused)
    return server, connection


def test_pause_is_rereported_after_a_new_connection(monkeypatch):
    server, connection = _server(monkeypatch, True)
    server._reported_pause = True  # last connection already saw this state
    server._report_pause_change()
    assert connection.messages == []

    # A reconnect may belong to a restarted Gateway whose session mirror is
    # empty, so the handshake forces one snapshot on the next tick.
    server._reported_pause = None
    server._report_pause_change()
    assert connection.messages == [{
        "type": "event",
        "name": "pause",
        "paused": True,
        "instanceId": server_module._INSTANCE_ID,
    }]


def test_human_edits_are_not_drained_while_disconnected(monkeypatch):
    server, connection = _server(monkeypatch, False, connected=False)
    drained = []

    def drain():
        drained.append(True)
        return [{"kind": "object", "name": "Cube", "revision": 1}]

    monkeypatch.setattr(server_module, "drain_human_edits", drain)
    server._report_human_edits()
    assert drained == []
    assert connection.messages == []

    server.connected = True
    server._report_human_edits()
    assert len(drained) == 1
    assert connection.messages[0]["name"] == "human_edit"
    assert [item["name"] for item in connection.messages[0]["objects"]] == ["Cube"]


def test_human_edits_are_restored_when_the_send_fails(monkeypatch):
    server, _connection = _server(monkeypatch, False, fail=True)
    edits = [{"kind": "object", "name": "Cube", "revision": 1}]
    restored = []
    monkeypatch.setattr(server_module, "drain_human_edits", lambda: list(edits))
    monkeypatch.setattr(server_module, "restore_human_edits", lambda items: restored.append(items))
    server._report_human_edits()
    assert restored == [edits]


def test_restored_edits_are_reported_on_the_next_tick(monkeypatch):
    server, connection = _server(monkeypatch, False, fail=True)
    edits = [{"kind": "collection", "name": "Props", "revision": 7}]
    monkeypatch.setattr(server_module, "drain_human_edits", lambda: list(edits))
    monkeypatch.setattr(server_module, "restore_human_edits", lambda items: None)
    server._report_human_edits()

    server._connection.fail = False
    server._report_human_edits()
    assert connection.messages[0]["collections"] == edits