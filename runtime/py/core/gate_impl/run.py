# -*- coding: utf-8 -*-
"""Split from gate.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .plan import _aggregate, _collect_echo, _eval_pass_if, _expr_names, _load_spec, _norm_spec, _status_of

def _missing_skip_groups(args, groups):
    """skip_if_missing 判据：每个内层组是"候选键名的或"；任一组一个都没给 ⇒ 该组未配置。"""
    miss = []
    for grp in groups or []:
        keys = [str(k) for k in (grp if isinstance(grp, (list, tuple)) else [grp])]
        if not any(k in (args or {}) for k in keys):
            miss.append(keys)
    return miss


def _norm_overrides(overrides):
    """overrides={门id: {args键: 值}}（v0.9.6）：给 preset 里"现场才知道"的 args 补来源。
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


def _gate_prepare(spec, spec_path, preset, overrides):
    """加载 spec + 归一 + overrides 合并；返回 (sp, src, md5, ov)。"""
    data, src, md5 = _load_spec(spec, spec_path, preset)
    sp = _norm_spec(data)
    _ov = _norm_overrides(overrides)
    for g in sp["gates"]:
        if g["id"] in _ov:
            g["args"] = dict(g["args"] or {}, **_ov[g["id"]])
    return sp, src, md5, _ov


def _gate_missing_row(g, api):
    """该门模块没 preload / 来源未配置 ⇒ 直接给 error/skipped 行（跑不了）；可跑返回 None。"""
    rec = {"id": g["id"], "op": g["op"]}
    attr, _sub = OP_MAP[g["op"]]
    if api is None:
        rec.update({"state": "error", "error": "该门需要模块 %s（preload 里没注入）" % attr})
        return rec
    # v0.9.6：未配置来源 ⇒ 该门 skipped（未跑）。一定显式列出，且会把顶层压成 degraded。
    _miss = _missing_skip_groups(g["args"], g.get("skip_if_missing"))
    if _miss:
        rec.update({"state": "skipped", "ms": 0,
                    "reason": "该门未配置来源（缺 %s）⇒ 未检查（skipped ≠ 通过；v3 起顶层因此判 degraded）"
                              % " / ".join("|".join(m) for m in _miss),
                    "hint": "要真判这一门：spec 里补上这些 args，或用 overrides={「%s」:{…}} 覆盖 ——"
                            "只有每门都真跑过且都过，顶层才会 pass" % g["id"],
                    "skipped_args": _miss})
        return rec
    return None


def _gate_call(api, sub, args):
    """调子 op；返回 (sub_receipt, ms, err)。"""
    t1 = time.time()
    try:
        return api(sub, args or {}), int((time.time() - t1) * 1000), None
    except Exception as e:
        return None, int((time.time() - t1) * 1000), "%s: %s" % (type(e).__name__, str(e)[:180])


def _gate_envelope_error(rec, sub_receipt):
    """子 op 自己报错（参数不对 / 前置不满足）⇒ 把它的话端上来，别拿 pass_if 的错误去误导。"""
    _is_err_env = (isinstance(sub_receipt, dict) and sub_receipt.get("ok") is False
                   and bool(sub_receipt.get("error") or sub_receipt.get("stage") or sub_receipt.get("reason")))
    if not _is_err_env:
        return False
    rec["state"] = "error"
    rec["error"] = str(sub_receipt.get("error") or sub_receipt.get("reason") or "该门的前置 op 返回 ok=false")[:200]
    rec["key"] = {k: sub_receipt.get(k) for k in ("error", "hint", "reason") if k in sub_receipt}
    return True


def _gate_apply_judgement(g, rec, sub_receipt):
    """pass_if / degrade_if 判定 + key 证据；返回 False 表示判据求值失败（该门 error）。"""
    if g["pass_if"]:
        try:
            got = _eval_pass_if(g["pass_if"], sub_receipt if isinstance(sub_receipt, dict) else {},
                                label="pass_if")
        except Exception as e:
            rec.update({"state": "error", "error": "pass_if 求值失败：%s" % str(e)[:200]})
            return False
        # v0.9.6（标定）：**写了 pass_if 就以它为准** —— 子 op 的 ok=false 可能只是"某一件没过"
        # （如 print_report 的 overhang），不能压过显式判据；只有 degraded 例外：
        # 底层没跑完（ok=null）时不许读成通过。
        if rec["state"] != "degraded":
            rec["state"] = "pass" if got else "fail"
        rec["pass_if"] = g["pass_if"]
        rec["pass_if_value"] = bool(got)
    # v3：**判据为真 ≠ 结论可信**。degrade_if 命中（如 min_mm 低于测量分辨率）⇒ 该门 degraded，
    # 绝不许假通过；fail/degraded 已经是更强的结论，不被 degrade_if 覆盖（fail 优先）。
    if g.get("degrade_if") and rec["state"] == "pass":
        try:
            _dg = _eval_pass_if(g["degrade_if"], sub_receipt if isinstance(sub_receipt, dict) else {},
                                label="degrade_if")
        except Exception as e:
            rec.update({"state": "error", "error": "degrade_if 求值失败：%s" % str(e)[:200]})
            return False
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
    return True


def _gate_row(g, detail):
    """单门：跳过/模块缺失/异常/执行/判据。返回 (row, stop_kind)。

    stop_kind：never（不因 stop_on_fail 中断）/ always（执行异常即中断）/ if_fail（fail/error 才中断）。
    """
    attr, sub = OP_MAP[g["op"]]
    api = getattr(_kernel(), attr, None)   # 模块间调用：API 都挂在 dsh_rt_kernel 上
    miss_row = _gate_missing_row(g, api)
    if miss_row is not None:
        return miss_row, "never"
    rec = {"id": g["id"], "op": g["op"]}
    sub_receipt, ms, call_err = _gate_call(api, sub, g["args"])
    if call_err is not None:
        rec.update({"state": "error", "error": call_err, "ms": ms})
        return rec, "always"
    rec["ms"] = ms
    rec["state"] = _status_of(sub_receipt)
    # v3：echo —— 把声明的**真实字段**（分辨率 / min_mm / warning…）带进回执做证据（可审计）
    if g.get("echo") and isinstance(sub_receipt, dict):
        rec["echo"] = _collect_echo(sub_receipt, g["echo"])
    if _gate_envelope_error(rec, sub_receipt):
        return rec, "always"
    if not _gate_apply_judgement(g, rec, sub_receipt):
        return rec, "never"
    if detail:
        rec["receipt"] = sub_receipt
    return rec, "if_fail"


def _gate_payload(sp, rows, src, md5, ov, t0):
    """三态汇总回执（verdict/coverage/skipped_note/overrides）。"""
    states = [r.get("state") for r in rows]
    verdict = _aggregate(states)
    _checked = [r["id"] for r in rows if r.get("state") in ("pass", "fail", "degraded", "error")]
    _skipped = [r["id"] for r in rows if r.get("state") == "skipped"]
    payload = {"ok": verdict == "pass", "verdict": verdict, "name": sp["name"], "units": sp["units"],
               "tolerance_mm": sp["tolerance_mm"], "spec_source": src, "spec_md5": md5,
               "gates": rows, "ran": len(rows), "total": len(sp["gates"]),
               "failed": [r["id"] for r in rows if r.get("state") in ("fail", "error")],
               "degraded": [r["id"] for r in rows if r.get("state") == "degraded"],
               # v0.9.6+ v3：跳过的门必须看得见，而且**会把顶层压成 degraded**
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
    if ov:
        payload["overrides_applied"] = {k: sorted(v.keys()) for k, v in ov.items()}
    return payload


def _gate_write_json(payload, out_json):
    """out_json 落盘（失败不致命，记 out_json_error）。"""
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
    return payload


def gate_run(spec=None, spec_path=None, preset=None, detail=False, out_json=None, stop_on_fail=False,
             overrides=None):
    """跑完整包：逐门执行 → 三态汇总。回执默认只带**判据用到的字段**（自带瘦身）。

    overrides（v0.9.6）：{门id: {args键: 值}} —— 只并进那一门的 args，不改判据。preset 里
    "现场才知道"的参数（如跨件干涉的两侧对象）就靠它补；没补时声明了 skip_if_missing 的门记 skipped，
    而 **skipped 会把顶层压成 degraded**（v3：跳过 ≠ 通过，不许为"健康默认 preset"变绿）。

    degrade_if：判据为真但证据不可信（如 min_mm 低于测量分辨率）⇒ 该门 degraded，echo 提供真实读数。
    """
    sp, src, md5, _ov = _gate_prepare(spec, spec_path, preset, overrides)
    t0 = time.time()
    rows = []
    for g in sp["gates"]:
        row, stop_kind = _gate_row(g, detail)
        rows.append(row)
        if stop_on_fail and (stop_kind == "always" or
                             (stop_kind == "if_fail" and row.get("state") in ("fail", "error"))):
            break
    payload = _gate_payload(sp, rows, src, md5, _ov, t0)
    if out_json:
        payload = _gate_write_json(payload, out_json)
    return _j(payload)

