# -*- coding: utf-8 -*-
"""Split from qc_render.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .lock import _lock_acquire, _lock_holder_default, _lock_line, _lock_path, _lock_release
from .geom import _aabb, _apply_step, _collect_objs, _corners, _db_counts, _focus_box, _md5, _montage_api, _project_px, _safe, _solve_view
from .rig import _add_rig, _aim_rig, _alpha_stats, _apply_samples, _device_info, _device_line, _device_live, _device_slim, _engine_label, _restore_engine_samples, _set_engine
from .parts import PARTS_PALETTE, _legend_line_count, _parts_assign, _parts_groups, _parts_legend_rows, _parts_override, _parts_restore, _write_parts_legend

def qc_render_views(args=None):
    t0 = time.perf_counter()
    a = _unpack_args(args)          # JSON 字符串 / {"args": {...}} 包一层 / None —— 口径与 dispatch 一致

    # ---- v0.8.10（B1）：参数作用域强校验 —— "只认顶层的键"若出现在子字典里，必须报出来（本项目 234 张假证据的根因）
    TOP_KEYS = set(["file", "views", "res", "samples", "budget_s", "thr", "outdir", "jsonl", "ref_path", "ref_box",
                    "ref_search", "lights", "lights_mode", "margin", "engine", "view_transform", "tag", "targets",
                    "allow_open_file", "film_transparent", "lens", "sensor_width", "warmup", "ladder",
                    "per_view_budget_s", "aabb", "focus", "sheet", "sheet_cols", "sheet_tile", "chunk",
                    "sheet_grid", "sheet_scale_m", "args",
                    # v0.8.11（Procedura 融合：parts_color / 视角目录 / 设备）
                    "mode", "parts_color", "parts_group_by", "parts_flat", "parts_palette", "parts_legend",
                    "catalog_ortho", "view_order", "strict_views",
                    # v0.9.1（93 反馈 E1：渲染锁）
                    "lock", "lock_wait_s", "lock_dir", "lock_holder"])
    VIEW_KEYS = set(["name", "az", "el", "from", "loc", "location", "look_at", "target", "to", "lens", "sensor",
                     "sensor_fit", "ortho", "ortho_scale", "res", "margin", "path", "ref_path", "ref_box",
                     "aabb", "focus", "samples"])
    scope_warnings = []
    for k in sorted(a.keys()):
        if k not in TOP_KEYS:
            scope_warnings.append("未知顶层参数 %s（已忽略；可用参数见 qc_render_help）" % k)
    _views_raw = a.get("views")
    if isinstance(_views_raw, list):
        for _i, _v in enumerate(_views_raw):
            if not isinstance(_v, dict):
                continue
            for _k in _v.keys():
                if _k in TOP_KEYS and _k not in VIEW_KEYS:
                    scope_warnings.append("views[%d] 里的 %s **只在顶层生效，已被忽略**；实际生效值 = %r（把它挪到 args 顶层）"
                                          % (_i, _k, a.get(_k)))
                elif _k not in VIEW_KEYS:
                    scope_warnings.append("views[%d] 未知键 %s（已忽略）" % (_i, _k))

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
    # v0.8.11 顺序修正：原来 `_set_engine`/`_apply_samples` 在"取景失败/没有可见 mesh"的早退之前，
    # 于是早退路径会把引擎改掉却不还原（pre-existing 现场泄漏）。取景与视角归一都不依赖引擎，
    # 所以把它们挪到所有早退之后 —— 一旦动过状态，就一定在下面的 try/finally 里还回去。
    sc = bpy.context.scene
    eng_mode = str(a.get("engine") or "keep")
    samples = int(a.get("samples") or 64)

    # ---- 目标与 AABB（+ v0.8.10 B1：把"实际生效的筛选集"回显出来）
    _t_req = a.get("targets")
    objs, skipped = _collect_objs(_t_req)
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    mn, mx, nobj = _aabb(objs)
    if nobj == 0 or mn is None:
        return _j({"ok": False, "error": "取景失败：没有任何可见 mesh（可传 targets=[名字] 指定）",
                   "file": bpy.data.filepath, "skipped": skipped})
    targets_resolved = {"requested": ([str(x) for x in _t_req] if isinstance(_t_req, (list, tuple)) else None),
                        "resolved": [ob.name for ob in objs], "count": len(objs),
                        "missing": list(skipped.get("missing") or []), "skipped_hidden": int(skipped.get("hidden") or 0)}
    if targets_resolved["missing"]:
        scope_warnings.append("targets 里这些名字不存在（已忽略）：%s" % ", ".join(targets_resolved["missing"]))
    if targets_resolved["requested"] and len(objs) < len(targets_resolved["requested"]):
        scope_warnings.append("targets 请求 %d 个、实际参与取景 %d 个（差集见 targets_resolved.missing）"
                              % (len(targets_resolved["requested"]), len(objs)))
    center = (mn + mx) * 0.5
    size = mx - mn
    corners = _corners(mn, mx)

    # ---- 视角归一化（v0.8.11：走目录 + 别名；未知名原样回传，绝不静默替换）
    # v0.9.1（A1）：**认不出来就失败** —— 非空 views 且一个都没解析成功 → ok:false 且一张不渲；
    # 只有"省略/空"才走默认（默认 = 兜底 4 视角；v0.8.11 及以前是 3 视角，见 DEFAULT_VIEWS_LEGACY）。
    views = a.get("views")
    views_from = "default_legacy"
    if not views:                                   # None / "" / [] → v0.8.11 的老默认（3 张；补丁版不改默认产物集）
        views = list(DEFAULT_VIEWS_LEGACY)
    else:
        views_from = "request"
    if isinstance(views, str):
        views = [v.strip() for v in views.split(",") if v.strip()]
    requested_count = len([v for v in views
                           if (isinstance(v, str) and v.strip()) or isinstance(v, dict)])
    base_res = a.get("res") or DEFAULT_RES
    if isinstance(base_res, (int, float)):
        base_res = [int(base_res), int(base_res)]
    base_res = [int(base_res[0]), int(base_res[1])]
    view_order = str(a.get("view_order") or "catalog").lower()
    strict_views = bool(a.get("strict_views"))
    catalog_ortho = bool(a.get("catalog_ortho"))
    named_req = [v for v in views if isinstance(v, str) and not _is_azel_spec(v)]
    rv = resolve_view_names(named_req)
    canon_order, unknown_views, alias_applied, deduped_views = rv["names"], rv["unknown"], rv["aliases"], rv["deduped"]
    no_guess_views = rv.get("no_guess") or []
    explicit_specs, spec_err = [], None
    # 显式视角（dict / az=el 字符串）保持请求顺序，且**不受**目录排序与去重影响（老参数原样可用的关键）
    for i, v in enumerate(views):
        if isinstance(v, dict):
            explicit_specs.append(dict(v))
        elif isinstance(v, str) and _is_azel_spec(v):
            s = {"name": v, "_source": "azel"}
            try:
                kv = {}
                for part in v.strip().lower().replace(" ", "").split(","):
                    if "=" in part:
                        kk, vv = part.split("=", 1)
                        kv[kk] = vv
                if "az" in kv:
                    s["az"] = float(kv["az"])
                if "el" in kv:
                    s["el"] = float(kv["el"])
            except Exception as e:
                spec_err = "views[%d] 的 az/el 写法解析失败：%r（%s）" % (i, v, str(e)[:100])
                break
            s.setdefault("az", NAMED_VIEWS["iso"][0])
            s.setdefault("el", NAMED_VIEWS["iso"][1])
            explicit_specs.append(s)
        elif not isinstance(v, str):
            return _j({"ok": False, "error": "views[%d] 类型不支持：%r" % (i, v)})
    if spec_err:
        return _j({"ok": False, "error": spec_err, "views": [str(x) for x in views]})
    fallback_used = (views_from in ("default_legacy", "default_fallback"))
    if views_from == "default_legacy":
        scope_warnings.append("views 省略/空 → 默认 %s（与 v0.8.11 一致；要 Procedura 的 4 视角集就显式传 %s）"
                              % (",".join(DEFAULT_VIEWS_LEGACY), ",".join(DEFAULT_VIEWS_FALLBACK)))
    if not canon_order and not explicit_specs:
        # v0.9.1（A1）**硬失败**：views 非空、一个都没解析成功 —— 这里以前会静默回落 4 视角（93 反馈：
        # 产物文件名对、角度错、ok:true，调用方看不出来）。现在**一张都不渲**，把可选名清单直接给回去。
        _requested_raw = [str(x) for x in views]
        scope_warnings.append("views 里 %d 个名字一个都认不出来 → 一张都没渲（未知名绝不猜、也不兜底替换）"
                              % requested_count)
        return _j({"ok": False,
                   "version": QC_RENDER_VERSION,
                   "error": "views 里没有一个认得出来的视角名：%s（已按 A1 纪律硬失败：一张都不渲，"
                            "不再静默替换成 iso/front/right/top）" % ", ".join(_requested_raw),
                   "unknown_views": unknown_views, "unknown_count": len(unknown_views),
                   "no_guess_views": no_guess_views,
                   "requested_count": requested_count, "rendered_count": 0, "count": 0,
                   "views_requested": _requested_raw, "views_from": views_from,
                   "available_views": list(VIEW_ORDER),
                   "view_menu": view_menu_full_text(),
                   "view_aliases": dict(VIEW_ALIASES),
                   "hint": "未知名不会猜；要么用目录里的名（available_views / view_menu），要么用别名；"
                           "枪械语境请自己映射 muzzle_end→front 之类（*_end 一律不猜，由调用方按本项目目录显式映射）",
                   "file": bpy.data.filepath, "outdir": outdir,
                   "scope_warnings": scope_warnings,
                   "note": "这是**空产出**：rendered_count=0、没有任何 PNG（含兜底图）；"
                           "拿到 unknown_views 后改名字重跑即可"})
    if unknown_views:
        scope_warnings.append("未知视角名 %s 已跳过（%d/%d 张可渲；原样返回在 unknown_views；具名表见 "
                              "qc_render_help.view_menu）"
                              % (", ".join(unknown_views), len(canon_order) + len(explicit_specs), requested_count))
        if no_guess_views:
            scope_warnings.append("其中 %s 命中**不猜**纪律（%s）" % (", ".join(no_guess_views), VIEW_NO_GUESS_NOTE))
    if deduped_views:
        scope_warnings.append("重复视角 %s 已去重（同一规范名只渲一次；要渲两次请用 dict 视角显式指定 path）"
                              % ", ".join(deduped_views))
    if strict_views and unknown_views:
        return _j({"ok": False, "version": QC_RENDER_VERSION,
                   "error": "views 里有认不出来的名字，strict_views=true 时不许带病交付",
                   "unknown_views": unknown_views, "unknown_count": len(unknown_views),
                   "no_guess_views": no_guess_views,
                   "requested_count": requested_count, "rendered_count": 0, "count": 0,
                   "known_views": canon_order,
                   "available_views": list(VIEW_ORDER), "view_menu": view_menu_full_text(),
                   "hint": "去掉未知名或改成目录里的名（available_views）；strict_views=false 时会渲已知名并把未知名报在 unknown_views",
                   "file": bpy.data.filepath, "outdir": outdir, "views_from": views_from,
                   "scope_warnings": scope_warnings})
    named_specs = {}
    req_spelling = {}
    for raw in named_req:                       # 别名/大小写容错时记下用户原始拼写（结果里可追溯）
        c = _canon_view(raw)
        if c and _norm_view_name(raw) != _norm_view_name(c):
            req_spelling.setdefault(c, str(raw))
    for nm in canon_order:
        s = {"name": nm, "az": NAMED_VIEWS[nm][0], "el": NAMED_VIEWS[nm][1], "_source": "catalog"}
        if nm in req_spelling:
            s["_requested"] = req_spelling[nm]
        if catalog_ortho:
            s["ortho"] = VIEW_ORTHO[nm]
            s["_ortho_src"] = "catalog"
        named_specs[nm] = s
    if view_order == "request":
        # 老口径逃生门：完全按请求顺序（字符串按首次出现位置插回，dict/az=el 保持原位）
        vlist, _emitted = [], set()
        for v in views:
            if isinstance(v, dict) or (isinstance(v, str) and _is_azel_spec(v)):
                vlist.append(explicit_specs.pop(0))
            else:
                nm = _canon_view(v)
                if nm and nm in named_specs and nm not in _emitted:
                    _emitted.add(nm)
                    vlist.append(named_specs[nm])
    else:
        vlist = [named_specs[n] for n in canon_order] + explicit_specs
    for i, s in enumerate(vlist):
        if str(s.get("_source")) == "azel":
            s["_requested"] = str(s.get("name"))     # az=el 原样留痕（名字就是原串）
        s.setdefault("name", "v%d" % (i + 1))
        s.setdefault("res", base_res)

    # ---- v0.8.11（A）：逐部件配色（parts_color）—— 覆盖材质槽之前先把"要覆盖谁、覆盖成什么"定下来
    mode = str(a.get("mode") or ("parts_color" if a.get("parts_color") else "views")).lower()
    if mode in ("parts", "parts_color", "parts_color_views", "parts-color"):
        mode = "parts_color"
    parts_enabled = (mode == "parts_color")
    parts_group_by = str(a.get("parts_group_by") or "object").lower()
    if parts_group_by not in ("object", "collection"):
        scope_warnings.append("parts_group_by=%r 不认（只有 object/collection）→ 按 object 走" % parts_group_by)
        parts_group_by = "object"
    parts_flat = bool(a.get("parts_flat"))
    parts_palette = PARTS_PALETTE
    if a.get("parts_palette"):
        try:
            pp = [tuple(int(x) for x in c[:3]) for c in a["parts_palette"]]
            pp = [c for c in pp if len(c) == 3]
            if pp:
                parts_palette = pp
            else:
                scope_warnings.append("parts_palette 里没有合法的 [R,G,B] → 用默认 12 色")
        except Exception as e:
            scope_warnings.append("parts_palette 解析失败（%s）→ 用默认 12 色" % str(e)[:100])
    parts_groups, parts_assign, parts_rows, parts_info = [], {}, [], None
    if parts_enabled:
        pc_objs, pc_skipped = _collect_objs(None)        # 参与渲染的**全部可见 mesh**（与 targets 取景无关）
        if not pc_objs:
            return _j({"ok": False, "error": "parts_color：场景里没有可见 mesh，没东西可配色",
                       "skipped": pc_skipped})
        parts_groups = _parts_groups(pc_objs, parts_group_by)
        parts_assign = _parts_assign(parts_groups, parts_palette)
        parts_rows = _parts_legend_rows(parts_groups, parts_assign)
        _reused = [r["name"] for r in parts_rows if r.get("color_source") == "hash"]
        if len(parts_rows) > len(parts_palette):
            scope_warnings.append("部件数 %d > 调色板 %d 色：%d 个部件按名字哈希复用颜色"
                                  "（图例里 color_source=hash；同名同色跨次稳定，不报错）"
                                  % (len(parts_rows), len(parts_palette), len(_reused)))
        if a.get("ref_path"):
            scope_warnings.append("parts_color 模式下 ref 比对意义有限（材质被覆盖成色块，IoU/剖面差不再是几何判据）")
        parts_info = {"enabled": True, "mode": mode, "group_by": parts_group_by, "flat": parts_flat,
                      "parts": len(parts_rows), "palette_size": len(parts_palette),
                      "palette": [[int(c[0]), int(c[1]), int(c[2])] for c in parts_palette],
                      "palette_space": "sRGB 0-255（图例色）；渲染时按 srgb→linear 写入材质，"
                                       "Principled 还叠灯光/色彩变换，故图上颜色不会与图例逐像素相等"
                                       "（要逐像素对上用 parts_flat=true + view_transform=\"Standard\"）",
                      "assignments": dict((k, v) for k, v in parts_assign.items()),
                      "color_reused_by_hash": _reused,
                      "scope": "全部可见 mesh（%d 个对象 / %d 个部件）；与 targets 取景相互独立"
                               % (len(pc_objs), len(parts_rows)),
                      "skipped": pc_skipped,
                      "touched": "只改对象材质槽（+ 链接副本临时复制 mesh）；可见性/世界/灯光/相机不动"}
    else:
        parts_info = {"enabled": False, "mode": mode,
                      "hint": "要逐部件配色渲图：传 mode=\"parts_color\"（或 parts_color=true）"}
    if a.get("parts_legend"):
        legend_txt = _win(a["parts_legend"])
    else:
        legend_txt = _win(os.path.join(outdir, "parts_color_meta.txt"))
    eng_before, eng_after = _set_engine(eng_mode)
    samp_before = _apply_samples(samples)
    # v0.8.11（B）：设备**必须在引擎切完之后**测 —— 批前测会把"上一批/上一次的引擎"记进这一批
    # （踩过：engine="cycles" 的批次里 device 报的是 EEVEE 的 GPU 语义，等于把判据挂错了对象）
    dev_batch = _device_info()

    # ---- v0.8.10（C7 分片）：chunk=[i,n] → 只渲第 i 份（1-based），大活可切多份并行/续跑
    _chunk = a.get("chunk")
    if isinstance(_chunk, (list, tuple)) and len(_chunk) == 2:
        try:
            _ci, _cn = int(_chunk[0]), max(1, int(_chunk[1]))
            _sel = [v for _idx, v in enumerate(vlist) if (_idx % _cn) == (_ci - 1)]
            if _sel:
                vlist = _sel
                scope_warnings.append("分片模式：chunk=[%d,%d] → 本次只渲 %d/%d 张" % (_ci, _cn, len(vlist), _cn))
        except Exception as e:
            scope_warnings.append("chunk 参数不认（应为 [i,n]）：%s" % str(e)[:80])

    budget_s = a.get("budget_s", a.get("thr"))
    try:
        budget_ms = int(round(float(budget_s) * 1000.0)) if budget_s not in (None, "", 0, "0") else 0
        # v0.8.11：正数但 < 0.5ms 的预算会被 round 成 0，而 0 在下游等于「不设预算」—— 静默忽略预算
        # 与"超预算立刻停"的约定正好相反；这里抬到 1ms 并说清楚（None/""/0/"0" 仍照旧 = 不设预算）。
        if budget_ms == 0 and budget_s not in (None, "", 0, "0"):
            try:
                if float(budget_s) > 0:
                    budget_ms = 1
                    scope_warnings.append("budget_s=%r 太小（取整后 <1ms），按 1ms 处理（不退化成「不设预算」）"
                                          % (budget_s,))
            except Exception:
                pass
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
    # ---- v0.9.1（A2）：色彩变换三态（**缺省 = "Standard"**）
    # 93 反馈原话：EEVEE 无头渲染下 AgX 把纯白底压到 ≈0.85，凡是"按亮度阈值裁参考/算剪影"的分析整轮失效。
    # 三态：
    #   省略/空/"Standard" 等具体名 → 用那个（缺省 = Standard：QC 口径）
    #   "scene"                    → 沿用场景设置（v0.8.11 的旧行为，beauty 图用）；场景非 Standard 时给 warning
    vt_raw = a.get("view_transform")
    vt_arg = None if vt_raw is None else str(vt_raw).strip()
    if not vt_arg:
        vt_mode, vt_target = "default_standard", "Standard"
    elif vt_arg.lower() == "scene":
        vt_mode, vt_target = "scene", None
    else:
        vt_mode, vt_target = "explicit", vt_arg
    view_transform = vt_target                 # 老变量名沿用：值 = 要写进 css 的名字（scene 时为 None）
    if parts_enabled and parts_flat and vt_mode == "default_standard":
        # v0.8.11 的老键保留（parts_color+flat 的色彩变换也是 Standard，现在是缺省口径的顺带结果）
        parts_info["view_transform_defaulted"] = "Standard"
        scope_warnings.append("parts_color+parts_flat：未指定 view_transform，已默认 Standard"
                              "（AgX/Filmic 会把图例色洗淡；出图后还原原设置）")
    if vt_mode == "scene":
        scope_warnings.append("view_transform=\"scene\"：沿用场景设置（旧行为）；若场景不是 Standard，"
                              "阈值判读需自行校正")

    # ---- 现场保存
    css = getattr(sc, "view_settings", None)
    vt_before = getattr(css, "view_transform", None) if css is not None else None
    if vt_mode == "scene" and vt_before is not None and str(vt_before) != "Standard":
        scope_warnings.append("当前场景色彩变换是 %s（非 Standard）：本次出图沿用场景设置，"
                              "按亮度阈值裁参考/算剪影的分析需自行校正（要 QC 口径传 view_transform=\"Standard\"）"
                              % str(vt_before))
    vt_used = str(vt_target) if vt_target is not None else (str(vt_before) if vt_before is not None else None)
    rs = sc.render
    saved = {
        "camera": sc.camera, "engine": eng_before,
        "res_x": int(rs.resolution_x), "res_y": int(rs.resolution_y), "res_pct": int(rs.resolution_percentage),
        "filepath": str(rs.filepath), "use_ext": bool(rs.use_file_extension),
        "fmt": str(rs.image_settings.file_format), "color_mode": str(rs.image_settings.color_mode),
        "film_transparent": bool(rs.film_transparent),
    }
    db_before = _db_counts()          # v0.8.10（D1）：临时资源泄漏自检的基线
    db_after = None
    hidden_lights = []
    made_cam = None
    rig = []
    entries = []
    skipped_views = []
    stopped_early = False
    spent_ms = 0
    err = None
    # v0.8.10：即使中途异常，结果字典也必须有这些字段（否则 except 之后会 NameError）
    warmup_info = None
    ladder = None
    ladder_idx = 0
    per_view_budget_ms = 0
    engine_check = None
    sheet_path = None
    sheet_metrics = None
    # v0.8.11（A/B）：parts_color 现场状态 + 设备复核 + 图例产物
    pc_state, pc_mats, pc_restore = [], {}, None
    # v0.9.1（A2）：色彩变换还原核对结果（finally 里填；异常路径也要有值）
    vt_restore = None
    pc_legend_json = None
    pc_err = None
    dev_after = None
    device_check = None
    device_used = dev_batch      # 顶部汇总：渲染完成后换成"渲完之后"的实测回读
    # ---- v0.9.1（E1）：渲染锁 —— 渲染前自动 acquire（放在所有"不渲染的早退"之后，避免空占锁；
    # 也放在这个 try 之前，这样下面的 finally 一定能 release）
    lock_on = a.get("lock", True)
    if isinstance(lock_on, str):
        lock_on = lock_on.strip().lower() not in ("0", "false", "off", "no", "")
    lock_enabled = bool(lock_on)
    lock_holder = str(a.get("lock_holder") or _lock_holder_default())
    lock_base = a.get("lock_dir")
    lock_path = _lock_path(lock_base)
    lock_res, lock_line = None, _lock_line(None)
    if lock_enabled:
        try:
            lock_wait_s = float(a.get("lock_wait_s") if a.get("lock_wait_s") is not None else 120.0)
        except Exception as e:
            scope_warnings.append("lock_wait_s=%r 不是数字（%s）→ 按 0 处理" % (a.get("lock_wait_s"), str(e)[:80]))
            lock_wait_s = 0.0
        if lock_wait_s < 0:
            scope_warnings.append("lock_wait_s=%r < 0 → 按 0 处理" % (a.get("lock_wait_s"),))
            lock_wait_s = 0.0
        lock_res = _lock_acquire(lock_path, lock_holder, LOCK_DEFAULT_TTL_S, lock_wait_s,
                                 note="qc_render_views tag=%s views=%s" % (tag, ",".join([str(v.get("name"))
                                                                                          for v in vlist[:8]])))
        lock_line = _lock_line(lock_res)
        if lock_res.get("stale_broken"):
            scope_warnings.append(lock_res.get("stale_note") or "打破了过期锁")
        if lock_res.get("parse_error"):
            scope_warnings.append("锁文件解析失败（已按 mtime 判年龄）：%s" % lock_res["parse_error"])
        if not lock_res.get("ok"):
            # 等不到锁 → **不渲**（渲染了也是虚高的计时，等于把不可比的数写进证据链）。
            # v0.9.1：engine/samples 在这之前已经改过（它们在 try 之外），早退不走 finally →
            # 这里必须自己写回并核对，否则"锁失败"会顺手污染调用方的场景（自检 E2 快照抓到过）。
            _pre = _restore_engine_samples(sc, eng_before, samp_before)
            if not _pre.get("ok"):
                scope_warnings.append("锁超时早退时引擎/采样回滚**没通过核对**：%s" % _j(_pre))
            return _j({"ok": False, "version": QC_RENDER_VERSION,
                       "error": lock_res.get("error"), "hint": lock_res.get("hint"),
                       "lock": lock_line, "lock_dir": os.path.dirname(os.path.dirname(lock_path)),
                       "current_holder": lock_res.get("current_holder"),
                       "holder_age_s": lock_res.get("holder_age_s"),
                       "holder_ttl_remaining_s": lock_res.get("holder_ttl_remaining_s"),
                       "prelock_restore": _pre,
                       "rendered_count": 0, "count": 0, "requested_count": requested_count,
                       "file": bpy.data.filepath, "outdir": outdir, "tag": tag,
                       "views_requested": [str(v.get("name")) for v in vlist],
                       "scope_warnings": scope_warnings,
                       "note": "一张都没渲：拿不到渲染锁就不出图（lock=false 可显式跳过锁，自行承担计时口径）；"
                               "已把引擎/采样写回原值（prelock_restore 逐项核对）"})
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
            if budget_ms and spent_ms > budget_ms:
                stopped_early = True
                skipped_views = [str(x.get("name")) for x in vlist[idx + 1:]]
                break

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
    except BaseException as e:
        import traceback
        err = {"error": "%s: %s" % (type(e).__name__, str(e)[:300]), "traceback": traceback.format_exc()[-2000:]}
    finally:
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
