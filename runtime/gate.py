# -*- coding: utf-8 -*-
"""DSH 规格驱动门包（v0.9.6 · 现场反馈 #3）—— 把"每个项目重写一遍门"变成一份 spec。

现场背景：一次 95 对象 / 3.05 M 面的装配建模里，作者自己写了 interference / redcheck /
selfint_diag 三个脚本约 600 行来做门。这类东西应该是插件的一部分：**判据来自 spec 文件，
执行与取证由插件负责**。

spec 形态（JSON 或 .py 模块，二选一）：
    {"name":"MK1","units":"mm","tolerance_mm":3.0,
     "gates":[
       {"id":"mesh",    "op":"audit_scene",        "args":{"summary_only":true,"top_k":5,"self_intersect":true},
        "pass_if":"clean == True"},
       {"id":"floaters","op":"audit_connectivity", "args":{"scope":"COL_Geo"},
        "pass_if":"gate['verdict'] == 'pass'"},
       {"id":"clash",   "op":"audit_interference", "args":{"objects_a":["Body"],"objects_b":["Lid"]},
        "pass_if":"verdict == 'refuted'"},
       {"id":"print",   "op":"print_report",       "args":{"min_mm":2.0},
        "pass_if":"thin_samples_total == 0 and walls_ok == True"}
     ]}
.py 形态：模块里给 SPEC = {...}（或模块级 NAME/UNITS/TOLERANCE_MM/GATES）。

**三态，不是两态**：每个门的结果是 pass / fail / degraded / error，外加第四种"没跑"的 skipped；
总 verdict = 任一 fail/error → fail；否则任一 degraded **或 skipped** → degraded；否则 pass。
@@ok@@ **只在 verdict==pass 时为 true** —— 现场教训："降级长得太像绿"，所以顶层永远读 @@verdict@@，别读 @@ok@@。

**v3 收紧（F8 · Lead 否决"pass + skipped = pass"）**：`skipped`（未配置来源 / 该门不适用）**不再是透明的** ——
只要包里有一门 skipped，顶层就是 `degraded`、`ok=false`。理由：assembly preset 的 `no_clash` 默认 skipped，
旧口径会让"没量过干涉"的健康场景拿绿；**跳过 ≠ 通过，也不许为"健康默认 preset"把标准降下来**。
要拿 pass 只有一条路：**每一门都真跑过且都过**（给 preset 补 `overrides={门id:{args…}}`，或写自己的 spec）。
"默认未配干涉来源 ⇒ 顶层 degraded" 是**正确行为**，不是 bug。

**三态不总在顶层（v0.9.6 复审修复 · F2）**：`audit_connectivity` 的顶层 `ok` 只表示"分析跑完了"，
门结论在**嵌套 `gate`** 里（`gate.state` / `gate.verdict`）—— 顶层没有 `state` 可读，旧写法
`pass_if="state == 'pass'"` 会让该门直接 `error`。正确写法是 `pass_if="gate['verdict'] == 'pass'"`
（回执自己给的已验证三态）。`_status_of` 也认嵌套三态，于是"未分析/未确认 ⇒ degraded"既不会被
pass_if 压成 fail、也不会被抬成 pass（`degraded ≠ 通过`）。域内名 `unresolved`（干涉"证据不足"）
同样读作 degraded。

**skipped 是第四种"结果"（v0.9.6 · F2；v3 收紧汇总口径 · F8）**：门可以声明 `skip_if_missing`（一组组"候选键名或"）——
每组一个都没给时，该门记 `state=skipped`（**未配置 / 不适用 ⇒ 没跑**）。它**不进** fail/degraded 的逐门点名，
但**会把整包压成 degraded**（`_aggregate`：任一 skipped 或 degraded ⇒ degraded，fail 优先），并且一定显式出现在
回执的 `skipped[]` 与 `coverage` 里（`skipped ≠ 通过`：没跑过的门不许被读成绿）。典型例子：
`audit_interference / audit_overlap` 是**两两**算子，preset 无从知道"哪两件比"，所以 assembly preset 的
`no_clash` 默认 skipped —— 于是**默认 assembly preset 的顶层就是 degraded**（正确；不是 bug）；要真判就写 spec 给
`objects_a`/`objects_b`，或 `gate_run(preset="assembly", overrides={"no_clash":{...}})` ——
**三门都真跑过且都过时才 pass**。

**degrade_if（v3 · F8）**：门的判据为真、但**证据本身不可信**时用 `degrade_if`（同样是受限表达式）把该门降级 ——
典型是 print preset：`walls_ok=true` 只说明"没量到薄壁"，而 `min_mm` 低于本网格的测量分辨率时这句"没量到"
**没有意义**（`print_walls` 只把它写进 per-object 的 `detail.walls.objects[].warning`/`resolution_mm`，顶层没有这些字段）。
print preset 现在用 `degrade_if = params['min_mm'] < detail['walls']['objects'][0]['resolution_mm']` 读**真实字段** ⇒
分辨率不可信时该门 `degraded`（不许假通过）；`echo=[…]` 把这几个真实读数（分辨率 / min_mm / warning）带进回执，可审计。

@@pass_if@@ 是一个**受限表达式**（ast 白名单：比较、布尔、算术、属性/下标、min/max/len/abs/round/all/any/sum），
变量从该门回执的顶层字段解析 —— 写成什么字段，回执里就只回**那几个字段**（自带瘦身）。`degrade_if` 用**同一套**
受限表达式与同一套变量解析；`echo` 走点路径（`detail.walls.objects.0.resolution_mm`）取真实字段做证据。
"""
import ast
import hashlib
import json
import os
import sys
import time

import bpy


def _kernel():
    """模块命名空间里没有 K（K 在 dsh_rt_kernel 模块上）—— 与 sculpt.py / uv_tools.py 同一套取法。"""
    return sys.modules.get("dsh_rt_kernel")

GATE_VERSION = 3   # 3（v0.9.6 最终集成 · F8）：**任一 skipped/degraded ⇒ 顶层 degraded**（旧口径 pass+skipped=pass
                   #   被 Lead 否决）+ gate.degrade_if / gate.echo（不可信分辨率 ⇒ degraded，不许假通过）；
                   #   2（v0.9.6 复审修复 · F2）：skipped 状态 + gate.skip_if_missing + gate_run(overrides=) +
                   #   回执 coverage / 嵌套三态（audit_connectivity 的 gate.verdict）

# plan 级 op → (内核里的模块 API 属性, 该模块的子 op)
OP_MAP = {
    "audit_scene": ("dsh_audit_api", "scene"),
    "audit_mesh": ("dsh_audit_api", "mesh"),
    "audit_connectivity": ("dsh_audit_api", "connectivity"),
    "audit_interference": ("dsh_audit_api", "interference"),
    "audit_overlap": ("dsh_audit_api", "overlap"),
    "audit_gate": ("dsh_audit_api", "gate"),
    "audit_drift": ("dsh_audit_api", "drift"),
    "audit_duplicates": ("dsh_audit_api", "duplicates"),
    "print_report": ("dsh_print_api", "report"),
    "print_walls": ("dsh_print_api", "walls"),
    "uv_stats": ("dsh_uv_api", "stats"),
    "material_scan": ("dsh_material_api", "scan"),
    "fix_repair": ("dsh_fix_api", "repair"),
    "motion_measure": ("dsh_motion_api", "measure"),
    "sweep_analyze": ("dsh_sweep_api", "analyze"),
    "deliver_verify": ("dsh_deliver_api", "verify"),
    "audit_measure": ("dsh_audit_api", "measure"),
    "human_base": ("dsh_human_api", "base"),
    "human_measure": ("dsh_human_api", "measure"),
    "human_head": ("dsh_human_api", "head"),
    "human_spec": ("dsh_human_api", "spec"),
    "audit_file_mesh": ("dsh_audit_api", "file_mesh"),
}
ALLOWED_FUNCS = {"min": min, "max": max, "len": len, "abs": abs, "round": round,
                 "all": all, "any": any, "sum": sum, "float": float, "int": int, "str": str}

# 现场手写过的三个"包"，做成 preset（可直接 gate_run(preset="…")）
PRESETS = {
    "assembly": {
        "name": "assembly", "units": "mm", "tolerance_mm": 3.0,
        "gates": [
            # F2-a（v0.9.6 复审修复）：**必须显式 self_intersect=true** —— audit_scene 默认跳过自交检查，
            # 而新口径把"跳过检查"如实读成 degraded/clean=false（"没查"不许读成"没问题"）。不写这一条，
            # 再健康的场景也拿不到 pass。
            {"id": "mesh_health", "op": "audit_scene",
             "args": {"summary_only": True, "top_k": 5, "self_intersect": True},
             "pass_if": "clean == True",
             "note": "全场景网格体检。self_intersect=true 是**门的前提**：默认(false)会如实给 degraded（clean=false）"},
            # F2-b（v0.9.6 复审修复）：读回执**嵌套 gate 的已验证三态** —— audit_connectivity 顶层没有
            # state/verdict（顶层 ok 只表示"分析跑完了"），写 state == 'pass' 必 error。
            {"id": "no_floaters", "op": "audit_connectivity", "args": {},
             "pass_if": "gate['verdict'] == 'pass'",
             "note": "装配级连通门：判的是「某个分量与其它分量都不相接」（读回执嵌套 gate 的真实三态）。"
                     "单件/单连通分量场景这条判据**无从成立**（回执 components[].evidence=single-body）——"
                     "单件内部壳的贴合/互穿请用 audit_mesh(island_split=true)，别拿装配门当单件门。"
                     "未分析/未确认 ⇒ degraded（pass_if 不会把它抬成 pass）"},
            # F2-c（v0.9.6 复审修复；v3 收紧汇总口径 · F8）：audit_interference 是**两两**算子
            # （a/b 或 objects_a/objects_b 或 file_a/file_b），preset 无从知道零件对 ⇒ 空参必 error。
            # 这里声明 skip_if_missing：未配置来源 ⇒ state=skipped（显式列在回执 skipped[]/coverage 里）。
            # v3：skipped **会把整包压成 degraded** —— 所以**默认 assembly preset 的顶层是 degraded、ok=false**
            # （这是正确行为：没量过干涉就不许读成绿），要 pass 必须把两侧来源补上。
            {"id": "no_clash", "op": "audit_interference", "args": {},
             "skip_if_missing": [["a", "objects_a", "file_a"], ["b", "objects_b", "file_b"]],
             "pass_if": "verdict == 'refuted'",
             "note": "跨件干涉要**两侧来源**：refuted=可证无干涉(过) / supported=真干涉(fail) / "
                     "unresolved=证据不足(degraded)。未配置来源时本门是 skipped（未检查，不是通过；"
                     "v3 起 skipped 会把顶层压成 degraded）——"
                     "要真判：gate_run(preset=\"assembly\", overrides={\"no_clash\":{objects_a:[…],objects_b:[…]}})，"
                     "或写自己的 spec"},
        ],
    },
    "print": {
        "name": "print", "units": "mm", "tolerance_mm": 1.0,
        "gates": [
            # F2 同一条缺陷类别（v0.9.6 复审修复）：旧 pass_if 引用了回执里不存在的 thin_samples /
            # resolution_mm / min_mm ⇒ 该门必 error。改成 print_report 真实顶层字段：
            #   thin_samples_total=所有对象低于 min_mm 的采样数；walls_ok=逐对象 ok（含"射线没命中"）。
            # F8（v3 · 最终集成）：**只判 walls_ok 会假通过** —— 粗网格（体素/低模）的测量分辨率可能
            # 比 min_mm 大好几个数量级，此时"没量到薄壁"毫无意义：print_walls 只把这件事写进 per-object
            # 的 warning/resolution_mm，顶层 pass_if 看不见它。⇒ 加 degrade_if 读**真实字段**
            # （params.min_mm vs detail.walls.objects[0].resolution_mm）：分辨率不可信 ⇒ 该门 degraded
            # （不是 fail，也不是 pass），echo 把三个真实读数带进回执供审计。
            {"id": "walls", "op": "print_report", "args": {"min_mm": 2.0, "scope": "ACTIVE"},
             "pass_if": "thin_samples_total == 0 and walls_ok == True",
             "degrade_if": "params['min_mm'] < detail['walls']['objects'][0]['resolution_mm']",
             "echo": ["params.min_mm", "detail.walls.objects.0.resolution_mm",
                      "detail.walls.objects.0.warning"],
             "note": "制造门：壁厚 < min_mm 的采样数必须为 0，**且这个结论得可信**。分辨率口径在 per-object 的 "
                     "detail.walls.objects[].warning / resolution_mm 上（顶层没有这个字段）⇒ degrade_if 直接读它们："
                     "min_mm 低于本网格测量分辨率时判 degraded（顶层变 degraded，不是假绿）。"
                     "判的是活动对象（scope=ACTIVE，objects[0]）；多对象/多分辨率请自己写 spec 逐对象判"},
        ],
    },
    "delivery": {
        "name": "delivery", "units": "mm", "tolerance_mm": 1.0,
        "gates": [
            # F2 同一条缺陷类别：旧 pass_if "zero_area_faces == 0" 的字段不存在（uv_stats 顶层只有
            # ok/objects/note；零面积 UV 面在 objects[].degenerate_uv_faces）⇒ 该门必 error。
            {"id": "uv", "op": "uv_stats", "args": {}, "pass_if": "ok == True",
             "note": "uv_stats 顶层 ok = 逐对象「有 UV 层 且 零面积 UV 面=0」（明细在 objects[].degenerate_uv_faces）。"
                     "scope 默认 ACTIVE ⇒ 交付门要先选好活动对象"},
            {"id": "materials", "op": "material_scan", "args": {}, "pass_if": "count > 0",
             "note": "material_scan 在对象没有材质时直接抛错（不返回 count）⇒ 该门会 error；先 material_build/apply。"
                     "scope 默认 ACTIVE"},
        ],
    },
}



def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _md5_text(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- spec 解析

def _load_spec(spec=None, spec_path=None, preset=None):
    """返回 (spec_dict, source, md5)。.py 模块、.json、内联 dict 三种都给。"""
    if preset:
        key = str(preset)
        if key not in PRESETS:
            raise ValueError("未知 preset：%s（允许 %s）" % (preset, sorted(PRESETS)))
        raw = _j(PRESETS[key])
        return json.loads(raw), "preset:" + key, _md5_text(raw)
    if spec_path:
        p = str(spec_path)
        _k = _kernel()
        if p.startswith("/"):
            wsl = _k.win_path(p) if _k is not None else p
        else:
            wsl = p
        if not os.path.exists(wsl):
            wsl = p
        with open(wsl, "r", encoding="utf-8") as f:
            text = f.read()
        if wsl.lower().endswith(".py"):
            ns = {}
            exec(compile(text, wsl, "exec"), ns)
            if "SPEC" in ns:
                data = ns["SPEC"]
            else:
                data = {"name": ns.get("NAME", os.path.basename(wsl)), "units": ns.get("UNITS", "mm"),
                        "tolerance_mm": ns.get("TOLERANCE_MM"), "gates": ns.get("GATES")}
        else:
            data = json.loads(text)
        return data, "file:" + wsl, _md5_text(text)
    if isinstance(spec, dict):
        raw = _j(spec)
        return json.loads(raw), "inline", _md5_text(raw)
    raise ValueError("要给 spec=<dict> 或 spec_path=<.json/.py> 或 preset=<名字>")


def _norm_spec(data):
    if not isinstance(data, dict):
        raise ValueError("spec 必须是对象（或 .py 里的 SPEC）")
    gates = data.get("gates")
    if not isinstance(gates, list) or not gates:
        raise ValueError("spec.gates 必须是非空数组")
    out = {"name": str(data.get("name") or "unnamed"), "units": str(data.get("units") or "mm"),
           "tolerance_mm": data.get("tolerance_mm"), "gates": []}
    for i, g in enumerate(gates):
        if not isinstance(g, dict):
            raise ValueError("gates[%d] 必须是对象" % i)
        op = str(g.get("op") or "")
        if op not in OP_MAP:
            near = sorted(OP_MAP)
            raise ValueError("gates[%d].op 不认识：%s（允许：%s）" % (i, op, ", ".join(near)))
        out["gates"].append({"id": str(g.get("id") or ("g%d" % i)), "op": op,
                             "args": g.get("args") or {}, "pass_if": str(g.get("pass_if") or ""),
                             "note": g.get("note"),
                             # v0.9.6（F2）：这些键"一个都没给"⇒ 该门 skipped（未配置/不适用），
                             # 显式列进回执 skipped[]；v3 起 skipped 会把顶层压成 degraded。"组"= 候选键名的或关系。
                             "skip_if_missing": _norm_skip_groups(g.get("skip_if_missing")),
                             # v3（F8）：判据为真但证据不可信 ⇒ 降级；echo 把真实字段带进回执做证据。
                             "degrade_if": str(g.get("degrade_if") or ""),
                             "echo": _norm_str_list(g.get("echo"))})
    return out


def _norm_str_list(raw):
    """把 echo 归一化成 [点路径…]（单串也收）。"""
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if str(x).strip()]
    raise ValueError("echo 必须是点路径字符串或它们的数组")


def _path_get(root, path):
    """按点路径取真实字段（供 echo 用）：`detail.walls.objects.0.resolution_mm`。

    只读；任一段不存在/越界 ⇒ (None, False)（不抛异常，别让取证把门搞挂）。
    """
    cur = root
    for seg in str(path or "").split("."):
        if not seg:
            continue
        if isinstance(cur, dict):
            if seg not in cur:
                return None, False
            cur = cur[seg]
        elif isinstance(cur, (list, tuple)):
            if not seg.lstrip("-").isdigit():
                return None, False
            i = int(seg)
            if not (-len(cur) <= i < len(cur)):
                return None, False
            cur = cur[i]
        else:
            return None, False
    return cur, True


def _collect_echo(receipt, paths):
    """echo：把声明的点路径上的**真实字段**取进回执（取证用）。过大的值只报摘要，别把回执撑爆。"""
    out = {}
    for p in paths or []:
        v, found = _path_get(receipt, p)
        if not found:
            out[str(p)] = "<缺该字段>"
            continue
        try:
            s = _j(v)
        except Exception:
            s = None
        out[str(p)] = v if (s is not None and len(s) <= 300) else "<字段过大，见 detail>"
    return out


def _norm_skip_groups(raw):
    """skip_if_missing 归一化成 [[候选键…], …]（单层列表也收：["a","b"] → [["a","b"]]）。"""
    if not raw:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("skip_if_missing 必须是数组（每组是候选键名或它们的数组）")
    out = []
    if raw and all(isinstance(x, str) for x in raw):
        raw = [list(raw)]
    for grp in raw:
        keys = [str(k) for k in (grp if isinstance(grp, (list, tuple)) else [grp])]
        if keys:
            out.append(keys)
    return out


def _expr_names(expr):
    """取表达式里用到的变量名（用于"只回判据字段"的瘦身）。"""
    names = []
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return names
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id not in ALLOWED_FUNCS and n.id not in ("True", "False", "None"):
            if n.id not in names:
                names.append(n.id)
    return names


def _check_ast(node, label="pass_if"):
    ok_types = (ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Name, ast.Load, ast.Constant,
                ast.Attribute, ast.Subscript, ast.Tuple, ast.List, ast.BinOp, ast.Call,
                ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                ast.In, ast.NotIn, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.USub, ast.UAdd)
    for n in ast.walk(node):
        if not isinstance(n, ok_types):
            raise ValueError("%s 里有不允许的语法：%s" % (label, type(n).__name__))
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in ALLOWED_FUNCS:
                raise ValueError("%s 只允许这些函数：%s" % (label, sorted(ALLOWED_FUNCS)))
        if isinstance(n, ast.Attribute) and n.attr.startswith("_"):
            raise ValueError("%s 不许访问下划线属性" % label)


def _eval_pass_if(expr, receipt, label="pass_if"):
    """受限表达式求值（`pass_if` 与 `degrade_if` 共用）。变量从回执顶层取；取不到 → 明确报缺哪个字段。"""
    tree = ast.parse(expr, mode="eval")
    _check_ast(tree, label)
    env = {"True": True, "False": False, "None": None}
    env.update(ALLOWED_FUNCS)
    missing = []
    for nm in _expr_names(expr):
        if nm in receipt:
            env[nm] = receipt[nm]
        elif nm in ("units", "tolerance_mm"):
            env[nm] = None
        else:
            missing.append(nm)
    if missing:
        raise ValueError("%s 引用了回执里没有的字段：%s（回执顶层字段：%s）"
                         % (label, missing, sorted(k for k in receipt if not str(k).startswith("_"))[:14]))
    return bool(eval(compile(tree, "<%s>" % label, "eval"), {"__builtins__": {}}, env))


def _aggregate(states):
    """整包三态（v3 · F8 收紧）：任一 fail/error ⇒ fail；否则任一 **degraded 或 skipped** ⇒ degraded；否则 pass。

    最小修（Lead 决议）：**任一门 skipped 或 degraded 都导致顶层 degraded，fail 优先** ——
    旧口径 `pass + skipped = pass` 被否决（"没量过干涉"不许拿绿，也不许为"健康默认 preset"降标准）。
    至少在有一门真 pass 且没有任何 fail/error/degraded/skipped 时才给 pass；
    一门都没有（空包）也一律 degraded —— "什么都没查"不给通过结论。
    """
    if any(s in ("fail", "error") for s in states):
        return "fail"
    if any(s in ("degraded", "skipped") for s in states):
        return "degraded"
    if not any(s == "pass" for s in states):
        return "degraded"
    return "pass"


_DOMAIN_DEGRADED_VERDICTS = ("unresolved",)     # 域内三态里的"证据不足"名：等同 degraded，不许读成通过


def _tri_of(d):
    """从一个 dict 里读三态（pass/fail/degraded）；读不出来给 None（调用方继续往下找）。"""
    if not isinstance(d, dict):
        return None
    if d.get("ok") is False or d.get("state") == "fail" or d.get("verdict") == "fail":
        return "fail"
    if d.get("ok") is None or d.get("state") == "degraded" or d.get("verdict") == "degraded":
        return "degraded"
    for v in (d.get("state"), d.get("verdict")):
        if v == "pass":
            return "pass"
    return None


def _status_of(receipt):
    """把一个门回执判成三态：pass / fail / degraded（回执不是对象 ⇒ error）。

    v0.9.6（复审修复 · F2）：**三态不总在顶层**。`audit_connectivity` 的顶层 `ok` 只表示"分析跑完了"，
    门结论在**嵌套 `gate`**（`gate.state` / `gate.verdict`）里 —— 顶层读不出三态时必须往嵌套里看，
    否则：① pass_if 写 `state` 就是"引用不存在的字段"（必 error）；② "未分析/未确认 ⇒ degraded"
    会因为顶层 `ok=true` 被读成 pass（假通过）。域内名 `unresolved`（干涉证据不足）等同 degraded。
    """
    if not isinstance(receipt, dict):
        return "error"
    st = _tri_of(receipt)
    if st:
        return st
    if receipt.get("verdict") in _DOMAIN_DEGRADED_VERDICTS:
        return "degraded"
    for key in ("gate", "connectivity"):
        sub = receipt.get(key)
        st = _tri_of(sub)
        if st:
            return st
        if isinstance(sub, dict) and sub.get("verdict") in _DOMAIN_DEGRADED_VERDICTS:
            return "degraded"
    return "pass"


# ---------------------------------------------------------------- ops

def gate_plan(spec=None, spec_path=None, preset=None):
    """只读：解析 spec、列出将要跑的门与判据（不执行）。"""
    data, src, md5 = _load_spec(spec, spec_path, preset)
    sp = _norm_spec(data)
    rows = []
    for g in sp["gates"]:
        rows.append({"id": g["id"], "op": g["op"], "args": g["args"], "pass_if": g["pass_if"],
                     "reads": _expr_names(g["pass_if"]), "note": g["note"],
                     "skip_if_missing": g["skip_if_missing"] or None,
                     "degrade_if": g["degrade_if"] or None, "echo": g["echo"] or None,
                     "module": OP_MAP[g["op"]][0].replace("dsh_", "").replace("_api", "")})
    return _j({"ok": True, "name": sp["name"], "units": sp["units"], "tolerance_mm": sp["tolerance_mm"],
               "spec_source": src, "spec_md5": md5, "gate_count": len(rows), "gates": rows,
               "presets": sorted(PRESETS),
               "note": "gate_run 才真跑；verdict 三态（pass/fail/degraded），ok 只在 pass 时为 true；"
                       "声明 skip_if_missing 的门在「这些键一个都没给」时记 skipped（未跑；v3 起 skipped 会把"
                       "顶层压成 degraded —— 跳过 ≠ 通过）；degrade_if 命中 ⇒ 该门 degraded（证据不可信）"})


def _missing_skip_groups(args, groups):
    """skip_if_missing 判据：每个内层组是"候选键名的或"；任一组一个都没给 ⇒ 该组未配置。"""
    miss = []
    for grp in groups or []:
        keys = [str(k) for k in (grp if isinstance(grp, (list, tuple)) else [grp])]
        if not any(k in (args or {}) for k in keys):
            miss.append(keys)
    return miss


def _norm_overrides(overrides):
    """overrides={门id: {args键: 值}}（v0.9.6 · F2）：给 preset 里"现场才知道"的 args 补来源。
    例：gate_run(preset="assembly", overrides={"no_clash":{"objects_a":[…],"objects_b":[…]}})。"""
    if not overrides:
        return {}
    if isinstance(overrides, str):
        overrides = json.loads(overrides) if overrides.strip() else {}
    if not isinstance(overrides, dict):
        raise ValueError("overrides 必须是 {门id: {args键: 值}}")
    out = {}
    for k, v in overrides.items():
        if not isinstance(v, dict):
            raise ValueError("overrides[%s] 必须是对象（args 键值）" % k)
        out[str(k)] = v
    return out


def gate_run(spec=None, spec_path=None, preset=None, detail=False, out_json=None, stop_on_fail=False,
             overrides=None):
    """跑完整包：逐门执行 → 三态汇总。回执默认只带**判据用到的字段**（自带瘦身）。

    overrides（v0.9.6 · F2）：{门id: {args键: 值}} —— 只并进那一门的 args，不改判据。preset 里
    "现场才知道"的参数（如跨件干涉的两侧对象）就靠它补；没补时声明了 skip_if_missing 的门记 skipped，
    而 **skipped 会把顶层压成 degraded**（v3 · F8：跳过 ≠ 通过，不许为"健康默认 preset"变绿）。

    degrade_if（v3 · F8）：判据为真但证据不可信（如 min_mm 低于测量分辨率）⇒ 该门 degraded，echo 提供真实读数。
    """
    data, src, md5 = _load_spec(spec, spec_path, preset)
    sp = _norm_spec(data)
    _ov = _norm_overrides(overrides)
    for g in sp["gates"]:
        if g["id"] in _ov:
            g["args"] = dict(g["args"] or {}, **_ov[g["id"]])
    t0 = time.time()
    rows = []
    for g in sp["gates"]:
        attr, sub = OP_MAP[g["op"]]
        api = getattr(_kernel(), attr, None)   # 模块间调用：API 都挂在 dsh_rt_kernel 上
        rec = {"id": g["id"], "op": g["op"]}
        if api is None:
            rec.update({"state": "error", "error": "该门需要模块 %s（preload 里没注入）" % attr})
            rows.append(rec)
            continue
        # v0.9.6（F2）：未配置来源 ⇒ 该门 skipped（未跑）。一定显式列出，且（v3 · F8）会把顶层压成 degraded。
        _miss = _missing_skip_groups(g["args"], g.get("skip_if_missing"))
        if _miss:
            rec.update({"state": "skipped", "ms": 0,
                        "reason": "该门未配置来源（缺 %s）⇒ 未检查（skipped ≠ 通过；v3 起顶层因此判 degraded）"
                                  % " / ".join("|".join(m) for m in _miss),
                        "hint": "要真判这一门：spec 里补上这些 args，或用 overrides={「%s」:{…}} 覆盖 ——"
                                "只有每门都真跑过且都过，顶层才会 pass" % g["id"],
                        "skipped_args": _miss})
            rows.append(rec)
            continue
        t1 = time.time()
        try:
            sub_receipt = api(sub, g["args"] or {})
        except Exception as e:
            rec.update({"state": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:180]),
                        "ms": int((time.time() - t1) * 1000)})
            rows.append(rec)
            if stop_on_fail:
                break
            continue
        rec["ms"] = int((time.time() - t1) * 1000)
        rec["state"] = _status_of(sub_receipt)
        # v3（F8）：echo —— 把声明的**真实字段**（分辨率 / min_mm / warning…）带进回执做证据（可审计）
        if g.get("echo") and isinstance(sub_receipt, dict):
            rec["echo"] = _collect_echo(sub_receipt, g["echo"])
        # 子 op 自己就报错（参数不对 / 前置不满足）⇒ 直接把它的话端上来，别拿 pass_if 的错误去误导
        _is_err_env = (isinstance(sub_receipt, dict) and sub_receipt.get("ok") is False
                       and bool(sub_receipt.get("error") or sub_receipt.get("stage") or sub_receipt.get("reason")))
        if _is_err_env:
            rec["state"] = "error"
            rec["error"] = str(sub_receipt.get("error") or sub_receipt.get("reason") or "该门的前置 op 返回 ok=false")[:200]
            rec["key"] = {k: sub_receipt.get(k) for k in ("error", "hint", "reason") if k in sub_receipt}
            rows.append(rec)
            if stop_on_fail:
                break
            continue
        if g["pass_if"]:
            try:
                got = _eval_pass_if(g["pass_if"], sub_receipt if isinstance(sub_receipt, dict) else {},
                                    label="pass_if")
            except Exception as e:
                rec.update({"state": "error", "error": "pass_if 求值失败：%s" % str(e)[:200]})
                rows.append(rec)
                continue
            # v0.9.6（P0 标定）：**写了 pass_if 就以它为准** —— 子 op 的 ok=false 可能只是"某一件没过"
            # （如 print_report 的 overhang），不能压过显式判据；只有 degraded 例外：
            # 底层没跑完（ok=null）时不许读成通过。
            if rec["state"] == "degraded":
                pass
            else:
                rec["state"] = "pass" if got else "fail"
            rec["pass_if"] = g["pass_if"]
            rec["pass_if_value"] = bool(got)
        # v3（F8）：**判据为真 ≠ 结论可信**。degrade_if 命中（如 min_mm 低于测量分辨率）⇒ 该门 degraded，
        # 绝不许假通过；fail/degraded 已经是更强的结论，不被 degrade_if 覆盖（fail 优先）。
        if g.get("degrade_if") and rec["state"] == "pass":
            try:
                _dg = _eval_pass_if(g["degrade_if"], sub_receipt if isinstance(sub_receipt, dict) else {},
                                    label="degrade_if")
            except Exception as e:
                rec.update({"state": "error", "error": "degrade_if 求值失败：%s" % str(e)[:200]})
                rows.append(rec)
                continue
            if _dg:
                rec["state"] = "degraded"
                rec["degrade_if"] = g["degrade_if"]
                rec["degrade_reason"] = (g.get("degrade_reason")
                                         or "degrade_if 命中：判据为真但证据不可信（分辨率/覆盖不足）⇒ 不许当通过")
                rec["hint"] = "要拿 pass：先把测量口径做实（细化网格 / 调 min_mm），见 echo 里的真实读数"
        rec["key"] = {k: sub_receipt.get(k) for k in _expr_names(g["pass_if"]) if isinstance(sub_receipt, dict) and k in sub_receipt}
        if rec["state"] != "pass":
            # 失败/降级才带更多证据（够定位，但不至于把整份回执塞进来）
            for k in ("reason", "error", "hint", "state", "clean", "verdict", "count"):
                if isinstance(sub_receipt, dict) and k in sub_receipt and k not in rec["key"]:
                    rec["key"][k] = sub_receipt[k]
        if detail:
            rec["receipt"] = sub_receipt
        rows.append(rec)
        if stop_on_fail and rec["state"] in ("fail", "error"):
            break
    states = [r.get("state") for r in rows]
    verdict = _aggregate(states)
    _checked = [r["id"] for r in rows if r.get("state") in ("pass", "fail", "degraded", "error")]
    _skipped = [r["id"] for r in rows if r.get("state") == "skipped"]
    payload = {"ok": verdict == "pass", "verdict": verdict, "name": sp["name"], "units": sp["units"],
               "tolerance_mm": sp["tolerance_mm"], "spec_source": src, "spec_md5": md5,
               "gates": rows, "ran": len(rows), "total": len(sp["gates"]),
               "failed": [r["id"] for r in rows if r.get("state") in ("fail", "error")],
               "degraded": [r["id"] for r in rows if r.get("state") == "degraded"],
               # v0.9.6（F2）+ v3（F8）：跳过的门必须看得见，而且**会把顶层压成 degraded**
               # （skipped ≠ 通过；只有每门都真跑过且都过才 pass）
               "skipped": _skipped,
               "coverage": {"gates": len(sp["gates"]), "checked": len(_checked), "skipped": len(_skipped),
                            "all_gates_ran": not _skipped, "checked_ids": _checked, "skipped_ids": _skipped},
               "ms": int((time.time() - t0) * 1000),
               "note": "顶层永远读 verdict（三态）；ok 只在 pass 时为 true —— 任一门 degraded **或 skipped** ⇒"
                       "顶层 degraded（fail 优先）：跳过 ≠ 通过，也没查过的门不许读成绿；"
                       "degrade_if 命中同样判 degraded（证据不可信，见 echo）"}
    if _skipped:
        payload["skipped_note"] = ("skipped ≠ 通过：这些门没有跑（未配置来源 / 不适用）：%s ——"
                                   "v3 起 skipped 会把顶层判成 degraded（不是 pass）；"
                                   "要它们参与判定就补 args 或走 overrides" % _skipped)
    if _ov:
        payload["overrides_applied"] = {k: sorted(v.keys()) for k, v in _ov.items()}
    if out_json:
        try:
            _k = _kernel()
            p = _k.win_path(str(out_json)) if (str(out_json).startswith("/") and _k is not None) else str(out_json)
            d = os.path.dirname(p)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(_j(payload))
            payload["out_json"] = p
        except Exception as e:
            payload["out_json_error"] = str(e)[:160]
    return _j(payload)


def gate_selftest():
    """自检：造一个"该过"的场景 + 一个"该挂"的门，断言三态判定与 pass_if/degrade_if/skipped 语义都对。

    v3（F8）新增断言：① 任一 skipped/degraded ⇒ 顶层 degraded（pass+skipped 不再算 pass）；
    ② print preset 的 degrade_if（真实字段：min_mm vs resolution_mm）在真机回执上把"判据为真但分辨率
    不可信"的门判成 degraded，且 echo 与 receipt 里的真实值一致。
    """
    import bmesh
    ev = {}
    made = []
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
        # v0.9.6（F2）单元级：**嵌套三态**（audit_connectivity 的门结论在 gate 里）+ **skipped 汇总（v3 收紧）**
        ev["nested_tri_unit"] = {
            "conn_pass": _status_of({"ok": True, "analyzed": True, "gate": {"ok": True, "state": "pass", "verdict": "pass"}}),
            "conn_fail": _status_of({"ok": True, "analyzed": True, "gate": {"ok": False, "state": "fail", "verdict": "fail"}}),
            "conn_unanalyzed": _status_of({"ok": True, "analyzed": False,
                                           "gate": {"ok": None, "state": "degraded", "verdict": "degraded"}}),
            "inter_unresolved": _status_of({"ok": True, "analyzed": True, "verdict": "unresolved"}),
            "inter_refuted_is_pass": _status_of({"ok": True, "analyzed": True, "verdict": "refuted"}),
            # v3（F8 · Lead 否决 pass+skipped=pass）：任一 skipped ⇒ 顶层 degraded
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
        # v3（F8）单元级：degrade_if —— 判据为真但证据不可信 ⇒ degraded（不许假通过）。
        # 直接用 **print preset 的真实表达式** 跑合成回执：分辨率 1000mm vs min_mm 2mm ⇒ 命中；
        # 分辨率 0.1mm ⇒ 不命中（"只有当分辨率不可信时才降级"）。echo 走点路径取真实字段。
        _preset_print = PRESETS["print"]["gates"][0]
        _dg_expr = _preset_print["degrade_if"]
        _coarse = {"params": {"min_mm": 2.0}, "detail": {"walls": {"objects": [{"resolution_mm": 1000.0}]}}}
        _fine = {"params": {"min_mm": 2.0}, "detail": {"walls": {"objects": [{"resolution_mm": 0.1}]}}}
        ev["degrade_if_unit"] = {
            "expr": _dg_expr,
            "coarse_hits": _eval_pass_if(_dg_expr, _coarse),
            "fine_hits": _eval_pass_if(_dg_expr, _fine),
            "path_ok": _path_get(_coarse, "detail.walls.objects.0.resolution_mm") == (1000.0, True),
            "path_missing": _path_get(_coarse, "detail.walls.objects.9.resolution_mm")[1] is False,
            "echo": _collect_echo(_coarse, _preset_print.get("echo")),
            "plan_echo": (json.loads(gate_plan(preset="print"))["gates"][0] or {}).get("echo"),
        }
        ev["degrade_if_ok"] = bool(ev["degrade_if_unit"]["coarse_hits"] is True
                                   and ev["degrade_if_unit"]["fine_hits"] is False
                                   and ev["degrade_if_unit"]["path_ok"]
                                   and ev["degrade_if_unit"]["path_missing"]
                                   and ev["degrade_if_unit"]["echo"].get("params.min_mm") == 2.0
                                   and ev["degrade_if_unit"]["plan_echo"])
        # preset 静态契约（F2-a/b/c + v3 的跳过/降级字段）：门还在、各自读的字段是真的、没来源的门会 skipped
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
        # v3（F8）：print preset 的分辨率降级必须在**真实回执**上成立（不是合成回执）
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
                  and ev.get("unknown_op_error")
                  and (ev.get("bad_field") or {}).get("state") == "error"
                  and "没有的字段" in ((ev.get("bad_field") or {}).get("error") or ""))
        return _j({"ok": ok, "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        for nm in made:
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


def gate_dispatch(op, args_json):
    args = {}
    if isinstance(args_json, str) and args_json.strip():
        try:
            args = json.loads(args_json)
        except Exception as e:
            return _j({"ok": False, "error": "args 不是合法 JSON: %s" % str(e)[:120]})
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    ops = {"plan": gate_plan, "run": gate_run, "selftest": gate_selftest, "help": gate_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown gate op", "op": op, "ops": sorted(ops)})
    import inspect
    try:
        allowed = set(inspect.signature(fn).parameters)
        unknown = sorted(set(kw) - allowed)
        if unknown:
            return _j({"ok": False, "error": "不认识的参数 %s" % unknown, "op": op, "allowed": sorted(allowed)})
    except (TypeError, ValueError):
        pass
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op,
                   "help": gate_help() if op not in ("help",) else None})


class _DshApi(dict):
    def __call__(self, op=None, args=None, **kw):
        if op is None or isinstance(op, dict):
            args, op = (op if isinstance(op, dict) else args), "help"
        payload = args if isinstance(args, str) else _j(dict(args or {}, **kw))
        return json.loads(self["dispatch"](str(op), payload))

    def call(self, op, args=None, **kw):
        return self(op, args, **kw)


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_gate_api = _DshApi({"version": GATE_VERSION, "dispatch": gate_dispatch, "plan": gate_plan,
                               "run": gate_run, "selftest": gate_selftest, "help": gate_help})
