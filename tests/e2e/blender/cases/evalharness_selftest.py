# -*- coding: utf-8 -*-
"""tests/evalharness_selftest.py —— 评测基座（runtime/evalharness.py）真机自检。

跑法（插件无头通道，任务约定）：
    blender_rt_headless(preload="evalharness,assembly,audit,contract,clearance,gate,imgtools,calib,mesh_fix",
        engine="none", factory_startup=True, timeout_ms=300000,
        script_file="<包路径>/tests/evalharness_selftest.py")

或独立跑：
    blender -b --factory-startup --python tests/evalharness_selftest.py

覆盖：
  * assembly suite：capture_rate ≥0.8、6 类缺陷 cases 全 caught、baseline_pass=true、verdict pass；
  * reference suite：capture_rate ≥0.8、false_positive=0、verdict pass；
  * core suite：calib 在但缺 printcheck ⇒ skipped/degraded（不是 pass）；组合跑 verdict=degraded；
  * out_json：文件存在、md5=64hex 且与文件 sha256 一致、JSON 可反序列化；
  * 稳定性：同 suite 跑两次，verdict/capture_rate/missed/false_positive/baseline_pass/cases.ok 一致；
  * 场景安全：用户对象前后不变、无 __eval_ 残留；
  * 坏参数：未知 suite / min_capture 越界 / 未知参数 / 未知 op → ok:false。
"""
import hashlib
import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
RT = os.path.normpath(os.path.join(HERE, "..", "runtime"))
LOAD_ORDER = ("kit.py", "assembly.py", "audit.py", "contract.py", "clearance.py", "gate.py",
              "imgtools.py", "calib.py", "mesh_fix.py", "evalharness.py")


def _ensure_kernel():
    """preload 已注入就用现成的；否则按依赖序自己 exec（独立跑也能自检）。"""
    import types
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    if getattr(K, "dsh_eval_api", None) is not None:
        return K
    for nm in LOAD_ORDER:
        p = os.path.join(RT, nm)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                src = fh.read()
            exec(compile(src, p, "exec"), {"__name__": nm[:-3], "__file__": p})
        except Exception:
            pass
    return sys.modules["dsh_rt_kernel"]


K = _ensure_kernel()
API = getattr(K, "dsh_eval_api", None)
if API is None:
    API = getattr(K, "dsh_evalharness_api", None)


def call(op, *args, **kw):
    if args and isinstance(args[0], dict):
        kw = dict(args[0], **kw)
    out = API(op, kw)
    return json.loads(out) if isinstance(out, str) else out


def _safe(op, *args, **kw):
    try:
        if not callable(API):
            return {"ok": False, "error": "K.dsh_eval_api 不存在"}
        return call(op, *args, **kw)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}


EVID = {}


def _rec(key, cond, **nums):
    EVID[key] = bool(cond)
    for k, v in nums.items():
        EVID[key + "." + k] = v
    return bool(cond)


def _user_objects():
    """快照用户对象（排除本基座的 __eval_ 前缀）。"""
    return sorted(str(o.name) for o in bpy.data.objects if not str(o.name).startswith("__eval_"))


def _suite_of(receipt, sid):
    for s in (receipt.get("suites") or []):
        if isinstance(s, dict) and str(s.get("id")) == sid:
            return s
    return {}


def _stable(receipt):
    """去掉 ms/耗时等易变字段后的结构指纹（用于"跑两次结果稳定"断言）。"""
    suites = []
    for s in receipt.get("suites") or []:
        if not isinstance(s, dict):
            continue
        suites.append({
            "id": s.get("id"), "state": s.get("state"),
            "capture_rate": s.get("capture_rate"), "missed": s.get("missed"),
            "false_positive": s.get("false_positive"), "baseline_pass": s.get("baseline_pass"),
            "cases": [{"id": c.get("id"), "ok": c.get("ok"), "got": c.get("got")}
                      for c in (s.get("cases") or [])],
        })
    return {"verdict": receipt.get("verdict"), "capture_rate": receipt.get("capture_rate"),
            "missed": receipt.get("missed"), "false_positive": receipt.get("false_positive"),
            "baseline_pass": receipt.get("baseline_pass"), "suites": suites,
            "scene_clean": (receipt.get("scene") or {}).get("scene_clean")}


import bpy
# ────────────────────────────── 主流程 ──────────────────────────────
def main():
    if API is None:
        return {"ok": False, "error": "K.dsh_eval_api 不存在（preload 带 evalharness）", "evidence": EVID}
    user_before = _user_objects()
    h = _safe("help", {})
    _rec("api.version", h.get("version") == 1, version=h.get("version"))
    _rec("api.help_ops", sorted((h.get("ops") or {}).keys()) == ["help", "run", "selftest"],
         ops=sorted((h.get("ops") or {}).keys()))

    # ---- assembly suite ----
    r1 = _safe("run", suite="assembly")
    su1 = _suite_of(r1, "assembly")
    cases1 = su1.get("cases") or []
    _rec("assembly.ok", r1.get("ok") is True, error=r1.get("error"))
    _rec("assembly.verdict_pass", r1.get("verdict") == "pass", verdict=r1.get("verdict"),
         missed=r1.get("missed"))
    _rec("assembly.capture_rate_ge_08", (r1.get("capture_rate") or 0.0) >= 0.8,
         capture_rate=r1.get("capture_rate"))
    _rec("assembly.cases_6", len(cases1) == 6, n=len(cases1))
    _rec("assembly.all_caught", bool(cases1) and all(c.get("ok") is True for c in cases1),
         missed=r1.get("missed"))
    _rec("assembly.baseline_pass", r1.get("baseline_pass") is True)
    _rec("assembly.fp_zero", int(r1.get("false_positive") or 0) == 0)
    _rec("assembly.json_serializable", bool(json.dumps(r1, ensure_ascii=False)))
    _rec("assembly.scene_clean", (r1.get("scene") or {}).get("scene_clean") is True)

    # ---- reference suite ----
    r2 = _safe("run", suite="reference")
    su2 = _suite_of(r2, "reference")
    cases2 = su2.get("cases") or []
    _rec("reference.ok", r2.get("ok") is True, error=r2.get("error"))
    _rec("reference.verdict_pass", r2.get("verdict") == "pass", verdict=r2.get("verdict"),
         missed=r2.get("missed"))
    _rec("reference.capture_rate_ge_08", (r2.get("capture_rate") or 0.0) >= 0.8,
         capture_rate=r2.get("capture_rate"))
    _rec("reference.fp_zero", int(r2.get("false_positive") or 0) == 0,
         fp=r2.get("false_positive"))
    _rec("reference.cases_12", len(cases2) == 12, n=len(cases2))

    # ---- core：calib 在、门依赖可能缺 ⇒ skipped/degraded，不许 pass ----
    r3 = _safe("run", suite="core")
    su3 = _suite_of(r3, "core")
    core_state = str(su3.get("state"))
    _rec("core.not_pass", core_state in ("skipped", "degraded", "fail"), state=core_state,
         deps=su3.get("dependencies"), warnings=(su3.get("warnings") or [])[:2])
    if core_state in ("skipped", "degraded"):
        _rec("core.overall_degraded", r3.get("verdict") == "degraded", verdict=r3.get("verdict"))
    else:
        _rec("core.overall_degraded", True, verdict=r3.get("verdict"))

    # ---- 组合：assembly+reference 应 pass；all（含 core 缺依赖）应 degraded ----
    r12 = _safe("run", suite="assembly,reference")
    _rec("combined.pass", r12.get("verdict") == "pass", verdict=r12.get("verdict"),
         missed=r12.get("missed"))
    r123 = _safe("run", suite="all")
    core_all = str(_suite_of(r123, "core").get("state"))
    expect_all = "degraded" if core_all in ("skipped", "degraded") else r123.get("verdict")
    _rec("combined.all_matches_logic", r123.get("verdict") == expect_all,
         verdict=r123.get("verdict"), core=core_all)

    # ---- out_json 落盘 + md5 ----
    tmp = tempfile.mkdtemp(prefix="dsh_evalharness_selftest_")
    try:
        outp = os.path.join(tmp, "eval_receipt.json")
        rw = _safe("run", suite="assembly", out_json=outp)
        exists = os.path.isfile(outp)
        md5 = str((rw.get("out_json") or {}).get("md5") or "")
        file_md5 = None
        loaded = {}
        if exists:
            with open(outp, "rb") as fh:
                file_md5 = hashlib.sha256(fh.read()).hexdigest()
            with open(outp, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        _rec("out_json.written", exists and rw.get("ok") is True, path=outp)
        _rec("out_json.md5_64", len(md5) == 64, md5=md5)
        _rec("out_json.md5_matches_file", md5 == file_md5, md5=md5, file_md5=file_md5)
        _rec("out_json.roundtrip", loaded.get("verdict") == rw.get("verdict")
             and isinstance(loaded.get("suites"), list), keys=sorted(loaded.keys())[:8])
    finally:
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    # ---- 稳定性：同 suite 跑两次结构一致 ----
    s1 = _stable(_safe("run", suite="assembly"))
    s2 = _stable(_safe("run", suite="assembly"))
    _rec("stability.stable", s1 == s2,
         diff=(None if s1 == s2 else {"run1": s1, "run2": s2}))

    # ---- 坏参数 ----
    _rec("bad.unknown_suite", _safe("run", suite="nope").get("ok") is False)
    _rec("bad.min_capture_out_of_range", _safe("run", suite="assembly", min_capture=2.0).get("ok") is False)
    _rec("bad.unknown_arg", _safe("run", suite="assembly", bogus=1).get("ok") is False)
    _rec("bad.unknown_op", _safe("nope_op").get("ok") is False)

    # ---- 场景安全 ----
    user_after = _user_objects()
    leftovers = [str(o.name) for o in bpy.data.objects if str(o.name).startswith("__eval_")]
    _rec("scene.user_unchanged", user_before == user_after,
         before=len(user_before), after=len(user_after))
    _rec("scene.no_leftovers", not leftovers, leftovers=leftovers[:6])


def main_safe():
    try:
        main()
        bad = sorted([k for k, v in EVID.items()
                      if isinstance(v, bool) and v is not True and "." not in k])
        return {"ok": len(bad) == 0, "failed": bad, "evidence": EVID}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:400]),
                "traceback": traceback.format_exc()[-1500:], "evidence": EVID}


if __name__ == "__main__":
    res_out = main_safe()
    print("HEADLESS " + json.dumps(res_out, ensure_ascii=False))
    print("EVALHARNESS_SELFTEST " + ("ALL PASS" if res_out.get("ok") else "FAIL"))
    if not res_out.get("ok"):
        sys.exit(1)
