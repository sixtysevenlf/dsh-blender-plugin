"""World-space frustum geometry for the presence overlay.

The Gateway publishes each observer's pose (viewMatrix or from/lookAt), the
capture projection, and the pixel dimensions. This module turns that snapshot
into world-space line segments. presence_draw.py owns the snapshot state,
colours, GPU buffers, and draw handlers, so nothing here imports bpy or touches
scene data.

The functions are pure and accept a matrix4() parser so the caller keeps the
mathutils surface it already exposes to unit tests.
"""

from mathutils import Vector


def tuple3(vector):
    return (float(vector.x), float(vector.y), float(vector.z))


def aspect(view):
    try:
        width = float(view.get("width") or 0)
        height = float(view.get("height") or 0)
        if width > 0 and height > 0:
            return width / height
    except (TypeError, ValueError):
        pass
    return 16.0 / 9.0


def ndc_corner(proj_inv, x, y, z):
    try:
        point = proj_inv @ Vector((x, y, z, 1.0))
        if abs(point.w) < 1e-9:
            return None
        return point / point.w
    except Exception:
        return None


def rotation(world):
    if world is None:
        return None
    try:
        return world.to_3x3().to_4x4()
    except Exception:
        return None


def projected_frustum(view, world, matrix4):
    """World-space frustum lines from the capture projection, or None."""
    projection = matrix4(view.get("projection"))
    if projection is None:
        return None
    try:
        proj_inv = projection.inverted()
    except Exception:
        return None
    apex = world.translation.copy()
    rot = rotation(world)
    if rot is None:
        return None
    try:
        if bool(view.get("ortho")):
            near = []
            far = []
            for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                a = ndc_corner(proj_inv, x, y, -1.0)
                b = ndc_corner(proj_inv, x, y, 1.0)
                if a is None or b is None:
                    return None
                near.append(apex + rot @ Vector((a.x, a.y, a.z)))
                far.append(apex + rot @ Vector((b.x, b.y, b.z)))
            lines = []
            for i in range(4):
                lines.extend([tuple3(near[i]), tuple3(near[(i + 1) % 4])])
                lines.extend([tuple3(far[i]), tuple3(far[(i + 1) % 4])])
                lines.extend([tuple3(near[i]), tuple3(far[i])])
            return lines
        corners = []
        for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            point = ndc_corner(proj_inv, x, y, 0.0)
            if point is None:
                return None
            depth = max(-point.z, 1e-6)
            scaled = point * (2.0 / depth)
            corners.append(apex + rot @ Vector((scaled.x, scaled.y, scaled.z)))
        lines = []
        for corner in corners:
            lines.extend([tuple3(apex), tuple3(corner)])
        for i in range(4):
            lines.extend([tuple3(corners[i]), tuple3(corners[(i + 1) % 4])])
        return lines
    except Exception:
        return None


def fallback_frustum(view, world):
    """Frustum lines when the capture published no projection."""
    rot = rotation(world)
    if rot is None:
        return None
    try:
        apex = world.translation.copy()
        if view.get("ortho"):
            # Orthographic poses have no apex: draw parallel near/far boxes so
            # the frustum never looks like a perspective pyramid. The published
            # pose carries no clip range, so derive both depths from ortho_scale.
            scale = max(float(view.get("orthoScale") or 10.0), 0.2)
            half_h = scale * 0.5
            half_w = half_h * aspect(view)
            near_z = -0.5 * scale
            far_z = -2.5 * scale
            offsets = ((-1, -1), (1, -1), (1, 1), (-1, 1))
            near = [rot @ Vector((sx * half_w, sy * half_h, near_z)) for sx, sy in offsets]
            far = [rot @ Vector((sx * half_w, sy * half_h, far_z)) for sx, sy in offsets]
            world_near = [apex + corner for corner in near]
            world_far = [apex + corner for corner in far]
            lines = []
            for index in range(4):
                lines.extend([tuple3(world_near[index]), tuple3(world_near[(index + 1) % 4])])
                lines.extend([tuple3(world_far[index]), tuple3(world_far[(index + 1) % 4])])
                lines.extend([tuple3(world_near[index]), tuple3(world_far[index])])
            return lines
        depth = 2.0
        lens = float(view.get("lens") or 50.0)
        half_h = depth * 18.0 / max(lens, 1.0)
        half_w = half_h * aspect(view)
        corners = [
            rot @ Vector((-half_w, -half_h, -depth)),
            rot @ Vector((half_w, -half_h, -depth)),
            rot @ Vector((half_w, half_h, -depth)),
            rot @ Vector((-half_w, half_h, -depth)),
        ]
        world_corners = [apex + corner for corner in corners]
        lines = []
        for corner in world_corners:
            lines.extend([tuple3(apex), tuple3(corner)])
        for index in range(4):
            lines.extend([tuple3(world_corners[index]), tuple3(world_corners[(index + 1) % 4])])
        return lines
    except Exception:
        return None


def frustum_lines(view, world, matrix4):
    """Projected frustum when the pose carries a projection, else fallback."""
    lines = projected_frustum(view, world, matrix4)
    if lines is None:
        lines = fallback_frustum(view, world)
    return lines
