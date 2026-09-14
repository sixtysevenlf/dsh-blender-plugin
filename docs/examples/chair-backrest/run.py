# -*- coding: utf-8 -*-
r"""椅子靠背接缝实验 —— 外部证据不足 + 探针判别（S0 示例，纯无头可复现）

问题（来自 issue #3 的场景）：
    参考图里靠背与座椅的连接处被挡板遮住，看不出靠背是
      H_attach【贴附】：靠背是一块板，紧贴座椅后表面（内部没有榫）
      H_insert【插入】：同一块板 + 内部榫头插进座椅（外部轮廓完全一样）
    两种假设在**所有外部视图**下外形相同 → 外部证据无法判别。
    要判别只能靠**探针**：拆/藏座椅看榫槽（本示例用"隐藏座椅后再看"代替，非破坏性）。

本示例用可复现的方式证明四件事：
    1) 假设 = 允许区间 + 暂时禁止项（不是一句自然语言）；
    2) 程序在区间内搜索，用**可见轮廓**打分；
    3) **可辨识与不可辨识要分开**：外部视图能把板的位置 dy 钉到毫米级，却对"内部榫长"完全不敏感
       （目标函数在该维度上平坦 → 该维度不可辨识）；
    4) 判定必须是【unresolved + 需要什么探针】，拿到探针证据后才判 supported / refuted。

运行（无头，约 3 s）：
    blender -b --factory-startup --python <本文件> -- <outdir>
或用插件：
    blender_rt_headless(preload="view", script="exec(open(<本文件>).read())", outdir="D:...")

依赖：Blender 5.x（离屏绘制需 gpu.init()）+ numpy。preload="view" 时用 K.dsh_view_api 出图，否则走内置兜底。
"""
import bpy, json, math, os, struct, sys, time, zlib
import numpy as np

VERSION = 2
SPEC = {
    "seat": {"x": 0.45, "y": 0.45, "z": 0.05, "z0": 0.45},
    "plate": {"x": 0.45, "y": 0.03, "z": 0.50},
    "joint_y": 0.225,
    "gt": {"hypothesis": "insert", "tenon_len": 0.060, "dy": 0.010},
    "tol_ext": 220,
    "tol_probe_m": 0.010,          # 探针（深度规）容差：10 mm
}
VIEW_EXT = {"from": [-2.60, 0.00, 0.78], "look_at": [0.0, 0.16, 0.66], "width": 320, "height": 200,
            "lens": 50, "background": False, "overlays": False}
VIEW_EXT2 = {"from": [1.20, 1.20, 0.35], "look_at": [0.0, 0.28, 0.45], "width": 320, "height": 200,
             "lens": 50, "background": False, "overlays": False}
# 探针视角必须在 -y 侧：榫头朝 -y 伸进座椅内部，从 +y 看会被靠背板挡住（第一版就踩了这个）
VIEW_PROBE = {"from": [0.75, -1.45, 0.31], "look_at": [0.0, 0.20, 0.465], "width": 320, "height": 200,
              "lens": 50, "background": False, "overlays": False}
OCC = {"size": [0.06, 0.30, 0.30], "center": [0.0, 0.26, 0.47]}
# 探针（非破坏性拆解）：拆掉遮挡套 + 隐藏座椅 → 露出榫区；装配回去即可复原
PROBE_HIDE = ["COMP_Seat", "OCC_Sleeve"]
PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])

RANGES = {
    "attach": {"dy": (0.000, 0.030), "tilt": (-2.0, 6.0), "dz": (-0.020, 0.020), "tenon_len": (0.000, 0.000)},
    "insert": {"dy": (0.000, 0.030), "tilt": (-2.0, 6.0), "dz": (-0.020, 0.020), "tenon_len": (0.030, 0.100)},
}
FORBIDDEN = ["boolean_union", "weld", "apply_transform"]
UNIDENTIFIABLE = ["tenon_len"]


def clean_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)
    bpy.context.view_layer.update()


def make_box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    ob = bpy.context.object
    ob.name = name
    ob.scale = size
    ob.select_set(False)
    return ob


def set_tenon(length, inner_y):
    """榫从板的内表面 inner_y 往 -y 长出 length（length=0 → 没有榫）"""
    ob = bpy.data.objects.get("HID_Tenon")
    if length <= 1e-6:
        if ob:
            ob.hide_render = True
            ob.hide_viewport = True
        return None
    if ob is None:
        ob = make_box("HID_Tenon", (0.10, 1.0, 0.06), (0, 0, 0))
    ob.hide_render = False
    ob.hide_viewport = False
    L = max(length, 1e-4)
    ob.scale = (0.10, L, 0.06)
    ob.location = (0.0, inner_y - L / 2.0, 0.465)
    bpy.context.view_layer.update()
    return ob


def place_plate(plate_back_y_face, tilt_deg, dz):
    p = SPEC["plate"]
    plate = bpy.data.objects["COMP_Backrest"]
    y_c = plate_back_y_face - p["y"] / 2.0
    z_c = SPEC["seat"]["z0"] + p["z"] / 2.0 + dz
    plate.location = (0.0, y_c, z_c)
    plate.rotation_euler = (math.radians(tilt_deg), 0.0, 0.0)
    bpy.context.view_layer.update()
    return y_c, z_c


def build_scene(hypothesis, tenon_len, dy, tilt=0.0, dz=0.0):
    if bpy.data.objects.get("COMP_Seat") is None:
        s = SPEC["seat"]
        make_box("COMP_Seat", (s["x"], s["y"], s["z"]), (0, 0, s["z0"] + s["z"] / 2))
    if bpy.data.objects.get("OCC_Sleeve") is None:
        make_box("OCC_Sleeve", OCC["size"], OCC["center"])
    if bpy.data.objects.get("COMP_Backrest") is None:
        p = SPEC["plate"]
        make_box("COMP_Backrest", (p["x"], p["y"], p["z"]), (0, 0.24, 0.70))
    inner_y = SPEC["joint_y"] + dy                     # 板的内表面
    set_tenon(tenon_len, inner_y)
    place_plate(inner_y + SPEC["plate"]["y"], tilt, dz)
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()


def show_objects(names, visible):
    for n in names:
        ob = bpy.data.objects.get(n)
        if ob:
            ob.hide_render = not visible
            ob.hide_viewport = not visible
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()


def probe_tenon_len():
    """探针（深度规）：拆掉遮挡套与座椅后，量靠背板内表面往 -y 伸出多少米。
    这是"非破坏性拆解 + 测量"，与出图无关，因此**不受姿态估计误差影响**。"""
    from mathutils import Vector
    plate = bpy.data.objects.get("COMP_Backrest")
    if plate is None:
        return 0.0
    inner_y = plate.location.y - SPEC["plate"]["y"] / 2.0
    depth = None
    for ob in bpy.data.objects:
        if ob.type != "MESH" or ob.name in ("COMP_Backrest", "COMP_Seat", "OCC_Sleeve"):
            continue
        if ob.hide_render or ob.hide_viewport:            # 藏起来的（没有榫）不算
            continue
        ys = [(ob.matrix_world @ Vector(c)).y for c in ob.bound_box]
        m = min(ys)
        if depth is None or m < depth:
            depth = m
    if depth is None:
        return 0.0
    return round(max(0.0, inner_y - depth), 4)


def capture(spec, path):
    K = sys.modules.get("dsh_rt_kernel")
    api = getattr(K, "dsh_view_api", None) if K else None
    s = dict(spec); s["path"] = path; s["mode"] = "viewport"
    info = json.loads(api["capture"](json.dumps(s))) if api is not None else _fallback_capture(s)
    if not info.get("ok"):
        raise RuntimeError("capture 失败: " + str(info))
    return path


def _fallback_capture(s):
    import gpu
    from mathutils import Vector, Matrix
    try:
        gpu.init()
    except Exception:
        pass
    area = None; win = None
    for w in bpy.context.window_manager.windows:
        for a in (w.screen.areas if w.screen else []):
            if a.type == "VIEW_3D":
                win, area = w, a; break
        if area: break
    if area is None:
        raise RuntimeError("找不到 VIEW_3D（兜底路径需要 3D 视口）")
    reg = next(r for r in area.regions if r.type == "WINDOW")
    space = area.spaces.active
    frm = Vector(s["from"]); look = Vector(s["look_at"])
    d = (look - frm).normalized()
    view = (Matrix.Translation(frm) @ d.to_track_quat("-Z", "Y").to_matrix().to_4x4()).inverted()
    W = float(s["width"]); H = float(s["height"])
    hx = 36.0 / 2.0; hy = hx * (H / W)
    proj = Matrix(((s.get("lens", 50.0) / hx, 0, 0, 0), (0, s.get("lens", 50.0) / hy, 0, 0),
                   (0, 0, -1.0002, -0.20002), (0, 0, -1, 0)))
    off = gpu.types.GPUOffScreen(int(W), int(H))
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.05, 0.05, 0.05, 1.0), depth=1.0)
            off.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, reg, view, proj,
                            do_color_management=True, draw_background=False)
            rgba = bytes(memoryview(off.texture_color.read()))
    finally:
        off.free()
    _write_png(s["path"], int(W), int(H), rgba)
    return {"ok": True, "mode": "fallback", "path": s["path"]}


def _chunk(t, body):
    return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 4294967295)


def _write_png(path, w, h, rgba):
    raw = bytearray(); stride = w * 4
    for y in range(h - 1, -1, -1):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]
    png = (PNG_MAGIC + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def read_mask(path, thresh=0.16):
    d = open(path, "rb").read()
    pos = 8; idat = b""; w = h = None
    while pos < len(d):
        ln = struct.unpack(">I", d[pos:pos + 4])[0]; typ = d[pos + 4:pos + 8]
        body = d[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, _, ct, _, _, _ = struct.unpack(">IIBBBBB", body)
            assert ct == 6
        elif typ == b"IDAT":
            idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat); stride = w * 4
    out = np.zeros((h, w), dtype=bool)
    for y in range(h):
        base = y * (stride + 1)
        assert raw[base] == 0
        px = np.frombuffer(raw[base + 1: base + 1 + stride], dtype=np.uint8).reshape(w, 4)
        out[y] = (px[:, :3].mean(axis=1).astype(np.float32) / 255.0) > thresh
    return out


def diff_px(a, b):
    return int(np.logical_xor(a, b).sum())


def grid(rng, ns=(5, 3, 3), tenon_n=4):
    dys = np.linspace(rng["dy"][0], rng["dy"][1], ns[0])
    tls = np.linspace(rng["tilt"][0], rng["tilt"][1], ns[1])
    dzs = np.linspace(rng["dz"][0], rng["dz"][1], ns[2])
    tns = np.linspace(rng["tenon_len"][0], rng["tenon_len"][1], tenon_n)
    out = []
    for a in dys:
        for t in tls:
            for z in dzs:
                for n in tns:
                    out.append({"dy": round(float(a), 4), "tilt": round(float(t), 2),
                                "dz": round(float(z), 4), "tenon_len": round(float(n), 4)})
    return out


def run():
    t0 = time.time()
    outdir = None
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        for i, a in enumerate(rest):
            if a == "--out" and i + 1 < len(rest):
                outdir = rest[i + 1]
    if not outdir:
        rest = [a for a in sys.argv[1:] if not a.startswith("-")]
        outdir = rest[-1] if rest else os.path.join(os.path.expanduser("~"), "chair_backrest_out")
    os.makedirs(outdir, exist_ok=True)
    res = {"version": VERSION, "spec": SPEC, "ranges": RANGES, "forbidden": FORBIDDEN,
           "unidentifiable_from_external_views": UNIDENTIFIABLE,
           "views": {"external_side": VIEW_EXT, "external_rear": VIEW_EXT2, "probe": VIEW_PROBE},
           "outdir": outdir}

    clean_scene()
    gt = SPEC["gt"]
    build_scene(gt["hypothesis"], gt["tenon_len"], gt["dy"])
    ext1 = os.path.join(outdir, "gt_external_side.png")
    ext2 = os.path.join(outdir, "gt_external_rear.png")
    capture(VIEW_EXT, ext1)
    capture(VIEW_EXT2, ext2)
    m_gt_ext1 = read_mask(ext1)
    m_gt_ext2 = read_mask(ext2)
    show_objects(PROBE_HIDE, False)
    probe = os.path.join(outdir, "gt_probe_no_seat.png")
    capture(VIEW_PROBE, probe)
    m_gt_probe = read_mask(probe)
    gt_probe_len = probe_tenon_len()          # 真值读数（模拟"拆开后用深度规量一下"）
    show_objects(PROBE_HIDE, True)
    res["evidence"] = {"external_side_px": int(m_gt_ext1.sum()), "external_rear_px": int(m_gt_ext2.sum()),
                       "probe_px": int(m_gt_probe.sum()), "gt_probe_tenon_len_m": gt_probe_len,
                       "files": [os.path.basename(ext1), os.path.basename(ext2), os.path.basename(probe)]}

    hyp = {}
    for name in ("attach", "insert"):
        rng = RANGES[name]
        samples = grid(rng, ns=(4, 2, 2), tenon_n=3)
        best = None
        flat = []
        for p in samples:
            build_scene(name, p["tenon_len"], p["dy"], p["tilt"], p["dz"])
            shot = os.path.join(outdir, "cand_%s.png" % name)
            capture(VIEW_EXT, shot)
            e1 = diff_px(read_mask(shot), m_gt_ext1)
            shot2 = os.path.join(outdir, "cand_%s_rear.png" % name)
            capture(VIEW_EXT2, shot2)
            e2 = diff_px(read_mask(shot2), m_gt_ext2)
            err = e1 + e2
            flat.append({"dy": p["dy"], "tilt": p["tilt"], "dz": p["dz"],
                         "tenon_len": p["tenon_len"], "err_ext": err})
            if best is None or err < best["err_ext"]:
                best = dict(p); best["err_ext"] = err; best["err_side"] = e1; best["err_rear"] = e2
        build_scene(name, best["tenon_len"], best["dy"], best["tilt"], best["dz"])
        show_objects(PROBE_HIDE, False)
        ps = os.path.join(outdir, "best_%s_probe.png" % name)
        capture(VIEW_PROBE, ps)
        best["err_probe"] = diff_px(read_mask(ps), m_gt_probe)
        best["shot_probe"] = ps
        show_objects(PROBE_HIDE, True)
        ident = {}
        for param in ("dy", "tilt", "dz", "tenon_len"):
            byv = {}
            for r in flat:
                byv.setdefault(r[param], []).append(r["err_ext"])
            mins = {k: min(v) for k, v in sorted(byv.items())}
            ident[param] = {"min_err_by_value": mins,
                            "range": max(mins.values()) - min(mins.values())}
        # ---- 阶段 B（探针）：外部已钉住外形姿态，探针只回答"内部"这一个问题
        #      => 固定阶段 A 的最优姿态，只在该假设的榫长范围内搜索（新证据 → 重新辨识）
        best_p = None
        tns = np.linspace(rng["tenon_len"][0], rng["tenon_len"][1], 5) if rng["tenon_len"][1] > rng["tenon_len"][0] else [rng["tenon_len"][0]]
        for tl in tns:
            build_scene(name, float(tl), best["dy"], best["tilt"], best["dz"])
            show_objects(PROBE_HIDE, False)
            measured = probe_tenon_len()                       # 深度规读数
            if best_p is None or abs(measured - gt_probe_len) < abs(best_p["measured_tenon_len"] - gt_probe_len):
                shotp = os.path.join(outdir, "probe_cand_%s.png" % name)
                capture(VIEW_PROBE, shotp)
                best_p = {"dy": best["dy"], "tilt": best["tilt"], "dz": best["dz"],
                          "tenon_len": round(float(tl), 4), "measured_tenon_len": measured,
                          "err_probe_m": round(abs(measured - gt_probe_len), 4), "probe_shot": shotp}
            show_objects(PROBE_HIDE, True)
        hyp[name] = {"range": rng, "best": best, "candidates_tried": len(samples),
                     "identifiability": ident, "probe_shot": ps,
                     "best_probe": best_p, "probe_candidates_tried": len(list(tns))}

    for name, h in hyp.items():
        b = h["best"]
        if b["err_ext"] > SPEC["tol_ext"]:
            h["verdict_ext"] = "refuted"
            h["why_ext"] = "外部证据下所有参数都达不到容差（best=%d > %d）" % (b["err_ext"], SPEC["tol_ext"])
        else:
            h["verdict_ext"] = "unresolved"
            h["why_ext"] = ("外部证据能满足（best=%d <= %d），但 %s 在外部视图下不可辨识 → 需要探针"
                            % (b["err_ext"], SPEC["tol_ext"], ", ".join(UNIDENTIFIABLE)))
        bp = h["best_probe"]
        h["verdict_probe"] = "supported" if bp["err_probe_m"] <= SPEC["tol_probe_m"] else "refuted"
        h["why_probe"] = ("探针（拆解 + 深度规）：读数 %.4f m，与真值 %.4f m 差 %.4f m %s %.4f m"
                          % (bp["measured_tenon_len"], gt_probe_len, bp["err_probe_m"],
                             "<=" if bp["err_probe_m"] <= SPEC["tol_probe_m"] else ">", SPEC["tol_probe_m"]))
        idt = h["identifiability"]
        h["identifiable_params"] = sorted([k for k, v in idt.items() if v["range"] > 5 * SPEC["tol_ext"] / 10])
        h["unidentifiable_params"] = sorted([k for k, v in idt.items() if v["range"] <= 5 * SPEC["tol_ext"] / 10])

    res["hypotheses"] = hyp
    ext_ok = [k for k, v in hyp.items() if v["verdict_ext"] != "refuted"]
    probe_ok = [k for k, v in hyp.items() if v["verdict_probe"] == "supported"]
    res["stage1_external_only"] = {
        "hypotheses_not_refuted": ext_ok,
        "conclusion": ("外部证据不足（" + ", ".join(ext_ok) + " 都能满足）→ 必须报 unresolved")
                      if len(ext_ok) > 1 else "外部证据已足以排除其余假设"}
    res["stage2_with_probe"] = {
        "supported": probe_ok,
        "conclusion": ("探针后唯一支持：" + ", ".join(probe_ok)) if len(probe_ok) == 1
                      else ("探针未能判别：" + ", ".join(sorted(hyp)) if not probe_ok else "多个仍成立")}
    res["gt_matches_supported"] = (SPEC["gt"]["hypothesis"] in probe_ok)
    res["ms"] = int((time.time() - t0) * 1000)
    res["preload_view_api"] = bool(getattr(sys.modules.get("dsh_rt_kernel"), "dsh_view_api", None))

    with open(os.path.join(outdir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    write_report(res, os.path.join(outdir, "results.md"))
    print("HEADLESS " + json.dumps({"ok": True, "stage1": res["stage1_external_only"]["conclusion"],
                                    "stage2": res["stage2_with_probe"]["conclusion"],
                                    "gt_matches_supported": res["gt_matches_supported"],
                                    "err_ext": {k: v["best"]["err_ext"] for k, v in hyp.items()},
                                    "probe_read": {k: v["best_probe"]["measured_tenon_len"] for k, v in hyp.items()},
                                    "probe_truth": gt_probe_len,
                                    "ms": res["ms"], "outdir": outdir}, ensure_ascii=False))


def write_report(res, path):
    L = ["# 椅子靠背接缝：外部证据不足 + 探针判别（自动生成）", "",
         "> 阶段一（只用外部证据）：**" + res["stage1_external_only"]["conclusion"] + "**", "",
         "> 阶段二（加探针证据）：**" + res["stage2_with_probe"]["conclusion"] + "**", "",
         "| 假设 | 允许区间 | 试了 | 最优看板参数 | 外部误差(侧+后) | 探针读数 | 与真值差 | 外部判定 | 探针判定 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for k, v in res["hypotheses"].items():
        r = v["range"]; b = v["best"]
        bp = v["best_probe"]
        L.append("| %s | dy %s~%s · tilt %s~%s deg · dz %s~%s · 榫长 %s~%s | %d | dy=%.3f tilt=%.1f dz=%.3f 榫=%.3f | %d | %.4f m | %.4f m | %s | %s |" % (
            k, r["dy"][0], r["dy"][1], r["tilt"][0], r["tilt"][1], r["dz"][0], r["dz"][1],
            r["tenon_len"][0], r["tenon_len"][1], v["candidates_tried"], b["dy"], b["tilt"], b["dz"],
            b["tenon_len"], b["err_ext"], bp["measured_tenon_len"], bp["err_probe_m"],
            v["verdict_ext"], v["verdict_probe"]))
    L += ["", "## 可辨识性（只用外部证据时，哪些参数是能钉住的）", "",
          "| 假设 | 参数 | 该参数各取值下的最小外部误差 | 极差 | 可辨识? |", "|---|---|---|---|---|"]
    for k, v in res["hypotheses"].items():
        for param, idt in v["identifiability"].items():
            vals = " / ".join("%s:%d" % (kk, vv) for kk, vv in idt["min_err_by_value"].items())
            L.append("| %s | %s | %s | %d | %s |" % (
                k, param, vals, idt["range"], "否（平坦）" if idt["range"] <= 5 * res["spec"]["tol_ext"] / 10 else "是"))
    L += ["", "> 极差 ≈ 0 表示该维度对外部证据**不可辨识**：程序必须说出来，而不是随手取一个值（本示例里是 tenon_len）。", "",
          "## 判定依据（两句人话）", ""]
    for k, v in res["hypotheses"].items():
        L.append("- **%s**：外部 —— %s；探针 —— %s" % (k, v["why_ext"], v["why_probe"]))
    L += ["", "## 证据文件", "",
          "| 文件 | 内容 |", "|---|---|",
          "| gt_external_side.png / gt_external_rear.png | 真值的外部两张（接缝被挡板遮住） |",
          "| gt_probe_no_seat.png | 探针证据：藏掉座椅后看榫区 |"]
    for k in res["hypotheses"]:
        L.append("| best_%s_probe.png | %s 假设最优参数在探针视图下 |" % (k, k))
    L += ["", "## 复现", "",
          "    blender -b --factory-startup --python docs/examples/chair-backrest/run.py -- <outdir>",
          "    # 或 blender_rt_headless(preload='view', script=..., outdir=...)", "",
          "## 结论怎么用", "",
          "- 外部证据不足时不要在 attach / insert 之间随便选：报 unresolved，并列出「需要什么探针」；",
          "- 判别前禁止 boolean_union / weld / apply_transform（会毁掉可回退性）；",
          "- 拿到探针证据后再判 supported / refuted，并把两次判定的证据一起存档。"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    run()
