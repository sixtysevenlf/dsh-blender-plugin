"""DSH Blender 自定义视角捕获 —— 不动物体、不动场景相机，按给定相机参数出图。

通过 blender_rt_see {from, look_at, ...} 调用；API 挂在 K.dsh_view_api。

解决什么：
  get_viewport_screenshot 只能出"当前用户视口"那一个角度（要换角度就得改用户视口）。
  本模块用自建 view/projection 矩阵 + GPUOffScreen.draw_view3d 离屏绘制，
  可以在**完全不修改场景与用户视口**的前提下，从任意位置/朝向/焦距出一帧。

数学（已与真相机对拍验证，误差 ~1e-7，见 selftest_matrices）：
  view = (Translation(from) @ dir.to_track_quat('-Z','Y').to_matrix().to_4x4()).inverted()
  proj:  AUTO 时 sensor 贴长边；
         hx = sensor/2（长边为宽时），hy = hx*h/w
         p00 = lens/hx, p11 = lens/hy
         这套与 bpy.types.Camera.calc_matrix_camera() 输出逐元素一致。
  正交同理：p00 = 2/x_extent, p11 = 2/y_extent。

两种模式：
  viewport（默认）  GPUOffScreen.draw_view3d —— 就是用户视口渲染出来的样子（含着色/覆盖物设置），零场景改动
  render           临时相机 + bpy.ops.render.opengl（Workbench/EEVEE 快渲），用完彻底还原
"""
import bpy, json, math, time, os, struct, zlib, tempfile

VIEW_VERSION = 3

# 近空帧的 PNG 字节阈值（v0.9.4 · P0-1）：**低于它才自动**跑一次诊断三项。
# 为什么要这个阈值：诊断（coverage / scene_bbox / objects_in_frame）实测 403 物体 560px 要 63 ms、
# 1120px 137 ms、2240px 488 ms，而真正的绘制 + 显存读回只有 ~22 ms ⇒ 正常帧不该每次白付。
# 但"瞄空"的告警不能丢：近空帧的 PNG 会压得极小（实测本机带网格线的一帧 560×315 = 37,908 B），
# 所以用字节数当机械判据，近空帧自动补跑诊断 → frame_looks_empty 照旧触发。
EMPTY_PNG_BYTES = 8000

# 默认输出到 Blender 本机临时目录（分享版不假设固定路径；正常调用时引擎会传
# path = 配置里的工作目录，这里只是兜底）。
WIN_DEFAULT = os.path.join(tempfile.gettempdir(), "dsh_view_capture.png")


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("view 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


# ---------------------------------------------------------------- 矩阵

def make_view_matrix(frm, look_at, up=(0.0, 0.0, 1.0)):
    """相机空间 → 与 cam.matrix_world.inverted() 一致（-Z 朝视线，Y 朝上）。"""
    from mathutils import Vector, Matrix
    frm = Vector(frm[:3])
    look = Vector(look_at[:3])
    d = look - frm
    if d.length < 1e-9:
        d = Vector((0.0, -1.0, 0.0))
    d.normalize()
    q = d.to_track_quat("-Z", "Y")
    return (Matrix.Translation(frm) @ q.to_matrix().to_4x4()).inverted()


def make_proj_matrix(w, h, lens=50.0, sensor=36.0, clip_start=0.1, clip_end=1000.0,
                     sensor_fit="AUTO", ortho=False, ortho_scale=1.0, shift_x=0.0, shift_y=0.0):
    """与 bpy.types.Camera.calc_matrix_camera() 逐元素一致（landscape/portrait/ortho 均已对拍）。"""
    from mathutils import Matrix
    w = float(max(1, w)); h = float(max(1, h))
    fit = ("H" if w >= h else "V") if str(sensor_fit).upper().startswith("AUTO") else str(sensor_fit)[0].upper()
    if fit == "H":
        hx = sensor / 2.0
        hy = hx * (h / w)
    else:
        hy = sensor / 2.0
        hx = hy * (w / h)
    cs = float(clip_start); ce = float(clip_end)
    if ortho:
        sx = float(ortho_scale)
        x_extent = sx if fit == "H" else sx * (w / h)
        y_extent = sx * (h / w) if fit == "H" else sx
        p00 = 2.0 / x_extent; p11 = 2.0 / y_extent
        return Matrix(((p00, 0.0, 2.0 * float(shift_x), 0.0),
                       (0.0, p11, 2.0 * float(shift_y), 0.0),
                       (0.0, 0.0, -2.0 / (ce - cs), -(ce + cs) / (ce - cs)),
                       (0.0, 0.0, 0.0, 1.0)))
    p00 = lens / hx; p11 = lens / hy
    return Matrix(((p00, 0.0, 2.0 * float(shift_x), 0.0),
                   (0.0, p11, 2.0 * float(shift_y), 0.0),
                   (0.0, 0.0, -(ce + cs) / (ce - cs), -(2.0 * ce * cs) / (ce - cs)),
                   (0.0, 0.0, -1.0, 0.0)))


# ---------------------------------------------------------------- PNG 直写

def write_png(path, w, h, rgba, flip=True):
    """rgba: 顶层→底层顺序的 RGBA8 字节流（len = w*h*4）。

    直接写 PNG（不经过 bpy.data.images）的原因：GPU 帧缓冲读回来的是**已经 sRGB 编码**的 8bit 值，
    走 image.pixels（线性语义）再保存会被二次 gamma 编码。直写字节 = 所见即所得，且省掉一次色彩空间猜测。
    """
    raw = bytearray()
    stride = w * 4
    rng = range(h - 1, -1, -1) if flip else range(h)
    for y in rng:
        raw.append(0)  # filter type 0
        raw += rgba[y * stride:(y + 1) * stride]

    def _chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


# ---------------------------------------------------------------- 视口定位

def _find_view3d(area_index=None):
    """返回 (window, area, region, space_view3d)；找不到 3D 视口时报错说明。"""
    wm = bpy.context.window_manager
    cands = []
    for win in wm.windows:
        scr = win.screen
        if scr is None:
            continue
        for a in scr.areas:
            if a.type == "VIEW_3D":
                cands.append((win, a))
    if not cands:
        raise RuntimeError("没有找到 VIEW_3D 区域：需要在 GUI 模式下打开一个 3D 视口（后台 -b 模式无法用它）")
    win, area = cands[0] if area_index is None else cands[min(max(0, int(area_index)), len(cands) - 1)]
    reg = next((r for r in area.regions if r.type == "WINDOW"), None)
    if reg is None:
        raise RuntimeError("VIEW_3D 区域没有 WINDOW 子区域")
    return win, area, reg, area.spaces.active


def _spec(spec):
    s = dict(spec or {})
    frm = s.get("from") or s.get("loc") or s.get("location") or [7.0, -7.0, 5.0]
    look = s.get("look_at") or s.get("target") or s.get("to") or [0.0, 0.0, 1.0]
    w = int(s.get("width") or (s.get("resolution") or [960, 540])[0])
    h = int(s.get("height") or (s.get("resolution") or [960, 540])[1])
    return {
        "from": [float(v) for v in frm],
        "look_at": [float(v) for v in look],
        "lens": float(s.get("lens", 50.0)),
        "sensor": float(s.get("sensor_width", s.get("sensor", 36.0))),
        "sensor_fit": str(s.get("sensor_fit", "AUTO")),
        "clip_start": float(s.get("clip_start", 0.1)),
        "clip_end": float(s.get("clip_end", 1000.0)),
        "ortho": bool(s.get("ortho", False)),
        "ortho_scale": float(s.get("ortho_scale", 10.0)),
        "shift_x": float(s.get("shift_x", 0.0)),
        "shift_y": float(s.get("shift_y", 0.0)),
        "width": max(16, min(4096, w)),
        "height": max(16, min(4096, h)),
        "mode": str(s.get("mode", "viewport")),
        "engine": str(s.get("engine", "BLENDER_WORKBENCH")),
        "shading": s.get("shading"),
        "overlays": s.get("overlays"),
        "color_management": bool(s.get("color_management", True)),
        "background": bool(s.get("background", True)),
        "path": str(s.get("path") or WIN_DEFAULT),
        "area": None if s.get("area") is None else int(s.get("area")),
        "clear": s.get("clear", [0.05, 0.05, 0.05, 1.0]),
        # v0.9.4（P0-1）：默认**不跑**诊断三项（省 45–490 ms/次）；true = 强制每次跑
        "diagnostics": bool(s.get("diagnostics", False)),
    }


def matrices_json(spec_json):
    """只算矩阵，不出图（无 GUI 也能跑，便于对拍/调试）。"""
    s = _spec(json.loads(spec_json) if isinstance(spec_json, str) else spec_json)
    vm = make_view_matrix(s["from"], s["look_at"])
    pm = make_proj_matrix(s["width"], s["height"], s["lens"], s["sensor"], s["clip_start"], s["clip_end"],
                          s["sensor_fit"], s["ortho"], s["ortho_scale"], s["shift_x"], s["shift_y"])
    return _j({
        "ok": True, "width": s["width"], "height": s["height"], "mode": "matrices",
        "view_matrix": [[round(v, 6) for v in row] for row in vm],
        "proj_matrix": [[round(v, 6) for v in row] for row in pm],
        "spec": s,
    })


def targets():
    """列出所有 3D 视口（area 索引 → 是否可获得 region/着色模式），供选视角用。"""
    out = []
    wm = bpy.context.window_manager
    i = 0
    for win in wm.windows:
        scr = win.screen
        if scr is None:
            continue
        for a in scr.areas:
            if a.type == "VIEW_3D":
                reg = next((r for r in a.regions if r.type == "WINDOW"), None)
                sp = a.spaces.active
                out.append({"index": i, "window": [win.width, win.height],
                            "area": [a.x, a.y, a.width, a.height],
                            "region": None if reg is None else [reg.x, reg.y, reg.width, reg.height],
                            "shading": getattr(sp.shading, "type", None),
                            "overlays": bool(getattr(sp.overlay, "show_overlays", False)),
                            "is_active": bool(getattr(sp, "region_3d", None) and sp.region_3d.is_perspective is not None)})
                i += 1
    return _j({"ok": True, "count": len(out), "targets": out})


# ---------------------------------------------------------------- 捕获

def _capture_viewport(s):
    import gpu
    win, area, reg, space = _find_view3d(s.get("area"))
    try:
        gpu.init()  # 后台/未初始化时必需；GUI 下已初始化，抛错忽略
    except Exception:
        pass
    vm = make_view_matrix(s["from"], s["look_at"])
    pm = make_proj_matrix(s["width"], s["height"], s["lens"], s["sensor"], s["clip_start"], s["clip_end"],
                          s["sensor_fit"], s["ortho"], s["ortho_scale"], s["shift_x"], s["shift_y"])
    old_shading = None
    old_overlays = None
    try:
        if s["shading"]:
            old_shading = space.shading.type
            space.shading.type = str(s["shading"]).upper()
        if s["overlays"] is not None:
            old_overlays = space.overlay.show_overlays
            space.overlay.show_overlays = bool(s["overlays"])
        off = gpu.types.GPUOffScreen(s["width"], s["height"])
        try:
            with off.bind():
                fb = gpu.state.active_framebuffer_get()
                c = s["clear"]
                fb.clear(color=(float(c[0]), float(c[1]), float(c[2]), float(c[3]) if len(c) > 3 else 1.0), depth=1.0)
                scene = getattr(win, "scene", None) or bpy.context.scene
                vl = getattr(win, "view_layer", None) or bpy.context.view_layer
                off.draw_view3d(scene, vl, space, reg, vm, pm,
                                do_color_management=bool(s["color_management"]),
                                draw_background=bool(s["background"]))
                buf = off.texture_color.read()
                rgba = bytes(memoryview(buf))
        finally:
            try:
                off.free()
            except Exception:
                pass
    finally:
        if old_shading is not None:
            try:
                space.shading.type = old_shading
            except Exception:
                pass
        if old_overlays is not None:
            try:
                space.overlay.show_overlays = old_overlays
            except Exception:
                pass
    return rgba, {"region": [reg.x, reg.y, reg.width, reg.height], "area": [area.x, area.y, area.width, area.height],
                  "shading_used": space.shading.type}


def _capture_render(s):
    """临时相机 + viewport OpenGL 渲染（Workbench/EEVEE 快渲），结束后逐项还原。"""
    from mathutils import Vector
    scene = bpy.context.scene
    vl = bpy.context.view_layer
    cam_data = bpy.data.cameras.new("dsh_view_tmp_cam")
    cam = bpy.data.objects.new("dsh_view_tmp_cam", cam_data)
    saved = {}
    try:
        cam_data.lens = s["lens"]
        cam_data.sensor_width = s["sensor"]
        cam_data.sensor_fit = "AUTO" if s["sensor_fit"].upper().startswith("AUTO") else s["sensor_fit"].upper()
        cam_data.type = "ORTHO" if s["ortho"] else "PERSP"
        if s["ortho"]:
            cam_data.ortho_scale = s["ortho_scale"]
        cam_data.clip_start = s["clip_start"]
        cam_data.clip_end = s["clip_end"]
        cam_data.shift_x = s["shift_x"]
        cam_data.shift_y = s["shift_y"]
        cam.location = Vector(s["from"])
        cam.rotation_euler = (Vector(s["look_at"]) - Vector(s["from"])).to_track_quat("-Z", "Y").to_euler()
        scene.collection.objects.link(cam)
        vl.update()
        r = scene.render
        saved = {"camera": scene.camera, "engine": r.engine, "rx": r.resolution_x, "ry": r.resolution_y,
                 "pct": r.resolution_percentage, "fp": r.filepath, "fmt": r.image_settings.file_format,
                 "color_mode": r.image_settings.color_mode}
        scene.camera = cam
        r.engine = s["engine"]
        r.resolution_x = s["width"]; r.resolution_y = s["height"]; r.resolution_percentage = 100
        r.filepath = s["path"]; r.image_settings.file_format = "PNG"; r.image_settings.color_mode = "RGBA"
        if s["shading"]:
            try:
                scene.display.shading.type = str(s["shading"]).upper()
            except Exception:
                pass
        bpy.ops.render.opengl(write_still=True, view_context=False)
    finally:
        try:
            if saved.get("camera") is None and scene.camera is not None:
                scene.camera = None
            elif saved.get("camera") is not None:
                scene.camera = saved["camera"]
            if saved:
                scene.render.engine = saved["engine"]
                scene.render.resolution_x = saved["rx"]; scene.render.resolution_y = saved["ry"]
                scene.render.resolution_percentage = saved["pct"]
                scene.render.filepath = saved["fp"]
                scene.render.image_settings.file_format = saved["fmt"]
                scene.render.image_settings.color_mode = saved["color_mode"]
        except Exception:
            pass
        try:
            bpy.data.objects.remove(cam, do_unlink=True)
            bpy.data.cameras.remove(cam_data)
        except Exception:
            pass
    return None, {"method": "render.opengl", "engine": s["engine"], "output": s["path"]}


# ---------------------------------------------------------------- v0.9.1（93-D1/D2）：GUI 原语 + 自定义视角自诊断

def _win_path(p):
    """把 WSL 路径换成 Blender（Windows）能用的形态：优先用内核里的 K.win_path。"""
    K = _kernel()
    try:
        if K is not None and hasattr(K, "win_path"):
            return K.win_path(str(p))
    except Exception:
        pass
    return str(p)


def _scene_bbox():
    """当前场景可见 mesh 的世界 bbox + 最长边（给"瞄空"自诊断建议用）。"""
    from mathutils import Vector
    import numpy as np
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and not (o.hide_render or o.hide_viewport)]
    if not meshes:
        return None
    pts = []
    for o in meshes:
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            pts.append((p.x, p.y, p.z))
    A = np.asarray(pts, dtype=float)
    mn, mx = A.min(axis=0), A.max(axis=0)
    size = mx - mn
    return {"min": [round(float(x), 4) for x in mn], "max": [round(float(x), 4) for x in mx],
            "center": [round(float(x), 4) for x in (mn + mx) / 2.0],
            "span": round(float(size.max()), 4), "objects": len(meshes)}


def _coverage(rgba, w, h, clear=None, stride=4):
    """画面里"非底色像素"占比（0..1）——判"自定义视角是不是瞄空了"。子采样（约 1/16 像素）。"""
    try:
        import numpy as np
        A = np.frombuffer(rgba, dtype="uint8").reshape(h, w, 4)[::stride, ::stride, :3].astype("int16")
        if clear and len(clear) >= 3:
            ref = np.asarray([int(round(float(c) * 255.0)) for c in clear[:3]], dtype="int16")
            return float((np.abs(A - ref).max(axis=2) > 8).mean())
        flat = A.reshape(-1, 3)
        if len(flat) == 0:
            return 0.0
        uniq, cnt = np.unique(flat, axis=0, return_counts=True)
        ref = uniq[int(np.argmax(cnt))]
        return float((np.abs(flat - ref).max(axis=1) > 8).mean())
    except Exception:
        try:
            n = len(rgba) // 4
            if n == 0:
                return 0.0
            base = tuple(int(round(float(c) * 255.0)) for c in (clear or [0, 0, 0])[:3])
            step = int(max(1, stride))
            diff = total = 0
            for i in range(0, n, step):
                j = i * 4
                total += 1
                if ((rgba[j] - base[0]) ** 2 + (rgba[j + 1] - base[1]) ** 2 + (rgba[j + 2] - base[2]) ** 2) > 64:
                    diff += 1
            return diff / max(1, total)
        except Exception:
            return 0.0


def _objects_in_frame(s):
    """按相机矩阵把每个可见对象的 bbox 投到画面里：返回 (in_frame_names, min_margin_px)。
    比"数非底色像素"可靠 —— background=true 时世界底色也算像素，纯覆盖率判不出"瞄空"。
    注意：mathutils.Matrix 只能跟 mathutils.Vector 乘（跟 numpy 数组会 TypeError，自检抓到过）。"""
    from mathutils import Vector
    vm = make_view_matrix(s["from"], s["look_at"])
    pm = make_proj_matrix(s["width"], s["height"], s["lens"], s["sensor"], s["clip_start"], s["clip_end"],
                          s["sensor_fit"], s["ortho"], s["ortho_scale"], s["shift_x"], s["shift_y"])
    vpm = pm @ vm
    w, h = float(s["width"]), float(s["height"])
    names, best = [], None
    for o in bpy.context.scene.objects:
        if o.type != "MESH" or o.hide_render or o.hide_viewport:
            continue
        inside = False
        for c in o.bound_box:
            p = o.matrix_world @ Vector((c[0], c[1], c[2]))
            clip = vpm @ Vector((p.x, p.y, p.z, 1.0))
            if clip[3] <= 1e-9:
                continue                      # 在相机背后
            ndc = clip.xyz / clip[3]          # mathutils：切片 [:3] 会退化成 tuple，用 .xyz 才拿到 Vector
            x = (ndc[0] + 1.0) * 0.5 * w
            y = (ndc[1] + 1.0) * 0.5 * h
            if -0.1 * w <= x <= 1.1 * w and -0.1 * h <= y <= 1.1 * h:
                inside = True
                mx = min(x, w - x, y, h - y)
                best = mx if best is None else min(best, mx)
        if inside:
            names.append(o.name)
    return names, (None if best is None else round(float(best), 4))


def _empty_frame_note(cov, bbox, s, in_frame=None):
    """画面几乎为空时的可执行告警（而不是让用户自己去按 Home）。"""
    empty = (in_frame is not None and len(in_frame) == 0)
    if not empty and (cov is None or cov > 0.004):
        return None
    sug = None
    if bbox:
        import math as _m
        c = bbox["center"]
        span = max(float(bbox["span"]), 1e-6)
        v = [float(s["from"][i]) - c[i] for i in range(3)]
        n = _m.sqrt(sum(x * x for x in v)) or 1.0
        d = 2.2 * span
        sug = {"look_at": c, "from": [round(c[i] + v[i] / n * d, 4) for i in range(3)], "lens": s["lens"]}
    return {"code": "frame_looks_empty", "coverage": round(float(cov or 0.0), 6), "scene_bbox": bbox, "suggest": sug,
            "objects_in_frame": list(in_frame or []),
            "why": "画面里没有可见几何 —— 自定义视角瞄空（from/look_at 与场景不在同一处）"
                   if in_frame is not None else "画面里几乎只剩底色 —— 自定义视角多半瞄空",
            "hint": ("用返回的 scene_bbox.center 当 look_at；或直接用 suggest 里那组 from/look_at 再出一次"
                     if sug else "当前场景没有可见 mesh —— 自定义视角当然只有底色")}


def gui_frame(object=None, area=None, all=False):
    """GUI 原语（93-D1）：在真 UI 上下文里框选对象/全场景（rt_do 里 bpy.context.screen 为 None，做不到这件事）。"""
    win, ar, reg, space = _find_view3d(area)
    prev_sel = [o.name for o in bpy.context.selected_objects][:20]
    got = None
    try:
        with bpy.context.temp_override(window=win, screen=win.screen, area=ar, region=reg):
            if object:
                ob = bpy.data.objects.get(str(object))
                if ob is None:
                    return _j({"ok": False, "error": "对象不存在：%s" % object,
                               "hint": "先在 K 里看 bpy.data.objects 的名字"})
                ob.select_set(True)
                bpy.context.view_layer.objects.active = ob
                bpy.ops.view3d.view_selected(use_all_regions=False)
                got = ob.name
            else:
                bpy.ops.view3d.view_all(center=bool(all))
    except Exception as e:
        import traceback
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                   "traceback": traceback.format_exc()[-500:],
                   "hint": "需要 GUI 的 3D 视口；无头 -b 模式没有 UI 上下文（用 view.py 的 capture 代替）"})
    return _j({"ok": True, "framed": got, "mode": "all" if not object else "selected",
               "area": [ar.x, ar.y, ar.width, ar.height], "prev_selected": prev_sel,
               "note": "只动视口取景；场景数据未改"})


def gui_shading(mode="SOLID", color_type=None, area=None):
    """GUI 原语（93-D1）：切视口着色（WIREFRAME/SOLID/MATERIAL/RENDERED）+ 可选 color_type。"""
    win, ar, reg, space = _find_view3d(area)
    before = {"shading": space.shading.type, "color_type": getattr(space.shading, "color_type", None)}
    modes = ("WIREFRAME", "SOLID", "MATERIAL", "RENDERED")
    m = str(mode).upper()
    if m not in modes:
        return _j({"ok": False, "error": "未知 shading：%s" % mode, "allowed": list(modes)})
    try:
        space.shading.type = m
        if color_type:
            space.shading.color_type = str(color_type).upper()
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    return _j({"ok": True, "shading": space.shading.type, "color_type": getattr(space.shading, "color_type", None),
               "before": before, "note": "只改视口显示；渲染设置未动"})


def gui_open(path):
    """GUI 原语（93-D1）：在 GUI 里打开 .blend（⚠ 替换当前文件；打开后旧引用全部失效）。"""
    if not path:
        return _j({"ok": False, "error": "path 必填"})
    wp = _win_path(path)
    try:
        bpy.ops.wm.open_mainfile(filepath=wp)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "path": wp})
    return _j({"ok": True, "opened": wp, "file": bpy.data.filepath, "objects": len(bpy.data.objects),
               "warning": "open_mainfile 会重建 bpy.data：之前抓到的 Object/Collection/材质引用全部作废，重新取一次",
               "note": "这是本会话当前文件；要写盘请显式 bpy.ops.wm.save_as_mainfile(filepath=...)"})


def gui_help():
    return _j({"version": VIEW_VERSION,
               "ops": {"gui_frame": "object=<名>|省略=全场景（真 UI 上下文 view_selected / view_all）",
                       "gui_shading": "mode=WIREFRAME|SOLID|MATERIAL|RENDERED，color_type 可选",
                       "gui_open": "path=<.blend>（⚠ 替换当前文件，旧引用失效）"},
               "why": "rt_do 的 execute_code 上下文没有 screen/area（实测 bpy.context.screen is None）→ 这三条由插件侧在真 UI 上下文执行",
               "read_only_note": "gui_frame / gui_shading 只动视口显示；gui_open 会换文件，属写操作"})


def capture(spec_json):
    """出图主入口：按 spec 出一帧 PNG，返回 JSON（含路径/尺寸/矩阵/耗时）。"""
    t0 = time.perf_counter()
    s = _spec(json.loads(spec_json) if isinstance(spec_json, str) else spec_json)
    path = s["path"]
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    meta = {}
    rgba = None
    mode = s["mode"].lower()
    err = None
    if mode == "viewport":
        try:
            rgba, meta = _capture_viewport(s)
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
            mode = "render"  # 自动降级（例如没有 3D 视口可用）
    if rgba is None:
        rgba, meta2 = _capture_render(s)
        meta.update(meta2 or {})
        if not os.path.isfile(path):
            return _j({"ok": False, "error": err or "出图失败（viewport 与 render 两条路都没产出）", "mode": mode})
        size = os.path.getsize(path)
        # v0.9.4（P0-1）：原实现这里**连着调了两次** _scene_bbox()（每次 O(物体数)，403 物体 ~18 ms×2）；
        #   现在只算一次，且同样只在"显式要诊断"或"近空帧"时才算。
        need_diag_r = bool(s.get("diagnostics")) or int(size) < EMPTY_PNG_BYTES
        bbox_r = _scene_bbox() if need_diag_r else None
        return _j({"ok": True, "mode": mode, "path": path, "bytes": size,
                   "width": s["width"], "height": s["height"], "fallback_from": err,
                   "diagnostics": need_diag_r,
                   "coverage_estimate": None, "scene_bbox": bbox_r,
                   "warning": (None if bbox_r else (_empty_frame_note(0.0, None, s) if need_diag_r else None)),
                   "matrix": {"view": [[round(v, 6) for v in r] for r in make_view_matrix(s["from"], s["look_at"])],
                              "proj": [[round(v, 6) for v in r] for r in make_proj_matrix(
                                  s["width"], s["height"], s["lens"], s["sensor"], s["clip_start"], s["clip_end"],
                                  s["sensor_fit"], s["ortho"], s["ortho_scale"], s["shift_x"], s["shift_y"])]},
                   "meta": meta, "ms": int((time.perf_counter() - t0) * 1000), "spec": s})
    n = write_png(path, s["width"], s["height"], rgba)
    # ── 诊断三项：默认不跑，近空帧自动补跑（v0.9.4 · P0-1）────────────────────────
    # v0.9.1（93-D2）的自诊断（coverage_estimate / scene_bbox / objects_in_frame + frame_looks_empty）
    # 语义**不变**，只是不再每次都付：正常帧省 45–490 ms，近空帧（PNG < EMPTY_PNG_BYTES）
    # 或调用方显式 diagnostics=true 时照旧跑全。缓冲区已经在手里，补跑不需要重新绘制。
    need_diag = bool(s.get("diagnostics")) or int(n) < EMPTY_PNG_BYTES
    if need_diag:
        cov = _coverage(rgba, s["width"], s["height"], None)   # 底色取众数（background=true 时世界底色也算像素）
        bbox = _scene_bbox()
        try:
            in_frame, margin_px = _objects_in_frame(s)
        except Exception:
            in_frame, margin_px = None, None
        warn = _empty_frame_note(cov, bbox, s, in_frame)
    else:
        cov = bbox = in_frame = margin_px = warn = None
    return _j({"ok": True, "mode": "viewport", "path": path, "bytes": n,
               "diagnostics": need_diag,
               "width": s["width"], "height": s["height"], "fallback_from": err,
               "coverage_estimate": (None if cov is None else round(float(cov), 6)), "scene_bbox": bbox,
               "objects_in_frame": in_frame, "min_margin_px": margin_px, "warning": warn,
               "matrix": {"view": [[round(v, 6) for v in r] for r in make_view_matrix(s["from"], s["look_at"])],
                          "proj": [[round(v, 6) for v in r] for r in make_proj_matrix(
                              s["width"], s["height"], s["lens"], s["sensor"], s["clip_start"], s["clip_end"],
                              s["sensor_fit"], s["ortho"], s["ortho_scale"], s["shift_x"], s["shift_y"])]},
               "meta": meta, "ms": int((time.perf_counter() - t0) * 1000), "spec": s})


def selftest():
    """无需 GUI 的自检：GPUOffScreen 清屏 → 读回 → 直写 PNG → 校验字节（后台 -b 也能跑）。"""
    import gpu
    out = {}
    try:
        gpu.init()
        out["gpu_init"] = "ok"
    except Exception as e:
        out["gpu_init"] = "ERR " + str(e)[:120]
    from mathutils import Vector, Matrix
    cam_data = bpy.data.cameras.new("st")
    cam = bpy.data.objects.new("st", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    try:
        cam_data.lens = 50.0
        cam.location = (6.0, -4.0, 3.5)
        cam.rotation_euler = (Vector((0, 0, 1)) - Vector(cam.location)).to_track_quat("-Z", "Y").to_euler()
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        ref = cam.calc_matrix_camera(dg, x=1920, y=1080)
        mine = make_proj_matrix(1920, 1080, 50.0, 36.0, cam_data.clip_start, cam_data.clip_end)
        out["proj_max_delta"] = max(abs(a - b) for ra, rb in zip([list(r) for r in ref], [list(r) for r in mine])
                                    for a, b in zip(ra, rb))
        vref = cam.matrix_world.inverted()
        vmine = make_view_matrix((6.0, -4.0, 3.5), (0.0, 0.0, 1.0))
        out["view_max_delta"] = max(abs(a - b) for ra, rb in zip([list(r) for r in vref], [list(r) for r in vmine])
                                    for a, b in zip(ra, rb))
        cam_data.type = "ORTHO"; cam_data.ortho_scale = 12.0
        bpy.context.view_layer.update(); dg = bpy.context.evaluated_depsgraph_get()
        oref = cam.calc_matrix_camera(dg, x=1920, y=1080)
        omine = make_proj_matrix(1920, 1080, ortho=True, ortho_scale=12.0)
        out["ortho_max_delta"] = max(abs(a - b) for ra, rb in zip([list(r) for r in oref], [list(r) for r in omine])
                                     for a, b in zip(ra, rb))
    finally:
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    W, H = 96, 54
    off = gpu.types.GPUOffScreen(W, H)
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(25 / 255.0, 153 / 255.0, 51 / 255.0, 1.0), depth=1.0)
            rgba = bytes(memoryview(off.texture_color.read()))
    finally:
        off.free()
    p = os.path.join(os.path.dirname(WIN_DEFAULT), "dsh_view_selftest.png")
    out["png_bytes"] = write_png(p, W, H, rgba)
    out["png_path"] = p
    out["first_px"] = list(rgba[:4])
    out["ok"] = (out.get("proj_max_delta", 9) < 1e-5 and out.get("view_max_delta", 9) < 1e-5
                 and out.get("ortho_max_delta", 9) < 1e-5 and out["first_px"] == [25, 153, 51, 255])
    return _j(out)


def help():
    return _j({
        "version": VIEW_VERSION,
        "capture": "capture({from, look_at, lens, ortho, ortho_scale, width, height, shading, overlays, mode, path})",
        "fields": {
            "from / look_at": "世界坐标 [x,y,z]（必给）",
            "lens / sensor_width": "焦距 mm / 传感器宽 mm（默认 50 / 36，AUTO 贴长边）",
            "ortho / ortho_scale": "正交开关与尺度（贴长边）",
            "width / height": "出图分辨率（16..4096）",
            "shading": "WIREFRAME/SOLID/MATERIAL/RENDERED —— 临时切换视口着色，出图后还原",
            "overlays": "true/false 临时开关覆盖物（网格/坐标轴/gizmo），出图后还原",
            "mode": "viewport（默认，零场景改动）/ render（临时相机 + render.opengl）",
            "color_management": "默认 true（出图带色彩管理，所见即所得）",
            "background": "默认 true（画世界背景）",
            "path": "输出 PNG 的路径（Blender 侧可见；默认取配置的工作目录，兜底用系统临时目录）",
        },
        "others": {"matrices": "只算矩阵不出图", "targets": "列出可用的 3D 视口", "selftest": "无 GUI 自检",
                   "gui_frame": "GUI 原语：框选对象/全场景（rt_do 里 screen 为 None 做不到）",
                   "gui_shading": "GUI 原语：切视口着色 WIREFRAME/SOLID/MATERIAL/RENDERED",
                   "gui_open": "GUI 原语：打开 .blend（⚠ 替换当前文件）", "gui_help": "GUI 原语速查"},
        "diagnostics_v3": {
            "coverage_estimate": "画面里非底色像素占比（子采样）—— 用来判'自定义视角是不是瞄空了'",
            "scene_bbox": "当前场景可见 mesh 的世界 bbox（center/span）—— 瞄空时按它重设 look_at",
            "warning": "coverage ≤ 0.004 时给 {code:'frame_looks_empty', suggest:{from,look_at,lens}}，直接照它再出一次",
            "when": "v0.9.4 起**默认不跑**（回执里 diagnostics=false 且三项为 null）；"
                    "PNG < %d 字节（近空帧）自动补跑；spec.diagnostics=true 可强制每次都跑。"
                    "实测省 45 ms（空场景）～488 ms（2240×1260）/次" % EMPTY_PNG_BYTES,
        },
        "notes": "viewport 模式完全不改场景（物体/相机/选择都不动）；render 模式临时加相机，结束即删并逐项还原渲染设置",
    })


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_view_api = _KIT.Api({"version": VIEW_VERSION, "capture": capture, "matrices": matrices_json,
                       "targets": targets, "selftest": selftest, "help": help,
                       "gui_frame": gui_frame, "gui_shading": gui_shading, "gui_open": gui_open,
                       "gui_help": gui_help,
                       # v0.9.1：诊断原语正式暴露（私有名在 preload 的命名空间隔离下不外泄，
                       # 所以"能被复用/被自检调用"的东西必须挂到 API 上）
                       "diagnostics": {"scene_bbox": _scene_bbox, "coverage": _coverage,
                                       "objects_in_frame": _objects_in_frame,
                                       "empty_frame_note": _empty_frame_note,
                                       # v0.9.4（P0-1）：把"什么时候会自动跑诊断"的阈值一并暴露，
                                       # 自检才能对"diagnostics 标志 == (显式要求 or 字节数 < 阈值)"做机械断言
                                       "empty_png_bytes": EMPTY_PNG_BYTES}})
