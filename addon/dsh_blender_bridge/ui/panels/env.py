""" Environment-generation sub-panel (asset libraries). """

import bpy

from ...config import POLYHAVEN_SITE, get_dshbb_addon_preferences
from .api import draw_api_toggle


class DSHBB_PT_EnvGen(bpy.types.Panel):
    bl_label = "环境生成"
    bl_idname = "DSHBB_PT_EnvGen"
    bl_parent_id = "DSHBB_PT_EnvModelAPI"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        prefs = get_dshbb_addon_preferences(context)

        sub = draw_api_toggle(layout, scene, "dshbb_use_polyhaven", "Poly Haven")
        if sub:
            col = sub.column(align=True)
            col.label(text="Free CC0 HDRIs, textures and models")
            col.operator("wm.url_open", text="polyhaven.com").url = POLYHAVEN_SITE

        sub = draw_api_toggle(layout, scene, "dshbb_use_sketchfab", "Sketchfab")
        if sub:
            col = sub.column(align=True)
            if prefs:
                col.prop(prefs, "sketchfab_api_key", text="API Key")
            else:
                col.label(text="API keys live in the add-on preferences", icon="INFO")

        sub = draw_api_toggle(layout, scene, "dshbb_use_polypizza", "Poly Pizza")
        if sub:
            col = sub.column(align=True)
            if prefs:
                col.prop(prefs, "polypizza_api_key", text="API Key")
            else:
                col.label(text="API keys live in the add-on preferences", icon="INFO")
