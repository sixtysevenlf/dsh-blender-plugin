"""DSH 内环 runner v2 —— Blender 主线程高频迭代，模型只定目标与验收。

新增（v2）：多目标/约束罚项（penalize）、退火步长（anneal / ns["frac"]）、候选表（top_k / group_key / record）、
跨对象批量（把对象索引当作参数维度 + group_key 分组保留每组最优）、导出可复用脚本（dsh_loop_export）、契约速查（dsh_loop_help）。

spec 字段：
    setup / step / measure      源码字符串；共享命名空间 ns（step 写的变量 measure 能读）
    iterations / budget_ms      硬上限（必须给）
    interval                    每 tick 间隔秒（0 = 事件循环允许的最快速度）
    measure_every / minimize    测量频率 / 最小化还是最大化
    patience                    平台期早停（v0.9.4 · P0-3）：连续这么多次 measure 没有刷新 best 就收工
                                （0 = 关，默认关；stop_reason 记 "plateau"）。只影响"什么时候停"，
                                不改每次迭代的行为 —— 实测 timer 地板 ≈5.7 ms/tick（176 tps），
                                不做"动态调频率"（那个旋钮本来就是 measure_every）
    top_k                       候选表容量（0 = 关闭）；board 按 score 排序去重
    group_key                   分组维度名（如 "obj"）→ 每组各留 top_k（跨对象批量用）
    redraw_every / history_max  重绘频率（0 = 不重绘，最快）/ 指标尾迹长度

measure 里可写：
    ns["score"]       必须（数值，越小越好/越大越好取决于 minimize）
    ns["params"]      参数 dict（收敛时随 best 一起返回）
    ns["metrics"]     命名指标 dict（如 {"ex":..,"ey":..,"ez":..}）→ 随 best/board 返回
    ns["violations"]  违规量 list 或 dict（>0 视为违规，供 penalize 用）
namespace 预置：bpy / K / math / random / numpy(np) / i / frac / st / penalize / anneal / record
"""
import bpy, json, math, random, time, traceback

RUNNER_VERSION = 3


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("runner 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _clean(v):
    """把 ns 里的值转成可 JSON 化的形态（numpy 标量/数组、Vector 等）"""
    try:
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        if isinstance(v, dict):
            return {str(k): _clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_clean(x) for x in v]
        return [float(x) for x in v]
    except Exception:
        try:
            return float(v)
        except Exception:
            return str(v)


def penalize(errors=None, violations=None, weights=None, lam=1.0):
    """多目标 + 约束罚项：score = Σ w_i·E_i + λ·Σ max(0, violation)"""
    if errors is None:
        errors = {}
    if isinstance(errors, (list, tuple)):
        errs = [float(x) for x in errors]
        total = 0.0
        for idx, e in enumerate(errs):
            w = 1.0
            if weights is not None:
                if isinstance(weights, (list, tuple)):
                    w = float(weights[idx]) if idx < len(weights) else 1.0
                elif isinstance(weights, dict):
                    w = float(weights.get(str(idx), 1.0))
            total += w * e
    else:
        total = 0.0
        for k, e in errors.items():
            w = 1.0
            if weights is not None:
                if isinstance(weights, dict):
                    w = float(weights.get(k, 1.0))
                elif isinstance(weights, (list, tuple)):
                    w = float(weights[list(errors.keys()).index(k)]) if len(weights) > list(errors.keys()).index(k) else 1.0
            total += w * float(e)
    pen = 0.0
    if violations:
        vals = violations.values() if isinstance(violations, dict) else violations
        for v in vals:
        # 违规量：>0 才罚
            try:
                v = float(v)
            except Exception:
                continue
            if v > 0:
                pen += v
    return float(total + float(lam) * pen)


def anneal(v_start, v_end, frac, power=1.0):
    """退火：按进度 frac 从 v_start 线性（或 power 次）过渡到 v_end。步长通常 v_start > v_end"""
    f = max(0.0, min(1.0, float(frac)))
    if power and float(power) != 1.0:
        f = f ** float(power)
    return float(v_start) + (float(v_end) - float(v_start)) * f


def _state():
    import sys
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        raise RuntimeError("内环 runner 需要持久内核 K（用 blender_rt_do / engine.act 通道）")
    st = getattr(K, "dsh_loop", None)
    if st is None:
        st = {"running": False, "i": 0, "t0": 0.0, "spec": None, "ns": {}, "best": None,
              "history": [], "board": [], "board_groups": {}, "error": None, "stop": False,
              "done": False, "stop_reason": None, "tps": 0.0, "elapsed_ms": 0.0,
              "timer": None, "step_code": None, "measure_code": None, "version": RUNNER_VERSION}
        K.dsh_loop = st
    return st


def _redraw():
    try:
        for a in bpy.context.screen.areas:
            a.tag_redraw()
    except Exception:
        pass


def _finish(st, reason):
    st["running"] = False
    st["done"] = True
    st["stop_reason"] = reason
    dt = time.perf_counter() - st["t0"]
    st["elapsed_ms"] = round(dt * 1000.0, 1)
    st["tps"] = round(st["i"] / dt, 1) if dt > 0 else 0.0
    fn = st.get("timer")
    if fn is not None:
        try:
            bpy.app.timers.unregister(fn)
        except Exception:
            pass
    _redraw()


def _key(entry):
    return _j(entry.get("params"))


def _insert_sorted(lst, entry, minimize, topk):
    k = _key(entry)
    for i, e in enumerate(lst):
        if _key(e) == k:
            better = entry["score"] < e["score"] if minimize else entry["score"] > e["score"]
            if better:
                lst.pop(i)
                break
            return
    lst.append(entry)
    lst.sort(key=lambda e: e["score"], reverse=(not minimize))
    del lst[int(topk):]


def _track(st, spec, entry):
    minimize = spec["minimize"]
    prev = st["best"]
    if prev is None or (entry["score"] < prev["score"] if minimize else entry["score"] > prev["score"]):
        st["best"] = entry
        st["last_improve_i"] = st["i"]      # v0.9.4（P0-3）：平台期判据只看"最后一次刷新 best 的迭代"
    hist = st["history"]
    hist.append(entry["score"])
    if len(hist) > spec["history_max"]:
        del hist[0]
    topk = int(spec.get("top_k") or 0)
    if topk > 0:
        gk = spec.get("group_key")
        if gk:
            p = entry.get("params") or {}
            g = str(p.get(gk))
            lst = st["board_groups"].setdefault(g, [])
            _insert_sorted(lst, entry, minimize, topk)
        else:
            _insert_sorted(st["board"], entry, minimize, topk)


def _make_recorder(st, spec):
    def record(params=None, score=None, metrics=None, violations=None):
        """measure 里手动登记一个候选（默认用 ns 当前值）"""
        if score is None:
            raise ValueError("record() 需要 score")
        e = {"score": float(score), "i": st["i"], "params": _clean(params), "metrics": _clean(metrics),
             "violations": _clean(violations), "t_ms": round((time.perf_counter() - st["t0"]) * 1000.0, 1)}
        topk = int(spec.get("top_k") or 0)
        if topk > 0:
            gk = spec.get("group_key")
            if gk and isinstance(e["params"], dict):
                _insert_sorted(st["board_groups"].setdefault(str(e["params"].get(gk)), []), e, spec["minimize"], topk)
            else:
                _insert_sorted(st["board"], e, spec["minimize"], topk)
        return e
    return record


def _tick():
    st = _state()
    if not st["running"]:
        return None
    spec = st["spec"]
    try:
        now = time.perf_counter()
        if st["stop"]:
            _finish(st, "stopped")
            return None
        if st["i"] >= spec["iterations"]:
            _finish(st, "iterations")
            return None
        if (now - st["t0"]) * 1000.0 >= spec["budget_ms"]:
            _finish(st, "budget")
            return None
        ns = st["ns"]
        ns["i"] = st["i"]
        ns["frac"] = (st["i"] / float(spec["iterations"])) if spec["iterations"] else 0.0
        exec(st["step_code"], ns)
        st["i"] += 1
        if st["i"] % spec["measure_every"] == 0:
            exec(st["measure_code"], ns)
            score = ns.get("score")
            if score is not None:
                entry = {"score": float(score), "i": st["i"], "params": _clean(ns.get("params")),
                         "metrics": _clean(ns.get("metrics")), "violations": _clean(ns.get("violations")),
                         "t_ms": round((now - st["t0"]) * 1000.0, 1)}
                st["last"] = entry["score"]
                _track(st, spec, entry)
                # v0.9.4（P0-3）：平台期早停 —— 只在"已经有 best"时判，避免 measure 一直没给 score 的循环被误杀
                if (spec["patience"] > 0 and st["best"] is not None
                        and (st["i"] - int(st.get("last_improve_i") or 0)) >= spec["patience"]):
                    _finish(st, "plateau")
                    return None
        if spec["redraw_every"] and st["i"] % spec["redraw_every"] == 0:
            _redraw()
        return spec["interval"]
    except StopIteration:
        _finish(st, "converged")
        return None
    except BaseException:
        st["error"] = traceback.format_exc(limit=4)
        _finish(st, "error")
        return None


def _status_dict(st, history=8, board=0):
    spec = st.get("spec") or {}
    now = time.perf_counter()
    if st["running"]:
        dt = (now - st["t0"]) or 1e-9
        elapsed = dt * 1000.0
        tps = round(st["i"] / dt, 1)
    else:
        elapsed = st.get("elapsed_ms", 0.0)
        tps = st.get("tps", 0.0)
    d = {
        "running": st["running"], "done": st.get("done", False), "i": st["i"],
        "iterations": spec.get("iterations"), "elapsed_ms": round(elapsed, 1), "tps": tps,
        "best": st["best"], "last": st.get("last"), "history_tail": st["history"][-int(history):],
        "error": st["error"], "stop_reason": st.get("stop_reason"),
        "patience": spec.get("patience"), "last_improve_i": st.get("last_improve_i"),
        "board_size": len(st.get("board") or []), "board_groups": len(st.get("board_groups") or {}),
        "runner_version": RUNNER_VERSION,
    }
    if board:
        d["board"] = _board_payload(st, int(board))
    return d


def _board_payload(st, limit):
    out = {"flat": (st.get("board") or [])[:limit]}
    groups = st.get("board_groups") or {}
    if groups:
        out["groups"] = {k: v[:limit] for k, v in list(groups.items())[:limit * 4]}
    return out


def dsh_loop_start(spec=None, **kw):
    st = _state()
    if st["running"]:
        return _j({"ok": False, "err": "loop already running", "status": _status_dict(st)})
    raw = dict(spec or {})
    raw.update(kw)
    s = {
        "setup": raw.get("setup") or "",
        "step": raw.get("step") or "pass",
        "measure": raw.get("measure") or "pass",
        "iterations": int(raw.get("iterations") or 1000),
        "budget_ms": float(raw.get("budget_ms") or 10000),
        "interval": float(raw.get("interval") or 0.0),
        "measure_every": max(1, int(raw.get("measure_every") or 1)),
        # v0.9.4（P0-3）平台期早停：连续 patience 次 measure 没刷新 best 就停（0 = 关，保持旧行为）
        "patience": max(0, int(raw.get("patience") or 0)),
        "minimize": bool(raw.get("minimize", True)),
        "top_k": int(raw.get("top_k") or 0),
        "group_key": (str(raw.get("group_key")) if raw.get("group_key") else None),
        "redraw_every": int(raw.get("redraw_every", 0)),
        "history_max": int(raw.get("history_max") or 400),
    }
    st.update({"running": True, "i": 0, "t0": time.perf_counter(), "spec": s, "ns": {}, "last_improve_i": 0,
               "best": None, "history": [], "board": [], "board_groups": {}, "error": None,
               "stop": False, "done": False, "stop_reason": None, "last": None, "tps": 0.0})
    try:
        st["step_code"] = compile(s["step"], "<dsh_loop.step>", "exec")
        st["measure_code"] = compile(s["measure"], "<dsh_loop.measure>", "exec")
        ns = st["ns"]
        ns["bpy"] = bpy
        ns["K"] = __import__("sys").modules.get("dsh_rt_kernel")
        ns["math"] = math
        ns["random"] = random
        ns["penalize"] = penalize
        ns["anneal"] = anneal
        ns["record"] = _make_recorder(st, s)
        try:
            import numpy as _np
            ns["np"] = _np
        except Exception:
            pass
        if s["setup"]:
            exec(compile(s["setup"], "<dsh_loop.setup>", "exec"), ns)
    except BaseException:
        st["error"] = traceback.format_exc(limit=4)
        st["running"] = False
        return _j({"ok": False, "err": "setup/compile failed", "error": st["error"]})
    st["timer"] = _tick
    bpy.app.timers.register(_tick, first_interval=0.0, persistent=False)
    return _j({"ok": True, "started": True, "runner_version": RUNNER_VERSION,
               "limits": {"iterations": s["iterations"], "budget_ms": s["budget_ms"],
                          "interval": s["interval"], "measure_every": s["measure_every"], "patience": s["patience"],
                          "minimize": s["minimize"], "top_k": s["top_k"], "group_key": s["group_key"]}})


def dsh_loop_status(history=8, board=0):
    return _j(_status_dict(_state(), history, board))


def dsh_loop_stop():
    st = _state()
    st["stop"] = True
    return _j({"ok": True, "stopping": True, "i": st["i"]})


def dsh_loop_board(limit=10, groups=True):
    st = _state()
    payload = _board_payload(st, int(limit))
    if not groups:
        payload.pop("groups", None)
    return _j({"ok": True, "best": st["best"], "board": payload})


def dsh_loop_export(path=None, top=1, include_variants=False, note=""):
    """把 best（或 top-K 候选）导出成可复用脚本：内嵌 setup 源码 + 最优参数，直接 apply。"""
    st = _state()
    spec = st.get("spec") or {}
    best = st.get("best")
    if best is None:
        return _j({"ok": False, "err": "还没有结果（best 为空）"})
    variants = []
    if include_variants:
        if spec.get("group_key"):
            # 批量场景：每个分组（对象）取自己的最优，而不是全局 top-k
            pool = [lst[0] for _g, lst in sorted((st.get("board_groups") or {}).items()) if lst]
        else:
            pool = list(st.get("board") or [])
        pool.sort(key=lambda e: e["score"], reverse=(not spec.get("minimize", True)))
        seen = set()
        for e in pool:
            k = _json_key(e)
            if k in seen:
                continue
            seen.add(k)
            variants.append(e)
            if len(variants) >= max(1, int(top)):
                break
    payload = {"score": best["score"], "params": best.get("params"), "metrics": best.get("metrics"),
               "i": best["i"], "t_ms": best.get("t_ms"), "note": note,
               "spec": {"iterations": spec.get("iterations"), "budget_ms": spec.get("budget_ms"),
                        "minimize": spec.get("minimize"), "group_key": spec.get("group_key"),
                        "top_k": spec.get("top_k")}}
    setup_src = spec.get("setup") or ""
    lines = [
        "# -*- coding: utf-8 -*-",
        "# 由 DSH 内环 runner v%d 导出的可复用脚本（最好成绩 + setup 源码 + 应用步骤）" % RUNNER_VERSION,
        "# 用法：blender_rt_do(code=本文件内容) 或在 Blender 里 exec 本脚本 → 复现找到的参数",
        "import json",
        "BEST = json.loads(" + _py_lit(json.dumps(payload, ensure_ascii=False, indent=2)) + ")",
        "SETUP_SRC = json.loads(" + _py_lit(json.dumps(setup_src, ensure_ascii=False)) + ")",
        "VARIANTS = json.loads(" + _py_lit(json.dumps(variants, ensure_ascii=False)) + ") if " + ("True" if include_variants else "False") + " else []",
        "_ns = {}",
        "exec(SETUP_SRC, _ns)",
        "_apply = _ns.get(\"apply\")",
        "if _apply is None:",
        "    raise RuntimeError(\"setup 里没有定义 apply(params)，无法自动复现；请手动使用 BEST[\\\"params\\\"]\")",
        "_seen = set()",
        "_apply(BEST[\"params\"])",
        "_seen.add(json.dumps(BEST[\"params\"], sort_keys=True))",
        "_applied = 1",
        "for _v in VARIANTS:",
        "    _k = json.dumps(_v.get(\"params\"), sort_keys=True)",
        "    if _k in _seen:",
        "        continue",
        "    _seen.add(_k)",
        "    _apply(_v[\"params\"])",
        "    _applied += 1",
        "print(\"APPLIED \" + json.dumps({\"score\": BEST[\"score\"], \"params\": BEST[\"params\"], \"variants_applied\": _applied - 1}, ensure_ascii=False))",
    ]
    src = "\n".join(lines) + "\n"
    wrote = None
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            wrote = path
        except Exception as e:
            return _j({"ok": False, "err": "写文件失败: %s" % e, "script": src[:200]})
    return _j({"ok": True, "path": wrote, "chars": len(src), "best": payload,
               "variants": len(variants), "script": src})


def _py_lit(s):
    return json.dumps(s)


def _json_key(e):
    return _j(e.get("params"))


def dsh_loop_help():
    return _j({
        "runner_version": RUNNER_VERSION,
        "start": "dsh_loop_start(spec)：spec={setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every, patience}",
        "ns": "setup/step/measure 共享 ns；预置 bpy/K/math/random/np/i/frac/st/penalize/anneal/record",
        "measure": "必须给 ns[score] 赋值；可写 ns[params]、ns[metrics]、ns[violations]",
        "helpers": ["penalize(errors, violations, weights, lam) -> 加权误差 + λ·Σmax(0,v)",
                    "anneal(v_start, v_end, frac, power) -> 退火步长（用 ns[frac]）",
                    "record(params, score, metrics, violations) -> 手动登记候选进候选表",
                    "ns[i] / ns[frac] -> 当前迭代号 / 进度（step 里可读，用于自适应步长）"],
        "ops": "status(history, board) / stop / board(limit, groups) / export(path, top, include_variants) / bench(iterations)",
        "multi_objective": "score = penalize({ex,ey,ez}, violations=[max(0,rot-0.9)], weights={ex:1,ey:1,ez:2}, lam=50)",
        "batch": "参数里带 obj 维度 + group_key=\"obj\" → 每组各留 top_k 个最优",
        "safety": "iteration/budget 双上限 + op=stop 急停 + 出错自动停；step 内不要做文件 I/O",
    })


def dsh_loop_bench(iterations=5000):
    import time as _t
    t0 = _t.perf_counter()
    ns = {}
    for k in range(int(iterations)):
        ns["x"] = k + 1
    dt = _t.perf_counter() - t0
    return _j({"iterations": int(iterations), "ms": round(dt * 1000.0, 2),
               "per_iter_us": round(dt / max(1, int(iterations)) * 1e6, 2)})


# execute_code 每次都是新命名空间 → API 挂到持久内核 K，后续调用用 K.dsh_loop_api[...]
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_loop_api = _KIT.Api({"version": RUNNER_VERSION, "start": dsh_loop_start, "status": dsh_loop_status,
                       "stop": dsh_loop_stop, "board": dsh_loop_board, "export": dsh_loop_export,
                       "help": dsh_loop_help, "bench": dsh_loop_bench})

# ---- v0.9.1（93-B1/B2）：API 可调用化（换成 dict 子类实例，返回已解析对象）----
# 背景：K.dsh_x_api 原来是普通 dict → 进程内 api(args) 报 TypeError: 'dict' object is not callable；
# 且 dispatch 返回 JSON 字符串，调用方还得自己 json.loads。
# 现在：api("op", {…}) 或 api({…}) → dict；api["dispatch"](op, json_str) 仍返回 str（引擎契约不变）。
# 注意：dict 是静态类型，不能对已有实例做 __class__ 赋值（实测 TypeError），所以换成一个新实例。
_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys_api
_K_api = _sys_api.modules.get("dsh_rt_kernel")
if _K_api is not None and isinstance(getattr(_K_api, "dsh_runner_api", None), dict) \
        and not isinstance(getattr(_K_api, "dsh_runner_api", None), _DshApi):
    _K_api.dsh_runner_api = _DshApi(_K_api.dsh_runner_api)
