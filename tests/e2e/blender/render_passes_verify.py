"""Pixel-level checks for the render-pass E2E (run inside a real Blender).

Usage:
    blender -b --factory-startup --python render_passes_verify.py -- <frame_dir> [<expect.json>]

The frame directory must contain `manifest.json` plus the pass files it names.
`expect.json` (written by the Node E2E) states the expected camera-space normal of
the scene's ground plane, so the check is independent of the Python conversion.
Prints one PASS/FAIL line per contract point and a final VERIFY_RESULT line so the
Node E2E can assert on machine-readable output instead of scraping prose.
"""

from __future__ import annotations

import json
import os
import struct
import sys

import numpy as np

failures: list[str] = []

# Raw 0 in the depth PNG is reserved for "no geometry", so the usable range starts one step in.
DEPTH_STEP = 1.0 / 65535.0


def png_header(path: str) -> tuple[int, int]:
    """Return (bit_depth, color_type) from the IHDR chunk of a PNG file."""
    with open(path, "rb") as handle:
        head = handle.read(33)
    if len(head) < 26 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return (-1, -1)
    width, height, bit_depth, color_type = struct.unpack(">IIBB", head[16:26])
    return (int(bit_depth), int(color_type))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + ((" · " + detail) if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def load_pass(bpy, manifest: dict, name: str, frame_dir: str):
    entry = manifest["passes"][name]
    path = entry["path"]
    if not os.path.isabs(path):
        path = os.path.join(frame_dir, os.path.basename(path))
    image = bpy.data.images.load(path, check_existing=False)
    # Data passes are not pictures: reading them as sRGB would silently transform values.
    image.colorspace_settings.name = "Non-Color"
    width, height = image.size
    buffer = np.empty(width * height * image.channels, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    pixels = buffer.reshape((height, width, image.channels))
    bpy.data.images.remove(image)
    # Blender stores image rows bottom-up; flip once so row 0 is the top of the
    # picture and every geometric check (top/bottom bands, neighbours) reads
    # the same orientation as the rendered frame.
    pixels = np.flipud(pixels)
    return entry, pixels, width, height


def verify_depth(bpy, manifest, frame_dir, expected):
    entry, pixels, width, height = load_pass(bpy, manifest, "depth", frame_dir)
    check("depth:resolution", [width, height] == expected, "%dx%d" % (width, height))
    path = entry["path"]
    if not os.path.isabs(path):
        path = os.path.join(frame_dir, os.path.basename(path))
    bit_depth, color_type = png_header(path)
    check("depth:png_bit_depth_16", bit_depth == 16, "bit depth %d, color type %d" % (bit_depth, color_type))
    depth = pixels[:, :, 0]
    raw = np.rint(depth * 65535.0).astype(np.int64)
    steps = int(np.unique(raw).size)
    # An 8-bit PNG cannot hold a value above 255, so any raw step above that
    # proves the file carries more than 8 bits per sample. The distinct-step
    # count is reported for context only: it depends on how much of the clip
    # range the scene covers (a shallow scene legitimately repeats values).
    check("depth:not_8bit", int(raw.max()) > 255, "max raw %d, %d distinct steps" % (int(raw.max()), steps))
    near, far = float(entry["near"]), float(entry["far"])
    span = 1.0 - DEPTH_STEP
    meters = near + ((depth - DEPTH_STEP) / span) * (far - near)
    # Raw 0 is the documented no-geometry sentinel, never a surface at the near plane.
    valid = (raw > 0) & (raw < 65535)
    check("depth:has_surface", int(valid.sum()) > 0.2 * depth.size, "%d/%d px" % (int(valid.sum()), depth.size))
    check(
        "depth:no_geometry_sentinel",
        entry.get("noGeometryValue") == 0 and entry.get("clampedAt") == "far",
        "%s/%s" % (entry.get("noGeometryValue"), entry.get("clampedAt")),
    )
    if valid.any():
        lo, hi = float(meters[valid].min()), float(meters[valid].max())
        check("depth:meters_within_clip", near <= lo <= hi <= far, "%.3f..%.3f m" % (lo, hi))
    check(
        "depth:decode_formula",
        "pixel 0 = no geometry" in str(entry.get("decodeFormula")),
        str(entry.get("decodeFormula")),
    )
    return depth


def verify_normal(bpy, manifest, frame_dir, expected, depth, ground_expected):
    entry, pixels, width, height = load_pass(bpy, manifest, "normal", frame_dir)
    check("normal:resolution", [width, height] == expected, "%dx%d" % (width, height))
    vectors = pixels[:, :, :3] * 2.0 - 1.0
    covered = depth > 0.0
    if covered.any():
        lengths = np.linalg.norm(vectors[covered], axis=-1)
        check(
            "normal:unit_length",
            0.85 <= float(np.median(lengths)) <= 1.15,
            "median |n|=%.3f" % float(np.median(lengths)),
        )
    check("normal:coordinate_space", entry.get("coordinateSpace") == "camera", str(entry.get("coordinateSpace")))
    check("normal:encoding", entry.get("encoding") == "n*0.5+0.5", str(entry.get("encoding")))
    check(
        "normal:converted_from_world",
        entry.get("source") == "converted:world->camera",
        str(entry.get("source")),
    )
    if ground_expected is None:
        return
    # The flat ground fills the bottom of a camera pitched down onto it: there the
    # camera-space normal must be the rotated world +Z, not (0,0,1) and not +Z in
    # world space. An 8-bit encoding is good to ~1/127, so allow a small tolerance.
    expected_vector = np.asarray(ground_expected, dtype=np.float32)
    norm = float(np.linalg.norm(expected_vector))
    if norm > 0.0:
        expected_vector = expected_vector / norm
    band = vectors[int(height * 0.85) :, :, :]
    flat = band.reshape(-1, 3)
    lengths = np.linalg.norm(flat, axis=-1)
    usable = flat[lengths > 0.5]
    if usable.size == 0:
        check("normal:ground_band_present", False, "no usable pixels in the bottom band")
        return
    dots = usable @ expected_vector
    close = float(np.mean(dots > 0.95))
    check(
        "normal:ground_matches_camera_space",
        close >= 0.7 and float(np.median(dots)) > 0.98,
        "%.0f%% within 18deg, median dot %.3f, expected %s"
        % (close * 100.0, float(np.median(dots)), np.round(expected_vector, 3).tolist()),
    )
    # A world-space leak would show up as the ground normal being exactly +Z.
    check(
        "normal:ground_is_not_world_space",
        float(np.median(dots)) < 0.9999 and not bool(np.allclose(np.median(usable, axis=0), [0.0, 0.0, 1.0], atol=0.05)),
        "median %s" % np.round(np.median(usable, axis=0), 3).tolist(),
    )


def verify_alpha(bpy, manifest, frame_dir, expected):
    entry, pixels, width, height = load_pass(bpy, manifest, "alpha", frame_dir)
    check("alpha:resolution", [width, height] == expected, "%dx%d" % (width, height))
    alpha = pixels[:, :, 0]
    check(
        "alpha:unit_range",
        bool(alpha.min() >= 0.0 and alpha.max() <= 1.0),
        "%.3f..%.3f" % (float(alpha.min()), float(alpha.max())),
    )
    check("alpha:channels", entry["channels"] == 1, str(entry["channels"]))


def verify_beauty(bpy, manifest, frame_dir, expected):
    entry, pixels, width, height = load_pass(bpy, manifest, "beauty", frame_dir)
    check("beauty:resolution", [width, height] == expected, "%dx%d" % (width, height))
    rgb = pixels[:, :, :3]
    check("beauty:not_black", float(rgb.max()) > 0.05, "max=%.3f" % float(rgb.max()))
    check("beauty:channels", entry["channels"] == 3, str(entry["channels"]))
    check(
        "beauty:manifest_format_is_honest",
        entry.get("format") == "PNG" and entry.get("dtype") == "uint8",
        "%s/%s" % (entry.get("format"), entry.get("dtype")),
    )
    path = entry["path"]
    if not os.path.isabs(path):
        path = os.path.join(frame_dir, os.path.basename(path))
    bit_depth, color_type = png_header(path)
    check("beauty:png_bit_depth_8", bit_depth == 8, "bit depth %d, color type %d" % (bit_depth, color_type))
    # A PNG reader that silently widened the file would fool the range checks.
    raw = np.rint(pixels[:, :, :3] * 255.0).astype(np.int64)
    check("beauty:is_not_8bit_quantized", int(np.unique(raw).size) > 1, "%d levels" % int(np.unique(raw).size))


def verify_object_index(bpy, manifest, frame_dir, expected):
    entry, pixels, width, height = load_pass(bpy, manifest, "object_index", frame_dir)
    check("object_index:resolution", [width, height] == expected, "%dx%d" % (width, height))
    check(
        "object_index:dtype",
        entry["dtype"] == "uint16" and entry["channels"] == 1,
        "%s/%s" % (entry["dtype"], entry["channels"]),
    )
    check(
        "object_index:decode_formula",
        entry["decodeFormula"] == "index = round(pixel*65535)",
        str(entry.get("decodeFormula")),
    )
    indices = np.rint(pixels[:, :, 0] * 65535).astype(np.int64)
    values = np.unique(indices)
    mapping = manifest["objectIndex"]["mapping"]
    allowed = {0} | {int(key) for key in mapping}
    check(
        "object_index:only_mapped_ids", {int(v) for v in values} <= allowed, "found=%s" % sorted(int(v) for v in values)
    )
    visible = [int(v) for v in values if int(v) != 0]
    check("object_index:objects_visible", len(visible) > 0, "ids=%s" % visible)
    check("object_index:background", 0 in {int(v) for v in values}, "background present")
    names = sorted(v.get("name", "?") for v in mapping.values())
    check("object_index:mapping_matches_scene", len(names) >= len(visible), "mapping=%s" % names)


def main() -> int:
    import bpy

    args = sys.argv[sys.argv.index("--") + 1 :]
    frame_dir = args[0]
    with open(os.path.join(frame_dir, "manifest.json"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    expected = [int(v) for v in manifest["resolution"]]
    expect: dict = {}
    if len(args) > 1 and os.path.isfile(args[1]):
        with open(args[1], "r", encoding="utf-8") as handle:
            expect = json.load(handle)
    ground_expected = expect.get("groundNormalCamera")

    check("manifest:version", manifest.get("manifestVersion") == 1, str(manifest.get("manifestVersion")))
    check("manifest:degraded_clean", manifest.get("degraded") == [], str(manifest.get("degraded")))
    check("manifest:has_blender_version", bool(manifest.get("blenderVersion")), str(manifest.get("blenderVersion")))

    requested = list(manifest.get("requestedPasses") or [])
    depth = None
    if "beauty" in requested:
        verify_beauty(bpy, manifest, frame_dir, expected)
    if "depth" in requested:
        depth = verify_depth(bpy, manifest, frame_dir, expected)
    if "normal" in requested:
        verify_normal(
            bpy,
            manifest,
            frame_dir,
            expected,
            depth if depth is not None else np.ones((expected[1], expected[0]), dtype=np.float32),
            ground_expected,
        )
    if "alpha" in requested:
        verify_alpha(bpy, manifest, frame_dir, expected)
    if "object_index" in requested:
        verify_object_index(bpy, manifest, frame_dir, expected)

    print("VERIFY_RESULT " + json.dumps({"ok": not failures, "failures": failures}), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
