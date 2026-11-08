""" Operators. """

import bpy

from ..commands.selection import best_collection_for_selection, record_selection_send, send_collection_to_dsh, send_selection_to_dsh
from .. import config
from ..config import RODIN_FREE_TRIAL_KEY, get_dshbb_addon_preferences, set_session_credential
from ..protocol import redraw_view3d_panels
from ..server import DSHBlenderBridgeServer
from .install import format_plan, install_missing

class DSHBB_OT_ToggleAIPause(bpy.types.Operator):
    """暂停 / 继续 AI 操作（拒绝 DSH 发来的 Blender 写操作）"""
    bl_idname = "dshbb.toggle_ai_pause"
    bl_label = "暂停 AI 操作"
    bl_description = "暂停后模型只能做只读查询；在 N 面板顶部点「继续 AI 操作」恢复"

    def execute(self, context):
        scene = context.scene
        scene.dshbb_ai_paused = not bool(getattr(scene, "dshbb_ai_paused", False))
        redraw_view3d_panels()
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
        # 凭据单一所有者是 add-on preferences（config.get_credential）。免费试用
        # 只写进程内 session override：本次 Blender 会话生效，reload/重开后失效，
        # 不会覆盖用户已保存的私钥；Scene 属性副本已在 4.5 删除。
        set_session_credential("hyper3d_api_key", RODIN_FREE_TRIAL_KEY)
        prefs = get_dshbb_addon_preferences(context)
        if prefs:
            if not prefs.hyper3d_api_key or prefs.hyper3d_api_key == RODIN_FREE_TRIAL_KEY:
                prefs.hyper3d_api_key = RODIN_FREE_TRIAL_KEY
            else:
                self.report(
                    {'INFO'},
                    "Using free trial for this session only; saved private key was kept."
                )
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
        result = record_selection_send(send_selection_to_dsh())
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
            return best_collection_for_selection() is not None
        except Exception:
            return False

    def execute(self, context):
        best = best_collection_for_selection()
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
        result = record_selection_send(send_collection_to_dsh(collection_name=name))
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
    bl_label = "Connect to DSH"
    bl_description = "Start the DSH Blender Bridge server to connect with DSH"

    def execute(self, context):
        config._user_stopped_server = False
        scene = context.scene

        # A stopped server re-reads its Gateway endpoint on start(), so replace
        # it instead of pinning the port captured when it was first created.
        server = getattr(bpy.types, "dshbb_server", None)
        if server is None or not server.running:
            server = DSHBlenderBridgeServer()
            bpy.types.dshbb_server = server

        server.start()
        scene.dshbb_server_running = server.running

        return {'FINISHED'}

# Operator to stop the server
class DSHBB_OT_StopServer(bpy.types.Operator):
    bl_idname = "dshbb.stop_server"
    bl_label = "Stop the connection to DSH"
    bl_description = "Stop the connection to DSH"

    def execute(self, context):
        config._user_stopped_server = True
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "dshbb_server") and bpy.types.dshbb_server:
            bpy.types.dshbb_server.stop()
            del bpy.types.dshbb_server

        scene.dshbb_server_running = False

        return {'FINISHED'}



class DSHBB_OT_InstallVirtualDisplayDeps(bpy.types.Operator):
    """安装 virtual-gui 缺失的系统前提（一键命令来自 Gateway /doctor，经系统认证执行）"""
    bl_idname = "dshbb.install_virtual_display_deps"
    bl_label = "Install virtual-gui prerequisites"
    bl_description = (
        "读取 Gateway /doctor 的同一份安装计划：只执行支持 pkexec 的一键命令，"
        "其余显示手动命令；安装后复检 capabilities，失败保留 stderr"
    )

    def execute(self, context):
        try:
            result = install_missing()
        except Exception as error:  # noqa: BLE001 - operator report
            self.report({'ERROR'}, "读取安装计划失败：%s" % error)
            return {'CANCELLED'}
        manual = result.get("manual") or []
        if result.get("ok"):
            installed = ", ".join(result.get("installed") or []) or "无"
            self.report({'INFO'}, "virtual-gui 前提已就绪（本次安装：%s）" % installed)
            return {'FINISHED'}
        if manual:
            lines = " | ".join(format_plan(plan) for plan in manual)
            self.report({'WARNING'}, "以下前提需要手动安装：%s" % lines)
        detail = str(result.get("message") or "安装未完成")
        stderr = str(result.get("stderr") or "").strip().splitlines()
        if stderr:
            detail += " · " + stderr[-1]
        self.report({'ERROR'}, detail)
        return {'CANCELLED'}
