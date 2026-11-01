"""Unit checks for the pure file helpers in assets/io.py (F3.3/F3.4).

Downloads are exercised with an in-memory requests double: no socket is opened
and no file leaves the pytest tmp_path.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

io_module = importlib.import_module("addon.dsh_blender_bridge.assets.io")


def test_safe_file_name_strips_paths_and_falls_back():
    assert io_module._safe_file_name("models/chair.glb", "fallback.glb") == "chair.glb"
    assert io_module._safe_file_name("bad name?.obj", "fallback.obj") == "bad_name_.obj"
    assert io_module._safe_file_name("..", "fallback.bin") == "fallback.bin"
    assert io_module._safe_file_name("", "fallback.bin") == "fallback.bin"
    assert io_module._safe_file_name(None, "fallback.bin") == "fallback.bin"


def test_match_texture_names_copies_the_single_spare_image(tmp_path):
    (tmp_path / "chair.mtl").write_text(
        "newmtl Chair\nmap_Kd textures/chair_diffuse.png\n", encoding="utf-8")
    spare = tmp_path / "download.png"
    spare.write_bytes(b"png")
    io_module._match_texture_names(str(tmp_path))
    copied = tmp_path / "chair_diffuse.png"
    assert copied.is_file()
    assert copied.read_bytes() == b"png"


def test_match_texture_names_does_nothing_when_ambiguous(tmp_path):
    (tmp_path / "chair.mtl").write_text(
        "newmtl Chair\nmap_Kd a.png\nmap_Ks b.png\n", encoding="utf-8")
    (tmp_path / "spare.png").write_bytes(b"png")
    io_module._match_texture_names(str(tmp_path))
    assert not (tmp_path / "a.png").exists()
    assert not (tmp_path / "b.png").exists()


class _FakeResponse:
    def __init__(self, payload=b"data"):
        self.payload = payload

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):  # noqa: ARG002 - interface match
        yield self.payload


def test_download_model_files_rejects_non_http_urls(tmp_path):
    try:
        io_module._download_model_files(
            [{"role": "model", "format": "obj", "url": "file:///etc/passwd"}], str(tmp_path))
    except ValueError as error:
        assert "URL" in str(error)
    else:
        raise AssertionError("invalid URL was accepted")


def test_download_model_files_requires_a_model_file(tmp_path):
    monkeypatch_get = SimpleNamespace(get=lambda *args, **kwargs: _FakeResponse())
    original = io_module.requests
    io_module.requests = monkeypatch_get
    try:
        try:
            io_module._download_model_files(
                [{"role": "texture", "format": "png", "url": "https://example.test/t.png",
                  "file_name": "t.png"}], str(tmp_path))
        except ValueError as error:
            assert "No model file" in str(error)
        else:
            raise AssertionError("missing model file was accepted")
    finally:
        io_module.requests = original


def test_download_model_files_downloads_named_and_duplicate_files(tmp_path):
    original = io_module.requests
    io_module.requests = SimpleNamespace(get=lambda *args, **kwargs: _FakeResponse(b"x"))
    try:
        path, fmt = io_module._download_model_files([
            {"role": "model", "format": "GLB", "url": "https://example.test/m.glb",
             "file_name": "chair.glb"},
            {"role": "texture", "format": "png", "url": "https://example.test/t.png",
             "file_name": "chair.glb"},
        ], str(tmp_path))
    finally:
        io_module.requests = original
    assert path == str(tmp_path / "chair.glb")
    assert fmt == "glb"
    assert (tmp_path / "1_chair.glb").read_bytes() == b"x"


def test_collect_mesh_children_is_recursive():
    leaf = SimpleNamespace(type="MESH", children=[])
    other = SimpleNamespace(type="EMPTY", children=[leaf])
    root = SimpleNamespace(type="MESH", children=[other])
    assert io_module.collect_mesh_children(root) == [root, leaf]
