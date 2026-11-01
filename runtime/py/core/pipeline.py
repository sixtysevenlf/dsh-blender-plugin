# -*- coding: utf-8 -*-
"""DSH 流水线（S5）：**工件注册表 + 步骤编排** —— 让 176 个 op 真正配合起来，而不是各自为战。

解决的痛点（重构前的实测）：
  · op 之间只能"人肉搬运"：vehicle_sections 回一份 5,656 字符的 JSON，vehicle_loft 要把 stations 数组再粘回去；
  · 每一步的交接都经过模型上下文 ⇒ 又贵又容易抄错（字段名、单位、层次）；
  · skill 里那 18 条多步链全是散文，模型要自己翻译成 N 次工具调用。

机制：
  · **工件注册表**：任何一步的产出可命名存放（存 K.dsh_artifacts，随持久内核 K 活一个 Blender 会话）；
  · **@引用**：后续步骤的 args 里写 "@名字" 或 "@名字.a.b"（点路径），运行时解析成真值；
  · **pipe_run**：一次调用把整条链跑完，逐步留回执（耗时/是否 ok/产出键名），失败按 stop_on_fail 停。

协议：
    pipe_put(name, value)            —— 手工存一个工件（模型算出来的数字/数组也能存）
    pipe_run(steps=[{"api","op","args","out"}…])  —— 串起来跑
    pipe_get / pipe_list / pipe_clear

⚠ 边界：@引用只在**本模块解析**（即可供 pipe_* 与走 pipe_run 的链使用）；
  单发 plan op 不会自动解引用 —— 那是引擎路由层的事，留待后续（避免动脆弱的写路径）。
"""
import json
import sys
import time

PIPE_VERSION = 1

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("pipeline.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")
_j = _KIT.j


def _reg():
    r = getattr(_K, "dsh_artifacts", None)
    if r is None:
        r = {}
        _K.dsh_artifacts = r
    return r


def _brief(v, depth=0):
    """给回执用的简短类型描述（不把大对象塞进回执）。"""
    if isinstance(v, dict):
        return {"type": "dict", "keys": sorted(list(v.keys()))[:12], "n": len(v)}
    if isinstance(v, (list, tuple)):
        return {"type": "list", "n": len(v), "first": (str(v[0])[:60] if v else None)}
    if isinstance(v, str):
        return {"type": "str", "n": len(v), "head": v[:60]}
    return {"type": type(v).__name__, "value": (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:40])}


def pipe_put(name, value=None, note=None):
    """存一个工件（name 不带 @；value 任意 JSON 兼容结构）。"""
    nm = str(name or "").lstrip("@")
    if not nm:
        return _j({"ok": False, "error": "name 必填"})
    reg = _reg()
    reg[nm] = {"value": value, "note": note, "ts": time.time()}
    return _j({"ok": True, "name": nm, "brief": _brief(value), "count": len(reg)})


def pipe_get(name, path=None):
    """取工件（可选点路径 path，如 "stations" 或 "package.length_mm"）。"""
    nm = str(name or "").lstrip("@")
    reg = _reg()
    if nm not in reg:
        return _j({"ok": False, "error": "没有这个工件: %s" % nm, "have": sorted(reg.keys())})
    v = reg[nm]["value"]
    if path:
        try:
            for part in str(path).split("."):
                v = v[int(part)] if isinstance(v, list) else v[part]
        except Exception as e:
            return _j({"ok": False, "error": "路径取不到: %s（%s）" % (path, str(e)[:80])})
    return _j({"ok": True, "name": nm, "value": v})


def pipe_list():
    reg = _reg()
    return _j({"ok": True, "count": len(reg),
               "artifacts": [{"name": k, "brief": _brief(v["value"]), "note": v.get("note")} for k, v in sorted(reg.items())]})


def pipe_clear(name=None):
    reg = _reg()
    if name:
        nm = str(name).lstrip("@")
        reg.pop(nm, None)
    else:
        reg.clear()
    return _j({"ok": True, "count": len(reg)})


def _resolve(v, reg):
    """把 args 里的 "@名字[.路径]" 解析成真值（递归处理 dict/list）。"""
    if isinstance(v, str) and v.startswith("@"):
        body = v[1:]
        parts = body.split(".")
        nm = parts[0]
        if nm not in reg:
            raise KeyError("引用了不存在的工件 @%s（现有：%s）" % (nm, ", ".join(sorted(reg.keys())) or "无"))
        cur = reg[nm]["value"]
        for p in parts[1:]:
            cur = cur[int(p)] if isinstance(cur, list) else cur[p]
        return cur
    if isinstance(v, dict):
        return {k: _resolve(x, reg) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve(x, reg) for x in v]
    return v


def pipe_run(steps=None, stop_on_fail=True, dry_run=False):
    """按序跑一串步骤；每步 {api, op, args, out}，args 里可用 "@工件[.路径]" 引用前面的产出。"""
    reg = _reg()
    if not steps:
        return _j({"ok": False, "error": "steps 必填：[{api, op, args, out}]"})
    rows, failed = [], []
    t_all = time.time()
    for i, st in enumerate(list(steps)):
        s = dict(st or {})
        api_name = str(s.get("api") or "")
        op = str(s.get("op") or "")
        out = s.get("out")
        row = {"i": i, "api": api_name, "op": op, "out": out}
        try:
            args = _resolve(s.get("args") or {}, reg)
        except Exception as e:
            row.update({"ok": False, "error": "引用解析失败: %s" % str(e)[:160]})
            rows.append(row)
            failed.append(i)
            if stop_on_fail:
                break
            continue
        if dry_run:
            row.update({"ok": True, "dry_run": True, "args_keys": sorted(args.keys())})
            rows.append(row)
            continue
        api_obj = _KIT.api(api_name)
        if api_obj is None:
            row.update({"ok": False, "error": "模块未注入: %s（先让该族 op 被调用一次，或加进 preload）" % api_name})
            rows.append(row)
            failed.append(i)
            if stop_on_fail:
                break
            continue
        t0 = time.time()
        try:
            res = api_obj(op, args)
        except Exception as e:
            res = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        row["ms"] = int((time.time() - t0) * 1000)
        if not isinstance(res, dict):
            row.update({"ok": False, "error": "模块返回非 dict（%s）" % type(res).__name__})
            rows.append(row)
            failed.append(i)
            if stop_on_fail:
                break
            continue
        row["ok"] = bool(res.get("ok", True))
        row["keys"] = sorted(res.keys())[:10]
        if not row["ok"]:
            row["error"] = str(res.get("error") or res.get("reason") or "ok=false")[:160]
        if out:
            nm = str(out).lstrip("@")
            reg[nm] = {"value": res, "note": "%s.%s" % (api_name, op), "ts": time.time()}
            row["stored"] = nm
        rows.append(row)
        if not row["ok"]:
            failed.append(i)
            if stop_on_fail:
                break
    ok = not failed
    return _j({"ok": ok, "verdict": ("pass" if ok else "fail"), "steps": rows,
               "step_count": len(rows), "failed_steps": failed,
               "artifacts": sorted(reg.keys()), "ms": int((time.time() - t_all) * 1000),
               "note": "每步的产出用 out=@名字 存下，后续步骤 args 里写 \"@名字\" 或 \"@名字.a.b\" 引用；"
                       "失败默认即停（stop_on_fail=false 可继续跑完看全貌）"})


def pipe_selftest():
    """自检：工件存取 + @引用解析（含点路径/嵌套）+ 流水线逐步回执 + 失败即停。"""
    ev = {}
    try:
        pipe_clear()
        pipe_put("t", {"a": {"b": [10, 20]}, "s": "hello"})
        ev["get_plain"] = json.loads(pipe_get("t"))["ok"]
        ev["get_path"] = json.loads(pipe_get("t", "a.b"))["value"] == [10, 20]
        ev["get_index"] = json.loads(pipe_get("t", "a.b.1"))["value"] == 20
        reg = _reg()
        ev["resolve_str"] = _resolve("@t.s", reg) == "hello"
        ev["resolve_nested"] = _resolve({"pairs": [{"min_mm": "@t.a.b.0"}]}, reg) == {"pairs": [{"min_mm": 10}]}
        ev["resolve_missing_raises"] = False
        try:
            _resolve("@nope.x", reg)
        except KeyError:
            ev["resolve_missing_raises"] = True
        # 真跑一条两步链：clearance.help → 用它的输出做一次 check（顺带验证 out= 存工件）
        cl = _KIT.api("clearance")
        if cl is not None:
            r = json.loads(pipe_run(steps=[
                {"api": "clearance", "op": "help", "args": {}, "out": "helped"},
                {"api": "clearance", "op": "selftest", "args": {}, "out": "checked"}]))
            ev["run_chain_ok"] = bool(r.get("ok")) and r.get("step_count") == 2
            ev["run_stored"] = "helped" in (r.get("artifacts") or [])
        else:
            ev["run_chain_ok"] = "skip（clearance 未注入）"
        # 失败即停：故意引用不存在的工件
        r2 = json.loads(pipe_run(steps=[{"api": "clearance", "op": "help", "args": {"x": "@nope"}}]))
        ev["fail_fast"] = r2.get("ok") is False and r2.get("failed_steps") == [0]
        pipe_clear()
        ok = all(v is True for v in ev.values())
        return _j({"ok": bool(ok), "version": PIPE_VERSION, "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})


def pipe_help():
    return _j({
        "module": "pipeline.py", "version": PIPE_VERSION,
        "what": "工件注册表 + 步骤编排：把多步链变成一次调用，产出用 @名字 传递，不再人肉搬运 JSON",
        "ops": ["pipe_run(steps)", "pipe_put", "pipe_get", "pipe_list", "pipe_clear", "pipe_selftest", "pipe_help"],
        "step_schema": {"api": "模块名（vehicle/audit/clearance/img…）", "op": "该模块的子 op",
                        "args": "参数（值可写 \"@工件\" 或 \"@工件.a.b\" 引用前面的产出）", "out": "@名字（可选，存下本步回执）"},
        "example": [{"api": "img", "op": "rectify", "args": {"path": "D:/ref/photo.jpg", "quad": [[0, 0], [1, 0], [1, 1], [0, 1]], "out_w": 1600}, "out": "@rect"},
                    {"api": "vehicle", "op": "sections", "args": {"side": "@rect.out", "mm_per_px": 2.0}, "out": "@sec"},
                    {"api": "vehicle", "op": "loft", "args": {"stations": "@sec.stations", "section_shape": "@sec.section_shape"}, "out": "@shell"}],
        "limits": ["@引用只在 pipe_run 内解析（单发 plan op 不会自动解引用）",
                   "工件存活于当前 Blender 会话（K 跨后端重启保留，但 Blender 重启即清空）",
                   "步骤按序同步执行；长任务仍应用 rt_job/headless 那条路"],
    })


_KIT.register("pipe", PIPE_VERSION,
              {"run": pipe_run, "put": pipe_put, "get": pipe_get, "list": pipe_list,
               "clear": pipe_clear, "selftest": pipe_selftest, "help": pipe_help})