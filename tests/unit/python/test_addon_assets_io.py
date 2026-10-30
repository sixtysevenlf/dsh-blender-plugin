"""Unit checks for safe download file names, texture matching and import errors (assets/io.py)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

assets_io = importlib.import_module("addon.dsh_blender_bridge.assets.io")
core = importlib.import_module("addon.dsh_blender_bridge.commands.core")


def test_safe_file_name_strips_paths_and_unsafe_characters():
    assert assets_io._safe_file_name("../../evil.zip", "fallback") == "evil.zip"
    assert assets_io._safe_file_name("a/../b.glb", "fallback") == "b.glb"
    assert assets_io._safe_file_name("hello world.png", "fallback") == "hello_world.png"
    assert assets_io._safe_file_name("naïve-模型.obj", "fallback") == "na_ve-__.obj"
    assert assets_io._safe_file_name("", "fallback") == "fallback"
    assert assets_io._safe_file_name(None, "fallback") == "fallback"
    assert assets_io._safe_file_name("..", "fallback") == "fallback"
    assert assets_io._safe_file_name(".hidden", "fallback") == ".hidden"
    assert assets_io._safe_file_name("model.GLB", "fallback") == "model.GLB"
    assert assets_io.MODEL_DOWNLOAD_TIMEOUT == 120


def test_texture_matching_copies_a_single_missing_reference(tmp_path):
    (tmp_path / "model.mtl").write_text("newmtl mat\nmap_Kd missing.png\n", encoding="utf-8")
    (tmp_path / "spare.jpg").write_bytes(b"image-bytes")
    assets_io._match_texture_names(str(tmp_path))
    assert (tmp_path / "missing.png").read_bytes() == b"image-bytes"


def test_texture_matching_ignores_ambiguous_or_unsafe_cases(tmp_path):
    (tmp_path / "model.mtl").write_text("newmtl mat\nmap_Kd missing1.png\nmap_Kd missing2.png\n",
                                        encoding="utf-8")
    (tmp_path / "spare.jpg").write_bytes(b"other")
    assets_io._match_texture_names(str(tmp_path))
    assert not (tmp_path / "missing1.png").exists()
    assert not (tmp_path / "missing2.png").exists()

    # A path in the MTL is reduced to its basename before the safe-name check;
    # the copy stays inside the download directory.
    (tmp_path / "model.mtl").write_text("newmtl mat\nmap_Kd ../evil.png\n", encoding="utf-8")
    assets_io._match_texture_names(str(tmp_path))
    assert (tmp_path / "evil.png").read_bytes() == b"other"


class _DownloadResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.raised = False

    def raise_for_status(self):
        self.raised = True

    def iter_content(self, chunk_size=8192):
        for chunk in self.chunks:
            yield chunk


def test_download_model_files_writes_safe_unique_names_and_returns_model(tmp_path, monkeypatch):
    requested = []

    def fake_get(url, stream=False, timeout=None):
        requested.append({"url": url, "stream": stream, "timeout": timeout})
        return _DownloadResponse([b"part-", b"two"])

    monkeypatch.setattr(assets_io.requests, "get", fake_get)
    files = [
        {"role": "texture", "format": "PNG", "url": "https://example.com/tex/../../tex.png",
         "file_name": "../../tex.png"},
        {"role": "model", "format": "OBJ", "url": "https://example.com/model.obj",
         "file_name": "..\\..\\model.obj"},
        {"role": "material", "format": "mtl", "url": "https://example.com/model.mtl",
         "file_name": "model.obj"},
    ]
    path, fmt = assets_io._download_model_files(files, str(tmp_path))
    assert fmt == "obj"
    assert path == str(tmp_path / "model.obj")
    assert (tmp_path / "model.obj").read_bytes() == b"part-two"
    assert (tmp_path / "tex.png").read_bytes() == b"part-two"
    assert (tmp_path / "2_model.obj").read_bytes() == b"part-two"
    assert all(item["stream"] is True and item["timeout"] == 120 for item in requested)
    assert len(requested) == 3


def test_download_model_files_rejects_non_http_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(assets_io.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not download")
    ))
    try:
        assets_io._download_model_files([{"role": "model", "format": "glb",
                                          "url": "file:///etc/passwd"}], str(tmp_path))
    except ValueError as exc:
        assert str(exc) == "Invalid model file URL."
    else:
        raise AssertionError("expected ValueError")


def test_download_model_files_requires_a_model_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(assets_io.requests, "get", lambda *a, **k: _DownloadResponse([b"x"]))
    try:
        assets_io._download_model_files([{"role": "texture", "format": "png",
                                          "url": "https://example.com/tex.png"}], str(tmp_path))
    except ValueError as exc:
        assert str(exc) == "No model file to import."
    else:
        raise AssertionError("expected ValueError")


def test_import_mesh_file_raises_when_the_importer_imports_nothing(tmp_path, monkeypatch):
    previous = getattr(bpy.ops, "import_scene", None)
    try:
        bpy.ops.import_scene = SimpleNamespace(gltf=lambda **kwargs: None)
        path = tmp_path / "empty.glb"
        path.write_bytes(b"not-a-real-glb")
        try:
            assets_io._import_mesh_file(str(path), "glb", "mesh")
        except ValueError as exc:
            assert str(exc) == "No mesh objects were imported."
        else:
            raise AssertionError("expected ValueError")
    finally:
        if previous is not None:
            bpy.ops.import_scene = previous


def test_import_model_files_wraps_errors_and_cleans_up(monkeypatch):
    cleanup = {}
    real_mkdtemp = assets_io.tempfile.mkdtemp
    real_rmtree = assets_io.shutil.rmtree

    def fake_mkdtemp(prefix=""):
        path = real_mkdtemp(prefix=prefix)
        cleanup["dir"] = path
        return path

    def fake_rmtree(path):
        cleanup["removed"] = path
        real_rmtree(path)

    monkeypatch.setattr(assets_io.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(assets_io.shutil, "rmtree", fake_rmtree)
    result = assets_io.import_model_files([{"role": "model", "format": "glb",
                                            "url": "ftp://example.com/model.glb"}], "mesh")
    assert result == {"succeed": False, "error": "Invalid model file URL."}
    assert cleanup["removed"] == cleanup["dir"]


def test_collect_mesh_children_walks_descendants():
    leaf = SimpleNamespace(name="leaf", type="MESH", children=[])
    branch = SimpleNamespace(name="branch", type="MESH", children=[leaf])
    empty = SimpleNamespace(name="empty", type="EMPTY", children=[branch])
    assert assets_io.collect_mesh_children(leaf) == [leaf]
    assert assets_io.collect_mesh_children(empty) == [branch, leaf]


def test_clean_imported_glb_returns_none_when_nothing_was_imported(tmp_path):
    previous_view = bpy.context.view_layer
    previous_imports = getattr(bpy.ops, "import_scene", None)
    try:
        bpy.context.view_layer = SimpleNamespace(objects=[], update=lambda: None)
        bpy.ops.import_scene = SimpleNamespace(gltf=lambda **kwargs: None)
        path = tmp_path / "empty.glb"
        path.write_bytes(b"x")
        assert assets_io.clean_imported_glb(str(path), "mesh") is None
    finally:
        bpy.context.view_layer = previous_view
        if previous_imports is not None:
            bpy.ops.import_scene = previous_imports


def test_core_get_aabb_rejects_non_mesh_objects():
    try:
        core.CoreCommands._get_aabb(SimpleNamespace(type="EMPTY"))
    except TypeError as exc:
        assert str(exc) == "Object must be a mesh"
    else:
        raise AssertionError("expected TypeError")
