# -*- coding: utf-8 -*-
"""Host-integration primitives (/host/*) split out of view.py.

These three are user-side capabilities (a click in the viewport becomes part of
the user's own message), not model tools: they are absent from the /plan op
catalog and do not change the add-on contract. The math reuses the same
make_view_matrix / make_proj_matrix / write_png paths as view.py, bound once by
view.py so this module never grows a second implementation.
"""

import json
import os
import time

# 点击兜底半径（按**出图**像素算）：灯光/相机/空物体没有可命中的表面，
# ray_cast 打不到，只能按"它在画面上的位置离点击点多近"兜底（对齐上游 addon.py 的 _PICK_RADIUS_PX）。
HOST_PICK_RADIUS_PX = 22.0

_DEPS = {}


def bind(**deps):
    """Wire the helpers view.py owns; called once when view.py loads."""
    _DEPS.update(deps)


def _dumps(value):
    return _DEPS["dumps"](value)


def _default_path():
    return _DEPS["default_path"]


def _find_view3d(area):
    return _DEPS["find_view3d"](area)


def _texture_rgba(buffer, width, height):
    return _DEPS["texture_rgba"](buffer, width, height)


def _write_png(path, width, height, rgba):
    return _DEPS["write_png"](path, width, height, rgba)


def _host_obj_brief(o):
    """pick 命中回执里的对象摘要。键固定 name/type/detail/location（契约 §1.5 的示例形状）。"""
    detail = str(o.type)
    if o.type == "MESH" and getattr(o, "data", None) is not None:
        try:
            detail = "MESH %dv/%df" % (len(o.data.vertices), len(o.data.polygons))
        except Exception:
            pass
    loc = o.matrix_world.translation
    return {"name": str(o.name), "type": str(o.type), "detail": detail,
            "location": [round(float(loc.x), 6), round(float(loc.y), 6), round(float(loc.z), 6)]}


def host_capture(spec_json):
    """抓**当前 GUI 视口**那一帧（矩阵直接取 region_3d），返回 PNG + 矩阵 + origin 信息。

    与 capture() 的分工：
      capture()      = "从任意位置看一眼"（自建矩阵，不动用户视口）—— 模型侧 blender_see 用
      host_capture() = "用户现在看到的就是这一帧"（region_3d 自己的 view/window 矩阵）—— 宿主面板用
    零场景改动、零视口改动（不改 shading/overlays/选择/相机），只读 GPU 离屏缓冲。

    ⚠ 拿不到矩阵就 **ok=false**（让上层把 pickable 置 false），绝不编一组矩阵冒充能点选。
    """
    import bpy

    s = json.loads(spec_json) if isinstance(spec_json, str) else dict(spec_json or {})
    try:
        max_size = int(s.get("max_size") or 1000)
    except Exception:
        max_size = 1000
    max_size = max(200, min(2000, max_size))
    path = str(s.get("path") or _default_path())
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    t0 = time.perf_counter()
    win, area, reg, space = _find_view3d(s.get("area"))
    r3d = getattr(space, "region_3d", None)
    if r3d is None:
        return _dumps({"ok": False, "error": "视口没有 region_3d：拿不到相机矩阵，不能假装能点选"})
    src_w, src_h = int(reg.width), int(reg.height)
    if max(src_w, src_h) > max_size:
        k = float(max_size) / float(max(src_w, src_h))
        w, h = max(1, int(src_w * k)), max(1, int(src_h * k))
    else:
        w, h = src_w, src_h
    vm = r3d.view_matrix.copy()
    pm = r3d.window_matrix.copy()
    import gpu
    try:
        gpu.init()          # 后台/未初始化时必需；GUI 下已初始化，抛错忽略
    except Exception:
        pass
    off = gpu.types.GPUOffScreen(w, h)
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.05, 0.05, 0.05, 1.0), depth=1.0)
            # 画的就是用户视口的样子（do_color_management=True → write_png 直写字节，不做二次 gamma）
            off.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, reg, vm, pm,
                            do_color_management=True, draw_background=True)
            rgba = _texture_rgba(off.texture_color.read(), w, h)
    finally:
        try:
            off.free()
        except Exception:
            pass
    n = _write_png(path, w, h, rgba)
    return _dumps({"ok": True, "path": path, "bytes": int(n), "width": int(w), "height": int(h),
                   "view_matrix": [[round(float(v), 9) for v in row] for row in vm],
                   "window_matrix": [[round(float(v), 9) for v in row] for row in pm],
                   "file": str(bpy.data.filepath or ""), "scene": str(bpy.context.scene.name),
                   "scene_count": int(len(bpy.data.scenes)),
                   "region": [int(reg.x), int(reg.y), int(reg.width), int(reg.height)],
                   "perspective": bool(r3d.is_perspective), "method": "region_3d/offscreen",
                   "ms": int((time.perf_counter() - t0) * 1000)})


def host_pick(spec_json):
    """点击反投影：归一化点击坐标（0..1，左上原点、y 向下）→ 世界射线 → ray_cast 命中对象。

    **纪律（契约 §1.5）**：先校验 file/scene 错配，错配就回 `*_mismatch`；
    绝不把点击"投到当前场景"—— 那是把用户的一次点击变成一次误操作。
    """
    import bpy
    from mathutils import Matrix, Vector

    s = json.loads(spec_json) if isinstance(spec_json, str) else dict(spec_json or {})
    try:
        x = min(1.0, max(0.0, float(s.get("x", 0.5))))
        y = min(1.0, max(0.0, float(s.get("y", 0.5))))
    except Exception:
        x, y = 0.5, 0.5
    exp_file = "" if s.get("file") is None else str(s.get("file"))
    exp_scene = "" if s.get("scene") is None else str(s.get("scene"))
    cur_file = str(bpy.data.filepath or "")
    cur_scene = str(bpy.context.scene.name)
    if exp_file != cur_file:
        return _dumps({"ok": False, "error": "file_mismatch",
                       "expected": (exp_file or None), "current": (cur_file or None)})
    if exp_scene != cur_scene:
        return _dumps({"ok": False, "error": "scene_mismatch",
                       "expected": (exp_scene or None), "current": (cur_scene or None)})
    try:
        vm = Matrix([[float(v) for v in row] for row in s["view_matrix"]])
        pm = Matrix([[float(v) for v in row] for row in s["window_matrix"]])
    except Exception as e:
        return _dumps({"ok": False, "error": "unknown_seq", "why": "矩阵不可用: %s" % str(e)[:120]})
    try:
        w = float(s.get("width") or 0.0)
        h = float(s.get("height") or 0.0)
    except Exception:
        w = h = 0.0
    if w <= 0 or h <= 0:
        return _dumps({"ok": False, "error": "unknown_seq", "why": "缺 width/height"})
    vpm = pm @ vm
    try:
        inv = vpm.inverted()
    except Exception:
        return _dumps({"ok": False, "error": "unknown_seq", "why": "view@proj 不可逆"})
    ndc_x = 2.0 * x - 1.0
    ndc_y = 1.0 - 2.0 * y              # 图像 y 向下 → NDC y 向上
    p0 = inv @ Vector((ndc_x, ndc_y, -1.0, 1.0))
    p1 = inv @ Vector((ndc_x, ndc_y, 1.0, 1.0))
    if abs(p0.w) < 1e-12 or abs(p1.w) < 1e-12:
        return _dumps({"ok": False, "error": "unknown_seq", "why": "齐次 w≈0"})
    p0 = p0 / p0.w
    p1 = p1 / p1.w
    origin = Vector((p0.x, p0.y, p0.z))
    direction = Vector((p1.x - p0.x, p1.y - p0.y, p1.z - p0.z))
    if direction.length < 1e-12:
        return _dumps({"ok": False, "error": "unknown_seq", "why": "射线方向退化"})
    direction.normalize()
    scene = bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    hit, loc, nrm, idx, obj, mtx = scene.ray_cast(dg, origin, direction)
    if hit and obj is not None:
        try:                              # 视口里看不见的东西不该被点到
            if obj.hide_viewport or not obj.visible_get():
                obj = None
        except Exception:
            pass
    if obj is not None:
        return _dumps({"ok": True, "object": _host_obj_brief(obj)})
    # 兜底：灯光/相机/空物体没有表面可命中，按屏幕距离取最近的一个
    best, best_d = None, None
    for o in scene.objects:
        if o.type not in ("LIGHT", "CAMERA", "EMPTY", "SPEAKER", "LIGHT_PROBE"):
            continue
        try:
            if o.hide_viewport or not o.visible_get():
                continue
        except Exception:
            pass
        p = o.matrix_world.translation
        clip = vpm @ Vector((p.x, p.y, p.z, 1.0))
        if clip.w <= 1e-9:
            continue
        sx = (clip.x / clip.w + 1.0) * 0.5 * w
        sy = (1.0 - clip.y / clip.w) * 0.5 * h
        dd = ((sx - x * w) ** 2 + (sy - y * h) ** 2) ** 0.5
        if dd <= HOST_PICK_RADIUS_PX and (best_d is None or dd < best_d):
            best, best_d = o, dd
    if best is not None:
        return _dumps({"ok": True, "object": _host_obj_brief(best)})
    return _dumps({"ok": True, "object": None})


def host_scene_items(spec_json):
    """`@` 提及的数据源（契约 §1.6）：objects / materials / collections，按 name 升序（稳定）。

    q 大小写不敏感子串过滤（空 = 全部）；limit **每类**各取这么多（默认 200、夹到 [1,1000]）。
    对象取 `bpy.data.objects`（不是 scene.objects）—— 契约要求"名字在 bpy.data.objects 里可直接用"。
    """
    import bpy

    s = json.loads(spec_json) if isinstance(spec_json, str) else dict(spec_json or {})
    q = str(s.get("q") or "").lower()
    # 口径与 Node 侧 clampItemsLimit 一致：缺省/非数 → 200；给了数 → 夹进 [1,1000]
    # （显式 limit=0 → 1，不是 200；契约 §1.6 写的是"夹到 [1,1000]"）
    lim = s.get("limit")
    if lim is None or lim == "":
        limit = 200
    else:
        try:
            limit = int(lim)
        except Exception:
            limit = 200
    limit = max(1, min(1000, limit))

    def hit(name):
        return (q == "") or (q in str(name).lower())

    objects = sorted([{"name": str(o.name), "type": str(o.type)} for o in bpy.data.objects if hit(o.name)],
                     key=lambda r: r["name"])[:limit]
    materials = sorted([{"name": str(m.name)} for m in bpy.data.materials if hit(m.name)],
                       key=lambda r: r["name"])[:limit]
    collections = sorted([{"name": str(c.name)} for c in bpy.data.collections if hit(c.name)],
                         key=lambda r: r["name"])[:limit]
    return _dumps({"ok": True, "objects": objects, "materials": materials, "collections": collections})
