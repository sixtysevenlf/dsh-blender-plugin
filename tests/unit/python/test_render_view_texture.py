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


def test_spec_rejects_incomplete_coordinates():
    module = _load_view()
    for bad in ("6,-6", "a,b,c", [1, 2]):
        try:
            module._spec({"from": bad})
        except ValueError:
            continue
        raise AssertionError("bad coordinate %r must raise" % (bad,))
