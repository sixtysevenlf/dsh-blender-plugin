# -*- coding: utf-8 -*-
"""qc_render_views 的前半段：参数/目标/视角/部件/渲染设置/渲染锁（不含渲染循环）。

状态经 st(dict) 传递：helper 进入时从 st 解包、退出时把本地变量写回 st（成功/异常都写）。
因此异常路径下 finally 仍能拿到"已经改到一半的现场"，与拆分前语义一致。
"""
from .common import *  # noqa: F401,F403
from .lock import _lock_acquire, _lock_holder_default, _lock_line, _lock_path
from .geom import _aabb, _collect_objs, _corners, _db_counts, _safe
from .rig import _apply_samples, _device_info, _restore_engine_samples, _set_engine
from .parts import PARTS_PALETTE, _parts_assign, _parts_groups, _parts_legend_rows


def _qv_prepare(st):
    a = st["a"]
    try:
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_open_file(st):
    a = st["a"]
    try:
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
                               "hint": "长活请走 blender_run(kind='headless', file=..., preload=\"qc,qc_render\", as_job=true)；"
                                       "确实要在 GUI 里换文件就传 allow_open_file=true"})
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_aim(st):
    a, scope_warnings = st["a"], st["scope_warnings"]
    try:
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_views(st):
    a = st["a"]
    try:
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_vlist(st):
    base_res, canon_order, catalog_ortho, deduped_views, explicit_specs = (
        st["base_res"], st["canon_order"], st["catalog_ortho"], st["deduped_views"], st["explicit_specs"])
    named_req, no_guess_views, outdir, requested_count, scope_warnings = (
        st["named_req"], st["no_guess_views"], st["outdir"], st["requested_count"], st["scope_warnings"])
    strict_views, unknown_views, view_order, views, views_from = (
        st["strict_views"], st["unknown_views"], st["view_order"], st["views"], st["views_from"])
    try:
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_parts(st):
    a, outdir, scope_warnings = st["a"], st["outdir"], st["scope_warnings"]
    pc_objs, pc_skipped = (st.get("pc_objs"), st.get("pc_skipped"))
    try:
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_setup(st):
    a, eng_mode, parts_enabled, parts_flat, parts_info = (
        st["a"], st["eng_mode"], st["parts_enabled"], st["parts_flat"], st["parts_info"])
    samples, sc, scope_warnings, vlist = st["samples"], st["sc"], st["scope_warnings"], st["vlist"]
    try:
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _qv_lock(st):
    a, eng_before, outdir, requested_count, samp_before = (
        st["a"], st["eng_before"], st["outdir"], st["requested_count"], st["samp_before"])
    sc, scope_warnings, tag, vlist = st["sc"], st["scope_warnings"], st["tag"], st["vlist"]
    try:
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")
