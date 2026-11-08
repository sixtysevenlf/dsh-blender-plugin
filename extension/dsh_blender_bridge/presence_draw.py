"""Viewport-only presence drawing for attached DSH agents.

The Gateway pushes a JSON snapshot of every attached caller (view pose, colour,
selection, activity). This module draws frusta and selection boxes with the GPU
draw handlers used by SpaceView3D; it never writes scene data, selection, the
camera, the frame, or the user's view.

Drawing is deliberately best-effort: a Blender build without the GPU/blf API
still accepts the snapshot and simply draws nothing.
"""

import json

import bpy
from mathutils import Matrix, Vector

_MAX_AGENTS = 8
_MAX_SELECTED = 32

_STATE = {"agents": [], "paused": False, "instanceId": None}
_HANDLES = []


def set_presence(payload):
    """Replace the snapshot and ensure the draw handlers exist."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    agents = payload.get("agents")
    _STATE["agents"] = list(agents)[:_MAX_AGENTS] if isinstance(agents, list) else []
    _STATE["paused"] = bool(payload.get("paused"))
    _STATE["instanceId"] = payload.get("instanceId")
    _ensure_handlers()
    _tag_redraw()


def clear_presence():
    _STATE["agents"] = []
    _STATE["paused"] = False
    _STATE["instanceId"] = None
    _remove_handlers()
    _tag_redraw()


def get_presence():
    return {"agents": list(_STATE["agents"]), "paused": _STATE["paused"]}


def _ensure_handlers():
    if _HANDLES:
        return
    try:
        _HANDLES.append(
            bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), "WINDOW", "POST_VIEW")
        )
        _HANDLES.append(
            bpy.types.SpaceView3D.draw_handler_add(_draw_text, (), "WINDOW", "POST_PIXEL")
        )
    except Exception:
        _HANDLES.clear()


def _remove_handlers():
    while _HANDLES:
        handle = _HANDLES.pop()
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass


def _tag_redraw():
    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return
    try:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:
        pass


def _color(value, fallback=(0.3, 0.55, 1.0)):
    text = str(value or "")
    if len(text) == 7 and text[0] == "#":
        try:
            return (
                int(text[1:3], 16) / 255.0,
                int(text[3:5], 16) / 255.0,
                int(text[5:7], 16) / 255.0,
                1.0,
            )
        except Exception:
            pass
    return fallback + (1.0,)


def _rotation(view):
    matrix = view.get("viewMatrix") if isinstance(view, dict) else None
    if isinstance(matrix, list) and len(matrix) == 16:
        try:
            return Matrix(matrix)
        except Exception:
            pass
    origin = view.get("from")
    target = view.get("lookAt")
    if not (isinstance(origin, list) and isinstance(target, list)):
        return None
    try:
        direction = Vector(target) - Vector(origin)
        if direction.length < 1e-6:
            return None
        return direction.to_track_quat("-Z", "Y").to_matrix().to_4x4()
    except Exception:
        return None


def _frustum(view, color):
    rot = _rotation(view)
    origin = view.get("from") if isinstance(view, dict) else None
    if rot is None or not isinstance(origin, list):
        return
    try:
        apex = Vector(origin)
        depth = 2.0
        lens = float(view.get("lens") or 50.0)
        half_h = depth * 18.0 / max(lens, 1.0)
        half_w = half_h
        if view.get("ortho"):
            half_w = half_h = max(float(view.get("orthoScale") or 10.0) * 0.25, 0.1)
        corners = [
            rot @ Vector((-half_w, -half_h, -depth)),
            rot @ Vector((half_w, -half_h, -depth)),
            rot @ Vector((half_w, half_h, -depth)),
            rot @ Vector((-half_w, half_h, -depth)),
        ]
        world = [apex + corner for corner in corners]
        lines = []
        for corner in world:
            lines.extend([_tuple(apex), _tuple(corner)])
        for index in range(4):
            lines.extend([_tuple(world[index]), _tuple(world[(index + 1) % 4])])
        _draw_lines(lines, color)
    except Exception:
        return


def _selection(uid, color):
    try:
        name = str(uid).split(":")[-2]
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH":
            return
        matrix = obj.matrix_world
        corners = [matrix @ Vector(corner) for corner in obj.bound_box]
        edges = [
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        ]
        lines = []
        for a, b in edges:
            lines.extend([_tuple(corners[a]), _tuple(corners[b])])
        _draw_lines(lines, color)
    except Exception:
        return


def _tuple(vector):
    return (float(vector.x), float(vector.y), float(vector.z))


def _draw_lines(vertices, color):
    if not vertices:
        return
    try:
        import gpu
        from gpu.types import GPUBatch, GPUVertBuf

        buffer = GPUVertBuf(len=len(vertices), format="3f")
        buffer.attr_fill(0, vertices)
        batch = GPUBatch(type="LINES", buf=buffer)
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
    except Exception:
        return


def _draw_view():
    if not _STATE["agents"]:
        return
    try:
        space = getattr(bpy.context, "space_data", None)
        if space is None or getattr(space, "type", "") != "VIEW_3D":
            return
        for row in _STATE["agents"]:
            view = row.get("view") if isinstance(row, dict) else None
            if not isinstance(view, dict):
                continue
            color = _color(row.get("color"))
            if view.get("viewpoint") != "viewport":
                _frustum(view, color)
            selected = row.get("selected") if isinstance(row.get("selected"), list) else []
            for uid in selected[:_MAX_SELECTED]:
                _selection(uid, color)
    except Exception:
        return


def _label(region, region_3d, location, text, color):
    if not (region and region_3d and text):
        return
    try:
        import blf

        if not hasattr(_label, "font"):
            path = bpy.utils.system_resource("DATAFILES", path="fonts/bfont.pfb")
            _label.font = blf.load(path)
        co = region_3d.perspective_matrix @ Vector(location)
        if co.w <= 0:
            return
        x = region.x + (co.x / co.w + 1.0) * 0.5 * region.width
        y = region.y + (co.y / co.w + 1.0) * 0.5 * region.height
        font = _label.font
        blf.position(font, x + 6.0, y + 6.0, 0.0)
        blf.size(font, 12)
        blf.color(font, color[0], color[1], color[2], color[3])
        blf.draw(font, text)
    except Exception:
        return


def _draw_text():
    if not _STATE["agents"]:
        return
    try:
        region = getattr(bpy.context, "region", None)
        region_3d = getattr(bpy.context, "region_data", None)
        for row in _STATE["agents"]:
            view = row.get("view") if isinstance(row, dict) else None
            if not isinstance(view, dict):
                continue
            origin = view.get("from")
            if not isinstance(origin, list):
                continue
            label = str(row.get("displayName") or "agent")
            if _STATE["paused"]:
                label += " (paused)"
            _label(region, region_3d, origin, label, _color(row.get("color")))
    except Exception:
        return
