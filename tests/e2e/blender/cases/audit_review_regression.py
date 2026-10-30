# -*- coding: utf-8 -*-
"""audit_mesh / calib 复审回归（v0.9.6 复审修复）—— 把这次抓到的 4 个"假通过"钉死。

被钉死的缺陷（每一条都先复现、再修、再回归）：
  A. **孤立点**：`loose_verts > 0` 却 `clean=true` —— loose_verts 没进 clean 判据。
  B. **反向分离壳**：同一对象里"大正向壳 + 小分离反向壳"，聚合有符号体积恒为正（+8 + (−1) = +7）
     ⇒ 旧代码 `normals_outward = vol > 0` 恒为 true，反向壳被吞掉。修法：逐**连通壳**判。
  C. **合法空腔**：外层正向 + 内层反向且**确实嵌套**（空腔）是合法几何，不许误报成反向缺陷；
     而 AABB 相交却无法确证嵌套/空腔时不许猜 —— 必须 `normals_state=unknown`（→ verdict=degraded）。
  D. **自交检查跳过/异常**：`self_intersect=false`（跳过）或 BVH 抛异常时，旧代码留下 `self_intersections=0`
     ⇒ `clean=true` 的假通过。修法：`self_intersections_analyzed/state` + verdict=degraded + clean=false。
  E. **calib 任意失败算捕获**：旧 `caught = verdict != 'pass'` ⇒ 门 error（模块没注入 / pass_if 写错）
     和 degraded 都被记成"抓到了"，捕获率虚高（实测：抽掉 print 模块后仍报 8/8，其中 thin_wall 名义上
     只被 print"抓住"，而那条门其实 error）。修法：**只有对应门 state=='fail' 才算捕获**。
  F. **calib 单位重置**：基线在 1 单位 = 1 mm 下跑，缺陷段却复位回场景单位 ⇒ 两段口径不同，
     micro_gap(0.3mm)/min_mm(1.0) 的门径在两段之间漂移。修法：全段统一 CALIB_SCALE，finally 复位。

跑法（无头 + factory-startup；绝不能拿 GUI 场景跑，因为要临时建对象）：
    blender_rt_headless(preload="audit,calib,gate,printcheck", engine="none", factory_startup=True,
        timeout_ms=300000, outdir="/home/sixtyseven67/DSH/blender-review-probes/audit",
        script_file="/home/sixtyseven67/DSH/dsh-blender-plugin/tests/audit_review_regression.py")
    # 或： blender -b --factory-startup --python tests/audit_review_regression.py

判据全部是**数值/状态断言**（不靠肉眼）：健康必须 pass、反向分离壳必须 fail、合法空腔必须 pass、
孤立点必须 fail、跳过/异常必须 degraded 且 clean=false、calib 必须逐类由对应门 fail 捕获。
"""
import json
import mathutils
import bmesh
import bpy

PASS, FAILS = [], []
PREFIX = "DSHAUDREG_"
SCALE_TOL = 1e-6            # scale_length 是 float32 属性（0.001 存成 0.001000000047…）
CALIB_SCALE = 0.001


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


def near(a, b, tol=SCALE_TOL):
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))
    except Exception:
        return False


# ------------------------------------------------------------------ 造探针网格

def add_cube(bm, size=2.0, loc=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    """往 bm 里加一个盒子，返回**这次新加的面**（用于只翻转其中一块壳）。"""
    before = set(bm.faces)
    m = mathutils.Matrix.Translation(loc) @ mathutils.Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0))
    bmesh.ops.create_cube(bm, size=size, matrix=m)
    return [f for f in bm.faces if f not in before]


def mk(name, builder):
    me = bpy.data.meshes.new(PREFIX + name)
    ob = bpy.data.objects.new(PREFIX + name, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    builder(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return ob.name


def mesh_info(name, **kw):
    """audit_mesh 单对象便利封装：返回 (整包回执, 该对象行)。"""
    args = {"objects": [name], "max_issues": 5}
    args.update(kw)
    r = api("audit")("mesh", args)
    row = (r.get("objects") or [{}])[0]
    return r, row


def shell_of(row, state):
    return [s for s in (row.get("normals_shells") or []) if s.get("state") == state]


# ------------------------------------------------------------------ A. 孤立点

def t_loose_verts():
    """缺陷 A：孤立点必须让 clean=false（旧代码 loose_verts>0 仍 clean=true）。"""
    ob = mk("loose", lambda bm: (add_cube(bm, 2.0), bm.verts.new((6, 6, 6)), bm.verts.new((-7, 1, 2))))
    r, row = mesh_info(ob, self_intersect=True)
    chk("A 孤立点：loose_verts >= 2 被看见", (row.get("loose_verts") or 0) >= 2, row.get("loose_verts"))
    chk("A 孤立点：clean == False（回归：旧代码在这里给 true）", r.get("clean") is False, r.get("clean"))
    chk("A 孤立点：state == 'fail'", r.get("state") == "fail", r.get("state"))
    chk("A 孤立点：totals.loose_verts > 0", (r.get("totals") or {}).get("loose_verts", 0) >= 2,
        r.get("totals"))
    chk("A 孤立点：孤立点不是朝向缺陷（normals_outward 仍为 True）",
        row.get("normals_outward") is True and row.get("normals_state") == "pass",
        (row.get("normals_outward"), row.get("normals_state")))
    # 孤立**边**（wire edge）：旧代码里 MeshEdge 没有 link_faces ⇒ loose_edges 恒 None/0（"没查"被当"没有"）
    ob2 = mk("wire", lambda bm: _wire(bm))
    r2, row2 = mesh_info(ob2, self_intersect=True)
    chk("A 孤立边：loose_edges >= 1（不再恒为 0）", (row2.get("loose_edges") or 0) >= 1, row2.get("loose_edges"))
    chk("A 孤立边：clean == False", r2.get("clean") is False, r2.get("clean"))


def _wire(bm):
    add_cube(bm, 2.0)
    bm.verts.ensure_lookup_table()
    bm.verts.new((6, 0, 0))
    bm.verts.new((8, 0, 0))
    bm.verts.ensure_lookup_table()
    bm.edges.new((bm.verts[-2], bm.verts[-1]))


# ------------------------------------------------------------------ 健康

def t_healthy():
    """健康基线：闭合单壳必须干净通过（门不许误报）。"""
    ob = mk("healthy", lambda bm: add_cube(bm, 2.0))
    r, row = mesh_info(ob, self_intersect=True)
    chk("健康：clean == True", r.get("clean") is True, (r.get("clean"), r.get("reason")))
    chk("健康：state == 'pass'", r.get("state") == "pass", r.get("state"))
    chk("健康：normals_outward == True 且 normals_state == 'pass'",
        row.get("normals_outward") is True and row.get("normals_state") == "pass",
        (row.get("normals_outward"), row.get("normals_state")))
    chk("健康：self_intersections_analyzed == True 且 state == 'pass'",
        row.get("self_intersections_analyzed") is True and row.get("self_intersections_state") == "pass",
        (row.get("self_intersections_analyzed"), row.get("self_intersections_state")))
    chk("健康：analysis.normals == 'checked'（判过，不是没判）",
        (r.get("analysis") or {}).get("normals") == "checked", r.get("analysis"))
    # 逐壳明细：单壳、未嵌套、pass；同时用散度定理的面形式体积与 bm.calc_volume 对拍
    shells = row.get("normals_shells") or []
    chk("健康：逐连通壳明细给了 1 个壳且 state=pass",
        len(shells) == 1 and shells[0].get("state") == "pass" and shells[0].get("nested") is False, shells)
    chk("健康：逐壳有符号体积 +8 与整体有符号体积一致（面形式 Σ(A/3)·n̂·(c−o) 对拍 bm.calc_volume）",
        abs(float(shells[0].get("signed_volume") or 0) - 8.0) < 1e-4
        and abs(float(row.get("signed_volume") or 0) - 8.0) < 1e-4,
        (shells, row.get("signed_volume")))
    # audit_scene（自交全查）也要给出 pass
    sc = api("audit")("scene", {"self_intersect": True, "summary_only": True, "top_k": 50})
    mine = [x for x in (sc.get("worst") or []) if x.get("name") == ob]
    chk("健康：audit_scene(self_intersect=True) 里该对象 state=pass",
        bool(mine) and mine[0].get("state") == "pass", mine)


def t_shell_face_cap():
    """安全阀：超过逐壳分析面数上限时降级为 unknown（未分析 ≠ 通过），且 env 可调。"""
    import os
    ob = mk("cap_probe", lambda bm: add_cube(bm, 2.0))
    os.environ["DSH_SHELL_MAX_FACES"] = "4"          # 6 面 > 4 ⇒ 触发上限
    try:
        r, row = mesh_info(ob, self_intersect=True)
    finally:
        os.environ.pop("DSH_SHELL_MAX_FACES", None)
    chk("上限：超面数上限 ⇒ normals_state == 'unknown' 且 normals_outward is None（不猜）",
        row.get("normals_state") == "unknown" and row.get("normals_outward") is None,
        (row.get("normals_state"), row.get("normals_outward")))
    chk("上限：整体判成 degraded（clean=false），不是 fail 也不是假 pass",
        r.get("state") == "degraded" and r.get("clean") is False,
        (r.get("state"), r.get("clean"), r.get("reason")))
    chk("上限：reason 点明面数上限与 env 开关",
        "DSH_SHELL_MAX_FACES" in (row.get("normals_reason") or ""), row.get("normals_reason"))
    # env 撤掉后同一对象必须重新 pass（证明上面是上限触发的，不是几何问题）
    r2, row2 = mesh_info(ob, self_intersect=True)
    chk("上限：撤掉 env 上限后同一对象重新 normals_state=pass",
        row2.get("normals_state") == "pass" and r2.get("clean") is True,
        (row2.get("normals_state"), r2.get("clean")))


# ------------------------------------------------------------------ B. 反向分离壳

def t_inverted_separated_shell():
    """缺陷 B：同对象"大正向壳 + 小分离反向壳"——聚合有符号体积为正，逐壳必须判出反向。"""
    ob = mk("sep_inv", lambda bm: _sep_inv(bm))
    r, row = mesh_info(ob, self_intersect=True)
    chk("B 反向分离壳：聚合有符号体积仍为正（正是旧判据被吞掉的场景）",
        (row.get("signed_volume") or 0) > 0, row.get("signed_volume"))
    chk("B 反向分离壳：normals_outward == False（回归：旧代码给 true）",
        row.get("normals_outward") is False, row.get("normals_outward"))
    chk("B 反向分离壳：normals_state == 'fail'", row.get("normals_state") == "fail", row.get("normals_state"))
    chk("B 反向分离壳：totals.normals_inverted >= 1", (r.get("totals") or {}).get("normals_inverted", 0) >= 1,
        r.get("totals"))
    chk("B 反向分离壳：clean == False / state == 'fail'",
        r.get("clean") is False and r.get("state") == "fail", (r.get("clean"), r.get("state")))
    shells = row.get("normals_shells") or []
    chk("B 反向分离壳：逐壳判定 —— 恰好 1 个 fail 壳 + 1 个 pass 壳（不是整体一刀切）",
        len(shells) == 2 and len(shell_of(row, "fail")) == 1 and len(shell_of(row, "pass")) == 1, shells)
    inv = shell_of(row, "fail")
    chk("B 反向分离壳：被点名的正是反向那块（signed_volume < 0）",
        bool(inv) and inv[0].get("signed_volume", 0) < 0, inv)
    chk("B 反向分离壳：normals_reason 说清是逐壳判的",
        "非嵌套壳" in (row.get("normals_reason") or ""), row.get("normals_reason"))
    # 对照：两块**同向**分离实心体（合法）不许被判反向
    ob2 = mk("two_solids", lambda bm: (add_cube(bm, 2.0), add_cube(bm, 1.0, (5, 0, 0))))
    r2, row2 = mesh_info(ob2, self_intersect=True)
    chk("B 对照：两块分离同向实心体 ⇒ pass（分离本身不是缺陷）",
        r2.get("clean") is True and row2.get("normals_outward") is True and len(row2.get("normals_shells") or []) == 2,
        (r2.get("clean"), row2.get("normals_outward"), row2.get("normals_shells")))


def _sep_inv(bm):
    add_cube(bm, 2.0)                                   # 大正向壳：+8
    bmesh.ops.reverse_faces(bm, faces=add_cube(bm, 1.0, (5, 0, 0)))   # 分离的小壳：−1


def _degenerate_face(bm):
    """零面积面（三角化后把一条边的两个端点重合）—— 必须报 degenerate_faces>0。"""
    add_cube(bm, 2.0)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.faces.ensure_lookup_table()
    f = bm.faces[0]
    f.verts[1].co = f.verts[0].co.copy()


# ------------------------------------------------------------------ C. 合法空腔

def t_legal_cavity():
    """缺陷 C：合法嵌套空腔不许误报；无法确定必须 unknown/degraded（不许猜 pass）。"""
    # ① 外壳正向 + 内壳反向且确实在里面 ⇒ 合法空腔，必须 pass
    ob = mk("cavity", lambda bm: _cavity(bm))
    r, row = mesh_info(ob, self_intersect=True)
    chk("C 合法空腔：clean == True（不许把空腔当反向缺陷）", r.get("clean") is True,
        (r.get("clean"), r.get("reason")))
    chk("C 合法空腔：normals_outward == True / normals_state == 'pass'",
        row.get("normals_outward") is True and row.get("normals_state") == "pass",
        (row.get("normals_outward"), row.get("normals_state")))
    nested = [s for s in (row.get("normals_shells") or []) if s.get("nested")]
    chk("C 合法空腔：内壳被识别为 nested（且它自己是负体积）",
        len(nested) == 1 and nested[0].get("signed_volume", 0) < 0, row.get("normals_shells"))
    chk("C 合法空腔：外层的负体积嵌套壳没有进 normals_inverted",
        (r.get("totals") or {}).get("normals_inverted", 0) == 0, r.get("totals"))
    # ② 壳内独立实心件（同向嵌套）也是合法几何
    ob2 = mk("nested_solid", lambda bm: (add_cube(bm, 2.0), add_cube(bm, 1.0)))
    r2, row2 = mesh_info(ob2, self_intersect=True)
    chk("C 壳内独立件：clean == True 且有一个 nested 壳",
        r2.get("clean") is True and any(s.get("nested") for s in (row2.get("normals_shells") or [])),
        (r2.get("clean"), row2.get("normals_shells")))
    # ③ 判不出来的（AABB 相交但确证不了嵌套/空腔）⇒ unknown + degraded，绝不猜 pass
    ob3 = mk("crossing", lambda bm: (add_cube(bm, 2.0), add_cube(bm, 1.2, (0.5, 0, 0))))
    r3, row3 = mesh_info(ob3, self_intersect=True)
    chk("C 无法确定：normals_state == 'unknown' 且 normals_outward is None（不猜成 True）",
        row3.get("normals_state") == "unknown" and row3.get("normals_outward") is None,
        (row3.get("normals_state"), row3.get("normals_outward")))
    chk("C 无法确定：互穿同时被 self_intersections 真抓到 ⇒ state == 'fail'",
        r3.get("state") == "fail" and (row3.get("self_intersections") or 0) > 0,
        (r3.get("state"), row3.get("self_intersections")))
    chk("C 无法确定：totals.normals_unknown >= 1（unknown 会被单独计数）",
        (r3.get("totals") or {}).get("normals_unknown", 0) >= 1, r3.get("totals"))


def _cavity(bm):
    add_cube(bm, 2.0)                                       # 外层：+8
    bmesh.ops.reverse_faces(bm, faces=add_cube(bm, 1.0))    # 内层完全在外层里 → 反向 = 合法空腔


# ------------------------------------------------------------------ D. 跳过 / 异常

def t_self_intersect_skipped():
    """缺陷 D-1：显式跳过自交检查不许假通过（旧代码 self_intersections=0 ⇒ clean=true）。"""
    ob = mk("skip_probe", lambda bm: add_cube(bm, 2.0))
    r, row = mesh_info(ob, self_intersect=False)
    chk("D 跳过：clean == False（回归：旧代码给 true）", r.get("clean") is False, r.get("clean"))
    chk("D 跳过：state/verdict == 'degraded'（不是 pass，也不是 fail）",
        r.get("state") == "degraded" and r.get("verdict") == "degraded", (r.get("state"), r.get("verdict")))
    chk("D 跳过：analysis.self_intersections == 'skipped'",
        (r.get("analysis") or {}).get("self_intersections") == "skipped", r.get("analysis"))
    chk("D 跳过：self_intersections_analyzed == False 且 state == 'skipped'",
        row.get("self_intersections_analyzed") is False and row.get("self_intersections_state") == "skipped",
        (row.get("self_intersections_analyzed"), row.get("self_intersections_state")))
    chk("D 跳过：totals.analysis_incomplete >= 1",
        (r.get("totals") or {}).get("analysis_incomplete", 0) >= 1, r.get("totals"))
    chk("D 跳过：带醒目 warn（DEGRADED ≠ 通过）", "DEGRADED" in (r.get("warn") or ""), r.get("warn"))
    chk("D 跳过：给出补救 hint（self_intersect=true）",
        "self_intersect=true" in (r.get("hint") or ""), r.get("hint"))
    # 但 mesh 判定字段本身仍如实：这是"没查完"，不是"查出了缺陷"
    chk("D 跳过：boundary/nonmanifold 仍为 0（只是没查完，不是诬告）",
        (row.get("boundary_edges") or 0) == 0 and (row.get("nonmanifold_edges") or 0) == 0, row)


def t_self_intersect_exception():
    """缺陷 D-2：BVH 自交检查抛异常不许假通过（旧代码只记 self_intersect_error，clean 照样 true）。"""
    import mathutils.bvhtree as bvh_mod
    real = bvh_mod.BVHTree

    class _BoomBVH(object):
        """只炸 FromBMesh（自交检查那条）；FromPolygons 仍转发真实现（壳包含性确认要用）。"""
        FromPolygons = staticmethod(real.FromPolygons)

        @staticmethod
        def FromBMesh(*a, **k):                     # noqa: N802
            raise RuntimeError("simulated BVH failure")

    ob = mk("exc_probe", lambda bm: add_cube(bm, 2.0))
    bvh_mod.BVHTree = _BoomBVH
    try:
        r, row = mesh_info(ob, self_intersect=True)
    finally:
        bvh_mod.BVHTree = real
    chk("D 异常：clean == False（回归：旧代码给 true）", r.get("clean") is False, r.get("clean"))
    chk("D 异常：state/verdict == 'degraded'", r.get("state") == "degraded" and r.get("verdict") == "degraded",
        (r.get("state"), r.get("verdict")))
    chk("D 异常：self_intersections_state == 'error' 且留了 error 原文",
        row.get("self_intersections_state") == "error" and bool(row.get("self_intersect_error")),
        (row.get("self_intersections_state"), row.get("self_intersect_error")))
    chk("D 异常：analysis_incomplete 被计入", (r.get("totals") or {}).get("analysis_incomplete", 0) >= 1,
        r.get("totals"))
    # 恢复后同一对象必须重新干净通过（证明异常路径没留下副作用）
    r2, _ = mesh_info(ob, self_intersect=True)
    chk("D 异常：恢复真 BVH 后同一对象重新 clean == True", r2.get("clean") is True,
        (r2.get("clean"), r2.get("reason")))


# ------------------------------------------------------------------ 门接线

def t_gate_wiring():
    """缺陷必须被**对应门**捕获；跳过自交时门只能是 degraded（不许被 pass_if 抬成 pass）。"""
    g = api("gate")
    mesh_gate = {"id": "mesh", "op": "audit_mesh",
                 "args": {"objects": [], "self_intersect": True, "summary_only": True},
                 "pass_if": "clean == True and totals[\"self_intersections\"] == 0"}
    healthy = mk("gate_ok", lambda bm: add_cube(bm, 2.0))
    loose = mk("gate_loose", lambda bm: (add_cube(bm, 2.0), bm.verts.new((6, 6, 6))))
    dotted = mk("gate_dot", lambda bm: _degenerate_face(bm))

    def run(obj, **over):
        gate = json.loads(json.dumps(mesh_gate))
        gate["args"]["objects"] = [obj]
        gate["args"].update(over)
        r = g("run", {"spec": {"name": "reg", "gates": [gate]}})
        return r, (r.get("gates") or [{}])[0]

    r, row = run(healthy)
    chk("门：健康对象 ⇒ verdict=pass / 门 state=pass",
        r.get("verdict") == "pass" and row.get("state") == "pass", (r.get("verdict"), row.get("state")))
    r, row = run(loose)
    chk("门：孤立点 ⇒ mesh 门 state=fail（缺陷由对应门捕获）",
        row.get("state") == "fail" and r.get("verdict") == "fail", (row.get("state"), r.get("verdict")))
    r, row = run(dotted)
    chk("门：零面积面 ⇒ mesh 门 state=fail", row.get("state") == "fail", (row.get("state"), row.get("key")))
    r, row = run(healthy, self_intersect=False)
    chk("门：跳过自交 ⇒ 门 state=degraded（pass_if 不许把它抬成 pass）",
        row.get("state") == "degraded" and r.get("verdict") == "degraded",
        (row.get("state"), r.get("verdict")))
    # 反向分离壳经门必须 fail（B 的门级复现）
    sep = mk("gate_sep", lambda bm: _sep_inv(bm))
    r, row = run(sep)
    chk("门：反向分离壳 ⇒ mesh 门 state=fail", row.get("state") == "fail", (row.get("state"), row.get("key")))


# ------------------------------------------------------------------ E. calib 捕获判据

def t_calib_capture_rule():
    """缺陷 E：error / degraded 不算捕获；缺模块时旧口径会报 8/8。"""
    c = api("calib")
    full = c("run", {})
    rows = {d.get("id"): d for d in (full.get("defects") or [])}
    chk("E 全量：baseline_pass == True", full.get("baseline_pass") is True,
        (full.get("baseline") or {}).get("gates"))
    chk("E 全量：8/8 全部被**对应门**真抓到（caught_by_expected_gate）",
        full.get("caught") == full.get("total") == 8 and full.get("missed") == []
        and all(d.get("caught") is True and d.get("caught_by_expected_gate") is True for d in rows.values()),
        {"missed": full.get("missed"),
         "rows": {k: (v.get("caught"), v.get("caught_by")) for k, v in rows.items()}})
    chk("E 全量：caught_by 只由 state=='fail' 的门组成（error/degraded 不在里面）",
        all(not (set(d.get("caught_by") or []) & set(d.get("gate_errors") or []))
            and not (set(d.get("caught_by") or []) & set(d.get("gate_degraded") or []))
            for d in rows.values()),
        {k: (v.get("caught_by"), v.get("gate_errors")) for k, v in rows.items()})
    chk("E 全量：孤立点由 mesh 门抓获（本次修的门）",
        rows.get("loose_verts", {}).get("caught_by_expected_gate") is True
        and rows.get("loose_verts", {}).get("expect") == "mesh", rows.get("loose_verts"))
    # ① 门跑不起来（pass_if 写错 ⇒ error）：不许算捕获
    br = c("run", {"ids": ["delete_face", "loose_verts"], "break_gates": ["mesh"]})
    brows = br.get("defects") or []
    chk("E 坏门：mesh 门 error 时 ok == False", br.get("ok") is False, br.get("ok"))
    chk("E 坏门：对应门 error 的缺陷一律 caught == False",
        all(x.get("caught") is not True for x in brows), [(x.get("id"), x.get("caught")) for x in brows])
    chk("E 坏门：旧口径（verdict != pass）会把这些误记成「捕获」（naive_caught=true）",
        all(x.get("naive_caught") is True for x in brows), [(x.get("id"), x.get("naive_caught")) for x in brows])
    chk("E 坏门：false_captures 如实列出被旧口径误判的用例", len(br.get("false_captures") or []) == len(brows),
        br.get("false_captures"))
    chk("E 坏门：why_missed 点明「门跑不起来 ≠ 抓到缺陷」",
        all("error" in (x.get("why_missed") or "") for x in brows), [x.get("why_missed") for x in brows])
    # ② 模块直接抽掉（原始现场：print 没注入 ⇒ 旧代码仍报 8/8）
    kern = __import__("sys").modules.get("dsh_rt_kernel")
    saved = getattr(kern, "dsh_print_api", None)
    had = hasattr(kern, "dsh_print_api")
    try:
        if had:
            delattr(kern, "dsh_print_api")
        miss = c("run", {"ids": ["thin_wall"]})
    finally:
        if had:
            kern.dsh_print_api = saved
    mrow = (miss.get("defects") or [{}])[0]
    chk("E 缺模块：print 模块不在 ⇒ thin_wall 不算被捕获（回归：旧口径报 8/8）",
        mrow.get("caught") is not True and mrow.get("naive_caught") is True, mrow)
    chk("E 缺模块：gate_errors 里点名 print", "print" in (miss.get("gate_errors") or []), miss.get("gate_errors"))
    chk("E 缺模块：baseline 判为不通过（valid_run=false）", miss.get("valid_run") is False, miss.get("baseline"))
    # ③ 分类器语义（calib 自带 selftest 也覆盖一遍）
    st = c("selftest", {})
    chk("E 自检：classifier_rejects_error_and_degraded == True",
        st.get("classifier_rejects_error_and_degraded") is True, st)
    chk("E 自检：broken_gate_not_counted_as_caught == True",
        st.get("broken_gate_not_counted_as_caught") is True, st)
    chk("E 自检：整体 ok == True", st.get("ok") is True, st)


# ------------------------------------------------------------------ F. calib 单位

def t_calib_units():
    """缺陷 F：基线与缺陷段必须同一单位口径，且跑完把场景单位复位。"""
    c = api("calib")
    us = bpy.context.scene.unit_settings
    orig = float(us.scale_length)
    # 把场景单位**故意设成非 1**，看 calib 是否：① 全段用 CALIB_SCALE ② 跑完复位到 0.5
    us.scale_length = 0.5
    try:
        r = c("run", {})
        after = float(us.scale_length)
    finally:
        us.scale_length = orig
    chk("F 单位：跑完复位到原来的 0.5（不是留在 0.001，也不是泄漏成别的值）", near(after, 0.5),
        {"before": 0.5, "after": after})
    chk("F 单位：unit_ok == True", r.get("unit_ok") is True, r.get("units"))
    units = r.get("units") or {}
    chk("F 单位：units.scene_before/after 如实回报",
        near(units.get("scene_before"), 0.5) and near(units.get("scene_after"), 0.5), units)
    chk("F 单位：每一类缺陷都在 CALIB_SCALE 下跑（口径不漂移）",
        bool(units.get("defect_scales")) and all(near(x, CALIB_SCALE) for x in units["defect_scales"]),
        units.get("defect_scales"))
    chk("F 单位：baseline 也在同一口径（baseline.unit_scale）",
        near((r.get("baseline") or {}).get("unit_scale"), CALIB_SCALE), (r.get("baseline") or {}).get("unit_scale"))
    # 每行都带 unit_scale，可逐条核
    chk("F 单位：每个 defects[] 行都带 unit_scale 且等于 CALIB_SCALE",
        all(near(d.get("unit_scale"), CALIB_SCALE) for d in (r.get("defects") or [])),
        [d.get("unit_scale") for d in (r.get("defects") or [])])
    chk("F 单位：reset 后不影响下一次跑的结论（再跑一次仍 8/8）",
        (c("run", {}).get("capture_rate") or 0) >= 1.0, None)


# ------------------------------------------------------------------ 收尾

def _purge_prefix():
    for ob in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        me = ob.data if ob.type == "MESH" else None
        bpy.data.objects.remove(ob, do_unlink=True)
        if me is not None and me.users == 0:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
    for me in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        if me.users == 0:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass


def main():
    before = len(bpy.data.objects)
    before_scale = float(bpy.context.scene.unit_settings.scale_length)
    print("== audit / calib 复审回归 ==")
    for fn in (t_healthy, t_shell_face_cap, t_loose_verts, t_inverted_separated_shell, t_legal_cavity,
               t_self_intersect_skipped, t_self_intersect_exception, t_gate_wiring,
               t_calib_capture_rule, t_calib_units):
        try:
            fn()
        except Exception as e:
            chk(fn.__name__ + " 未抛异常", False, "%s: %s" % (type(e).__name__, str(e)[:220]))
        _purge_prefix()
    chk("临时对象已清干净（场景对象数复原）", len(bpy.data.objects) == before,
        "%d -> %d" % (before, len(bpy.data.objects)))
    chk("场景单位已复原", near(bpy.context.scene.unit_settings.scale_length, before_scale),
        bpy.context.scene.unit_settings.scale_length)
    out = {"pass": len(PASS), "fail": len(FAILS), "fails": FAILS,
           "probe": "audit_review_regression"}
    print("HEADLESS " + json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    if FAILS:
        raise SystemExit(1)


main()
