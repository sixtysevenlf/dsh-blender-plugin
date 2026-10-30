"""Integration checks for the add-on TCP framing (partial frames, UTF-8 splits).

Uses socket.socketpair() as the transport and drives the real _handle_client
thread plus the real _drain_command_queue. No Blender process is involved.
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


def _running_server(monkeypatch):
    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.delenv("BLENDERMCP_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.setattr(server_module, "premium_tripo_status", lambda: {"handled": "get_tripo_status"})
    server = server_module.DSHBlenderBridgeServer(port=0)
    server.running = True
    return server


def _start_client_thread(server, client_socket):
    thread = threading.Thread(target=server._handle_client, args=(client_socket,), daemon=True)
    thread.start()
    return thread


def _wait_for_command(server, timeout=2.0):
    deadline = time.time() + timeout
    while server.command_queue.empty() and time.time() < deadline:
        time.sleep(0.01)
    assert not server.command_queue.empty(), "socket handler did not frame the command"


def _stop(server, thread, peer):
    server.running = False
    try:
        peer.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    peer.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_a_frame_split_across_two_recv_chunks_is_assembled(monkeypatch):
    server = _running_server(monkeypatch)
    client, peer = socket.socketpair()
    thread = _start_client_thread(server, client)
    try:
        frame = json.dumps({"type": "get_scene_info", "params": {"tag": "split"}}).encode("utf-8")
        peer.sendall(frame[:7])
        time.sleep(0.05)
        assert server.command_queue.empty(), "half a frame must not dispatch"
        peer.sendall(frame[7:])
        _wait_for_command(server)
        command, _client = server.command_queue.get_nowait()
        assert command == {"type": "get_scene_info", "params": {"tag": "split"}}
    finally:
        _stop(server, thread, peer)


def test_a_multibyte_utf8_character_split_across_chunks_does_not_crash(monkeypatch):
    server = _running_server(monkeypatch)
    client, peer = socket.socketpair()
    thread = _start_client_thread(server, client)
    try:
        payload = {"type": "execute_code", "params": {"code": "print('\u4e2d\u6587')"}}
        frame = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        marker = "\u4e2d".encode("utf-8")
        split_at = frame.index(marker) + 1
        peer.sendall(frame[:split_at])
        time.sleep(0.05)
        assert server.command_queue.empty()
        peer.sendall(frame[split_at:])
        _wait_for_command(server)
        command, _client = server.command_queue.get_nowait()
        assert command["params"]["code"] == "print('\u4e2d\u6587')"
    finally:
        _stop(server, thread, peer)


def test_two_sequential_commands_on_one_connection_are_framed(monkeypatch):
    server = _running_server(monkeypatch)
    client, peer = socket.socketpair()
    thread = _start_client_thread(server, client)
    try:
        peer.sendall(json.dumps({"type": "ping"}).encode("utf-8"))
        _wait_for_command(server)
        server._drain_command_queue()
        peer.settimeout(2.0)
        assert json.loads(peer.recv(65536).decode("utf-8")) == {
            "status": "success", "result": {"pong": True},
        }

        peer.sendall(json.dumps({"type": "ping", "params": {"again": True}}).encode("utf-8"))
        _wait_for_command(server)
        server._drain_command_queue()
        assert json.loads(peer.recv(65536).decode("utf-8"))["status"] == "success"
    finally:
        _stop(server, thread, peer)


def test_response_is_one_json_document_per_command(monkeypatch):
    server = _running_server(monkeypatch)
    client, peer = socket.socketpair()
    thread = _start_client_thread(server, client)
    try:
        peer.sendall(json.dumps({"type": "get_addon_info", "params": {}}).encode("utf-8"))
        _wait_for_command(server)
        server._drain_command_queue()
        peer.settimeout(2.0)
        raw = peer.recv(65536)
        decoded = json.loads(raw.decode("utf-8"))
        assert decoded["status"] == "success"
        assert decoded["result"]["protocol_version"] == 11
        assert set(decoded) == {"status", "result"}
    finally:
        _stop(server, thread, peer)


def test_disconnect_removes_the_client_and_ends_the_handler(monkeypatch):
    server = _running_server(monkeypatch)
    client, peer = socket.socketpair()
    thread = _start_client_thread(server, client)
    try:
        deadline = time.time() + 2.0
        while not server._clients and time.time() < deadline:
            time.sleep(0.01)
        assert len(server._clients) == 1
        peer.close()
        thread.join(timeout=2.5)
        assert not thread.is_alive(), "handler thread survived client disconnect"
        assert server._clients == set()
    finally:
        server.running = False
        thread.join(timeout=2.0)


def test_invalid_json_does_not_dispatch_or_crash_the_handler(monkeypatch):
    server = _running_server(monkeypatch)
    client, peer = socket.socketpair()
    thread = _start_client_thread(server, client)
    try:
        peer.sendall(b"{not json")
        time.sleep(0.1)
        assert server.command_queue.empty()
        assert thread.is_alive()
    finally:
        _stop(server, thread, peer)
