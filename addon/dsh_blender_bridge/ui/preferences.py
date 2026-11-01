""" Add-on preferences. """

import bpy
from bpy.props import IntProperty, BoolProperty

from ..compat import ADDON_MODULE
from ..recorders.edit_capture import sync_edit_capture_handlers

class DSHBB_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_MODULE

    def _on_telemetry_consent_changed(self, context):
        try:
            sync_edit_capture_handlers()
        except Exception as e:
            print(f"DSH Blender Bridge: could not sync manual edit handlers: {e}")

    telemetry_consent: BoolProperty(
        name="Allow Telemetry",
        description="Opt in to collection of prompts, code snippets, screenshots, and trajectory data to help improve DSH Blender Bridge. Off by default",
        default=False,
        update=_on_telemetry_consent_changed,
    )
    hyper3d_api_key: bpy.props.StringProperty(
        name="Hyper3D API Key",
        subtype="PASSWORD",
        description="Persistent Hyper3D API Key",
        default=""
    )
    sketchfab_api_key: bpy.props.StringProperty(
        name="Sketchfab API Key",
        subtype="PASSWORD",
        description="Persistent Sketchfab API Key",
        default=""
    )
    polypizza_api_key: bpy.props.StringProperty(
        name="Poly Pizza API Key",
        subtype="PASSWORD",
        description="Persistent Poly Pizza API Key",
        default=""
    )
    hunyuan3d_secret_id: bpy.props.StringProperty(
        name="Hunyuan3D SecretId",
        description="Persistent Hunyuan3D SecretId",
        default=""
    )
    hunyuan3d_secret_key: bpy.props.StringProperty(
        name="Hunyuan3D SecretKey",
        subtype="PASSWORD",
        description="Persistent Hunyuan3D SecretKey",
        default=""
    )
    hunyuan3d_api_url: bpy.props.StringProperty(
        name="Hunyuan3D API URL",
        description="Persistent Hunyuan3D API URL",
        default=""
    )

    def draw(self, context):
        layout = self.layout

        # Telemetry section
        layout.label(text="Telemetry & Privacy:", icon='PREFERENCES')

        box = layout.box()
        row = box.row()
        row.prop(self, "telemetry_consent", text="Allow Telemetry")

        # Info text
        box.separator()
        if self.telemetry_consent:
            box.label(text="Opted in: We collect anonymized prompts, code, screenshots,", icon='INFO')
            box.label(text="and trajectory data (actions, scene state, feedback).", icon='BLANK1')
        else:
            box.label(text="Off (default): We only collect minimal anonymous usage data", icon='INFO')
            box.label(text="(tool names, success/failure, duration - no prompts or code).", icon='BLANK1')
        box.separator()
        box.label(text="Data is not linked to your name or account. Change this anytime.", icon='CHECKMARK')

        # Terms and Conditions link
        box.separator()
        row = box.row()
        row.operator("dshbb.open_terms", text="View Terms and Conditions", icon='TEXT')

        layout.separator()
        layout.label(text="3D Generation:", icon='SHADERFX')
        gen_box = layout.box()
        col = gen_box.column()
        col.prop(self, "hyper3d_api_key", text="Hyper3D API Key")
        row = col.row(align=True)
        row.operator("wm.url_open", text="hyper3d.ai keys", icon='URL').url = "https://hyper3d.ai/"
        row.operator("wm.url_open", text="fal.ai keys", icon='URL').url = "https://fal.ai/dashboard/keys"
        col.separator()
        col.prop(self, "hunyuan3d_secret_id", text="Hunyuan3D SecretId")
        col.prop(self, "hunyuan3d_secret_key", text="Hunyuan3D SecretKey")
        col.operator("wm.url_open", text="Tencent Cloud keys", icon='URL').url = \
            "https://console.cloud.tencent.com/cam/capi"
        col.prop(self, "hunyuan3d_api_url", text="Hunyuan3D API URL")

        layout.separator()
        layout.label(text="Persistent API Credentials:", icon='LOCKED')
        cred_box = layout.box()
        cred_box.prop(self, "sketchfab_api_key", text="Sketchfab API Key")
        cred_box.prop(self, "polypizza_api_key", text="Poly Pizza API Key")
