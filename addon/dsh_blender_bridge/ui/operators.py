""" Operators. """

import bpy
import socket
from contextlib import contextmanager, redirect_stdout, suppress

from ..commands.selection import _best_collection_for_selection, _record_selection_send, send_collection_to_dsh, send_selection_to_dsh
from .. import config
from ..config import RODIN_FREE_TRIAL_KEY, get_dshbb_addon_preferences
from ..premium.client import PREMIUM_INTEGRATIONS, PREMIUM_MANAGE_URL_DEFAULT, _premium_status, _premium_tag_redraw, premium_instance_id, premium_license_key, premium_request, premium_summary
from ..protocol import _redraw_view3d_panels
from ..server import DSHBlenderBridgeServer

class DSHBB_OT_PremiumActivate(bpy.types.Operator):
    bl_idname = "dshbb.premium_activate"
    bl_label = "Activate Premium"
    bl_description = "Activate this device with your Premium license key"

    def execute(self, context):
        prefs = get_dshbb_addon_preferences(context)
        key = premium_license_key()
        if not prefs or not key:
            self.report({'ERROR'}, "Paste your license key first.")
            return {'CANCELLED'}
        result = premium_request("POST", "/activate", {
            "license_key": key,
            "device_name": socket.gethostname() or "Blender",
        }, auth=False)
        if result.get("code") or not result.get("instance_id"):
            self.report({'ERROR'}, result.get("message") or "Activation failed.")
            return {'CANCELLED'}
        prefs.premium_instance_id = result["instance_id"]
        prefs.generation_source = "PREMIUM"
        # Premium covers every generator, so switch them all on in this scene.
        for _label, prop in PREMIUM_INTEGRATIONS.values():
            with suppress(Exception):
                setattr(context.scene, prop, True)
        _premium_tag_redraw()
        self.report({'INFO'}, premium_summary())
        return {'FINISHED'}


class DSHBB_OT_PremiumDeactivate(bpy.types.Operator):
    bl_idname = "dshbb.premium_deactivate"
    bl_label = "Deactivate Premium"
    bl_description = "Free this device's activation slot so the key can be used on another machine"

    def execute(self, context):
        prefs = get_dshbb_addon_preferences(context)
        result = premium_request("POST", "/deactivate", {})
        # A key the server no longer knows has nothing left to free.
        if result.get("code") and result["code"] not in {"INVALID_KEY", "NOT_ACTIVATED"}:
            self.report({'ERROR'}, result["message"])
            return {'CANCELLED'}
        if prefs:
            prefs.premium_instance_id = ""
            # Without an activation Premium can't generate, so go back to own keys.
            # The license key stays filled in, so reactivating is one click.
            prefs.generation_source = "BYOK"
        _premium_status.clear()
        # Deactivate runs from Preferences; the 3D Viewport sidebar won't redraw on its own.
        _premium_tag_redraw()
        self.report({'INFO'}, "This device was deactivated. Generation uses your own API keys again.")
        return {'FINISHED'}


class DSHBB_OT_PremiumRefresh(bpy.types.Operator):
    bl_idname = "dshbb.premium_refresh"
    bl_label = "Refresh Premium Status"
    bl_description = "Fetch your plan and remaining generations"

    def execute(self, context):
        result = premium_request("GET", "/status")
        if result.get("code"):
            self.report({'ERROR'}, result["message"])
            return {'CANCELLED'}
        self.report({'INFO'}, premium_summary())
        return {'FINISHED'}


class DSHBB_OT_PremiumOpenAccount(bpy.types.Operator):
    bl_idname = "dshbb.premium_open_account"
    bl_label = "Manage Premium Account"
    bl_description = "Upgrade, cancel or download invoices"

    def execute(self, context):
        # Portal links are per customer and short-lived, so fetch one per click.
        url = PREMIUM_MANAGE_URL_DEFAULT
        if premium_instance_id():
            url = premium_request("GET", "/status?portal=1").get("manage_url") or url
        bpy.ops.wm.url_open(url=url)
        return {'FINISHED'}


PREMIUM_CLASSES = (
    DSHBB_OT_PremiumActivate,
    DSHBB_OT_PremiumDeactivate,
    DSHBB_OT_PremiumRefresh,
    DSHBB_OT_PremiumOpenAccount,
)


class DSHBB_OT_ToggleAIPause(bpy.types.Operator):
    """暂停 / 继续 AI 操作（拒绝 DSH 发来的 Blender 写操作）"""
    bl_idname = "dshbb.toggle_ai_pause"
    bl_label = "暂停 AI 操作"
    bl_description = "暂停后模型只能做只读查询；在 N 面板顶部点「继续 AI 操作」恢复"

    def execute(self, context):
        scene = context.scene
        scene.dshbb_ai_paused = not bool(getattr(scene, "dshbb_ai_paused", False))
        _redraw_view3d_panels()
        if scene.dshbb_ai_paused:
            self.report({'WARNING'}, "AI 操作已暂停（只读查询仍可用）")
        else:
            self.report({'INFO'}, "AI 操作已恢复")
        return {'FINISHED'}


# Operator to set Hyper3D API Key
class DSHBB_OT_SetFreeTrialHyper3DAPIKey(bpy.types.Operator):
    bl_idname = "dshbb.set_hyper3d_free_trial_api_key"
    bl_label = "Set Free Trial API Key"

    def execute(self, context):
        prefs = get_dshbb_addon_preferences(context)
        if prefs:
            if not prefs.hyper3d_api_key or prefs.hyper3d_api_key == RODIN_FREE_TRIAL_KEY:
                prefs.hyper3d_api_key = RODIN_FREE_TRIAL_KEY
            else:
                self.report(
                    {'INFO'},
                    "Using free trial for this session only; saved private key was kept."
                )
        context.scene.dshbb_hyper3d_api_key = RODIN_FREE_TRIAL_KEY
        context.scene.dshbb_hyper3d_mode = 'MAIN_SITE'
        self.report({'INFO'}, "API Key set successfully!")
        return {'FINISHED'}

# Operator to start the server
class DSHBB_OT_SendSelectionToDSH(bpy.types.Operator):
    """发送当前原生选中到 DSH（写进下一条消息草稿，不自动发送）"""
    bl_idname = "dshbb.send_selection_to_dsh"
    bl_label = "发送选中到 DSH"
    bl_description = "把当前选中的物体 POST 给 DSH 插件后端；DSH 收到后插入当前会话草稿（不自动发送）"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        try:
            return bool(context.selected_objects)
        except Exception:
            return False

    def execute(self, context):
        result = _record_selection_send(send_selection_to_dsh())
        if result.get("ok"):
            self.report({'INFO'}, "已发送选中到 DSH（seq %s）" % result.get("seq"))
        else:
            self.report({'WARNING'}, "发送失败：%s"
                        % (result.get("error") or result.get("hint") or "unknown"))
        try:
            context.area.tag_redraw()
        except Exception:
            pass
        return {'FINISHED'}


class DSHBB_OT_UseSelectionCollection(bpy.types.Operator):
    """把「集合」下拉设为当前选中对象最常出现的集合"""
    bl_idname = "dshbb.use_selection_collection"
    bl_label = "用选中对象的集合"
    bl_description = "当前选中对象分布在多个集合时，自动挑最相关的一个填进「集合」下拉"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        try:
            return _best_collection_for_selection() is not None
        except Exception:
            return False

    def execute(self, context):
        best = _best_collection_for_selection()
        if best is None:
            self.report({'WARNING'}, "当前选中对象不属于任何集合")
            return {'CANCELLED'}
        try:
            context.scene.dshbb_collection_target = best
        except Exception as exc:
            self.report({'WARNING'}, "设置集合失败：%s" % str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, "已选集合「%s」（%d 件）" % (best.name, len(best.objects)))
        try:
            context.area.tag_redraw()
        except Exception:
            pass
        return {'FINISHED'}


class DSHBB_OT_ListRowsInc(bpy.types.Operator):
    """把 N 面板列表加高一点"""
    bl_idname = "dshbb.list_rows_inc"
    bl_label = "列表加高"
    bl_description = "把对象 / 成员列表的高度增加 2 行（3–30）"

    @classmethod
    def poll(cls, context):
        try:
            return int(getattr(context.scene, "dshbb_list_rows", 8)) < 30
        except Exception:
            return False

    def execute(self, context):
        scene = context.scene
        scene.dshbb_list_rows = min(30, int(getattr(scene, "dshbb_list_rows", 8)) + 2)
        try:
            context.area.tag_redraw()
        except Exception:
            pass
        return {'FINISHED'}


class DSHBB_OT_ListRowsDec(bpy.types.Operator):
    """把 N 面板列表变矮一点"""
    bl_idname = "dshbb.list_rows_dec"
    bl_label = "列表变矮"
    bl_description = "把对象 / 成员列表的高度减少 2 行（3–30）"

    @classmethod
    def poll(cls, context):
        try:
            return int(getattr(context.scene, "dshbb_list_rows", 8)) > 3
        except Exception:
            return False

    def execute(self, context):
        scene = context.scene
        scene.dshbb_list_rows = max(3, int(getattr(scene, "dshbb_list_rows", 8)) - 2)
        try:
            context.area.tag_redraw()
        except Exception:
            pass
        return {'FINISHED'}


class DSHBB_OT_SendCollectionToDSH(bpy.types.Operator):
    """把一个集合整体发送到 DSH（大装配体推荐）"""
    bl_idname = "dshbb.send_collection_to_dsh"
    bl_label = "发送集合到 DSH"
    bl_description = "把 N 面板「集合」下拉里选中的集合整体发送到 DSH（不要求先选中对象）"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        try:
            return getattr(context.scene, "dshbb_collection_target", None) is not None
        except Exception:
            return False

    def execute(self, context):
        target = getattr(context.scene, "dshbb_collection_target", None)
        name = getattr(target, "name", None) if target else None
        result = _record_selection_send(send_collection_to_dsh(collection_name=name))
        if result.get("ok"):
            self.report({'INFO'}, "已发送集合「%s」到 DSH（seq %s）" % (name, result.get("seq")))
        else:
            self.report({'WARNING'}, "发送集合失败：%s"
                        % (result.get("error") or result.get("hint") or "unknown"))
        try:
            context.area.tag_redraw()
        except Exception:
            pass
        return {'FINISHED'}


class DSHBB_OT_StartServer(bpy.types.Operator):
    bl_idname = "dshbb.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the DSH Blender Bridge server to connect with Claude"

    def execute(self, context):
        config._user_stopped_server = False
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "dshbb_server") or not bpy.types.dshbb_server:
            bpy.types.dshbb_server = DSHBlenderBridgeServer(port=scene.dshbb_port)

        # Start the server
        bpy.types.dshbb_server.start()
        scene.dshbb_server_running = bpy.types.dshbb_server.running

        return {'FINISHED'}

# Operator to stop the server
class DSHBB_OT_StopServer(bpy.types.Operator):
    bl_idname = "dshbb.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        config._user_stopped_server = True
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "dshbb_server") and bpy.types.dshbb_server:
            bpy.types.dshbb_server.stop()
            del bpy.types.dshbb_server

        scene.dshbb_server_running = False

        return {'FINISHED'}

# Operator to open Terms and Conditions
class DSHBB_OT_OpenTerms(bpy.types.Operator):
    bl_idname = "dshbb.open_terms"
    bl_label = "View Terms and Conditions"
    bl_description = "Open the Terms and Conditions document"

    def execute(self, context):
        # Open the Terms and Conditions on GitHub
        terms_url = "https://github.com/ahujasid/blender-mcp/blob/main/TERMS_AND_CONDITIONS.md"
        try:
            import webbrowser
            webbrowser.open(terms_url)
            self.report({'INFO'}, "Terms and Conditions opened in browser")
        except Exception as e:
            self.report({'ERROR'}, f"Could not open Terms and Conditions: {str(e)}")

        return {'FINISHED'}
