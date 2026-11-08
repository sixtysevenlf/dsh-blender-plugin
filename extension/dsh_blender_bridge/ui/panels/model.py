""" Model-generation sub-panel (AI model APIs). """

import bpy

from ...config import get_dshbb_addon_preferences
from .api import draw_api_toggle


class DSHBB_PT_ModelGen(bpy.types.Panel):
    bl_label = "模型生成"
    bl_idname = "DSHBB_PT_ModelGen"
    bl_parent_id = "DSHBB_PT_EnvModelAPI"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        prefs = get_dshbb_addon_preferences(context)

        sub = draw_api_toggle(layout, scene, "dshbb_use_hyper3d", "Hyper3D Rodin")
        if sub:
            col = sub.column(align=True)
            col.prop(scene, "dshbb_hyper3d_mode", text="Mode")
            if prefs:
                col.prop(prefs, "hyper3d_api_key", text="API Key")
            else:
                col.label(text="API keys live in the add-on preferences", icon="INFO")
            sub.operator("dshbb.set_hyper3d_free_trial_api_key", text="Set Free Trial API Key")

        sub = draw_api_toggle(layout, scene, "dshbb_use_hunyuan3d", "Tencent Hunyuan 3D")
        if sub:
            col = sub.column(align=True)
            col.prop(scene, "dshbb_hunyuan3d_mode", text="Mode")
            if scene.dshbb_hunyuan3d_mode == "OFFICIAL_API":
                if prefs:
                    col.prop(prefs, "hunyuan3d_secret_id", text="SecretId")
                    col.prop(prefs, "hunyuan3d_secret_key", text="SecretKey")
                else:
                    col.label(text="Credentials live in the add-on preferences", icon="INFO")
                col.prop(scene, "dshbb_hunyuan3d_intl_pro", text="International (Pro) account")
            if scene.dshbb_hunyuan3d_mode == "LOCAL_API":
                if prefs:
                    col.prop(prefs, "hunyuan3d_api_url", text="API URL")
                else:
                    col.label(text="API URL lives in the add-on preferences", icon="INFO")
                col.separator()
                col.prop(scene, "dshbb_hunyuan3d_octree_resolution", text="Octree Resolution")
                col.prop(scene, "dshbb_hunyuan3d_num_inference_steps", text="Inference Steps")
                col.prop(scene, "dshbb_hunyuan3d_guidance_scale", text="Guidance Scale")
                col.prop(scene, "dshbb_hunyuan3d_texture", text="Generate Texture")
