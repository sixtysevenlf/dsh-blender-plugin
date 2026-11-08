# -*- coding: utf-8 -*-
"""Split from qc_render.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .lock import _lock_path, render_lock
from .geom import _collect_objs, _corners, _db_counts, _project_px, _solve_view
from .parts import _legend_line_count
from .main import qc_render_views

def _selftest_projection():
    """老自检内容（v0.8.8 原样保留）：解算出的相机用 Blender 自己的 world_to_camera_view 复核。
    给出每个具名视角的：解算距离 / 8 角投影后的像素 bbox / 是否全部在画幅内 /
    自算矩阵与 Blender 真值逐点像素差（proj_err_px）。"""
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
    return {"res": [w, h], "margin": margin, "views": rows, "proj_sources": srcs,
            "proj_err_px_max": (round(max(errs), 6) if errs else None),
            "all_inside": all(r["inside_frame"] for r in rows),
            "expect": "all_inside=true；fill ≈ %.3f（=1/margin）；proj_err_px ≈ 0（自算矩阵 vs Blender 真值）"
                      % (1.0 / margin)}


# ---------------------------------------------------------------- 场景快照（自检用）

def _scene_snapshot():
    """场景快照：对象数 + 逐对象材质槽(link/材质名)/可见性/mesh 与用户数 + 渲染设置 + datablock 计数。

    为什么逐对象记材质槽：parts_color 是**改材质槽**实现的，还原漏一个对象就等于污染了用户的成品文件；
    link 也要记（DATA/OBJECT 槽的写入口不同），mesh 名与 users 一起记（链接副本会临时换 mesh）。
    """
    sc = bpy.context.scene
    rs = sc.render
    objs = {}
    for ob in sc.objects:
        try:
            slots = [[str(s.link), (str(s.material.name) if s.material else None)] for s in ob.material_slots]
        except Exception:
            slots = None
        objs[str(ob.name)] = {"type": str(ob.type), "slots": slots,
                              "hide_render": bool(ob.hide_render),
                              "mesh": (str(ob.data.name) if ob.data is not None else None),
                              "mesh_users": (int(getattr(ob.data, "users", 0) or 0) if str(ob.type) == "MESH" else None)}
    ee = getattr(sc, "eevee", None)
    counts = _db_counts()
    try:
        # 渲染器自己的常驻图像（"Render Result" / "Viewer Node"）在**首次渲染时**才会出现，不是现场改动 ——
        # 快照里剔掉它们，否则 -b 新进程里自检会把自己第一次渲染误判成"没还原"（实测首渲 +2 个 image）。
        counts["images"] = len([i for i in bpy.data.images
                                if str(i.name) not in ("Render Result", "Viewer Node")])
        counts["images_note"] = "已剔除渲染器常驻的 Render Result / Viewer Node（不是现场改动）"
    except Exception:
        pass
    return {"objects": objs, "n_objects": len(sc.objects),
            "render": {"engine": str(rs.engine), "res": [int(rs.resolution_x), int(rs.resolution_y)],
                       "pct": int(rs.resolution_percentage), "film_transparent": bool(rs.film_transparent),
                       "filepath": str(rs.filepath), "format": str(rs.image_settings.file_format),
                       "color_mode": str(rs.image_settings.color_mode), "frame": int(sc.frame_current)},
            "camera": (str(sc.camera.name) if sc.camera else None),
            "view_transform": str(getattr(getattr(sc, "view_settings", None), "view_transform", None)),
            "samples": {"eevee_taa": (int(getattr(ee, "taa_render_samples", -1)) if ee is not None else None),
                        "cycles": (int(sc.cycles.samples) if hasattr(sc, "cycles") else None)},
            "counts": counts}


def _snap_diff(a, b, path=""):
    """逐项比较两个快照 → 差异字符串列表（空 = 逐项相等）。自检用它断言"渲染后场景 = 渲染前场景"。"""
    diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(list(a.keys()) + list(b.keys()))):
            p = ("%s.%s" % (path, k)) if path else str(k)
            if k not in a:
                diffs.append("%s：渲染前没有、渲染后多出 %r" % (p, b[k]))
            elif k not in b:
                diffs.append("%s：渲染前有、渲染后丢了（前 %r）" % (p, a[k]))
            else:
                diffs.extend(_snap_diff(a[k], b[k], p))
        return diffs
    if a != b:
        diffs.append("%s：%r != %r" % (path or "(root)", a, b))
    return diffs


def _png_palette_hits(path, rgbs, tol=16):
    """读 PNG，数每个图例色在图里出现的像素数（±tol/255 逐通道）。

    为什么这么判：颜色编码图的价值全在"图例对不对得上图" —— flat(Emission)+Standard 下
    部件实心区的像素就是图例色，对不上说明材质/色彩变换/图例三者有一个错了。
    """
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    px = np.clip(buf.reshape(h, w, 4)[:, :, :3], 0.0, 1.0) * 255.0
    hits = []
    for rgb in rgbs:
        d = np.abs(px - np.array([float(rgb[0]), float(rgb[1]), float(rgb[2])], dtype=np.float32))
        hits.append(int(np.count_nonzero(np.all(d <= float(tol), axis=2))))
    return hits


def _st_box_mesh(name, size=1.0, loc=(0.0, 0.0, 0.0)):
    """自检用的方块 mesh（from_pydata 手搓：不依赖 bpy.ops 的上下文，无头/GUI 都能跑）。"""
    s = float(size) * 0.5
    verts = [(-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s),
             (-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata([(v[0] + loc[0], v[1] + loc[1], v[2] + loc[2]) for v in verts], [], faces)
    me.update()
    return me

def _read_jsonl(p):
    rows = []
    if p and os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    rows.append(json.loads(ln))
    return rows

def _st_build_parts(sc):
    made_objs, made_meshes, made_mats = [], [], []
    # ---- 临时部件：4 个（含 1 对**共享 mesh 的链接副本** + 1 个 0 槽对象）——
    # 沿 X 一字排开（俯视时一个都不缺），两条都踩在 parts_color 最容易出错的点上。
    # 注意：链接副本的两个对象**位置必须分开**（早先把它们放重叠 → 有一只被完全遮住，
    # 图例色在图上找不到 → A4 判不出"是颜色错了还是被挡住了"）。
    base_me = _st_box_mesh("DSH_QC_ST_B", 1.0)
    for m in (bpy.data.materials.new("DSH_QC_ST_M0"), bpy.data.materials.new("DSH_QC_ST_M1")):
        base_me.materials.append(m)
        made_mats.append(m)
    ob_a = bpy.data.objects.new("DSH_QC_ST_A", _st_box_mesh("DSH_QC_ST_A", 1.0))
    made_meshes.append(ob_a.data)
    mt = bpy.data.materials.new("DSH_QC_ST_MA")
    ob_a.data.materials.append(mt)
    made_mats.append(mt)
    ob_b = bpy.data.objects.new("DSH_QC_ST_B", base_me)          # 与 ob_c 共享 mesh（链接副本）
    ob_c = bpy.data.objects.new("DSH_QC_ST_C", base_me)
    made_meshes.append(base_me)
    ob_d_me = _st_box_mesh("DSH_QC_ST_D", 1.0)                   # 0 材质槽
    ob_d = bpy.data.objects.new("DSH_QC_ST_D", ob_d_me)
    made_meshes.append(ob_d_me)
    for ob, loc in ((ob_a, (3.2, 0.0, 0.5)), (ob_b, (4.8, 0.0, 0.5)),
                    (ob_c, (6.4, 0.0, 0.5)), (ob_d, (8.0, 0.0, 0.5))):
        ob.location = loc
        sc.collection.objects.link(ob)
        made_objs.append(ob)
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    parts_n = len(_collect_objs(None)[0])          # 部件数 = 全部可见 mesh（含默认场景自带的）
    snap1 = _scene_snapshot()
    return made_objs, made_meshes, made_mats, parts_n, snap1

def _st_batch_parts_color(a, res, samples, outdir, lock_dir, parts_n, snap1, _chk):
    outdir_msgs = []
    # ---- 批次 1：parts_color（flat；top 视角让 5 个部件一个都不缺 —— A4 就靠它判"图例对不对得上图"）
    b1_views = a.get("views_batch1") or ["iso", "front", "top", "totally_bogus_view_xyz"]
    r1 = json.loads(qc_render_views({
        "mode": "parts_color", "parts_flat": True, "views": b1_views, "res": res, "samples": samples,
        "outdir": outdir, "tag": "selftest_pc", "warmup": True, "budget_s": 240,
        "jsonl": os.path.join(outdir, "selftest_pc.jsonl"), "lights_mode": "only",
        "lock_dir": lock_dir}))
    snap2 = _scene_snapshot()
    restore_diffs = _snap_diff(snap1, snap2)
    files = [e.get("path") for e in (r1.get("views") or [])]
    sizes = [int(e.get("bytes") or 0) for e in (r1.get("views") or [])]
    _chk("A1 parts_color 出图", bool(r1.get("ok")) and len(files) >= 1 and all(s > 0 for s in sizes),
         "ok=%s count=%s bytes=%s" % (r1.get("ok"), len(files), sizes))
    _chk("A2 渲后场景逐项还原（对象/材质槽/引擎/分辨率/采样/色彩变换）", not restore_diffs,
         ("逐项相等" if not restore_diffs else "差异：%s" % restore_diffs[:6]) + "；还原核对=%s"
         % _j((r1.get("parts_color") or {}).get("restore", {}).get("ok")))
    legend = (r1.get("parts_color") or {}).get("legend_path")
    legend_lines = (_legend_line_count(legend) if (legend and os.path.isfile(legend)) else None)
    _chk("A3 图例存在且行数 == 部件数", bool(legend and os.path.isfile(legend))
         and legend_lines == parts_n == (r1.get("parts_color") or {}).get("parts"),
         "legend=%s 行数=%s；独立数出来的可见 mesh=%s；结果里报的部件数=%s"
         % (legend, legend_lines, parts_n, (r1.get("parts_color") or {}).get("parts")))
    # A4：flat 图上色标必须能找到（用 top 视角那一张）
    used = []
    for row in ((r1.get("parts_color") or {}).get("assignments") or {}).values():
        if row.get("rgb"):
            used.append(row["rgb"])
    top_entry = None
    for e in (r1.get("views") or []):
        if str(e.get("view")) == "top":
            top_entry = e
    if top_entry is None and (r1.get("views") or []):
        top_entry = r1["views"][0]
    if top_entry and used:
        try:
            hits = _png_palette_hits(top_entry["path"], used)
        except Exception as e:
            hits = None
            outdir_msgs.append("读图失败：%s" % str(e)[:120])
        _chk("A4 图例色能在 flat 图上找到（颜色编码可对拍）", bool(hits) and all(h > 0 for h in hits),
             "view=%s 各色像素命中=%s（tol=16/255；0 = 该部件被完全遮挡或材质/色彩变换不对）"
             % (top_entry.get("view"), hits))
    # B1/B2：jsonl 每行有 device 键；device 是实测（null 也要给出处）
    jl = r1.get("jsonl")
    lines = []
    if jl and os.path.isfile(jl):
        with open(jl, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    lines.append(json.loads(ln))
    has_dev = [("device" in ln) for ln in lines]
    dev_ok = [bool(ln.get("device")) and ln["device"].get("engine") for ln in lines]
    _chk("B1 jsonl 每行都有 device 键", bool(lines) and all(has_dev),
         "jsonl=%s 行数=%d 都有 device 键=%s；device=%s"
         % (jl, len(lines), all(has_dev), _j([ln.get("device") for ln in lines[:2]])))
    _chk("B2 device 是实测（null 时给 device_source）", bool(lines) and all(
        (d and d.get("device") and d.get("source")) or ((not d or not d.get("device")) and ln.get("device_source"))
        for d, ln in zip([ln.get("device") for ln in lines], lines)),
        "本批 device=%s / source=%s" % (_j((r1.get("device") or {}).get("device")),
                                        (r1.get("device") or {}).get("source")))
    # C1：未知名进 unknown_views，已知视角照常渲
    known = [str(e.get("view")) for e in (r1.get("views") or [])]
    _chk("C1 未知名进 unknown_views 且已知视角照常渲",
         (r1.get("unknown_views") == ["totally_bogus_view_xyz"]) and len(known) == 3
         and set(known) == set(["iso", "front", "top"]) and known == ["front", "top", "iso"],
         "unknown_views=%s render_order=%s（目录顺序）count=%s"
         % (_j(r1.get("unknown_views")), known, r1.get("count")))
    # C2/C3/C4：纯函数口径（不渲染，快）
    # v0.9.1：isoright 从"→ iso"**修正**为"→ iso_br"（目录 desc 明写 iso-BR-top = 后右上），
    # 所以这条老断言改用 isofrontright 继续守"等轴测拼写 → iso"的老契约。
    rr = resolve_view_names(["iso-FR-top", "isometric", "isofrontright", "front-left", "ISO_L"])
    _chk("C2 别名归一（iso-FR-top/isometric/isofrontright → iso；front-left/ISO_L 直接命中目录）",
         rr["names"] == ["iso", "iso_l", "front_left"] and not rr["unknown"]
         and len(rr["aliases"]) == 3 and [x["resolved"] for x in rr["aliases"]] == ["iso"] * 3,
         "names=%s aliases=%s unknown=%s" % (_j(rr["names"]), _j(rr["aliases"]), _j(rr["unknown"])))
    bad = [k for k, v in LEGACY_NAMED_VIEWS.items() if NAMED_VIEWS.get(k) != v]
    _chk("C3 老具名视角角度逐项未变", not bad and len(VIEW_CATALOG) >= len(LEGACY_NAMED_VIEWS),
         "老名 %d 个全等（差异 %s）；目录共 %d 名" % (len(LEGACY_NAMED_VIEWS), bad or "无", len(VIEW_CATALOG)))
    rr2 = resolve_view_names(["top", "front", "front", "iso"])
    _chk("C4 目录顺序 + 去重（catalog 口径）",
         rr2["names"] == ["front", "top", "iso"] and rr2["deduped"] == ["front"],
         "names=%s deduped=%s（目录顺序取目录，不是请求顺序）" % (_j(rr2["names"]), _j(rr2["deduped"])))
    # E1：材质覆盖确实进了渲染（同视角、同色彩变换，只有材质不同 → hash 必须不同）
    r2 = json.loads(qc_render_views({
        "views": ["iso"], "res": res, "samples": samples, "outdir": outdir, "tag": "selftest_plain",
        "warmup": False, "view_transform": "Standard",
        "jsonl": os.path.join(outdir, "selftest_plain.jsonl"), "lights_mode": "only",
        "lock_dir": lock_dir}))
    h_pc = None
    for e in (r1.get("views") or []):
        if str(e.get("view")) == "iso":
            h_pc = e.get("hash")
    h_plain = ((r2.get("views") or [{}])[0]).get("hash")
    _chk("E1 材质覆盖确实进了渲染（plain vs parts_color 同视角 hash 不同）",
         bool(h_pc) and bool(h_plain) and h_pc != h_plain,
         "parts_color.iso=%s plain.iso=%s" % (h_pc, h_plain))
    snap3 = _scene_snapshot()
    d3 = _snap_diff(snap1, snap3)
    _chk("E1b 第二批复用同一现场也还原（plain 批次）", not d3, ("逐项相等" if not d3 else "差异：%s" % d3[:6]))
    return {"batch1": r1, "batch2": r2, "hits": hits, "legend_lines": legend_lines}

def _st_phase_v091a(a, res, samples, outdir, lock_dir, sc, _chk):
    # ---- F1（A1）：views 全未知名 → ok:false 且**一张都不渲**（也检查产物目录里没有兜底 PNG / jsonl）
    # 注意：这里必须用**真·未知名**（不含别名可命中的拼写）—— "side_left/side_right" 在 v0.9.1 已收进
    # 别名表（A1-4），拿它们当"全未知名"会被别名解析成功 → 那是 F1b 的"部分未知名"场景。
    a1_dir = os.path.join(outdir, "selftest_a1_unknown", time.strftime("%Y%m%d-%H%M%S"))
    r3 = json.loads(qc_render_views({
        "views": ["muzzle_end", "breech_end", "flux_capacitor", "side"],
        "res": res, "samples": samples, "outdir": a1_dir, "tag": "selftest_a1",
        "jsonl": os.path.join(a1_dir, "selftest_a1.jsonl"), "lock_dir": lock_dir}))
    a1_files = sorted(os.listdir(a1_dir)) if os.path.isdir(a1_dir) else []
    a1_pngs = [n for n in a1_files if n.lower().endswith(".png")]
    a1_jsonls = [n for n in a1_files if n.lower().endswith(".jsonl")]
    _chk("F1 A1 全未知名 → ok:false 且一张都没渲（无兜底图 / 无 jsonl / 无 PNG）",
         (r3.get("ok") is False) and r3.get("rendered_count") == 0 and r3.get("count") == 0
         and r3.get("unknown_count") == 4
         and r3.get("unknown_views") == ["muzzle_end", "breech_end", "flux_capacitor", "side"]
         and r3.get("no_guess_views") == ["muzzle_end", "breech_end"]
         and bool(r3.get("available_views")) and len(r3.get("available_views") or []) == len(VIEW_CATALOG)
         and bool(r3.get("hint")) and bool(r3.get("view_menu")) and not a1_pngs and not a1_jsonls,
         "ok=%s rendered_count=%s unknown_count=%s no_guess=%s outdir=%s 文件=%s available_views=%d hint=%s"
         % (r3.get("ok"), r3.get("rendered_count"), r3.get("unknown_count"), _j(r3.get("no_guess_views")),
            a1_dir, _j(a1_files), len(r3.get("available_views") or []), str(r3.get("hint"))[:80]))

    # ---- F1b（A1）：93 反馈里的**原始复现名单**（side_left/side_right/muzzle_end/breech_end）——
    # A1-4 收进拼写别名后，前者解析成 left/right、后者仍不猜 → 这次是"部分未知名"：渲 2 张、
    # 计数 2/4/2 一眼可见；**关键判据是产物里没有 iso/top/front 兜底图**（v0.8.11 会静默渲这 4 张）。
    a1b_dir = os.path.join(outdir, "selftest_a1_repro", time.strftime("%Y%m%d-%H%M%S"))
    r3b = json.loads(qc_render_views({
        "views": ["side_left", "side_right", "muzzle_end", "breech_end"], "warmup": False,
        "res": res, "samples": samples, "outdir": a1b_dir, "tag": "selftest_a1b",
        "jsonl": os.path.join(a1b_dir, "selftest_a1b.jsonl"), "lights_mode": "only", "lock_dir": lock_dir}))
    a1b_pngs = sorted([n for n in os.listdir(a1b_dir)
                       if n.lower().endswith(".png")]) if os.path.isdir(a1b_dir) else []
    _chk("F1b A1 93 反馈原始名单 → 别名 2 张 + 未知名 2 个（计数 2/4/2），且**没有 iso/front/top 兜底图**",
         bool(r3b.get("ok")) and r3b.get("requested_count") == 4 and r3b.get("rendered_count") == 2
         and r3b.get("unknown_count") == 2 and r3b.get("count") == 2
         and set(a1b_pngs) == set(["selftest_a1b_left.png", "selftest_a1b_right.png"])
         and not [n for n in a1b_pngs if n.endswith(("_iso.png", "_top.png", "_front.png"))],
         "requested/rendered/unknown=%s/%s/%s 产物=%s（应为 left/right 两张，无兜底）"
         % (r3b.get("requested_count"), r3b.get("rendered_count"), r3b.get("unknown_count"), _j(a1b_pngs)))

    # ---- F2/F6（A1 默认 / A2 缺省 Standard）：views 省略 → 默认 3 视角（v0.8.11 口径）；jsonl 每行有 view_transform
    vt_scene_before = str(getattr(getattr(sc, "view_settings", None), "view_transform", None))
    r4 = json.loads(qc_render_views({
        "res": res, "samples": samples, "outdir": outdir, "tag": "selftest_default4",
        "warmup": False, "jsonl": os.path.join(outdir, "selftest_default4.jsonl"),
        "lights_mode": "only", "lock_dir": lock_dir}))
    d4_names = [str(e.get("view")) for e in (r4.get("views") or [])]
    vt_scene_after = str(getattr(getattr(sc, "view_settings", None), "view_transform", None))
    _chk("F2 A1 views 省略 → 默认 3 视角（iso/front/right，与 v0.8.11 一致）+ 计数一致",
         bool(r4.get("ok")) and d4_names == ["front", "right", "iso"] and r4.get("count") == 3
         and r4.get("requested_count") == 3 and r4.get("rendered_count") == 3
         and r4.get("views_from") == "default_legacy" and r4.get("unknown_count") == 0,
         "render_order=%s requested_count=%s rendered_count=%s views_from=%s"
         % (_j(d4_names), r4.get("requested_count"), r4.get("rendered_count"), r4.get("views_from")))
    lines4 = _read_jsonl(r4.get("jsonl"))
    vt_lines4 = [ln.get("view_transform") for ln in lines4]
    vtr = r4.get("view_transform_restore") or {}
    _chk("F6 A2 缺省 view_transform 实测 Standard + jsonl 每行都有 + 出图后场景逐项还原",
         r4.get("view_transform") == "Standard" and r4.get("view_transform_mode") == "default_standard"
         and bool(lines4) and all(v == "Standard" for v in vt_lines4)
         and bool(vtr.get("ok")) and vtr.get("scene_before") == vt_scene_before
         and vtr.get("scene_after") == vt_scene_before and vt_scene_after == vt_scene_before,
         "view_transform=%s mode=%s jsonl 行数=%d 逐行值=%s；场景 before=%r after(还原后)=%r 快照=%s"
         % (r4.get("view_transform"), r4.get("view_transform_mode"), len(lines4), _j(set(vt_lines4)),
            vt_scene_before, vt_scene_after, _j(vtr)))
    return {"r3": r3, "a1_dir": a1_dir, "a1_pngs": a1_pngs, "a1_jsonls": a1_jsonls, "r3b": r3b, "a1b_dir": a1b_dir, "a1b_pngs": a1b_pngs, "r4": r4, "d4_names": d4_names, "lines4": lines4, "vt_scene_before": vt_scene_before}

def _st_phase_v091b(a, res, samples, outdir, lock_dir, _chk):
    # ---- F3/F4（A1 部分未知名 / 别名）：requested/rendered/unknown 计数 + 别名生效 + *_end 不猜
    r5 = json.loads(qc_render_views({
        "views": ["side_left", "iso_right", "muzzle_end"], "res": res, "samples": samples,
        "outdir": outdir, "tag": "selftest_alias", "warmup": False,
        "jsonl": os.path.join(outdir, "selftest_alias.jsonl"), "lights_mode": "only", "lock_dir": lock_dir}))
    n5 = [str(e.get("view")) for e in (r5.get("views") or [])]
    _chk("F3 A1 部分未知名 → 三个计数正确（rendered_count < requested_count）",
         bool(r5.get("ok")) and r5.get("requested_count") == 3 and r5.get("rendered_count") == 2
         and r5.get("unknown_count") == 1 and r5.get("count") == 2
         and r5.get("unknown_views") == ["muzzle_end"],
         "requested_count=%s rendered_count=%s unknown_count=%s unknown_views=%s"
         % (r5.get("requested_count"), r5.get("rendered_count"), r5.get("unknown_count"),
            _j(r5.get("unknown_views"))))
    _chk("F4 A1 拼写别名 side_left→left / iso_right→iso_br 生效；muzzle_end 仍进 unknown（不猜）",
         n5 == ["left", "iso_br"]
         and sorted([(x.get("requested"), x.get("resolved")) for x in (r5.get("view_aliases_applied") or [])])
             == [("iso_right", "iso_br"), ("side_left", "left")]
         and r5.get("no_guess_views") == ["muzzle_end"] and r5.get("unknown_views") == ["muzzle_end"],
         "render_order=%s aliases=%s no_guess=%s" % (_j(n5), _j(r5.get("view_aliases_applied")),
                                                     _j(r5.get("no_guess_views"))))

    # ---- F5（A1 老形态不失效）：显式 3 视角仍渲 3 张
    r5b = json.loads(qc_render_views({
        "views": ["iso", "front", "right"], "res": res, "samples": samples, "outdir": outdir,
        "tag": "selftest_explicit3", "warmup": False,
        "jsonl": os.path.join(outdir, "selftest_explicit3.jsonl"), "lights_mode": "only",
        "lock_dir": lock_dir}))
    _chk("F5 A1 显式 3 视角仍渲 3 张（老 parameters 形态没失效）",
         bool(r5b.get("ok")) and r5b.get("count") == 3
         and [str(e.get("view")) for e in (r5b.get("views") or [])] == ["front", "right", "iso"]
         and r5b.get("requested_count") == 3 and r5b.get("rendered_count") == 3,
         "render_order=%s count=%s requested_count=%s"
         % (_j([str(e.get("view")) for e in (r5b.get("views") or [])]), r5b.get("count"),
            r5b.get("requested_count")))
    return {"r5": r5, "n5": n5, "r5b": r5b}

def _st_phase_v091c(a, res, samples, outdir, lock_dir, r4, sc, assertions, _chk):
    # ---- F7（A2 "scene" 三态）：沿用场景值（非 Standard → warning），且 hash 与 Standard 那张不同
    vt_orig = str(getattr(getattr(sc, "view_settings", None), "view_transform", None))
    vt_nonstd = None
    for _cand in ("AgX", "Filmic", "Khronos PBR Neutral", "Filmic Log", "Raw"):
        try:
            sc.view_settings.view_transform = _cand
            if str(sc.view_settings.view_transform) == _cand:
                vt_nonstd = _cand
                break
        except Exception:
            pass
    r6 = None
    if vt_nonstd is None:
        # 本机 OCIO 里没有非 Standard 选项 → **明确标 untested**（不假装测过这条分支）
        assertions.append({"name": "F7 A2 view_transform=\"scene\" 沿用场景值（非 Standard 给 warning）",
                           "pass": False, "untested": True,
                           "detail": "本机 view_transform 只有 Standard 可选，无法实测「非 Standard + warning」分支"})
    else:
        r6 = json.loads(qc_render_views({
            "views": ["front"], "res": res, "samples": samples, "outdir": outdir, "tag": "selftest_scene",
            "warmup": False, "view_transform": "scene",
            "jsonl": os.path.join(outdir, "selftest_scene.jsonl"), "lights_mode": "only",
            "lock_dir": lock_dir}))
        sc_after6 = str(getattr(getattr(sc, "view_settings", None), "view_transform", None))
        lines6 = _read_jsonl(r6.get("jsonl"))
        warn6 = " ".join([str(w) for w in (r6.get("scope_warnings") or [])])
        h_std = [e.get("hash") for e in (r4.get("views") or []) if str(e.get("view")) == "front"]
        h_scn = [e.get("hash") for e in (r6.get("views") or []) if str(e.get("view")) == "front"]
        _chk("F7 A2 view_transform=\"scene\" 沿用场景 %s（非 Standard 给 warning；hash 与 Standard 不同）" % vt_nonstd,
             bool(r6.get("ok")) and r6.get("view_transform") == vt_nonstd
             and r6.get("view_transform_mode") == "scene" and r6.get("view_transform_assigned") is None
             and ("阈值判读需自行校正" in warn6) and sc_after6 == vt_nonstd
             and bool(lines6) and all(ln.get("view_transform") == vt_nonstd for ln in lines6)
             and bool(h_std) and bool(h_scn) and h_std[0] != h_scn[0],
             "view_transform=%s jsonl=%s warning=%s 场景 before/after=%s/%s；"
             "Standard.front=%s scene.front=%s（不同才对）"
             % (r6.get("view_transform"), _j([ln.get("view_transform") for ln in lines6]),
                ("有" if "阈值判读需自行校正" in warn6 else "无"), vt_nonstd, sc_after6,
                (h_std or [None])[0], (h_scn or [None])[0]))
    try:
        sc.view_settings.view_transform = vt_orig          # 还原自检自己动过的场景设置（E2 会再兜一次）
    except Exception:
        pass

    # ---- F8（B1/B2）：可调用 API
    _Kapi = getattr(_kernel(), "dsh_qc_render_api", None) if _kernel() is not None else None
    api_res = api_res2 = api_str = api_help = None
    api_err = None
    if _Kapi is None:
        api_err = "K.dsh_qc_render_api 不存在（kernel 未注入？）"
    else:
        try:
            _unk = {"views": ["not_a_real_view_name"], "res": res, "outdir": outdir, "lock_dir": lock_dir}
            api_res = _Kapi("render_views", _unk)      # 位置参形态
            api_res2 = _Kapi(_unk)                     # 只给 dict（等价 render_views）
            api_str = _Kapi["dispatch"]("render_catalog", "{}")   # 老契约：仍是 str
            api_help = _Kapi("help")
        except Exception as e:
            api_err = "%s: %s" % (type(e).__name__, str(e)[:160])
    _chk("F8 B1/B2 api(...) 返回 dict、api({...}) 等价、api[\"dispatch\"] 仍返回 str、api(\"help\") 可用",
         (api_err is None) and callable(_Kapi) and isinstance(api_res, dict) and isinstance(api_res2, dict)
         and isinstance(api_str, str) and isinstance(api_help, dict)
         and api_res.get("ok") is False and api_res.get("rendered_count") == 0
         and api_res2.get("unknown_views") == ["not_a_real_view_name"]
         and api_help.get("version") == QC_RENDER_VERSION,
         "callable=%s api(...)=%s api({...})=%s dispatch=%s help=%s err=%s"
         % (callable(_Kapi), type(api_res).__name__, type(api_res2).__name__, type(api_str).__name__,
            type(api_help).__name__, api_err))
    return {"r6": r6, "api_err": api_err, "api_res": api_res, "api_res2": api_res2, "api_str": api_str, "_Kapi": _Kapi}

def _st_phase_lock(lock_dir, lines4, r4, a, res, samples, outdir, sc, _Kapi, _chk):
    # ---- G1/G2/G3（E1）：锁原语（自检专用目录）
    def _lk(**kw):
        return json.loads(render_lock(dict({"dir": lock_dir}, **kw)))

    g1a = _lk(action="acquire", holder="selftest-A", ttl_s=600)
    g1b = _lk(action="acquire", holder="selftest-B", wait_s=1)
    _chk("G1 E1 A 持有 → B wait_s=1 超时被拒（报 holder + waited_ms ≥ 900）",
         bool(g1a.get("ok")) and bool(g1a.get("acquired")) and (g1b.get("ok") is False)
         and g1b.get("current_holder") == "selftest-A" and int(g1b.get("waited_ms") or 0) >= 900
         and os.path.isfile(_lock_path(lock_dir)) and bool(g1b.get("hint")),
         "A=%s；B ok=%s holder=%s waited_ms=%s path=%s"
         % (_j({k: g1a.get(k) for k in ("ok", "acquired", "holder", "waited_ms", "stale_broken")}),
            g1b.get("ok"), g1b.get("current_holder"), g1b.get("waited_ms"), _lock_path(lock_dir)))
    g2a = _lk(action="release", holder="selftest-B")        # holder 不匹配 → 必须被拒
    g2b = _lk(action="release", holder="selftest-A")        # 真持有者 → 放掉
    g2c = _lk(action="acquire", holder="selftest-B", wait_s=0)
    g2s = _lk(action="status")
    _chk("G2 E1 holder 不匹配的 release 被拒；A release 后 B 能拿到（status 报 B）",
         (g2a.get("ok") is False) and g2a.get("current_holder") == "selftest-A"
         and bool(g2b.get("ok")) and bool(g2b.get("released"))
         and bool(g2c.get("ok")) and bool(g2c.get("acquired")) and g2s.get("holder") == "selftest-B",
         "B 释放被拒=%s（当前 %s）；A 释放=%s；B 再取=%s；status holder=%s remaining_s=%s"
         % (g2a.get("ok"), g2a.get("current_holder"), g2b.get("released"), g2c.get("acquired"),
            g2s.get("holder"), g2s.get("remaining_s")))
    g2d = _lk(action="release", holder="selftest-B")
    # G3：伪造一把"过期锁"（acquired_at 是很久以前的时间戳）→ acquire 必须按时间戳判过期并打破
    _lp = _lock_path(lock_dir)
    os.makedirs(os.path.dirname(_lp), exist_ok=True)
    with open(_lp, "w", encoding="utf-8") as f:
        f.write(_j({"holder": "selftest-stale", "pid": 0, "acquired_at": time.time() - 9999.0,
                    "ttl_s": 600.0, "note": "自检伪造的过期锁"}))
    g3 = _lk(action="acquire", holder="selftest-C", ttl_s=600, wait_s=0)
    g3r = _lk(action="release", holder="selftest-C")
    _chk("G3 E1 伪造过期锁 → 被打破且 stale_broken=true，新持有者拿到",
         bool(g3.get("ok")) and bool(g3.get("acquired")) and bool(g3.get("stale_broken"))
         and g3.get("holder") == "selftest-C" and bool(g3r.get("ok"))
         and bool(g3.get("stale_note")),
         "stale_broken=%s holder=%s waited_ms=%s note=%s" % (g3.get("stale_broken"), g3.get("holder"),
                                                             g3.get("waited_ms"), g3.get("stale_note")))
    # G4：自动锁写进了产物（每行 jsonl 的 lock）
    lock_paths4 = [((ln.get("lock") or {}).get("path")) for ln in lines4]
    lock_ok4 = all(isinstance(ln.get("lock"), dict) and (ln["lock"].get("acquired") is True)
                   and bool(ln["lock"].get("holder")) and ("waited_ms" in ln["lock"])
                   and ("stale_broken" in ln["lock"]) and bool(ln["lock"].get("path")) for ln in lines4)
    _chk("G4 E1 jsonl 每行都有 lock{holder, waited_ms, acquired, stale_broken, path}（自动锁）",
         bool(lines4) and lock_ok4 and all(p == _lock_path(lock_dir) for p in lock_paths4)
         and (r4.get("lock") or {}).get("released") is True,
         "行数=%d lock[0]=%s 路径全等=%s 返回体 lock.released=%s"
         % (len(lines4), _j(lines4[0].get("lock")) if lines4 else None,
            all(p == _lock_path(lock_dir) for p in lock_paths4), (r4.get("lock") or {}).get("released")))

    # ---- G5（E1/B2）：engine 的**两条路由拼写**都落到实处（plan 通道真实会发的 op）
    # engine.mjs：op="render_lock" → dispatch("lock")（render_* 分支 slice(7)）；
    #             op="render_status" → dispatch("status")；op="qc_render_lock" → dispatch("render_lock")。
    g5, g5_err = {}, None
    if _Kapi is not None:
        try:
            for _op in ("lock", "render_lock", "status", "render_status"):
                _s = _Kapi["dispatch"](_op, _j({"action": "status", "dir": lock_dir}))
                g5[_op] = [isinstance(_s, str), bool(json.loads(_s).get("ok"))]
        except Exception as e:
            g5_err = "%s: %s" % (type(e).__name__, str(e)[:160])
    _chk("G5 E1 engine 路由拼写（lock / render_lock / status / render_status）都能 dispatch 且仍返回 str",
         bool(g5) and g5_err is None and all(v[0] and v[1] for v in g5.values()),
         "各拼写=[返回 str, ok]=%s err=%s" % (_j(g5), g5_err))

    # ---- G6（E1 回归）：拿不到锁 → **空产出 ok:false**，而且引擎/采样必须已写回（早退不污染场景）
    # 为什么有它：实测踩到过一次 —— 锁超时的早退路径不走 finally，把 eevee.taa_render_samples
    # 从 64 留成了 16（E2 快照对拍抓出来的）。这条断言专门守住这个回归。
    _hold = _lk(action="acquire", holder="selftest-HOLD", ttl_s=600)
    _eng0 = str(sc.render.engine)
    _ee0 = int(getattr(getattr(sc, "eevee", None), "taa_render_samples", -1))
    _cy0 = int(sc.cycles.samples) if hasattr(sc, "cycles") else -1
    r7 = json.loads(qc_render_views({
        "views": ["front"], "res": res, "samples": 8, "outdir": outdir, "tag": "selftest_lockfail",
        "warmup": False, "lock_wait_s": 0, "lock_dir": lock_dir, "lights_mode": "only"}))
    _eng1 = str(sc.render.engine)
    _ee1 = int(getattr(getattr(sc, "eevee", None), "taa_render_samples", -1))
    _cy1 = int(sc.cycles.samples) if hasattr(sc, "cycles") else -1
    _pre = r7.get("prelock_restore") or {}
    _chk("G6 E1 拿不到锁 → 空产出 ok:false，且引擎/采样已写回（早退不污染场景）",
         bool(_hold.get("ok")) and (r7.get("ok") is False) and r7.get("count") == 0
         and r7.get("rendered_count") == 0
         and (r7.get("lock") or {}).get("acquired") is False
         and r7.get("current_holder") == "selftest-HOLD"
         and r7.get("holder_ttl_remaining_s") is not None
         and bool(_pre.get("ok")) and _eng1 == _eng0 and _ee1 == _ee0 and _cy1 == _cy0,
         "ok=%s holder=%s remaining_s=%s；engine %s→%s · eevee %s→%s · cycles %s→%s · prelock=%s"
         % (r7.get("ok"), r7.get("current_holder"), r7.get("holder_ttl_remaining_s"),
            _eng0, _eng1, _ee0, _ee1, _cy0, _cy1, _j(_pre)))
    _lk(action="release", holder="selftest-HOLD")
    return {"g1b": g1b, "g3": g3}

def _st_cleanup(made_objs, made_meshes, made_mats, outdir, snap0):
    for ob in made_objs:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for me in made_meshes:
        try:
            if me.users == 0:
                bpy.data.meshes.remove(me)
        except Exception:
            pass
    for m in made_mats:
        try:
            bpy.data.materials.remove(m, do_unlink=True)
        except Exception:
            pass
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    # v0.9.1：锁文件也清掉（自检专用目录；真用户的锁在 K.workdir/locks 下，绝不碰）
    try:
        _lp_clean = _lock_path(os.path.join(outdir, "selftest_lock"))
        if os.path.isfile(_lp_clean):
            os.remove(_lp_clean)
    except Exception:
        pass
    return _snap_diff(snap0, _scene_snapshot())

def _st_report(a, outdir, res, samples, parts_n, hits, legend_lines, batch1, batch2, assertions,
               r3, a1_dir, a1_pngs, a1_jsonls, r3b, a1b_dir, a1b_pngs, r4, d4_names,
               r5, n5, r5b, r6, api_err, api_res, api_res2, api_str, g1b, g3, lock_dir,
               cleanup_diffs, _chk):
    # ---- D1：投影对拍（老自检内容）
    try:
        proj = _selftest_projection()
    except Exception as e:
        proj = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    _chk("D1 投影对拍 proj_err_px ≈ 0（自算矩阵 vs Blender 真值）",
         bool(proj) and proj.get("all_inside") and (proj.get("proj_err_px_max") is None
                                                    or proj["proj_err_px_max"] < 0.05),
         "all_inside=%s proj_err_px_max=%s 视角数=%s" % (proj.get("all_inside"), proj.get("proj_err_px_max"),
                                                       len(proj.get("views") or [])))
    _chk("E2 自检对象/材质/mesh 清理干净（回到进入自检前的快照）", not cleanup_diffs,
         ("逐项相等" if not cleanup_diffs else "差异：%s" % cleanup_diffs[:8]))

    failed = [x["name"] for x in assertions if not x["pass"]]
    out = {"ok": not failed, "version": QC_RENDER_VERSION, "kind": "qc_render_selftest",
           "blender": str(bpy.app.version_string), "background": bool(bpy.app.background),
           "file": bpy.data.filepath, "outdir": outdir, "res": res, "samples": samples,
           "assertions": assertions, "failed": failed,
           "parts_color_batch": ({"ok": batch1.get("ok"), "count": batch1.get("count"),
                                  "mode": batch1.get("mode"), "jsonl": batch1.get("jsonl"),
                                  "legend": (batch1.get("parts_color") or {}).get("legend_path"),
                                  "legend_lines": legend_lines,
                                  "parts": (batch1.get("parts_color") or {}).get("parts"),
                                  "restore": (batch1.get("parts_color") or {}).get("restore"),
                                  "view_transform_defaulted":
                                      (batch1.get("parts_color") or {}).get("view_transform_defaulted"),
                                  "palette_hits": hits} if batch1 else None),
           "plain_batch": ({"ok": batch2.get("ok"), "count": batch2.get("count"),
                            "jsonl": batch2.get("jsonl")} if batch2 else None),
           "device": (batch1.get("device") if batch1 else None),
           "device_line": (batch1.get("device_line") if batch1 else None),
           "views": ({"unknown_views": batch1.get("unknown_views"), "render_order": batch1.get("render_order"),
                      "aliases": batch1.get("view_aliases_applied"), "menu": view_menu_text()} if batch1 else None),
           # ---- v0.9.1（93 反馈）：A1/A2/E1/B 的实测快照（自检报告要能被人复核，不只靠开头那几行）
           "v091": {"a1_unknown": ({"ok": r3.get("ok"), "rendered_count": r3.get("rendered_count"),
                                    "unknown_views": r3.get("unknown_views"),
                                    "no_guess_views": r3.get("no_guess_views"), "outdir": a1_dir,
                                    "png_in_outdir": a1_pngs, "jsonl_in_outdir": a1_jsonls} if r3 else None),
                    "a1_repro_93": ({"ok": r3b.get("ok"), "requested_count": r3b.get("requested_count"),
                                     "rendered_count": r3b.get("rendered_count"),
                                     "unknown_count": r3b.get("unknown_count"),
                                     "png": a1b_pngs, "outdir": a1b_dir} if r3b else None),
                    "default4": ({"ok": r4.get("ok"), "render_order": d4_names,
                                  "requested_count": r4.get("requested_count"),
                                  "rendered_count": r4.get("rendered_count"), "views_from": r4.get("views_from"),
                                  "view_transform": r4.get("view_transform"),
                                  "view_transform_restore": r4.get("view_transform_restore"),
                                  "lock": r4.get("lock")} if r4 else None),
                    "partial_unknown": ({"ok": r5.get("ok"), "requested_count": r5.get("requested_count"),
                                         "rendered_count": r5.get("rendered_count"),
                                         "unknown_count": r5.get("unknown_count"),
                                         "render_order": n5,
                                         "aliases": r5.get("view_aliases_applied")} if r5 else None),
                    "explicit3": ({"ok": r5b.get("ok"), "count": r5b.get("count"),
                                   "render_order": [str(e.get("view")) for e in (r5b.get("views") or [])]}
                                  if r5b else None),
                    "scene_transform": ({"ok": r6.get("ok"), "view_transform": r6.get("view_transform"),
                                         "mode": r6.get("view_transform_mode"),
                                         "warnings": r6.get("scope_warnings")} if r6 else {"tested": False,
                                                                                            "reason": "本机无非 Standard 选项"}),
                    "api": {"callable": bool(api_err is None), "call_returns": type(api_res).__name__,
                            "dict_arg_returns": type(api_res2).__name__,
                            "dispatch_returns": type(api_str).__name__, "error": api_err},
                    "lock": {"g1_waited_ms": g1b.get("waited_ms"), "g1_holder": g1b.get("current_holder"),
                             "g3_stale_broken": g3.get("stale_broken"), "path": _lock_path(lock_dir),
                             "dir": lock_dir}},
           "projection": proj,
           "expect": "ok=true 表示 A1-A4/B1-B2/C1-C4/D1/E1-E2 + v0.9.1 的 F1-F8/G1-G6 全过；"
                     "任一不过 → ok=false 且 failed 里点名"}
    # 自检报告落盘：证据要能被人和其他会话复核（工具返回值会被日志截断，磁盘上的不会）
    try:
        rp = _win(a.get("report") or os.path.join(outdir, "qc_render_selftest.json"))
        with open(rp, "w", encoding="utf-8") as f:
            f.write(_j(out))
        out["report_path"] = rp
    except Exception as e:
        out["report_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return _j(out)

def qc_render_selftest(args=None):
    """渲染 harness 自检（v0.9.1 扩展）：投影对拍（老内容）+ Procedura 融合（v0.8.11）+ 93 反馈 A1/A2/E1/B1-B2。

    **必须在无头隔离进程里跑**（会临时建对象、覆盖材质槽、出图；虽然全部还原，但你在看着的场景不该被碰）：
        blender_run(kind="headless", preload="qc,qc_render", engine="eevee", factory_startup=True, timeout_ms=300000,
                            script="import json;print('HEADLESS ' + K.dsh_qc_render_api['selftest']())")
    GUI 里默认拒绝；确实要跑传 {"allow_gui": true}（自检自己建的对象会删干净，但你的场景会被渲几张）。

    args：{"outdir": 产物目录（默认 K.out_dir）, "res": 默认 [192,192], "samples": 默认 16,
           "allow_gui": bool, "views_batch1": [...], "keep_files": bool}

    断言（ok = 全部 pass；每条都在 assertions 里给 pass + detail，不静默）：
      A1 parts_color 出图（≥1 张、字节 > 0）                A2 渲后场景**逐项还原**（对象数/材质槽/引擎/分辨率/采样/色彩变换）
      A3 图例存在且行数 == 部件数                            A4 flat 图上能找到每一个部件的图例色（颜色编码没写错）
      B1 jsonl 每行都有 device 键（值可为 null）             B2 device 是实测：给出 engine/device 或 null+出处
      C1 未知名进 unknown_views，已知视角照常渲              C2 别名归一（iso-FR-top/isometric/isofrontright → iso）
      C3 老具名视角角度逐项未变（LEGACY_NAMED_VIEWS）        C4 目录顺序 + 去重生效（catalog 口径）
      D1 投影对拍 proj_err_px ≈ 0（老自检内容）
      E1 材质覆盖确实进了渲染（同视角 plain vs parts_color 的 hash 不同）
      E2 自检对象清理干净（场景回到进入自检前的快照）
      —— v0.9.1（93 反馈）新增 ——
      F1 A1：views 全未知名 → ok:false 且**一张都没渲**（outdir 里没有任何 PNG，无兜底图）
      F2 A1：views 省略 → 与 v0.8.11 一致的默认 3 视角（iso/front/right）；requested_count == rendered_count == 3
      F3 A1：部分未知名 → rendered_count < requested_count 且三个计数逐个正确
      F4 A1：拼写别名生效（side_left→left、iso_right→iso_br），muzzle_end 仍进 unknown（命中不猜纪律）
      F5 A1：显式 3 视角仍渲 3 张（老参数形态没失效）
      F6 A2：缺省 view_transform 实测 == "Standard"，jsonl 每行都有；出图后场景 transform 逐项还原
      F7 A2：view_transform="scene" → 沿用场景值（非 Standard 时给 warning），且与 Standard 那张 hash 不同
      F8 B：api("render_views", {...}) 返回 dict、api({...}) 等价、api["dispatch"] 仍返回 str、api("help") 可用
      G1 E1：A 持有 → B wait_s=1 超时被拒（给出 holder + waited_ms ≥ 900）
      G2 E1：holder 不匹配的 release 被拒；A release 后 B 能拿到
      G3 E1：伪造过期锁 → 被打破且 stale_broken=true，新持有者拿到
      G4 E1：jsonl 每行都有 lock{holder, waited_ms, acquired, stale_broken, path}（自动锁写进了产物）
      G5 E1：engine 路由拼写（lock / render_lock / status / render_status）都能 dispatch 且仍返回 str
      G6 E1 回归：拿不到锁 → 空产出 ok:false，且引擎/采样已写回（早退路径不污染场景）
    """
    a = _unpack_args(args)
    assertions = []

    def _chk(name, ok, detail):
        assertions.append({"name": name, "pass": bool(ok), "detail": detail})
        return bool(ok)

    if not bool(getattr(bpy.app, "background", False)) and not a.get("allow_gui"):
        return _j({"ok": False, "version": QC_RENDER_VERSION, "kind": "qc_render_selftest",
                   "error": "GUI 进程里默认不跑自检（会临时改当前场景的材质槽并渲几张）",
                   "hint": "无头隔离跑：blender_run(kind='headless', preload=\"qc,qc_render\", engine=\"eevee\", "
                           "factory_startup=True, timeout_ms=300000, "
                           "script=\"import json;print('HEADLESS ' + K.dsh_qc_render_api['selftest']())\")；"
                           "确要在 GUI 跑就传 {\"allow_gui\": true}"})
    outdir = _win(a.get("outdir") or _out_dir())
    rv = a.get("res") or [192, 192]
    if isinstance(rv, (int, float)):
        rv = [int(rv), int(rv)]
    res = [max(64, int(rv[0])), max(64, int(rv[1]))]
    samples = int(a.get("samples") or 16)
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        return _j({"ok": False, "error": "outdir 建不出来: %s (%s)" % (outdir, e)})
    sc = bpy.context.scene
    snap0 = _scene_snapshot()
    # v0.9.1（E1）：自检**全程用隔离锁目录**（<outdir>/selftest_lock），绝不与线上渲染抢真锁
    # —— 实测踩到：自检头两批没传 lock_dir，撞上真锁后等满 120s 失败，硬把 9 条老断言判红。
    lock_dir = os.path.join(outdir, "selftest_lock")
    made_objs, made_meshes, made_mats = [], [], []
    batch1 = batch2 = proj = None
    parts_n = legend_lines = None
    hits = None
    cleanup_diffs = None
    try:
        made_objs, made_meshes, made_mats, parts_n, snap1 = _st_build_parts(sc)
        _pm = _st_batch_parts_color(a, res, samples, outdir, lock_dir, parts_n, snap1, _chk)
        batch1, batch2 = _pm["batch1"], _pm["batch2"]
        hits, legend_lines = _pm["hits"], _pm["legend_lines"]
        _va = _st_phase_v091a(a, res, samples, outdir, lock_dir, sc, _chk)
        r3, a1_dir, a1_pngs, a1_jsonls = _va["r3"], _va["a1_dir"], _va["a1_pngs"], _va["a1_jsonls"]
        r3b, a1b_dir, a1b_pngs = _va["r3b"], _va["a1b_dir"], _va["a1b_pngs"]
        r4, d4_names, lines4 = _va["r4"], _va["d4_names"], _va["lines4"]
        _vb = _st_phase_v091b(a, res, samples, outdir, lock_dir, _chk)
        r5, n5, r5b = _vb["r5"], _vb["n5"], _vb["r5b"]
        _vc = _st_phase_v091c(a, res, samples, outdir, lock_dir, r4, sc, assertions, _chk)
        r6, api_err, api_res, api_res2, api_str = _vc["r6"], _vc["api_err"], _vc["api_res"], _vc["api_res2"], _vc["api_str"]
        _lk = _st_phase_lock(lock_dir, lines4, r4, a, res, samples, outdir, sc, _vc["_Kapi"], _chk)
        g1b, g3 = _lk["g1b"], _lk["g3"]
    finally:
        cleanup_diffs = _st_cleanup(made_objs, made_meshes, made_mats, outdir, snap0)
    return _st_report(a, outdir, res, samples, parts_n, hits, legend_lines, batch1, batch2, assertions,
                       r3, a1_dir, a1_pngs, a1_jsonls, r3b, a1b_dir, a1b_pngs, r4, d4_names,
                       r5, n5, r5b, r6, api_err, api_res, api_res2, api_str, g1b, g3, lock_dir,
                       cleanup_diffs, _chk)
