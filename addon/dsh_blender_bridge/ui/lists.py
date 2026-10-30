""" Selection lists and list refresh timer. """

import bpy
import time
import os

from ..compat import _read_sub_selection
from ..protocol import _redraw_view3d_panels
from ..recorders.activity import get_activity_tracker

LIST_UI_CAP = 500

_LAST_UI_STATE_KEY = {"key": None}


class DSHBB_SelectionItem(bpy.types.PropertyGroup):
    obj_name: bpy.props.StringProperty(name="Name", default="")
    obj_type: bpy.props.StringProperty(name="Type", default="")


class DSHBB_UL_ObjectList(bpy.types.UIList):
    bl_idname = "DSHBB_UL_ObjectList"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=getattr(item, "obj_name", "") or "<unnamed>", icon='OBJECT_DATA')
        row.label(text=getattr(item, "obj_type", "") or "?")


def _sub_mode_label(mode):
    labels = {
        "EDIT_MESH": "编辑模式（网格）",
        "EDIT_ARMATURE": "编辑模式（骨架）",
        "POSE": "姿态模式",
        "EDIT_CURVE": "编辑模式（曲线）",
        "EDIT_SURFACE": "编辑模式（曲面）",
        "EDIT_LATTICE": "编辑模式（晶格）",
        "EDIT_TEXT": "编辑模式（文本）",
        "PAINT_GPENCIL": "绘制模式（蜡笔）",
        "EDIT_GPENCIL": "编辑模式（蜡笔）",
    }
    return labels.get(str(mode), str(mode))


def _sub_selection_entries(sub):
    """把子元素选择变成 UIList 行：对象行 + 每个元素一行（标签, 短类型）。"""
    if not sub:
        return []
    entries = []
    # 对象行由 _selection_list_entries 的 selected_objects 部分负责，这里只加子元素行
    if str(sub.get("type")) == "MESH":
        idx = sub.get("indices") or {}
        for i in (idx.get("vertices") or [])[:LIST_UI_CAP // 3]:
            entries.append(("顶点 %s" % i, "VERT"))
        for pair in (idx.get("edges") or [])[:LIST_UI_CAP // 3]:
            entries.append(("边 %s" % pair, "EDGE"))
        for i in (idx.get("faces") or [])[:LIST_UI_CAP // 3]:
            entries.append(("面 %s" % i, "FACE"))
    elif str(sub.get("type")) == "ARMATURE":
        for name in (sub.get("bones") or [])[:LIST_UI_CAP]:
            entries.append((str(name), "BONE"))
    elif str(sub.get("type")) in ("CURVE", "LATTICE"):
        for pair in (sub.get("points") or [])[:LIST_UI_CAP]:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                entries.append(("%s : %s" % (pair[0], pair[1]), "POINT"))
    return entries[:LIST_UI_CAP]


def _selection_list_entries():
    entries = [(obj.name, str(getattr(obj, "type", "?"))) for obj in (bpy.context.selected_objects or [])[:LIST_UI_CAP]]
    sub = _read_sub_selection()
    if sub is not None:
        entries.extend(_sub_selection_entries(sub))
    return entries[:LIST_UI_CAP]


def _refresh_selection_list(scene, context=None):
    try:
        _sync_ui_list(scene.dshbb_selection_items, _selection_list_entries())
    except Exception:
        pass


def _refresh_collection_list(scene, context=None):
    try:
        coll = getattr(scene, "dshbb_collection_target", None)
        entries = []
        if coll is not None:
            entries = [(obj.name, str(getattr(obj, "type", "?"))) for obj in list(coll.objects)[:LIST_UI_CAP]]
        _sync_ui_list(scene.dshbb_collection_items, entries)
    except Exception:
        pass


def _on_show_selection_list_update(self, context):
    _refresh_selection_list(self, context)


def _on_show_collection_list_update(self, context):
    _refresh_collection_list(self, context)


def _dshbb_ui_list_timer():
    """列表打开时保持内容与场景同步；活动状态/暂停状态变化时重绘 N 面板。"""
    try:
        scene = bpy.context.scene
        if scene is not None:
            if getattr(scene, "dshbb_show_selection_list", False):
                _refresh_selection_list(scene)
            if getattr(scene, "dshbb_show_collection_list", False):
                _refresh_collection_list(scene)
            try:
                snap = get_activity_tracker().snapshot(include_current_command=False)
                key = (snap.get("state"), bool(getattr(scene, "dshbb_ai_paused", False)))
                if key != _LAST_UI_STATE_KEY["key"]:
                    _LAST_UI_STATE_KEY["key"] = key
                    _redraw_view3d_panels()
            except Exception:
                pass
    except Exception:
        pass
    return 0.5


def _sync_ui_list(collection, entries):
    """把 [(name, type)] 同步进 PropertyGroup 集合；签名相同就跳过（避免每次重绘都重建）。"""
    if len(collection) == len(entries):
        same = True
        for i, (name, typ) in enumerate(entries):
            item = collection[i]
            if item.obj_name != name or item.obj_type != typ:
                same = False
                break
        if same:
            return
    collection.clear()
    for name, typ in entries:
        item = collection.add()
        item.obj_name = name
        item.obj_type = typ


# 面板绘制异常记录：写场景属性 + 日志文件（用户不方便看控制台时也能诊断）
def _log_panel_error(tb):
    import traceback
    text = str(tb)[-4000:]
    try:
        p = os.path.join("D:/DSH/blender/tmp", "dshbb-panel-error.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n")
    except Exception:
        pass
    try:
        p2 = os.path.join(bpy.app.tempdir or "", "dshbb-panel-error.log")
        with open(p2, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n")
    except Exception:
        pass
