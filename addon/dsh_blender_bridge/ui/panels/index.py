""" Main N-panel: activity state, connection, selection and community links. """

import bpy

from ...commands import selection as selection_commands
from ...commands.selection import selection_collection_summary
from ...config import dshbb_backend_url, gateway_url, get_dshbb_addon_preferences, get_gateway_port
from ...protocol import ai_paused
from ...recorders.activity import get_activity_tracker
from ...ui.lists import LIST_UI_CAP, log_panel_error, sub_mode_label


class DSHBB_PT_Panel(bpy.types.Panel):
    bl_label = "DSH Blender Bridge"
    bl_idname = "DSHBB_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "DSH Blender Bridge"

    def draw(self, context):
        # 包一层：任何绘制异常都记录下来（用户不方便开控制台），并尽量不把整个面板拖黑
        try:
            self._draw_impl(context)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            log_panel_error(tb)
            try:
                context.scene.dshbb_last_panel_error = tb[-3000:]
            except Exception:
                pass
            try:
                self.layout.label(text="面板绘制异常：" + str(exc)[:120], icon="ERROR")
            except Exception:
                pass

    def _draw_impl(self, context):
        layout = self.layout
        scene = context.scene
        activity = self._activity_snapshot()
        self._draw_activity(layout, activity)
        self._draw_connection(layout, scene, context)
        self._draw_selection(layout, scene, activity)

    @staticmethod
    def _activity_snapshot():
        try:
            return get_activity_tracker().snapshot(include_current_command=False)
        except Exception:
            return {}

    @staticmethod
    def _draw_activity(layout, activity):
        # v2.5 · 活动状态置顶（AI 操作时警告）+ 暂停 AI 操作
        layout.separator()
        act_box = layout.box()
        state = str(activity.get("state") or "idle")
        state_text = {"human": "人：操作中", "agent": "AI：执行中",
                      "both": "人 / AI 同时活动", "idle": "空闲"}.get(state, state)
        paused = ai_paused()
        if state in ("agent", "both"):
            warn = act_box.row()
            warn.alert = True
            warn.label(text="AI 正在操作 Blender" + ("（同时有人操作）" if state == "both" else ""),
                       icon="ERROR")
        else:
            act_box.label(text="活动状态：" + state_text)
        if paused:
            prow = act_box.row()
            prow.alert = True
            prow.label(text="AI 操作已暂停 · 模型请求会被拒绝")
        btn_row = act_box.row()
        btn_row.scale_y = 1.2
        btn_row.operator("dshbb.toggle_ai_pause",
                         text=("继续 AI 操作" if paused else "暂停 AI 操作"))
        if activity.get("last_human_kind"):
            act_box.label(text="最近人类动作：" + str(activity.get("last_human_kind")))

    @staticmethod
    def _draw_connection(layout, scene, context):
        # The add-on dials the DSH Gateway; a running server may still be waiting
        # for the Gateway, so "running" is never displayed as "connected".
        box = layout.box()
        col = box.column()
        server = getattr(bpy.types, "dshbb_server", None)
        port = getattr(server, "gateway_port", None) or get_gateway_port()
        if scene.dshbb_server_running and server is not None:
            if getattr(server, "connected", False):
                col.label(text="Connected to DSH Gateway", icon="CHECKMARK")
                col.label(text=gateway_url(port))
            else:
                col.label(text="Waiting for DSH Gateway", icon="URL")
                col.label(text=gateway_url(port))
                handshake_error = getattr(server, "handshake_error", "")
                if handshake_error:
                    col.label(text=str(handshake_error)[:120], icon="ERROR")
            col.operator("dshbb.stop_server", text="Disconnect from DSH", icon="X")
        else:
            col.label(text="Not connected to the DSH Gateway", icon="RADIOBUT_OFF")
            preferences = get_dshbb_addon_preferences(context)
            if preferences is not None:
                col.prop(preferences, "gateway_port")
            else:
                col.label(text="Gateway port: %d" % port)
            col.operator("dshbb.start_server", text="Connect to DSH", icon="PLAY")

    def _draw_selection(self, layout, scene, activity):
        # v1.8 · Selection → DSH（在 Blender 原生选区里选，按按钮进 DSH 下一条消息）
        layout.separator()
        layout.label(text="Selection → DSH")
        sel_box = layout.box()
        selection = activity.get("selection") or {}
        sel_count = int(selection.get("count") or 0)
        sel_names = list(selection.get("names") or [])
        mode = str(getattr(scene, "dshbb_selection_mode", "MULTI") or "MULTI")
        mode_row = sel_box.row(align=True)
        mode_row.prop(scene, "dshbb_selection_mode", expand=True)
        sel_col = sel_box.column(align=True)
        if mode == "COLLECTION":
            self._draw_collection_selection(sel_box, sel_col, scene)
        else:
            self._draw_multi_selection(sel_box, sel_col, scene, selection, sel_count, sel_names)
        self._draw_selection_status(sel_box)
        self._draw_advanced(sel_box, scene)

    @staticmethod
    def _draw_collection_selection(sel_box, sel_col, scene):
        coll_row = sel_box.row(align=True)
        coll_row.prop(scene, "dshbb_collection_target", text="集合")
        coll_row.operator("dshbb.use_selection_collection", text="", icon="FILE_REFRESH")
        target_coll = getattr(scene, "dshbb_collection_target", None)
        try:
            selected_names = {obj.name for obj in (bpy.context.selected_objects or [])}
        except Exception:
            selected_names = set()
        if target_coll is not None:
            try:
                members = list(target_coll.objects)
            except Exception:
                members = []
            selected_in_coll = sum(1 for obj in members if obj.name in selected_names)
            sel_col.label(text="集合「%s」：%d 件（当前选中 %d 件）—— 发送整个集合"
                               % (target_coll.name, len(members), selected_in_coll), icon="OUTLINER_COLLECTION")
            # 成员列表：可折叠；展开后是 UIList（内容多时自带滚动条），高度用「高度（行）」滑杆调
            list_open = bool(getattr(scene, "dshbb_show_collection_list", False))
            list_row = sel_box.row()
            list_row.prop(scene, "dshbb_show_collection_list",
                          text="成员列表（%d）" % len(members), emboss=False,
                          icon="TRIA_DOWN" if list_open else "TRIA_RIGHT")
            if list_open:
                sel_box.prop(scene, "dshbb_list_rows", text="高度（行）", slider=True)
                ctrl = sel_box.row(align=True)
                ctrl.operator("dshbb.list_rows_dec", text="− 矮一点")
                ctrl.operator("dshbb.list_rows_inc", text="+ 高一点")
                sel_box.template_list("DSHBB_UL_ObjectList", "dshbb_collection",
                                      scene, "dshbb_collection_items",
                                      scene, "dshbb_collection_index",
                                      rows=int(getattr(scene, "dshbb_list_rows", 8)), maxrows=30)
                if len(members) > LIST_UI_CAP:
                    sel_box.label(text="列表只显示前 %d 件（发送不受影响）" % LIST_UI_CAP, icon="INFO")
                if not members:
                    sel_box.label(text="集合是空的", icon="INFO")
            try:
                children = list(target_coll.children)
            except Exception:
                children = []
            if children:
                sel_box.label(text="子集合 %d 个：%s"
                                   % (len(children), "、".join(c.name for c in children[:4])), icon="OUTLINER_COLLECTION")
        else:
            sel_col.label(text="未选择集合：在上面的下拉里选一个", icon="INFO")
        send_row = sel_box.row()
        send_row.scale_y = 1.2
        send_row.enabled = target_coll is not None
        send_row.operator("dshbb.send_collection_to_dsh", text="发送集合到 DSH", icon="EXPORT")

    @staticmethod
    def _draw_multi_selection(sel_box, sel_col, scene, selection, sel_count, sel_names):
        if not sel_count:
            sel_col.label(text="未选中物体：在视口里选中后再发送", icon="INFO")
            send_row = sel_box.row()
            send_row.scale_y = 1.2
            send_row.enabled = False
            send_row.operator("dshbb.send_selection_to_dsh", text="发送选中到 DSH", icon="EXPORT")
            return
        try:
            sub_mode = str(getattr(bpy.context, "mode", "") or "")
        except Exception:
            sub_mode = ""
        if sub_mode and sub_mode != "OBJECT":
            try:
                sub_obj = getattr(bpy.context, "edit_object", None) or getattr(bpy.context, "active_object", None)
            except Exception:
                sub_obj = None
            if sub_obj is not None:
                sel_col.label(text="子元素：" + sub_mode_label(sub_mode) + " · " + sub_obj.name,
                              icon="EDITMODE_HLT")
        shown = "、".join(sel_names[:3])
        if sel_count > 3 or selection.get("truncated"):
            shown += "…"
        sel_col.label(text="已选 %d 件：%s" % (sel_count, shown), icon="OBJECT_DATA")
        if selection.get("active"):
            sel_col.label(text="活动对象：%s · 模式：%s"
                               % (selection.get("active"), selection.get("mode") or "?"))
        if sel_count > 200:
            sel_col.label(text="件数过多：切到「集合」模式整组发送更快", icon="INFO")
        try:
            selected_objs = list(bpy.context.selected_objects or [])
        except Exception:
            selected_objs = []
        list_open = bool(getattr(scene, "dshbb_show_selection_list", False))
        list_row = sel_box.row()
        list_row.prop(scene, "dshbb_show_selection_list",
                      text="对象 / 子元素列表（%d）" % len(selected_objs), emboss=False,
                      icon="TRIA_DOWN" if list_open else "TRIA_RIGHT")
        if list_open:
            sel_box.prop(scene, "dshbb_list_rows", text="高度（行）", slider=True)
            ctrl = sel_box.row(align=True)
            ctrl.operator("dshbb.list_rows_dec", text="− 矮一点")
            ctrl.operator("dshbb.list_rows_inc", text="+ 高一点")
            sel_box.template_list("DSHBB_UL_ObjectList", "dshbb_selection",
                                  scene, "dshbb_selection_items",
                                  scene, "dshbb_selection_index",
                                  rows=int(getattr(scene, "dshbb_list_rows", 8)), maxrows=30)
            if len(selected_objs) > LIST_UI_CAP:
                sel_box.label(text="列表只显示前 %d 件（发送不受影响）" % LIST_UI_CAP, icon="INFO")
        send_row = sel_box.row()
        send_row.scale_y = 1.2
        send_row.enabled = True
        send_row.operator("dshbb.send_selection_to_dsh", text="发送选中到 DSH", icon="EXPORT")
        if 1 < sel_count <= 200:
            try:
                selected_now = list(bpy.context.selected_objects or [])
            except Exception:
                selected_now = []
            colls = selection_collection_summary(selected_now, cap=4)
            if colls:
                sel_col.label(text="选中涉及集合：" + "、".join(
                    "%s(%d)" % (c["name"], c["count"]) for c in colls), icon="OUTLINER_COLLECTION")

    @staticmethod
    def _draw_selection_status(sel_box):
        send_state = selection_commands._selection_send_state
        if send_state:
            if send_state.get("ok"):
                sel_box.label(text="最近发送：成功（seq %s）" % send_state.get("seq"), icon="CHECKMARK")
            else:
                sel_box.label(text="最近发送失败：" + str(send_state.get("error") or "unknown")[:120], icon="ERROR")

    @staticmethod
    def _draw_advanced(sel_box, scene):
        # 后端地址 / 覆盖项：折叠（排错时才展开）
        adv_open = bool(getattr(scene, "dshbb_show_advanced", False))
        adv_row = sel_box.row()
        adv_row.prop(scene, "dshbb_show_advanced", text="后端 / 高级", emboss=False,
                     icon="TRIA_DOWN" if adv_open else "TRIA_RIGHT")
        if adv_open:
            sel_box.label(text="DSH 后端：" + dshbb_backend_url(), icon="URL")
            sel_box.prop(scene, "dshbb_backend_url", text="覆盖后端地址（可留空）")

