# -*- coding: utf-8 -*-
"""Native E2E: qc_compare_auto / qc_align_iou are really dispatchable.

The catalog has advertised both ops for a long time, but dsh_qc_api had no
literal dispatch key for them, so the plan route answered "unknown qc op".
This case drives the real K.dsh_qc_api["dispatch"] path with synthetic PNGs.
"""
from __future__ import annotations

import json
import struct
import zlib

import numpy as np


def _write_rgb_png(path, rgb):
    """Write a color-type-2 PNG from an HxWx3 uint8 array (Blender-safe)."""
    h, w = rgb.shape[:2]
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y].astype(np.uint8).tobytes()

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(
            ">I", zlib.crc32(tag + body) & 0xFFFFFFFF
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return str(path)


def _block_image(path, width=48, height=48, x0=32, y0=32):
    """White 128x128 canvas with one black block; x0/y0 can park it at the origin."""
    img = np.full((128, 128, 3), 255, dtype=np.uint8)
    img[y0:min(128, y0 + height), x0:min(128, x0 + width)] = 0
    return _write_rgb_png(path, img)


def test_qc_drift_ops_are_dispatchable(ctx):
    qc = ctx.api("qc")
    ref = _block_image(ctx.test_dir / "qc_ref.png")
    same = _block_image(ctx.test_dir / "qc_same.png")
    narrow = _block_image(ctx.test_dir / "qc_narrow.png", width=24)

    cmp_args = {"ref_path": ref, "render_path": same, "label": "dispatch", "search": False}
    cmp_res = ctx.parse(qc["dispatch"]("compare_auto", json.dumps(cmp_args)))
    ctx.check("compare_auto dispatches", isinstance(cmp_res, dict) and cmp_res.get("ok") is True, cmp_res)
    ctx.check("compare_auto returns iou", isinstance(cmp_res, dict) and cmp_res.get("iou") is not None, cmp_res)
    cmp_text = json.dumps(cmp_res, ensure_ascii=False)
    ctx.check("compare_auto is not unknown qc op", "unknown qc op" not in cmp_text, cmp_text[:400])

    same_res = ctx.parse(qc["dispatch"]("align_iou", json.dumps({"ref_path": ref, "render_path": same})))
    ctx.check("align_iou dispatches", isinstance(same_res, dict) and same_res.get("ok") is True, same_res)
    same_iou = float(same_res.get("iou", 0.0)) if isinstance(same_res, dict) else 0.0
    ctx.check("align_iou same image ~= 1", same_iou > 0.98, same_iou)

    # bbox alignment deliberately cancels translation, so the negative case is a
    # shape change (same height, half width): IoU must drop clearly below 1.
    narrow_res = ctx.parse(qc["dispatch"]("align_iou", json.dumps({"ref_path": ref, "render_path": narrow})))
    ctx.check("align_iou narrower image lower", isinstance(narrow_res, dict) and float(narrow_res.get("iou", 1.0)) < 0.9, narrow_res)
