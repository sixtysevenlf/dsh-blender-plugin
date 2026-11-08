""" Add-on preferences. """

import bpy
from bpy.props import IntProperty

from ..compat import ADDON_MODULE
from ..gateway_schema import GATEWAY_DEFAULT_PORT

class DSHBB_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_MODULE  # pyright: ignore[reportAssignmentType]

    gateway_port: IntProperty(
        name="Gateway Port",
        description="Port of the local DSH Gateway WebSocket endpoint (default "
                    f"{GATEWAY_DEFAULT_PORT}; the DSHBB_GATEWAY_PORT environment "
                    "variable overrides this)",
        default=GATEWAY_DEFAULT_PORT,
        min=1024,
        max=65535,
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

        # Connection section
        layout.label(text="DSH Gateway:", icon='URL')
        connection_box = layout.box()
        connection_box.prop(self, "gateway_port", text="Gateway Port")

        layout.separator()
        layout.label(text="Virtual Display (virtual-gui):", icon='DISPLAY')
        display_box = layout.box()
        display_box.operator(
            "dshbb.install_virtual_display_deps",
            text="Install missing prerequisites",
        )
        display_box.label(
            text="同一份计划也由终端 /blender doctor --fix 使用；一键只执行 Gateway 给出的 pkexec 命令",
            icon='INFO',
        )

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
