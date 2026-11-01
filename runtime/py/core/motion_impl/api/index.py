# -*- coding: utf-8 -*-
"""motion dispatch/reset + kernel API registration (entry surface preserved)."""
from ..common import *  # noqa: F401,F403
from ..infer import motion_infer_axis, motion_joint, motion_joints
from ..measure import motion_measure
from ..urdf import motion_export_urdf
from ..usda import motion_export_usda
from .joints import motion_help, motion_status
from .selftest import motion_selftest


# ============================================================ 派发

def motion_reset():
    K = _kernel()
    if K is None:
        return _j({"ok": False, "error": "需要持久内核 K"})
    K.dsh_motion = {"joints": {}, "exports": [], "log": [], "seq": 0}
    return _j({"ok": True, "op": "reset", "note": "只清 K.dsh_motion（不动场景、不删文件）"})


def motion_dispatch(op, args=None):
    """统一入口：工具侧发 {op, args}；args 里再包一层 {"args": {...}} 也解包（与契约层同口径）。"""
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
    fn = motion_ops().get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown motion op", "op": op, "ops": sorted(motion_ops())})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op,
                   "given": sorted(kw), "sig_hint": motion_help()})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "op": op,
                   "traceback": traceback.format_exc()[-800:]})


def motion_ops():
    return {"joint": motion_joint, "joints": motion_joints, "infer_axis": motion_infer_axis,
            "measure": motion_measure, "export_urdf": motion_export_urdf,
            "export_usda": motion_export_usda, "status": motion_status, "help": motion_help,
            "selftest": motion_selftest, "reset": motion_reset}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_motion_api = _KIT.Api({"version": MOTION_VERSION, "dispatch": motion_dispatch,
                         "joint": motion_joint, "joints": motion_joints,
                         "infer_axis": motion_infer_axis, "measure": motion_measure,
                         "export_urdf": motion_export_urdf, "export_usda": motion_export_usda,
                         "status": motion_status, "help": motion_help,
                         "selftest": motion_selftest, "reset": motion_reset,
                         "ops": motion_ops})

# ---- v0.9.1（93-B1/B2）：API 可调用化（换成 dict 子类实例，返回已解析对象）----
# 背景：K.dsh_x_api 原来是普通 dict → 进程内 api(args) 报 TypeError: 'dict' object is not callable；
# 且 dispatch 返回 JSON 字符串，调用方还得自己 json.loads。
# 现在：api("op", {…}) 或 api({…}) → dict；api["dispatch"](op, json_str) 仍返回 str（引擎契约不变）。
# 注意：dict 是静态类型，不能对已有实例做 __class__ 赋值（实测 TypeError），所以换成一个新实例。
_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys_api
_K_api = _sys_api.modules.get("dsh_rt_kernel")
if _K_api is not None and isinstance(getattr(_K_api, "dsh_motion_api", None), dict) \
        and not isinstance(getattr(_K_api, "dsh_motion_api", None), _DshApi):
    _K_api.dsh_motion_api = _DshApi(_K_api.dsh_motion_api)
