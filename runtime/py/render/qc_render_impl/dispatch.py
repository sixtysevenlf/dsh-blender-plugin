# -*- coding: utf-8 -*-
"""Split from qc_render.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .lock import render_lock
from .rig import _device_info, _device_line
from .main import qc_render_views
from .help import qc_render_help
from .selftest import qc_render_selftest
from .parts import PARTS_PALETTE

def qc_render_view_menu():
    """纯文本速查（按组分列具名视角）：给提示词/人直接抄。"""
    return _j({"ok": True, "version": QC_RENDER_VERSION, "menu_text": view_menu_text(),
               "groups": [{"id": g, "label": lb} for g, lb in VIEW_GROUPS]})


def qc_render_device_info():
    """当前进程的渲染设备实测（不渲染、不改设置）—— 排查"是不是悄悄跑了 CPU"的第一站。"""
    info = _device_info()
    return _j({"ok": True, "version": QC_RENDER_VERSION, "device": info, "device_line": _device_line(info)})


def qc_render_dispatch(op, args=None):
    """op 两种拼写都认 —— engine 路由会把 `qc_render_<X>` 的前 3 个字符剥掉，于是收到的是 `render_<X>`：

        blender_op(op="qc_render_views", args={...})     → dispatch("render_views")
        blender_op(op="qc_render_help")                  → dispatch("render_help")
        blender_op(op="qc_render_selftest", args={...})  → dispatch("render_selftest")
        blender_op(op="qc_render_catalog")               → dispatch("render_catalog")
        blender_op(op="qc_render_device_info")           → dispatch("render_device_info")
        blender_op(op="qc_render_lock", args={...})      → dispatch("render_lock")   # v0.9.1 E1

    坑（v0.8.11 修）：老 dict 里只登记了 "render_views"，所以 docs/操作教程.md 里写的
    `op="qc_render_help"` 其实一直落到 unknown op（实际收到的是 "render_help"）—— 这里把
    render_* 别名补齐，老拼写（views/render_views/qc_render_views）一个不动。
    参数容忍 JSON 字符串 / {"args": {...}} 再包一层（照抄 qc_render_views 的既有解包口径）。
    v0.9.1（B1/B2）：dispatch **仍然返回 str**（引擎契约不变）；要 dict 走 _DshApi 调用（api("render_views", {...})）。
    """
    import inspect
    ops = {"views": qc_render_views, "render_views": qc_render_views, "qc_render_views": qc_render_views,
           "help": qc_render_help, "render_help": qc_render_help,
           "selftest": qc_render_selftest, "render_selftest": qc_render_selftest,
           "catalog": qc_render_view_catalog, "views_catalog": qc_render_view_catalog,
           "render_catalog": qc_render_view_catalog, "render_views_catalog": qc_render_view_catalog,
           "view_menu": qc_render_view_menu, "render_view_menu": qc_render_view_menu,
           "device_info": qc_render_device_info, "render_device_info": qc_render_device_info,
           # ---- v0.9.1（E1）：渲染锁（render_lock / lock / qc_render_lock 三种拼写都认）
           # engine.mjs 的 render_* 分支是 dispatch(op.slice(7))，所以 op="render_lock"→"lock"、
           # op="render_status"→"status"、op="qc_render_lock"→"render_lock" 都要有落点。
           "render_lock": render_lock, "lock": render_lock, "qc_render_lock": render_lock,
           "status": render_lock, "render_status": render_lock}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown qc_render op", "op": op, "ops": sorted(ops)})
    # 按签名决定要不要喂参数：**不再用 "TypeError 就重试"** —— 渲染中途抛 TypeError 会被重试一遍（重复出图）
    try:
        params = inspect.signature(fn).parameters
        wants = any(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in params.values())
    except Exception:
        wants = True
    try:
        if not wants:
            return fn()
        return fn(_unpack_args(args) if args is not None else {})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_qc_render_api = _DshApi({
                            "version": QC_RENDER_VERSION, "dispatch": qc_render_dispatch,
                            "render_views": qc_render_views, "help": qc_render_help,
                            "selftest": qc_render_selftest,
                            # v0.8.11 新增（只加不改）：
                            "view_catalog": qc_render_view_catalog, "view_menu_text": view_menu_text,
                            "view_menu": qc_render_view_menu, "resolve_view_names": resolve_view_names,
                            "device_info": qc_render_device_info, "parts_palette": PARTS_PALETTE,
                            # v0.9.1 新增（只加不改）：
                            "render_lock": render_lock, "lock": render_lock,
                            "view_menu_full_text": view_menu_full_text, "vt_options": _vt_options,
                            "default_views_fallback": list(DEFAULT_VIEWS_FALLBACK),
                            "default_views_legacy": list(DEFAULT_VIEWS_LEGACY)},
                           default_op="render_views")
