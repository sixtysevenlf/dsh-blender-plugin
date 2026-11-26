# -*- coding: utf-8 -*-
"""Regression guard for the offscreen colour-readback bug (#98).

``GPUOffScreen.texture_color.read()`` returns a Buffer whose internal stride
follows the *texture* layout, while every consumer in this module (``write_png``)
expects tightly packed RGBA8. Copying it with ``bytes(memoryview(buf))`` without
pinning ``buf.dimensions`` skews every row and the frame degenerates into
diagonal colour noise. The byte length stays correct, so nothing raises -- the
failure is completely silent, which is why it was reported as a driver bug.

Measured on Blender 4.2.22 / RTX 3050 / NVIDIA 546.92:
  * adjacent-column delta ~97.6 when unpinned vs ~1.1 when pinned
  * ``clear((1,0,0,1))`` read back as ``[[0,0,0],[255,255,255]]`` instead of
    ``[[255,0,0]]`` -- reproduces the report in #98 verbatim

This suite pins the fix without Blender, a GPU, or a GUI.
"""

import ast
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
VIEW = ROOT / "runtime" / "py" / "render" / "view.py"


class _Kit:
    """Minimal stand-in for the shared kernel injected by KERNEL_BOOTSTRAP."""

    j = None

    class Api(dict):
        def __init__(self, payload=None, *args, **kwargs):
            super().__init__(payload or {})

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeBuffer(bytearray):
    """Buffer-protocol object that also carries ``dimensions`` like a GPU Buffer."""

    def __init__(self, data):
        super().__init__(data)
        self.dimensions = None


class _FakeTexture:
    def __init__(self, width, height, fill):
        self._raw = bytes([fill]) * (width * height * 4)
        self.buffer = None

    def read(self):
        self.buffer = _FakeBuffer(self._raw)
        return self.buffer


class _FakeOffscreen:
    def __init__(self, width, height, fill=7):
        self.texture_color = _FakeTexture(width, height, fill)


@pytest.fixture(scope="module")
def view_module():
    saved = {name: sys.modules.get(name) for name in ("bpy", "dsh_rt_kernel")}
    sys.modules["bpy"] = types.ModuleType("bpy")
    kernel = types.ModuleType("dsh_rt_kernel")
    kernel.dsh_kit = _Kit()
    sys.modules["dsh_rt_kernel"] = kernel
    try:
        spec = importlib.util.spec_from_file_location("view_under_test", str(VIEW))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def _read_function():
    """Return the ast.FunctionDef of read_offscreen_rgba."""
    tree = ast.parse(VIEW.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "read_offscreen_rgba":
            return node
    raise AssertionError("read_offscreen_rgba is missing")


def test_dimensions_are_pinned_to_the_frame_size(view_module):
    """The Buffer must report width*height*4 -- that is the whole fix."""
    off = _FakeOffscreen(8, 4)
    view_module.read_offscreen_rgba(off, 8, 4)
    assert off.texture_color.buffer.dimensions == 8 * 4 * 4


def test_odd_resolution_still_gets_the_pinned_length(view_module):
    """315 / 323 are the heights from the two field reports."""
    for height in (315, 323):
        off = _FakeOffscreen(560, height)
        view_module.read_offscreen_rgba(off, 560, height)
        assert off.texture_color.buffer.dimensions == 560 * height * 4


def test_dimensions_are_set_before_the_bytes_are_taken():
    """Pin the *order*: assigning after the copy would not fix anything."""
    fn = _read_function()
    dims_line = None
    copy_line = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "dimensions":
                    dims_line = node.lineno if dims_line is None else min(dims_line, node.lineno)
        if isinstance(node, ast.Call):
            src = ast.dump(node)
            if "memoryview" in src and "bytes" in src:
                copy_line = node.lineno if copy_line is None else min(copy_line, node.lineno)
    assert dims_line is not None, "read_offscreen_rgba must assign .dimensions"
    assert copy_line is not None, "read_offscreen_rgba must copy the buffer"
    assert dims_line < copy_line, "dimensions must be pinned before bytes(memoryview(...))"


def test_returns_tightly_packed_rgba(view_module):
    off = _FakeOffscreen(8, 4, fill=9)
    rgba = view_module.read_offscreen_rgba(off, 8, 4)
    assert len(rgba) == 8 * 4 * 4
    assert set(rgba) == {9}


def test_plain_bytes_buffer_is_tolerated(view_module):
    """A stubbed ``read()`` returning plain bytes has no ``dimensions`` slot."""

    class _PlainTexture:
        def read(self):
            return b"\x01" * (4 * 2 * 4)

    class _PlainOffscreen:
        texture_color = _PlainTexture()

    rgba = view_module.read_offscreen_rgba(_PlainOffscreen(), 4, 2)
    assert len(rgba) == 4 * 2 * 4


def test_no_bare_texture_read_remains_in_source():
    """Guard against re-introducing the unpinned one-liner."""
    src = VIEW.read_text(encoding="utf-8")
    assert "bytes(memoryview(off.texture_color.read()))" not in src
    assert src.count("off.texture_color.read()") == 1, "only read_offscreen_rgba may read the texture"


def _function_lines(name):
    """Return the source of one top-level function, by line span."""
    src = VIEW.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(src.splitlines()[node.lineno - 1 : node.end_lineno])
    raise AssertionError(name + " is missing from view.py")


def test_each_call_site_pins_the_size_of_its_own_offscreen():
    """GPUOffScreen(w, h) honours explicit width/height overrides, so the pin must
    use those same values -- not s["width"]/s["height"], which can differ in the
    ``current`` mode where the projection is recomputed for the target size."""
    capture = _function_lines("_capture_viewport")
    assert "GPUOffScreen(w, h)" in capture
    assert "read_offscreen_rgba(off, w, h)" in capture

    host = _function_lines("host_capture")
    assert "GPUOffScreen(w, h)" in host
    assert "read_offscreen_rgba(off, w, h)" in host

    selftest = _function_lines("selftest")
    assert "GPUOffScreen(W, H)" in selftest
    assert "read_offscreen_rgba(off, W, H)" in selftest


def test_selftest_asserts_whole_frame_uniformity():
    """first_px is always correct, so it alone cannot detect stride skew."""
    src = VIEW.read_text(encoding="utf-8")
    assert 'out["uniform"]' in src
    assert 'out["distinct_px"]' in src
    assert 'and out["uniform"]' in src
