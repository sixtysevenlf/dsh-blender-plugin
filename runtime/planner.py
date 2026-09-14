# -*- coding: utf-8 -*-
"""DSH 规划器（S3）—— 对象图（Component / Connection / Feature）+ 编译到 bpy + 依赖诊断。

API 挂在持久内核上：K.dsh_plan_api。设计口径（对应 issue #3 的"规划器"）：
    * Planner 只操作**通用对象**：Component（组件）/ Connection（连接）/ Feature（特征）；
    * 具体 Blender 操作由**对象图编译下来**（本模块的 build()）；
    * 后台按**对象契约**报错，像 IDE 一样给出诊断码：
        MissingComponent / MissingObject / UnresolvedConnection /
        UnsupportedDestructiveMerge / Cycle / ParamOutOfRange
    * 图可导出（mermaid / dot），便于人看与存档。

plan 结构（JSON）：
{
  "id": "chair",
  "envelope": {"min": [-0.5,-0.5,0], "max": [0.5,0.5,1.5]},        # 可选：编译后自动建包络
  "components": [
    {"id":"seat","kind":"box","params":{"size":[0.45,0.45,0.05],"loc":[0,0,0.475]},
     "tags":["seat"],"range":{"size":[0.3,0.6]}},
    {"id":"backrest","kind":"box","params":{"size":[0.45,0.03,0.50],"loc":[0,0.25,0.70]}},
    {"id":"tenon","kind":"box","params":{"size":[0.10,0.06,0.06],"loc":[0,0.195,0.465]},
     "hidden_when":{"connection":"joint","candidate":"attach"}}      # 只在某候选下存在
  ],
  "connections": [
    {"id":"joint","a":"seat","b":"backrest","candidates":["attach","insert"],
     "candidate":"insert","status":"unresolved","forbidden":["boolean_union","weld"],
     "offset":{"translate":[0,0.01,0]}}
  ],
  "features": [
    {"id":"slats","kind":"array","src":"backrest","count":3,"offset":[0,0,0.06],
     "depends_on":["backrest","joint"]}
  ]
}
"""
import bpy, json, math, os, time

PLAN_VERSION = 1


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _store():
    K = _kernel()
    if K is None:
        raise RuntimeError("需要持久内核 K（走 blender_rt_* 通道）")
    if not hasattr(K, "dsh_plan"):
        K.dsh_plan = {"plan": None, "build": None, "log": []}
    return K.dsh_plan


def _log(action, detail):
    st = _store()
    st["log"].append({"t": time.strftime("%H:%M:%S"), "action": action, "detail": detail})
    del st["log"][:-200]


# ---------------------------------------------------------------- 载入 / 校验

def p_load(plan):
    if isinstance(plan, str):
        s = plan.strip()
        if s.startswith("{"):
            plan = json.loads(s)
        else:
            with open(s, encoding="utf-8") as f:
                plan = json.load(f)
    if not isinstance(plan, dict):
        return _j({"ok": False, "error": "plan 必须是 JSON 对象或路径"})
    comps = {c["id"]: c for c in plan.get("components", []) if isinstance(c, dict) and c.get("id")}
    conns = {c["id"]: c for c in plan.get("connections", []) if isinstance(c, dict) and c.get("id")}
    feats = {f["id"]: f for f in plan.get("features", []) if isinstance(f, dict) and f.get("id")}
    st = _store()
    st["plan"] = {"id": plan.get("id", "plan"), "envelope": plan.get("envelope"),
                  "components": comps, "connections": conns, "features": feats,
                  "raw": plan, "loaded": time.strftime("%Y-%m-%d %H:%M:%S")}
    _log("load", {"id": st["plan"]["id"], "components": len(comps), "connections": len(conns), "features": len(feats)})
    d = json.loads(p_diag())
    return _j({"ok": True, "id": st["plan"]["id"], "counts": {"components": len(comps), "connections": len(conns),
                                                              "features": len(feats)},
               "diagnostics": d["diagnostics"]})


def p_diag():
    st = _store()
    pl = st["plan"]
    if not pl:
        return _j({"ok": False, "error": "还没载入 plan（先 op=load）", "diagnostics": []})
    comps, conns, feats = pl["components"], pl["connections"], pl["features"]
    D = []

    def add(code, msg, target=None, fix=None):
        D.append({"code": code, "message": msg, "target": target, "fix": fix})

    for cid, c in comps.items():
        kind = c.get("kind", "box")
        if kind not in ("box", "cylinder", "sphere", "mesh_copy"):
            add("UnknownKind", "组件 %s 的 kind=%s 不支持" % (cid, kind), cid, "box/cylinder/sphere/mesh_copy")
        rng = (c.get("range") or {}).get("size")
        sz = (c.get("params") or {}).get("size")
        if rng and sz:
            for v in sz:
                if v < rng[0] or v > rng[1]:
                    add("ParamOutOfRange", "组件 %s 的 size=%s 超出声明区间 %s" % (cid, sz, rng), cid, "改小/放大到区间内")
        if kind == "mesh_copy" and not (c.get("params") or {}).get("src"):
            add("MissingComponent", "mesh_copy 组件 %s 缺 params.src" % cid, cid, "指定源对象名")
    for k, cn in conns.items():
        for side in ("a", "b"):
            if cn.get(side) not in comps:
                add("MissingComponent", "连接 %s 的 %s=%s 未定义" % (k, side, cn.get(side)), k, "补组件或改引用")
        if cn.get("candidate") and cn.get("candidates") and cn["candidate"] not in cn["candidates"]:
            add("MissingComponent", "连接 %s 的 candidate=%s 不在 candidates 里" % (k, cn["candidate"]), k, "")
        if cn.get("status") not in ("supported",):
            if cn.get("forbidden"):
                add("UnresolvedConnection",
                    "连接 %s 未判别（status=%s）却声明了禁止项 %s" % (k, cn.get("status"), cn["forbidden"]), k,
                    "先按 cookbook 判定（外部+探针），或去掉禁止项")
        if "boolean_union" in (cn.get("forbidden") or []) and (cn.get("apply") or {}).get("boolean_union"):
            add("UnsupportedDestructiveMerge", "连接 %s 声明禁止 boolean_union 但仍要求执行" % k, k, "删掉该 apply")
    # 依赖与环
    graph = {fid: list(f.get("depends_on") or []) for fid, f in feats.items()}
    for fid, deps in graph.items():
        for d in deps:
            if d not in feats and d not in comps and d not in conns:
                add("MissingComponent", "特征 %s 依赖的 %s 不存在" % (fid, d), fid, "补组件/连接/特征")
            if d in conns and conns[d].get("status") != "supported":
                add("UnresolvedConnection", "特征 %s 依赖未判别的连接 %s" % (fid, d), fid,
                    "先判定该连接，或让特征暂不依赖它")
    # 拓扑排序查环
    seen, stack = {}, []

    def visit(n):
        if seen.get(n) == 1:
            add("Cycle", "依赖成环：" + " -> ".join(stack + [n]), n, "打断一条依赖")
            return
        if seen.get(n) == 2:
            return
        seen[n] = 1
        stack.append(n)
        for d in graph.get(n, []):
            if d in graph:
                visit(d)
        stack.pop()
        seen[n] = 2

    for fid in graph:
        visit(fid)
    # 编译后才会出现的对象缺失（场景里查一遍，build 时对象名 = PLAN_<id>）
    for cid, c in comps.items():
        for oname in (c.get("objects") or []):
            if bpy.data.objects.get(oname) is None:
                add("MissingObject", "组件 %s 声明的对象 %s 不在场景里" % (cid, oname), cid, "先 build 或修正对象名")
    return _j({"ok": True, "diagnostics": D,
               "summary": {"errors": len([d for d in D if d["code"] != "MissingObject"]),
                           "warnings": len([d for d in D if d["code"] == "MissingObject"])}})


def p_order():
    st = _store()
    if not st["plan"]:
        return _j({"ok": False, "error": "还没载入 plan"})
    pl = st["plan"]; comps, conns, feats = pl["components"], pl["connections"], pl["features"]
    order = []
    done = set()
    pend_c = list(comps)
    pend_f = list(feats)
    while True:
        progressed = False
        for n in list(pend_c):                      # ① 组件（无依赖）
            order.append({"node": n, "kind": "component"}); done.add(n); pend_c.remove(n); progressed = True
        for k, cn in conns.items():                 # ② 连接（两端组件就绪）
            if k not in done and cn.get("a") in done and cn.get("b") in done:
                order.append({"node": k, "kind": "connection"}); done.add(k); progressed = True
        for fid in list(pend_f):                    # ③ 特征（依赖都就绪；依赖可以是组件/连接/特征）
            deps = list(feats[fid].get("depends_on") or [])
            if all(d in done for d in deps):
                order.append({"node": fid, "kind": "feature"}); done.add(fid); pend_f.remove(fid); progressed = True
        if not progressed:
            break
    blocked = [n for n in (pend_c + pend_f)] + [k for k in conns if k not in done]
    return _j({"ok": True, "order": order, "blocked": blocked,
               "counts": {"components": len(comps), "connections": len(conns), "features": len(feats)}})


# ---------------------------------------------------------------- 编译到 bpy

def _make_component(cid, c, suffix=""):
    kind = c.get("kind", "box")
    pr = c.get("params") or {}
    name = "PLAN_%s%s" % (cid, suffix)
    loc = pr.get("loc", [0, 0, 0])
    rot = pr.get("rot", [0, 0, 0])
    if kind == "box":
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
        ob = bpy.context.object
        ob.scale = pr.get("size", [1, 1, 1])
    elif kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(radius=pr.get("radius", 0.1), depth=pr.get("depth", 0.5), location=loc)
        ob = bpy.context.object
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=pr.get("radius", 0.1), location=loc)
        ob = bpy.context.object
    elif kind == "mesh_copy":
        src = bpy.data.objects.get(pr.get("src"))
        if src is None:
            raise RuntimeError("mesh_copy 源不存在: %s" % pr.get("src"))
        ob = src.copy(); ob.data = src.data.copy(); bpy.context.scene.collection.objects.link(ob)
    else:
        raise RuntimeError("未知 kind: %s" % kind)
    ob.name = name
    ob.rotation_euler = (math.radians(rot[0]), math.radians(rot[1]), math.radians(rot[2]))
    ob.select_set(False)
    return ob


def p_build(dry_run=True, replace=True):
    st = _store()
    pl = st["plan"]
    if not pl:
        return _j({"ok": False, "error": "还没载入 plan"})
    d = json.loads(p_diag())
    hard = [x for x in d["diagnostics"] if x["code"] in ("MissingComponent", "Cycle", "UnknownKind")]
    if hard:
        return _j({"ok": False, "blocked": True, "code": "PlanInvalid",
                   "diagnostics": hard, "message": "硬错误未解决，拒绝编译（这就是 IDE 式报错）"})
    comps, conns, feats = pl["components"], pl["connections"], pl["features"]
    created, skipped = [], []
    # hidden_when：按连接候选决定存在性
    for cid, c in comps.items():
        hw = c.get("hidden_when")
        if hw and conns.get(hw.get("connection"), {}).get("candidate") != hw.get("candidate"):
            skipped.append({"component": cid, "why": "hidden_when: connection %s candidate != %s" % (
                hw.get("connection"), hw.get("candidate"))})
            continue
        conn_offset = None
        for k, cn in conns.items():
            if cn.get("b") == cid and cn.get("offset"):
                conn_offset = cn["offset"]
        created.append({"component": cid, "kind": c.get("kind", "box"), "connection_offset": conn_offset,
                        "object": "PLAN_%s" % cid})
    # 特征展开
    for fid, f in feats.items():
        created.append({"feature": fid, "kind": f.get("kind"), "count": f.get("count") or 1,
                        "src": f.get("src"), "depends_on": f.get("depends_on") or []})
    if dry_run:
        return _j({"ok": True, "dry_run": True, "would_create": created, "skipped": skipped,
                   "diagnostics": d["diagnostics"]})
    if replace:
        for cid in comps:
            for suf in [""] + ["_%d" % i for i in range(1, 33)]:
                ob = bpy.data.objects.get("PLAN_%s%s" % (cid, suf))
                if ob:
                    bpy.data.objects.remove(ob, do_unlink=True)
    # 建组件
    objs = {}
    for cid, c in comps.items():
        hw = c.get("hidden_when")
        if hw and conns.get(hw.get("connection"), {}).get("candidate") != hw.get("candidate"):
            continue
        try:
            ob = _make_component(cid, c)
        except Exception as e:
            return _j({"ok": False, "error": str(e), "component": cid})
        # 连接偏移
        for k, cn in conns.items():
            if cn.get("b") == cid and cn.get("offset", {}).get("translate"):
                t = cn["offset"]["translate"]
                ob.location = (ob.location[0] + t[0], ob.location[1] + t[1], ob.location[2] + t[2])
        objs[cid] = ob
    # 建特征（array / grid / mirror）
    for fid, f in feats.items():
        src = objs.get(f.get("src")) or bpy.data.objects.get(f.get("src"))
        if src is None:
            continue
        kind = f.get("kind")
        count = int(f.get("count") or 1)
        off = f.get("offset") or [0, 0, 0]
        made = []
        if kind == "array":
            for i in range(1, count + 1):
                ob = src.copy(); ob.data = src.data.copy(); bpy.context.scene.collection.objects.link(ob)
                ob.name = "PLAN_%s_%d" % (fid, i)
                ob.location = (src.location[0] + off[0] * i, src.location[1] + off[1] * i, src.location[2] + off[2] * i)
                ob.select_set(False); made.append(ob.name)
        elif kind == "grid":
            nx = int(f.get("nx") or count); ny = int(f.get("ny") or count)
            n = 1
            for ix in range(nx):
                for iy in range(ny):
                    ob = src.copy(); ob.data = src.data.copy(); bpy.context.scene.collection.objects.link(ob)
                    ob.name = "PLAN_%s_%d" % (fid, n); n += 1
                    ob.location = (src.location[0] + off[0] * ix, src.location[1] + off[1] * iy, src.location[2])
                    ob.select_set(False); made.append(ob.name)
        elif kind == "mirror":
            axis = f.get("axis", 0)
            ob = src.copy(); ob.data = src.data.copy(); bpy.context.scene.collection.objects.link(ob)
            ob.name = "PLAN_%s_mirror" % fid
            loc = list(src.location); loc[axis] = -loc[axis]
            ob.location = loc
            ob.scale = list(src.scale); ob.scale[axis] = -ob.scale[axis]
            ob.select_set(False); made.append(ob.name)
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()
    st["build"] = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "objects": len(objs)}
    _log("build", {"components": len(objs)})
    # 编译后自动建包络（若 plan 里声明了）
    env_note = None
    if pl.get("envelope"):
        K = _kernel(); env = pl["envelope"]; scope = [o.name for o in objs.values()]
        if hasattr(K, "dsh_contract_api"):
            K.dsh_contract_api["register_envelope"]("plan_env", env["min"], env["max"], scope, "由 plan 编译时建")
            env_note = json.loads(K.dsh_contract_api["check_envelope"]("plan_env"))["envelopes"][0]
    return _j({"ok": True, "created_components": len(objs), "objects": sorted(o.name for o in objs.values()),
               "envelope_check": env_note, "note": "编译产物名字前缀 PLAN_；连接偏移已应用；特征已展开"})


def p_graph(fmt="mermaid"):
    st = _store()
    pl = st["plan"]
    if not pl:
        return _j({"ok": False, "error": "还没载入 plan"})
    comps, conns, feats = pl["components"], pl["connections"], pl["features"]
    if fmt == "dot":
        L = ["digraph plan {", '  rankdir="LR";']
        for cid in comps:
            L.append('  "%s" [shape=box];' % cid)
        for k, cn in conns.items():
            L.append('  "%s" -> "%s" [label="%s (%s)"];' % (cn.get("a"), cn.get("b"), k, cn.get("status", "?")))
        for fid, f in feats.items():
            L.append('  "%s" [shape=ellipse];' % fid)
            for d in (f.get("depends_on") or []):
                L.append('  "%s" -> "%s";' % (d, fid))
        L.append("}")
        return _j({"ok": True, "fmt": "dot", "text": "\n".join(L)})
    L = ["graph LR"]
    for cid in comps:
        L.append('  %s["%s (component)"]' % (cid, cid))
    for k, cn in conns.items():
        L.append('  %s -- "%s [%s]" --> %s' % (cn.get("a"), k, cn.get("status", "?"), cn.get("b")))
    for fid, f in feats.items():
        L.append('  %s(("%s (feature)"))' % (fid, fid))
        for d in (f.get("depends_on") or []):
            L.append('  %s --> %s' % (d, fid))
    return _j({"ok": True, "fmt": "mermaid", "text": "\n".join(L)})


def p_status():
    st = _store()
    pl = st["plan"]
    if not pl:
        return _j({"ok": True, "loaded": False})
    d = json.loads(p_diag())
    o = json.loads(p_order())
    return _j({"ok": True, "loaded": True, "id": pl["id"],
               "counts": {"components": len(pl["components"]), "connections": len(pl["connections"]),
                          "features": len(pl["features"])},
               "diagnostics": d["diagnostics"], "build_order": o["order"], "blocked": o["blocked"],
               "last_build": st.get("build")})


def p_help():
    return _j({"version": PLAN_VERSION,
               "ops": {"load": "plan(JSON 对象 / JSON 字符串 / 文件路径) → 载入并出诊断",
                       "validate": "→ 诊断清单（MissingComponent / UnresolvedConnection / Cycle / ...）",
                       "order": "→ 拓扑编译顺序（被依赖的先）",
                       "build": "dry_run(默认 True) / replace → 编译成 PLAN_* 对象；dry_run 只看计划",
                       "graph": "fmt=mermaid|dot → 图文本",
                       "status": "→ 概览 + 诊断 + 编译顺序"},
               "component_kinds": ["box", "cylinder", "sphere", "mesh_copy"],
               "feature_kinds": ["array", "grid", "mirror"],
               "diagnostic_codes": ["MissingComponent", "MissingObject", "UnresolvedConnection",
                                    "UnsupportedDestructiveMerge", "Cycle", "ParamOutOfRange", "UnknownKind", "PlanInvalid"],
               "docs": "docs/假设驱动建模-cookbook.md（S3 与契约层/内环的分工）"})


def p_dispatch(op, args=None):
    """统一入口：工具侧用 plan_<op> 调用（load/validate/order/build/graph/status/help）。"""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    alias = {"validate": "diag", "help": "help"}
    name = alias.get(str(op), str(op))
    fn = p_ops().get(name)
    if fn is None:
        return _j({"ok": False, "error": "unknown plan op", "op": op, "ops": sorted(p_ops())})
    # 工具侧常用 "plan" 作为参数名，这里兼容成 plan=
    if "plan" in kw and name == "load":
        pass
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(kw),
                   "sig_hint": p_help()})


def p_ops():
    return {"load": p_load, "diag": p_diag, "validate": p_diag, "order": p_order,
            "build": p_build, "graph": p_graph, "status": p_status, "help": p_help}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_plan_api = {"version": PLAN_VERSION, "dispatch": p_dispatch,
                       "load": p_load, "diag": p_diag, "order": p_order,
                       "build": p_build, "graph": p_graph, "status": p_status, "help": p_help}
    # 给契约层补一个"规划器诊断"入口（S1 与 S3 联动）
    if hasattr(_K, "dsh_contract_api"):
        _K.dsh_contract_api["plan_diag"] = p_diag
