# -*- coding: utf-8 -*-
"""Integration-layer environment smoke (F4.1: was `assert True`).

Every test in tests/integration/python assumes two things: loopback TCP works on
this machine and the repository root is importable. The previous body asserted
nothing, so an environment where both were broken would still show green. This
daemon-free probe fails loudly instead.
"""
import json
import socket
import sys
import threading

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _serve_once(server, ready):
    ready.set()
    conn, _addr = server.accept()
    with conn:
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        request = json.loads(data.decode("utf-8"))
        reply = {
            "ok": True,
            "echo": request.get("name"),
            "port": server.getsockname()[1],
        }
        conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))


def test_loopback_socket_roundtrip():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(5.0)
    ready = threading.Event()
    thread = threading.Thread(target=_serve_once, args=(server, ready), daemon=True)
    thread.start()
    assert ready.wait(5.0), "server thread did not start"
    host, port = server.getsockname()[:2]
    try:
        with socket.create_connection((host, port), timeout=5.0) as client:
            client.sendall((json.dumps({"name": "smoke"}) + "\n").encode("utf-8"))
            received = b""
            while not received.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk:
                    break
                received += chunk
        reply = json.loads(received.decode("utf-8"))
        assert reply == {"ok": True, "echo": "smoke", "port": port}
    finally:
        server.close()
        thread.join(timeout=5.0)
    assert not thread.is_alive(), "server thread did not stop"


def test_runtime_package_is_importable_from_the_repo_root():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import runtime.py._facade as facade

    resolved = Path(facade.__file__).resolve()
    assert ROOT in resolved.parents, (str(ROOT), str(resolved))
    assert callable(facade.find_root) and callable(facade.load_impl)
