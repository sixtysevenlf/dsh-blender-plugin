# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025

""" DSH Blender Bridge add-on entry point. """

import bpy
from bpy.props import IntProperty, BoolProperty

from .commands.selection import _on_selection_mode_update
from .protocol import ADDON_PROTOCOL_VERSION, ADDON_VERSION
from .config import _dshbb_register_auto_start, _dshbb_unregister_auto_start
from .recorders.activity import _unregister_activity_handlers
from .recorders.edit_capture import _unregister_edit_capture_handlers
from .ui.lists import DSHBB_SelectionItem, DSHBB_UL_ObjectList, _dshbb_ui_list_timer, _on_show_collection_list_update, _on_show_selection_list_update
from .ui.operators import DSHBB_OT_ListRowsDec, DSHBB_OT_ListRowsInc, DSHBB_OT_OpenTerms, DSHBB_OT_SendCollectionToDSH, DSHBB_OT_SendSelectionToDSH, DSHBB_OT_SetFreeTrialHyper3DAPIKey, DSHBB_OT_StartServer, DSHBB_OT_StopServer, DSHBB_OT_ToggleAIPause, DSHBB_OT_UseSelectionCollection, PREMIUM_CLASSES
from .ui.panels import DSHBB_PT_EnvGen, DSHBB_PT_EnvModelAPI, DSHBB_PT_ModelGen, DSHBB_PT_Panel
from .ui.preferences import DSHBB_AddonPreferences

bl_info = {
    "name": "DSH Blender Bridge",
    "author": "Siddharth Ahuja",
    "version": (1, 11),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > DSH Blender Bridge",
    "description": "Connect Blender to Claude via MCP",
    "doc_url": "https://github.com/sixtysevenlf/dsh-blender-plugin",
    "category": "Interface",
}


def register():
    # UIList 保持同步的轻量 timer（不在 draw 里写 ID 数据）
    try:
        bpy.app.timers.register(_dshbb_ui_list_timer, first_interval=0.8, persistent=True)
    except Exception:
        pass
    # UIList 的 PropertyGroup 必须先注册，后面 CollectionProperty(type=...) 才能用
    bpy.utils.register_class(DSHBB_SelectionItem)
    bpy.utils.register_class(DSHBB_UL_ObjectList)

    bpy.types.Scene.dshbb_port = IntProperty(
        name="Port",
        description="Port for the DSH Blender Bridge server",
        default=9876,
        min=1024,
        max=65535
    )

    bpy.types.Scene.dshbb_server_running = bpy.props.BoolProperty(
        name="Server Running",
        default=False
    )

    bpy.types.Scene.dshbb_auto_start_server = bpy.props.BoolProperty(
        name="Auto-Start Server",
        description="Automatically start the MCP server when Blender loads",
        default=True
    )

    bpy.types.Scene.dshbb_backend_url = bpy.props.StringProperty(
        name="DSH Backend URL",
        description="DSH 插件后端地址（默认 http://127.0.0.1:9877；留空用默认 / 环境变量 DSH_BLENDER_BACKEND_URL）",
        default=""
    )

    bpy.types.Scene.dshbb_collection_target = bpy.props.PointerProperty(
        name="Collection",
        description="要发送给 DSH 的集合（大装配体不必逐个选对象）",
        type=bpy.types.Collection
    )

    # v1.8 UIList：对象 / 集合成员列表（可折叠、可滚动、行数可调）
    bpy.types.Scene.dshbb_selection_items = bpy.props.CollectionProperty(type=DSHBB_SelectionItem)
    bpy.types.Scene.dshbb_collection_items = bpy.props.CollectionProperty(type=DSHBB_SelectionItem)
    bpy.types.Scene.dshbb_selection_index = bpy.props.IntProperty(default=0)
    bpy.types.Scene.dshbb_collection_index = bpy.props.IntProperty(default=0)
    bpy.types.Scene.dshbb_show_selection_list = bpy.props.BoolProperty(
        name="Show Selection List", description="展开/折叠 N 面板的对象列表", default=False,
        update=_on_show_selection_list_update)
    bpy.types.Scene.dshbb_show_collection_list = bpy.props.BoolProperty(
        name="Show Collection List", description="展开/折叠 N 面板的集合成员列表", default=False,
        update=_on_show_collection_list_update)
    bpy.types.Scene.dshbb_list_rows = bpy.props.IntProperty(
        name="List Rows", description="列表高度（显示行数，可拖动滑杆调整）",
        default=8, min=3, max=30, soft_min=3, soft_max=20)
    bpy.types.Scene.dshbb_show_advanced = bpy.props.BoolProperty(
        name="Show Advanced", description="展开/折叠后端地址等高级项", default=False)
    bpy.types.Scene.dshbb_last_panel_error = bpy.props.StringProperty(
        name="Last Panel Error", description="最近一次 N 面板绘制异常（诊断用）", default="")
    bpy.types.Scene.dshbb_ai_paused = bpy.props.BoolProperty(
        name="Pause AI Operations",
        description="暂停后，DSH/模型发来的 Blender 写操作会被拒绝（只读查询仍可用）；在 N 面板顶部恢复",
        default=False)

    bpy.types.Scene.dshbb_selection_mode = bpy.props.EnumProperty(
        name="Selection Mode",
        description="发送到 DSH 的模式：多选（当前选中的对象）/ 集合（整组）",
        items=[
            ("MULTI", "多选", "发送当前选中的多个对象", 'RESTRICT_SELECT_OFF', 0),
            ("COLLECTION", "集合", "发送一个集合（大装配体推荐）", 'OUTLINER_COLLECTION', 1),
        ],
        default="MULTI",
        update=_on_selection_mode_update
    )

    bpy.types.Scene.dshbb_use_polyhaven = bpy.props.BoolProperty(
        name="Use Poly Haven",
        description="Enable Poly Haven asset integration",
        default=False
    )

    bpy.types.Scene.dshbb_use_hyper3d = bpy.props.BoolProperty(
        name="Use Hyper3D Rodin",
        description="Enable Hyper3D Rodin generatino integration",
        default=False
    )

    bpy.types.Scene.dshbb_hyper3d_mode = bpy.props.EnumProperty(
        name="Rodin Mode",
        description="Choose the platform used to call Rodin APIs",
        items=[
            ("MAIN_SITE", "hyper3d.ai", "hyper3d.ai"),
            ("FAL_AI", "fal.ai", "fal.ai"),
        ],
        default="MAIN_SITE"
    )

    bpy.types.Scene.dshbb_hyper3d_api_key = bpy.props.StringProperty(
        name="Hyper3D API Key",
        subtype="PASSWORD",
        description="API Key provided by Hyper3D",
        default=""
    )

    bpy.types.Scene.dshbb_use_hunyuan3d = bpy.props.BoolProperty(
        name="Use Hunyuan 3D",
        description="Enable Hunyuan asset integration",
        default=False
    )

    bpy.types.Scene.dshbb_hunyuan3d_mode = bpy.props.EnumProperty(
        name="Hunyuan3D Mode",
        description="Choose a local or official APIs",
        items=[
            ("LOCAL_API", "local api", "local api"),
            ("OFFICIAL_API", "official api", "official api"),
        ],
        default="LOCAL_API"
    )

    bpy.types.Scene.dshbb_hunyuan3d_intl_pro = bpy.props.BoolProperty(
        name="International (Pro)",
        description="Use the Tencent Cloud International 'Hunyuan-to-3D (Professional)' service "
                    "(hunyuan API, region ap-singapore, PBR enabled). Enable this when your SecretId/"
                    "SecretKey come from tencentcloud.com; leave it off for mainland AI3D 3.0 accounts",
        default=False
    )

    bpy.types.Scene.dshbb_hunyuan3d_secret_id = bpy.props.StringProperty(
        name="Hunyuan 3D SecretId",
        description="SecretId provided by Hunyuan 3D",
        default=""
    )

    bpy.types.Scene.dshbb_hunyuan3d_secret_key = bpy.props.StringProperty(
        name="Hunyuan 3D SecretKey",
        subtype="PASSWORD",
        description="SecretKey provided by Hunyuan 3D",
        default=""
    )

    bpy.types.Scene.dshbb_hunyuan3d_api_url = bpy.props.StringProperty(
        name="API URL",
        description="URL of the Hunyuan 3D API service",
        default="http://localhost:8081"
    )

    bpy.types.Scene.dshbb_hunyuan3d_octree_resolution = bpy.props.IntProperty(
        name="Octree Resolution",
        description="Octree resolution for the 3D generation",
        default=256,
        min=128,
        max=512,
    )

    bpy.types.Scene.dshbb_hunyuan3d_num_inference_steps = bpy.props.IntProperty(
        name="Number of Inference Steps",
        description="Number of inference steps for the 3D generation",
        default=20,
        min=20,
        max=50,
    )

    bpy.types.Scene.dshbb_hunyuan3d_guidance_scale = bpy.props.FloatProperty(
        name="Guidance Scale",
        description="Guidance scale for the 3D generation",
        default=5.5,
        min=1.0,
        max=10.0,
    )

    bpy.types.Scene.dshbb_hunyuan3d_texture = bpy.props.BoolProperty(
        name="Generate Texture",
        description="Whether to generate texture for the 3D model",
        default=False,
    )

    bpy.types.Scene.dshbb_use_sketchfab = bpy.props.BoolProperty(
        name="Use Sketchfab",
        description="Enable Sketchfab asset integration",
        default=False
    )

    bpy.types.Scene.dshbb_sketchfab_api_key = bpy.props.StringProperty(
        name="Sketchfab API Key",
        subtype="PASSWORD",
        description="API Key provided by Sketchfab",
        default=""
    )

    bpy.types.Scene.dshbb_use_tripo = bpy.props.BoolProperty(
        name="Use Tripo",
        description="Enable Tripo 3D model generation (Premium)",
        default=False
    )

    bpy.types.Scene.dshbb_use_polypizza = bpy.props.BoolProperty(
        name="Use Poly Pizza",
        description="Enable Poly Pizza asset integration",
        default=False
    )

    bpy.types.Scene.dshbb_polypizza_api_key = bpy.props.StringProperty(
        name="Poly Pizza API Key",
        subtype="PASSWORD",
        description="API Key provided by Poly Pizza",
        default=""
    )

    # Register preferences class
    bpy.utils.register_class(DSHBB_AddonPreferences)

    bpy.utils.register_class(DSHBB_PT_Panel)
    # v2.5：父面板 + 两个子面板（默认全折叠），注册顺序决定面板顺序
    bpy.utils.register_class(DSHBB_PT_EnvModelAPI)
    bpy.utils.register_class(DSHBB_PT_EnvGen)
    bpy.utils.register_class(DSHBB_PT_ModelGen)
    bpy.utils.register_class(DSHBB_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.register_class(DSHBB_OT_StartServer)
    bpy.utils.register_class(DSHBB_OT_SendSelectionToDSH)
    bpy.utils.register_class(DSHBB_OT_SendCollectionToDSH)
    bpy.utils.register_class(DSHBB_OT_ToggleAIPause)
    bpy.utils.register_class(DSHBB_OT_ListRowsInc)
    bpy.utils.register_class(DSHBB_OT_ListRowsDec)
    bpy.utils.register_class(DSHBB_OT_UseSelectionCollection)
    bpy.utils.register_class(DSHBB_OT_StopServer)
    bpy.utils.register_class(DSHBB_OT_OpenTerms)
    for cls in PREMIUM_CLASSES:
        bpy.utils.register_class(cls)

    # Add-on registration can run before Blender has a stable UI/scene context.
    # Defer socket startup and retry after startup-file or .blend loads.
    _dshbb_register_auto_start()

    print("DSH Blender Bridge addon registered")


def unregister():
    try:
        bpy.app.timers.unregister(_dshbb_ui_list_timer)
    except Exception:
        pass
    # 先删属性，再注销类（顺序反过来会留下悬空属性）
    _dshbb_unregister_auto_start()

    _unregister_edit_capture_handlers()
    _unregister_activity_handlers()

    # Stop the server if it's running
    if hasattr(bpy.types, "dshbb_server") and bpy.types.dshbb_server:
        bpy.types.dshbb_server.stop()
        del bpy.types.dshbb_server

    bpy.utils.unregister_class(DSHBB_PT_Panel)
    bpy.utils.unregister_class(DSHBB_PT_ModelGen)
    bpy.utils.unregister_class(DSHBB_PT_EnvGen)
    bpy.utils.unregister_class(DSHBB_PT_EnvModelAPI)
    bpy.utils.unregister_class(DSHBB_UL_ObjectList)
    bpy.utils.unregister_class(DSHBB_SelectionItem)
    bpy.utils.unregister_class(DSHBB_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.unregister_class(DSHBB_OT_StartServer)
    bpy.utils.unregister_class(DSHBB_OT_SendSelectionToDSH)
    bpy.utils.unregister_class(DSHBB_OT_SendCollectionToDSH)
    bpy.utils.unregister_class(DSHBB_OT_ToggleAIPause)
    bpy.utils.unregister_class(DSHBB_OT_ListRowsInc)
    bpy.utils.unregister_class(DSHBB_OT_ListRowsDec)
    bpy.utils.unregister_class(DSHBB_OT_UseSelectionCollection)
    bpy.utils.unregister_class(DSHBB_OT_StopServer)
    bpy.utils.unregister_class(DSHBB_OT_OpenTerms)
    for cls in PREMIUM_CLASSES:
        bpy.utils.unregister_class(cls)
    bpy.utils.unregister_class(DSHBB_AddonPreferences)

    del bpy.types.Scene.dshbb_port
    del bpy.types.Scene.dshbb_server_running
    del bpy.types.Scene.dshbb_auto_start_server
    del bpy.types.Scene.dshbb_backend_url
    del bpy.types.Scene.dshbb_collection_target
    del bpy.types.Scene.dshbb_selection_mode
    del bpy.types.Scene.dshbb_selection_items
    del bpy.types.Scene.dshbb_collection_items
    del bpy.types.Scene.dshbb_selection_index
    del bpy.types.Scene.dshbb_collection_index
    del bpy.types.Scene.dshbb_show_selection_list
    del bpy.types.Scene.dshbb_show_collection_list
    del bpy.types.Scene.dshbb_list_rows
    del bpy.types.Scene.dshbb_show_advanced
    del bpy.types.Scene.dshbb_last_panel_error
    del bpy.types.Scene.dshbb_ai_paused
    del bpy.types.Scene.dshbb_use_polyhaven
    del bpy.types.Scene.dshbb_use_hyper3d
    del bpy.types.Scene.dshbb_hyper3d_mode
    del bpy.types.Scene.dshbb_hyper3d_api_key
    del bpy.types.Scene.dshbb_use_sketchfab
    del bpy.types.Scene.dshbb_sketchfab_api_key
    del bpy.types.Scene.dshbb_use_tripo
    del bpy.types.Scene.dshbb_use_polypizza
    del bpy.types.Scene.dshbb_polypizza_api_key
    del bpy.types.Scene.dshbb_use_hunyuan3d
    del bpy.types.Scene.dshbb_hunyuan3d_mode
    del bpy.types.Scene.dshbb_hunyuan3d_intl_pro
    del bpy.types.Scene.dshbb_hunyuan3d_secret_id
    del bpy.types.Scene.dshbb_hunyuan3d_secret_key
    del bpy.types.Scene.dshbb_hunyuan3d_api_url
    del bpy.types.Scene.dshbb_hunyuan3d_octree_resolution
    del bpy.types.Scene.dshbb_hunyuan3d_num_inference_steps
    del bpy.types.Scene.dshbb_hunyuan3d_guidance_scale
    del bpy.types.Scene.dshbb_hunyuan3d_texture

    print("DSH Blender Bridge addon unregistered")


if __name__ == "__main__":
    register()
