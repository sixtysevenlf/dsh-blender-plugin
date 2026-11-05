"""Pure-logic tests for the qc dispatch table (no Blender).

Pins the catalog-facing qc ops added for the terminal-state closeout:
`compare_auto` and the path-level `align_iou` wrapper. The wrapper was
previously covered only by the native Blender E2E; these tests pin its
empty-mask, missing-argument and wrong-argument behaviour plus the
dispatch-table registrations, using synthetic NumPy images and a stubbed
`qc_load` so no bpy/Blender is needed.

The shared kernel module is bootstrapped by executing the real
runtime/py/core/kit.py; qc_impl/common.py requires its `dsh_kit` object at
import time, so this exercises the production kernel without Blender.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
import types

import numpy as np
import pytest

from bpy_stub import install as install_bpy_stub

ROOT = pathlib.Path(__file__).resolve().parents[3]
RUNTIME_PY = ROOT / "runtime" / "py"
KIT = RUNTIME_PY / "core" / "kit.py"


def _boot_kernel() -> None:
    kernel = types.ModuleType("dsh_rt_kernel")
    sys.modules["dsh_rt_kernel"] = kernel
    with open(KIT, encoding="utf-8") as fh:
        exec(compile(fh.read(), str(KIT), "exec"), {})


@pytest.fixture(scope="module")
def qc_api():
    install_bpy_stub()
    saved = sys.modules.get("dsh_rt_kernel")
    _boot_kernel()
    for name in [name for name in sys.modules if name == "render" or name.startswith("render.")]:
        del sys.modules[name]
    sys.path.insert(0, str(RUNTIME_PY))
    try:
        yield importlib.import_module("render.qc_impl.api")
    finally:
        sys.path.remove(str(RUNTIME_PY))
        if saved is None:
            sys.modules.pop("dsh_rt_kernel", None)
        else:
            sys.modules["dsh_rt_kernel"] = saved


def _image(width: int, height: int, size: int = 128, origin: tuple[int, int] = (20, 10)):
    x0, y0 = origin
    srgb = np.zeros((size, size, 3), dtype=np.float32)
    srgb[y0 : y0 + height, x0 : x0 + width] = 1.0
    alpha = np.ones((size, size), dtype=np.float32)
    return srgb, alpha


def _fake_load(images):
    def load(path):
        return images[str(path)]

    return load


def _dispatch(api, op: str, payload):
    return json.loads(api.qc_dispatch(op, payload))


def test_dispatch_table_registers_catalog_qc_ops(qc_api):
    ops = qc_api.qc_ops()
    assert ops["compare_auto"] is qc_api.qc_compare_auto
    assert ops["align_iou"] is qc_api.qc_align_iou_op
    help_payload = json.loads(qc_api.qc_help())
    assert "compare_auto" in help_payload["ops"]
    assert "align_iou" in help_payload["ops"]


def test_align_iou_identical_images(qc_api, monkeypatch):
    monkeypatch.setattr(qc_api, "qc_load", _fake_load({"ref": _image(41, 41), "render": _image(41, 41)}))
    out = _dispatch(qc_api, "align_iou", {"ref_path": "ref", "render_path": "render"})
    assert out["ok"] is True, out
    assert out["iou"] == pytest.approx(1.0, abs=0.02)


def test_align_iou_narrower_render_lowers_iou(qc_api, monkeypatch):
    monkeypatch.setattr(
        qc_api, "qc_load", _fake_load({"ref": _image(41, 41), "render": _image(21, 41)})
    )
    out = _dispatch(qc_api, "align_iou", {"ref_path": "ref", "render_path": "render"})
    assert out["ok"] is True, out
    assert 0.4 < out["iou"] < 0.65


def test_align_iou_reports_empty_masks(qc_api, monkeypatch):
    empty = (np.zeros((64, 64, 3), dtype=np.float32), np.ones((64, 64), dtype=np.float32))
    monkeypatch.setattr(qc_api, "qc_load", _fake_load({"ref": empty, "render": empty}))
    out = _dispatch(qc_api, "align_iou", {"ref_path": "ref", "render_path": "render"})
    assert out["ok"] is False, out
    assert "掩膜为空" in out["error"]


def test_align_iou_rejects_missing_render_path(qc_api):
    out = _dispatch(qc_api, "align_iou", {"ref_path": "ref"})
    assert out["ok"] is False, out
    assert "render_path" in out["error"]


def test_align_iou_rejects_empty_ref_path(qc_api):
    out = _dispatch(qc_api, "align_iou", {"ref_path": "", "render_path": "render"})
    assert out["ok"] is False, out
    assert "ref_path" in out["error"]


def test_align_iou_wrong_kwargs_surfaces_help(qc_api):
    out = _dispatch(qc_api, "align_iou", {"ref": "a.png"})
    assert out["ok"] is False, out
    assert "参数不匹配" in out["error"]
    assert "align_iou" in out["sig_hint"]


def test_unknown_op_lists_available_ops(qc_api):
    out = _dispatch(qc_api, "nope", {})
    assert out["ok"] is False, out
    assert "unknown qc op" in out["error"]
    assert "compare_auto" in out["ops"]
    assert "align_iou" in out["ops"]
