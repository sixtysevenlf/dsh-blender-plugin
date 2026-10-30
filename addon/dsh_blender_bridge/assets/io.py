""" Safe file names, texture matching, downloads and mesh import. """

import re
import bpy
import requests
import tempfile
import os
import shutil
import os.path as osp
from contextlib import contextmanager, redirect_stdout, suppress

from ..commands.core import CoreCommands

MODEL_DOWNLOAD_TIMEOUT = 120


def _safe_file_name(name, fallback):
    name = re.sub(r"[^A-Za-z0-9._-]", "_", osp.basename(str(name or "")))
    return name if name.strip(".") else fallback


def _match_texture_names(directory):
    """If a texture arrived without its file name, save it under the one name
    the MTL is still missing."""
    names = os.listdir(directory)
    referenced = set()
    for name in names:
        if name.lower().endswith(".mtl"):
            with open(osp.join(directory, name), encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and (parts[0].lower().startswith("map_") or parts[0].lower() == "bump"):
                        referenced.add(osp.basename(parts[-1].replace("\\", "/")))
    missing = [name for name in referenced if name not in names]
    spare = [name for name in names if name.lower().endswith((".png", ".jpg", ".jpeg")) and name not in referenced]
    if len(missing) == 1 and len(spare) == 1 and _safe_file_name(missing[0], "") == missing[0]:
        shutil.copyfile(osp.join(directory, spare[0]), osp.join(directory, missing[0]))


def _download_model_files(files, directory):
    """files: [{role, format, url, file_name}]. Returns (model path, format)."""
    model = None
    used = set()
    for index, file in enumerate(files):
        url = str(file.get("url") or "")
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise ValueError("Invalid model file URL.")
        fmt = str(file.get("format") or "").lower()
        name = _safe_file_name(file.get("file_name"), f"{file.get('role') or 'file'}.{fmt or 'bin'}")
        if name in used:
            name = f"{index}_{name}"
        used.add(name)
        path = osp.join(directory, name)
        response = requests.get(url, stream=True, timeout=MODEL_DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        with open(path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        if file.get("role") == "model" and model is None:
            model = (path, fmt or osp.splitext(name)[1].lstrip(".").lower())
    if model is None:
        raise ValueError("No model file to import.")
    _match_texture_names(directory)
    return model


def _import_mesh_file(path, fmt, name):
    existing_objects = set(bpy.data.objects)
    existing_images = set(bpy.data.images)
    if fmt == "obj":
        if bpy.app.version >= (4, 0, 0):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    else:
        bpy.ops.import_scene.gltf(filepath=path)
    # The download folder is deleted after import, so embed any texture the
    # OBJ importer loaded from it. glTF textures are packed already.
    for image in set(bpy.data.images) - existing_images:
        with suppress(Exception):
            if image.filepath and not image.packed_file:
                image.pack()
    imported = set(bpy.data.objects) - existing_objects
    meshes = [obj for obj in imported if obj.type == "MESH"]
    if not meshes:
        raise ValueError("No mesh objects were imported.")
    obj = max(meshes, key=lambda o: len(o.data.vertices))
    # glTF wraps the mesh in an empty; drop it so the mesh is the object Claude moves.
    parent = obj.parent
    if parent is not None and parent in imported and parent.type == "EMPTY" and len(parent.children) == 1:
        matrix = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = matrix
        bpy.data.objects.remove(parent)
    if name:
        obj.name = name
        with suppress(Exception):
            obj.data.name = name
    return obj


def import_model_files(files, name):
    """Download and import; returns the importers' usual reply shape."""
    temp_dir = tempfile.mkdtemp(prefix="dshbb_model_")
    try:
        path, fmt = _download_model_files(files, temp_dir)
        obj = _import_mesh_file(path, fmt, name)
        return {
            "succeed": True,
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "world_bounding_box": CoreCommands._get_aabb(obj),
        }
    except Exception as e:
        return {"succeed": False, "error": str(e)}
    finally:
        with suppress(Exception):
            shutil.rmtree(temp_dir)


def clean_imported_glb(filepath, mesh_name=None):
    # Get the set of existing objects before import
    existing_objects = set(bpy.data.objects)

    # Import the GLB file
    bpy.ops.import_scene.gltf(filepath=filepath)

    # Ensure the context is updated
    bpy.context.view_layer.update()

    # Get all imported objects
    imported_objects = list(set(bpy.data.objects) - existing_objects)
    # imported_objects = [obj for obj in bpy.context.view_layer.objects if obj.select_get()]

    if not imported_objects:
        print("Error: No objects were imported.")
        return

    # Identify the mesh object
    mesh_obj = None

    if len(imported_objects) == 1 and imported_objects[0].type == 'MESH':
        mesh_obj = imported_objects[0]
        print("Single mesh imported, no cleanup needed.")
    else:
        if len(imported_objects) == 2:
            empty_objs = [i for i in imported_objects if i.type == "EMPTY"]
            if len(empty_objs) != 1:
                print("Error: Expected an empty node with one mesh child or a single mesh object.")
                return
            parent_obj = empty_objs.pop()
            if len(parent_obj.children) == 1:
                potential_mesh = parent_obj.children[0]
                if potential_mesh.type == 'MESH':
                    print("GLB structure confirmed: Empty node with one mesh child.")

                    # Unparent the mesh from the empty node
                    potential_mesh.parent = None

                    # Remove the empty node
                    bpy.data.objects.remove(parent_obj)
                    print("Removed empty node, keeping only the mesh.")

                    mesh_obj = potential_mesh
                else:
                    print("Error: Child is not a mesh object.")
                    return
            else:
                print("Error: Expected an empty node with one mesh child or a single mesh object.")
                return
        else:
            print("Error: Expected an empty node with one mesh child or a single mesh object.")
            return

    # Rename the mesh if needed
    try:
        if mesh_obj and mesh_obj.name is not None and mesh_name:
            mesh_obj.name = mesh_name
            if mesh_obj.data.name is not None:
                mesh_obj.data.name = mesh_name
            print(f"Mesh renamed to: {mesh_name}")
    except Exception as e:
        print("Having issue with renaming, give up renaming.")

    return mesh_obj


def collect_mesh_children(obj):
    """Recursively collect MESH objects below obj (shared by import commands)."""
    meshes = []
    if obj.type == 'MESH':
        meshes.append(obj)
    for child in obj.children:
        meshes.extend(collect_mesh_children(child))
    return meshes
