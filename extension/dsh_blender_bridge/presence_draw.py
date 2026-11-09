"""Viewport-only presence drawing for attached DSH agents.

The Gateway pushes a JSON snapshot of every attached caller (view pose, colour,
selection, activity, stable id, notice). This module draws frusta and selection
boxes with the GPU draw handlers used by SpaceView3D; it never writes scene
data, selection, the camera, the frame, or the user's view.

Drawing is deliberately best-effort: a Blender build without the GPU/blf API
still accepts the snapshot and simply draws nothing.
"""

import json
import os

import bpy
from mathutils import Matrix, Vector

_MAX_AGENTS = 8
_MAX_SELECTED = 32
_DEFAULT_COLOR = (0.30, 0.55, 1.0, 1.0)

_STATE = {"agents": [], "paused": False, "instanceId": None}
_HANDLES = []
_HIDDEN = set()
_LAST_NOTICE_SEQ = 0
_NOTICE_TIMER = [None]
# 绘制错误只提示一次，避免每帧刷屏，同时不让 GPU API 变更再次无声失败。
_DRAW_ERROR_REPORTED = [False]
_FONT = [None]


def set_presence(payload):
    """Replace the snapshot and ensure the draw handlers exist."""
    global _LAST_NOTICE_SEQ
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
    live = {str(row.get("id")) for row in _STATE["agents"] if isinstance(row, dict)}
    _HIDDEN.intersection_update(live)
    notice = payload.get("notice")
    if isinstance(notice, dict):
        try:
            seq = int(notice.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq > _LAST_NOTICE_SEQ:
            _LAST_NOTICE_SEQ = seq
            _show_notice(str(notice.get("text") or ""))
    _ensure_handlers()
    _tag_redraw()


def clear_presence():
    _STATE["agents"] = []
    _STATE["paused"] = False
    _STATE["instanceId"] = None
    _HIDDEN.clear()
    _remove_handlers()
    _tag_redraw()


def get_presence():
    return {"agents": list(_STATE["agents"]), "paused": _STATE["paused"]}


def presence_rows():
    """Rows for the Blender sidebar; never mutates the pushed snapshot."""
    rows = []
    for row in _STATE["agents"]:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "id": str(row.get("id") or ""),
                "shortId": str(row.get("shortId") or ""),
                "displayName": str(row.get("displayName") or "agent"),
                "color": str(row.get("color") or ""),
                "lastActivity": str(row.get("lastActivity") or ""),
                "lastActivityAt": float(row.get("lastActivityAt") or 0),
                "activity": str(row.get("activity") or ""),
                "hidden": _is_hidden(row),
            }
        )
    return rows


def toggle_agent(agent_id):
    value = str(agent_id or "")
    if not value:
        return False
    if value in _HIDDEN:
        _HIDDEN.discard(value)
        hidden = False
    else:
        _HIDDEN.add(value)
        hidden = True
    _tag_redraw()
    return hidden


def set_agent_hidden(agent_id, hidden):
    value = str(agent_id or "")
    if not value:
        return
    if hidden:
        _HIDDEN.add(value)
    else:
        _HIDDEN.discard(value)
    _tag_redraw()


def _is_hidden(row):
    agent_id = str(row.get("id") or "")
    return bool(agent_id) and agent_id in _HIDDEN


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


def _prefs():
    try:
        addon = bpy.context.preferences.addons.get(__package__)
        return addon.preferences if addon is not None else None
    except Exception:
        return None


def _pref_color():
    prefs = _prefs()
    value = getattr(prefs, "presence_color", None) if prefs is not None else None
    if value is not None:
        try:
            vals = [float(v) for v in value]
            if len(vals) >= 3:
                return (vals[0], vals[1], vals[2], 1.0)
        except Exception:
            pass
        hex_value = _color(value, None)
        if hex_value is not None:
            return hex_value
    return _DEFAULT_COLOR


def _pref_color_by_agent():
    prefs = _prefs()
    return bool(getattr(prefs, "presence_color_by_agent", False)) if prefs is not None else False


def _pref_show_ids():
    prefs = _prefs()
    return bool(getattr(prefs, "presence_show_ids", True)) if prefs is not None else True


def _color(value, fallback=_DEFAULT_COLOR):
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
    return fallback


def _agent_color(row):
    if _pref_color_by_agent():
        return _color(row.get("color"), _pref_color())
    return _pref_color()


def _short_label(row):
    name = str(row.get("displayName") or "agent")
    short = str(row.get("shortId") or "")
    if _pref_show_ids() and short:
        return short + " \u00b7 " + name
    return name


def _show_notice(text):
    if not text:
        return
    try:
        workspace = getattr(bpy.context, "workspace", None)
        if workspace is None:
            return
        workspace.status_text_set(text[:160])
        try:
            if _NOTICE_TIMER[0] is not None and bpy.app.timers.is_registered(_NOTICE_TIMER[0]):
                bpy.app.timers.unregister(_NOTICE_TIMER[0])
        except Exception:
            pass

        def _clear():
            _NOTICE_TIMER[0] = None
            try:
                bpy.context.workspace.status_text_set(None)
            except Exception:
                pass
            return None

        _NOTICE_TIMER[0] = bpy.app.timers.register(_clear, first_interval=2.0)
    except Exception:
        pass


def _vec3(value):
    """Normalize a 3-component vector: [x, y, z], (x, y, z) or "x,y,z"."""
    if isinstance(value, str):
        parts = value.replace(" ", "").split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return None
    if len(parts) != 3:
        return None
    try:
        return [float(part) for part in parts]
    except (TypeError, ValueError):
        return None


def _matrix4(value):
    """4x4 rows or 16 flat floats -> Matrix; any other shape gives None."""
    if not isinstance(value, (list, tuple)):
        return None
    try:
        if len(value) == 16 and all(isinstance(item, (int, float)) for item in value):
            return Matrix([list(value[index * 4:index * 4 + 4]) for index in range(4)])
        if len(value) == 4 and all(
            isinstance(row, (list, tuple)) and len(row) == 4 for row in value
        ):
            return Matrix([list(row) for row in value])
    except Exception:
        return None
    return None


def _camera_world(view):
    """Camera->world matrix for one presence pose.

    viewMatrix wins when present: the Gateway publishes it as Blender's
    matrix_world.inverted() (world->view), so inverting gives the exact pose
    including roll, while from/lookAt can only reconstruct a roll-free track
    orientation. from/lookAt remain the fallback for poses without a matrix.
    A 4x4 matrix is accepted as nested rows or as 16 flat floats.
    """
    matrix = _matrix4(view.get("viewMatrix"))
    if matrix is not None:
        try:
            return matrix.inverted()
        except Exception:
            pass
    origin = _vec3(view.get("from"))
    target = _vec3(view.get("lookAt"))
    if origin is not None and target is not None:
        try:
            direction = Vector(target) - Vector(origin)
            if direction.length >= 1e-6:
                matrix = direction.to_track_quat("-Z", "Y").to_matrix().to_4x4()
                matrix.translation = Vector(origin)
                return matrix
        except Exception:
            pass
    return None


def _rotation(world):
    if world is None:
        return None
    try:
        return world.to_3x3().to_4x4()
    except Exception:
        return None


def _aspect(view):
    try:
        width = float(view.get("width") or 0)
        height = float(view.get("height") or 0)
        if width > 0 and height > 0:
            return width / height
    except (TypeError, ValueError):
        pass
    return 16.0 / 9.0


def _ndc_corner(proj_inv, x, y, z):
    try:
        point = proj_inv @ Vector((x, y, z, 1.0))
        if abs(point.w) < 1e-9:
            return None
        return point / point.w
    except Exception:
        return None


def _projected_frustum(view, world):
    """World-space frustum lines from the capture projection, or None."""
    projection = _matrix4(view.get("projection"))
    if projection is None:
        return None
    try:
        proj_inv = projection.inverted()
    except Exception:
        return None
    apex = world.translation.copy()
    rot = _rotation(world)
    if rot is None:
        return None
    try:
        if bool(view.get("ortho")):
            near = []
            far = []
            for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                a = _ndc_corner(proj_inv, x, y, -1.0)
                b = _ndc_corner(proj_inv, x, y, 1.0)
                if a is None or b is None:
                    return None
                near.append(apex + rot @ Vector((a.x, a.y, a.z)))
                far.append(apex + rot @ Vector((b.x, b.y, b.z)))
            lines = []
            for i in range(4):
                lines.extend([_tuple(near[i]), _tuple(near[(i + 1) % 4])])
                lines.extend([_tuple(far[i]), _tuple(far[(i + 1) % 4])])
                lines.extend([_tuple(near[i]), _tuple(far[i])])
            return lines
        corners = []
        for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            point = _ndc_corner(proj_inv, x, y, 0.0)
            if point is None:
                return None
            depth = max(-point.z, 1e-6)
            scaled = point * (2.0 / depth)
            corners.append(apex + rot @ Vector((scaled.x, scaled.y, scaled.z)))
        lines = []
        for corner in corners:
            lines.extend([_tuple(apex), _tuple(corner)])
        for i in range(4):
            lines.extend([_tuple(corners[i]), _tuple(corners[(i + 1) % 4])])
        return lines
    except Exception:
        return None


def _fallback_frustum(view, world):
    rot = _rotation(world)
    if rot is None:
        return None
    try:
        apex = world.translation.copy()
        depth = 2.0
        lens = float(view.get("lens") or 50.0)
        half_h = depth * 18.0 / max(lens, 1.0)
        half_w = half_h * _aspect(view)
        if view.get("ortho"):
            half_h = max(float(view.get("orthoScale") or 10.0) * 0.25, 0.1)
            half_w = half_h * _aspect(view)
        corners = [
            rot @ Vector((-half_w, -half_h, -depth)),
            rot @ Vector((half_w, -half_h, -depth)),
            rot @ Vector((half_w, half_h, -depth)),
            rot @ Vector((-half_w, half_h, -depth)),
        ]
        world_corners = [apex + corner for corner in corners]
        lines = []
        for corner in world_corners:
            lines.extend([_tuple(apex), _tuple(corner)])
        for index in range(4):
            lines.extend([_tuple(world_corners[index]), _tuple(world_corners[(index + 1) % 4])])
        return lines
    except Exception:
        return None


def _frustum(view, color):
    world = _camera_world(view)
    if world is None:
        return
    lines = _projected_frustum(view, world)
    if lines is None:
        lines = _fallback_frustum(view, world)
    if lines:
        _draw_lines(lines, color)


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
        from gpu.types import GPUBatch, GPUVertBuf, GPUVertFormat

        # 4.2-5.2: GPUVertBuf takes a GPUVertFormat, never a format string
        # (a string raises TypeError and used to be swallowed below).
        fmt = GPUVertFormat()
        fmt.attr_add(id="pos", comp_type="F32", len=3, fetch_mode="FLOAT")
        buffer = GPUVertBuf(len=len(vertices), format=fmt)
        buffer.attr_fill(0, vertices)
        batch = GPUBatch(type="LINES", buf=buffer)
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader.bind()
        shader.uniform_float("color", color)
        gpu.state.depth_test_set("NONE")
        wide = tuple(bpy.app.version) < (5, 0, 0)
        try:
            if wide:
                gpu.state.line_width_set(2.0)
            batch.draw(shader)
        finally:
            if wide:
                gpu.state.line_width_set(1.0)
            gpu.state.depth_test_set("LESS_EQUAL")
    except Exception as exc:
        # 只报一次：绘制每帧都会重试，反复打印会刷爆控制台；但完全静默会让
        # GPU API 变更再次变成"视锥永远不出现"的无声故障（v3.1 前的教训）。
        if not _DRAW_ERROR_REPORTED[0]:
            _DRAW_ERROR_REPORTED[0] = True
            print("[presence] draw disabled after error: %r" % (exc,))
        return


def _draw_view():
    if not _STATE["agents"]:
        return
    try:
        space = getattr(bpy.context, "space_data", None)
        if space is None or getattr(space, "type", "") != "VIEW_3D":
            return
        for row in _STATE["agents"]:
            if not isinstance(row, dict) or _is_hidden(row):
                continue
            view = row.get("view") if isinstance(row.get("view"), dict) else None
            if not isinstance(view, dict):
                continue
            color = _agent_color(row)
            _frustum(view, color)
            selected = row.get("selected") if isinstance(row.get("selected"), list) else []
            for uid in selected[:_MAX_SELECTED]:
                _selection(uid, color)
    except Exception:
        return


def _label_screen(region, region_3d, location):
    """World point -> region pixel coordinate, or None when behind the view."""
    try:
        co = region_3d.perspective_matrix @ Vector((location[0], location[1], location[2], 1.0))
        if co.w <= 0:
            return None
        x = region.x + (co.x / co.w + 1.0) * 0.5 * region.width
        y = region.y + (co.y / co.w + 1.0) * 0.5 * region.height
        return (x, y)
    except Exception:
        return None


def _label(region, region_3d, location, text, color):
    if not (region and region_3d and text):
        return
    try:
        import blf

        if _FONT[0] is None:
            root = bpy.utils.system_resource("DATAFILES")
            for name in ("fonts/Inter.woff2", "fonts/DejaVuSansMono.woff2", "fonts/bfont.pfb"):
                path = os.path.join(root, name) if root else ""
                if path and os.path.isfile(path):
                    _FONT[0] = blf.load(path)
                    if _FONT[0]:
                        break
        screen = _label_screen(region, region_3d, location)
        if screen is None:
            return
        x, y = screen
        font = _FONT[0]
        if not font:
            return
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
            if not isinstance(row, dict) or _is_hidden(row):
                continue
            view = row.get("view") if isinstance(row.get("view"), dict) else None
            if not isinstance(view, dict):
                continue
            world = _camera_world(view)
            origin = None if world is None else world.translation
            if origin is None:
                continue
            label = _short_label(row)
            if _matrix4(view.get("projection")) is None:
                label += " (approx)"
            if _STATE["paused"]:
                label += " (paused)"
            _label(region, region_3d, origin, label, _agent_color(row))
    except Exception:
        return