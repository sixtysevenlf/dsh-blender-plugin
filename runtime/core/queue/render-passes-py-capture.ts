/**
 * Render-pass Blender script: per-frame capture, manifest assembly and the entry point.
 *
 * Second half of the fixed entry point; see `render-passes-py-setup.ts` for the seam rule.
 */
export const RENDER_PASSES_PY_CAPTURE = String.raw`def find_pass_file(frame_dir, name):
    prefix = name + "_"
    candidates = []
    try:
        entries = os.listdir(frame_dir)
    except OSError:
        return None
    for entry in entries:
        if entry.startswith(prefix) and os.path.isfile(os.path.join(frame_dir, entry)):
            candidates.append(os.path.join(frame_dir, entry))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1]


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert_normal_to_camera_space(scene, camera, path):
    """Blender's Normal pass is world space (Cycles and EEVEE); the manifest promises camera space.

    Every engine goes through the same numpy conversion, so a Cycles frame and an
    EEVEE frame of one scene describe the same vector in the same space. numpy is
    part of the bundled Blender install; without it the pass cannot be honest and
    the render fails instead of writing a mislabelled image.
    """
    engine = str(getattr(scene.render, "engine", ""))
    try:
        import numpy as np
    except ImportError:
        fail(
            "pass_unsupported",
            "the normal pass needs numpy to convert world-space normals to camera space",
            passName="normal",
            engine=engine,
        )
    image = bpy.data.images.load(path, check_existing=False)
    temporary = None
    try:
        image.colorspace_settings.name = "Non-Color"
        width, height = image.size
        buffer = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        pixels = buffer.reshape((height, width, 4))
        world = pixels[:, :, :3] * 2.0 - 1.0
        rotation = np.array(camera.matrix_world.to_3x3(), dtype=np.float32)
        camera_space = world @ rotation
        length = np.linalg.norm(camera_space, axis=-1, keepdims=True)
        length[length < 1e-6] = 1.0
        camera_space = camera_space / length
        pixels[:, :, :3] = np.clip(camera_space * 0.5 + 0.5, 0.0, 1.0)
        image.pixels.foreach_set(pixels.reshape(-1))
        temporary = path + ".tmp.png"
        image.filepath_raw = temporary
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)
    if temporary is None:
        fail("render_passes_failed", "the normal pass could not be converted", passName="normal")
    os.replace(temporary, path)
    return "converted:world->camera"


def pass_metadata(name, path, near, far, scene, normal_source):
    size = os.path.getsize(path)
    common = {"path": path.replace(os.sep, "/"), "bytes": size, "sha256": sha256_of(path)}
    if name == "beauty":
        # The pass entry renders 8-bit PNG only (rejected in config otherwise), so
        # these fields describe what is actually on disk.
        common.update(
            {
                "format": "PNG",
                "dtype": "uint8",
                "channels": 3,
                "range": [0, 1],
                "colorspace": "sRGB",
                "encoding": "image",
            }
        )
    elif name == "depth":
        common.update(
            {
                "dtype": "uint16",
                "channels": 1,
                "range": [0, 1],
                "colorspace": "linear",
                "encoding": "normalized_depth",
                "surfaces": "camera",
                "near": near,
                "far": far,
                "noGeometryValue": 0,
                "clampedAt": "far",
                "decodeFormula": DEPTH_DECODE_FORMULA,
            }
        )
    elif name == "normal":
        common.update(
            {
                "dtype": "uint8",
                "channels": 3,
                "range": [0, 1],
                "colorspace": "linear",
                "encoding": "n*0.5+0.5",
                "coordinateSpace": NORMAL_COORDINATE_SPACE,
                "decodeFormula": "n = pixel/255*2-1",
                "source": normal_source,
            }
        )
    elif name == "object_index":
        common.update(
            {
                "dtype": "uint16",
                "channels": 1,
                "range": [0, 1],
                "colorspace": "non-color",
                "encoding": "index/65535",
                "decodeFormula": "index = round(pixel*65535)",
            }
        )
    else:
        common.update({"dtype": "uint8", "channels": 1, "range": [0, 1], "colorspace": "linear", "encoding": "alpha"})
    return common


def write_json_atomic(path, payload):
    temporary = path + ".tmp-" + str(os.getpid())
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def reset_frame_dir(frame_dir):
    """Clear a frame directory before rendering into it.

    The outdir can be a user directory that already holds pass files from an older
    render; a leftover depth_0001.png would otherwise be reported as this frame.
    """
    if not os.path.isdir(frame_dir):
        return
    for entry in os.listdir(frame_dir):
        target = os.path.join(frame_dir, entry)
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        except OSError:
            fail("render_passes_failed", "cannot clear the frame directory", path=target)


def collect_frame(scene, camera, frame, frame_dir, passes, near, far, mapping):
    entries = {}
    degraded = []
    normal_source = "not-requested"
    if "normal" in passes:
        normal_path = find_pass_file(frame_dir, "normal")
        if normal_path is not None:
            normal_source = convert_normal_to_camera_space(scene, camera, normal_path)
    for name in passes:
        path = find_pass_file(frame_dir, name)
        if path is None:
            degraded.append(name)
            continue
        entries[name] = pass_metadata(name, path, near, far, scene, normal_source)
    resolution = [
        int(scene.render.resolution_x * scene.render.resolution_percentage / 100.0),
        int(scene.render.resolution_y * scene.render.resolution_percentage / 100.0),
    ]
    manifest = {
        "manifestVersion": MANIFEST_VERSION,
        "frame": frame,
        "camera": camera.name,
        "resolution": resolution,
        "samples": scene.cycles.samples if scene.render.engine == "CYCLES" and hasattr(scene, "cycles") else None,
        "engine": str(scene.render.engine),
        "blenderVersion": blender_version(),
        "passes": entries,
        "requestedPasses": list(passes),
        "format": "PNG",
        "depth": {
            "near": near,
            "far": far,
            "units": "m",
            "noGeometryValue": 0,
            "clampedAt": "far",
            "formula": DEPTH_DECODE_FORMULA,
        },
        "normal": {
            "coordinateSpace": NORMAL_COORDINATE_SPACE,
            "encoding": "n*0.5+0.5",
            "decodeFormula": "n = pixel/255*2-1",
            "source": normal_source,
        },
        "objectIndex": {"background": 0, "mapping": mapping},
        "filmTransparent": bool(getattr(scene.render, "film_transparent", False)),
        "degraded": degraded,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if "object_index" not in passes:
        manifest["objectIndex"] = {"background": 0, "mapping": {}}
    manifest_path = os.path.join(frame_dir, "manifest.json")
    # The manifest is the frame's commit record: write it after every pass file.
    write_json_atomic(manifest_path, manifest)
    total = sum(entry["bytes"] for entry in entries.values()) + os.path.getsize(manifest_path)
    return {
        "frame": frame,
        "manifest": manifest_path.replace(os.sep, "/"),
        "passes": entries,
        "bytes": total,
        "degraded": degraded,
        "requestedPasses": list(passes),
    }


def frame_range(scene, cfg):
    frames = cfg.get("frames")
    if frames is None:
        return int(scene.frame_current), int(scene.frame_current)
    if isinstance(frames, list) and len(frames) >= 2:
        try:
            start = int(frames[0])
            end = int(frames[1])
        except (TypeError, ValueError):
            fail("render_config_invalid", "frames is invalid", frames=frames)
    elif isinstance(frames, dict):
        try:
            start = int(frames.get("start"))
            end = int(frames.get("end", start))
        except (TypeError, ValueError):
            fail("render_config_invalid", "frames is invalid", frames=frames)
    elif isinstance(frames, (int, float)):
        start = int(frames)
        end = start
    else:
        fail("render_config_invalid", "frames is invalid", frames=frames)
    if end < start:
        fail("render_config_invalid", "the frame range ends before it starts", start=start, end=end)
    return start, end


def main():
    cfg = load_config()
    passes = list(cfg["passes"])
    scene = bpy.context.scene
    set_scene_params(scene, cfg)
    # Engine gate first: a socket probe without Cycles looks like a missing pass.
    assert_engine_supports_passes(scene, passes)
    camera = resolve_camera(scene, cfg)
    near, far = depth_range_of(camera)
    enable_view_layer_passes(passes)
    mapping = assign_object_indices(scene) if "object_index" in passes else {}
    nodes = build_compositor(scene, cfg, passes, near, far)
    start, end = frame_range(scene, cfg)
    outdir = cfg["outdir"]
    os.makedirs(outdir, exist_ok=True)
    summaries = []
    for frame in range(start, end + 1):
        frame_dir = os.path.join(outdir, "passes", "frame_%04d" % frame)
        # A stale file from an earlier render must never be reported as this frame.
        reset_frame_dir(frame_dir)
        os.makedirs(frame_dir, exist_ok=True)
        set_outputs_base_path(nodes, frame_dir)
        scene.frame_set(frame)
        bpy.ops.render.render(write_still=False)
        summaries.append(collect_frame(scene, camera, frame, frame_dir, passes, near, far, mapping))
    degraded_frames = [entry["frame"] for entry in summaries if entry["degraded"]]
    summary = {
        "ok": True,
        "manifestVersion": MANIFEST_VERSION,
        "passes": passes,
        "frames": summaries,
        "degradedFrames": degraded_frames,
        "outdir": outdir.replace(os.sep, "/"),
    }
    emit("PASSES_DONE", summary)
    if degraded_frames and len(degraded_frames) == len(summaries):
        # Every requested frame lost at least one pass: nothing usable was produced.
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # one machine-readable line beats a raw traceback only
        fail("render_passes_failed", str(exc), blender=blender_version())`
