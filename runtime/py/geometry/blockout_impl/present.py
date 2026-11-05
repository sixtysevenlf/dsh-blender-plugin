# -*- coding: utf-8 -*-
"""Split from blockout.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .templates import _parse_res
from .geometry import _bbox_bu, _configure_workbench, _tan_half
from .ops import _find_model_objects


# ────────────────────────────── 场景呈现（turntable / refsheet 共用）──────────────────────────────
_PRESENT_RENDER_ATTRS = ("resolution_x", "resolution_y", "resolution_percentage", "film_transparent",
                         "fps", "fps_base", "filepath")
_PRESENT_IMG_ATTRS = ("media_type", "file_format", "color_mode", "color_depth")
_PRESENT_FF_ATTRS = ("format", "codec", "constant_rate_factor", "audio_codec", "ffmpeg_preset")
_PRESENT_SHADING_ATTRS = ("light", "color_type", "single_color", "show_cavity", "cavity_type",
                          "show_object_outline", "object_outline_color", "show_specular_highlight",
                          "background_type", "background_color")
_BG_COLORS = {"white": 1.0, "gray": 0.5, "grey": 0.5, "black": 0.0, "darkgray": 0.18}
_REF_VIEW_ALIASES = {"front": "front", "back": "back", "left": "left", "side": "side", "right": "side",
                     "top": "top", "bottom": "bottom", "iso": "iso", "isometric": "iso", "hero": "iso"}
_REF_PERSP_ANGLES = {"front": (-90.0, 0.0), "back": (90.0, 0.0), "side": (0.0, 0.0),
                     "left": (180.0, 0.0), "top": (0.0, 90.0), "bottom": (0.0, -90.0),
                     "iso": (-45.0, 25.0)}


def _present_state_save(sc):
    st = {"render": {}, "img": {}, "ff": {}, "sh": {}, "scene": {}, "misc": {}}
    for a in _PRESENT_RENDER_ATTRS:
        try:
            st["render"][a] = getattr(sc.render, a)
        except Exception:
            pass
    try:
        st["render"]["engine"] = sc.render.engine
    except Exception:
        pass
    for a in _PRESENT_IMG_ATTRS:
        try:
            st["img"][a] = getattr(sc.render.image_settings, a)
        except Exception:
            pass
    for a in _PRESENT_FF_ATTRS:
        try:
            st["ff"][a] = getattr(sc.render.ffmpeg, a)
        except Exception:
            pass
    for a in _PRESENT_SHADING_ATTRS:
        try:
            st["sh"][a] = getattr(sc.display.shading, a)
        except Exception:
            pass
    for a in ("frame_start", "frame_end", "frame_step", "frame_current"):
        try:
            st["scene"][a] = getattr(sc, a)
        except Exception:
            pass
    try:
        st["misc"]["aa"] = sc.display.render_aa
    except Exception:
        pass
    try:
        st["misc"]["view_transform"] = sc.view_settings.view_transform
    except Exception:
        pass
    try:
        st["misc"]["camera"] = sc.camera
    except Exception:
        pass
    try:
        st["misc"]["world"] = sc.world
        if sc.world is not None:
            st["misc"]["world_color"] = tuple(sc.world.color)
            st["misc"]["world_use_nodes"] = bool(sc.world.use_nodes)
    except Exception:
        pass
    return st


def _present_state_restore(sc, st):
    def _set(obj, attr, val):
        try:
            setattr(obj, attr, val)
        except Exception:
            pass
    for a, v in (st.get("render") or {}).items():
        _set(sc.render, a, v)
    for a, v in (st.get("img") or {}).items():
        _set(sc.render.image_settings, a, v)
    for a, v in (st.get("ff") or {}).items():
        _set(sc.render.ffmpeg, a, v)
    try:
        sh = sc.display.shading
        for a, v in (st.get("sh") or {}).items():
            _set(sh, a, v)
    except Exception:
        pass
    for a, v in (st.get("scene") or {}).items():
        _set(sc, a, v)
    misc = st.get("misc") or {}
    if "aa" in misc:
        _set(sc.display, "render_aa", misc["aa"])
    if "view_transform" in misc:
        _set(sc.view_settings, "view_transform", misc["view_transform"])
    if "camera" in misc:
        _set(sc, "camera", misc["camera"])
    try:
        original_world = misc.get("world")
        if original_world is None:
            world = sc.world
            if world is not None and str(world.name).startswith("DSH_PRESENT_WORLD"):
                sc.world = None
                bpy.data.worlds.remove(world)
        else:
            sc.world = original_world
            if "world_color" in misc:
                _set(original_world, "color", misc["world_color"])
            if "world_use_nodes" in misc:
                _set(original_world, "use_nodes", misc["world_use_nodes"])
    except Exception:
        pass


def _bg_rgb(background, what="background"):
    b = str(background if background is not None else "white").strip().lower()
    if b in _BG_COLORS:
        return _BG_COLORS[b], b, False
    if b in ("transparent", "alpha"):
        return 0.0, "transparent", True
    raise ValueError("%s 只支持 white/gray/black/transparent（收到 %r）" % (what, background))


def _hide_other_objects(sc, objs):
    objset = set(objs)
    hidden = []
    for ob in list(sc.objects):
        if ob in objset:
            continue
        if ob.type in _RENDERABLE_TYPES:
            hidden.append((ob, bool(ob.hide_render)))
            ob.hide_render = True
    return hidden


def _unhide_objects(hidden):
    for ob, hv in hidden or []:
        try:
            if bpy.data.objects.get(ob.name) is not None:
                ob.hide_render = hv
        except Exception:
            pass


def _present_setup(sc, res, background, neutral=0.62):
    """Workbench + 白底/透明 + 中性 SINGLE 色（不动物体材质）+ Standard 色彩变换。

    Workbench 的 F12 渲染在 background_type=VIEWPORT 时仍会读 scene.world 的颜色；
    scene.world 为空时 PNG 的 alpha 还会是 0。因此这里临时补一个纯色 World，并由
    _present_state_save/_restore 负责恢复/删除（见 refsheet 回归）。
    """
    bg_val, bg_name, transparent = _bg_rgb(background)
    _configure_workbench(sc, res, film_transparent=transparent)
    try:
        world = sc.world
        if world is None:
            world = bpy.data.worlds.new("DSH_PRESENT_WORLD")
            world.use_nodes = False
            sc.world = world
        try:
            world.use_nodes = False
        except Exception:
            pass
        world.color = (float(bg_val), float(bg_val), float(bg_val))
    except Exception:
        pass
    try:
        sc.view_settings.view_transform = "Standard"
    except Exception:
        pass
    try:
        sh = sc.display.shading
        sh.color_type = "SINGLE"
        sh.single_color = (float(neutral), float(neutral), float(neutral))
        sh.background_type = "VIEWPORT"
        sh.background_color = (float(bg_val), float(bg_val), float(bg_val))
        sh.light = "STUDIO"
        try:
            sh.show_cavity = True
            sh.cavity_type = "BOTH"
        except Exception:
            pass
        try:
            sh.show_object_outline = True
            sh.object_outline_color = (0.05, 0.05, 0.05)
        except Exception:
            pass
    except Exception:
        pass
    try:
        sc.render.image_settings.media_type = "IMAGE"
    except Exception:
        pass
    try:
        ims = sc.render.image_settings
        ims.file_format = "PNG"
        ims.color_mode = "RGBA"
        ims.color_depth = "8"
    except Exception:
        pass
    return {"background": bg_name, "transparent": transparent,
            "bg_rgb": [float(bg_val)] * 3, "neutral_color": float(neutral)}


def _look_at(cam, loc, target):
    cam.location = tuple(loc)
    cam.rotation_mode = "XYZ"
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def _orbit_loc(center, dist, az_deg, el_deg):
    a = math.radians(float(az_deg))
    e = math.radians(float(el_deg))
    d = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
    return Vector(center) + d * float(dist)


def _find_video_file(root, stem):
    base = os.path.basename(stem)
    cands = []
    try:
        for fn in os.listdir(root):
            if not fn.startswith(base):
                continue
            if fn.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
                cands.append(os.path.join(root, fn))
    except Exception:
        return None
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0] if cands else None


def _clean_prefix_files(root, stem, exts):
    for fn in list(os.listdir(root)):
        low = fn.lower()
        if fn.startswith(os.path.basename(stem)) and any(low.endswith(e) for e in exts):
            try:
                os.remove(os.path.join(root, fn))
            except Exception:
                pass


def _render_turntable_video(sc, cam, center, cam_dist, elev, az_start, orbit, frames, root, stem, fps_v, res):
    """Blender 内置 FFMPEG：media_type=VIDEO + file_format=FFMPEG（Blender 5.x 的口径）。"""
    _clean_prefix_files(root, stem + "_turntable", (".mp4", ".mov", ".mkv", ".avi", ".webm"))
    prefix = _norm_path(os.path.join(root, stem + "_turntable"))
    ims = sc.render.image_settings
    ims.media_type = "VIDEO"
    ims.file_format = "FFMPEG"
    ff = sc.render.ffmpeg
    ff.format = "MPEG4"
    ff.codec = "H264"
    for attr, val in (("constant_rate_factor", "HIGH"), ("audio_codec", "NONE"), ("ffmpeg_preset", "GOOD")):
        try:
            setattr(ff, attr, val)
        except Exception:
            pass
    sc.render.fps = int(round(float(fps_v)))
    sc.frame_start = 1
    sc.frame_end = int(frames)
    sc.frame_step = 1
    sc.render.filepath = prefix
    for i in range(int(frames)):
        az = float(az_start) + float(orbit) * i / float(frames)
        _look_at(cam, _orbit_loc(center, cam_dist, az, elev), center)
        f = i + 1
        cam.keyframe_insert(data_path="location", frame=f)
        cam.keyframe_insert(data_path="rotation_euler", frame=f)
    try:
        ad = cam.animation_data
        act = ad.action if ad is not None else None
        if act is not None:
            for fc in act.fcurves:
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
    except Exception:
        pass
    t0 = time.perf_counter()
    bpy.ops.render.render(animation=True)
    render_ms = int(round((time.perf_counter() - t0) * 1000.0))
    path = _find_video_file(root, stem + "_turntable")
    if path is None:
        raise RuntimeError("FFMPEG 渲染后没找到视频文件（目录：%s）" % sorted(os.listdir(root))[:8])
    rec = _file_receipt(path)
    with open(path, "rb") as f:
        head = f.read(12)
    rec.update({"ok": True, "magic_ok": head[4:8] == b"ftyp", "container": "MPEG4", "codec": "H264",
                "frames": int(frames), "fps": int(round(float(fps_v))),
                "duration_s": round(float(frames) / float(fps_v), 3), "res": list(res),
                "render_ms": render_ms, "prefix": prefix})
    if not rec["magic_ok"]:
        raise RuntimeError("视频魔数不对（第 4-8 字节不是 ftyp）：%s" % path)
    return rec


def _render_turntable_sequence(sc, cam, center, cam_dist, elev, az_start, orbit, frames, outdir, res):
    outdir = _norm_path(outdir)
    os.makedirs(outdir, exist_ok=True)
    try:
        for fn in list(os.listdir(outdir)):
            if fn.startswith("frame_") and fn.lower().endswith(".png"):
                os.remove(os.path.join(outdir, fn))
    except Exception:
        pass
    try:
        if cam.animation_data is not None:
            cam.animation_data_clear()
    except Exception:
        pass
    try:
        sc.render.image_settings.media_type = "IMAGE"
    except Exception:
        pass
    ims = sc.render.image_settings
    ims.file_format = "PNG"
    ims.color_mode = "RGBA"
    ims.color_depth = "8"
    t0 = time.perf_counter()
    files = []
    for i in range(int(frames)):
        az = float(az_start) + float(orbit) * i / float(frames)
        _look_at(cam, _orbit_loc(center, cam_dist, az, elev), center)
        try:
            sc.frame_set(i + 1)
        except Exception:
            pass
        path = _norm_path(os.path.join(outdir, "frame_%04d.png" % (i + 1)))
        sc.render.filepath = path
        bpy.ops.render.render(write_still=True)
        files.append(_file_receipt(path))
    ms = int(round((time.perf_counter() - t0) * 1000.0))
    manifest = "|".join(f["md5"] for f in files)
    return {"ok": True, "dir": outdir, "pattern": "frame_%04d.png", "count": len(files),
            "files": [f["path"] for f in files], "res": list(res),
            "bytes_total": int(sum(f["bytes"] for f in files)),
            "md5_manifest": hashlib.md5(manifest.encode("utf-8")).hexdigest(),
            "first": files[0] if files else None, "last": files[-1] if files else None,
            "ms": ms}


def _tt_params(objects, collection, name, frames, fps, res, elev_deg, distance, background, fmt,
               orbit_deg, out_dir=None):
    """转台参数解析 + 相机 meta；坏值 raise ValueError（调用方转 _err）。"""
    objs = _find_model_objects(objects=objects, name=name, collection=collection)
    nm = _safe_token(name or collection or "turntable", "name")
    nframes = int(_num(frames, "frames", default=96, gt=0.0, maxv=1200.0))
    fps_v = _num(fps, "fps", default=24.0, gt=0.0, maxv=120.0)
    res_v = _parse_res([512, 512] if res is None else res)
    elev = _num(elev_deg, "elev_deg", default=15.0, minv=-89.0, maxv=89.0)
    dmult = _num(distance, "distance", default=1.6, gt=0.0, maxv=20.0)
    orbit = _num(orbit_deg, "orbit_deg", default=360.0, gt=0.0, maxv=3600.0)
    fmt_s = str(fmt if fmt is not None else "mp4").strip().lower()
    if fmt_s not in ("mp4", "png", "sequence"):
        raise ValueError("fmt 只支持 mp4 / png(sequence)（收到 %r）" % (fmt,))
    bg_val, bg_name, transparent = _bg_rgb(background)
    root = _root_outdir(out_dir)
    mn, mx = _bbox_bu(objs)
    center = (mn + mx) * 0.5
    radius = max(float((mx - mn).length) * 0.5, 1e-5)
    tan_half = _tan_half(res_v, 50.0)
    fit_dist = radius / max(math.sin(math.atan(tan_half)), 1e-6) * 1.12
    cam_dist = fit_dist * dmult
    cam_meta = {"az_start_deg": -90.0, "orbit_deg": round(orbit, 3), "elev_deg": round(elev, 3),
                "distance_mult": round(dmult, 4), "fit_distance": round(fit_dist, 6),
                "camera_distance": round(cam_dist, 6), "radius": round(radius, 6),
                "center_mm": [round(float(v) * _mm_per_bu(), 3) for v in center],
                "lens_mm": 50.0, "sensor_width_mm": 36.0}
    return {"objs": objs, "nm": nm, "nframes": nframes, "fps_v": fps_v, "res_v": res_v,
            "elev": elev, "dmult": dmult, "orbit": orbit, "fmt_s": fmt_s,
            "background": background, "transparent": transparent, "root": root,
            "center": center, "cam_dist": cam_dist, "cam_meta": cam_meta}


def _tt_render(sc, cam, pr, warnings):
    """渲染视频或 PNG 序列（MP4 失败自动回退序列）；返回 (video, sequence, degraded, error, pres, fatal)。"""
    fmt_s = pr["fmt_s"]
    video = None
    sequence = None
    degraded = False
    error = None
    if fmt_s == "mp4":
        if pr["transparent"]:
            warnings.append("background=transparent 不支持视频（MP4 无 alpha）→ 已按 white 渲染；"
                            "要带 alpha 的帧请用 fmt=png")
        pres = _present_setup(sc, pr["res_v"], "white" if pr["transparent"] else pr["background"])
        try:
            video = _render_turntable_video(sc, cam, pr["center"], pr["cam_dist"], pr["elev"], -90.0, pr["orbit"],
                                            pr["nframes"], pr["root"], pr["nm"], pr["fps_v"], pr["res_v"])
        except Exception as e:
            degraded = True
            error = "MP4 输出失败：%s: %s" % (type(e).__name__, str(e)[:220])
            warnings.append(error + " → 回退 PNG 序列")
            try:
                _clean_prefix_files(pr["root"], pr["nm"] + "_turntable",
                                    (".mp4", ".mov", ".mkv", ".avi", ".webm"))
            except Exception:
                pass
            try:
                sequence = _render_turntable_sequence(
                    sc, cam, pr["center"], pr["cam_dist"], pr["elev"], -90.0, pr["orbit"], pr["nframes"],
                    os.path.join(pr["root"], pr["nm"] + "_turntable_frames"), pr["res_v"])
            except Exception as e2:
                return None, None, True, error, None, _err(
                    "MP4 与 PNG 序列都失败：%s / %s" % (str(e)[:140], str(e2)[:140]), op="turntable")
    else:
        pres = _present_setup(sc, pr["res_v"], pr["background"])
        sequence = _render_turntable_sequence(
            sc, cam, pr["center"], pr["cam_dist"], pr["elev"], -90.0, pr["orbit"], pr["nframes"],
            os.path.join(pr["root"], pr["nm"] + "_turntable_frames"), pr["res_v"])
    return video, sequence, degraded, error, pres, None


def _tt_payload(pr, pres, video, sequence, degraded, error, warnings, t_start):
    """转台回执（video 优先，degraded 时 ok=false + error）。"""
    fps_v = pr["fps_v"]
    nframes = pr["nframes"]
    fps_out = int(round(fps_v)) if abs(fps_v - round(fps_v)) < 1e-6 else round(fps_v, 3)
    out = {"op": "turntable", "name": pr["nm"], "engine": "BLENDER_WORKBENCH", "fmt": pr["fmt_s"],
           "frames": nframes, "fps": fps_out,
           "duration_s": round(float(nframes) / float(fps_v), 3), "res": pr["res_v"],
           "background": pres["background"], "camera": pr["cam_meta"],
           "objects": [ob.name for ob in pr["objs"]], "object_count": len(pr["objs"]),
           "video": video, "sequence": sequence, "warnings": warnings,
           "degraded": bool(degraded),
           "ms": {"render": ((video or {}).get("render_ms") or (video or {}).get("ms")
                             or (sequence or {}).get("ms"))}}
    if video is not None:
        out.update({"ok": True, "path": video["path"], "bytes": video["bytes"], "md5": video["md5"]})
    elif sequence is not None:
        out.update({"ok": not degraded, "path": sequence["dir"],
                    "bytes": sequence["bytes_total"], "md5": sequence["md5_manifest"]})
    else:
        out.update({"ok": False, "error": "没有产物"})
    if degraded:
        out["ok"] = False
        out["error"] = error
    out["ms"]["total"] = int(round((time.perf_counter() - t_start) * 1000.0))
    return out


def _tt_cleanup(sc, cam, cam_data, hidden, st):
    """移除相机/孤立动画/相机数据，恢复隐藏状态与场景状态。"""
    act = None
    try:
        ad = cam.animation_data if cam is not None else None
        act = ad.action if ad is not None else None
    except Exception:
        act = None
    try:
        if cam is not None:
            bpy.data.objects.remove(cam, do_unlink=True)
    except Exception:
        pass
    try:
        if act is not None and getattr(act, "users", 0) == 0:
            bpy.data.actions.remove(act)
    except Exception:
        pass
    if cam_data is not None and getattr(cam_data, "users", 0) == 0:
        try:
            bpy.data.cameras.remove(cam_data)
        except Exception:
            pass
    _unhide_objects(hidden)
    _present_state_restore(sc, st)


def blockout_turntable(objects=None, collection=None, name=None, out_dir=None, frames=96, fps=24,
                       res=None, elev_deg=15, distance=1.6, background="white", fmt="mp4", orbit_deg=360):
    """绕 Z 轴转台：内置 FFMPEG 出 MP4（失败回退 PNG 序列，ok:false/degraded + warnings）。"""
    t_start = time.perf_counter()
    try:
        pr = _tt_params(objects, collection, name, frames, fps, res, elev_deg, distance,
                        background, fmt, orbit_deg, out_dir)
    except ValueError as e:
        return _err(e, hint="turntable 用 objects=/name=/collection= 指定已建白模；参数见 help().ops.turntable",
                    op="turntable")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), op="turntable")
    sc = bpy.context.scene
    st = _present_state_save(sc)
    hidden = []
    cam = None
    cam_data = None
    warnings = []
    try:
        hidden = _hide_other_objects(sc, pr["objs"])
        cam_data = bpy.data.cameras.new("BO_TT_CAM")
        cam = bpy.data.objects.new("BO_TT_CAM", cam_data)
        sc.collection.objects.link(cam)
        cam_data.lens = 50.0
        cam_data.sensor_width = 36.0
        cam_data.sensor_fit = "AUTO"
        cam_data.clip_start = 1e-4
        cam_data.clip_end = 1e4
        sc.camera = cam
        video, sequence, degraded, error, pres, fatal = _tt_render(sc, cam, pr, warnings)
        if fatal is not None:
            return fatal
        return _tt_payload(pr, pres, video, sequence, degraded, error, warnings, t_start)
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)[:280]),
                    hint="转台需要已建白模（BO-* 对象）；检查场景/对象名", op="turntable")
    finally:
        _tt_cleanup(sc, cam, cam_data, hidden, st)
