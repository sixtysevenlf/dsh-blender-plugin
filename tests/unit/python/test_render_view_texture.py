"""Unit checks for the render view helpers (no GPU, no window).

Covers the two shapes that used to break cross-version:
- GPUTexture.read() Buffer unpacking (4.2/4.5 non-contiguous vs 5.2 contiguous);
- "x,y,z" coordinate strings for custom views (the old custom see 502).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import bpy_stub
import pytest

bpy_stub.install()

ROOT = pathlib.Path(__file__).resolve().parents[3]
VIEW_PATH = ROOT / "runtime" / "py" / "render" / "view.py"


def _load_view():
    kernel = types.ModuleType("dsh_rt_kernel")
    kernel.dsh_kit = types.SimpleNamespace(
        j=lambda value: value if isinstance(value, str) else str(value),
        Api=lambda spec: spec,
    )
    previous = sys.modules.get("dsh_rt_kernel")
    sys.modules["dsh_rt_kernel"] = kernel
    try:
        spec = importlib.util.spec_from_file_location("dsh_render_view_under_test", VIEW_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("dsh_rt_kernel", None)
        else:
            sys.modules["dsh_rt_kernel"] = previous


RED = bytes([255, 0, 0, 255])


def test_contiguous_buffer_is_copied_without_to_list():
    module = _load_view()
    raw = RED + bytes([0, 255, 0, 255]) + bytes([0, 0, 255, 255]) + bytes([9, 9, 9, 255])
    assert module._texture_rgba(memoryview(raw), 2, 2) == raw


def test_non_contiguous_buffer_falls_back_to_to_list():
    module = _load_view()

    class Buffer:
        def to_list(self):
            return [[[255, 0, 0, 255], [0, 255, 0, 255]], [[0, 0, 255, 255], [9, 9, 9, 255]]]

    expected = RED + bytes([0, 255, 0, 255]) + bytes([0, 0, 255, 255]) + bytes([9, 9, 9, 255])
    assert module._texture_rgba(Buffer(), 2, 2) == expected


def test_flat_to_list_is_flattened_depth_agnostically():
    module = _load_view()

    class Buffer:
        def to_list(self):
            return [255, 0, 0, 255, 0, 255, 0, 255]

    assert module._texture_rgba(Buffer(), 2, 1) == RED + bytes([0, 255, 0, 255])


def test_strided_memoryview_is_rejected_instead_of_copied_blindly():
    module = _load_view()
    base = bytearray(32)
    strided = memoryview(base)[::2]
    assert strided.c_contiguous is False
    try:
        module._texture_rgba(strided, 2, 2)
    except RuntimeError as exc:
        assert "contiguous" in str(exc)
    else:
        raise AssertionError("non-contiguous buffers must not be copied blindly")


def test_contiguous_but_wrongly_shaped_buffer_is_rejected():
    module = _load_view()
    raw = b"\x00" * 16
    wrongly_shaped = memoryview(raw).cast("B", shape=[4, 2, 2])
    assert wrongly_shaped.c_contiguous is True
    try:
        module._texture_rgba(wrongly_shaped, 2, 2)
    except RuntimeError as exc:
        assert "contiguous" in str(exc)
    else:
        raise AssertionError("a contiguous buffer with the wrong shape must not be copied blindly")


def test_wrong_readback_size_raises():
    module = _load_view()
    try:
        module._texture_rgba(memoryview(b"1234"), 2, 2)
    except RuntimeError as exc:
        assert "16" in str(exc)
    else:
        raise AssertionError("size mismatch must raise")


def test_spec_accepts_csv_coordinates_of_custom_views():
    module = _load_view()
    spec = module._spec({"from": "6,-6,4", "look_at": "0,0,0", "mode": "viewport"})
    assert spec["from"] == [6.0, -6.0, 4.0]
    assert spec["look_at"] == [0.0, 0.0, 0.0]


def test_current_view_pose_uses_the_frozen_view_matrix():
    module = _load_view()

    class Row:
        def __init__(self, values):
            self.values = values

        def __getitem__(self, index):
            return self.values[index]

    class Matrix:
        def __init__(self, rows, translation):
            self.rows = [Row(row) for row in rows]
            self.translation = types.SimpleNamespace(x=translation[0], y=translation[1], z=translation[2])

        def __getitem__(self, index):
            return self.rows[index]

        def inverted(self):
            return self

    world = Matrix(
        [[1, 0, 0, 4], [0, 1, 0, -4], [0, 0, 1, 3], [0, 0, 0, 1]],
        (4, -4, 3),
    )
    view = types.SimpleNamespace(inverted=lambda: world)
    assert module._pose_from_view_matrix(view) == ([4.0, -4.0, 3.0], [4.0, -4.0, 2.0])


def test_spec_rejects_incomplete_coordinates():
    module = _load_view()
    for bad in ("6,-6", "a,b,c", [1, 2]):
        try:
            module._spec({"from": bad})
        except ValueError:
            continue
        raise AssertionError("bad coordinate %r must raise" % (bad,))


def _camera_fixture(ctype="ORTHO"):
    class Vec:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = x, y, z

    class Quaternion:
        def __matmul__(self, value):
            return Vec(0.0, 0.0, -1.0)

    class World:
        translation = Vec(1.0, 2.0, 3.0)

        def to_quaternion(self):
            return Quaternion()

        def inverted(self):
            return "view-matrix"

    camera_data = types.SimpleNamespace(
        type=ctype,
        lens=35.0,
        sensor_width=36.0,
        sensor_fit="AUTO",
        clip_start=0.1,
        clip_end=1000.0,
        ortho_scale=12.0,
        shift_x=0.0,
        shift_y=0.0,
        dof=types.SimpleNamespace(use_dof=False),
    )

    class Evaluated:
        matrix_world = World()
        data = camera_data

        def calc_matrix_camera(self, depsgraph, x=0, y=0):
            return {"x": x, "y": y}

    class Camera:
        name = "Cam"
        data = camera_data

        def evaluated_get(self, depsgraph):
            return Evaluated()

    return Camera()


def _camera_scene():
    return types.SimpleNamespace(
        frame_current=1,
        render=types.SimpleNamespace(
            resolution_x=1920,
            resolution_y=1080,
            pixel_aspect_x=1.0,
            pixel_aspect_y=1.0,
            use_motion_blur=False,
        )
    )


def test_camera_capture_publishes_evaluated_ortho_metadata(monkeypatch):
    module = _load_view()
    monkeypatch.setattr(module.bpy, "context", types.SimpleNamespace(
        evaluated_depsgraph_get=lambda: "depsgraph"))
    spec = {"max_size": 544}

    view, proj, width, height, extra = module._camera_matrices(
        spec, _camera_scene(), _camera_fixture())

    assert view == "view-matrix"
    assert proj == {"x": 544, "y": 306}
    assert (width, height) == (544, 306)
    # The /view header and the presence frustum read these from spec: an
    # orthographic camera must publish ortho + ortho_scale, never a perspective
    # cone with the defaults.
    assert spec["ortho"] is True
    assert spec["ortho_scale"] == 12.0
    assert spec["sensor"] == 36.0
    assert spec["sensor_fit"] == "AUTO"
    assert spec["clip_start"] == 0.1
    assert spec["clip_end"] == 1000.0
    assert spec["lens"] == 35.0
    assert spec["from"] == [1.0, 2.0, 3.0]
    assert spec["look_at"] == [1.0, 2.0, 2.0]
    assert extra["camera"] == "Cam"
    assert extra["preview_only"] is True


@pytest.mark.parametrize("bad", [600, 1000, "big"])
def test_camera_max_size_only_accepts_the_documented_tiers(monkeypatch, bad):
    module = _load_view()
    monkeypatch.setattr(module.bpy, "context", types.SimpleNamespace(
        evaluated_depsgraph_get=lambda: "depsgraph"))
    with pytest.raises(RuntimeError) as exc:
        module._camera_matrices({"max_size": bad}, _camera_scene(), _camera_fixture())
    assert "invalid_params" in str(exc.value)


def test_pano_camera_is_rejected_before_any_capture(monkeypatch):
    module = _load_view()
    monkeypatch.setattr(module.bpy, "context", types.SimpleNamespace(
        evaluated_depsgraph_get=lambda: "depsgraph"))
    with pytest.raises(RuntimeError) as exc:
        module._camera_matrices({}, _camera_scene(), _camera_fixture("PANO"))
    assert "PANO" in str(exc.value)
