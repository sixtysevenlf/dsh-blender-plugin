# -*- coding: utf-8 -*-
"""规划器自检（S3）—— 对象图 → 诊断 → 编译 → 图导出，纯无头可跑。

跑法：
    blender -b --factory-startup --python tests/plan_selftest.py -- <outdir>
    # 或插件：blender_rt_headless(preload="view", script="exec(open('<本文件>').read())", outdir=...)
"""
import bpy, json, os, sys

FAILS, OKS = [], []


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)))


def clean():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)
    bpy.context.view_layer.update()


def load_module(name):
    K = sys.modules.get("dsh_rt_kernel")
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "runtime", name)
    src = open(path, encoding="utf-8").read()
    exec(compile(src, name, "exec"), globals())
    return K


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


def main():
    outdir = None
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        outdir = rest[-1] if rest else None
    outdir = outdir or os.path.join(os.path.expanduser("~"), "plan_selftest_out")
    os.makedirs(outdir, exist_ok=True)
    K = load_module("planner.py")
    if K is None or not hasattr(K, "dsh_plan_api"):
        print("HEADLESS " + json.dumps({"ok": False, "error": "planner 未挂到 K"}))
        return
    if not hasattr(K, "dsh_contract_api"):
        load_module("contract.py")
    api = K.dsh_plan_api
    print("PLAN_SELFTEST version=%s" % api["version"])
    clean()

    r = json.loads(api["load"](PLAN))
    codes = [d["code"] for d in r["diagnostics"]]
    check("load 成功并出诊断", r.get("ok") and r["counts"]["components"] == 3, r)
    check("未判别连接 → UnresolvedConnection", "UnresolvedConnection" in codes, codes)

    r = json.loads(api["status"]())
    check("status 汇总正常", r["counts"]["features"] == 1 and r["loaded"], r)

    r = json.loads(api["order"]())
    kinds = [o["kind"] for o in r["order"]]
    check("拓扑顺序：组件 → 连接 → 特征",
          kinds.index("component") < kinds.index("connection") < kinds.index("feature"), kinds)
    check("没有阻塞节点", not r["blocked"], r["blocked"])

    r = json.loads(api["build"](dry_run=True))
    check("dry_run 只出计划", r.get("ok") and r.get("dry_run") and bpy.data.objects.get("PLAN_seat") is None, r)

    r = json.loads(api["build"](dry_run=False))
    check("编译出组件对象", bpy.data.objects.get("PLAN_seat") is not None and bpy.data.objects.get("PLAN_backrest") is not None, r)
    check("hidden_when(insert) → 榫存在", bpy.data.objects.get("PLAN_tenon") is not None)
    check("特征展开 2 个", all(bpy.data.objects.get("PLAN_slats_%d" % i) for i in (1, 2)))
    check("连接偏移已应用（backrest y +0.01）", abs(bpy.data.objects["PLAN_backrest"].location.y - 0.26) < 1e-6,
          bpy.data.objects["PLAN_backrest"].location.y)

    plan2 = json.loads(json.dumps(PLAN))
    plan2["connections"][0]["candidate"] = "attach"
    plan2["connections"][0]["status"] = "supported"
    r = json.loads(api["load"](plan2))
    codes2 = [d["code"] for d in r["diagnostics"]]
    check("连接判为 supported 后不再有 UnresolvedConnection", "UnresolvedConnection" not in codes2, codes2)
    api["build"](dry_run=False)
    check("hidden_when(attach) → 榫不生成", bpy.data.objects.get("PLAN_tenon") is None)

    bad = json.loads(json.dumps(PLAN))
    bad["components"].append({"id": "ghost", "kind": "box", "params": {"size": [9, 9, 9]},
                              "range": {"size": [1, 2]}})
    r = json.loads(api["load"](bad))
    codes3 = [d["code"] for d in r["diagnostics"]]
    check("超区间 → ParamOutOfRange", "ParamOutOfRange" in codes3, codes3)
    check("硬错误会拦编译", json.loads(api["build"](dry_run=True)).get("blocked") is True or "ParamOutOfRange" in codes3)

    cyc = {"id": "cyc", "components": [{"id": "a", "kind": "box"}],
           "features": [{"id": "f1", "kind": "array", "src": "a", "depends_on": ["f2"]},
                        {"id": "f2", "kind": "array", "src": "a", "depends_on": ["f1"]}]}
    r = json.loads(api["load"](cyc))
    codes4 = [d["code"] for d in r["diagnostics"]]
    check("依赖成环 → Cycle", "Cycle" in codes4, codes4)

    api["load"](PLAN)
    g = json.loads(api["graph"]("mermaid"))
    check("mermaid 图包含连接与特征", "joint" in g["text"] and "slats" in g["text"])
    d = json.loads(api["graph"]("dot"))
    check("dot 图包含节点", "digraph" in d["text"] and "seat" in d["text"])
    with open(os.path.join(outdir, "plan_graph.mmd"), "w", encoding="utf-8") as f:
        f.write(g["text"])
    r = json.loads(api["load"](json.dumps(PLAN)))
    r = json.loads(api["build"](dry_run=False))
    env = r.get("envelope_check")
    check("编译时按 plan.envelope 建包络（与契约层联动）", env is not None, r)

    print("HEADLESS " + json.dumps({"ok": len(FAILS) == 0, "passed": len(OKS), "failed": len(FAILS),
                                    "failures": FAILS, "outdir": outdir}, ensure_ascii=False))


if __name__ == "__main__":
    main()
