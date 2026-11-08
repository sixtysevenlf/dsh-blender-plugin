"""Unit checks for the add-on WebSocket client (no bpy, no Gateway process).

The module under test is loaded straight from disk: that also proves
transport.py stays importable outside Blender, which the package import path
(__init__ -> protocol -> bpy) would otherwise hide.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import socket
import struct
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TRANSPORT_PATH = REPO_ROOT / "addon" / "dsh_blender_bridge" / "transport.py"


def _load_transport():
    spec = importlib.util.spec_from_file_location("dshbb_transport_under_test", TRANSPORT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


transport = _load_transport()


class _FakeGateway:
    """Minimal server side of RFC 6455, sufficient for client behaviour checks."""

    def __init__(self, status_line="HTTP/1.1 101 Switching Protocols", accept_override=None):
        self.status_line = status_line
        self.accept_override = accept_override
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.client = None
        self.buffer = b""
        self.headers = {}
        self.request_line = ""
        self.error = None

    def accept(self, timeout=3.0):
        try:
            self.listener.settimeout(timeout)
            client, _address = self.listener.accept()
            client.settimeout(timeout)
            self.client = client
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = client.recv(4096)
                if not chunk:
                    raise AssertionError("client closed during the upgrade")
                request += chunk
            head, _, rest = request.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            self.request_line = lines[0]
            for line in lines[1:]:
                name, separator, value = line.partition(":")
                if separator:
                    self.headers[name.strip().lower()] = value.strip()
            key = self.headers["sec-websocket-key"]
            accept = self.accept_override if self.accept_override is not None \
                else transport.expected_accept(key)
            response = (
                "%s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % (self.status_line, accept)
            )
            client.sendall(response.encode("latin-1"))
            self.buffer = rest
        except BaseException as exc:  # surfaced by the tests through .error
            self.error = exc

    def read_frame(self, timeout=3.0):
        self.client.settimeout(timeout)
        while True:
            try:
                frame, consumed = transport.decode_frame(self.buffer)
            except transport.IncompleteFrame:
                chunk = self.client.recv(65536)
                if not chunk:
                    raise ConnectionError("client closed the connection")
                self.buffer += chunk
                continue
            self.buffer = self.buffer[consumed:]
            return frame

    def read_json(self):
        return json.loads(self.read_frame().payload.decode("utf-8"))

    def send(self, opcode, payload=b"", fin=True):
        self.client.sendall(transport.encode_frame(opcode, payload, mask=False, fin=fin))

    def send_text(self, text):
        self.send(transport.OP_TEXT, text.encode("utf-8"))

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
def _connected():
    gateway = _FakeGateway()
    thread = threading.Thread(target=gateway.accept, daemon=True)
    thread.start()
    client = transport.WebSocketClient("127.0.0.1", gateway.port)
    client.connect(timeout=3.0)
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert gateway.error is None, gateway.error
    try:
        yield gateway, client
    finally:
        client.close()
        gateway.close()


def test_accept_computation_matches_the_rfc6455_example():
    assert transport.expected_accept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_frame_roundtrip_with_and_without_mask():
    for payload in (b"", b"hello", b"x" * 200, b"y" * 70000):
        for mask in (True, False):
            encoded = transport.encode_frame(transport.OP_TEXT, payload, mask=mask)
            frame, consumed = transport.decode_frame(encoded)
            assert consumed == len(encoded)
            assert frame.fin is True
            assert frame.opcode == transport.OP_TEXT
            assert frame.payload == payload
            assert frame.masked is mask


def test_byte_by_byte_partial_decode():
    payload = "split across reads \u4e2d\u6587".encode("utf-8")
    encoded = transport.encode_frame(transport.OP_TEXT, payload, mask=True)
    buffer = b""
    for index, byte in enumerate(encoded):
        buffer += bytes([byte])
        if index < len(encoded) - 1:
            with pytest.raises(transport.IncompleteFrame):
                transport.decode_frame(buffer)
        else:
            frame, consumed = transport.decode_frame(buffer)
            assert consumed == len(encoded)
            assert frame.payload == payload


def test_connect_sends_the_upgrade_request_and_validates_accept():
    gateway = _FakeGateway()
    thread = threading.Thread(target=gateway.accept, daemon=True)
    thread.start()
    client = transport.WebSocketClient("127.0.0.1", gateway.port)
    try:
        client.connect(timeout=3.0)
        thread.join(timeout=3.0)
        assert gateway.error is None
        assert gateway.request_line == "GET /addon HTTP/1.1"
        assert gateway.headers["upgrade"].lower() == "websocket"
        assert gateway.headers["sec-websocket-version"] == "13"
        assert len(gateway.headers["sec-websocket-key"]) >= 16
        assert client.connected is True
    finally:
        client.close()
        gateway.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"status_line": "HTTP/1.1 403 Forbidden"}, "rejected the WebSocket upgrade"),
        ({"accept_override": "not-the-right-hash"}, "invalid Sec-WebSocket-Accept"),
    ],
)
def test_bad_handshake_replies_raise_a_clean_error(kwargs, message):
    gateway = _FakeGateway(**kwargs)
    thread = threading.Thread(target=gateway.accept, daemon=True)
    thread.start()
    client = transport.WebSocketClient("127.0.0.1", gateway.port)
    try:
        with pytest.raises(transport.WebSocketError, match=message):
            client.connect(timeout=3.0)
        assert client.connected is False
    finally:
        thread.join(timeout=3.0)
        gateway.close()


def test_client_text_frames_are_masked_and_server_frames_are_not():
    with _connected() as (gateway, client):
        client.send_text('{"type":"ping"}')
        frame = gateway.read_frame()
        assert frame.masked is True
        assert frame.opcode == transport.OP_TEXT
        assert json.loads(frame.payload.decode("utf-8")) == {"type": "ping"}

        gateway.send_text('{"status":"success"}')
        assert client.recv_text(timeout=2.0) == '{"status":"success"}'


def test_fragmented_text_is_reassembled_and_ping_between_fragments_is_answered():
    with _connected() as (gateway, client):
        gateway.send(transport.OP_TEXT, b'{"type":', fin=False)
        gateway.send(transport.OP_PING, b"keepalive")
        gateway.send(transport.OP_CONTINUATION, b'"ping"}', fin=True)
        # The pong is produced while recv_text walks the incoming frames, so the
        # message must be consumed before the pong can be read off the wire.
        assert client.recv_text(timeout=2.0) == '{"type":"ping"}'
        pong = gateway.read_frame()
        assert pong.opcode == transport.OP_PONG
        assert pong.payload == b"keepalive"
        assert pong.masked is True


def test_pong_frames_are_ignored():
    with _connected() as (gateway, client):
        gateway.send(transport.OP_PONG, b"heartbeat")
        gateway.send_text("done")
        assert client.recv_text(timeout=2.0) == "done"


def test_close_frame_returns_none_and_client_replies_with_close():
    with _connected() as (gateway, client):
        gateway.send(transport.OP_CLOSE, struct.pack("!H", 1000))
        assert client.recv_text(timeout=2.0) is None
        reply = gateway.read_frame()
        assert reply.opcode == transport.OP_CLOSE
        assert client.connected is False


def test_illegal_opcode_and_masked_server_frame_raise():
    with pytest.raises(transport.WebSocketError, match="unsupported opcode"):
        transport.decode_frame(bytes([0x83, 0x00]))

    with _connected() as (gateway, client):
        gateway.client.sendall(bytes([0x83, 0x00]))
        with pytest.raises(transport.WebSocketError, match="unsupported opcode"):
            client.recv_text(timeout=2.0)

    with _connected() as (gateway, client):
        gateway.send(transport.OP_BINARY, b"\x00\x01")
        with pytest.raises(transport.WebSocketError, match="binary frame"):
            client.recv_text(timeout=2.0)

    with _connected() as (gateway, client):
        gateway.client.sendall(transport.encode_frame(transport.OP_TEXT, b"masked", mask=True))
        with pytest.raises(transport.WebSocketError, match="masked frame"):
            client.recv_text(timeout=2.0)


def test_oversized_frame_is_rejected_from_its_header_alone():
    too_big = transport.MAX_MESSAGE_SIZE + 1
    header = struct.pack("!BBQ", 0x81, 0x80 | 127, too_big) + b"\x00\x00\x00\x00"
    with pytest.raises(transport.WebSocketError, match="16MB limit"):
        transport.decode_frame(header)


def test_recv_text_timeout_raises_timeout_error():
    with _connected() as (_gateway, client):
        with pytest.raises(TimeoutError):
            client.recv_text(timeout=0.05)


def test_close_is_idempotent_and_blocks_further_sends():
    with _connected() as (gateway, client):
        client.close()
        client.close()
        assert client.connected is False
        with pytest.raises(transport.WebSocketError, match="closed"):
            client.send_text("after close")
        reply = gateway.read_frame()
        assert reply.opcode == transport.OP_CLOSE
