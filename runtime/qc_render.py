# -*- coding: utf-8 -*-
"""DSH 内置渲染 harness（v0.8.8，外部反馈 #4 的 P2-1）—— 一次调用出多视角 QC 图。

外部反馈原话：入参 {file, views[], res, samples, thr, outdir} → 自动按 bbox 精确取景
+ 固定三点光 + 逐张 jsonl 计时 + 不超过 budget 的判定 —— "这是任何建模任务都要重写一遍的东西"。

API 挂 K.dsh_qc_render_api。入口两条：
    blender_rt_plan(op="qc_render_views", args={...})                    # 走 qc_render 模块（engine 路由）
    blender_rt_headless(file=..., preload="qc,qc_render", script=...)    # 无头/作业层（长活、天然隔离）

参数（除 views 外都有默认值）：
    file        .blend 路径。与当前文件不同时：后台 -b 直接打开；GUI 默认拒绝（会顶掉用户场景），
                需要 allow_open_file=true 才开
    views       ["front","iso","top"] 或 [{"name","az","el","from","look_at","lens","ortho","res","margin"}]
                具名视角：front/back/left/right/top/bottom/iso/iso_l/iso_back/front_high
                也可写 "az=35,el=20"；默认 ["iso","front","right"]
    res         [w,h] 或单个数（默认 [512,512]）
    samples     采样数（EEVEE=taa_render_samples / Cycles=samples，默认 64）
    budget_s    累计渲染秒数上限（别名 thr）。超了立即停：已出的图与 jsonl 行保留，within_budget=false
    outdir      输出目录（Windows 或 WSL 路径都收，默认 K.out_dir）
    ref_path    可选参考图：每张渲染调 qc.py 的 compare 算 IoU/剖面差并写进 jsonl（需注入 qc.py）
    ref_box     参考图裁切框 [x0,y0,x1,y1]；也可给 {"front":[..], "iso":[..]} 按视角区分
    ref_search  参考比对是否用搜索对齐（默认 false = 固定对齐口径，防刷分）
    lights      {"key":4.0,"fill":1.2,"rim":2.5} 或 false 关闭；默认叠在场景现有灯光之上
    lights_mode "add"（默认）| "only"（临时屏蔽场景里其它灯，出图后还原）
    margin      取景余量（默认 1.12；越大留白越多）
    engine      "keep"（默认，跟随进程当前引擎）| "eevee"（EEVEE+光追）| "cycles"
    view_transform  色彩变换（默认 null = 保留场景设置；给 "Standard" 可去掉 AgX 洗淡）
    tag         输出文件名前缀（默认 "views"）；jsonl 默认 <outdir>/render_views.jsonl

产物：<outdir>/<tag>_<view>.png × N + <outdir>/render_views.jsonl（每行 {view, ms, bytes, hash, ...}）

纪律（与本项目其它模块一致）：
  · 相机与三点光**只在本进程内临时建**；finally 里删掉，scene 的渲染设置全部还原；
  · 不写 .blend、不动用户相机、不动用户灯光（除非 lights_mode="only"，也是出图后还原）；
  · GUI 里默认不打开别的 .blend —— 长活走 headless/作业层（天然隔离）。
"""
import bpy
import hashlib
import json
import math
import os
import tempfile
import time

import numpy as np
from mathutils import Vector

QC_RENDER_VERSION = 1
DEFAULT_RES = (512, 512)
# 固定三点光（强度 W/m² 级的 SUN，与场景尺度无关）：key 3.2 / fill 1.0 / rim 2.4
DEFAULT_LIGHTS = {"key": 3.2, "fill": 1.0, "rim": 2.4}
# 相对**相机方位角**的固定角度（度）：key 右前上 · fill 左前平 · rim 后方轮廓
LIGHT_ANGLES = {"key": (35.0, 45.0), "fill": (-50.0, 8.0), "rim": (165.0, 30.0)}
LIGHT_SOFT = {"key": 5.0, "fill": 14.0, "rim": 6.0}
NAMED_VIEWS = {
    "front": (-90.0, 0.0), "back": (90.0, 0.0), "right": (0.0, 0.0), "left": (180.0, 0.0),
    "top": (0.0, 90.0), "bottom": (0.0, -90.0),
    "iso": (-45.0, 25.0), "iso_l": (-135.0, 25.0), "iso_back": (135.0, 25.0),
    "front_high": (-90.0, 35.0), "right_high": (0.0, 35.0),
}


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _win(path):
    """WSL → Windows 侧路径（Blender 是 Windows 进程）。K.win_path 可用时用它。"""
    K = _kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            return K.win_path(path)
        except Exception:
            pass
    s = str(path)
    if s.startswith("/mnt/") and len(s) > 6:
        return s[5].upper() + ":" + chr(92) + s[7:].replace("/", chr(92))
    return s


def _out_dir():
    K = _kernel()
    d = getattr(K, "out_dir", None) if K is not None else None
    return d or os.path.join(tempfile.gettempdir(), "dsh_qc_render")


def _safe(name):
    s = "".join((c if (c.isalnum() or c in "-_.") else "_") for c in str(name))
    return s.strip("_") or "view"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 目标 AABB + 取景

def _collect_objs(names=None):
    scn = bpy.context.scene
    skipped = {"hidden": 0, "empty": 0, "missing": []}
    objs = []
    if names:
        for n in names:
            ob = bpy.data.objects.get(str(n))
            if ob is None:
                skipped["missing"].append(str(n))
                continue
            objs.append(ob)
        return objs, skipped
    for ob in scn.objects:
        if ob.type != "MESH":
            continue
        if ob.hide_render:
            skipped["hidden"] += 1
            continue
        try:
            if not ob.visible_get():
                skipped["hidden"] += 1
                continue
        except Exception:
            pass
        if ob.data is None or len(ob.data.vertices) == 0:
            skipped["empty"] += 1
            continue
        objs.append(ob)
    return objs, skipped


def _aabb(objs):
    mn = Vector((1e30, 1e30, 1e30))
    mx = Vector((-1e30, -1e30, -1e30))
    n = 0
    for ob in objs:
        mw = ob.matrix_world
        for c in ob.bound_box:
            p = mw @ Vector((c[0], c[1], c[2]))
            for i in range(3):
                if p[i] < mn[i]:
                    mn[i] = p[i]
                if p[i] > mx[i]:
                    mx[i] = p[i]
        n += 1
    if n == 0:
        return None, None, 0
    return mn, mx, n


def _corners(mn, mx):
    return [Vector((x, y, z)) for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])]


def _fov_tans(w, h, lens, sensor, sensor_fit="AUTO"):
    """与 bpy Camera.calc_matrix_camera 同一口径（AUTO：sensor 贴长边），返回 (tan_x, tan_y)。"""
    fit = ("H" if w >= h else "V") if str(sensor_fit).upper().startswith("AUTO") else str(sensor_fit)[0].upper()
    if fit == "H":
        hx = sensor / 2.0
        hy = hx * (h / float(w))
    else:
        hy = sensor / 2.0
        hx = hy * (w / float(h))
    return hx / max(1e-9, float(lens)), hy / max(1e-9, float(lens))


def _basis(az, el):
    """(相机方位角, 仰角) → (center→camera 单位向量, 视线方向, right, up)。"""
    a = math.radians(float(az))
    e = math.radians(float(el))
    away = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
    d = -away
    up_hint = Vector((0.0, 0.0, 1.0))
    if abs(d.dot(up_hint)) > 0.999:
        up_hint = Vector((0.0, 1.0, 0.0))
    right = d.cross(up_hint)
    right = right.normalized() if right.length > 1e-9 else Vector((1.0, 0.0, 0.0))
    up = right.cross(d).normalized()
    return away, d, right, up


def _solve_view(center, corners, az, el, w, h, lens, sensor, margin, ortho=False, ortho_scale=None):
    """解相机位置：让 AABB 八角全部落在视锥内，再乘 margin 留白。返回 (loc, d, ortho_scale_used, 统计)。"""
    away, d, right, up = _basis(az, el)
    tx, ty = _fov_tans(w, h, lens, sensor)
    half_x = half_y = 0.0
    dist = 0.0
    for p in corners:
        r = p - center
        x = r.dot(right)
        y = r.dot(up)
        z = r.dot(-d)          # 朝相机方向为正
        half_x = max(half_x, abs(x))
        half_y = max(half_y, abs(y))
        if not ortho:
            dist = max(dist, abs(x) / tx - z, abs(y) / ty - z)
    if ortho:
        if w >= h:
            need = max(2.0 * half_x, 2.0 * half_y * (w / float(h)))
        else:
            need = max(2.0 * half_y, 2.0 * half_x * (h / float(w)))
        scale = float(ortho_scale) if ortho_scale else need * float(margin)
        dd = max(2.0 * max(half_x, half_y), 1e-3) * 4.0
        return center - d * dd, d, scale, {"half_x": half_x, "half_y": half_y, "dist": dd}
    dist = max(dist * float(margin), 1e-3)
    return center - d * dist, d, None, {"half_x": half_x, "half_y": half_y, "dist": dist}


def _proj_matrix_fallback(cam, w, h):
    """与 view.py 同源的投影矩阵（calc_matrix_camera 不可用时的兜底），相机在原点朝 -Z。"""
    from mathutils import Matrix
    cd = cam.data
    lens = float(cd.lens)
    sensor = float(cd.sensor_width)
    fit = str(cd.sensor_fit)
    tx, ty = _fov_tans(w, h, lens, sensor, fit)
    if cd.type == "ORTHO":
        sx = float(cd.ortho_scale)
        if w >= h:
            p00, p11 = 2.0 / sx, 2.0 / (sx * (h / float(w)))
        else:
            p00, p11 = 2.0 / (sx * (w / float(h))), 2.0 / sx
        return Matrix(((p00, 0.0, 2.0 * cd.shift_x, 0.0), (0.0, p11, 2.0 * cd.shift_y, 0.0),
                       (0.0, 0.0, -2.0 / (cd.clip_end - cd.clip_start),
                        -(cd.clip_end + cd.clip_start) / (cd.clip_end - cd.clip_start)), (0.0, 0.0, 0.0, 1.0)))
    p00 = 1.0 / tx
    p11 = 1.0 / ty
    cs, ce = float(cd.clip_start), float(cd.clip_end)
    return Matrix(((p00, 0.0, 2.0 * cd.shift_x, 0.0), (0.0, p11, 2.0 * cd.shift_y, 0.0),
                   (0.0, 0.0, -(ce + cs) / (ce - cs), -(2.0 * ce * cs) / (ce - cs)), (0.0, 0.0, -1.0, 0.0)))


def _fallback_px(cam, points, w, h):
    """自算投影矩阵 → 像素（与 view.py 同源；Blender 5.2 起 Camera.calc_matrix_camera 已被移除）。"""
    pm = _proj_matrix_fallback(cam, w, h)
    vm = cam.matrix_world.inverted()
    xs, ys = [], []
    for p in points:
        # 注意：mathutils 里 4x4 @ 3D 向量不做透视除法 → 显式拼成 4D 点
        v = vm @ Vector((float(p[0]), float(p[1]), float(p[2])))
        c = pm @ Vector((float(v[0]), float(v[1]), float(v[2]), 1.0))
        ww = float(c[3])
        if abs(ww) < 1e-9:
            ww = 1e-9
        xs.append((float(c[0]) / ww * 0.5 + 0.5) * w)
        ys.append((0.5 - float(c[1]) / ww * 0.5) * h)
    return xs, ys


def _project_px(cam, points, w, h):
    """世界点 → 像素 bbox。主口径 = Blender 自己的 world_to_camera_view（真相），
    自算矩阵作对拍；两者逐点像素差进 proj_err_px（正常应在 1e-3 像素以内）。

    返回 (bbox, source, err_px)。
    """
    xs, ys = [], []
    src = "fallback"
    try:
        from bpy_extras.object_utils import world_to_camera_view
        scn = bpy.context.scene
        for p in points:
            c = world_to_camera_view(scn, cam, p)
            xs.append(float(c[0]) * w)
            ys.append((1.0 - float(c[1])) * h)
        src = "world_to_camera_view"
    except Exception:
        xs, ys, src = [], [], "fallback"
    fbx, fby = _fallback_px(cam, points, w, h)
    if src == "world_to_camera_view" and len(fbx) == len(xs):
        err = max([abs(a - b) for a, b in zip(xs, fbx)] + [abs(a - b) for a, b in zip(ys, fby)] or [0.0])
        err = round(float(err), 6)
    else:
        xs, ys, src, err = fbx, fby, "fallback", None
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)], src, err


# ---------------------------------------------------------------- 灯光

def _add_rig(az, energies):
    made = []
    for key in ("key", "fill", "rim"):
        en = float((energies or {}).get(key, 0.0) or 0.0)
        if en <= 0:
            continue
        daz, el = LIGHT_ANGLES[key]
        a = math.radians(float(az) + daz)
        e = math.radians(el)
        from_dir = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        ld = bpy.data.lights.new("DSH_QC_LIGHT_" + key.upper(), type="SUN")
        ld.energy = en
        try:
            ld.angle = math.radians(LIGHT_SOFT[key])
        except Exception:
            pass
        ob = bpy.data.objects.new("DSH_QC_LIGHT_" + key.upper(), ld)
        bpy.context.scene.collection.objects.link(ob)
        ob.location = Vector((0.0, 0.0, 0.0))
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = (-from_dir).to_track_quat("-Z", "Y")
        made.append(ob)
    return made


def _aim_rig(objs, az):
    for ob in objs:
        key = ob.name.rsplit("_", 1)[-1].lower()
        daz, el = LIGHT_ANGLES.get(key, (0.0, 45.0))
        a = math.radians(float(az) + daz)
        e = math.radians(el)
        from_dir = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        ob.rotation_quaternion = (-from_dir).to_track_quat("-Z", "Y")


# ---------------------------------------------------------------- 读回：alpha 包围盒

def _alpha_stats(path, thr=0.02):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    a = buf.reshape(h, w, 4)[::-1, :, 3]
    m = a > float(thr)
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return {"bbox": None, "coverage": 0.0, "alpha_max": round(float(a.max()), 4), "size": [w, h]}
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    return {"bbox": [x0, y0, x1, y1], "margin_px": [x0, y0, w - 1 - x1, h - 1 - y1],
            "coverage": round(float(m.mean()), 4),
            "fill": round(float((x1 - x0 + 1) * (y1 - y0 + 1)) / float(w * h), 4),
            "alpha_max": round(float(a.max()), 4), "size": [w, h]}


# ---------------------------------------------------------------- 引擎 / 渲染设置

def _set_engine(mode):
    """mode: eevee | cycles | keep。返回 (before, after) 供还原。"""
    sc = bpy.context.scene
    before = sc.render.engine
    m = str(mode or "keep").lower()
    if m in ("eevee", "eevee_rt", "e"):
        try:
            sc.render.engine = "BLENDER_EEVEE"
        except Exception:
            sc.render.engine = "BLENDER_EEVEE_NEXT"
        ee = getattr(sc, "eevee", None)
        if ee is not None:
            for attr, val in (("use_raytracing", True), ("use_shadows", True)):
                if hasattr(ee, attr):
                    try:
                        setattr(ee, attr, val)
                    except Exception:
                        pass
            if hasattr(ee, "ray_tracing_method"):
                try:
                    ee.ray_tracing_method = "SCREEN"
                except Exception:
                    pass
    elif m in ("cycles", "c"):
        sc.render.engine = "CYCLES"
    return before, sc.render.engine


def _engine_label():
    sc = bpy.context.scene
    e = str(sc.render.engine)
    if "EEVEE" in e:
        ee = getattr(sc, "eevee", None)
        return e + ("+RT" if getattr(ee, "use_raytracing", False) else "")
    return e


def _apply_samples(n):
    sc = bpy.context.scene
    n = int(max(1, int(n)))
    done = {}
    ee = getattr(sc, "eevee", None)
    if ee is not None and hasattr(ee, "taa_render_samples"):
        done["eevee.taa_render_samples"] = int(ee.taa_render_samples)
        ee.taa_render_samples = n
    if hasattr(sc, "cycles"):
        try:
            done["cycles.samples"] = int(sc.cycles.samples)
            sc.cycles.samples = n
        except Exception:
            pass
    return done


# ---------------------------------------------------------------- 主入口

def qc_render_views(args=None):
    t0 = time.perf_counter()
    if isinstance(args, str):
        args = json.loads(args) if str(args).strip() else {}
    a = dict(args or {})
    # 兼容：把 {"args": {...}} 再展开一层
    if "args" in a and isinstance(a["args"], dict):
        merged = dict(a["args"])
        merged.update((k, v) for k, v in a.items() if k != "args")
        a = merged

    # ---- 输出目录与 jsonl
    outdir = _win(a.get("outdir") or _out_dir())
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        return _j({"ok": False, "error": "outdir 建不出来: %s (%s)" % (outdir, e)})
    tag = _safe(a.get("tag") or "views")
    jsonl = a.get("jsonl") or os.path.join(outdir, "render_views.jsonl")
    jsonl = _win(jsonl)

    # ---- file：与当前不同才开（GUI 默认拒绝，保护用户场景）
    want_file = a.get("file")
    opened = False
    if want_file:
        target = _win(want_file)
        cur = bpy.data.filepath or ""
        same = False
        try:
            same = bool(cur) and os.path.normcase(os.path.abspath(cur)) == os.path.normcase(os.path.abspath(target))
        except Exception:
            same = (cur == target)
        if not same:
            if bool(getattr(bpy.app, "background", False)) or a.get("allow_open_file"):
                bpy.ops.wm.open_mainfile(filepath=target)
                opened = True
            else:
                return _j({"ok": False, "error": "GUI 进程里拒绝打开别的 .blend（会顶掉你当前的场景）",
                           "file": target, "current": cur,
                           "hint": "长活请走 blender_rt_headless(file=..., preload=\"qc,qc_render\", as_job=true)；"
                                   "确实要在 GUI 里换文件就传 allow_open_file=true"})

    # ---- 引擎 / 采样
    sc = bpy.context.scene
    eng_mode = str(a.get("engine") or "keep")
    eng_before, eng_after = _set_engine(eng_mode)
    samples = int(a.get("samples") or 64)
    samp_before = _apply_samples(samples)

    # ---- 目标与 AABB
    objs, skipped = _collect_objs(a.get("targets"))
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    mn, mx, nobj = _aabb(objs)
    if nobj == 0 or mn is None:
        return _j({"ok": False, "error": "取景失败：没有任何可见 mesh（可传 targets=[名字] 指定）",
                   "file": bpy.data.filepath, "skipped": skipped})
    center = (mn + mx) * 0.5
    size = mx - mn
    corners = _corners(mn, mx)

    # ---- 视角归一化
    views = a.get("views") or ["iso", "front", "right"]
    if isinstance(views, str):
        views = [v.strip() for v in views.split(",") if v.strip()]
    base_res = a.get("res") or DEFAULT_RES
    if isinstance(base_res, (int, float)):
        base_res = [int(base_res), int(base_res)]
    base_res = [int(base_res[0]), int(base_res[1])]
    vlist = []
    for i, v in enumerate(views):
        if isinstance(v, str):
            s = {"name": v}
            key = v.strip().lower()
            if key in NAMED_VIEWS:
                s["az"], s["el"] = NAMED_VIEWS[key]
            else:
                kv = {}
                for part in key.replace(" ", "").split(","):
                    if "=" in part:
                        kk, vv = part.split("=", 1)
                        kv[kk] = vv
                if "az" in kv:
                    s["az"] = float(kv["az"])
                if "el" in kv:
                    s["el"] = float(kv["el"])
                s.setdefault("az", NAMED_VIEWS["iso"][0])
                s.setdefault("el", NAMED_VIEWS["iso"][1])
        elif isinstance(v, dict):
            s = dict(v)
        else:
            return _j({"ok": False, "error": "views[%d] 类型不支持：%r" % (i, v)})
        s.setdefault("name", "v%d" % (i + 1))
        s.setdefault("res", base_res)
        vlist.append(s)

    budget_s = a.get("budget_s", a.get("thr"))
    try:
        budget_ms = int(round(float(budget_s) * 1000.0)) if budget_s not in (None, "", 0, "0") else 0
    except Exception:
        budget_ms = 0

    lights_cfg = a.get("lights", DEFAULT_LIGHTS)
    if lights_cfg is False or lights_cfg is None:
        lights_cfg = {}
    elif lights_cfg is True:
        lights_cfg = dict(DEFAULT_LIGHTS)
    elif isinstance(lights_cfg, dict):
        lights_cfg = dict(DEFAULT_LIGHTS, **{k: v for k, v in lights_cfg.items() if k in ("key", "fill", "rim")})
    else:
        lights_cfg = dict(DEFAULT_LIGHTS)
    lights_mode = str(a.get("lights_mode") or "add").lower()
    margin = float(a.get("margin", 1.12))
    lens_def = float(a.get("lens", 50.0))
    sensor_def = float(a.get("sensor_width", 36.0))
    view_transform = a.get("view_transform")

    # ---- 现场保存
    css = getattr(sc, "view_settings", None)
    vt_before = getattr(css, "view_transform", None) if css is not None else None
    rs = sc.render
    saved = {
        "camera": sc.camera, "engine": eng_before,
        "res_x": int(rs.resolution_x), "res_y": int(rs.resolution_y), "res_pct": int(rs.resolution_percentage),
        "filepath": str(rs.filepath), "use_ext": bool(rs.use_file_extension),
        "fmt": str(rs.image_settings.file_format), "color_mode": str(rs.image_settings.color_mode),
        "film_transparent": bool(rs.film_transparent),
    }
    hidden_lights = []
    made_cam = None
    rig = []
    entries = []
    skipped_views = []
    stopped_early = False
    spent_ms = 0
    err = None
    try:
        # 三点光（只在本进程内临时建）
        if lights_cfg and lights_mode != "off":
            if lights_mode == "only":
                for ob in list(sc.objects):
                    if ob.type == "LIGHT" and not ob.hide_render:
                        ob.hide_render = True
                        hidden_lights.append(ob)
            rig = _add_rig(0.0, lights_cfg)
        # 临时相机
        cam_data = bpy.data.cameras.new("DSH_QC_CAM")
        made_cam = bpy.data.objects.new("DSH_QC_CAM", cam_data)
        sc.collection.objects.link(made_cam)
        made_cam.rotation_mode = "QUATERNION"
        cam_data.clip_start = 0.001
        cam_data.clip_end = 100000.0
        cam_data.sensor_width = sensor_def
        cam_data.sensor_fit = "AUTO"

        # 渲染设置（出图后全部还原）
        rs.image_settings.file_format = "PNG"
        rs.image_settings.color_mode = "RGBA"
        rs.film_transparent = bool(a.get("film_transparent", True))
        rs.resolution_percentage = 100
        rs.use_file_extension = False
        sc.camera = made_cam
        if view_transform and css is not None:
            try:
                css.view_transform = str(view_transform)
            except Exception:
                pass

        lines = []
        for idx, v in enumerate(vlist):
            if budget_ms and spent_ms >= budget_ms:
                stopped_early = True
                skipped_views = [str(x.get("name")) for x in vlist[idx:]]
                break
            rv = v.get("res") or base_res
            if isinstance(rv, (int, float)):
                rv = [int(rv), int(rv)]
            w, h = max(16, int(rv[0])), max(16, int(rv[1]))
            lens = float(v.get("lens", lens_def))
            ortho = bool(v.get("ortho", False))
            vmargin = float(v.get("margin", margin))
            nm = _safe(v.get("name"))
            path = _win(v.get("path") or os.path.join(outdir, "%s_%s.png" % (tag, nm)))
            frm = v.get("from") or v.get("loc")
            look = v.get("look_at") or v.get("target")
            if frm and look:
                loc = Vector([float(x) for x in frm[:3]])
                d = (Vector([float(x) for x in look[:3]]) - loc)
                d = d.normalized() if d.length > 1e-9 else Vector((0.0, -1.0, 0.0))
                az = math.degrees(math.atan2(-d[1], -d[0]))
                el = math.degrees(math.asin(max(-1.0, min(1.0, -d[2]))))
                stats = {"explicit": True}
                oscale = float(v.get("ortho_scale")) if (ortho and v.get("ortho_scale")) else (float(v.get("ortho_scale")) if v.get("ortho_scale") else None)
            else:
                az = float(v.get("az", NAMED_VIEWS["iso"][0]))
                el = float(v.get("el", NAMED_VIEWS["iso"][1]))
                loc, d, oscale, stats = _solve_view(center, corners, az, el, w, h, lens, sensor_def, vmargin,
                                                    ortho=ortho, ortho_scale=v.get("ortho_scale"))
            made_cam.location = loc
            made_cam.rotation_quaternion = d.to_track_quat("-Z", "Y")
            cam_data.lens = lens
            cam_data.type = "ORTHO" if ortho else "PERSP"
            if ortho:
                cam_data.ortho_scale = float(oscale or a.get("ortho_scale") or 10.0)
            # 相机与灯光角度：贴住视锥 + 别插进近裁剪面
            radius = max((mx - mn).length * 0.5, 1e-3)
            cam_data.clip_start = max(0.001, radius * 0.01)
            cam_data.clip_end = max(radius * 100.0, cam_data.clip_start * 1000.0)
            if rig:
                _aim_rig(rig, az)
            rs.resolution_x = w
            rs.resolution_y = h
            rs.filepath = path
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
            pred, pm_src, pm_err = _project_px(made_cam, corners, w, h)
            # ---- 渲染
            t1 = time.perf_counter()
            bpy.ops.render.render(write_still=True)
            ms = int(round((time.perf_counter() - t1) * 1000.0))
            spent_ms += ms
            got = os.path.isfile(path)
            nbytes = int(os.path.getsize(path)) if got else 0
            if not got:
                raise RuntimeError("渲染没有产出文件：%s" % path)
            entry = {"view": str(v.get("name")), "ms": ms, "bytes": nbytes, "hash": _md5(path),
                     "path": path, "res": [w, h], "engine": _engine_label(), "samples": samples,
                     "camera": {"az": round(az, 3), "el": round(el, 3), "lens": lens, "ortho": ortho,
                                "loc": [round(float(x), 4) for x in loc],
                                "look_at": [round(float(x), 4) for x in (loc + d)],
                                "ortho_scale": (round(float(cam_data.ortho_scale), 4) if ortho else None)},
                     "frame": {"pred_bbox_px": pred, "proj_source": pm_src, "proj_err_px": pm_err,
                               "solve": {k: (round(float(vv), 4) if isinstance(vv, float) else vv) for k, vv in stats.items()}},
                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            try:
                entry["frame"].update(_alpha_stats(path))
            except Exception as e:
                entry["frame"]["alpha_err"] = str(e)[:120]
            # ---- 可选：参考比对（走 qc.py 的 compare）
            ref_path = v.get("ref_path") or a.get("ref_path")
            if ref_path:
                K = _kernel()
                qapi = getattr(K, "dsh_qc_api", None) if K is not None else None
                if qapi is None:
                    entry["ref"] = {"ok": False, "error": "需要先注入 qc.py（headless preload 里带上 qc，或引擎自动注入）"}
                else:
                    rb = v.get("ref_box", None)
                    if rb is None:
                        rball = a.get("ref_box")
                        rb = rball.get(str(v.get("name")), None) if isinstance(rball, dict) else rball
                    try:
                        rr = json.loads(qapi["compare"](_win(ref_path), rb, path, str(v.get("name")),
                                                        None, None, bool(a.get("ref_search", False))))
                        met = rr.get("metrics", {}) or {}
                        entry["ref"] = {"ok": True, "iou": rr.get("iou"), "iou_fixed": met.get("iou_fixed"),
                                        "iou_search": met.get("iou_search"), "dice": met.get("dice"),
                                        "missing_px": met.get("missing_px"), "extra_px": met.get("extra_px"),
                                        "boundary_mean_px": (met.get("boundary") or {}).get("mean_px"),
                                        "profile_mean_diff_px": ((rr.get("profile") or {}).get("diff") or {}).get("mean_diff_px"),
                                        "sheet": rr.get("sheet"), "overlay": rr.get("overlay"),
                                        "ms": rr.get("ms"), "search": bool(a.get("ref_search", False))}
                    except Exception as e:
                        entry["ref"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
            entries.append(entry)
            lines.append(entry)
            # 逐张追加（第一张截断重写）：预算中途停下也留下已完成的行
            with open(jsonl, ("w" if len(lines) == 1 else "a"), encoding="utf-8") as f:
                f.write(_j(entry) + chr(10))
            if budget_ms and spent_ms > budget_ms:
                stopped_early = True
                skipped_views = [str(x.get("name")) for x in vlist[idx + 1:]]
                break
    except BaseException as e:
        import traceback
        err = {"error": "%s: %s" % (type(e).__name__, str(e)[:300]), "traceback": traceback.format_exc()[-2000:]}
    finally:
        # ---- 还原现场（临时对象删掉、设置回滚）
        try:
            for ob in rig:
                try:
                    bpy.data.objects.remove(ob, do_unlink=True)
                except Exception:
                    pass
            if made_cam is not None:
                try:
                    cd = made_cam.data
                    bpy.data.objects.remove(made_cam, do_unlink=True)
                    bpy.data.cameras.remove(cd, do_unlink=True)
                except Exception:
                    pass
            for ob in hidden_lights:
                try:
                    ob.hide_render = False
                except Exception:
                    pass
            sc.camera = saved["camera"]
            rs = sc.render
            rs.resolution_x = saved["res_x"]
            rs.resolution_y = saved["res_y"]
            rs.resolution_percentage = saved["res_pct"]
            rs.filepath = saved["filepath"]
            rs.use_file_extension = saved["use_ext"]
            rs.image_settings.file_format = saved["fmt"]
            rs.image_settings.color_mode = saved["color_mode"]
            rs.film_transparent = saved["film_transparent"]
            try:
                sc.render.engine = saved["engine"]
            except Exception:
                pass
            ee = getattr(sc, "eevee", None)
            if ee is not None and "eevee.taa_render_samples" in samp_before:
                try:
                    ee.taa_render_samples = samp_before["eevee.taa_render_samples"]
                except Exception:
                    pass
            if "cycles.samples" in samp_before:
                try:
                    sc.cycles.samples = samp_before["cycles.samples"]
                except Exception:
                    pass
            if view_transform and css is not None and vt_before is not None:
                try:
                    css.view_transform = vt_before
                except Exception:
                    pass
        except Exception as e2:
            if err is None:
                err = {"error": "还原现场失败: %s" % str(e2)[:200]}

    total_ms = int(round((time.perf_counter() - t0) * 1000.0))
    within = (not stopped_early) if budget_ms else True
    out = {"ok": err is None and len(entries) > 0,
           "version": QC_RENDER_VERSION,
           "file": bpy.data.filepath, "opened_file": opened,
           "outdir": outdir, "jsonl": jsonl, "jsonl_lines": len(entries),
           "views": entries, "count": len(entries),
           "total_ms": total_ms, "render_ms": spent_ms,
           "budget_ms": budget_ms, "within_budget": within, "stopped_early": stopped_early,
           "skipped_views": skipped_views,
           "engine": _engine_label(), "engine_mode": eng_mode, "samples": samples,
           "res": base_res, "objects": nobj, "aabb": {"min": [round(float(x), 4) for x in mn],
                                                      "max": [round(float(x), 4) for x in mx],
                                                      "size": [round(float(x), 4) for x in size],
                                                      "center": [round(float(x), 4) for x in center]},
           "skipped_objects": skipped, "lights": {"mode": lights_mode, "config": lights_cfg,
                                                  "angles_deg": LIGHT_ANGLES, "relative_to": "camera azimuth"},
           "margin": margin,
           "note": "每行 jsonl = 一张图；ms 是单张渲染耗时（首张含 EEVEE 着色器编译）；"
                   "within_budget=false 表示累计超预算已停（已出的图保留）"}
    if err is not None:
        out["error"] = err
    return _j(out)


def qc_render_help():
    return _j({
        "version": QC_RENDER_VERSION,
        "entry": ["blender_rt_plan(op='qc_render_views', args={...})",
                  "blender_rt_headless(file=..., preload='qc,qc_render', script=...)  # 长活/隔离"],
        "args": {"file": ".blend（GUI 需 allow_open_file）", "views": "具名/az+el/显式 from+look_at",
                 "res": "[w,h] 或单个数", "samples": "默认 64", "budget_s|thr": "累计渲染秒数上限",
                 "outdir": "默认 K.out_dir", "ref_path/ref_box/ref_search": "可选参考比对（qc.py compare）",
                 "lights": "{key,fill,rim} 或 false", "lights_mode": "add|only", "margin": "默认 1.12",
                 "engine": "keep|eevee|cycles", "tag": "文件名前缀", "targets": "限定参与取景的对象"},
        "named_views": NAMED_VIEWS,
        "outputs": ["<outdir>/<tag>_<view>.png", "<outdir>/render_views.jsonl（每行 {view, ms, bytes, hash, ...}）"],
        "guarantees": ["相机/三点光只在进程内临时建，finally 删除", "渲染设置全部还原",
                       "GUI 不打开别的 .blend", "预算超了立即停、已出的图保留"],
    })


def qc_render_selftest():
    """取景自检：解算出的相机用 Blender 自己的 world_to_camera_view 复核（临时相机，用完即删）。

    给出每个具名视角的：解算距离 / 8 角投影后的像素 bbox / 是否全部在画幅内 /
    自算矩阵与 Blender 真值逐点像素差（proj_err_px）。
    """
    box_mn = Vector((-1.0, -0.5, 0.0))
    box_mx = Vector((1.0, 0.5, 2.0))
    center = (box_mn + box_mx) * 0.5
    corners = _corners(box_mn, box_mx)
    w = h = 512
    lens, sensor = 50.0, 36.0
    margin = 1.12
    sc = bpy.context.scene
    rx, ry = int(sc.render.resolution_x), int(sc.render.resolution_y)
    sc.render.resolution_x, sc.render.resolution_y = w, h
    cd = bpy.data.cameras.new("DSH_QC_SELFTEST_CAM")
    cd.lens = lens
    cd.sensor_width = sensor
    cd.sensor_fit = "AUTO"
    ob = bpy.data.objects.new("DSH_QC_SELFTEST_CAM", cd)
    sc.collection.objects.link(ob)
    ob.rotation_mode = "QUATERNION"
    rows = []
    srcs = {}
    errs = []
    try:
        for name, (az, el) in sorted(NAMED_VIEWS.items()):
            loc, d, osc, st = _solve_view(center, corners, az, el, w, h, lens, sensor, margin)
            ob.location = loc
            ob.rotation_quaternion = d.to_track_quat("-Z", "Y")
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
            bbox, src, err = _project_px(ob, corners, w, h)
            srcs[src] = srcs.get(src, 0) + 1
            if err is not None:
                errs.append(err)
            rows.append({"view": name, "dist": round(st["dist"], 4),
                         "bbox_px": bbox,
                         "inside_frame": bool(bbox[0] >= -0.5 and bbox[1] >= -0.5 and bbox[2] <= w + 0.5 and bbox[3] <= h + 0.5),
                         "fill": round(max((bbox[2] - bbox[0]) / float(w), (bbox[3] - bbox[1]) / float(h)), 4),
                         "proj_err_px": err})
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.cameras.remove(cd)
        except Exception:
            pass
        sc.render.resolution_x, sc.render.resolution_y = rx, ry
    return _j({"ok": True, "version": QC_RENDER_VERSION, "res": [w, h], "margin": margin,
               "views": rows, "proj_sources": srcs,
               "proj_err_px_max": (round(max(errs), 6) if errs else None),
               "all_inside": all(r["inside_frame"] for r in rows),
               "expect": "all_inside=true；fill ≈ %.3f（=1/margin）；proj_err_px ≈ 0（自算矩阵 vs Blender 真值）" % (1.0 / margin)})


def qc_render_dispatch(op, args=None):
    ops = {"views": qc_render_views, "render_views": qc_render_views, "qc_render_views": qc_render_views,
           "help": qc_render_help, "selftest": qc_render_selftest}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown qc_render op", "op": op, "ops": sorted(ops)})
    try:
        return fn(args) if args is not None else fn()
    except TypeError:
        try:
            return fn(**dict(args or {}))
        except Exception as e:
            return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op})


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_qc_render_api = {"version": QC_RENDER_VERSION, "dispatch": qc_render_dispatch,
                            "render_views": qc_render_views, "help": qc_render_help,
                            "selftest": qc_render_selftest}
