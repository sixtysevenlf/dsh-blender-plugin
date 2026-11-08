""" Scene export commands: GLB/FBX writers. """

import bpy
import os


class SceneExportMixin:
    def export_scene(self, filepath, format="glb", object_names=None, selection_only=False, apply_modifiers=True):
        """Export the whole scene, the current selection, or the named objects to a GLB or FBX file.

        Named objects are exported together with their children. GLB carries PBR
        materials, emission, skins, shape keys and animation; FBX is the fallback for
        tools that need Unity's built-in importer. apply_modifiers=False keeps rigs
        and shape keys intact. The file is written where the caller asked, so other
        applications (game engines, viewers) can pick it up without going through
        execute_code.
        """
        if not filepath:
            return {"error": "filepath is required"}
        fmt = (format or "glb").lower()
        if fmt not in ("glb", "fbx"):
            return {"error": f"format must be glb or fbx, got '{format}'"}

        names = [n for n in (object_names or []) if n]
        use_selection = False
        exported = []
        if names:
            missing = [n for n in names if bpy.data.objects.get(n) is None]
            if missing:
                return {"error": "Objects not found in Blender: " + ", ".join(missing)}
            bpy.ops.object.select_all(action='DESELECT')
            for n in names:
                obj = bpy.data.objects[n]
                for o in [obj, *obj.children_recursive]:
                    o.select_set(True)
                    if o.name not in exported:
                        exported.append(o.name)
            bpy.context.view_layer.objects.active = bpy.data.objects[names[0]]
            use_selection = True
        elif selection_only:
            if not bpy.context.selected_objects:
                return {"error": "Nothing is selected in Blender and no object_names were given"}
            exported = [o.name for o in bpy.context.selected_objects]
            use_selection = True
        else:
            exported = [o.name for o in bpy.context.scene.objects]

        try:
            if bpy.context.object and getattr(bpy.context.object, "mode", 'OBJECT') != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        directory = os.path.dirname(filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

        if fmt == "glb":
            bpy.ops.export_scene.gltf(
                filepath=filepath, export_format='GLB', use_selection=use_selection,
                use_active_scene=True, export_apply=apply_modifiers,
                export_animations=True, export_skins=True, export_morph=True, export_yup=True)
        else:
            bpy.ops.export_scene.fbx(
                filepath=filepath, use_selection=use_selection, apply_unit_scale=True,
                bake_space_transform=apply_modifiers, use_mesh_modifiers=apply_modifiers,
                path_mode='COPY', embed_textures=True)

        return {
            "path": filepath,
            "bytes": os.path.getsize(filepath),
            "selection_only": use_selection,
            "exported": exported,
        }
