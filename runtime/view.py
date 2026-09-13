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

VIEW_VERSION = 1

# 默认输出到 Blender 本机临时目录（分享版不假设固定路径；正常调用时引擎会传
# path = 配置里的工作目录，这里只是兜底）。
WIN_DEFAULT = os.path.join(tempfile.gettempdir(), "dsh_view_capture.png")


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


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
        return _j({"ok": True, "mode": mode, "path": path, "bytes": size,
                   "width": s["width"], "height": s["height"], "fallback_from": err,
                   "matrix": {"view": [[round(v, 6) for v in r] for r in make_view_matrix(s["from"], s["look_at"])],
                              "proj": [[round(v, 6) for v in r] for r in make_proj_matrix(
                                  s["width"], s["height"], s["lens"], s["sensor"], s["clip_start"], s["clip_end"],
                                  s["sensor_fit"], s["ortho"], s["ortho_scale"], s["shift_x"], s["shift_y"])]},
                   "meta": meta, "ms": int((time.perf_counter() - t0) * 1000), "spec": s})
    n = write_png(path, s["width"], s["height"], rgba)
    return _j({"ok": True, "mode": "viewport", "path": path, "bytes": n,
               "width": s["width"], "height": s["height"], "fallback_from": err,
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
        "others": {"matrices": "只算矩阵不出图", "targets": "列出可用的 3D 视口", "selftest": "无 GUI 自检"},
        "notes": "viewport 模式完全不改场景（物体/相机/选择都不动）；render 模式临时加相机，结束即删并逐项还原渲染设置",
    })


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_view_api = {"version": VIEW_VERSION, "capture": capture, "matrices": matrices_json,
                       "targets": targets, "selftest": selftest, "help": help}
