# -*- coding: utf-8 -*-
"""Native E2E: planner S3 (object graph -> diagnostics -> compile -> graph).

Ported from the archived cases/plan_selftest.py.  Preload is the same as the
legacy wrapper (view); planner and contract are loaded explicitly by load_flat
like the archived case.  The only intentional harness change is the output
directory: it is ctx.test_dir, never the repo root.
"""

from __future__ import annotations

import json

PLAN = {
    "id": "chair-plan",
    "envelope": {"min": [-0.5, -0.5, 0.0], "max": [0.5, 0.6, 1.5]},
    "components": [
        {"id": "seat", "kind": "box", "params": {"size": [0.45, 0.45, 0.05], "loc": [0, 0, 0.475]},
         "range": {"size": [0.3, 0.6]}},
        {"id": "backrest", "kind": "box", "params": {"size": [0.45, 0.03, 0.50], "loc": [0, 0.25, 0.70]}},
        {"id": "tenon", "kind": "box", "params": {"size": [0.10, 0.06, 0.06], "loc": [0, 0.195, 0.465]},
         "hidden_when": {"connection": "joint", "candidate": "insert"}}
    ],
    "connections": [
        {"id": "joint", "a": "seat", "b": "backrest", "candidates": ["attach", "insert"],
         "candidate": "insert", "status": "proposed", "forbidden": ["boolean_union", "weld"],
         "offset": {"translate": [0.0, 0.01, 0.0]}}
    ],
    "features": [
        {"id": "slats", "kind": "array", "src": "backrest", "count": 2, "offset": [0, 0, 0.06],
         "depends_on": ["backrest", "joint"]}
    ],
}


def test_planner_flow(ctx):
    ctx.load_flat("planner.py")
    if ctx.K is not None and not hasattr(ctx.K, "dsh_contract_api"):
        ctx.load_flat("contract.py")
    api = ctx.api("plan")
    ctx.check("plan version is 1", api["version"] == 1, api.get("version"))

    r = ctx.call(api, "load", PLAN)
    codes = [d["code"] for d in r["diagnostics"]]
    ctx.check("load 成功并出诊断", r.get("ok") and r["counts"]["components"] == 3, r)
    ctx.check("未判别连接 -> UnresolvedConnection", "UnresolvedConnection" in codes, codes)

    r = ctx.call(api, "status")
    ctx.check("status 汇总正常", r["counts"]["features"] == 1 and r["loaded"], r)

    r = ctx.call(api, "order")
    kinds = [o["kind"] for o in r["order"]]
    ctx.check(
        "拓扑顺序：组件 -> 连接 -> 特征",
        kinds.index("component") < kinds.index("connection") < kinds.index("feature"),
        kinds,
    )
    ctx.check("没有阻塞节点", not r["blocked"], r["blocked"])

    r = ctx.call(api, "build", dry_run=True)
    ctx.check("dry_run 只出计划", r.get("ok") and r.get("dry_run") and ctx.K is not None
              and __import__("bpy").data.objects.get("PLAN_seat") is None, r)

    r = ctx.call(api, "build", dry_run=False)
    ctx.check("编译出组件对象",
              __import__("bpy").data.objects.get("PLAN_seat") is not None
              and __import__("bpy").data.objects.get("PLAN_backrest") is not None, r)
    ctx.check("hidden_when(insert) -> 榫存在", __import__("bpy").data.objects.get("PLAN_tenon") is not None)
    ctx.check("特征展开 2 个",
              all(__import__("bpy").data.objects.get("PLAN_slats_%d" % i) for i in (1, 2)))
    backrest = __import__("bpy").data.objects["PLAN_backrest"]
    ctx.check("连接偏移已应用（backrest y +0.01）",
              abs(backrest.location.y - 0.26) < 1e-6, backrest.location.y)

    plan2 = json.loads(json.dumps(PLAN))
    plan2["connections"][0]["candidate"] = "attach"
    plan2["connections"][0]["status"] = "supported"
    r = ctx.call(api, "load", plan2)
    codes2 = [d["code"] for d in r["diagnostics"]]
    ctx.check("连接判为 supported 后不再有 UnresolvedConnection", "UnresolvedConnection" not in codes2, codes2)
    ctx.call(api, "build", dry_run=False)
    ctx.check("hidden_when(attach) -> 榫不生成", __import__("bpy").data.objects.get("PLAN_tenon") is None)

    bad = json.loads(json.dumps(PLAN))
    bad["components"].append({"id": "ghost", "kind": "box", "params": {"size": [9, 9, 9]},
                              "range": {"size": [1, 2]}})
    r = ctx.call(api, "load", bad)
    codes3 = [d["code"] for d in r["diagnostics"]]
    ctx.check("超区间 -> ParamOutOfRange", "ParamOutOfRange" in codes3, codes3)
    ctx.check("硬错误会拦编译",
              ctx.call(api, "build", dry_run=True).get("blocked") is True or "ParamOutOfRange" in codes3)

    cyc = {"id": "cyc", "components": [{"id": "a", "kind": "box"}],
           "features": [{"id": "f1", "kind": "array", "src": "a", "depends_on": ["f2"]},
                        {"id": "f2", "kind": "array", "src": "a", "depends_on": ["f1"]}]}
    r = ctx.call(api, "load", cyc)
    codes4 = [d["code"] for d in r["diagnostics"]]
    ctx.check("依赖成环 -> Cycle", "Cycle" in codes4, codes4)

    ctx.call(api, "load", PLAN)
    g = ctx.call(api, "graph", "mermaid")
    ctx.check("mermaid 图包含连接与特征", "joint" in g["text"] and "slats" in g["text"])
    d = ctx.call(api, "graph", "dot")
    ctx.check("dot 图包含节点", "digraph" in d["text"] and "seat" in d["text"])
    graph_path = ctx.test_dir / "plan_graph.mmd"
    graph_path.write_text(g["text"], encoding="utf-8")
    ctx.check("plan_graph.mmd 写入 ctx.test_dir", graph_path.is_file() and graph_path.stat().st_size > 0,
              str(graph_path))

    r = ctx.call(api, "load", json.dumps(PLAN))
    r = ctx.call(api, "build", dry_run=False)
    ctx.check("编译时按 plan.envelope 建包络（与契约层联动）", r.get("envelope_check") is not None, r)
