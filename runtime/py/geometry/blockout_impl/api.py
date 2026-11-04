# -*- coding: utf-8 -*-
"""Split from blockout.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .templates import _parse_res
from .geometry import _alpha_stats, _bbox_bu, _bbox_mm, _fit_camera, _make_sheet, _tan_half
from .ops import _find_model_objects, blockout_base, blockout_batch, blockout_export
from .present import _REF_PERSP_ANGLES, _REF_VIEW_ALIASES, _bg_rgb, _hide_other_objects, _look_at, _present_setup, _present_state_restore, _present_state_save, _unhide_objects, blockout_turntable

def _parse_refsheet_views(raw):
    if raw is None:
        raw = ["front", "side", "top", "iso"]
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("views 必须是非空 list（默认 front/side/top/iso）")
    if len(raw) > 8:
        raise ValueError("views 最多 8 个（收到 %d）" % len(raw))
    out, seen = [], set()
    for i, v in enumerate(raw):
        if not isinstance(v, str) or not v.strip():
            raise ValueError("views[%d] 必须是视角名字符串（收到 %r）" % (i, v))
        key = _REF_VIEW_ALIASES.get(v.strip().lower())
        if key is None:
            raise ValueError("views[%d] 不支持 %r（可用：front/side/right/left/back/top/bottom/iso）"
                             % (i, v))
        if key in seen:
            raise ValueError("views 里的视角重复：%s" % key)
        seen.add(key)
        out.append(key)
    return out


def _setup_ref_camera(sc, cam, cam_data, mn, mx, view, res, ortho, lens=50.0, margin=1.12):
    """固定机位：front/side/top 正交（ortho=true 时），iso 透视；正交感光按 bbox 投影占满。"""
    center = (mn + mx) * 0.5
    half = (mx - mn) * 0.5
    radius = max(float(half.length), 1e-5)
    k = _mm_per_bu()
    cam_data.lens = float(lens)
    cam_data.sensor_width = 36.0
    cam_data.sensor_fit = "AUTO"
    cam_data.clip_start = 1e-4
    cam_data.clip_end = 1e4
    ortho_views = ("front", "back", "left", "side", "top", "bottom")
    if bool(ortho) and view in ortho_views:
        cam_data.type = "ORTHO"
        dirs = {"front": Vector((0.0, -1.0, 0.0)), "back": Vector((0.0, 1.0, 0.0)),
                "side": Vector((1.0, 0.0, 0.0)), "left": Vector((-1.0, 0.0, 0.0)),
                "top": Vector((0.0, 0.0, 1.0)), "bottom": Vector((0.0, 0.0, -1.0))}
        _look_at(cam, center + dirs[view] * max(radius * 4.0, 1.0), center)
        if view in ("front", "back"):
            ex, ey = float(half.x), float(half.z)
        elif view in ("side", "left"):
            ex, ey = float(half.y), float(half.z)
        else:
            ex, ey = float(half.x), float(half.y)
        cam_data.ortho_scale = max(max(ex, ey) * 2.0 * float(margin), 1e-4)
        meta = {"projection": "ortho", "az_deg": None, "el_deg": None,
                "ortho_scale": round(float(cam_data.ortho_scale), 6),
                "ortho_scale_mm": round(float(cam_data.ortho_scale) * k, 3), "lens_mm": None}
    else:
        cam_data.type = "PERSP"
        az, el = _REF_PERSP_ANGLES[view]
        tan_half = _tan_half(res, lens)
        _fit_camera(cam, sc, mn, mx, az, el, tan_half, margin)
        meta = {"projection": "persp", "az_deg": az, "el_deg": el, "lens_mm": float(lens),
                "ortho_scale": None, "ortho_scale_mm": None}
    meta.update({"loc_mm": [round(float(v) * k, 3) for v in cam.location],
                 "rot_euler_deg": [round(math.degrees(float(v)), 3) for v in cam.rotation_euler],
                 "res": list(res)})
    return meta


def _json_receipt(path, payload):
    p = _norm_path(path)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    rec = _file_receipt(p)
    rec["ok"] = True
    return rec


def _rs_params(objects, collection, out_dir, name, views, res, ortho, background, turntable):
    """refsheet 入参解析（对象/视图/分辨率/turntable 键校验/bbox）；坏值 raise ValueError。"""
    objs = _find_model_objects(objects=objects, name=name, collection=collection)
    nm = _safe_token(name or collection or "refsheet", "name")
    view_list = _parse_refsheet_views(views)
    res_v = _parse_res([768, 768] if res is None else res)
    ortho_b = _bool(ortho, "ortho", True)
    bg_val, bg_name, transparent = _bg_rgb(background)
    root = _root_outdir(out_dir)
    tt = None
    if turntable not in (None, False, 0):
        if turntable is True:
            tt = {}
        elif isinstance(turntable, dict):
            tt = dict(turntable)
        else:
            raise ValueError("turntable 必须是 false / true / dict（收到 %r）" % (turntable,))
        unknown = sorted(set(str(k) for k in tt.keys()) -
                         set(["frames", "fps", "elev_deg", "distance", "fmt", "res",
                              "background", "orbit_deg"]))
        if unknown:
            raise ValueError("refsheet.turntable 不认识的键 %s（可用：frames,fps,elev_deg,distance,"
                             "fmt,res,background,orbit_deg）" % unknown)
    mn, mx = _bbox_bu(objs)
    mmu = _mm_per_bu()
    bbox_mm = _bbox_mm(mn, mx)
    center_mm = [round(float(v) * mmu, 3) for v in ((mn + mx) * 0.5)]
    return {"objs": objs, "nm": nm, "view_list": view_list, "res_v": res_v, "ortho_b": ortho_b,
            "bg_val": bg_val, "bg_name": bg_name, "transparent": transparent, "root": root,
            "tt": tt, "mn": mn, "mx": mx, "mmu": mmu, "bbox_mm": bbox_mm, "center_mm": center_mm}


def _rs_render_views(sc, cam, cam_data, pr):
    """逐视图设机位 → 渲染 PNG → 回执行；返回 (view_rows, view_paths)。"""
    view_rows = []
    view_paths = []
    for view in pr["view_list"]:
        meta = _setup_ref_camera(sc, cam, cam_data, pr["mn"], pr["mx"], view, pr["res_v"], pr["ortho_b"])
        path = _norm_path(os.path.join(pr["root"], "%s_refsheet_%s.png" % (pr["nm"], view)))
        sc.render.filepath = path
        try:
            sc.render.image_settings.media_type = "IMAGE"
            sc.render.image_settings.file_format = "PNG"
            sc.render.image_settings.color_mode = "RGBA"
        except Exception:
            pass
        t1 = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        ms = int(round((time.perf_counter() - t1) * 1000.0))
        rec = _file_receipt(path)
        rec.update({"view": view, "ms": ms, "res": list(pr["res_v"]), "camera": meta,
                    "coverage_estimate": _alpha_stats(path).get("coverage") if pr["transparent"] else None})
        view_rows.append(rec)
        view_paths.append(path)
    return view_rows, view_paths


def _rs_turntable(pr, warnings):
    """内嵌转台（失败只记 warnings，不炸整包）；返回 (tt_rec, tt_ok)。"""
    tt = pr["tt"]
    if tt is None:
        return None, True
    tt_args = {"objects": [ob.name for ob in pr["objs"]], "name": pr["nm"] + "_refsheet",
               "out_dir": pr["root"], "frames": 120, "fps": 24, "elev_deg": 15, "fmt": "mp4",
               "background": "white" if pr["transparent"] else pr["bg_name"]}
    tt_args.update(tt)
    try:
        tt_rec = blockout_turntable(**tt_args)
        tt_ok = tt_rec.get("ok") is True
        if not tt_ok:
            warnings.append("refsheet 内嵌 turntable 未成功：%s"
                            % str(tt_rec.get("error") or "?")[:200])
            for w in (tt_rec.get("warnings") or []):
                warnings.append("turntable: %s" % str(w)[:160])
        return tt_rec, tt_ok
    except Exception as e:
        warnings.append("refsheet 内嵌 turntable 异常：%s" % str(e)[:180])
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])}, False


def _rs_manifest(pr, pres, view_rows, sheet, render_fps, tt_rec):
    """refsheet.json 的 payload（schema/单位/相机/视图/md5/turntable 摘要）。"""
    return {
        "schema": "dsh.blockout.refsheet/1", "version": BLOCKOUT_VERSION, "name": pr["nm"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "engine": "BLENDER_WORKBENCH", "view_transform": "Standard",
        "units": {"mm_per_unit": round(pr["mmu"], 6), "unit": "mm"},
        "bbox_mm": pr["bbox_mm"], "center_mm": pr["center_mm"],
        "forward_axis": "-Y", "up_axis": "Z", "screen_right_axis": "+X",
        "note": "front=相机在 -Y 看 +Y（看到的正面朝 -Y）；side=+X 看 -X；top=+Z 看 -Z；iso=透视",
        "res": list(pr["res_v"]), "ortho": bool(pr["ortho_b"]), "background": pr["bg_name"],
        "neutral_color": pres.get("neutral_color"), "render_fps": render_fps,
        "objects": [ob.name for ob in pr["objs"]], "object_count": len(pr["objs"]),
        "views": view_rows, "sheet": sheet,
        "md5": {"views": {v["view"]: v["md5"] for v in view_rows}, "sheet": sheet.get("md5")},
        "fps": render_fps, "frame_count": len(view_rows),
        "turntable": ({"ok": tt_rec.get("ok"), "path": tt_rec.get("path"),
                       "bytes": tt_rec.get("bytes"), "md5": tt_rec.get("md5"),
                       "frames": tt_rec.get("frames"), "fps": tt_rec.get("fps"),
                       "duration_s": tt_rec.get("duration_s"), "res": tt_rec.get("res"),
                       "degraded": tt_rec.get("degraded"),
                       "warnings": (tt_rec.get("warnings") or [])[:6]}
                      if tt_rec is not None else None),
    }


def _rs_cleanup(sc, cam, cam_data, hidden, st):
    """移除相机/相机数据，恢复隐藏状态与场景状态。"""
    try:
        if cam is not None:
            bpy.data.objects.remove(cam, do_unlink=True)
    except Exception:
        pass
    if cam_data is not None and getattr(cam_data, "users", 0) == 0:
        try:
            bpy.data.cameras.remove(cam_data)
        except Exception:
            pass
    _unhide_objects(hidden)
    _present_state_restore(sc, st)


def blockout_refsheet(objects=None, collection=None, out_dir=None, name=None, views=None, res=None,
                      ortho=True, background="white", turntable=None):
    """同机位参考图包：4 视图 PNG（正交前/侧/顶 + 透视 iso）+ 拼图 + refsheet.json（+ 可选转台 MP4）。"""
    t_start = time.perf_counter()
    try:
        pr = _rs_params(objects, collection, out_dir, name, views, res, ortho, background, turntable)
    except ValueError as e:
        return _err(e, hint="refsheet 用 objects=/name=/collection= 指定已建白模；参数见 help().ops.refsheet",
                    op="refsheet")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), op="refsheet")
    sc = bpy.context.scene
    st = _present_state_save(sc)
    hidden = []
    cam = None
    cam_data = None
    warnings = []
    try:
        hidden = _hide_other_objects(sc, pr["objs"])
        pres = _present_setup(sc, pr["res_v"], pr["bg_name"])
        cam_data = bpy.data.cameras.new("BO_REF_CAM")
        cam = bpy.data.objects.new("BO_REF_CAM", cam_data)
        sc.collection.objects.link(cam)
        sc.camera = cam
        view_rows, view_paths = _rs_render_views(sc, cam, cam_data, pr)
        cols = 2 if len(view_rows) >= 4 else len(view_rows)
        sheet = _make_sheet(view_paths, [v["view"] for v in view_rows],
                            os.path.join(pr["root"], "%s_refsheet_sheet.png" % pr["nm"]), cols=cols)
        render_fps = None
        try:
            render_fps = int(round(float(sc.render.fps)))
        except Exception:
            pass
        tt_rec, tt_ok = _rs_turntable(pr, warnings)
        payload = _rs_manifest(pr, pres, view_rows, sheet, render_fps, tt_rec)
        json_path = os.path.join(pr["root"], "%s_refsheet.json" % pr["nm"])
        json_rec = _json_receipt(json_path, payload)
        files = {"views": view_rows, "sheet": sheet, "json": json_rec,
                 "all": [v["path"] for v in view_rows] + [sheet["path"], json_rec["path"]]}
        if tt_rec is not None:
            files["turntable"] = tt_rec
            files["all"].extend([tt_rec.get("path")] if tt_rec.get("path") else [])
        ok = bool(view_rows and len(view_rows) == len(pr["view_list"]) and sheet.get("md5")
                  and json_rec.get("md5") and tt_ok)
        out = {"op": "refsheet", "name": pr["nm"], "engine": "BLENDER_WORKBENCH",
               "units": {"mm_per_unit": round(pr["mmu"], 6), "unit": "mm"},
               "bbox_mm": pr["bbox_mm"], "forward_axis": "-Y", "up_axis": "Z",
               "views": view_rows, "sheet": sheet, "files": files,
               "refsheet_json": json_rec["path"], "md5": json_rec["md5"],
               "turntable": tt_rec, "warnings": warnings,
               "ms": {"views": int(sum(v.get("ms") or 0 for v in view_rows)),
                      "turntable": ((tt_rec or {}).get("ms") or {}).get("total")
                      if isinstance((tt_rec or {}).get("ms"), dict) else (tt_rec or {}).get("ms"),
                      "total": int(round((time.perf_counter() - t_start) * 1000.0))}}
        out["ok"] = ok
        if not ok:
            if tt_rec is not None and not tt_ok:
                out["error"] = "refsheet 视图/JSON 已产出，但内嵌 turntable 失败（见 warnings）"
            else:
                out["error"] = "refsheet 产物不完整（views/sheet/json 缺失）"
        return out
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)[:280]),
                    hint="refsheet 需要已建白模（BO-* 对象）；检查场景/对象名/输出目录权限", op="refsheet")
    finally:
        _rs_cleanup(sc, cam, cam_data, hidden, st)


# ────────────────────────────── op: selftest / help ──────────────────────────────
def blockout_selftest():
    t0 = time.perf_counter()
    ev = {}
    names = []
    colname = None
    try:
        root = _root_outdir(None)
        d = _norm_path(os.path.join(root, "blockout_selftest"))
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = tempfile.mkdtemp(prefix="dsh_blockout_")
        r = blockout_base(
            name="unittest", parts=[
                {"id": "a", "prim": "box", "size_mm": [20, 20, 20], "at_mm": [0, 0, 10]},
                {"id": "b", "prim": "cylinder", "radius_mm": 6, "depth_mm": 20, "at_mm": [30, 0, 10], "axis": "x"},
                {"id": "c", "prim": "uv_sphere", "radius_mm": 5, "at_mm": [-30, 0, 10]},
                {"id": "d", "prim": "box", "size_mm": [8, 8, 8], "at_mm": [0, 30, 10], "mirror": True},
            ],
            render={"res": [96, 96], "views": ["front"], "sheet": False, "via": "internal"},
            export={"glb": True}, audit=True, outdir=d)
        names = list(r.get("objects") or [])
        colname = r.get("collection")
        ev["base_ok"] = r.get("ok") is True
        ev["object_count"] = r.get("object_count")
        ev["total_tris"] = r.get("total_tris")
        ev["total_tris_eval"] = r.get("total_tris_eval")
        ev["bbox_mm"] = r.get("bbox_mm")
        ev["audit_ok"] = (r.get("audit") or {}).get("ok")
        ev["views"] = [{"name": v.get("name"), "bytes": v.get("bytes"),
                        "md5": bool(v.get("md5")), "coverage": v.get("coverage_estimate")}
                       for v in ((r.get("render") or {}).get("views") or [])]
        ev["glb"] = {k: (r.get("export") or {}).get("glb", {}).get(k)
                     for k in ("path", "bytes", "magic_ok", "nodes", "md5")}
        bbox = r.get("bbox_mm") or {}
        # turntable / refsheet 冒烟（小帧数/小分辨率，只看产物与魔数）
        tt_ok = False
        try:
            tt = blockout_turntable(objects=names, name="unittest", out_dir=d, frames=4, fps=12,
                                    res=[64, 64], fmt="mp4")
            vid = tt.get("video") or {}
            seq = tt.get("sequence") or {}
            ev["turntable"] = {"ok": tt.get("ok"), "degraded": tt.get("degraded"),
                               "video": bool(vid.get("path")), "magic_ok": vid.get("magic_ok"),
                               "bytes": vid.get("bytes"), "sequence": seq.get("count"),
                               "frames": tt.get("frames"), "fps": tt.get("fps"),
                               "ms": (tt.get("ms") or {}).get("total"),
                               "error": tt.get("error"), "warnings": (tt.get("warnings") or [])[:2]}
            tt_ok = (tt.get("ok") is True and vid.get("magic_ok") is True
                     and int(vid.get("bytes") or 0) > 0) or \
                    (tt.get("degraded") is True and int(seq.get("count") or 0) == 4)
        except Exception as e:
            ev["turntable"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:180])}
        rs_ok = False
        try:
            rs = blockout_refsheet(objects=names, name="unittest", out_dir=d,
                                   views=["front", "side", "top", "iso"], res=[128, 128],
                                   ortho=True, background="white", turntable=None)
            files = rs.get("files") or {}
            ev["refsheet"] = {"ok": rs.get("ok"),
                              "views": len(files.get("views") or []),
                              "sheet": bool((files.get("sheet") or {}).get("bytes")),
                              "json": rs.get("refsheet_json"), "md5": rs.get("md5"),
                              "bbox_mm": rs.get("bbox_mm"),
                              "ms": (rs.get("ms") or {}).get("total"),
                              "error": rs.get("error"), "warnings": (rs.get("warnings") or [])[:2]}
            rs_ok = (rs.get("ok") is True and len(files.get("views") or []) == 4
                     and bool(rs.get("refsheet_json")) and bool(rs.get("md5"))
                     and os.path.isfile(rs.get("refsheet_json") or ""))
        except Exception as e:
            ev["refsheet"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:180])}
        ev["checks"] = {
            "object_count_5": ev["object_count"] == 5,
            "tris_box_12_each": bool(ev["total_tris"] and ev["total_tris"] >= 4 * 12),
            "bbox_z_bottom_0": bool(bbox.get("min") and abs(bbox["min"][2]) < 0.02),
            "bbox_size_x_75": bool(bbox.get("size") and abs(bbox["size"][0] - 75.0) < 0.50),
            "render_view_ok": bool(ev["views"] and ev["views"][0].get("bytes", 0) > 0),
            "glb_ok": bool(ev["glb"].get("bytes") and ev["glb"].get("magic_ok")),
            "audit_ok": ev["audit_ok"] is True,
            "turntable_ok": bool(tt_ok),
            "refsheet_ok": bool(rs_ok),
        }
        ok = all(bool(v) for v in ev["checks"].values())
        return {"ok": bool(ok), "evidence": ev, "ms": int(round((time.perf_counter() - t0) * 1000.0))}
    except Exception as e:
        return {"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}
    finally:
        try:
            _remove_objects(names)
            if colname:
                _remove_collection_if_empty(colname)
        except Exception:
            pass


def blockout_help():
    return {
        "module": "blockout.py", "version": BLOCKOUT_VERSION,
        "what": "Blockout 白模：bmesh 直建 primitive（mm 输入、底部贴地、统一命名/集合/白材质/Bevel），"
                "可一次渲染 3 视图 + 拼图 + 导出 GLB/OBJ；batch 一次进程建 N 件",
        "ops": {
            "base": {"doc": "建一件白模（几何 + 可选 render/export/audit）",
                     "args": ["name", "template(none|humanoid_prim|vehicle)", "units(mm|cm|m)",
                              "anchor(bottom|center|origin)", "parts", "landmarks", "collection",
                              "render", "export", "audit", "reset", "bevel_mm", "template_scale", "outdir"]},
            "batch": {"doc": "一次进程建 N 件（逐件 collection；cleanup=true 时逐件清理）",
                      "args": ["models", "outdir", "export", "collection", "cleanup", "render"]},
            "export": {"doc": "对已建场景补视图/补 GLB/OBJ（不重建）",
                       "args": ["objects", "name", "collection", "glb", "obj", "export", "render", "outdir"]},
            "turntable": {"doc": "绕 Z 轴转台视频：Blender 内置 FFMPEG 出 MP4（distance=自动取景距离倍数；"
                                 "背景透明会按 white 渲并 warning；失败回退 PNG 序列 + ok:false/degraded）",
                          "args": ["objects", "collection", "name", "out_dir", "frames", "fps", "res",
                                   "elev_deg", "distance", "background(white|gray|black|transparent)",
                                   "fmt(mp4|png)", "orbit_deg"]},
            "refsheet": {"doc": "同机位参考图包：正交前/侧/顶 + 透视 iso PNG + 拼图 + refsheet.json"
                                "（views 默认 front/side/top/iso；turntable={frames,fps,elev_deg,...} 可选）",
                         "args": ["objects", "collection", "out_dir", "name", "views", "res", "ortho",
                                  "background", "turntable"]},
            "selftest": {"doc": "模块自检（建 4 件 + 1 视图 + GLB + audit + 转台/参考图冒烟，跑完清理）"},
            "help": {"doc": "本说明"},
        },
        "part_schema": {
            "box": {"id": "p1", "prim": "box", "size_mm": [100, 60, 40], "at_mm": [0, 0, 20],
                    "rot_deg": [0, 0, 0], "mirror": False},
            "cylinder": {"id": "w1", "prim": "cylinder", "radius_mm": 30, "depth_mm": 80,
                         "axis": "x|y|z", "at_mm": [0, 0, 30], "mirror": True},
            "uv_sphere": {"id": "h1", "prim": "uv_sphere", "radius_mm": 25, "at_mm": [0, 0, 50]},
        },
        "render": {"engine": "workbench（只此一种）", "res": "[512,512]",
                   "views": "auto3 | front,right,top | [{name,az,el}] | az=35,el=20",
                   "sheet": "true=单行拼图 / 数字=列数；qc_render 路径也自己拼",
                   "via": "auto（优先 qc_render）/ internal / qc"},
        "protocol": [
            "先 base 拿 bbox_mm 与三段 ms（geom/render/export/total），再决定加细节；",
            "装配前用 collection 隔离；要保留多件就 batch(cleanup=false) 且 name 唯一；",
            "GLB 导出走 export_apply（Bevel 只作用于导出副本，场景里修改器不 apply）；",
            "对已建场景补图/补导出用 export op（不要重跑 base）；",
            "要交接/给参考图：refsheet（固定机位白底四视图 + refsheet.json + 可选转台）一次出齐；",
            "要展示片/转台：turntable（内置 FFMPEG；先渲染后编码，成片路径/bytes/md5 都在回执里）。",
        ],
        "limits": [
            "渲染只支持 Workbench（EEVEE/Cycles 未接）；render op 的视角是透视；refsheet 的 front/side/top 是正交；",
            "turntable MP4 走 Blender 内置 FFMPEG（media_type=VIDEO；不依赖系统 ffmpeg）；本机 Blender 缺 FFmpeg 时回退 PNG 序列并 ok:false/degraded；",
            "turntable 视频无 alpha（background=transparent 会按 white 渲并 warning；要 alpha 用 fmt=png）；",
            "template 只是比例占位（humanoid_prim/vehicle 的 primitive 骨架），不是解剖/车辆细节模型；",
            "landmarks 只记录不建空物体；audit 是内建轻量体检，不是 audit.py 全套；",
            "sheet 无文字标注；refsheet 用中性灰 SINGLE 色（0.62）避免白模白底看不清；",
            "转台/参考图只临时改相机/渲染设置/背景并在 finally 还原（不改用户材质、不 apply 修改器）。",
        ],
        "presentation": {"engine": "BLENDER_WORKBENCH", "view_transform": "Standard",
                          "neutral_color": 0.62, "background": "white|gray|black|transparent",
                          "forward_axis": "-Y（front 相机在 -Y 看 +Y）", "up_axis": "Z"},
        "kernel": "共享内核 K.dsh_kit v%s" % getattr(_KIT, "version", "?"),
        "templates": list(ALLOWED_TEMPLATES), "prims": list(ALLOWED_PRIMS),
        "fake_units_note": "units 只是输入解释口径；scene.unit_settings.scale_length 原样保留",
    }


# 注册（一行）：K.dsh_blockout_api["base"] / K.dsh_blockout_api("base", {...})
_KIT.register("blockout", BLOCKOUT_VERSION,
              {"base": blockout_base, "batch": blockout_batch, "export": blockout_export,
               "turntable": blockout_turntable, "refsheet": blockout_refsheet,
               "selftest": blockout_selftest, "help": blockout_help},
              extra={"templates": list(ALLOWED_TEMPLATES), "prims": list(ALLOWED_PRIMS),
                     "view_auto3": list(VIEW_AUTO3), "ref_views": ["front", "side", "top", "iso"],
                     "turntable_fmt": ["mp4", "png"]})