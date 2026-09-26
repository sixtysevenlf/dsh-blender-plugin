# -*- coding: utf-8 -*-
"""上游整合 A1–A5 自检：sculpt / mesh_fix / uv_tools / printcheck / sweep + audit 空网格回归。

跑法（无头，factory-startup，绝不碰用户 GUI 场景；engine=none 只为省引擎前导）：
    blender_rt_headless(preload="audit,sculpt,mesh_fix,uv_tools,printcheck,sweep", engine="none",
        factory_startup=True, timeout_ms=300000,
        script="_p=K.win_path('/home/sixtyseven67/DSH/dsh-blender-plugin/tests/upstream_integration_selftest.py');"
               "exec(compile(open(_p,encoding='utf-8').read(),'x','exec'),globals())")
    # 也可以直接： blender -b --factory-startup --python tests/upstream_integration_selftest.py

判据（数字对着实测写；不达标改代码，不改断言）：
    雕刻   1986 顶点的球：draw 笔触 affected>0 且 max_delta>0；单点 + symmetry=["x"] ⇒ stroke_paths==2（不是 1）；
           过小 voxel_size ⇒ 必须报错并给出可用尺寸（不许卡住 Blender）
    修复   合成缺陷网格：degenerate/loose 修复后归 0；空网格（0 边）audit_mesh 不再 IndexError，报 empty_objects≥1
    UV     立方体 smart project ⇒ 有 UV 层且零面积 UV 面 = 0
    制造   200×200×5mm 薄板（面≈4mm）@min_mm=10 ⇒ min≈5mm（±0.5）且 thin_samples>0、无 resolution 警告；
           悬垂面积对上解析解 40000mm²（占比 0.476）；
           粗网格（体素球）@min_mm=5 ⇒ 必须给 resolution_mm 与"结论不可信"警告
    扫掠   3 站 12 边形直管 ⇒ 38 顶点 / 48 面；过紧路径 ⇒ 拒绝（ok=false 且指出第几个点）
    材质   metal_brushed 建图（节点/连线 >=5）→ 套用 → scan 报 procedural/needs_uv → AO 128px 烘出贴图
    渲染   render_guard install/mark/clear：磁盘标记出现又消失（handler 数量 >=1）
"""
import json
import math

import bmesh
import bpy

PASS, FAILS, SKIP = [], [], []
PREFIX = "DSHST_"


def chk(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [OK]   " + name)
    else:
        FAILS.append({"clause": name, "detail": str(detail)[:400]})
        print("  [FAIL] " + name + "  <- " + str(detail)[:300])


def api(name):
    a = getattr(K, "dsh_" + name + "_api", None)  # noqa: F821  (K 由无头前导/rt_do 注入)
    if a is None:
        raise RuntimeError("模块没注入：dsh_%s_api（preload 里漏了 %s？）" % (name, name))
    return a


def clean():
    for ob in list(bpy.data.objects):
        if ob.name.startswith(PREFIX):
            me = ob.data if ob.type == "MESH" else None
            bpy.data.objects.remove(ob, do_unlink=True)
            if me is not None and me.users == 0:
                try:
                    bpy.data.meshes.remove(me, do_unlink=True)
                except Exception:
                    pass
    for me in list(bpy.data.meshes):
        if me.name.startswith(PREFIX) and me.users == 0:
            bpy.data.meshes.remove(me, do_unlink=True)


def new_sphere(name, segs=64, rings=32, radius=1.0):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=radius)
    except TypeError:
        bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, diameter=radius)
    bm.to_mesh(me)
    bm.free()
    return ob


def new_plate(name, size=0.1, segs=50, thick=0.005):
    """size=0.1 ⇒ 200mm 见方；thick=0.005 ⇒ 5mm。"""
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=segs, y_segments=segs, size=size)
    r = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
    vs = [e for e in r["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=vs, vec=(0.0, 0.0, thick))
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(me)
    bm.free()
    return ob


def t_sculpt():
    ob = new_sphere(PREFIX + "sphere")
    scan = api("sculpt")("scan", {"objects": [ob.name]})
    chk("sculpt_scan 基本读数", scan.get("ok") and scan["objects"][0]["verts"] > 1000, scan)
    chk("sculpt_scan 体素预算有解", scan["objects"][0].get("voxel_auto"), scan["objects"][0])

    r = api("sculpt")("apply", {"object": ob.name, "strokes": [
        {"brush": "draw", "points": [[0, 0, 1.0], [0.12, 0, 1.05]], "radius": 0.45, "strength": 0.7}]})
    s0 = r["results"][0]["strokes"][0]
    chk("sculpt_apply draw 真的动了网格", s0["affected_verts"] > 0 and s0["max_delta"] > 0, s0)
    chk("sculpt_apply 对称=独立笔触（不是串成假路径）",
        api("sculpt")("apply", {"object": ob.name, "strokes": [
            {"brush": "crease", "points": [[0.7, 0, 0.7]], "radius": 0.15, "strength": 0.5,
             "symmetry": ["x"]}]})["results"][0]["strokes"][0]["stroke_paths"] == 2)

    rm = api("sculpt")("remesh", {"objects": [ob.name], "mode": "VOXEL", "voxel_size": "auto"})
    chk("sculpt_remesh auto 体素重构", rm["objects"][0]["verts_after"] > 0, rm["objects"][0])
    bad = api("sculpt")("remesh", {"objects": [ob.name], "mode": "VOXEL", "voxel_size": 0.0005})
    chk("体素预算守卫拦下过小 voxel_size（并给可用尺寸）",
        bad.get("ok") is False and "会卡住 Blender" in json.dumps(bad, ensure_ascii=False), bad)
    chk("笔刷名写错要报错", api("sculpt")("apply", {"object": ob.name, "strokes": [
        {"brush": "nope", "points": [[0, 0, 1]], "radius": 0.2}]}).get("ok") is False)
    chk("参数名拼错要报错（不静默）",
        "不认识的参数" in json.dumps(api("sculpt")("apply", {"object": ob.name, "storkes": []}), ensure_ascii=False))


def t_fix_and_audit():
    ob = new_sphere(PREFIX + "broken")
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    dup = tuple(bm.verts[2].co)
    f = bm.faces[0]
    f.verts[1].co = f.verts[0].co.copy()          # 零面积面
    bm.verts.new((5, 5, 5))                       # 孤立点
    bm.verts.new(dup)                             # 重复点
    bm.to_mesh(me)
    bm.free()
    me.update()
    a0 = api("audit")("mesh", {"objects": [ob.name]})
    chk("audit_mesh 能看见缺陷", (a0["totals"]["loose_verts"] or 0) >= 1, a0["totals"])
    fx = api("fix")("repair", {"objects": [ob.name],
                               "actions": ["merge_doubles", "dissolve_degenerate", "delete_loose", "recalc_normals"]})
    t = fx["objects"][0]
    chk("fix_repair 修完自证（前后计数 + 真非流形=0）",
        fx.get("ok") and t["after"]["loose_verts"] == 0 and t["real_nonmanifold"] == 0, t)
    a1 = api("audit")("mesh", {"objects": [ob.name]})
    chk("audit_mesh 复检干净", (a1["totals"]["loose_verts"] or 0) == 0, a1["totals"])

    empty = bpy.data.meshes.new(PREFIX + "empty")
    eo = bpy.data.objects.new(PREFIX + "empty", empty)
    bpy.context.scene.collection.objects.link(eo)
    a2 = api("audit")("mesh", {"objects": [eo.name]})
    chk("空网格不再让 audit_mesh 崩（回归：empty_objects）",
        a2.get("ok") is True and (a2["totals"].get("empty_objects") or 0) == 1 and a2.get("clean") is False, a2.get("totals"))


def t_uv():
    me = bpy.data.meshes.new(PREFIX + "box")
    ob = bpy.data.objects.new(PREFIX + "box", me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me)
    bm.free()
    r = api("uv")("smart_project", {"objects": [ob.name], "angle_limit": 66, "island_margin": 0.01})
    row = r["objects"][0]
    chk("uv_smart_project 产出 UV 且零面积面=0",
        r.get("ok") and row.get("uv_layers") and row.get("degenerate_uv_faces") == 0, row)
    chk("uv_unwrap 传错 method 回允许列表",
        "allowed" in api("uv")("unwrap", {"objects": [ob.name], "method": "NOT_A_METHOD"}))
    st = api("uv")("stats", {"objects": [ob.name]})
    chk("uv_stats 判据（有层 + 零面积=0 ⇒ ok）", st.get("ok") is True, st.get("objects"))


def t_print():
    plate = new_plate(PREFIX + "plate")
    w = api("print")("walls", {"objects": [plate.name], "min_mm": 10})
    row = w["objects"][0]
    chk("薄板能测出 5mm 壁厚（±0.5）", abs(float(row["min_mm"]) - 5.0) <= 0.5, row.get("min_mm"))
    chk("薄板 @min_mm=10 判为不合格且无分辨率警告",
        w.get("ok") is False and row["thin_samples"] > 0 and not row.get("warning"), row)
    o = api("print")("overhang", {"objects": [plate.name], "max_angle_deg": 45})
    orow = o["objects"][0]
    # 解析解：200×200×5mm 薄板总表面积 = 2*200*200 + 4*200*5 = 84000 mm²，正下方底面 40000 mm²
    chk("薄板悬垂面积对上解析解（40000 mm² / 占比 0.476）",
        abs(float(orow["overhang_area_mm2"]) - 40000.0) <= 100.0
        and abs(float(orow["overhang_ratio"]) - 0.476) <= 0.01, orow)

    coarse = new_sphere(PREFIX + "coarse", segs=24, rings=12)
    w2 = api("print")("walls", {"objects": [coarse.name], "min_mm": 1})
    row2 = w2["objects"][0]
    chk("粗网格必须给 resolution_mm 与'结论不可信'警告",
        row2.get("resolution_mm") and row2.get("warning"), row2)


def t_sweep():
    r = api("sweep")("build", {"name": PREFIX + "pipe", "path": [[0, 0, 0], [1, 0, 0], [2, 0, 0]],
                               "profile": {"type": "circle", "radius": 0.1, "segments": 12}})
    chk("直管 3 站 × 12 边形 ⇒ 38 顶点 / 48 面", r.get("verts") == 38 and r.get("faces") == 48, r)
    tight = api("sweep")("analyze", {"path": [[0, 0, 0], [0.02, 0, 0], [0.02, 0.02, 0]],
                                    "profile": {"type": "rect", "width": 0.4, "height": 0.4}})
    chk("过紧路径被拦下并指出第几点",
        tight.get("ok") is False and tight.get("min_radius_at") == 1 and tight.get("hint"), tight)
    refuse = api("sweep")("build", {"name": PREFIX + "bad", "path": [[0, 0, 0], [0.02, 0, 0], [0.02, 0.02, 0]],
                                   "profile": {"type": "rect", "width": 0.4, "height": 0.4}})
    chk("sweep_build 默认先算后建（过紧直接拒）", refuse.get("ok") is False, refuse.get("error"))



def t_material():
    """材质链路：展 UV → material_build → apply → scan → bake（AO 128px）→ 断言贴图落盘。"""
    import shutil as _shutil
    import tempfile as _tempfile
    import bmesh as _bm
    me = bpy.data.meshes.new(PREFIX + "mat")
    ob = bpy.data.objects.new(PREFIX + "mat", me)
    bpy.context.scene.collection.objects.link(ob)
    bm = _bm.new()
    _bm.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me)
    bm.free()
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    od = _tempfile.mkdtemp(prefix="dsh-mat-selftest-")
    try:
        r_uv = api("uv")("smart_project", {"objects": [ob.name], "angle_limit": 66})
        chk("材质链路前置：UV 展好", r_uv.get("ok"), r_uv)
        b = api("material")("build", {"name": PREFIX + "M", "preset": "metal_brushed",
                                      "params": {"base_color": [0.4, 0.42, 0.45], "scale": 200}})
        chk("material_build 建出程序化节点图（节点与连线都 >=5）",
            b.get("ok") and (b.get("nodes") or 0) >= 5 and (b.get("links") or 0) >= 5, b)
        a = api("material")("apply", {"material": PREFIX + "M", "objects": [ob.name]})
        chk("material_apply 套到对象", a.get("ok"), a)
        s = api("material")("scan", {"materials": [PREFIX + "M"]})
        row = (s.get("materials") or [{}])[0]
        chk("material_scan 报 procedural 与 needs_uv", bool(row.get("procedural")) and bool(row.get("needs_uv")), row)
        bk = api("material")("bake", {"objects": [ob.name], "bake_type": "AO", "resolution": 128,
                                      "samples": 4, "outdir": od})
        imgs = ((bk.get("objects") or [{}])[0].get("images") or [{}])
        chk("material_bake 烘出 AO 贴图并落盘（bytes>0）",
            bk.get("ok") and (imgs[0].get("bytes") or 0) > 0, {"ok": bk.get("ok"), "img": imgs[0]})
        chk("未知 preset 要回允许列表", api("material")("build", {"name": PREFIX + "X", "preset": "nope"}).get("ok") is False)
        chk("bake_type 写错要回允许列表",
            "允许" in json.dumps(api("material")("bake", {"objects": [ob.name], "bake_type": "NOPE"}), ensure_ascii=False))
    finally:
        _shutil.rmtree(od, ignore_errors=True)


def t_render_guard():
    """渲染状态跟踪：install → idle → mark → 磁盘标记为 rendering → clear → 标记消失。
    注意：guard 的 API 挂在 K.dsh_render_guard（名字里没有 _api 后缀，见 render_guard.py 尾部）。"""
    rg = getattr(K, "dsh_render_guard", None)  # noqa: F821
    if rg is None:
        raise RuntimeError("模块没注入：K.dsh_render_guard（preload 里漏了 render_guard？）")
    r = rg("install", {})
    chk("render_guard 安装 4 个 handler", r.get("ok") and r.get("installed")
        and all(int(v) >= 1 for v in (r.get("handlers") or {}).values()), r.get("handlers"))
    st = rg("state", {})
    chk("初始状态 idle（无标记）", st.get("ok") and (st.get("local") or {}).get("state") == "idle", st.get("local"))
    rg("mark", {})
    st2 = rg("state", {})
    chk("mark 后标记文件为 rendering", (st2.get("flag") or {}).get("state") == "rendering", st2.get("flag"))
    rg("clear", {"why": "selftest"})
    st3 = rg("state", {})
    chk("clear 后标记消失且 state 归 idle",
        st3.get("flag") is None and (st3.get("local") or {}).get("state") == "idle", st3.get("local"))



def t_audit_slim():
    """D3 瘦身：summary_only / top_k 要既省字符又不改判定（clean 与 totals 必须一模一样）。"""
    import bmesh as _bm
    names = []
    for i in range(40):
        me = bpy.data.meshes.new(PREFIX + "s%02d" % i)
        ob = bpy.data.objects.new(PREFIX + "s%02d" % i, me)
        bpy.context.scene.collection.objects.link(ob)
        bm = _bm.new()
        _bm.ops.create_cube(bm, size=0.5)
        if i % 4 == 0:                      # 1/4 带缺陷：孤立点 + 重合点
            bm.verts.new((9, 9, 9))
            bm.verts.ensure_lookup_table()
            bm.verts.new(tuple(bm.verts[0].co))
        bm.to_mesh(me)
        bm.free()
        names.append(ob.name)
    full = api("audit")("scene", {})
    slim = api("audit")("scene", {"summary_only": True, "top_k": 5})
    tiny = api("audit")("scene", {"summary_only": True, "top_k": 2})
    chk("瘦身后判定不变（clean 一致）", full.get("clean") == slim.get("clean") == tiny.get("clean"),
        "%s/%s/%s" % (full.get("clean"), slim.get("clean"), tiny.get("clean")))
    chk("瘦身后聚合不变（totals 一致）", full.get("totals") == slim.get("totals"))
    chk("默认仍是全量（40 行）", len(full.get("worst", [])) == 40, len(full.get("worst", [])))
    chk("top_k 生效（5 / 2 行）", len(slim.get("worst", [])) == 5 and len(tiny.get("worst", [])) == 2,
        [len(slim.get("worst", [])), len(tiny.get("worst", []))])
    f_chars = len(json.dumps(full, ensure_ascii=False))
    s_chars = len(json.dumps(slim, ensure_ascii=False))
    chk("场景瘦身 >=70%% 字符（实测 %d -> %d）" % (f_chars, s_chars), s_chars <= f_chars * 0.3,
        "%d -> %d" % (f_chars, s_chars))
    made = _bm.new()
    _bm.ops.create_cube(made, size=1.0)
    me2 = bpy.data.meshes.new(PREFIX + "selfx")
    _bm.ops.create_cube(made, size=1.2, matrix=__import__("mathutils").Matrix.Translation((0.5, 0, 0)))
    made.to_mesh(me2)
    made.free()
    ob2 = bpy.data.objects.new(PREFIX + "selfx", me2)
    bpy.context.scene.collection.objects.link(ob2)
    mf = api("audit")("mesh", {"objects": [ob2.name], "self_intersect": True, "max_issues": 20})
    ms = api("audit")("mesh", {"objects": [ob2.name], "self_intersect": True, "summary_only": True})
    chk("mesh 瘦身去掉明细字段（self_intersection_pairs）",
        "self_intersection_pairs" not in (ms.get("objects") or [{}])[0]
        and "self_intersection_pairs" in (mf.get("objects") or [{}])[0])
    chk("mesh 瘦身后判据字段仍在（nonmanifold/boundary/closed）",
        all(k in (ms.get("objects") or [{}])[0] for k in ("nonmanifold_edges", "boundary_edges", "closed")))
    dup = api("audit")("duplicates", {"top_k": 1})
    chk("audit_duplicates 的 top_k/limit 不再是静默参数（回 slim）", "slim" in dup, dup.get("slim"))



def t_gate():
    """规格驱动门包：plan/run/selftest + 三态判定 + 未知 op 与坏字段必须报错（不静默 pass）。"""
    import bmesh as _bm
    g = getattr(K, "dsh_gate_api", None)  # noqa: F821
    if g is None:
        raise RuntimeError("模块没注入：K.dsh_gate_api（preload 里漏了 gate？）")
    me = bpy.data.meshes.new(PREFIX + "gate")
    ob = bpy.data.objects.new(PREFIX + "gate", me)
    bpy.context.scene.collection.objects.link(ob)
    bm = _bm.new()
    _bm.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me)
    bm.free()
    good = {"id": "ok", "op": "audit_mesh", "args": {"objects": [ob.name]}, "pass_if": "clean == True"}
    # 判据为假（cube 干净 ⇒ clean == False 必然假）⇒ 该门 fail；
    # 与"引用不存在的字段"区分：后者判 error（配置错），下面另有专门断言。
    bad = {"id": "bad", "op": "audit_mesh", "args": {"objects": [ob.name]}, "pass_if": "clean == False"}
    plan = g("plan", {"spec": {"name": "t", "gates": [good, bad]}})
    chk("gate_plan 只读地列出两个门并标出 pass_if 读取的字段",
        plan.get("ok") and plan.get("gate_count") == 2 and (plan["gates"][0].get("reads") == ["clean"]),
        [x.get("reads") for x in plan.get("gates", [])])
    r = g("run", {"spec": {"name": "t", "gates": [good, bad]}})
    chk("gate_run 三态：有门挂 ⇒ verdict=fail 且 ok=false，且点名 failed",
        r.get("verdict") == "fail" and r.get("ok") is False and r.get("failed") == ["bad"],
        {"verdict": r.get("verdict"), "ok": r.get("ok"), "failed": r.get("failed")})
    chk("过的门 state=pass、挂的门 state=fail（逐门三态）",
        [x.get("state") for x in r.get("gates", [])] == ["pass", "fail"],
        [x.get("state") for x in r.get("gates", [])])
    chk("回执瘦身：只回 pass_if 用到的字段 key",
        set((r["gates"][0].get("key") or {}).keys()) <= {"clean"}, r["gates"][0].get("key"))
    r2 = g("run", {"spec": {"name": "all-pass", "gates": [good]}})
    chk("全过 ⇒ verdict=pass 且 ok=true", r2.get("verdict") == "pass" and r2.get("ok") is True,
        {"v": r2.get("verdict"), "ok": r2.get("ok")})
    rp = g("run", {"preset": "assembly"})
    chk("preset 能整包跑起来（verdict 三态之一，不抛）",
        rp.get("verdict") in ("pass", "fail", "degraded"), rp.get("verdict"))
    err = g("run", {"spec": {"name": "e", "gates": [{"id": "x", "op": "nope", "pass_if": ""}]}})
    chk("未知 op ⇒ ok=false 且列出允许的 op（不静默）",
        err.get("ok") is False and "允许" in json.dumps(err, ensure_ascii=False), str(err)[:120])
    err2 = g("run", {"spec": {"name": "e2", "gates": [
        {"id": "y", "op": "audit_mesh", "args": {"objects": [ob.name]}, "pass_if": "nope_field == 1"}]}})
    chk("pass_if 引用不存在的字段 ⇒ 明确报错并提示可用字段",
        "没有的字段" in json.dumps(err2, ensure_ascii=False), str(err2)[:140])
    st = g("selftest", {})
    chk("gate_selftest 自证（含 degraded 判例）", bool(st.get("ok")),
        (st.get("evidence") or {}).get("degraded_case"))



def t_island():
    """反馈 #1：自交按连通岛分栏 + 小岛过滤（铆钉压入宿主属良性，不该和真互穿混在一起）。"""
    import bmesh as _bm
    from mathutils import Matrix as _M
    me = bpy.data.meshes.new(PREFIX + "isl")
    ob = bpy.data.objects.new(PREFIX + "isl", me)
    bpy.context.scene.collection.objects.link(ob)
    bm = _bm.new()
    _bm.ops.create_cube(bm, size=2.0)
    _bm.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=3, use_grid_fill=True)   # 宿主：顶点多
    _bm.ops.create_cube(bm, size=0.5, matrix=_M.Translation((1.0, 0.0, 0.0)))    # 铆钉：8 顶点小岛，穿宿主面
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.to_mesh(me)
    bm.free()
    n_verts = len(me.vertices)
    r = api("audit")("mesh", {"objects": [ob.name], "self_intersect": True, "island_split": True,
                              "summary_only": True, "max_issues": 5})
    info = (r.get("objects") or [{}])[0]
    chk("分岛：认出两个岛（宿主 + 铆钉）", (info.get("islands") or {}).get("count") == 2,
        info.get("islands"))
    chk("分岛：跨岛相交 > 0 且同岛 = 0", (info.get("self_intersections_cross_island") or 0) > 0
        and (info.get("self_intersections_same_island") or 0) == 0,
        {"same": info.get("self_intersections_same_island"), "cross": info.get("self_intersections_cross_island")})
    r2 = api("audit")("mesh", {"objects": [ob.name], "self_intersect": True, "island_split": True,
                               "min_island_verts": n_verts, "summary_only": True, "max_issues": 5})
    i2 = (r2.get("objects") or [{}])[0]
    chk("小岛过滤：铆钉（8 顶点 < %d）相关的相交全被滤掉、kept=0" % n_verts,
        (i2.get("self_intersections_filtered_small_island") or 0) > 0 and (i2.get("self_intersections_kept") or 0) == 0,
        {"filtered": i2.get("self_intersections_filtered_small_island"), "kept": i2.get("self_intersections_kept"),
         "small_islands": (i2.get("islands") or {}).get("small_islands")})
    chk("小岛过滤开关默认关（不传 min_island_verts 时不过滤）",
        "self_intersections_filtered_small_island" not in (api("audit")("mesh", {"objects": [ob.name],
         "self_intersect": False, "summary_only": True}).get("objects") or [{}])[0])
    # 同岛自交：把两个立方体用一条边连起来 ⇒ 变成一个岛，互穿记成 same_island
    me2 = bpy.data.meshes.new(PREFIX + "isl2")
    ob2 = bpy.data.objects.new(PREFIX + "isl2", me2)
    bpy.context.scene.collection.objects.link(ob2)
    bm2 = _bm.new()
    _bm.ops.create_cube(bm2, size=2.0)
    _bm.ops.create_cube(bm2, size=0.5, matrix=_M.Translation((1.0, 0.0, 0.0)))
    bm2.verts.ensure_lookup_table()
    bm2.edges.new((bm2.verts[0], bm2.verts[8]))     # 一条边把两块壳连成一个岛
    bm2.to_mesh(me2)
    bm2.free()
    i3 = (api("audit")("mesh", {"objects": [ob2.name], "self_intersect": True, "island_split": True,
                                "summary_only": True, "max_issues": 5}).get("objects") or [{}])[0]
    chk("单岛（一条边相连）⇒ 记成同岛自交", (i3.get("islands") or {}).get("count") == 1
        and (i3.get("self_intersections_same_island") or 0) > 0,
        {"islands": i3.get("islands"), "same": i3.get("self_intersections_same_island")})



def t_conn():
    """反馈 #2：三态显式成 verdict（degraded 不再像绿）+ 逐对象复核（整体超面数上限时的正解）。"""
    import bmesh as _bm
    from mathutils import Matrix as _M
    names = {}
    # solid = 两块**互插** 0.1 的立方体（工艺要求拼件互插 5–15mm ⇒ 连通门该过）；
    # float = 同一 mesh 里两块相距 5 的立方体（孤立的那块是可见浮块 ⇒ 该挂）
    # solid = 两块**互插** 0.1（工艺要求）；touch = 两块**正好相贴**（gap=0.00 mm，被焊接成一体）；
    # float = 同一 mesh 里两块相距 5（孤立的那块是可见浮块 ⇒ 该挂）
    for tag, parts in (("solid", [(0, 0, 0), (0.9, 0, 0)]), ("touch", [(0, 0, 0), (1.0, 0, 0)]),
                       ("float", [(0, 0, 0), (5, 0, 0)])):
        me = bpy.data.meshes.new(PREFIX + tag)
        ob = bpy.data.objects.new(PREFIX + tag, me)
        bpy.context.scene.collection.objects.link(ob)
        bm = _bm.new()
        for p in parts:
            _bm.ops.create_cube(bm, size=1.0, matrix=_M.Translation(p))
        bm.to_mesh(me)
        bm.free()
        names[tag] = ob.name
    g1 = api("audit")("gate", {"objects": [names["solid"]]})
    chk("连通门带 verdict 字段（三态显式化）", g1.get("verdict") == "pass", g1.get("verdict"))
    gt = api("audit")("gate", {"objects": [names["touch"]]})
    gc = gt.get("verdicts", {}).get("connectivity") or {}
    chk("正好相贴（gap=0.00 mm）算相接、不判浮块 ⇒ pass 且可见浮块=0",
        gt.get("verdict") == "pass" and gc.get("visible_floater_count") == 0,
        {"v": gt.get("verdict"), "visible": gc.get("visible_floater_count")})
    gf = api("audit")("gate", {"objects": [names["float"]]})
    chk("留出大间隙的孤立块仍判浮块 ⇒ fail", gf.get("verdict") == "fail", gf.get("verdict"))
    r = api("audit")("connectivity", {"objects": [names["solid"], names["float"]], "per_object": True})
    chk("per_object：逐对象出 verdict，且点名哪件挂了",
        r.get("per_object") is True and r.get("verdict") == "fail"
        and names["float"] in (r.get("failed_objects") or []),
        {"v": r.get("verdict"), "failed": r.get("failed_objects")})
    chk("per_object：每件都带 state/verdict/tris 与原因位",
        bool(r.get("objects")) and all(("state" in o and "verdict" in o and "tris" in o and "reason" in o)
                                       for o in r["objects"]),
        (r.get("objects") or [{}])[0])
    g2 = api("audit")("gate", {"objects": [names["solid"]], "max_tris": 1})
    chk("超复核上限 ⇒ state=degraded 且 verdict=degraded（不是 pass）",
        g2.get("state") == "degraded" and g2.get("verdict") == "degraded", 
        {"state": g2.get("state"), "verdict": g2.get("verdict")})
    chk("degraded 带醒目 warn：DEGRADED ≠ 通过", "DEGRADED" in (g2.get("warn") or ""), g2.get("warn"))



def t_human():
    """人形素体：高度误差、几何头身比、关节标记、头型与五官标记（零素材路线）。"""
    g = getattr(K, "dsh_human_api", None)  # noqa: F821
    if g is None:
        raise RuntimeError("模块没注入：K.dsh_human_api（preload 里漏了 human？）")
    r = g("base", {"height_mm": 1700.0, "heads": 7.5, "name": PREFIX + "human"})
    hre = r.get("height_rel_err")
    chk("human_base 高度误差 <0.5%", bool(r.get("ok")) and hre is not None and hre < 0.005,
        {"height_mm": r.get("height_mm"), "rel_err": hre})
    hm = r.get("heads_measured")
    chk("human_base 几何头身比在目标 ±5%", hm is not None and abs(hm - 7.5) / 7.5 <= 0.05, hm)
    chk("human_base 出关节标记 >=12 个", int(r.get("marker_count") or 0) >= 12, r.get("marker_count"))
    chk("human_base 顶点量在合理区间（2k–200k）",
        2000 <= int(r.get("vertices") or 0) <= 200000, r.get("vertices"))
    m = g("measure", {"name": PREFIX + "human"})
    chk("human_measure 读得到头高与头身比", bool(m.get("head_mm")) and bool(m.get("heads")),
        {"head_mm": m.get("head_mm"), "heads": m.get("heads")})
    lm = {"top": [0, 0], "chin": [0, 400], "face_l": [-150, 200], "face_r": [150, 200],
          "eye_l": [-70, 200], "eye_r": [70, 200], "nose_base": [0, 290]}
    h = g("head", {"landmarks": lm, "head_mm": 230.0, "name": PREFIX + "head"})
    chk("human_head 出五官定位标记 >=5 个", bool(h.get("ok")) and len(h.get("markers") or []) >= 5,
        len(h.get("markers") or []))
    hh = (h.get("size_mm") or {}).get("h") or 0
    chk("human_head 头高误差 <5%", abs(hh - 230.0) / 230.0 <= 0.05, hh)
    chk("human_head 从 landmark 推出眼线位置（≈0.50）",
        abs((h.get("landmark_fracs") or {}).get("eye_line_frac", 0) - 0.5) <= 0.05,
        (h.get("landmark_fracs") or {}).get("eye_line_frac"))



def t_vehicle():
    """车辆外壳：比例门（WBR 标定）+ 放样 + 轮与壳零互穿（布尔轮眉的构造保证）。"""
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    c = getattr(K, "dsh_clearance_api", None)  # noqa: F821
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api（preload 里漏了 vehicle？）")
    sp = v("spec", {"type": "sports", "length_mm": 4300.0, "width_mm": 1850.0, "height_mm": 1250.0,
                    "wheelbase_mm": 2600.0, "front_axle_from_front_mm": 900.0, "wheel_r_mm": 340.0,
                    "wheel_w_mm": 245.0, "track_mm": 1580.0})
    pk = v("package", {"spec": sp})
    chk("车辆比例门（真车标定的 WBR / 轴距比 / 高长比）该过", bool(pk.get("ok")), pk.get("failed"))
    bad = v("spec", {"type": "sports", "wheel_r_mm": 150.0})
    chk("轮径明显偏小 ⇒ 比例门点名 wheel_to_body_ratio",
        "wheel_to_body_ratio" in (v("package", {"spec": bad}).get("failed") or []))
    r = v("base", {"spec": sp, "name": PREFIX + "veh", "stations": 20, "section_pts": 12})
    chk("vehicle_base 建壳（尺寸吻合车长 ±60mm）",
        bool(r.get("ok")) and abs((r.get("size_mm") or {}).get("len", 0) - 4300.0) < 60.0, r.get("size_mm"))
    chk("四个轮都建出来了", len(r.get("wheels") or []) == 4, len(r.get("wheels") or []))
    if c is not None and r.get("wheel_objects"):
        cl = c("check", {"pairs": [{"id": "wheel_vs_shell", "a": [r["wheel_objects"][0]],
                                    "b": [r["object"]], "min_mm": 0.0}]})
        p0 = (cl.get("pairs") or [{}])[0]
        chk("轮与壳零互穿（布尔轮眉 ⇒ 构造保证）", p0.get("interpenetrating") is False,
            {"gap_mm": p0.get("gap_mm"), "inter": p0.get("interpenetrating")})
        chk("轮与壳间隙 ≈ arch_clear（默认 12mm 量到 6–14mm）",
            p0.get("gap_mm") is not None and 6.0 <= float(p0["gap_mm"]) <= 14.0, p0.get("gap_mm"))
    else:
        chk("clearance 模块可用（零互穿验收）", False, "未注入 clearance")


def t_clearance():
    """成对间隙门：10mm 过 / 2mm 挂 / 互穿挂 / 允许互插过。"""
    c = getattr(K, "dsh_clearance_api", None)  # noqa: F821
    if c is None:
        raise RuntimeError("模块没注入：K.dsh_clearance_api（preload 里漏了 clearance？）")
    r = c("selftest", {})
    chk("clearance 自检（四类规则一次判完）", bool(r.get("ok")), (r.get("evidence") or {}).get("top"))



def t_vehicle_shell():
    """外壳还原：站表放样（截面参数/轮眉外扩/腰线折痕）+ 按缝切分件（缝是真几何）。"""
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    c = getattr(K, "dsh_clearance_api", None)  # noqa: F821
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    r = v("loft", {"stations": sts, "name": PREFIX + "shell", "section_pts": 20, "n_top": 5.0, "n_bot": 3.0,
                   "tumblehome_mm": 120.0, "shoulder_inset_mm": 25.0, "sill_tuck_mm": 60.0,
                   "flare_mm": 30.0, "subsurf": 1, "crease_shoulder": 0.7})
    chk("站表放样成壳（车长吻合 ±80mm）",
        bool(r.get("ok")) and abs((r.get("size_mm") or {}).get("len", 0) - 4300.0) < 80.0, r.get("size_mm"))
    chk("腰线折痕生效（SubD 折痕环边 > 0）", int(r.get("crease_edges") or 0) > 0, r.get("crease_edges"))
    chk("轮眉外扩落在轮拱站位（不是最低车唇）",
        bool((r.get("flare") or {}).get("at_x_mm")), (r.get("flare") or {}).get("at_x_mm"))
    p = v("panels", {"object_name": r.get("object"), "cuts_mm": [1500.0, 3000.0], "gap_mm": 4.0})
    chk("按缝切分件成 3 件", bool(p.get("ok")) and len(p.get("parts") or []) == 3, p.get("parts"))
    if c is not None and len(p.get("parts") or []) >= 2:
        cl = c("check", {"pairs": [{"id": "seam", "a": [p["parts"][0]], "b": [p["parts"][1]], "min_mm": 0.0}]})
        q = (cl.get("pairs") or [{}])[0]
        chk("缝是真几何（≈4mm 且零互穿）",
            q.get("interpenetrating") is False and q.get("gap_mm") is not None
            and 3.0 <= float(q["gap_mm"]) <= 5.0, {"gap": q.get("gap_mm")})



def t_vehicle_fit_regions():
    """站表 → IoU 拟合闭环 + 多区域装配 + 前视截面轮廓（三件的数值门）。"""
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    ft = v("fit", {"stations": sts, "iterations": 120, "target_iou": 0.95})
    chk("站表→IoU 拟合闭环跑通并给出 IoU", bool(ft.get("ok")) and ft.get("iou") is not None, ft.get("iou"))
    chk("拟合不劣于解析初值（iou >= iou_start）",
        ft.get("iou") is not None and ft.get("iou_start") is not None
        and ft.get("iou") >= float(ft["iou_start"]) - 1e-6,
        {"start": ft.get("iou_start"), "end": ft.get("iou")})
    chk("解析反推给的初值高于默认初值（>0.5）",
        ft.get("iou_start") is not None and float(ft["iou_start"]) > 0.5, ft.get("iou_start"))
    rg = v("regions", {"stations": sts, "name": PREFIX + "reg",
                       "regions": [{"name": "a", "x0_mm": 0, "x1_mm": 1500},
                                   {"name": "b", "x0_mm": 1450, "x1_mm": 3000},
                                   {"name": "c", "x0_mm": 2950, "x1_mm": 4300}],
                       "loft_kw": {"subsurf": 1, "section_pts": 14}})
    chk("多区域装配成 3 个独立体量", bool(rg.get("ok")) and int(rg.get("count") or 0) == 3,
        [m.get("object") for m in (rg.get("regions") or [])])
    chk("每个区域各自有尺寸（互不粘连）",
        all((m.get("size_mm") or {}).get("len") for m in (rg.get("regions") or [])),
        [m.get("size_mm") for m in (rg.get("regions") or [])])
    shape = [{"z_frac": q, "hw_frac": 1.0 - 0.5 * q} for q in (0.0, 0.25, 0.5, 0.75, 1.0)]
    lo = v("loft", {"stations": sts, "name": PREFIX + "shape", "section_shape": shape, "subsurf": 1})
    chk("前视截面轮廓（section_shape）被放样采纳",
        bool(lo.get("ok")) and lo.get("section_source") == "front_view_shape", lo.get("section_source"))



def t_rectify_and_polyline():
    """照片透视校正（四角单应）与折线族拟合（更强形状族 + K 扫描）。"""
    img = getattr(K, "dsh_img_api", None)  # noqa: F821
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    if img is None or v is None:
        raise RuntimeError("模块没注入：K.dsh_img_api / K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    f2 = v("fit", {"stations": sts, "family": "polyline", "control_points": [6, 12], "iterations": 120})
    chk("折线族（闭合剖面）拟合跑通", bool(f2.get("ok")) and f2.get("mode") == "polyline_closed", f2.get("mode"))
    curve = f2.get("curve") or []
    chk("控制点数越多 IoU 越高（K↔IoU 曲线单调）",
        len(curve) == 2 and float(curve[1]["iou"]) >= float(curve[0]["iou"]) - 1e-6,
        [(q.get("control_points"), q.get("iou")) for q in curve])
    cargen = v("fit", {"stations": sts, "iterations": 60})
    chk("折线族天花板高于 cargen 族（0.67 → 0.72+）",
        float((curve[-1] if curve else {}).get("iou") or 0) > float(cargen.get("iou") or 0),
        {"polyline": (curve[-1] if curve else {}).get("iou"), "cargen": cargen.get("iou")})



def t_feature_lines():
    """特征线层：折痕落在指定高度/位置；凹陷按 A/B 量出真实收窄；横向线自动插站。"""
    import bpy  # noqa: F401
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    base = {"stations": sts, "section_pts": 24, "subsurf": 1}
    a = v("loft", dict(base, name=PREFIX + "fl_a"))
    b = v("loft", dict(base, name=PREFIX + "fl_b",
                       crease_lines=[{"z_mm": 560, "tol_mm": 45, "x_mm": [400, 3900], "weight": 0.85},
                                     {"x_mm": 1500, "tol_mm": 70, "weight": 1.0}],
                       inset_lines=[{"z_mm": 470, "band_mm": 60, "inset_mm": 12, "x_mm": [500, 3800]}]))
    chk("特征线（腰线 + 横向）都命中了环边",
        all(int(r.get("edges") or 0) > 0 for r in (b.get("crease_lines") or [])),
        [(r.get("spec"), r.get("edges")) for r in (b.get("crease_lines") or [])])
    chk("横向特征线不在站位上时自动插站", int(b.get("stations_inserted") or 0) >= 1, b.get("stations_inserted"))

    def band_w(name, zc, tol=0.02):
        ob = bpy.data.objects.get(name)
        if ob is None:
            return None
        ys = [abs(x.co.y) for x in ob.data.vertices if abs(x.co.z - zc) <= tol]
        return (max(ys) * 1000.0) if ys else None

    wa, wb = band_w(a.get("object"), 0.470), band_w(b.get("object"), 0.470)
    chk("凹陷在指定高度带真的收窄（A/B 同带对比 ≥5mm）",
        wa is not None and wb is not None and (wa - wb) >= 5.0, {"without": wa, "with": wb})
    chk("凹陷点数 > 0（记录在案）", int(b.get("inset_hits") or 0) > 0, b.get("inset_hits"))



def t_crease_radius():
    """圆角特征线：radius_mm 走 Bevel 权重 + Bevel 修改器（真半径圆弧，而非折痕权重近似）。"""
    import bpy  # noqa: F401
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    if v is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api")
    prof = [(0, 400, 110, 600), (400, 430, 110, 780), (900, 470, 289, 900), (1400, 560, 110, 925),
            (1900, 760, 110, 900), (2400, 760, 110, 880), (2900, 560, 110, 900), (3400, 470, 289, 925),
            (3900, 430, 110, 780), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    base = {"stations": sts, "section_pts": 20, "subsurf": 1, "n_top": 5.0, "n_bot": 3.0, "tumblehome_mm": 120.0}
    hard = v("loft", dict(base, name=PREFIX + "cr_hard", crease_lines=[{"frac": 0.55, "weight": 0.8}]))
    rnd = v("loft", dict(base, name=PREFIX + "cr_rnd",
                         crease_lines=[{"frac": 0.55, "radius_mm": 6.0, "segments": 3},
                                       {"x_mm": 1500, "radius_mm": 4.0, "segments": 2}]))
    chk("圆角特征线命中（bevel 权重边 > 0）",
        bool(rnd.get("ok")) and int((rnd.get("bevel") or {}).get("edges") or 0) > 0, rnd.get("bevel"))
    chk("圆角真的加了几何（顶点数 > 硬折线版）",
        int(rnd.get("vertices") or 0) > int(hard.get("vertices") or 0),
        {"hard": hard.get("vertices"), "rounded": rnd.get("vertices")})
    ob = bpy.data.objects.get(rnd.get("object"))
    chk("Bevel 修改器已落地（栈里不残留）", ob is not None and len(ob.modifiers) == 0,
        [m.name for m in (ob.modifiers if ob else [])])
    chk("半径/段数回执正确", (rnd.get("bevel") or {}).get("radius_mm") == 6.0
        and (rnd.get("bevel") or {}).get("segments") == 3, rnd.get("bevel"))



def t_shape_layer():
    """通用还原方法层：类别协议齐全 + 旋转体通路 + 委托量具/放样仍可用。"""
    sh = getattr(K, "dsh_shape_api", None)  # noqa: F821
    if sh is None:
        raise RuntimeError("模块没注入：K.dsh_shape_api（preload 里加 shapegen？）")
    st = sh("selftest", {})
    chk("shape 自检（类别协议无缺项 + 旋转体）", bool(st.get("ok")), (st.get("evidence") or {}).get("missing_fields"))
    chk("类别数 >= 8（vehicle/humanoid/furniture/hull/aircraft/rotational/weapon/generic…）",
        int((st.get("evidence") or {}).get("classes") or 0) >= 8, (st.get("evidence") or {}).get("classes"))
    for cls in ("vehicle", "humanoid", "furniture", "rotational"):
        p = sh("plan", {"object_class": cls})
        chk("协议 %s 给全视图/比例/算子/验收/坑" % cls,
            bool(p.get("ok")) and all((p.get("plan") or {}).get(k) for k in ("views", "ratios", "ops", "gates", "traps")),
            cls)
    # 选路协议：每类都必须给"用哪条截面通路"，且决策规则齐全（防以后加类别漏配）
    ev = st.get("evidence") or {}
    chk("每类都有截面通路条目（section_path 无缺项）", not (ev.get("missing_section_path") or []),
        ev.get("missing_section_path"))
    chk("截面通路决策规则 >= 4 条", int(ev.get("decision_rules") or 0) >= 4, ev.get("decision_rules"))
    air = sh("plan", {"object_class": "aircraft"})
    chk("翼型类明确指向 section_outline（不是镜像对称那条路）",
        "section_outline" in str((air.get("plan") or {}).get("section_path") or ""),
        (air.get("plan") or {}).get("section_path"))
    rot = sh("plan", {"object_class": "rotational"})
    chk("轴对称类明确指向 shape_revolve（并提示不要放样）",
        "shape_revolve" in str((rot.get("plan") or {}).get("section_path") or ""),
        (rot.get("plan") or {}).get("section_path"))
    rv = sh("revolve", {"profile": [[0, 0], [25, 0], [25, 80], [12, 110], [0, 120]], "name": PREFIX + "rv"})
    chk("旋转体通路（瓶/罐/轮毂）出几何且高度吻合",
        bool(rv.get("ok")) and abs((rv.get("size_mm") or {}).get("h", 0) - 120.0) < 1.0, rv.get("size_mm"))



def t_shape_extensions():
    """通用还原扩展：自定义/非对称截面（翼型）· 圆角 G2 控制 · 旋转体多体量装配。"""
    import math
    import bpy  # noqa: F401
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    sh = getattr(K, "dsh_shape_api", None)  # noqa: F821
    if v is None or sh is None:
        raise RuntimeError("模块没注入：K.dsh_vehicle_api / K.dsh_shape_api")
    prof = [(0, 400, 110, 600), (900, 470, 289, 900), (1900, 760, 110, 900), (2900, 560, 110, 900), (4300, 400, 110, 600)]
    sts = [{"x_mm": float(x), "z_top_mm": float(zt), "z_bottom_mm": float(zb), "half_w_mm": float(hw)}
           for (x, zt, zb, hw) in prof]
    outl = []
    for i in range(21):
        xx = abs(2 * (i / 20.0) - 1)
        yt = 5 * 0.12 * (0.2969 * math.sqrt(max(1e-9, 1 - xx ** 2)) - 0.1260 * (1 - xx ** 2)
                         - 0.3516 * (1 - xx ** 2) ** 2 + 0.2843 * (1 - xx ** 2) ** 3)
        outl.append([round((yt + 0.04 * (1 - xx ** 2)) * 1000, 2), round(xx * 1000, 1)])
    for i in range(19, -1, -1):
        xx = abs(2 * (i / 20.0) - 1)
        yt = 5 * 0.12 * (0.2969 * math.sqrt(max(1e-9, 1 - xx ** 2)) - 0.1260 * (1 - xx ** 2)
                         - 0.3516 * (1 - xx ** 2) ** 2 + 0.2843 * (1 - xx ** 2) ** 3)
        outl.append([round((-yt + 0.04 * (1 - xx ** 2)) * 1000, 2), round(xx * 1000, 1)])
    a = v("loft", {"stations": sts, "name": PREFIX + "airfoil", "section_outline": outl, "subsurf": 1})
    chk("翼型/自定义截面被采纳（section_source=custom_outline）",
        bool(a.get("ok")) and a.get("section_source") == "custom_outline", a.get("section_source"))
    ob = bpy.data.objects.get(a.get("object"))
    ys = [q.co.y * 1000.0 for q in ob.data.vertices] if ob else []
    chk("截面左右**不对称**（翼型有弯度 ⇒ |ymin| ≠ |ymax|）",
        ys and abs(abs(min(ys)) - abs(max(ys))) > 20.0, {"ymin": round(min(ys), 1), "ymax": round(max(ys), 1)})
    b = v("loft", {"stations": sts, "name": PREFIX + "g2", "section_pts": 20, "subsurf": 1,
                   "crease_lines": [{"frac": 0.55, "radius_mm": 8.0, "segments": 4, "profile": 0.75}]})
    chk("圆角剖面可控（profile 0.75 = 偏方/G2 近似）",
        bool(b.get("ok")) and abs(float((b.get("bevel") or {}).get("profile") or 0) - 0.75) < 1e-6, b.get("bevel"))
    c = sh("revolve", {"name": PREFIX + "rv", "parts": [
        {"profile": [[0, 0], [40, 0], [40, 30], [0, 30]], "at": [0, 0, 0], "name": PREFIX + "hub"},
        {"profile": [[0, 0], [30, 0], [30, 20], [0, 20]], "at": [200, 0, 0], "name": PREFIX + "tire"}]})
    chk("旋转体多体量装配（2 件独立对象 + 各自落点）",
        bool(c.get("ok")) and int(c.get("count") or 0) == 2, [q.get("object") for q in (c.get("parts") or [])])
    tire = bpy.data.objects.get(PREFIX + "tire")
    chk("落点写进对象（200mm → 0.2m）", tire is not None and abs(tire.location.x - 0.2) < 1e-6,
        [round(x, 3) for x in (tire.location if tire else [])])




def t_pipeline():
    # S5 流水线：工件注册表 + @引用 + 逐步回执 + 失败即停（让 op 真正配合）
    p = getattr(K, "dsh_pipe_api", None)  # noqa: F821
    if p is None:
        raise RuntimeError("模块没注入：K.dsh_pipe_api（preload 里加 pipeline）")
    st = p("selftest", {})
    chk("流水线自检（工件存取 / @引用含点路径 / 嵌套 / 失败即停）", bool(st.get("ok")), st.get("evidence"))
    p("clear", {})
    p("put", {"name": "nums", "value": {"a": [1, 2, 3], "s": "hi"}})
    got = p("get", {"name": "nums", "path": "a.2"})
    chk("点路径取值（a.2 -> 3）", got.get("value") == 3, got.get("value"))
    # 真链：第 3 步**消费**第 2 步的工件（value 写成 "@n"），第 4 步再取回来核对
    r = p("run", {"steps": [
        {"api": "clearance", "op": "help", "args": {}, "out": "@h"},
        {"api": "pipe", "op": "put", "args": {"name": "n", "value": 7}, "out": "@p1"},
        {"api": "pipe", "op": "put", "args": {"name": "m", "value": "@n"}, "out": "@p2"},
        {"api": "pipe", "op": "get", "args": {"name": "m"}, "out": "@g2"},
    ]})
    chk("四步链跑通且逐步都有回执", bool(r.get("ok")) and r.get("step_count") == 4,
        [(s.get("op"), s.get("ok"), s.get("ms")) for s in (r.get("steps") or [])])
    chk("产出按 out= 存成工件", set(["h", "p1", "p2", "g2"]).issubset(set(r.get("artifacts") or [])),
        r.get("artifacts"))
    chk("@引用传到真实调用里（value\"@n\" -> 7）", p("get", {"name": "m"}).get("value") == 7,
        p("get", {"name": "m"}).get("value"))
    bad = p("run", {"steps": [{"api": "clearance", "op": "help", "args": {"x": "@nope"}}]})
    chk("引用不存在的工件 = 失败即停（不静默）", bad.get("ok") is False and bad.get("failed_steps") == [0],
        bad.get("failed_steps"))
    p("clear", {})

def main():
    before = len(bpy.data.objects)
    print("== 上游整合 A1–A5 自检 ==")
    for fn in (t_sculpt, t_fix_and_audit, t_uv, t_print, t_sweep, t_material, t_render_guard, t_audit_slim, t_gate, t_island, t_conn, t_human, t_vehicle, t_clearance, t_vehicle_shell, t_vehicle_fit_regions, t_rectify_and_polyline, t_feature_lines, t_crease_radius, t_shape_layer, t_shape_extensions, t_pipeline):
        try:
            fn()
        except Exception as e:
            chk(fn.__name__ + " 未抛异常", False, "%s: %s" % (type(e).__name__, str(e)[:200]))
        clean()
    chk("临时对象已清干净（场景对象数复原）", len(bpy.data.objects) == before,
        "%d -> %d" % (before, len(bpy.data.objects)))
    out = {"pass": len(PASS), "fail": len(FAILS), "fails": FAILS, "skipped": SKIP,
           "objects_left": [o.name for o in bpy.data.objects if o.name.startswith(PREFIX)]}
    print("HEADLESS " + json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    if FAILS:
        raise SystemExit(1)


main()
