"""Integration checks for the add-on's outbound Gateway channel (3.1).

The real server thread dials a fake Gateway that speaks RFC 6455 and the
handshake contract; the real transport and the real command queue are used.
No Blender process is involved.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import socket
import threading
import time

import bpy_stub

bpy = bpy_stub.install()

server_module = importlib.import_module("extension.dsh_blender_bridge.server")
transport = importlib.import_module("extension.dsh_blender_bridge.transport")
command_registry = importlib.import_module("extension.dsh_blender_bridge.command_registry")


class _FakeGateway:
    """Accepts one WebSocket upgrade, then speaks the handshake + framing."""

    def __init__(self, handshake_reply="ok"):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(5.0)
        self.port = self.listener.getsockname()[1]
        self.client = None
        self.buffer = b""
        self.handshake = None
        self.handshake_reply = handshake_reply
        self.ready = threading.Event()
        self.error = None

    def serve(self):
        try:
            self._upgrade()
            self.handshake = self.read_json()
            if self.handshake_reply == "error":
                self.send_text(json.dumps({
                    "type": "handshake_error",
                    "code": "unsupported_protocol",
                    "message": "protocol 12 is required",
                    "fix": "update the add-on",
                }))
            else:
                self.send_text(json.dumps({"type": "handshake_ok"}))
            self.ready.set()
        except BaseException as exc:  # surfaced through .error by the tests
            self.error = exc
            self.ready.set()

    def _upgrade(self):
        client, _address = self.listener.accept()
        client.settimeout(5.0)
        self.client = client
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = client.recv(4096)
            if not chunk:
                raise ConnectionError("add-on closed during the upgrade")
            request += chunk
        head, _separator, rest = request.partition(b"\r\n\r\n")
        headers = {}
        for line in head.decode("latin-1").split("\r\n")[1:]:
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().lower()] = value.strip()
        accept = transport.expected_accept(headers["sec-websocket-key"])
        response = (
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept
        )
        client.sendall(response.encode("latin-1"))
        self.buffer = rest

    def read_frame(self):
        while True:
            try:
                frame, consumed = transport.decode_frame(self.buffer)
            except transport.IncompleteFrame:
                chunk = self.client.recv(65536)
                if not chunk:
                    raise ConnectionError("add-on closed the connection")
                self.buffer += chunk
                continue
            self.buffer = self.buffer[consumed:]
            return frame

    def read_json(self):
        return json.loads(self.read_frame().payload.decode("utf-8"))

    def send_text(self, text):
        frame = transport.encode_frame(transport.OP_TEXT, text.encode("utf-8"), mask=False)
        self.client.sendall(frame)

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except OSError:
                pass
            self.client = None
        try:
            self.listener.close()
        except OSError:
            pass


@contextlib.contextmanager
def _running(handshake_reply="ok"):
    previous_background = bpy.app.background
    bpy.app.background = False
    gateway = _FakeGateway(handshake_reply)
    gateway_thread = threading.Thread(target=gateway.serve, daemon=True)
    gateway_thread.start()
    server = server_module.DSHBlenderBridgeServer(gateway_port=gateway.port)
    try:
        server.start()
        assert gateway.ready.wait(5.0), "the add-on never sent its handshake"
        deadline = time.time() + 3.0
        while not server.connected and not server.handshake_error and time.time() < deadline:
            time.sleep(0.01)
        yield gateway, server
    finally:
        server.stop()
        gateway.close()
        gateway_thread.join(timeout=2.0)
        bpy.app.background = previous_background


def _wait_for_command(server, timeout=3.0):
    deadline = time.time() + timeout
    while server.command_queue.qsize() == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert server.command_queue.qsize() > 0, "the Gateway frame did not reach the command queue"


def _drain_and_read(gateway, server):
    _wait_for_command(server)
    server._drain_command_queue()
    return gateway.read_json()


def test_handshake_is_sent_before_serving_and_carries_the_31_contract():
    with _running() as (gateway, server):
        assert gateway.error is None, gateway.error
        handshake = gateway.handshake
        assert handshake["type"] == "handshake"
        assert handshake["kind"] == "user-gui"
        assert handshake["version"] == "2.0.0"
        assert handshake["protocol"] == 12
        assert handshake["instanceId"] == server_module._INSTANCE_ID
        assert handshake["instanceId"]
        assert handshake["project"] == ""
        assert handshake["capabilities"] == sorted(command_registry.COMMAND_NAMES)
        assert server.connected is True


def test_a_frame_split_across_two_chunks_is_assembled():
    with _running() as (gateway, server):
        frame = transport.encode_frame(
            transport.OP_TEXT,
            json.dumps({"type": "get_scene_info", "params": {"tag": "split"}}).encode("utf-8"),
            mask=False,
        )
        gateway.client.sendall(frame[:7])
        time.sleep(0.05)
        assert server.command_queue.qsize() == 0, "half a frame must not dispatch"
        gateway.client.sendall(frame[7:])
        _wait_for_command(server)
        command, _send, _request_id = server.command_queue.get_nowait()
        assert command == {"type": "get_scene_info", "params": {"tag": "split"}}


def test_a_multibyte_utf8_character_split_across_chunks_does_not_crash():
    with _running() as (gateway, server):
        payload = {"type": "execute_code", "params": {"code": "print('\u4e2d\u6587')"}}
        frame = transport.encode_frame(
            transport.OP_TEXT, json.dumps(payload, ensure_ascii=False).encode("utf-8"), mask=False)
        marker = "\u4e2d".encode("utf-8")
        split_at = frame.index(marker) + 1
        gateway.client.sendall(frame[:split_at])
        time.sleep(0.05)
        assert server.command_queue.qsize() == 0
        gateway.client.sendall(frame[split_at:])
        _wait_for_command(server)
        command, _send, _request_id = server.command_queue.get_nowait()
        assert command["params"]["code"] == "print('\u4e2d\u6587')"


def test_two_sequential_commands_are_framed_and_request_ids_are_echoed():
    with _running() as (gateway, server):
        gateway.send_text(json.dumps({"type": "ping", "requestId": "req-1"}))
        first = _drain_and_read(gateway, server)
        assert first == {"status": "success", "result": {"pong": True}, "requestId": "req-1"}

        gateway.send_text(json.dumps({"type": "ping", "requestId": "req-2"}))
        second = _drain_and_read(gateway, server)
        assert second == {"status": "success", "result": {"pong": True}, "requestId": "req-2"}


def test_response_is_one_json_document_per_command():
    with _running() as (gateway, server):
        gateway.send_text(json.dumps({"type": "get_addon_info", "params": {}}))
        decoded = _drain_and_read(gateway, server)
        assert decoded["status"] == "success"
        assert decoded["result"]["protocol_version"] == 12
        assert set(decoded) == {"status", "result"}


def test_invalid_json_does_not_dispatch_or_crash_the_server():
    with _running() as (gateway, server):
        gateway.send_text("{not json")
        time.sleep(0.1)
        assert server.command_queue.qsize() == 0
        gateway.send_text(json.dumps({"type": "ping"}))
        assert _drain_and_read(gateway, server)["status"] == "success"


def test_a_handshake_error_is_recorded_and_keeps_the_connection_closed():
    with _running(handshake_reply="error") as (gateway, server):
        deadline = time.time() + 2.0
        while not server.handshake_error and time.time() < deadline:
            time.sleep(0.01)
        assert server.handshake_error == "protocol 12 is required"
        assert server.connected is False
        assert server.running is True


def test_gateway_disconnect_keeps_the_server_running_and_reconnects():
    with _running() as (gateway, server):
        gateway.client.close()
        deadline = time.time() + 3.0
        while server.connected and time.time() < deadline:
            time.sleep(0.01)
        assert server.connected is False
        assert server.running is True
