# -*- coding: utf-8 -*-
"""Split from contract.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .checks import c_check_envelope, c_check_interface, c_check_interference, c_destructive_guard, c_register_component, c_register_connection, c_register_envelope
from .fingerprint import c_advance, c_evidence, c_fingerprint, c_flip, c_ledger, c_report, c_status, c_verify
from .mate import c_mate_check
from .fit import c_fit_help, c_help, c_interference_report



def c_dispatch(op, args=None):
    """统一入口：工具侧发 {op, args}，这里按参数名派发（args 可选再包一层 dict）。"""
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
    fn = c_ops().get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown contract op", "op": op, "ops": sorted(c_ops())})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(kw),
                   "sig_hint": c_help()})


def c_ops():
    return {"reset": c_reset, "help": c_help, "status": c_status,
            "register_component": c_register_component, "register_connection": c_register_connection,
            "register_envelope": c_register_envelope,
            "check_envelope": c_check_envelope, "check_interference": c_check_interference,
            "check_interface": c_check_interface, "destructive_guard": c_destructive_guard,
            "evidence": c_evidence, "ledger": c_ledger, "report": c_report,
            "verify": c_verify, "flip": c_flip, "advance": c_advance,
            "fingerprint": c_fingerprint,
            "mate_check": c_mate_check, "fit_help": c_fit_help,
            "interference_report": c_interference_report}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
def c_selftest():
    # 契约层自检：复位 -> 登记一个真组件 -> 状态可读 -> 复位（登记簿能收能清）
    import json

    def _d(x):
        return json.loads(x) if isinstance(x, str) else x

    import bpy
    import bmesh as _bm
    ev = {}
    nm = "__dsh_ct_cube"
    try:
        c_reset()
        me = bpy.data.meshes.new(nm)
        ob = bpy.data.objects.new(nm, me)
        bpy.context.scene.collection.objects.link(ob)
        bm = _bm.new()
        _bm.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        r = _d(c_register_component("cube1", objects=[nm], tags=["selftest"]))
        ev["register_ok"] = isinstance(r, dict) and r.get("ok") is not False
        st = _d(c_status())
        ev["status_dict"] = isinstance(st, dict)
        ev["components_counted"] = int(st.get("components") or 0) >= 1
        c_reset()
        ev["reset_ok"] = True
        return _j({"ok": all(v is True for v in ev.values()), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    finally:
        o = bpy.data.objects.get(nm)
        if o is not None:
            me2 = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if me2 is not None and me2.users == 0:
                bpy.data.meshes.remove(me2, do_unlink=True)



if _K is not None:
    _K.dsh_contract_api = _KIT.Api({
        "dispatch": _KIT.wrap_dispatch(c_dispatch, {"selftest": c_selftest}),
        "version": CONTRACT_VERSION,
        "reset": c_reset, "help": c_help, "status": c_status,
        "register_component": c_register_component, "register_connection": c_register_connection,
        "register_envelope": c_register_envelope,
        "check_envelope": c_check_envelope, "check_interference": c_check_interference,
        "check_interface": c_check_interface,
        "destructive_guard": c_destructive_guard,
        "evidence": c_evidence, "ledger": c_ledger, "report": c_report,
        "verify": c_verify, "flip": c_flip, "advance": c_advance,
        "fingerprint": c_fingerprint,
        "mate_check": c_mate_check, "fit_help": c_fit_help,
        "interference_report": c_interference_report,
        "selftest": c_selftest})

# ---- v0.9.1（93-B1/B2）：API 可调用化（换成 dict 子类实例，返回已解析对象）----
# 背景：K.dsh_x_api 原来是普通 dict → 进程内 api(args) 报 TypeError: 'dict' object is not callable；
# 且 dispatch 返回 JSON 字符串，调用方还得自己 json.loads。
# 现在：api("op", {…}) 或 api({…}) → dict；api["dispatch"](op, json_str) 仍返回 str（引擎契约不变）。
# 注意：dict 是静态类型，不能对已有实例做 __class__ 赋值（实测 TypeError），所以换成一个新实例。
_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys_api
_K_api = _sys_api.modules.get("dsh_rt_kernel")
if _K_api is not None and isinstance(getattr(_K_api, "dsh_contract_api", None), dict) \
        and not isinstance(getattr(_K_api, "dsh_contract_api", None), _DshApi):
    _K_api.dsh_contract_api = _DshApi(_K_api.dsh_contract_api)
