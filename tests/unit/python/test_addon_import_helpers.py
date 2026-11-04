"""Unit checks for the pure helpers extracted during the add-on split.

These are direct assertions on the helpers the package split produced:
selection caps, AABB geometry, and the Sketchfab / Poly Pizza import result
builders. bpy calls are stubbed; no Blender session is implied.
"""

from __future__ import annotations

import importlib
import mathutils
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

geometry = importlib.import_module("addon.dsh_blender_bridge.assets.geometry")
caps = importlib.import_module("addon.dsh_blender_bridge.selection.caps")
payload = importlib.import_module("addon.dsh_blender_bridge.selection.payload")
sketchfab_importer = importlib.import_module(
    "addon.dsh_blender_bridge.commands.sketchfab.importer")
polypizza_importer = importlib.import_module(
    "addon.dsh_blender_bridge.commands.polypizza.importer")


class _Matrix:
    """Identity matrix stand-in: returns the local corner as the world corner."""

    def __matmul__(self, corner):
        return mathutils.Vector(corner)


class _Object:
    def __init__(self, name="Obj", obj_type="MESH", bound_box=None,
                 parent=None, children=None):
        self.name = name
        self.type = obj_type
        self.bound_box = bound_box or [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)]
        self.matrix_world = _Matrix()
        self.parent = parent
        self.children = children or []
        self.props = {}
        self.scale = SimpleNamespace(x=1.0, y=1.0, z=1.0)

    def __setitem__(self, key, value):
        self.props[key] = value

    def __getitem__(self, key):
        return self.props[key]


def test_compute_aabb_requires_a_mesh():
    try:
        geometry.compute_aabb(_Object(obj_type="EMPTY"))
    except TypeError as error:
        assert "mesh" in str(error)
    else:
        raise AssertionError("compute_aabb accepted a non-mesh object")


def test_compute_aabb_returns_world_min_and_max():
    assert geometry.compute_aabb(_Object()) == [[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]]


def test_cap_selection_truncation_flag():
    assert caps.cap_selection(range(5), cap=2) == ([0, 1], True)
    assert caps.cap_selection([0, 1], cap=2) == ([0, 1], False)
    assert caps.cap_selection([], cap=2) == ([], False)
    values = list(range(caps.SUB_SELECTION_CAP + 3))
    capped, truncated = caps.cap_selection(values)
    assert len(capped) == caps.SUB_SELECTION_CAP
    assert truncated is True


def test_read_sub_selection_object_mode_returns_none():
    bpy.context.mode = "OBJECT"
    assert payload.read_sub_selection() is None


def test_read_sub_selection_mesh_error_is_reported_honestly():
    bpy.context.mode = "EDIT_MESH"
    bpy.context.edit_object = _Object()
    try:
        result = payload.read_sub_selection()
        assert result["type"] == "MESH"
        assert result["note"].startswith("读取选择失败：")
        assert result["indices"] == {}
    finally:
        bpy.context.mode = "OBJECT"
        bpy.context.edit_object = None


def test_read_sub_selection_other_edit_mode_is_explicit():
    bpy.context.mode = "EDIT_TEXT"
    bpy.context.edit_object = _Object()
    try:
        result = payload.read_sub_selection()
        assert result["type"] == "OTHER"
        assert "暂未展开" in result["note"]
    finally:
        bpy.context.mode = "OBJECT"
        bpy.context.edit_object = None


def test_sketchfab_import_result_without_meshes_has_no_bounding_box():
    result = sketchfab_importer.build_import_result(
        ["Obj"], [], [], normalize_size=False, target_size=1.0)
    assert result == {
        "success": True,
        "message": "Model imported successfully",
        "imported_objects": ["Obj"],
    }


def test_sketchfab_import_result_normalizes_root_scale():
    bpy.context.view_layer.update = lambda: None
    root = _Object(name="Root")
    child = _Object(name="Child", parent=root)
    root.children = [child]
    roots, meshes = sketchfab_importer.split_imported_objects([root, child])
    assert roots == [root]
    assert meshes == [root, child]

    result = sketchfab_importer.build_import_result(
        ["Root", "Child"], roots, meshes, normalize_size=True, target_size=2.0)
    assert result["normalized"] is True
    assert result["scale_applied"] == round(2.0 / 3.0, 6)
    assert root.scale == (2.0 / 3.0, 2.0 / 3.0, 2.0 / 3.0)
    assert result["world_bounding_box"] == [[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]]
    assert result["dimensions"] == [1.0, 2.0, 3.0]



def test_polypizza_safe_model_id_sanitizes_filenames():
    assert polypizza_importer.safe_model_id("a b/c!") == "a_b_c_"
    assert polypizza_importer.safe_model_id("model-1_A") == "model-1_A"
    assert polypizza_importer.safe_model_id("") == "model"


def test_polypizza_attribution_and_result_fields():
    root = _Object(name="Root")
    model = {
        "ID": "abc",
        "Title": "Chair",
        "Licence": "CC-BY",
        "Attribution": "Someone",
        "Tri Count": 12,
    }
    attribution, licence = polypizza_importer.apply_attribution([root], model, "fallback")
    assert (attribution, licence) == ("Someone", "CC-BY")
    assert root.props["polypizza_attribution"] == "Someone"
    assert root.props["polypizza_id"] == "abc"
    assert root.props["polypizza_licence"] == "CC-BY"

    result = polypizza_importer.build_download_result(
        ["Root"], [root], [], model, "fallback",
        normalize_size=True, target_size=2.0)
    assert result["model_id"] == "abc"
    assert result["title"] == "Chair"
    assert result["licence"] == "CC-BY"
    assert result["attribution"] == "Someone"
    assert result["tri_count"] == 12
    assert "world_bounding_box" not in result
    assert result["normalized"] is True
    assert result["scale_applied"] == 1.0
