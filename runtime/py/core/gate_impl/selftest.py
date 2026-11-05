# -*- coding: utf-8 -*-
"""Split from gate.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .plan import _aggregate, _collect_echo, _eval_pass_if, _path_get, _status_of, gate_plan
from .run import gate_run


def _gate_selftest_p1(st):
    try:
        import bmesh  # noqa: F401  # handed to the next split stage through st
        ev = {}
        made = []
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _gate_selftest_p2a(st):
    bmesh, ev, made = st["bmesh"], st["ev"], st["made"]
    try:
        me = bpy.data.meshes.new("__dsh_gate_selftest")
        ob = bpy.data.objects.new("__dsh_gate_selftest", me)
        bpy.context.scene.collection.objects.link(ob)
        made.append(ob.name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        spec = {"name": "selftest", "units": "mm", "tolerance_mm": 1.0, "gates": [
            {"id": "ok_gate", "op": "audit_mesh", "args": {"objects": [ob.name]}, "pass_if": "clean == True"},
            {"id": "bad_gate", "op": "audit_mesh", "args": {"objects": [ob.name]}, "pass_if": "boundary_edges > 999999"},
        ]}
        plan = json.loads(gate_plan(spec=spec))
        ev["plan"] = {"ok": plan.get("ok"), "gates": plan.get("gate_count"),
                      "reads": (plan.get("gates") or [{}])[0].get("reads")}
        r = json.loads(gate_run(spec=spec))
        ev["run"] = {"verdict": r.get("verdict"), "ok": r.get("ok"), "failed": r.get("failed"),
                     "states": [g.get("state") for g in r.get("gates", [])],
                     "key0": (r.get("gates") or [{}])[0].get("key")}
        ok1 = (plan.get("ok") and r.get("verdict") == "fail" and r.get("ok") is False
               and r.get("failed") == ["bad_gate"] and r["gates"][0]["state"] == "pass")
        # 全过的包 ⇒ verdict=pass、ok=true
        spec2 = {"name": "selftest-pass", "gates": [
            {"id": "a", "op": "audit_mesh", "args": {"objects": [ob.name]}, "pass_if": "clean == True"}]}
        r2 = json.loads(gate_run(spec=spec2))
        ev["pass_case"] = {"verdict": r2.get("verdict"), "ok": r2.get("ok")}
        # 三态规则直接单测（不靠"凑一个真降级的场景"）：ok=null ⇒ degraded，degraded 不能算过
        ev["tri_state_unit"] = {
            "degraded": _status_of({"ok": None, "state": "degraded"}),
            "fail": _status_of({"ok": False}),
            "pass": _status_of({"ok": True, "clean": True}),
            "error": _status_of("not-a-dict"),
            "agg_degraded": _aggregate(["pass", "degraded"]),
            "agg_fail_beats_degraded": _aggregate(["degraded", "fail"]),
            "agg_all_pass": _aggregate(["pass", "pass"]),
        }
        u = ev["tri_state_unit"]
        ev["tri_state_ok"] = (u["degraded"] == "degraded" and u["fail"] == "fail" and u["pass"] == "pass"
                              and u["error"] == "error" and u["agg_degraded"] == "degraded"
                              and u["agg_fail_beats_degraded"] == "fail" and u["agg_all_pass"] == "pass")
        # v0.9.6单元级：**嵌套三态**（audit_connectivity 的门结论在 gate 里）+ **skipped 汇总（v3 收紧）**
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _gate_selftest_p2b(st):
    ev = st["ev"]
    try:
        ev["nested_tri_unit"] = {
            "conn_pass": _status_of({"ok": True, "analyzed": True, "gate": {"ok": True, "state": "pass", "verdict": "pass"}}),
            "conn_fail": _status_of({"ok": True, "analyzed": True, "gate": {"ok": False, "state": "fail", "verdict": "fail"}}),
            "conn_unanalyzed": _status_of({"ok": True, "analyzed": False,
                                           "gate": {"ok": None, "state": "degraded", "verdict": "degraded"}}),
            "inter_unresolved": _status_of({"ok": True, "analyzed": True, "verdict": "unresolved"}),
            "inter_refuted_is_pass": _status_of({"ok": True, "analyzed": True, "verdict": "refuted"}),
            # v3（Lead 否决 pass+skipped=pass）：任一 skipped ⇒ 顶层 degraded
            "agg_pass_skipped": _aggregate(["pass", "skipped"]),
            "agg_only_skipped": _aggregate(["skipped"]),
            "agg_degraded_skipped": _aggregate(["degraded", "skipped"]),
            "agg_fail_skipped": _aggregate(["fail", "skipped"]),
            "agg_fail_skipped_degraded": _aggregate(["skipped", "degraded", "fail", "pass"]),
            "agg_empty": _aggregate([]),
        }
        n = ev["nested_tri_unit"]
        ev["nested_tri_ok"] = (n["conn_pass"] == "pass" and n["conn_fail"] == "fail"
                               and n["conn_unanalyzed"] == "degraded" and n["inter_unresolved"] == "degraded"
                               and n["inter_refuted_is_pass"] == "pass"
                               and n["agg_pass_skipped"] == "degraded" and n["agg_only_skipped"] == "degraded"
                               and n["agg_degraded_skipped"] == "degraded" and n["agg_fail_skipped"] == "fail"
                               and n["agg_fail_skipped_degraded"] == "fail" and n["agg_empty"] == "degraded")
        # v3单元级：degrade_if —— 判据为真但证据不可信 ⇒ degraded（不许假通过）。
        # 直接用 **print preset 的真实表达式** 跑合成回执：分辨率 1000mm vs min_mm 2mm ⇒ 命中；
        # 分辨率 0.1mm ⇒ 不命中（"只有当分辨率不可信时才降级"）。echo 走点路径取真实字段。
        _preset_print = PRESETS["print"]["gates"][0]
        _dg_expr = _preset_print["degrade_if"]
        _coarse = {"warnings": [], "params": {"min_mm": 2.0},
                   "detail": {"walls": {"objects": [{"resolution_mm": 1000.0}]}}}
        _fine = {"warnings": [], "params": {"min_mm": 2.0},
                 "detail": {"walls": {"objects": [{"resolution_mm": 0.1}]}}}
        # v1.0.5（issue #14）：悬垂一侧的 bed_warning 汇总在顶层 warnings[] —— 非空也必须降级
        _warned = {"warnings": ["贴床面标高 0.5 比集合最低点 0.0 高 0.5 mm"], "params": {"min_mm": 2.0},
                   "detail": {"walls": {"objects": [{"resolution_mm": 0.1}]}}}
        ev["degrade_if_unit"] = {
            "expr": _dg_expr,
            "coarse_hits": _eval_pass_if(_dg_expr, _coarse),
            "fine_hits": _eval_pass_if(_dg_expr, _fine),
            "warned_hits": _eval_pass_if(_dg_expr, _warned),
            "path_ok": _path_get(_coarse, "detail.walls.objects.0.resolution_mm") == (1000.0, True),
            "path_missing": _path_get(_coarse, "detail.walls.objects.9.resolution_mm")[1] is False,
            "echo": _collect_echo(_coarse, _preset_print.get("echo")),
            "plan_echo": (json.loads(gate_plan(preset="print"))["gates"][0] or {}).get("echo"),
        }
        ev["degrade_if_ok"] = bool(ev["degrade_if_unit"]["coarse_hits"] is True
                                   and ev["degrade_if_unit"]["fine_hits"] is False
                                   and ev["degrade_if_unit"]["warned_hits"] is True
                                   and ev["degrade_if_unit"]["path_ok"]
                                   and ev["degrade_if_unit"]["path_missing"]
                                   and ev["degrade_if_unit"]["echo"].get("params.min_mm") == 2.0
                                   and "warnings" in (ev["degrade_if_unit"]["plan_echo"] or [])
                                   and ev["degrade_if_unit"]["plan_echo"])
        # preset 静态契约（v3 的跳过/降级字段）：门还在、各自读的字段是真的、没来源的门会 skipped
        pp = json.loads(gate_plan(preset="assembly"))
        _by = {x["id"]: x for x in (pp.get("gates") or [])}
        ev["preset_assembly"] = {
            "gate_count": pp.get("gate_count"),
            "mesh_self_intersect": (_by.get("mesh_health", {}).get("args") or {}).get("self_intersect"),
            "no_floaters_reads": _by.get("no_floaters", {}).get("reads"),
            "no_clash_reads": _by.get("no_clash", {}).get("reads"),
            "no_clash_skip_groups": len(_by.get("no_clash", {}).get("skip_if_missing") or []),
        }
        pa = ev["preset_assembly"]
        ev["preset_ok"] = (pa["gate_count"] == 3 and pa["mesh_self_intersect"] is True
                           and pa["no_floaters_reads"] == ["gate"] and pa["no_clash_reads"] == ["verdict"]
                           and pa["no_clash_skip_groups"] == 2)
        # v3：print preset 的分辨率降级必须在**真实回执**上成立（不是合成回执）
        # 探针立方体 1 单位 = 1000mm：面尺寸 1 单位 ⇒ resolution_mm=1000 ≫ min_mm=2 ⇒ 该门 degraded；
        # 判据本身为真（thin_samples_total==0 且 walls_ok）—— 这正是旧口径会假通过的地方。
        r5 = json.loads(gate_run(preset="print", overrides={"walls": {"mm_per_unit": 1000.0}}, detail=True))
        g5 = (r5.get("gates") or [{}])[0]
        _echo5 = g5.get("echo") or {}
        _rec5 = (g5.get("receipt") or {}).get("detail", {}).get("walls", {}).get("objects", [{}])
        ev["print_resolution_case"] = {
            "state": g5.get("state"), "verdict": r5.get("verdict"), "ok": r5.get("ok"),
            "pass_if_value": g5.get("pass_if_value"),
            "resolution_mm": _echo5.get("detail.walls.objects.0.resolution_mm"),
            "min_mm": _echo5.get("params.min_mm"),
            "receipt_resolution_mm": (_rec5[0] or {}).get("resolution_mm") if _rec5 else None,
        }
        p5 = ev["print_resolution_case"]
        ev["print_resolution_ok"] = bool(p5["state"] == "degraded" and p5["verdict"] == "degraded"
                                        and p5["ok"] is False and p5["pass_if_value"] is True
                                        and p5["resolution_mm"] == p5["receipt_resolution_mm"]
                                        and (p5["resolution_mm"] or 0) > (p5["min_mm"] or 0))
        # v1.0.5（issue #14）**真机**：悬垂一侧的「平台面判不准」也必须进 warnings 并让门不绿。
        # 探针 = 绕 X 倾斜 45° 的立方体：它**没有水平朝下的面** ⇒ print_overhang 走 min-fallback 并写
        # bed_warning ⇒ print_report 顶层 warnings 非空、echo 也带出来。
        # 注：这条只证「warnings 确实产生 + 确实被 echo + 门确实 degraded」；「仅凭 warnings 就能降级」
        # 由上面的合成回执 _warned 单独锁（那条的分辨率是 0.1mm ⇒ 排除分辨率因素）。
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _gate_selftest_p2c(st):
    bmesh, ev, made, ob, ok1 = st["bmesh"], st["ev"], st["made"], st["ob"], st["ok1"]
    r2 = st["r2"]
    try:
        import mathutils as _mu
        tilt_me = bpy.data.meshes.new("__dsh_gate_selftest_tilt")
        tilt_ob = bpy.data.objects.new("__dsh_gate_selftest_tilt", tilt_me)
        bpy.context.scene.collection.objects.link(tilt_ob)
        made.append(tilt_ob.name)
        tbm = bmesh.new()
        bmesh.ops.create_cube(tbm, size=1.0)
        bmesh.ops.rotate(tbm, cent=(0.0, 0.0, 0.0), verts=list(tbm.verts),
                         matrix=_mu.Matrix.Rotation(0.7853981633974483, 3, "X"))   # 45°
        tbm.to_mesh(tilt_me)
        tbm.free()
        bpy.context.view_layer.objects.active = tilt_ob
        r6 = json.loads(gate_run(preset="print", overrides={"walls": {"min_mm": 900.0, "mm_per_unit": 1000.0}},
                                 detail=True))
        g6 = (r6.get("gates") or [{}])[0]
        _echo6 = g6.get("echo") or {}
        _recv6 = g6.get("receipt") or {}
        ev["print_bed_warning_case"] = {
            "state": g6.get("state"), "verdict": r6.get("verdict"), "ok": r6.get("ok"),
            "pass_if_value": g6.get("pass_if_value"),
            "warnings_n": len(_recv6.get("warnings") or []),
            "echo_warnings_n": len(_echo6.get("warnings") or []),
            "bed_mode": _echo6.get("params.bed_mode"),
            "thin_total": _recv6.get("thin_samples_total"),
            # 门侧回执是裁剪过的（只留 reads/echo 引用到的字段）；warnings 因被 degrade_if 引用而保留 ——
            # 这正是「门读得到这条警告」的证据；objects[] 明细不在裁剪回执里，别去那里找。
            "receipt_has_objects": "objects" in _recv6,
        }
        p6 = ev["print_bed_warning_case"]
        ev["print_bed_warning_ok"] = bool(p6["state"] == "degraded" and p6["verdict"] == "degraded"
                                          and p6["ok"] is False and p6["pass_if_value"] is True
                                          and p6["warnings_n"] >= 1 and p6["echo_warnings_n"] >= 1
                                          and p6["bed_mode"] == "min-fallback"
                                          and p6["thin_total"] == 0)
        # 未配置来源 ⇒ skipped（不是 error）；全部门都 skipped ⇒ degraded
        r4 = json.loads(gate_run(spec={"name": "skip", "gates": [
            {"id": "c", "op": "audit_interference", "args": {},
             "skip_if_missing": [["a", "objects_a", "file_a"], ["b", "objects_b", "file_b"]],
             "pass_if": "verdict == 'refuted'"}]}))
        g4 = (r4.get("gates") or [{}])[0]
        ev["skip_case"] = {"state": g4.get("state"), "verdict": r4.get("verdict"),
                           "skipped": r4.get("skipped"), "coverage": r4.get("coverage")}
        ev["skip_ok"] = bool(g4.get("state") == "skipped" and r4.get("verdict") == "degraded"
                             and r4.get("skipped") == ["c"]
                             and (r4.get("coverage") or {}).get("all_gates_ran") is False)
        # 不认识 op / 引用不存在的字段 ⇒ 明确报错（不是静默 pass）
        err = None
        try:
            gate_run(spec={"name": "e", "gates": [{"id": "x", "op": "not_an_op", "pass_if": ""}]})
        except Exception as e:
            err = str(e)[:80]
        ev["unknown_op_error"] = err
        # 引用不存在的字段：**不抛异常**，而是该门判 error 并说明缺哪个字段（比抛异常更好用）
        r3 = json.loads(gate_run(spec={"name": "e2", "gates": [
            {"id": "y", "op": "audit_mesh", "args": {"objects": [ob.name]}, "pass_if": "nope_field == 1"}]}))
        g3 = (r3.get("gates") or [{}])[0]
        ev["bad_field"] = {"state": g3.get("state"), "error": (g3.get("error") or "")[:90], "verdict": r3.get("verdict")}
        ok = bool(ok1 and r2.get("verdict") == "pass" and r2.get("ok") is True and ev.get("tri_state_ok")
                  and ev.get("nested_tri_ok") and ev.get("preset_ok") and ev.get("skip_ok")
                  and ev.get("degrade_if_ok") and ev.get("print_resolution_ok")
                  and ev.get("print_bed_warning_ok")
                  and ev.get("unknown_op_error")
                  and (ev.get("bad_field") or {}).get("state") == "error"
                  and "没有的字段" in ((ev.get("bad_field") or {}).get("error") or ""))
        return _j({"ok": ok, "evidence": ev})
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def gate_selftest():
    """自检：造一个"该过"的场景 + 一个"该挂"的门，断言三态判定与 pass_if/degrade_if/skipped 语义都对。

    v3 新增断言：① 任一 skipped/degraded ⇒ 顶层 degraded（pass+skipped 不再算 pass）；
    ② print preset 的 degrade_if（真实字段：min_mm vs resolution_mm）在真机回执上把"判据为真但分辨率
    不可信"的门判成 degraded，且 echo 与 receipt 里的真实值一致。
    """
    st = dict(locals())
    _gate_selftest_p1(st)
    try:
        for _fn_ in (_gate_selftest_p2a, _gate_selftest_p2b, _gate_selftest_p2c):
            _r = _fn_(st)
            if _r is not None:
                return _r
    except Exception as e:
        return _j({"ok": False, "evidence": st.get("ev"),
                   "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        for nm in (st.get("made") or []):
            o = bpy.data.objects.get(nm)
            if o is not None:
                m = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                try:
                    bpy.data.meshes.remove(m, do_unlink=True)
                except Exception:
                    pass



def gate_help():
    return _j({
        "module": "gate.py", "version": GATE_VERSION,
        "what": "规格驱动门包：一份 spec 跑完所有门，三态判定（pass/fail/degraded；未配置来源的门记 skipped）。"
                "**任一 skipped 或 degraded 都会把顶层压成 degraded**（fail 优先）—— 跳过 ≠ 通过，"
                "只有每门都真跑过且都过才 pass；degrade_if 命中（证据不可信，如分辨率不足）同样判 degraded。"
                "回执只带判据字段 + echo 的取证字段",
        "ops": ["gate_plan（只读，列门）", "gate_run（执行；overrides={门id:{args…}} 可给 preset 补现场参数）",
                "gate_selftest", "gate_help"],
        "spec": {"name": "包名", "units": "mm", "tolerance_mm": 3.0,
                 "gates": [{"id": "门名", "op": "audit_scene|audit_mesh|audit_connectivity|audit_interference|"
                                             "print_report|uv_stats|material_scan|fix_repair|audit_drift|deliver_verify…",
                            "args": "该 op 的参数", "pass_if": "受限表达式，例如 clean == True",
                            "skip_if_missing": "[可选] [[候选键…], …]：每组一个都没给 ⇒ 该门记 skipped（未跑；"
                                                "v3 起 skipped 会把顶层压成 degraded）",
                            "degrade_if": "[可选] 受限表达式：判据为真但证据不可信 ⇒ 该门 degraded（不许假通过）",
                            "echo": "[可选] [点路径…]：把这些真实字段（如 detail.walls.objects.0.resolution_mm）"
                                    "取进回执做证据"}]},
        "spec_sources": ["内联 dict", ".json 文件", ".py 模块（SPEC = {...} 或 NAME/UNITS/TOLERANCE_MM/GATES）", "preset"],
        "presets": PRESETS,
        "pass_if_examples": {
            "audit_scene": "args={summary_only:true, top_k:5, self_intersect:true} → pass_if=\"clean == True\""
                           "（必须显式 self_intersect:true：默认跳过自交检查会如实给 degraded/clean=false）",
            "audit_connectivity": "pass_if=\"gate['verdict'] == 'pass'\" —— 顶层没有 state/verdict，门结论在"
                                  "嵌套 gate 里；未分析/未确认 ⇒ degraded（不许抬成 pass）",
            "audit_interference": "args={objects_a:[…], objects_b:[…]} → pass_if=\"verdict == 'refuted'\""
                                  "（refuted=可证无干涉 / supported=真干涉 / unresolved=证据不足→degraded）；"
                                  "两两算子，没给两侧来源时该门 skipped（v3：skipped ⇒ 顶层 degraded）",
            "print_report": "pass_if=\"thin_samples_total == 0 and walls_ok == True\" **且** "
                            "degrade_if=\"params['min_mm'] < detail['walls']['objects'][0]['resolution_mm']\""
                            "（分辨率口径在 per-object 的 detail.walls.objects[].warning/resolution_mm，顶层没有"
                            "这个字段）—— min_mm 低于测量分辨率时该门判 degraded，不许把'没量到'读成绿；"
                            "echo=[\"detail.walls.objects.0.resolution_mm\", …] 把真实读数带进回执",
            "uv_stats": "pass_if=\"ok == True\"（明细在 objects[].degenerate_uv_faces）",
        },
        "rules": ["verdict 三态，ok 只在 pass 时为 true（degraded ≠ 通过）",
                  "**任一 skipped 或 degraded ⇒ 顶层 degraded（fail 优先）**：跳过 ≠ 通过，不许为"
                  "「健康默认 preset」把标准降下来；要 pass 必须每门都真跑过且都过",
                  "三态可能嵌在回执的 gate 里（audit_connectivity）：先读顶层，再读嵌套 gate/connectivity",
                  "pass_if / degrade_if 只允许比较/布尔/算术/属性与 min·max·len·abs·round·all·any·sum",
                  "引用了回执里不存在的字段 ⇒ 报错并列出可用字段（不静默）",
                  "degrade_if 命中（判据为真但证据不可信，如 min_mm < 测量分辨率）⇒ 该门 degraded，"
                  "echo 里的真实字段就是降级证据",
                  "声明 skip_if_missing 的门未配置来源时记 skipped：列在 skipped/coverage 里，"
                  "并把顶层压成 degraded",
                  "回执默认只回 pass_if 用到的字段 + 失败时的 reason/error + echo 的取证字段"],
        "why": "现场为一次装配手写了 interference / redcheck / selfint_diag ≈600 行；这些应该由插件负责执行与取证",
    })


