""" GLB import cleanup helpers for the asset import commands. """

import bpy


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
