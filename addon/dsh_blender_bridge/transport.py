"""Minimal RFC 6455 WebSocket client for the add-on -> DSH Gateway channel.

3.1 reversed the control channel: the DSH Gateway listens and the add-on dials
ws://127.0.0.1:<gatewayPort>/addon, so this module implements only the client
half of RFC 6455. Standard library only - this file must stay importable
without bpy so the transport unit tests run outside Blender.

Wire contract owned by the Gateway (protocol 12):
  * one JSON document per text message,
  * client -> server frames are masked, server -> client frames are not,
  * ping/pong and close are handled here, never by the command dispatcher.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
from typing import NamedTuple

try:  # package import: generated single source (config/gateway.json)
    from .gateway_schema import ADDON_WS_PATH as DEFAULT_WS_PATH
    from .gateway_schema import MAX_MESSAGE_BYTES as MAX_MESSAGE_SIZE
except ImportError:  # loaded standalone by the no-bpy transport test
    MAX_MESSAGE_SIZE = 16 * 1024 * 1024
    DEFAULT_WS_PATH = "/addon"

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WEBSOCKET_VERSION = 13
MAX_HANDSHAKE_BYTES = 64 * 1024
MAX_FRAME_HEADER_BYTES = 14

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

_KNOWN_OPCODES = frozenset({OP_CONTINUATION, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG})
_CONTROL_OPCODES = frozenset({OP_CLOSE, OP_PING, OP_PONG})
_CLOSE_NORMAL = 1000


class WebSocketError(Exception):
    """Protocol or transport failure; the caller decides whether to retry."""


class IncompleteFrame(WebSocketError):
    """The buffered bytes do not hold a whole frame yet (not a failure)."""


class Frame(NamedTuple):
    fin: bool
    opcode: int
    payload: bytes
    masked: bool


def expected_accept(key: str) -> str:
    """Sec-WebSocket-Accept value the Gateway must echo for this client key."""
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode: int, payload: bytes = b"", *, mask: bool = True, fin: bool = True) -> bytes:
    """Encode one RFC 6455 frame; client frames are masked by default."""
    payload = bytes(payload)
    if opcode not in _KNOWN_OPCODES:
        raise WebSocketError("cannot encode frame with unsupported opcode 0x%X" % opcode)
    if opcode in _CONTROL_OPCODES:
        if not fin:
            raise WebSocketError("control frames cannot be fragmented")
        if len(payload) > 125:
            raise WebSocketError("control frame payload must stay under 126 bytes")
    if len(payload) > MAX_MESSAGE_SIZE:
        raise WebSocketError("frame payload exceeds the 16MB limit")

    first = (0x80 if fin else 0x00) | opcode
    mask_bit = 0x80 if mask else 0x00
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, mask_bit | length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first, mask_bit | 126, length)
    else:
        header = struct.pack("!BBQ", first, mask_bit | 127, length)

    if not mask:
        return header + payload
    mask_key = os.urandom(4)
    masked = bytes(byte ^ mask_key[index & 3] for index, byte in enumerate(payload))
    return header + mask_key + masked


def decode_frame(data: bytes) -> "tuple[Frame, int]":
    """Decode one frame at the start of *data*.

    Returns (frame, consumed_bytes). Raises IncompleteFrame while the buffer is
    shorter than the frame, and WebSocketError on a protocol violation.
    """
    data = bytes(data)
    if len(data) < 2:
        raise IncompleteFrame("need at least 2 header bytes")

    first, second = data[0], data[1]
    fin = bool(first & 0x80)
    if first & 0x70:
        raise WebSocketError("reserved bits must be zero")
    opcode = first & 0x0F
    if opcode not in _KNOWN_OPCODES:
        raise WebSocketError("unsupported opcode 0x%X" % opcode)
    masked = bool(second & 0x80)
    length = second & 0x7F
    offset = 2

    if length == 126:
        if len(data) < 4:
            raise IncompleteFrame("need the 16-bit extended length")
        length = struct.unpack("!H", data[2:4])[0]
        offset = 4
    elif length == 127:
        if len(data) < 10:
            raise IncompleteFrame("need the 64-bit extended length")
        length = struct.unpack("!Q", data[2:10])[0]
        offset = 10
        if length & 0x8000000000000000:
            raise WebSocketError("64-bit payload length must not use the high bit")

    if opcode in _CONTROL_OPCODES:
        if not fin:
            raise WebSocketError("control frames cannot be fragmented")
        if length > 125:
            raise WebSocketError("control frame payload must stay under 126 bytes")
    if length > MAX_MESSAGE_SIZE:
        raise WebSocketError("frame payload exceeds the 16MB limit")

    mask_key = b""
    if masked:
        if len(data) < offset + 4:
            raise IncompleteFrame("need the 4-byte mask key")
        mask_key = data[offset:offset + 4]
        offset += 4
    if len(data) < offset + length:
        raise IncompleteFrame("need %d payload bytes" % length)

    payload = data[offset:offset + length]
    if masked:
        payload = bytes(byte ^ mask_key[index & 3] for index, byte in enumerate(payload))
    return Frame(fin=fin, opcode=opcode, payload=payload, masked=masked), offset + length


class WebSocketClient:
    """Blocking WebSocket client used by the single Gateway connection thread."""

    def __init__(self, host: str, port: int, path: str = DEFAULT_WS_PATH,
                 max_message_size: int = MAX_MESSAGE_SIZE):
        self.host = host
        self.port = int(port)
        self.path = path or DEFAULT_WS_PATH
        self.max_message_size = int(max_message_size)
        self._sock = None
        self._buffer = b""
        self._send_lock = threading.Lock()
        self._close_sent = False
        self._closed = True

    @property
    def connected(self) -> bool:
        return self._sock is not None and not self._closed

    # -- connection lifecycle ------------------------------------------------
    def connect(self, timeout: float = 5.0) -> "WebSocketClient":
        """Open the TCP connection and complete the HTTP Upgrade handshake."""
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        try:
            sock.settimeout(timeout)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = (
                "GET %s HTTP/1.1\r\n"
                "Host: %s:%d\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: %s\r\n"
                "Sec-WebSocket-Version: %d\r\n"
                "\r\n"
            ) % (self.path, self.host, self.port, key, WEBSOCKET_VERSION)
            sock.sendall(request.encode("ascii"))

            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    raise WebSocketError("Gateway closed the connection during the WebSocket upgrade")
                response += chunk
                if len(response) > MAX_HANDSHAKE_BYTES:
                    raise WebSocketError("Gateway handshake response exceeded 64KB")
            head, _, rest = response.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            self._check_handshake(lines, key)

            self._sock = sock
            self._buffer = rest
            self._closed = False
            self._close_sent = False
            return self
        except BaseException:
            try:
                sock.close()
            except OSError:
                pass
            raise

    @staticmethod
    def _check_handshake(lines: "list[str]", key: str) -> None:
        status = lines[0].split(" ", 2) if lines else []
        if len(status) < 2 or status[1] != "101":
            raise WebSocketError("Gateway rejected the WebSocket upgrade: %s" % (lines[0] if lines else "no status line"))
        headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().lower()] = value.strip()
        if headers.get("upgrade", "").lower() != "websocket":
            raise WebSocketError("Gateway handshake is missing 'Upgrade: websocket'")
        if "upgrade" not in headers.get("connection", "").lower():
            raise WebSocketError("Gateway handshake is missing 'Connection: Upgrade'")
        if headers.get("sec-websocket-accept") != expected_accept(key):
            raise WebSocketError("Gateway sent an invalid Sec-WebSocket-Accept")

    def close(self) -> None:
        """Send a close frame when possible, then release the socket."""
        if self._sock is not None and not self._close_sent:
            self._close_sent = True
            try:
                self._send_frame(OP_CLOSE, struct.pack("!H", _CLOSE_NORMAL))
            except WebSocketError:
                pass
        self._abort()

    def _abort(self) -> None:
        sock, self._sock = self._sock, None
        self._buffer = b""
        self._closed = True
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def __enter__(self) -> "WebSocketClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- sending -------------------------------------------------------------
    def send_text(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("send_text expects a str")
        payload = text.encode("utf-8")
        if len(payload) > self.max_message_size:
            raise WebSocketError("text message exceeds the 16MB limit")
        self._send_frame(OP_TEXT, payload)

    def ping(self, payload: bytes = b"") -> None:
        self._send_frame(OP_PING, payload)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if not self.connected:
            raise WebSocketError("WebSocket connection is closed")
        frame = encode_frame(opcode, payload, mask=True)
        with self._send_lock:
            try:
                self._sock.sendall(frame)
            except OSError as exc:
                self._abort()
                raise WebSocketError("failed to send a WebSocket frame: %s" % exc) from exc

    # -- receiving -----------------------------------------------------------
    def recv_text(self, timeout: "float | None" = None) -> "str | None":
        """Return the next text message, or None when the Gateway closed cleanly.

        Raises TimeoutError when no finished message arrived within *timeout*,
        and WebSocketError on a protocol violation or a broken connection.
        """
        if not self.connected:
            raise WebSocketError("WebSocket connection is closed")
        try:
            self._sock.settimeout(None if timeout is None else float(timeout))
            return self._recv_text_message()
        except socket.timeout:
            raise TimeoutError(
                "no WebSocket message within %s seconds" % ("the requested timeout" if timeout is None else timeout)
            ) from None

    def _recv_text_message(self) -> "str | None":
        fragments = None
        size = 0
        while True:
            frame = self._recv_frame()
            if frame.opcode == OP_CLOSE:
                self._reply_close()
                self._abort()
                return None
            if frame.opcode == OP_PING:
                self._send_frame(OP_PONG, frame.payload)
                continue
            if frame.opcode == OP_PONG:
                continue
            if frame.opcode == OP_BINARY:
                raise WebSocketError("Gateway sent a binary frame; only text frames are supported")
            if frame.opcode == OP_TEXT:
                if fragments is not None:
                    raise WebSocketError("Gateway started a new message before finishing the fragmented one")
                fragments = []
            elif fragments is None:
                raise WebSocketError("Gateway sent a continuation frame with no message to continue")
            fragments.append(frame.payload)
            size += len(frame.payload)
            if size > self.max_message_size:
                raise WebSocketError("WebSocket message exceeds the 16MB limit")
            if frame.fin:
                payload = b"".join(fragments)
                try:
                    return payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise WebSocketError("Gateway sent invalid UTF-8 in a text frame") from exc

    def _recv_frame(self) -> Frame:
        while True:
            try:
                frame, consumed = decode_frame(self._buffer)
            except IncompleteFrame:
                try:
                    chunk = self._sock.recv(65536)
                except socket.timeout:
                    raise
                except OSError as exc:
                    self._abort()
                    raise WebSocketError("Gateway connection failed while reading: %s" % exc) from exc
                if not chunk:
                    self._abort()
                    raise WebSocketError("Gateway closed the connection")
                self._buffer += chunk
                if len(self._buffer) > self.max_message_size + MAX_FRAME_HEADER_BYTES:
                    self._abort()
                    raise WebSocketError("WebSocket receive buffer exceeded the 16MB limit")
                continue
            self._buffer = self._buffer[consumed:]
            if frame.masked:
                raise WebSocketError("Gateway sent a masked frame (servers must not mask)")
            return frame

    def _reply_close(self) -> None:
        if self._close_sent:
            return
        self._close_sent = True
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", _CLOSE_NORMAL))
        except WebSocketError:
            pass
