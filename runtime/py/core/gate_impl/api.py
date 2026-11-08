# -*- coding: utf-8 -*-
"""Split from gate.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .plan import gate_plan
from .run import gate_run
from .selftest import gate_help, gate_selftest

def gate_preflight():
    """开工前体检卡：场景/单位/可用模块/建议先跑的门（只读，不碰对象）。"""
    import bpy
    try:
        sc = bpy.context.scene
        objs = list(bpy.data.objects)
        meshes = [o for o in objs if o.type == "MESH"]
        tris = 0
        for o in meshes:
            try:
                tris += len(o.data.polygons)
            except Exception:
                pass
        K = _kernel()
        available = []
        if K is not None:
            for name in ("dsh_audit_api", "dsh_assembly_api", "dsh_qc_api", "dsh_gate_api",
                         "dsh_txn_api", "dsh_deliver_api", "dsh_blockout_api", "dsh_img_api"):
                available.append(name.replace("dsh_", "").replace("_api", ""))
        return _j({
            "ok": True, "op": "preflight", "version": GATE_VERSION,
            "scene": str(getattr(sc, "name", "")), "file": str(bpy.data.filepath or ""),
            "units": {"system": str(getattr(sc.unit_settings, "system", "")),
                      "scale_length": float(getattr(sc.unit_settings, "scale_length", 1.0) or 1.0)},
            "objects": len(objs), "meshes": len(meshes), "triangles": tris,
            "available_modules": available,
            "recommended": [
                {"op": "audit_scene", "args": {"summary_only": True, "self_intersect": True}, "why": "网格基线（是否干净）"},
                {"op": "gate_run", "args": {"preset": "assembly"}, "why": "装配/出厂门（三态）"},
                {"op": "asm_scan", "args": {"scope": None}, "why": "多件穿模/悬空（需 assembly 模块）"},
            ],
            "notes": ["写 op 过写租约；只读 op 豁免", "拓扑改动前先 gate_checkpoint / txn.snapshot",
                      "deliver_export(require_gate=true) 可把出厂门接到导出前"],
            "next": "gate_checkpoint → 建模/修复 → gate_run 或 gate_deliver",
        })
    except Exception as e:
        return _j({"ok": False, "op": "preflight", "error": "%s: %s" % (type(e).__name__, str(e)[:240])})


def gate_checkpoint(label=None, note=""):
    """拓扑改动前的文件级回退点（包装 txn.snapshot；对象级 mark 不防布尔/合并）。"""
    K = _kernel()
    api = getattr(K, "dsh_txn_api", None) if K is not None else None
    if api is None:
        return _j({"ok": False, "op": "checkpoint",
                   "error": "K.dsh_txn_api 不存在：preload 里加 txn（或先跑 blender_session）",
                   "hint": "headless 预热：preload=\"txn\""})
    try:
        r = api("snapshot", {"label": label, "note": note})
        if isinstance(r, str):
            r = json.loads(r)
        if not isinstance(r, dict):
            return _j({"ok": False, "op": "checkpoint", "error": "txn.snapshot 返回类型异常"})
        r["op"] = "checkpoint"
        r["hint"] = "拓扑类改动前拿到这个 snapshot；失败时可 blender_session(action='txn', op='restore', label=…)"
        return _j(r)
    except Exception as e:
        return _j({"ok": False, "op": "checkpoint", "error": "%s: %s" % (type(e).__name__, str(e)[:240])})


def gate_deliver(dir=None, objects=None, scope=None, name="model", fmt="obj",
                 spec=None, spec_path=None, preset=None, overrides=None,
                 allow_degraded=False, force=False, reason="", verify=True, **kw):
    """先过门再导出：verdict != pass（或非显式 allow_degraded）⇒ blocked，不写文件。

    这是“门只存在不强制”的修复点：deliver_export 自身仍可单独调用（兼容旧流程），
    但本 op 是给弱模型/交付流程的一键强制路径。
    """
    if force and not str(reason or "").strip():
        return _j({"ok": False, "op": "deliver", "blocked": True,
                   "error": "force=true 必须给 reason（写进回执，供审计）"})
    gate_spec = {"spec": spec, "spec_path": spec_path, "preset": preset, "overrides": overrides}
    try:
        gr = gate_run(**gate_spec)
        if isinstance(gr, str):
            gr = json.loads(gr)
    except Exception as e:
        return _j({"ok": False, "op": "deliver", "blocked": True,
                   "error": "gate_run 失败：%s: %s" % (type(e).__name__, str(e)[:240])})
    verdict = (gr or {}).get("verdict")
    pass_ok = (verdict == "pass") or (allow_degraded and verdict == "degraded") or bool(force)
    if not pass_ok:
        return _j({"ok": False, "op": "deliver", "blocked": True, "verdict": verdict,
                   "gate": gr,
                   "error": "门未通过（verdict=%s）：不导出" % verdict,
                   "hint": "看 gate.failed/degraded/skipped；要降级交付显式 allow_degraded=true；真要强导必须 force=true+reason"})
    K = _kernel()
    api = getattr(K, "dsh_deliver_api", None) if K is not None else None
    if api is None:
        return _j({"ok": False, "op": "deliver", "verdict": verdict,
                   "error": "K.dsh_deliver_api 不存在：preload 里加 deliver"})
    try:
        dr = api("export", {"dir": dir, "objects": objects, "scope": scope, "name": name, "fmt": fmt,
                            "require_gate": False})
        if isinstance(dr, str):
            dr = json.loads(dr)
    except Exception as e:
        return _j({"ok": False, "op": "deliver", "verdict": verdict,
                   "error": "deliver_export 失败：%s: %s" % (type(e).__name__, str(e)[:240]), "gate": gr})
    out = {"ok": bool(dr.get("ok") is not False), "op": "deliver", "verdict": verdict,
           "gate": gr, "delivery": dr,
           "export_allowed": pass_ok, "forced": bool(force), "allow_degraded": bool(allow_degraded)}
    if verify and dr.get("ok") is not False:
        try:
            vr = api("verify", {"path_or_dir": dir})
            if isinstance(vr, str):
                vr = json.loads(vr)
            out["verification"] = vr
            out["ok"] = out["ok"] and bool(vr.get("ok") is not False)
        except Exception as e:
            out["verification"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
            out["ok"] = False
    return _j(out)


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
    try:
        kw = _KIT.resolve_refs(kw)   # 单步调用也支持 "@工件"
    except KeyError as e:
        return _j({"ok": False, "error": "引用解析失败: %s" % str(e)[:160]})
    ops = {"plan": gate_plan, "run": gate_run, "selftest": gate_selftest, "help": gate_help,
           "preflight": gate_preflight, "checkpoint": gate_checkpoint, "deliver": gate_deliver}
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


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_gate_api = _DshApi({"version": GATE_VERSION, "dispatch": gate_dispatch, "plan": gate_plan,
                               "run": gate_run, "selftest": gate_selftest, "help": gate_help,
                               "preflight": gate_preflight, "checkpoint": gate_checkpoint, "deliver": gate_deliver})
