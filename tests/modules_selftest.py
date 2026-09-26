# -*- coding: utf-8 -*-
"""S1-b/盲区补齐：给此前**没有自检**的 9 个模块（contract/deliver/montage/perf/planner/presets/qc/runner/txn）
跑各自的 <x>_selftest，作为独立回归套件。

为什么要单独一套：这 9 个模块原先没有 selftest —— 重构时是**盲区**（改坏了没人发现）。
现在每个模块自带自检（创建临时对象/数据 → 验核心机制 → 清理），这里只负责汇总。

用法（无头）：preload 这 9 个模块后 exec 本文件；输出 HEADLESS {pass, fail, ...}。
"""
import json

CASES = [
    ("contract", "c_selftest", "dsh_contract_api"),
    ("deliver", "d_selftest", "dsh_deliver_api"),
    ("montage", "montage_selftest", "dsh_montage_api"),
    ("planner", "p_selftest", "dsh_plan_api"),
    ("presets", "p_selftest", "dsh_preset_api"),
    ("qc", "qc_selftest", "dsh_qc_api"),
    ("runner", "dsh_loop_selftest", "dsh_loop_api"),
    ("txn", "t_selftest", "dsh_txn_api"),
]


def main():
    rows = []
    fails = []
    g = globals()
    for name, fn, api_name in CASES:
        api = getattr(K, api_name, None)  # noqa: F821
        try:
            r = api("selftest", {}) if api is not None else None
            ok = bool(r and r.get("ok"))
            ev = (r or {}).get("evidence")
            err = (r or {}).get("error")
        except Exception as e:
            ok, ev, r, err = False, None, None, "%s: %s" % (type(e).__name__, str(e)[:120])
        rows.append({"module": name, "ok": ok, "evidence": ev, "error": err})
        if not ok:
            fails.append({"clause": "%s 自检" % name, "detail": err or ev})
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))
    # perf 特例：该模块由 engine 直调、不注册 K.dsh_perf_api（尾部只有兼容 shim）⇒ 直接调模块级函数
    try:
        fn = g.get("dsh_perf_selftest")
        r = json.loads(fn()) if callable(fn) else None
        ok = bool(r and r.get("ok"))
        rows.append({"module": "perf", "ok": ok, "evidence": (r or {}).get("evidence")})
        if not ok:
            fails.append({"clause": "perf 自检", "detail": (r or {}).get("evidence")})
        print("  [%s] perf（engine 直调路径）" % ("OK" if ok else "FAIL"))
    except Exception as e:
        fails.append({"clause": "perf 自检", "detail": str(e)[:120]})
    print("HEADLESS " + json.dumps({"pass": len(rows) - len(fails), "fail": len(fails), "fails": fails,
                                    "probe": "modules_selftest", "rows": rows}, ensure_ascii=False))


main()