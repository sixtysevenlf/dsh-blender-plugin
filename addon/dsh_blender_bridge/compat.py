""" Blender version and selection payload compatibility helpers. """

import bpy

SELECTION_PAYLOAD_NAMES = 200
SELECTION_PAYLOAD_OBJECTS = 50
SELECTION_COLLECTION_ROWS = 50


SUB_SELECTION_CAP = 2000


def _cap_selection(values, cap=SUB_SELECTION_CAP):
    values = list(values)
    return values[:cap], len(values) > cap


def _read_sub_selection():
    """返回当前编辑/姿态模式下的子元素选择；对象模式返回 None。

    只在需要时调用（发送 / get_selection / 列表展开），不在 activity tick 里轮询 ——
    大网格上枚举全部顶点可能很慢。所有数组都夹到 SUB_SELECTION_CAP，并如实带 truncated。
    """
    mode = str(getattr(bpy.context, "mode", "OBJECT") or "OBJECT")
    if mode == "OBJECT":
        return None
    edit_obj = getattr(bpy.context, "edit_object", None)
    active_obj = getattr(bpy.context, "active_object", None)

    # ---- 网格：顶点 / 边 / 面 ----
    if mode == "EDIT_MESH":
        obj = edit_obj
        if obj is None:
            return None
        try:
            import bmesh
            bm = bmesh.from_edit_mesh(obj.data)
            verts = [v.index for v in bm.verts if v.select]
            edges = [e.index for e in bm.edges if e.select]
            faces = [f.index for f in bm.faces if f.select]
        except Exception as exc:
            return {"type": "MESH", "object": obj.name, "mode": mode,
                    "note": "读取选择失败：" + str(exc), "counts": {}, "indices": {}, "truncated": False}
        try:
            sm = tuple(bool(x) for x in bpy.context.tool_settings.mesh_select_mode)
        except Exception:
            sm = (False, False, False)
        v_cap, v_tr = _cap_selection(verts)
        e_cap, e_tr = _cap_selection(edges)
        f_cap, f_tr = _cap_selection(faces)
        return {"type": "MESH", "object": obj.name, "mode": mode,
                "select_mode": {"vertex": sm[0], "edge": sm[1], "face": sm[2]},
                "counts": {"vertices": len(verts), "edges": len(edges), "faces": len(faces)},
                "indices": {"vertices": v_cap, "edges": e_cap, "faces": f_cap},
                "truncated": bool(v_tr or e_tr or f_tr)}

    # ---- 骨架：编辑骨骼 / 姿态骨骼 ----
    if mode in ("EDIT_ARMATURE", "POSE"):
        obj = active_obj if mode == "POSE" else (edit_obj or active_obj)
        if obj is None or getattr(obj, "type", "") != "ARMATURE":
            return None
        try:
            if mode == "POSE":
                # 姿态模式的选中标志在 PoseBone.select（Bone 本身没有 select）
                names = [pb.name for pb in obj.pose.bones if bool(getattr(pb, "select", False))]
            else:
                names = [eb.name for eb in obj.data.edit_bones if bool(getattr(eb, "select", False))]
        except Exception as exc:
            return {"type": "ARMATURE", "object": obj.name, "mode": mode,
                    "note": "读取骨骼失败：" + str(exc), "counts": {}, "bones": [], "truncated": False}
        cap, tr = _cap_selection(names)
        return {"type": "ARMATURE", "object": obj.name, "mode": mode,
                "counts": {"bones": len(names)}, "bones": cap, "truncated": bool(tr)}

    # ---- 曲线 / 曲面：控制点（spline_index, point_index）----
    if mode in ("EDIT_CURVE", "EDIT_SURFACE"):
        obj = edit_obj or active_obj
        if obj is None:
            return None
        points = []
        total = 0
        try:
            for si, spline in enumerate(obj.data.splines):
                if str(getattr(spline, "type", "")) == "BEZIER":
                    for pi, bp in enumerate(spline.bezier_points):
                        if bool(getattr(bp, "select_control_point", False)):
                            total += 1
                            if len(points) < SUB_SELECTION_CAP:
                                points.append([si, pi])
                else:
                    for pi, pt in enumerate(getattr(spline, "points", [])):
                        if bool(getattr(pt, "select", False)):
                            total += 1
                            if len(points) < SUB_SELECTION_CAP:
                                points.append([si, pi])
        except Exception as exc:
            return {"type": "CURVE", "object": obj.name, "mode": mode,
                    "note": "读取曲线点失败：" + str(exc), "counts": {}, "points": [], "truncated": False}
        return {"type": "CURVE", "object": obj.name, "mode": mode,
                "counts": {"points": total}, "points": points, "truncated": total > len(points)}

    # ---- 晶格：控制点 ----
    if mode == "EDIT_LATTICE":
        obj = edit_obj or active_obj
        if obj is None:
            return None
        try:
            points = [i for i, pt in enumerate(obj.data.points) if bool(getattr(pt, "select", False))]
        except Exception as exc:
            return {"type": "LATTICE", "object": obj.name, "mode": mode,
                    "note": "读取晶格点失败：" + str(exc), "counts": {}, "points": [], "truncated": False}
        cap, tr = _cap_selection(points)
        return {"type": "LATTICE", "object": obj.name, "mode": mode,
                "counts": {"points": len(points)}, "points": cap, "truncated": bool(tr)}

    # ---- 其他编辑模式（文本 / 蜡笔等）：如实说明暂无子元素展开 ----
    if mode.startswith("EDIT_") or mode == "PAINT_GPENCIL":
        obj = edit_obj or active_obj
        return {"type": "OTHER", "object": getattr(obj, "name", None), "mode": mode,
                "note": "该编辑模式的子元素暂未展开；可让模型用 Python 直接读选择集",
                "counts": {}, "truncated": False}
    return None


ADDON_MODULE = __package__


def bpy_version():
    try:
        return tuple(bpy.app.version)
    except Exception:
        return (0, 0, 0)


def is_bpy_at_least(major, minor=0, patch=0):
    version = bpy_version()
    return version >= (major, minor, patch)
