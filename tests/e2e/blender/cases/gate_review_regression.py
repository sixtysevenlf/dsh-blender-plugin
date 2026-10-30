# -*- coding: utf-8 -*-
"""gate 复审回归（v0.9.6 复审修复 · F2 / 最终集成 · F8）—— 把"干净装配也拿不到 pass"与
"没量过的门不许读成绿"两条钉死。

被钉死的缺陷（每一条都先复现、再修、再回归）：
  F2-a **mesh_health 没显式查自交**：`audit_scene` 默认 `self_intersect=false`，而新口径把"跳过检查"
       如实读成 `state=degraded / clean=false` ⇒ preset 的 mesh 门没写 `self_intersect:true`，
       **健康场景也 degraded**。修法：preset 的 mesh 门显式 `self_intersect:true`。
  F2-b **no_floaters 读错字段**：preset 的 `pass_if = "state == 'pass'"`，而 `audit_connectivity`
       **顶层没有 state/verdict**（顶层 `ok=true` 只表示"分析跑完了"；门结论在**嵌套 `gate`** 里：
       `gate.state` / `gate.verdict`）⇒ 该门必 `error`（"pass_if 引用了回执里没有的字段"）。
       修法：读回执自己给的已验证三态 `gate['verdict']`，并让 `_status_of` 认嵌套三态 ——
       否则"未分析 / 未确认 ⇒ degraded"会在 pass_if 处被压成 fail（或更糟：被抬成 pass）。
  F2-c **no_clash 无来源**：`audit_interference` 是**两两**算子（必须给 a/b 或 objects_a/objects_b 或
       file_a/file_b），preset 无从知道"哪两件比" ⇒ 空参必 `error`。修法：该门声明 `skip_if_missing`，
       未配置来源时记 `state=skipped`（显式列进回执的 `skipped[]` / `coverage`）；要真判就给来源
       （`gate_run(preset="assembly", overrides={"no_clash":{...}})`，或自己写 spec）。
  F8-a **`skipped` 不许当透明**（v3 · Lead 否决 `pass + skipped = pass`）：旧 `_aggregate` 把
       `["pass","skipped"]` 判成 `pass` ⇒ assembly preset 默认（`no_clash` 未配置来源）会给一个**健康
       但没量过干涉**的场景发绿。修法：`_aggregate` 最小修 —— **任一 skipped 或 degraded ⇒ 顶层
       degraded（fail 优先）**；要 pass 必须**每一门都真跑过且都过**。
       ⇒ 于是：默认 `gate_run(preset="assembly")` 的顶层是 `degraded / ok=false`（**正确行为，不是 bug**），
       配上两侧来源后才是 `pass`（下面两条断言把两个方向都钉住）。
  F8-b **print preset 的分辨率假通过**：`print_report` 顶层只有 `thin_samples_total` / `walls_ok`，
       `min_mm` 低于本网格测量分辨率时 `print_walls` 只把"结论不可信"写进 **per-object** 的
       `detail.walls.objects[].warning` / `resolution_mm`（顶层看不见）⇒ 旧 `pass_if` 会让
       "**根本没量得出来**"读成绿。修法：该门加 `degrade_if`（读真实字段
       `params.min_mm` vs `detail.walls.objects[0].resolution_mm`）⇒ 分辨率不可信时该门 `degraded`，
       并用 `echo` 把真实读数带进回执。回归同时钉住**反向**：网格够细（分辨率 ≤ min_mm）时该门照常 pass。

跑法（独立无头进程 + factory-startup；**不要拿 GUI 场景跑**：本测试会临时隐藏别人的 mesh 对象，
才能让"干净装配"这句话成立；跑完复原）：

    blender_rt_headless(preload="audit,gate,printcheck,uv_tools,material", engine="none",
        factory_startup=True, timeout_ms=300000,
        script_file="/home/sixtyseven67/DSH/dsh-blender-plugin/tests/gate_review_regression.py")
    # 或： blender -b --factory-startup --python tests/gate_review_regression.py

判据（全是状态/数值断言，不靠肉眼）：
  * 健康多件相接装配（正好相贴 gap=0 / 0.2mm 设计间隙）⇒ 默认 `gate_run(preset="assembly")` **顶层
    degraded**（`no_clash` 未配置来源 ⇒ skipped ⇒ 压成 degraded；没跑过的门不许读成绿）；
    **配上两侧来源后 ⇒ verdict=pass / ok=true / skipped=[] / coverage.all_gates_ran=true**；
    —— 审慎区分：**正好相贴**（gap=0）的两件在 precision=5 下顶点被焊接成 **1 个连通分量**，回执如实给
    `evidence=single-body`（"这一坨是一体"≠"装配门判过跨件相接"）；"多件相接"的正面证据用 0.2mm 设计间隙
    那一例（**2 个连通分量** + 逐件 `evidence=bbox+mesh` + `mesh_confirmed=true`）。
  * 真浮块 ⇒ `no_floaters=fail` 且整包 `fail`（归因点 name 点名 no_floaters）；
  * 强制未分析 / 未确认 ⇒ `no_floaters=degraded` 且整包 `degraded`（**不许 pass**）；
  * 单件（1 个连通分量）⇒ 装配门的 pass 必须自带 `evidence=single-body` ——
    "装配门对单件不适用（无从判'与其它分量不相接'）"这句话要能从回执读出来；
  * print preset ⇒ 粗网格（分辨率 2000mm ≫ min_mm 2mm）**判 degraded 而不是假绿**，且 `echo` 里的
    resolution_mm 与 receipt 里的真实字段逐位一致；细网格（分辨率 ≤ min_mm）⇒ pass。
"""
import json
import mathutils
import bmesh
import bpy

PASS, FAILS = [], []
PREFIX = "DSHGATE_"

_parked = []          # [(name, hide_viewport, hide_render)] —— 临时藏起来的"别人的" mesh

TAIL = "assembly"     # preset 名（错误信息里用）


def chk(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [OK]   " + name)
    else:
        FAILS.append({"clause": name, "detail": str(detail)[:400]})
        print("  [FAIL] " + name + "  <- " + str(detail)[:300])


def api(name):
    a = getattr(K, "dsh_" + name + "_api", None)  # noqa: F821  （K 由无头前导注入）
    if a is None:
        raise RuntimeError("模块没注入：dsh_%s_api（preload 里漏了 %s？）" % (name, name))
    return a


def mk_cube(name, loc=(0.0, 0.0, 0.0), size=2.0):
    me = bpy.data.meshes.new(PREFIX + name)
    ob = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size, matrix=mathutils.Matrix.Translation(loc))
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob.name


def mk_fine_cube(name, loc=(0.0, 0.0, 0.0), size=1.0, cuts=6):
    """细网格立方体（面尺寸 ≪ 壁厚）：F8-b 的**可信**一侧 —— 分辨率 ≤ min_mm 时 print 门才该 pass。"""
    me = bpy.data.meshes.new(PREFIX + name)
    ob = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size, matrix=mathutils.Matrix.Translation(loc))
    bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=cuts, use_grid_fill=True)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob.name


def mk_two_shells(name):
    """一个对象里放两块**互不相接**的壳（单件内部的浮壳）。"""
    me = bpy.data.meshes.new(PREFIX + name)
    ob = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.create_cube(bm, size=2.0, matrix=mathutils.Matrix.Translation((10.0, 0.0, 0.0)))
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob.name


def park_foreign():
    """把别的 mesh 对象临时隐藏：audit_connectivity 按 hide_render/hide_viewport 过滤，
    这样"范围内只有我的探针"才是真的（不动几何、跑完 unpark 复原）。"""
    _parked.clear()
    for ob in bpy.data.objects:
        if ob.type == "MESH" and not ob.name.startswith(PREFIX):
            _parked.append((ob.name, bool(ob.hide_viewport), bool(ob.hide_render)))
            ob.hide_viewport = True
            ob.hide_render = True


def unpark_foreign():
    for name, hv, hr in _parked:
        ob = bpy.data.objects.get(name)
        if ob is not None:
            ob.hide_viewport, ob.hide_render = hv, hr
    _parked.clear()


def purge_prefix():
    for ob in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        me = ob.data if ob.type == "MESH" else None
        bpy.data.objects.remove(ob, do_unlink=True)
        if me is not None and me.users == 0:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
    for me in [m for m in bpy.data.meshes if m.name.startswith(PREFIX) and m.users == 0]:
        try:
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass
    for mat in [m for m in bpy.data.materials if m.name.startswith(PREFIX) and m.users == 0]:
        try:
            bpy.data.materials.remove(mat, do_unlink=True)
        except Exception:
            pass


def run_preset(preset=TAIL, **kw):
    return api("gate")("run", dict({"preset": preset}, **kw))


def rows_of(r):
    return {x.get("id"): x for x in (r.get("gates") or [])}


def states(r):
    return {x.get("id"): x.get("state") for x in (r.get("gates") or [])}


# ---------------------------------------------------------------- 静态契约（不依赖场景）

def t_plan_contract():
    """preset 的三门各自读什么、缺什么会跳过 —— 先在 plan 面钉死，再验运行面。"""
    p = api("gate")("plan", {"preset": "assembly"})
    rows = {x["id"]: x for x in (p.get("gates") or [])}
    chk("契约：assembly preset 仍是 3 门（mesh_health / no_floaters / no_clash）",
        p.get("gate_count") == 3 and set(rows) == {"mesh_health", "no_floaters", "no_clash"},
        {"gate_count": p.get("gate_count"), "ids": sorted(rows)})
    chk("F2-a：mesh 门显式 self_intersect=true（否则健康场景被跳过检查拖成 degraded）",
        (rows.get("mesh_health", {}).get("args") or {}).get("self_intersect") is True,
        rows.get("mesh_health", {}).get("args"))
    chk("F2-b：no_floaters 读嵌套 gate（reads == ['gate']），不再引用顶层不存在的 state",
        rows.get("no_floaters", {}).get("reads") == ["gate"]
        and "state" not in (rows.get("no_floaters", {}).get("pass_if") or ""),
        rows.get("no_floaters"))
    chk("F2-c：no_clash 读真实字段 verdict（reads == ['verdict']），不再引用不存在的 total_volume_mm3",
        rows.get("no_clash", {}).get("reads") == ["verdict"],
        rows.get("no_clash"))
    chk("F2-c：no_clash 声明 skip_if_missing 两组（两侧来源各一组替代名）",
        len(rows.get("no_clash", {}).get("skip_if_missing") or []) == 2
        and all(len(g) >= 2 for g in (rows.get("no_clash", {}).get("skip_if_missing") or [])),
        rows.get("no_clash", {}).get("skip_if_missing"))
    # F8-b：print preset 必须有 degrade_if（分辨率不可信 ⇒ degraded）与 echo（真实字段取证）
    pri = api("gate")("plan", {"preset": "print"})
    prow = {x["id"]: x for x in (pri.get("gates") or [])}.get("walls") or {}
    chk("F8-b：print preset 声明 degrade_if（读真实字段：params.min_mm vs detail.walls.objects[0].resolution_mm）",
        "resolution_mm" in (prow.get("degrade_if") or "")
        and "min_mm" in (prow.get("degrade_if") or "")
        and (prow.get("args") or {}).get("scope") == "ACTIVE",
        {"degrade_if": prow.get("degrade_if"), "args": prow.get("args")})
    chk("F8-b：print preset 声明 echo（分辨率 / min_mm / warning 三个真实读数进回执）",
        len(prow.get("echo") or []) == 3
        and any("resolution_mm" in str(x) for x in (prow.get("echo") or []))
        and any("warning" in str(x) for x in (prow.get("echo") or [])),
        prow.get("echo"))


# ---------------------------------------------------------------- F2-a：跳过自交是诚实降级

def t_scene_selfint_honest():
    """前提复现 + 修复面：默认跳过自交必须 degraded；显式开启才给 pass。"""
    ob = mk_cube("single")
    r0 = api("audit")("scene", {"summary_only": True})
    chk("F2-a 前提：audit_scene 默认（不查自交）⇒ state=degraded、clean=false（诚实降级）",
        r0.get("state") == "degraded" and r0.get("clean") is False,
        {"state": r0.get("state"), "clean": r0.get("clean"), "reason": r0.get("reason")})
    r1 = api("audit")("scene", {"summary_only": True, "self_intersect": True})
    chk("F2-a 修复面：显式 self_intersect=true ⇒ 健康场景 state=pass、clean=true",
        r1.get("state") == "pass" and r1.get("clean") is True,
        {"state": r1.get("state"), "clean": r1.get("clean"), "reason": r1.get("reason"),
         "worst": [(w.get("name"), w.get("state")) for w in (r1.get("worst") or [])]})
    chk("F2-a：mesh 门自己带 self_intersect=true 的读数（plan 的 args 直接对得上）",
        (api("gate")("plan", {"preset": "assembly"})["gates"][0].get("args") or {}).get("self_intersect") is True,
        api("gate")("plan", {"preset": "assembly"})["gates"][0].get("args"))


# ---------------------------------------------------------------- 健康多件相接装配 ⇒ pass

def t_healthy_assembly_passes():
    """健康多件相接装配：**默认 preset 判 degraded**（no_clash 未配置来源 ⇒ skipped ⇒ 压成 degraded），
    **配上两侧来源后才 pass**。两个方向都要钉住 —— 这才是"跳过 ≠ 通过"的可执行定义。"""
    a = mk_cube("touch_a", (0.0, 0.0, 0.0))
    b = mk_cube("touch_b", (2.0, 0.0, 0.0))          # 正好相贴（gap=0.00 mm）
    r = run_preset(detail=True)
    st = states(r)
    rows = rows_of(r)
    chk("健康装配 + 默认 preset（no_clash 未配置来源）⇒ verdict=degraded / ok=false（**不许**为健康默认预设变绿）",
        r.get("verdict") == "degraded" and r.get("ok") is False,
        {"verdict": r.get("verdict"), "ok": r.get("ok"), "states": st,
         "failed": r.get("failed"), "degraded": r.get("degraded"), "skipped": r.get("skipped")})
    chk("…F8-a：skipped 不透明 —— no_clash=skipped 直接把顶层压成 degraded（不是 pass、也不是 fail）",
        st.get("no_clash") == "skipped" and r.get("verdict") == "degraded"
        and not (r.get("failed") or []),
        {"state": st.get("no_clash"), "verdict": r.get("verdict"), "failed": r.get("failed")})
    chk("…mesh_health=pass（不再被'跳过自交'拖成 degraded）", st.get("mesh_health") == "pass",
        rows.get("mesh_health"))
    chk("…no_floaters=pass（读 gate.verdict；相贴件按'已相接'容忍）", st.get("no_floaters") == "pass",
        {"state": st.get("no_floaters"), "key": (rows.get("no_floaters") or {}).get("key")})
    chk("…no_clash=skipped（未配置两侧来源 ⇒ 未检查；不是 error、也不是 pass）",
        st.get("no_clash") == "skipped", rows.get("no_clash"))
    chk("…回执显式列出没跑的门：skipped['no_clash'] + coverage.all_gates_ran=false（跳过 ≠ 通过，可审计）",
        r.get("skipped") == ["no_clash"] and (r.get("coverage") or {}).get("all_gates_ran") is False
        and (r.get("coverage") or {}).get("skipped") == 1,
        {"skipped": r.get("skipped"), "coverage": r.get("coverage")})
    conn = (rows.get("no_floaters") or {}).get("receipt") or {}
    gate = conn.get("gate") or {}
    chk("…no_floaters 的 pass_if 读的是**回执里真实存在的**嵌套三态（gate.verdict == 'pass'）",
        gate.get("verdict") == "pass" and conn.get("analyzed") is True,
        {"gate": gate, "analyzed": conn.get("analyzed")})
    # 审慎区分：**正好相贴（gap=0）**时两件的重合顶点在 precision=5 下被焊接成 1 个连通分量，
    # 回执如实给 evidence=single-body —— 它只能说明"这一坨是一体"，**不是**"装配门判过跨件相接"。
    comps = conn.get("components") or [{}]
    chk("审慎区分：正好相贴 ⇒ 顶点焊接成 1 个连通分量、回执 evidence=single-body（别拿它当跨件结论）",
        len(comps) == 1 and (comps[0] or {}).get("evidence") == "single-body"
        and (gate.get("micro_tolerated") or 0) >= 1 and (conn.get("real_floater_count") or 0) == 0,
        {"ncomp": len(comps), "evidence": (comps[0] or {}).get("evidence"),
         "micro_tolerated": gate.get("micro_tolerated"), "real": conn.get("real_floater_count")})

    # 0.2mm 设计间隙（< 0.3mm 相接口径）= 真正的"多件相接"：2 个连通分量 + 逐件网格确认相接
    purge_prefix()
    a = mk_cube("gap_a", (0.0, 0.0, 0.0))
    b = mk_cube("gap_b", (2.0002, 0.0, 0.0))         # 0.2mm 间隙（1 单位 = 1 m）
    r2 = run_preset(detail=True)
    conn2 = ((rows_of(r2).get("no_floaters") or {}).get("receipt") or {})
    gate2 = conn2.get("gate") or {}
    comps2 = conn2.get("components") or []
    chk("健康多件相接（0.2mm 设计间隙）⇒ 默认 preset 仍 degraded（no_clash skipped）、no_floaters=pass、"
        "real_floater_count=0",
        r2.get("verdict") == "degraded" and states(r2).get("no_floaters") == "pass"
        and states(r2).get("no_clash") == "skipped"
        and (conn2.get("real_floater_count") or 0) == 0,
        {"verdict": r2.get("verdict"), "states": states(r2), "real": conn2.get("real_floater_count"),
         "gate": gate2})
    chk("…多件装配确实按'跨件相接'判：2 个连通分量、逐件 evidence=bbox+mesh、mesh_confirmed=true",
        len(comps2) == 2 and all((s or {}).get("evidence") == "bbox+mesh"
                                 and (s or {}).get("mesh_confirmed") is True for s in comps2)
        and (gate2.get("micro_tolerated") or 0) == 2,
        {"ncomp": len(comps2),
         "comps": [(s.get("evidence"), s.get("mesh_confirmed"), s.get("gap_mm")) for s in comps2],
         "gate": gate2})
    # F8-a 反向：**每一门都真跑过且都过** ⇒ 才是 pass。给 no_clash 补上两侧来源（0.2mm 间隙 ⇒ AABB 不相交 ⇒
    # interference verdict=refuted ⇒ 该门 pass），此时三门全跑全过 ⇒ 顶层 pass / ok=true / 无 skipped。
    r3 = run_preset(overrides={"no_clash": {"objects_a": [a], "objects_b": [b]}})
    chk("F8-a 反向：健康装配 + 给 no_clash 补两侧来源（每门都真跑过且都过）⇒ verdict=pass / ok=true",
        r3.get("verdict") == "pass" and r3.get("ok") is True and states(r3).get("no_clash") == "pass",
        {"verdict": r3.get("verdict"), "ok": r3.get("ok"), "states": states(r3),
         "key": (rows_of(r3).get("no_clash") or {}).get("key")})
    chk("…此时 coverage 自证「没有任何门被跳过」：skipped=[] / all_gates_ran=true / checked=3",
        (r3.get("skipped") or []) == [] and (r3.get("coverage") or {}).get("all_gates_ran") is True
        and (r3.get("coverage") or {}).get("checked") == 3
        and (r3.get("coverage") or {}).get("skipped") == 0,
        r3.get("coverage"))
    chk("…overrides 只换 args 不改判据（回执如实记 overrides_applied）",
        (r3.get("overrides_applied") or {}).get("no_clash") == ["objects_a", "objects_b"],
        r3.get("overrides_applied"))


# ---------------------------------------------------------------- 真浮块 ⇒ fail

def t_real_floater_fails():
    """真浮块（远处孤立件）：no_floaters 必须 fail，且整包 fail 归因到它。"""
    a = mk_cube("f_a", (0.0, 0.0, 0.0))
    b = mk_cube("f_b", (2.0, 0.0, 0.0))
    f = mk_cube("f_far", (50.0, 0.0, 0.0))
    r = run_preset(detail=True)
    st = states(r)
    rows = rows_of(r)
    chk("真浮块 ⇒ no_floaters=fail、整包 verdict=fail（且 mesh_health 仍 pass，归因不串门）",
        st.get("no_floaters") == "fail" and r.get("verdict") == "fail"
        and st.get("mesh_health") == "pass" and r.get("failed") == ["no_floaters"],
        {"states": st, "verdict": r.get("verdict"), "failed": r.get("failed")})
    conn = (rows.get("no_floaters") or {}).get("receipt") or {}
    gate = conn.get("gate") or {}
    chk("…fail 的证据来自真实字段：gate.verdict='fail' / real_floater_count>0 / offenders 有归因对象",
        gate.get("verdict") == "fail" and (conn.get("real_floater_count") or 0) > 0
        and bool(gate.get("offenders")),
        {"gate": gate, "real": conn.get("real_floater_count")})


# ---------------------------------------------------------------- 未分析/未确认 ⇒ degraded

def t_incomplete_analysis_degraded():
    """分析没跑完（analyzed=false）只能是 degraded —— 不许 pass，也不许被 pass_if 压成 fail。"""
    a = mk_cube("d_a", (0.0, 0.0, 0.0))
    b = mk_cube("d_b", (2.0, 0.0, 0.0))
    spec = {"name": "degraded", "gates": [
        {"id": "no_floaters", "op": "audit_connectivity", "args": {"max_tris": 1},
         "pass_if": "gate['verdict'] == 'pass'"}]}
    r = api("gate")("run", {"spec": spec, "detail": True})
    row = (r.get("gates") or [{}])[0]
    conn = row.get("receipt") or {}
    chk("强制未分析（max_tris=1）⇒ no_floaters=degraded、整包 degraded（不是 pass，也不是 fail）",
        row.get("state") == "degraded" and r.get("verdict") == "degraded" and r.get("ok") is False,
        {"state": row.get("state"), "verdict": r.get("verdict"), "ok": r.get("ok")})
    chk("…回执自证没跑完：analyzed=false、gate.verdict='degraded'（真实字段，不是靠猜）",
        conn.get("analyzed") is False and (conn.get("gate") or {}).get("verdict") == "degraded",
        {"analyzed": conn.get("analyzed"), "gate": conn.get("gate"), "reason": conn.get("reason")})
    chk("…不许读成通过：verdict != 'pass' 且 pass_if_value 不为真",
        r.get("verdict") != "pass" and row.get("pass_if_value") is not True,
        {"verdict": r.get("verdict"), "pass_if_value": row.get("pass_if_value")})

    # preset 走同一条路（overrides 只换 args，不改判据）⇒ 整包 degraded，no_floaters 点名在 degraded 里
    r2 = run_preset(overrides={"no_floaters": {"max_tris": 1}})
    chk("preset + overrides 强制未分析 ⇒ no_floaters=degraded、整包 verdict=degraded、failed 为空",
        states(r2).get("no_floaters") == "degraded" and r2.get("verdict") == "degraded"
        and (r2.get("degraded") or []) == ["no_floaters"] and not (r2.get("failed") or []),
        {"states": states(r2), "verdict": r2.get("verdict"), "degraded": r2.get("degraded"),
         "failed": r2.get("failed")})
    chk("…overrides 生效但没改判据（mesh_health 仍 pass；no_clash 仍 skipped）",
        states(r2).get("mesh_health") == "pass" and states(r2).get("no_clash") == "skipped",
        states(r2))


# ---------------------------------------------------------------- 单件 ≠ 装配（审慎区分）

def t_single_part_not_assembly():
    """单件（1 个连通分量）能过装配门，但 pass 必须自带 single-body 证据 —— 别把"单件"读成"装配已连通"。"""
    ob = mk_cube("one")
    r = run_preset(detail=True)
    row = (rows_of(r).get("no_floaters") or {})
    conn = row.get("receipt") or {}
    comps = conn.get("components") or [{}]
    chk("单件：no_floaters=pass（1 个连通分量无从判'与其它分量不相接'）", row.get("state") == "pass",
        {"state": row.get("state"), "key": row.get("key")})
    chk("单件：pass 自带 evidence=single-body（回执自己说明'这条判据对单件不成立'，可审计）",
        (comps[0] or {}).get("evidence") == "single-body"
        and "1 个连通分量" in str((comps[0] or {}).get("confirm_note") or ""),
        comps[0])
    chk("单件：装配门不冒充装配结论（回执里没有 real_floater_count>0 之类硬判据被绕过）",
        (conn.get("real_floater_count") or 0) == 0 and (conn.get("gate") or {}).get("visible_floater_count") == 0,
        {"real": conn.get("real_floater_count"), "gate": conn.get("gate")})

    # 单件内部两块互不相接的壳：连通门会抓到（真浮块），但这**不是装配结论** ——
    # 单件内部壳的贴合/互穿应看 audit_mesh(island_split=true)
    purge_prefix()
    two = mk_two_shells("two_shells")
    r2 = api("gate")("run", {"spec": {"name": "single-two-shells", "gates": [
        {"id": "nf", "op": "audit_connectivity", "args": {"objects": [two]},
         "pass_if": "gate['verdict'] == 'pass'"}]}})
    row2 = (r2.get("gates") or [{}])[0]
    chk("单件内部两块互不相接的壳 ⇒ 连通门 fail（真浮块 2）——是网格问题，不是'装配'问题",
        row2.get("state") == "fail" and r2.get("verdict") == "fail",
        {"state": row2.get("state"), "key": row2.get("key")})


# ---------------------------------------------------------------- 配置了来源的 clash 门

def t_clash_configured():
    """no_clash 配上两侧来源就是真门：真互穿 fail、远离 pass、'证不了' degraded。"""
    c = mk_cube("cl_a", (0.0, 0.0, 0.0))
    d = mk_cube("cl_b", (1.0, 0.0, 0.0))             # 互穿 1 单位
    e = mk_cube("cl_c", (2.0, 0.0, 0.0))             # 与 c 正好相贴
    g = mk_cube("cl_d", (50.0, 0.0, 0.0))            # 远离
    spec = {"name": "clash", "gates": [
        {"id": "no_clash", "op": "audit_interference", "args": {"objects_a": [c], "objects_b": [d]},
         "pass_if": "verdict == 'refuted'"}]}
    r = api("gate")("run", {"spec": spec})
    chk("no_clash 配了来源：真互穿 ⇒ 干涉 verdict=supported ⇒ 门 fail",
        states(r).get("no_clash") == "fail" and r.get("verdict") == "fail",
        {"states": states(r), "key": (r.get("gates") or [{}])[0].get("key")})
    spec["gates"][0]["args"] = {"objects_a": [c], "objects_b": [g]}
    r2 = api("gate")("run", {"spec": spec})
    chk("no_clash 配了来源：远离件 ⇒ 干涉 verdict=refuted ⇒ 门 pass",
        states(r2).get("no_clash") == "pass", {"states": states(r2),
                                               "key": (r2.get("gates") or [{}])[0].get("key")})
    spec["gates"][0]["args"] = {"objects_a": [c], "objects_b": [e]}
    r3 = api("gate")("run", {"spec": spec})
    chk("no_clash 配了来源：正好相贴 ⇒ 干涉 verdict=unresolved（上界不够紧）⇒ 门 degraded（'证不了'不许读成 pass）",
        states(r3).get("no_clash") == "degraded",
        {"states": states(r3), "key": (r3.get("gates") or [{}])[0].get("key")})

    # preset + overrides 配上 side 之后，整包会被这门如实拉成 degraded ——
    # 这就是 preset 默认把 clash 记为 skipped（而不是假装 pass）的理由
    purge_prefix()
    a = mk_cube("ov_a", (0.0, 0.0, 0.0))
    b = mk_cube("ov_b", (2.0, 0.0, 0.0))
    r4 = run_preset(overrides={"no_clash": {"objects_a": [a], "objects_b": [b]}})
    chk("preset + overrides 配了 side：相贴件的 clash 只能给 degraded ⇒ 整包 degraded（诚实，不是 pass）",
        states(r4).get("no_clash") == "degraded" and r4.get("verdict") == "degraded",
        {"states": states(r4), "verdict": r4.get("verdict"), "degraded": r4.get("degraded")})


# ---------------------------------------------------------------- 另外两个 preset 的字段有效性

def t_print_delivery_presets_field_valid():
    """print / delivery preset 的 pass_if 必须落在真实字段上；print 门还要**不许在分辨率不可信时假通过**。"""
    ob = mk_cube("print_ok", (0.0, 0.0, 0.0))        # 粗网格：面尺寸 2 单位 = 2000mm
    bpy.context.view_layer.objects.active = bpy.data.objects[ob]
    r = run_preset("print", detail=True)
    rows = rows_of(r)
    row = rows.get("walls") or {}
    echo = row.get("echo") or {}
    chk("print preset：pass_if 引用的是回执真实字段（不再 error '没有的字段'）",
        all(x.get("state") != "error" for x in rows.values()), states(r))
    # F8-b：粗网格（分辨率 2000mm ≫ min_mm 2mm）——"没量到薄壁"没有意义，必须 degraded 而不是绿。
    chk("F8-b：粗网格（resolution_mm 远大于 min_mm）⇒ walls 门 degraded、整包 degraded（**不许假通过**）",
        row.get("state") == "degraded" and r.get("verdict") == "degraded" and r.get("ok") is False,
        {"state": row.get("state"), "verdict": r.get("verdict"), "ok": r.get("ok"),
         "key": row.get("key"), "echo": echo})
    chk("F8-b：降级不是因为判据为假 —— pass_if_value=true（判据为真、证据不可信才是降级理由）",
        row.get("pass_if_value") is True and row.get("degrade_if"),
        {"pass_if_value": row.get("pass_if_value"), "degrade_if": row.get("degrade_if")})
    chk("F8-b：echo 取的是**真实字段**（与 receipt 里的 resolution_mm 逐位一致，且 > min_mm；warning 文本在）",
        echo.get("detail.walls.objects.0.resolution_mm") is not None
        and echo.get("detail.walls.objects.0.resolution_mm")
        == ((row.get("receipt") or {}).get("detail", {}).get("walls", {}).get("objects", [{}])[0] or {}).get("resolution_mm")
        and (echo.get("detail.walls.objects.0.resolution_mm") or 0) > (echo.get("params.min_mm") or 0)
        and bool(echo.get("detail.walls.objects.0.warning")),
        echo)

    # F8-b 反向：网格够细（面尺寸 ≪ 壁厚）⇒ 分辨率 ≤ min_mm ⇒ 该门照常 pass（"只有不可信才降级"）
    purge_prefix()
    fine = mk_fine_cube("print_fine", (0.0, 0.0, 0.0), size=1.0, cuts=6)
    bpy.context.view_layer.objects.active = bpy.data.objects[fine]
    r2f = run_preset("print", overrides={"walls": {"min_mm": 400.0, "mm_per_unit": 1000.0}})
    row2f = (rows_of(r2f).get("walls") or {})
    fch = row2f.get("echo") or {}
    chk("F8-b 反向：细网格（分辨率 ≈143mm ≤ min_mm 400mm，壁厚 1000mm）⇒ walls 门 pass、整包 pass",
        row2f.get("state") == "pass" and r2f.get("verdict") == "pass" and r2f.get("ok") is True,
        {"state": row2f.get("state"), "verdict": r2f.get("verdict"), "echo": fch,
         "key": row2f.get("key")})
    chk("F8-b 反向：可信读数也在 echo 里（resolution_mm < min_mm）",
        (fch.get("detail.walls.objects.0.resolution_mm") or 1e9) < (fch.get("params.min_mm") or 0),
        fch)

    # delivery preset：uv_stats / material_scan 的 scope 默认 ACTIVE ⇒ 先把探针设成活动对象
    purge_prefix()
    name = mk_cube("deliver_ok", (0.0, 0.0, 0.0))
    ob = bpy.data.objects[name]
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    uv = api("uv")("smart_project", {"objects": [name], "angle_limit": 66})
    mat = bpy.data.materials.new(PREFIX + "M")
    mat.use_nodes = True
    ob.data.materials.append(mat)
    r2 = run_preset("delivery")
    rows2 = rows_of(r2)
    chk("delivery preset：pass_if 引用的是回执真实字段（不再 error '没有的字段'）",
        all(x.get("state") != "error" for x in rows2.values()),
        {"states": states(r2), "rows": {k: v.get("error") for k, v in rows2.items()}})
    chk("delivery preset：有 UV（零面积面=0）+ 有材质 ⇒ verdict=pass",
        r2.get("verdict") == "pass" and uv.get("ok") is True,
        {"verdict": r2.get("verdict"), "states": states(r2), "uv_ok": uv.get("ok")})


# ---------------------------------------------------------------- skipped 不透明 + fail 优先（F8-a）

def t_skipped_drags_and_fail_wins():
    """F8-a 的最小定义：**任一 skipped 或 degraded ⇒ 顶层 degraded（fail 优先）**；全 pass 才 pass。"""
    ob = mk_cube("agg_ok", (0.0, 0.0, 0.0))
    ok_gate = {"id": "ok", "op": "audit_mesh", "args": {"objects": [ob]}, "pass_if": "clean == True"}
    bad_gate = {"id": "bad", "op": "audit_mesh", "args": {"objects": [ob]}, "pass_if": "boundary_edges > 999999"}
    skip_gate = {"id": "skip", "op": "audit_interference", "args": {},
                 "skip_if_missing": [["objects_a", "a"], ["objects_b", "b"]],
                 "pass_if": "verdict == 'refuted'"}
    r = api("gate")("run", {"spec": {"name": "pass+skip", "gates": [ok_gate, json.loads(json.dumps(skip_gate))]}})
    chk("F8-a：pass + skipped ⇒ 顶层 degraded / ok=false（旧口径会判 pass —— 这就是被否决的那条）",
        r.get("verdict") == "degraded" and r.get("ok") is False
        and states(r).get("ok") == "pass" and states(r).get("skip") == "skipped",
        {"verdict": r.get("verdict"), "ok": r.get("ok"), "states": states(r)})
    chk("F8-a：skipped 仍可审计（skipped[] / coverage / skipped_note 都在，且说清会把顶层压成 degraded）",
        (r.get("skipped") or []) == ["skip"] and (r.get("coverage") or {}).get("all_gates_ran") is False
        and "degraded" in (r.get("skipped_note") or ""),
        {"skipped": r.get("skipped"), "coverage": r.get("coverage"),
         "skipped_note": r.get("skipped_note")})
    r2 = api("gate")("run", {"spec": {"name": "fail+skip",
                                      "gates": [bad_gate, json.loads(json.dumps(skip_gate))]}})
    chk("F8-a：fail 优先 —— fail + skipped ⇒ 顶层 fail（不被 skipped 弱化成 degraded）",
        r2.get("verdict") == "fail" and (r2.get("failed") or []) == ["bad"]
        and (r2.get("skipped") or []) == ["skip"],
        {"verdict": r2.get("verdict"), "failed": r2.get("failed"), "skipped": r2.get("skipped")})
    r3 = api("gate")("run", {"spec": {"name": "all-skip", "gates": [json.loads(json.dumps(skip_gate))]}})
    chk("F8-a：全部门 skipped ⇒ degraded（什么都没查不给通过结论）",
        r3.get("verdict") == "degraded" and r3.get("ok") is False
        and (r3.get("coverage") or {}).get("checked") == 0,
        {"verdict": r3.get("verdict"), "coverage": r3.get("coverage")})
    # degrade_if 写错（引用不存在的字段）⇒ 该门 error 且点名 degrade_if（不许静默当通过）
    bad = {"id": "d", "op": "audit_mesh", "args": {"objects": [ob]}, "pass_if": "clean == True",
           "degrade_if": "nope_field > 1"}
    r4 = api("gate")("run", {"spec": {"name": "bad-degrade-if", "gates": [bad]}})
    row4 = (r4.get("gates") or [{}])[0]
    chk("F8：degrade_if 引用不存在的字段 ⇒ 该门 error、整包 fail，且错误里点名 degrade_if（不静默）",
        row4.get("state") == "error" and "degrade_if" in (row4.get("error") or "")
        and r4.get("verdict") == "fail",
        {"state": row4.get("state"), "error": row4.get("error"), "verdict": r4.get("verdict")})


def main():
    before = len(bpy.data.objects)
    print("== gate 复审回归（F2：干净装配拿不到 pass；F8：跳过与不可信证据都不许读成绿）==")
    park_foreign()
    try:
        for fn in (t_plan_contract, t_scene_selfint_honest, t_healthy_assembly_passes,
                   t_real_floater_fails, t_incomplete_analysis_degraded, t_single_part_not_assembly,
                   t_clash_configured, t_skipped_drags_and_fail_wins,
                   t_print_delivery_presets_field_valid):
            try:
                fn()
            except Exception as e:
                chk(fn.__name__ + " 未抛异常", False, "%s: %s" % (type(e).__name__, str(e)[:220]))
            purge_prefix()
    finally:
        purge_prefix()
        unpark_foreign()
    chk("临时对象已清干净（场景对象数复原）", len(bpy.data.objects) == before,
        "%d -> %d" % (before, len(bpy.data.objects)))
    out = {"pass": len(PASS), "fail": len(FAILS), "fails": FAILS, "probe": "gate_review_regression"}
    print("HEADLESS " + json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    if FAILS:
        raise SystemExit(1)


main()
