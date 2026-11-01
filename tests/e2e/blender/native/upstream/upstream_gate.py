# -*- coding: utf-8 -*-
"""上游整合 A4 规格驱动门（原 upstream_integration_selftest.py 拆分）.

Split from the archived upstream_integration_selftest.py by T11a; T11b.8 runs it through
the native runner. The support module binds K and the original helper names to ctx.
"""
from __future__ import annotations

import json
import math

import bmesh
import bpy

import support as U



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



def test_gate(ctx):
    U.install(globals(), ctx)
    U.run([t_gate], "上游整合 A4 规格驱动门（原 upstream_integration_selftest.py 拆分）")
