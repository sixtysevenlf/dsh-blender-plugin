""" Geometry helpers shared by commands and asset importers. """

import mathutils


def compute_aabb(obj):
    """ Returns the world-space axis-aligned bounding box (AABB) of an object. """
    if obj.type != "MESH":
        raise TypeError("Object must be a mesh")

    # Get the bounding box corners in local space
    local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

    # Convert to world coordinates
    world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

    # Compute axis-aligned min/max coordinates
    min_corner = mathutils.Vector(tuple(min(axis) for axis in zip(*world_bbox_corners)))
    max_corner = mathutils.Vector(tuple(max(axis) for axis in zip(*world_bbox_corners)))

    return [
        [*min_corner], [*max_corner]
    ]
