/**
 * Render-pass Blender script: scene preparation and the compositor graph.
 *
 * First half of the fixed entry point written to `<job temp dir>/render_passes.py`;
 * `render-passes-py-capture.ts` carries the second half and `render-passes-script.ts`
 * concatenates the two. The split is a file-size boundary only: the Python text is one
 * program, so the seam must stay between two top-level `def` blocks.
 */
export const RENDER_PASSES_PY_SETUP = String.raw`"""Fixed internal Blender entry point for declarative render passes.

The Gateway writes this file and render-config.json into the job temp directory
and runs:

  blender -b --factory-startup <scene.blend> --python render_passes.py -- <render-config.json>

It never receives caller Python: every input is declarative JSON. For every
requested frame it renders the requested Blender passes through the compositor
(one File Output node per pass), then writes that frame's manifest.json last.

stdout contract (one line each, machine parsed by the queue executor):
  PASSES_DONE  {json}   success summary
  PASSES_ERROR {json}   fatal failure; the process exits non-zero
A frame whose pass file is missing is marked degraded in the manifest and in
PASSES_DONE instead of being reported as a clean success.
"""
import bpy
import hashlib
import json
import os
import shutil
import sys
import time
import traceback

PASS_ORDER = ("beauty", "depth", "normal", "object_index", "alpha")
MANIFEST_VERSION = 1
NORMAL_COORDINATE_SPACE = "camera"
# The depth PNG spends raw 0 on "no geometry", so the usable range starts one step in.
DEPTH_ZERO_STEP = 1.0 / 65535.0
DEPTH_DECODE_FORMULA = (
    "depth_m = near + ((pixel/65535 - 1/65535)/(1 - 1/65535))*(far-near); pixel 0 = no geometry"
)
PASS_FORMATS = ("PNG",)


def emit(kind, payload):
    line = kind + " " + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    print(line, flush=True)


def fail(code, message, **extra):
    payload = {"ok": False, "code": str(code), "error": str(message)}
    payload.update(extra)
    emit("PASSES_ERROR", payload)
    if sys.exc_info()[0] is not None:
        traceback.print_exc()
    sys.exit(1)


def warn(message):
    print("PASSES_WARN " + str(message), flush=True)


def blender_version():
    return str(getattr(bpy.app, "version_string", getattr(bpy.app, "version", "unknown")))


def config_path():
    argv = list(sys.argv)
    if "--" in argv:
        rest = argv[argv.index("--") + 1:]
        if rest:
            return rest[0]
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "render-config.json")


def load_config():
    path = config_path()
    if not os.path.isfile(path):
        fail("render_config_missing", "render-config.json was not found", configPath=path)
    with open(path, "r", encoding="utf-8") as handle:
        try:
            cfg = json.load(handle)
        except ValueError as exc:
            fail("render_config_invalid", "render-config.json is not valid JSON", configPath=path, reason=str(exc))
    if not isinstance(cfg, dict):
        fail("render_config_invalid", "render-config.json must be an object")
    passes = cfg.get("passes")
    if not isinstance(passes, list) or not passes:
        fail("render_config_invalid", "passes must be a non-empty list")
    seen = set()
    for name in passes:
        if not isinstance(name, str) or name not in PASS_ORDER:
            fail("render_config_invalid", "unknown render pass", passName=str(name))
        if name in seen:
            fail("render_config_invalid", "duplicate render pass", passName=name)
        seen.add(name)
    outdir = cfg.get("outdir")
    if not isinstance(outdir, str) or not outdir.strip():
        fail("render_config_invalid", "outdir is required")
    fmt = str(cfg.get("format") or "PNG").upper()
    if fmt not in PASS_FORMATS:
        # The pass manifest declares 8-bit PNG dtypes; render at PNG or drop passes.
        fail(
            "pass_unsupported",
            "the render-pass path writes 8-bit PNG only, not " + fmt,
            passName="beauty",
            format=fmt,
        )
    cfg["format"] = fmt
    return cfg


def assert_engine_supports_passes(scene, passes):
    """View-layer pass availability depends on the engine, before any socket probe."""
    engine = str(getattr(scene.render, "engine", ""))
    if "object_index" in passes and engine != "CYCLES":
        fail(
            "pass_unsupported",
            "object_index needs the Cycles engine; Blender only exposes Object Index under Cycles (current engine: "
            + engine
            + ")",
            passName="object_index",
            engine=engine,
            fix="switch scene.render.engine to CYCLES or drop object_index",
        )


def set_scene_params(scene, cfg):
    resolution = cfg.get("resolution")
    if isinstance(resolution, list) and len(resolution) == 2:
        try:
            scene.render.resolution_x = max(1, int(resolution[0]))
            scene.render.resolution_y = max(1, int(resolution[1]))
            scene.render.resolution_percentage = 100
        except (TypeError, ValueError) as exc:
            fail("render_config_invalid", "resolution is invalid", reason=str(exc))
    engine = cfg.get("engine")
    if engine:
        try:
            scene.render.engine = str(engine)
        except TypeError:
            fail("pass_unsupported", "the requested render engine is not available", engine=str(engine))
    samples = cfg.get("samples")
    if samples is not None:
        try:
            value = max(1, int(samples))
        except (TypeError, ValueError) as exc:
            fail("render_config_invalid", "samples is invalid", reason=str(exc))
        if scene.render.engine == "CYCLES":
            scene.cycles.samples = value
        else:
            eevee = getattr(scene, "eevee", None)
            for attr in ("taa_render_samples", "taa_samples"):
                if eevee is not None and hasattr(eevee, attr):
                    setattr(eevee, attr, value)
                    break


def resolve_camera(scene, cfg):
    wanted = cfg.get("camera")
    if wanted is None or wanted is True:
        camera = getattr(scene, "camera", None)
        if camera is None:
            fail("render_camera_missing", "the scene has no active camera")
        return camera
    name = str(wanted)
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "CAMERA":
        fail("render_camera_missing", "camera object not found", camera=name)
    scene.camera = obj
    return obj


def depth_range_of(camera):
    data = camera.data
    near = float(getattr(data, "clip_start", 0.1))
    far = float(getattr(data, "clip_end", 100.0))
    if not far > near > 0.0:
        fail("pass_unsupported", "the camera clip range cannot drive a depth pass", near=near, far=far)
    return near, far


def enable_view_layer_passes(passes):
    view_layer = bpy.context.view_layer
    wanted = set(passes)
    toggles = (
        ("use_pass_combined", "beauty" in wanted, "beauty"),
        ("use_pass_z", "depth" in wanted, "depth"),
        ("use_pass_normal", "normal" in wanted, "normal"),
        ("use_pass_object_index", "object_index" in wanted, "object_index"),
    )
    for attr, value, name in toggles:
        if not hasattr(view_layer, attr):
            if value:
                fail("pass_unsupported", "this Blender does not expose the requested view-layer pass", passName=name, blender=blender_version())
            continue
        setattr(view_layer, attr, value)


RENDERABLE_TYPES = (
    "MESH",
    "CURVE",
    "SURFACE",
    "META",
    "FONT",
    "VOLUME",
    "CURVES",
    "POINTCLOUD",
    "GREASEPENCIL",
)


def assign_object_indices(scene):
    objects = [obj for obj in scene.objects if obj.type in RENDERABLE_TYPES]
    objects.sort(key=lambda obj: (obj.name, obj.type))
    mapping = {}
    for index, obj in enumerate(objects, start=1):
        if index > 65535:
            fail("pass_unsupported", "too many objects for the 16-bit object_index pass")
        obj.pass_index = index
        mapping[str(index)] = {"name": obj.name, "type": obj.type}
    return mapping


def compositor_tree(scene):
    for attr in ("compositing_node_group", "node_tree"):
        holder = getattr(scene, attr, None)
        if holder is None:
            continue
        tree = getattr(holder, "node_tree", holder)
        if tree is not None:
            return tree
    try:
        scene.use_nodes = True
    except Exception:
        pass
    for attr in ("compositing_node_group", "node_tree"):
        holder = getattr(scene, attr, None)
        if holder is None:
            continue
        tree = getattr(holder, "node_tree", holder)
        if tree is not None:
            return tree
    try:
        group = bpy.data.node_groups.new("DSH Render Passes", "CompositorNodeTree")
        scene.compositing_node_group = group
        return group
    except Exception as exc:
        fail("pass_unsupported", "the compositor is not available", blender=blender_version(), reason=str(exc))


def output_slots_mode(node):
    """Blender 5.x moved File Output configuration onto directory/file_name/file_output_items."""
    return hasattr(node, "file_output_items") and not hasattr(node, "file_slots")


PASS_SOCKET_TYPES = {
    "beauty": "RGBA",
    "depth": "FLOAT",
    "normal": "VECTOR",
    "object_index": "FLOAT",
    "alpha": "FLOAT",
}


def mark_data_pass(fmt):
    """Keep pass numbers out of the view transform: manifests promise raw values."""
    try:
        fmt.color_management = "OVERRIDE"
        fmt.view_settings.view_transform = "Raw"
        fmt.view_settings.look = "None"
        fmt.view_settings.exposure = 0.0
        fmt.view_settings.gamma = 1.0
    except Exception:
        # Older builds may not expose every colour setting; the values are still linear.
        pass


def set_output_slot_path(node, slot_path, pass_name):
    slots = getattr(node, "file_slots", None)
    if slots is not None:
        try:
            while len(slots) > 1:
                slots.remove(slots[len(slots) - 1])
            slots[0].path = slot_path
            return
        except Exception:
            pass
    fail("pass_unsupported", "cannot address the File Output slot on this Blender", passName=pass_name, blender=blender_version())


def new_output_node(tree, name, fmt, color_depth, color_mode, pass_name):
    node = tree.nodes.new("CompositorNodeOutputFile")
    node.name = "dsh_pass_" + name
    node.label = name
    items_mode = output_slots_mode(node)
    target = None
    if items_mode:
        # 5.x refuses PNG unless the node is told it writes a still image, and each
        # output socket carries its own file/format through a file_output_items entry.
        try:
            node.format.media_type = "IMAGE"
        except Exception:
            pass
        try:
            node.file_name = name + "_"
            item = node.file_output_items.new(socket_type=PASS_SOCKET_TYPES.get(pass_name, "FLOAT"), name=name)
            item.override_node_format = True
            target = item.format
            try:
                target.media_type = "IMAGE"
            except Exception:
                pass
        except Exception as exc:
            fail("pass_unsupported", "cannot create the File Output socket on this Blender", passName=pass_name, blender=blender_version(), reason=str(exc))
    else:
        target = node.format
    try:
        target.file_format = fmt
        target.color_depth = color_depth
        if hasattr(target, "color_mode"):
            target.color_mode = color_mode
        if name != "beauty":
            mark_data_pass(target)
    except Exception as exc:
        fail("pass_unsupported", "cannot set the output format for a pass", passName=pass_name, reason=str(exc))
    if hasattr(node, "save_as_render"):
        # Non-beauty passes are data, not pictures: never run view transform on them.
        node.save_as_render = name == "beauty"
    if not items_mode:
        set_output_slot_path(node, name + "_", pass_name)
    return node


def first_image_input(node):
    inputs = getattr(node, "inputs", None)
    if inputs is None or len(inputs) == 0:
        return None
    return inputs[0]


def output_socket(node, names, pass_name):
    outputs = getattr(node, "outputs", None)
    for name in names:
        if outputs is None:
            break
        try:
            socket = outputs.get(name)
        except Exception:
            socket = None
        if socket is not None:
            return socket
    engine = str(getattr(getattr(bpy.context.scene, "render", None), "engine", ""))
    hint = ""
    if pass_name == "object_index":
        hint = " (object_index needs the Cycles engine; Blender only exposes Object Index under Cycles, current engine is " + engine + ")"
    fail(
        "pass_unsupported",
        "the requested pass output is not available in Blender " + blender_version() + hint,
        passName=pass_name,
        engine=engine,
    )


def new_map_range(tree, pass_name):
    for type_name in ("CompositorNodeMapRange", "ShaderNodeMapRange"):
        if hasattr(bpy.types, type_name):
            try:
                return tree.nodes.new(type_name)
            except Exception:
                continue
    fail("pass_unsupported", "this Blender exposes no Map Range node for the depth pass", passName=pass_name, blender=blender_version())


def new_vector_math(tree, pass_name):
    for type_name in ("CompositorNodeVectorMath", "ShaderNodeVectorMath"):
        if hasattr(bpy.types, type_name):
            try:
                return tree.nodes.new(type_name)
            except Exception:
                continue
    return None


def normal_remap_output(tree, source_socket, pass_name):
    """n * 0.5 + 0.5 as a colour image; falls back to MixRGB on builds without VectorMath."""
    node = new_vector_math(tree, pass_name)
    if node is not None:
        try:
            node.operation = "MULTIPLY_ADD"
            node.inputs[1].default_value = (0.5, 0.5, 0.5)
            node.inputs[2].default_value = (0.5, 0.5, 0.5)
            tree.links.new(source_socket, node.inputs[0])
            return node.outputs[0]
        except Exception:
            pass
    try:
        multiply = tree.nodes.new("CompositorNodeMixRGB")
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        multiply.inputs[2].default_value = (0.5, 0.5, 0.5, 1.0)
        tree.links.new(source_socket, multiply.inputs[1])
        offset = tree.nodes.new("CompositorNodeMixRGB")
        offset.blend_type = "ADD"
        offset.inputs[0].default_value = 1.0
        offset.inputs[2].default_value = (0.5, 0.5, 0.5, 1.0)
        tree.links.new(multiply.outputs[0], offset.inputs[1])
        return offset.outputs[0]
    except Exception as exc:
        fail("pass_unsupported", "this Blender cannot encode the normal pass", passName=pass_name, blender=blender_version(), reason=str(exc))


def build_compositor(scene, cfg, passes, near, far):
    tree = compositor_tree(scene)
    for node in list(getattr(tree, "nodes", [])):
        tree.nodes.remove(node)
    render_layers = tree.nodes.new("CompositorNodeRLayers")
    try:
        render_layers.scene = scene
    except Exception:
        pass
    layer_name = getattr(bpy.context.view_layer, "name", "")
    if layer_name:
        try:
            render_layers.layer = layer_name
        except Exception:
            pass
    beauty_format = str(cfg.get("format") or "PNG").upper()
    nodes = {}
    for name in passes:
        if name == "beauty":
            node = new_output_node(tree, "beauty", beauty_format, "8", "RGB", name)
            tree.links.new(output_socket(render_layers, ("Image", "Combined"), name), first_image_input(node))
        elif name == "depth":
            node = new_output_node(tree, "depth", "PNG", "16", "BW", name)
            remap = new_map_range(tree, name)
            remap.inputs["From Min"].default_value = near
            remap.inputs["From Max"].default_value = far
            # Raw 0 is reserved for "no geometry": map the whole range into
            # [1/65535, 1] so the decode formula is exact and 0 stays a sentinel.
            remap.inputs["To Min"].default_value = DEPTH_ZERO_STEP
            remap.inputs["To Max"].default_value = 1.0
            if hasattr(remap, "clamp"):
                remap.clamp = False
            tree.links.new(output_socket(render_layers, ("Depth", "Z"), name), remap.inputs[0])
            tree.links.new(remap.outputs[0], first_image_input(node))
        elif name == "normal":
            node = new_output_node(tree, "normal", "PNG", "8", "RGB", name)
            source = output_socket(render_layers, ("Normal",), name)
            tree.links.new(normal_remap_output(tree, source, name), first_image_input(node))
        elif name == "object_index":
            node = new_output_node(tree, "object_index", "PNG", "16", "BW", name)
            # IndexOB carries the raw 1..N pass_index; a PNG can only hold [0,1], so the
            # pass is normalized by 65535 and the manifest decodes it back exactly.
            scale = new_map_range(tree, name)
            scale.inputs["From Min"].default_value = 0.0
            scale.inputs["From Max"].default_value = 65535.0
            scale.inputs["To Min"].default_value = 0.0
            scale.inputs["To Max"].default_value = 1.0
            if hasattr(scale, "clamp"):
                scale.clamp = True
            tree.links.new(output_socket(render_layers, ("IndexOB", "Object Index"), name), scale.inputs[0])
            tree.links.new(scale.outputs[0], first_image_input(node))
        else:  # alpha
            node = new_output_node(tree, "alpha", "PNG", "8", "BW", name)
            tree.links.new(output_socket(render_layers, ("Alpha",), name), first_image_input(node))
        nodes[name] = node
    return nodes


def set_outputs_base_path(nodes, frame_dir):
    for node in nodes.values():
        try:
            if output_slots_mode(node):
                node.directory = frame_dir
            else:
                node.base_path = frame_dir
        except Exception as exc:
            fail("pass_unsupported", "cannot set the File Output base path", reason=str(exc))


`
