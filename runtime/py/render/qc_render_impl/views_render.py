# -*- coding: utf-8 -*-
"""qc_render_views 的后半段：现场搭建/逐视角渲染/批后核对/现场还原/回执组装。

状态经 st(dict) 传递：helper 进入时从 st 解包、退出时把本地变量写回 st（成功/异常都写）。
因此异常路径下 finally 仍能拿到"已经改到一半的现场"，与拆分前语义一致。
"""
from .common import *  # noqa: F401,F403
from .lock import _lock_release
from .geom import _apply_step, _corners, _db_counts, _focus_box, _md5, _montage_api, _project_px, _safe, _solve_view
from .rig import _add_rig, _aim_rig, _alpha_stats, _device_info, _device_line, _device_live, _device_slim, _engine_label
from .parts import _legend_line_count, _parts_override, _parts_restore, _write_parts_legend


def _qv_scene(st):
    a, css, hidden_lights, lights_cfg, lights_mode = (
        st["a"], st["css"], st["hidden_lights"], st["lights_cfg"], st["lights_mode"])
    parts_assign, parts_enabled, parts_flat, parts_group_by, pc_objs = (
        st["parts_assign"], st["parts_enabled"], st["parts_flat"], st["parts_group_by"], st["pc_objs"])
    rs, sc, sensor_def, vt_mode, vt_target = (
        st["rs"], st["sc"], st["sensor_def"], st["vt_mode"], st["vt_target"])
    pc_err = (st.get("pc_err"))
    try:
        # ---- v0.8.11（A）：逐部件配色 —— 覆盖材质槽（放在三点光之前：万一覆盖就失败，灯还没建）
        if parts_enabled:
            pc_state, pc_mats, pc_err = _parts_override(pc_objs, parts_assign,
                                                        lambda ob: (sorted([str(c.name) for c in ob.users_collection])
                                                                    or ["(无集合)"])[0]
                                                        if parts_group_by == "collection" else str(ob.name),
                                                        parts_flat)
        if pc_err:
            raise RuntimeError(pc_err)
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
        # ---- v0.9.1（A2）：色彩变换实际写入 + **读回核对**（写不进去/名字本机没有 → 不许带病出图）
        vt_apply_err = None
        if vt_mode != "scene":
            if css is None:
                vt_apply_err = "场景没有 view_settings，无法保证色彩变换"
            else:
                try:
                    css.view_transform = str(vt_target)
                except Exception as e:
                    vt_apply_err = "%s: %s" % (type(e).__name__, str(e)[:160])
                vt_now = str(getattr(css, "view_transform", None))
                if vt_apply_err is None and vt_now != str(vt_target):
                    vt_apply_err = "写入后回读是 %r（请求 %r）" % (vt_now, str(vt_target))
                if vt_apply_err is None:
                    vt_used = vt_now
        if vt_apply_err is not None:
            raise RuntimeError("view_transform=%r 没能生效：%s（可用的名字：%s；"
                               "要沿用场景设置请传 \"scene\"）"
                               % (str(vt_target), vt_apply_err, ", ".join(_vt_options()) or "读不到清单"))

        lines = []
        # ---- v0.8.10（C2）：预热帧（不计时/不进预算/不进 jsonl）+ 降质阶梯状态
        do_warmup = bool(a.get("warmup", True))
        warmup_info = None
        ladder = a.get("ladder")
        if isinstance(ladder, dict):
            ladder = [ladder]
        elif isinstance(ladder, str):
            try:
                ladder = json.loads(ladder)
            except Exception:
                ladder = None
        if not isinstance(ladder, list):
            ladder = None
        ladder_idx = 0
        try:
            per_view_budget_ms = int(round(float(a.get("per_view_budget_s")) * 1000)) if a.get("per_view_budget_s") \
                else int(a.get("per_view_budget_ms") or 0)
        except Exception:
            per_view_budget_ms = 0
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_capture_a(st):
    a, base_res, cam_data, center, corners = (
        st["a"], st["base_res"], st["cam_data"], st["center"], st["corners"])
    lens_def, made_cam, margin, mn, mx = st["lens_def"], st["made_cam"], st["margin"], st["mn"], st["mx"]
    outdir, rig, rs, sensor_def, tag = st["outdir"], st["rig"], st["rs"], st["sensor_def"], st["tag"]
    v = st["v"]
    try:
        rv = v.get("res") or base_res
        if isinstance(rv, (int, float)):
            rv = [int(rv), int(rv)]
        w, h = max(16, int(rv[0])), max(16, int(rv[1]))
        lens = float(v.get("lens", lens_def))
        ortho = bool(v.get("ortho", False))
        vmargin = float(v.get("margin", margin))
        nm = _safe(v.get("name"))
        path = _win(v.get("path") or os.path.join(outdir, "%s_%s.png" % (tag, nm)))
        corners_used = corners
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
            # v0.8.10（C5）：世界坐标框选 —— aabb=[...] / focus="x,y,z,r"（按视角优先，其次顶层）
            _frame = _focus_box(v.get("aabb")) or _focus_box(v.get("focus")) or _focus_box(a.get("aabb")) or _focus_box(a.get("focus"))
            if _frame:
                corners_used = _corners(_frame[0], _frame[1])
                _fc = (_frame[0] + _frame[1]) * 0.5
                loc, d, oscale, stats = _solve_view(_fc, corners_used, az, el, w, h, lens, sensor_def, vmargin,
                                                    ortho=ortho, ortho_scale=v.get("ortho_scale"))
                stats["framed_by"] = "aabb/focus"
            else:
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_capture_b(st):
    az, cam_data, corners_used, d, dev_batch = (
        st["az"], st["cam_data"], st["corners_used"], st["d"], st["dev_batch"])
    do_warmup, el, h, idx, ladder = st["do_warmup"], st["el"], st["h"], st["idx"], st["ladder"]
    ladder_idx, lens, loc, made_cam, ortho = (
        st["ladder_idx"], st["lens"], st["loc"], st["made_cam"], st["ortho"])
    outdir, path, per_view_budget_ms, rs, samples = (
        st["outdir"], st["path"], st["per_view_budget_ms"], st["rs"], st["samples"])
    spent_ms, stats, v, w, warmup_info = st["spent_ms"], st["stats"], st["v"], st["w"], st["warmup_info"]
    try:
        # ---- v0.8.10（C2）预热帧：首帧 EEVEE 着色器编译不计时、不进预算、不进 jsonl
        if do_warmup and idx == 0 and warmup_info is None:
            try:
                rs.filepath = _win(os.path.join(outdir, "_warmup.png"))
                tw = time.perf_counter()
                bpy.ops.render.render(write_still=True)
                warmup_info = {"ms": int(round((time.perf_counter() - tw) * 1000.0)),
                               "path": _win(os.path.join(outdir, "_warmup.png")), "counted": False}
            except Exception as e:
                warmup_info = {"error": str(e)[:120], "counted": False}
            rs.filepath = path
        # ---- 渲染（降质阶梯：超 per_view_budget_s 且还有更粗档 → 降一档重渲，逐级留痕）
        step_i = ladder_idx
        ms_hist = []
        while True:
            w_eff, h_eff, step_used = _apply_step(rs, ladder, step_i, w, h)
            t1 = time.perf_counter()
            bpy.ops.render.render(write_still=True)
            ms = int(round((time.perf_counter() - t1) * 1000.0))
            ms_hist.append(ms)
            if per_view_budget_ms and ms > per_view_budget_ms and ladder and step_i + 1 < len(ladder):
                step_i += 1
                continue
            break
        if ladder and step_i != ladder_idx:
            ladder_idx = step_i      # 后续视角沿用更粗的档（逐级留痕见 ladder_trace）
        ladder_used = (ladder[step_i] if ladder else None)
        spent_ms += ms
        # 投影对拍按**实际**渲染分辨率（阶梯降质后分辨率会变）
        pred, pm_src, pm_err = _project_px(made_cam, corners_used, w_eff, h_eff)
        got = os.path.isfile(path)
        nbytes = int(os.path.getsize(path)) if got else 0
        if not got:
            raise RuntimeError("渲染没有产出文件：%s" % path)
        _dev_live = _device_live(dev_batch)      # 渲完即时回读（EEVEE 能在这一步补上真实 renderer 串）
        entry = {"view": str(v.get("name")), "ms": ms, "bytes": nbytes, "hash": _md5(path),
                 "path": path, "res": [w_eff, h_eff], "engine": _engine_label(), "samples": samples,
                 "warmup": bool(do_warmup and idx == 0), "step": (step_i if ladder else 0),
                 "step_cfg": ladder_used, "ms_history": ms_hist,
                 # v0.8.11（B）：这一张"实际用的渲染设备"（渲完即时回读；拿不到就 null + device_source）
                 "device": _device_slim(_dev_live), "device_source": _dev_live.get("source"),
                 "camera": {"az": round(az, 3), "el": round(el, 3), "lens": lens, "ortho": ortho,
                            "loc": [round(float(x), 4) for x in loc],
                            "look_at": [round(float(x), 4) for x in (loc + d)],
                            "ortho_scale": (round(float(cam_data.ortho_scale), 4) if ortho else None)},
                 "frame": {"pred_bbox_px": pred, "proj_source": pm_src, "proj_err_px": pm_err,
                           "solve": {k: (round(float(vv), 4) if isinstance(vv, float) else vv) for k, vv in stats.items()}},
                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_emit(st):
    a, catalog_ortho, entries, entry, jsonl = (
        st["a"], st["catalog_ortho"], st["entries"], st["entry"], st["jsonl"])
    legend_txt, lines, lock_line, parts_enabled, parts_flat = (
        st["legend_txt"], st["lines"], st["lock_line"], st["parts_enabled"], st["parts_flat"])
    parts_rows, path, v, vt_used = st["parts_rows"], st["path"], st["v"], st["vt_used"]
    try:
        # v0.8.11：视角溯源（别名归一/az=el 原样留痕）与 parts_color 图例路径
        if v.get("_requested") and str(v.get("_requested")) != str(v.get("name")):
            entry["view_requested"] = str(v.get("_requested"))
        if v.get("_source"):
            entry["view_source"] = str(v.get("_source"))
        if catalog_ortho and v.get("_ortho_src"):
            entry["ortho_source"] = str(v.get("_ortho_src"))
        if parts_enabled:
            entry["mode"] = "parts_color"
            entry["legend_path"] = legend_txt
            entry["parts"] = len(parts_rows)
            entry["parts_flat"] = bool(parts_flat)
        # ---- v0.9.1（A2/E1）：每行 jsonl 都记"这张图实际用的色彩变换"与"这一批的锁口径"
        # （A2：调用方的分析脚本可据此自适应；E1：waited_ms/holder 让"计时口径"可审计）
        entry["view_transform"] = vt_used
        entry["lock"] = dict(lock_line)
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_post(st):
    a, dev_batch, eng_after, eng_mode, entries = (
        st["a"], st["dev_batch"], st["eng_after"], st["eng_mode"], st["entries"])
    err, legend_txt, outdir, parts_enabled, parts_flat = (
        st["err"], st["legend_txt"], st["outdir"], st["parts_enabled"], st["parts_flat"])
    parts_group_by, parts_info, parts_palette, parts_rows, samples = (
        st["parts_group_by"], st["parts_info"], st["parts_palette"], st["parts_rows"], st["samples"])
    scope_warnings, tag, vt_used = st["scope_warnings"], st["tag"], st["vt_used"]
    try:
        # ---- v0.8.11（B）：批内设备复核 + 引擎回读校验前的实测回读
        dev_after = _device_info()
        _d0 = _device_slim(dev_batch)
        _d1 = _device_slim(dev_after)
        _d_used = dict(dev_after or dev_batch or {})
        _d_used["measured"] = "after_render（批次结束时的实测回读）"
        device_used = _d_used
        device_check = {"same_device": (_d0.get("device") == _d1.get("device") and _d0.get("engine") == _d1.get("engine")),
                        "identical_reading": (_d0 == _d1),
                        "before_render": _d0, "after_render": _d1,
                        "note": "EEVEE 的 renderer 串在后台进程里**首次渲染后**才拿得到（gpu.platform 需 GL 上下文），"
                                "所以 before/after 可能只差 gpu 字段 —— 只要 device/engine 相同就算同一设备"}
        if not device_check["same_device"]:
            scope_warnings.append("渲染前后设备回读不一致（before=%r / after=%r）—— 设备可能在批次中途被改动，"
                                  "本批的 ms 不可比" % (_d0.get("device"), _d1.get("device")))
        elif _d1.get("device") is None:
            scope_warnings.append("设备未知（device=null）：%s" % (_d1.get("source") or "拿不到判据"))

        # ---- v0.8.11（A）：图例落盘（渲染成功才写 —— 没有图却先有图例，是在造假证据）
        if parts_enabled and entries:
            try:
                pc_legend_json = _write_parts_legend(
                    legend_txt, parts_rows,
                    {"version": QC_RENDER_VERSION, "kind": "parts_color_legend",
                     "scene_file": bpy.data.filepath, "outdir": outdir, "tag": tag,
                     "group_by": parts_group_by, "flat": bool(parts_flat),
                     "view_transform": vt_used,
                     "palette_size": len(parts_palette),
                     "palette_srgb": [[int(c[0]), int(c[1]), int(c[2])] for c in parts_palette],
                     "color_space_note": "R,G,B 是 sRGB 0-255（图例色）；材质里写的是同色的线性值"
                                         "（srgb_to_linear），Principled 还叠灯光与色彩变换",
                     "color_reused_by_hash": [r["name"] for r in parts_rows if r.get("color_source") == "hash"],
                     "views": [str(e.get("view")) for e in entries],
                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
                parts_info["legend_path"] = legend_txt
                parts_info["legend_json"] = pc_legend_json
                parts_info["legend_lines"] = _legend_line_count(legend_txt)
                if parts_info["legend_lines"] != len(parts_rows):
                    scope_warnings.append("图例行数 %d != 部件数 %d（图例文件被外部改过？）"
                                          % (parts_info["legend_lines"], len(parts_rows)))
            except Exception as e:
                if err is None:
                    err = {"error": "写图例失败：%s: %s" % (type(e).__name__, str(e)[:200])}

        # ---- v0.8.10（C2）引擎回读校验：判据必须自带"当时是什么引擎"
        _eng_now = _engine_label()
        engine_check = {"requested": eng_mode, "configured_at_start": eng_after, "actual": _eng_now,
                        "rt": bool("+RT" in _eng_now), "samples": samples, "ok": True, "warnings": []}
        if str(eng_mode).lower() in ("eevee", "eevee_rt", "e") and "+RT" not in _eng_now:
            engine_check["ok"] = False
            engine_check["warnings"].append("请求 EEVEE+光追但回读没有 RT —— 玻璃/折射类材质不会正确")
        elif "+RT" not in _eng_now:
            engine_check["warnings"].append("本次引擎未开光追（%s）：与 EEVEE+RT 的历史分数不可直接比较" % _eng_now)

        # ---- v0.8.10（C5）N 宫格接触表（拼图 + 每格指标）
        sheet_path, sheet_metrics = None, None
        if int(a.get("sheet") or 0) > 1 and entries:
            try:
                mapi = _montage_api()
                if mapi is None:
                    scope_warnings.append("sheet 需要 montage 模块（runtime/py/render/montage.py）—— 未找到，已跳过拼图")
                else:
                    _sp = _win(os.path.join(outdir, "%s_sheet.png" % tag))
                    _r = mapi["montage"]([e["path"] for e in entries], int(a.get("sheet_cols") or 0) or None,
                                         int(a.get("sheet_tile") or 420), _sp, [e["view"] for e in entries],
                                         a.get("sheet_grid"), a.get("sheet_scale_m"))
                    if isinstance(_r, dict):
                        sheet_path = _r.get("path") or _sp
                        sheet_metrics = _r.get("tiles")
                    else:
                        sheet_path = _r or _sp
            except Exception as e:
                scope_warnings.append("拼图失败：%s" % str(e)[:140])
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_restore(st):
    css, err, hidden_lights, lock_holder, lock_path = (
        st["css"], st["err"], st["hidden_lights"], st["lock_holder"], st["lock_path"])
    lock_res, made_cam, pc_mats, pc_state, rig = (
        st["lock_res"], st["made_cam"], st["pc_mats"], st["pc_state"], st["rig"])
    samp_before, saved, sc, scope_warnings, vt_before = (
        st["samp_before"], st["saved"], st["sc"], st["scope_warnings"], st["vt_before"])
    vt_mode, vt_target = st["vt_mode"], st["vt_target"]
    try:
        # ---- v0.8.11（A）：parts_color 现场还原**排在最前** —— 材质槽没还回去就去删灯/相机，
        # 中途一步抛错就再也回不去了（渲染设置还原在下面，顺序同理：先还"改了内容的"，再还"设置"）。
        if pc_state:
            try:
                pc_restore = _parts_restore(pc_state, pc_mats)
            except Exception as e:
                pc_restore = {"ok": False, "mismatches": ["parts_color 还原抛错 %s: %s"
                                                          % (type(e).__name__, str(e)[:200])],
                              "temp_materials_left": [], "objects": len(pc_state)}
        # ---- 还原现场（临时对象删掉、设置回滚）
        try:
            for ob in rig:
                try:
                    _ld = ob.data if ob.type == "LIGHT" else None
                    bpy.data.objects.remove(ob, do_unlink=True)
                    if _ld is not None:
                        # v0.8.10（D1）：灯的 datablock 必须一起删 —— 旧版只删 object，一轮下来会攒出几十个孤儿灯
                        try:
                            bpy.data.lights.remove(_ld, do_unlink=True)
                        except Exception:
                            pass
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
            # ---- v0.9.1（A2）：色彩变换还原 + **逐项对拍**（改了没还回去 = 污染场景，比少出一张图严重）
            if vt_mode != "scene" and css is not None and vt_before is not None:
                vt_restore_err = None
                try:
                    css.view_transform = vt_before
                except Exception as e:
                    vt_restore_err = "%s: %s" % (type(e).__name__, str(e)[:160])
                vt_after_back = str(getattr(css, "view_transform", None))
                vt_restore = {"mode": vt_mode, "assigned": (str(vt_target) if vt_target is not None else None),
                              "scene_before": str(vt_before), "scene_after": vt_after_back,
                              "ok": (vt_after_back == str(vt_before) and vt_restore_err is None)}
                if vt_restore_err:
                    vt_restore["error"] = vt_restore_err
                if not vt_restore["ok"]:
                    scope_warnings.append("view_transform 还原**没通过核对**：before=%r after=%r%s"
                                          % (str(vt_before), vt_after_back,
                                             ("（%s）" % vt_restore_err) if vt_restore_err else ""))
            db_after = _db_counts()
            # ---- v0.9.1（E1）：放锁（异常路径也走到这里；holder 匹配才删，绝不误删别人的锁）
            if lock_res and lock_res.get("acquired"):
                try:
                    _rel = _lock_release(lock_path, lock_holder)
                    lock_res["release"] = _rel
                    lock_res["released"] = bool(_rel.get("released"))
                    if _rel.get("held_ms") is not None:
                        lock_res["held_ms"] = _rel.get("held_ms")
                    if not _rel.get("ok"):
                        scope_warnings.append("渲染锁释放失败：%s" % _rel.get("error"))
                    elif not _rel.get("released"):
                        scope_warnings.append("渲染锁释放：%s" % _rel.get("reason"))
                except Exception as e:
                    lock_res["released"] = False
                    scope_warnings.append("渲染锁释放抛错：%s: %s（TTL=%ss 到期后会被下一个进程打破）"
                                          % (type(e).__name__, str(e)[:160], LOCK_DEFAULT_TTL_S))
        except Exception as e2:
            if err is None:
                err = {"error": "还原现场失败: %s" % str(e2)[:200]}
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_finish_checks(st):
    budget_ms, db_after, db_before, err, lock_line = (
        st["budget_ms"], st["db_after"], st["db_before"], st["err"], st["lock_line"])
    lock_res, parts_enabled, parts_info, pc_restore, scope_warnings = (
        st["lock_res"], st["parts_enabled"], st["parts_info"], st["pc_restore"], st["scope_warnings"])
    stopped_early, t0, vt_restore = st["stopped_early"], st["t0"], st["vt_restore"]
    try:
        # ---- v0.8.10（D1）：临时资源泄漏自检
        db_delta = {}
        try:
            if db_before and db_after:
                for k, v0 in db_before.items():
                    v1 = db_after.get(k)
                    if isinstance(v0, int) and isinstance(v1, int) and v1 != v0:
                        db_delta[k] = v1 - v0
            if db_delta:
                scope_warnings.append("临时资源计数未回到基线：%s（灯/相机泄漏会拖慢后续渲染）" % db_delta)
        except Exception:
            pass
        # ---- v0.8.11（A）：parts_color 还原核对不过 = 场景被污染 → 不静默成功
        if parts_enabled and pc_restore is not None:
            if not pc_restore.get("ok"):
                scope_warnings.append("parts_color 现场还原**没通过核对**：%s"
                                      % (_j(pc_restore.get("mismatches"))[:400]))
                if err is None:
                    err = {"error": "parts_color 还原核对失败（场景材质槽可能与渲染前不同，务必先检查再继续出图）",
                           "restore": pc_restore}
            parts_info["restore"] = pc_restore
        # ---- v0.9.1（A2）：色彩变换没还原回去 = 场景被污染 → 同样不静默成功
        if vt_restore is not None and not vt_restore.get("ok"):
            if err is None:
                err = {"error": "view_transform 还原核对失败（场景色彩变换与渲染前不同，先检查再继续出图）",
                       "view_transform_restore": vt_restore}
        total_ms = int(round((time.perf_counter() - t0) * 1000.0))
        within = (not stopped_early) if budget_ms else True
        # v0.9.1（E1）：返回体里的 lock 带上"放锁结果"（jsonl 行是取锁当时写的，不含 released/held_ms）
        if lock_res is not None and lock_res.get("acquired"):
            lock_line = dict(lock_line, released=bool(lock_res.get("released")),
                             held_ms=lock_res.get("held_ms"), release=lock_res.get("release"))
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_finish_out(st):
    alias_applied, base_res, budget_ms, catalog_ortho, center = (
        st["alias_applied"], st["base_res"], st["budget_ms"], st["catalog_ortho"], st["center"])
    db_after, db_before, db_delta, deduped_views, dev_after = (
        st["db_after"], st["db_before"], st["db_delta"], st["deduped_views"], st["dev_after"])
    dev_batch, device_check, device_used, eng_mode, engine_check = (
        st["dev_batch"], st["device_check"], st["device_used"], st["eng_mode"], st["engine_check"])
    entries, err, fallback_used, jsonl, ladder = (
        st["entries"], st["err"], st["fallback_used"], st["jsonl"], st["ladder"])
    ladder_idx, lights_cfg, lights_mode, lock_line, lock_path = (
        st["ladder_idx"], st["lights_cfg"], st["lights_mode"], st["lock_line"], st["lock_path"])
    margin, mn, mode, mx, no_guess_views = st["margin"], st["mn"], st["mode"], st["mx"], st["no_guess_views"]
    nobj, opened, outdir, parts_info, per_view_budget_ms = (
        st["nobj"], st["opened"], st["outdir"], st["parts_info"], st["per_view_budget_ms"])
    requested_count, samples, scope_warnings, sheet_metrics, sheet_path = (
        st["requested_count"], st["samples"], st["scope_warnings"], st["sheet_metrics"], st["sheet_path"])
    size, skipped, skipped_views, spent_ms, stopped_early = (
        st["size"], st["skipped"], st["skipped_views"], st["spent_ms"], st["stopped_early"])
    targets_resolved, total_ms, unknown_views, view_order, views = (
        st["targets_resolved"], st["total_ms"], st["unknown_views"], st["view_order"], st["views"])
    views_from, vlist, vt_before, vt_mode, vt_raw = (
        st["views_from"], st["vlist"], st["vt_before"], st["vt_mode"], st["vt_raw"])
    vt_restore, vt_target, vt_used, warmup_info, within = (
        st["vt_restore"], st["vt_target"], st["vt_used"], st["warmup_info"], st["within"])
    try:
        out = {"ok": err is None and len(entries) > 0,
               "version": QC_RENDER_VERSION,
               "file": bpy.data.filepath, "opened_file": opened,
               "mode": mode,
               "outdir": outdir, "jsonl": jsonl, "jsonl_lines": len(entries),
               "views": entries, "count": len(entries),
               "total_ms": total_ms, "render_ms": spent_ms,
               "budget_ms": budget_ms, "within_budget": within, "stopped_early": stopped_early,
               "skipped_views": skipped_views,
               "engine": _engine_label(), "engine_mode": eng_mode, "samples": samples,
               # ---- v0.8.11（B）：本批实际的渲染设备（顶部汇总 = **渲完之后**的实测回读，最接近"实际用的"）
               "device": device_used,
               "device_before_render": dev_batch,
               "device_line": _device_line(dev_after if dev_after is not None else dev_batch),
               "device_check": device_check,
               # ---- v0.8.11（C）：视角目录/别名/未知名
               "view_order": view_order, "catalog_ortho": catalog_ortho,
               "views_requested": ([str(x) for x in views] if views_from == "request" else None),
               "views_from": views_from, "render_order": [str(v.get("name")) for v in vlist],
               "unknown_views": unknown_views, "view_aliases_applied": alias_applied,
               "deduped_views": deduped_views, "default_views_fallback": (list(DEFAULT_VIEWS_FALLBACK) if fallback_used else None),
               # ---- v0.9.1（A1）：三个计数（"12 个名字只渲 8 张"这类必须一眼看出来）
               "requested_count": requested_count, "rendered_count": len(entries),
               "unknown_count": len(unknown_views), "no_guess_views": no_guess_views,
               "available_views": list(VIEW_ORDER),
               # ---- v0.9.1（A2）：色彩变换（实际生效值 + 请求值 + 三态 + 还原核对）
               "view_transform": vt_used, "view_transform_mode": vt_mode,
               "view_transform_requested": (str(vt_raw) if vt_raw is not None else None),
               "view_transform_assigned": (str(vt_target) if vt_target is not None else None),
               "view_transform_scene": (str(vt_before) if vt_before is not None else None),
               "view_transform_restore": vt_restore,
               # ---- v0.9.1（E1）：渲染锁（holder/waited_ms/acquired/stale_broken/path —— 计时口径可审计）
               "lock": lock_line, "lock_dir": os.path.dirname(os.path.dirname(lock_path)),
               "view_group_of": dict((str(v.get("name")), VIEW_GROUP_OF.get(str(v.get("name")))) for v in vlist
                                     if str(v.get("name")) in VIEW_GROUP_OF),
               "parts_color": parts_info,
               "res": base_res, "objects": nobj, "aabb": {"min": [round(float(x), 4) for x in mn],
                                                          "max": [round(float(x), 4) for x in mx],
                                                          "size": [round(float(x), 4) for x in size],
                                                          "center": [round(float(x), 4) for x in center]},
               "skipped_objects": skipped, "lights": {"mode": lights_mode, "config": lights_cfg,
                                                      "angles_deg": LIGHT_ANGLES, "relative_to": "camera azimuth"},
               "margin": margin,
               "warmup": warmup_info, "per_view_budget_ms": per_view_budget_ms,
               "ladder": ladder, "ladder_final_step": (ladder_idx if ladder else None),
               "engine_check": engine_check,
               "scope_warnings": scope_warnings, "targets_resolved": targets_resolved,
               "datablocks": {"before": db_before, "after": db_after, "delta": db_delta},
               "sheet": sheet_path, "sheet_tiles": sheet_metrics,
               "note": "每行 jsonl = 一张图；ms 是单张渲染耗时（首张含 EEVEE 着色器编译）；"
                       "device 是**实测**的渲染设备（null = 拿不到判据，见 device_source，绝不许猜）；"
                       "within_budget=false 表示累计超预算已停（已出的图保留）"}
        if err is not None:
            out["error"] = err
        return _j(out)
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_loop(st):
    for idx, v in enumerate(st["vlist"]):
        if st["budget_ms"] and st["spent_ms"] >= st["budget_ms"]:
            st["stopped_early"] = True
            st["skipped_views"] = [str(x.get("name")) for x in st["vlist"][idx:]]
            break
        st["idx"] = idx
        st["v"] = v
        _qv_capture_a(st)
        _qv_capture_b(st)
        _qv_emit(st)
        if st["budget_ms"] and st["spent_ms"] > st["budget_ms"]:
            st["stopped_early"] = True
            st["skipped_views"] = [str(x.get("name")) for x in st["vlist"][idx + 1:]]
            break
