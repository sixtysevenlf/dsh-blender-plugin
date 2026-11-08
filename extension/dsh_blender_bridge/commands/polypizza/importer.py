""" Blender-side helpers that turn an imported Poly Pizza model into a result. """

import re

import bpy
import mathutils

from ...assets.io import collect_mesh_children


def safe_model_id(model_id):
    """Turn a model id into a filename-safe stem."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(model_id)) or "model"


def split_imported_objects(imported_objects):
    """Return (roots, every mesh below a root) for the imported objects."""
    root_objects = [obj for obj in imported_objects if obj.parent is None]
    all_meshes = []
    for obj in root_objects:
        all_meshes.extend(collect_mesh_children(obj))
    return root_objects, all_meshes


def apply_attribution(root_objects, model, model_id):
    """Stamp the catalogue's credit line as custom properties on every root.

    69% of the catalogue is CC-BY, so the credit line has to outlive the
    session. Custom properties are saved into the .blend.
    """
    attribution = model.get("Attribution") or ""
    licence = model.get("Licence") or ""
    for root in root_objects:
        root["polypizza_attribution"] = attribution
        root["polypizza_id"] = model.get("ID") or str(model_id)
        root["polypizza_licence"] = licence
    return attribution, licence


def combined_world_bounds(mesh_objects):
    all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
    all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))
    for mesh_obj in mesh_objects:
        # Get world-space bounding box corners
        for corner in mesh_obj.bound_box:
            world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
            all_min.x = min(all_min.x, world_corner.x)
            all_min.y = min(all_min.y, world_corner.y)
            all_min.z = min(all_min.z, world_corner.z)
            all_max.x = max(all_max.x, world_corner.x)
            all_max.y = max(all_max.y, world_corner.y)
            all_max.z = max(all_max.z, world_corner.z)
    return all_min, all_max


def dimensions_of(all_min, all_max):
    return [
        all_max.x - all_min.x,
        all_max.y - all_min.y,
        all_max.z - all_min.z,
    ]


def normalize_roots(root_objects, target_size, max_dimension):
    """Scale root objects so the model's largest dimension is target_size."""
    scale_factor = target_size / max_dimension

    # Only apply scale to ROOT objects (not children!)
    # Child objects inherit parent's scale through matrix_world
    for root in root_objects:
        root.scale = (
            root.scale.x * scale_factor,
            root.scale.y * scale_factor,
            root.scale.z * scale_factor
        )

    # Update the scene to recalculate matrix_world for all objects
    bpy.context.view_layer.update()
    return scale_factor


def build_download_result(imported_object_names, root_objects, all_meshes, model,
                          model_id, normalize_size, target_size):
    """Assemble the download command's result envelope."""
    attribution, licence = apply_attribution(root_objects, model, model_id)

    if all_meshes:
        all_min, all_max = combined_world_bounds(all_meshes)
        dimensions = dimensions_of(all_min, all_max)
        max_dimension = max(dimensions)

        scale_applied = 1.0
        if normalize_size and max_dimension > 0:
            scale_applied = normalize_roots(root_objects, target_size, max_dimension)
            # Recalculate bounding box after scaling
            all_min, all_max = combined_world_bounds(all_meshes)
            dimensions = dimensions_of(all_min, all_max)

        world_bounding_box = [[all_min.x, all_min.y, all_min.z], [all_max.x, all_max.y, all_max.z]]
    else:
        world_bounding_box = None
        dimensions = None
        scale_applied = 1.0

    result = {
        "success": True,
        "message": "Model imported successfully",
        "imported_objects": imported_object_names,
        "model_id": model.get("ID") or str(model_id),
        "title": model.get("Title"),
        "licence": licence,
        "attribution": attribution,
        "tri_count": model.get("Tri Count"),
    }

    if world_bounding_box:
        result["world_bounding_box"] = world_bounding_box
    if dimensions:
        result["dimensions"] = [round(d, 4) for d in dimensions]
    if normalize_size:
        result["scale_applied"] = round(scale_applied, 6)
        result["normalized"] = True

    return result
