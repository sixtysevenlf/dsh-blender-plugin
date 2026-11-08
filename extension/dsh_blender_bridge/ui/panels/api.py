""" Shared API toggle row and the parent API panel. """

import bpy


def draw_api_toggle(layout, scene, prop_name, title):
    """集成开关行（无图标，保持面板简洁）。"""
    box = layout.box()
    row = box.row()
    row.prop(scene, prop_name, text="")
    row.label(text=title)
    return box if getattr(scene, prop_name) else None


class DSHBB_PT_EnvModelAPI(bpy.types.Panel):
    bl_label = "环境与角色模型API调用"
    bl_idname = "DSHBB_PT_EnvModelAPI"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "DSH Blender Bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        self.layout.label(text="环境生成：资产库；模型生成：AI 模型 API")
