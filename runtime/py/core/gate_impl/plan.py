# -*- coding: utf-8 -*-
"""Split from gate.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

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
                             # v0.9.6：这些键"一个都没给"⇒ 该门 skipped（未配置/不适用），
                             # 显式列进回执 skipped[]；v3 起 skipped 会把顶层压成 degraded。"组"= 候选键名的或关系。
                             "skip_if_missing": _norm_skip_groups(g.get("skip_if_missing")),
                             # v3：判据为真但证据不可信 ⇒ 降级；echo 把真实字段带进回执做证据。
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


# ── v1.0.2（门禁合规）· 受限 AST 求值器 ────────────────────────────────────────
# 背景：本函数原先走的是内置的**动态求值入口**。语义上它已经被 `_check_ast` 限死在很小的
#   语法子集里（不许下划线属性、调用只许 ALLOWED_FUNCS），但那个入口的"名字+左括号"本身
#   就会被供应链扫描器判为 high（DANGEROUS_DYNAMIC_EXECUTION）——与其把关键词藏起来过检，
#   不如把求值器写成"逐节点按白名单算"，白名单与 `_check_ast` 逐条对齐（它仍是唯一入口）。
#   注：本文件与 runtime/ 下另有几处"用 exec 加载源码再执行"的既有写法（加载 spec 模块 /
#   跑测试脚本），它们是同一个受限内核里的常规用法，**不在**扫描器那条规则上，故原样不动。
_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}
_CMP_OPS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def _eval_node(node, env, label):
    """按白名单**逐节点**求值（与旧的受限动态求值语义对齐）。

    语义要点（都只在"更严"的方向上与内置实现不同）：
      · 名字只从 env 取；取不到 → 直接报错（旧版走到 builtins 空表才 NameError）；
      · 调用只允许 ALLOWED_FUNCS，且不支持 `*args` / `**kwargs`（`_check_ast` 本就不放行）；
      · BoolOp 返回短路处的操作数本身（不是 bool），Compare 支持链式 `a < b < c`；与 Python 一致。
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env, label)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError("%s 里的名字不可用：%s" % (label, node.id))
    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand, env, label)
        if isinstance(node.op, ast.Not):
            return not val
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        raise ValueError("%s 里有一元运算符不受支持：%s" % (label, type(node.op).__name__))
    if isinstance(node, ast.BoolOp):
        want_and = isinstance(node.op, ast.And)
        val = _eval_node(node.values[0], env, label)
        for nxt in node.values[1:]:
            if (want_and and not val) or ((not want_and) and val):
                return val
            val = _eval_node(nxt, env, label)
        return val
    if isinstance(node, ast.BinOp):
        fn = _BIN_OPS.get(type(node.op))
        if fn is None:
            raise ValueError("%s 里有二元运算符不受支持：%s" % (label, type(node.op).__name__))
        return fn(_eval_node(node.left, env, label), _eval_node(node.right, env, label))
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env, label)
        for op_node, comp in zip(node.ops, node.comparators):
            fn = _CMP_OPS.get(type(op_node))
            if fn is None:
                raise ValueError("%s 里有比较运算符不受支持：%s" % (label, type(op_node).__name__))
            right = _eval_node(comp, env, label)
            if not fn(left, right):
                return False
            left = right                      # 链式比较：a < b < c
        return True
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ValueError("%s 不许访问下划线属性" % label)
        return getattr(_eval_node(node.value, env, label), node.attr)
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if type(sl).__name__ == "Index":      # Python < 3.9 的 ast.Index 包装
            sl = sl.value
        return _eval_node(node.value, env, label)[_eval_node(sl, env, label)]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(e, env, label) for e in node.elts)
    if isinstance(node, ast.List):
        return [_eval_node(e, env, label) for e in node.elts]
    if isinstance(node, ast.Call):
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise ValueError("%s 的函数调用不支持 *args / **kwargs" % label)
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCS:
            raise ValueError("%s 只允许这些函数：%s" % (label, sorted(ALLOWED_FUNCS)))
        return env[node.func.id](*[_eval_node(a, env, label) for a in node.args])
    raise ValueError("%s 里有不允许的语法：%s" % (label, type(node).__name__))


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
    # v1.0.2：不再用内置 eval —— 逐节点按白名单求值（语法白名单仍是上面的 _check_ast）
    return bool(_eval_node(tree.body, env, label))


def _aggregate(states):
    """整包三态（v3 收紧）：任一 fail/error ⇒ fail；否则任一 **degraded 或 skipped** ⇒ degraded；否则 pass。

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

    v0.9.6（复审修复）：**三态不总在顶层**。`audit_connectivity` 的顶层 `ok` 只表示"分析跑完了"，
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


