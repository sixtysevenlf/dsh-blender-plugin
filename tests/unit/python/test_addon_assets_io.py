"""Unit checks for the remaining asset helpers in assets/io.py."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

assets_io = importlib.import_module("addon.dsh_blender_bridge.assets.io")
core = importlib.import_module("addon.dsh_blender_bridge.commands.core")


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
