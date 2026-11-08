# -*- coding: utf-8 -*-
"""QC API entry: qc_dispatch, qc_render bridges, and the path-level align_iou wrapper."""
from .common import *  # noqa: F401,F403
from .pnggif import _gif_first_frame, gif_to_png
from .load import qc_compare, qc_crop, qc_iou, qc_load, qc_mask, qc_profile, qc_profile_diff, qc_resize_mask
from .align import _align_search, _bbox, _iou_pair, _rotate_mask, qc_align_iou, qc_align_rotate, qc_mask_auto, qc_metrics
from .metrics import qc_compare_auto, qc_help, qc_mask_sweep, qc_robustness_check, qc_self_check

def m_aabb_err(obj_name, target_size):
    ob = bpy.data.objects.get(str(obj_name))
    if ob is None:
        return 1e9
    d = ob.dimensions
    t = [float(x) for x in target_size]
    return float(abs(d[0] - t[0]) + abs(d[1] - t[1]) + abs(d[2] - t[2]))


def _render_view(view_spec, label="measure"):
    K = _kernel()
    api = getattr(K, "dsh_view_api", None) if K is not None else None
    if api is None:
        raise RuntimeError("需要 view.py（K.dsh_view_api）才能出图；插件里 preload='view' 或已由引擎注入")
    spec = dict(view_spec or {})
    spec.setdefault("width", 480)
    spec.setdefault("height", 480)
    d = os.path.join(_out_dir(), "qc_renders")
    os.makedirs(d, exist_ok=True)
    spec["path"] = os.path.join(d, "%s_%s.png" % (str(label), time.strftime("%H%M%S")))
    info = json.loads(api["capture"](json.dumps(spec)))
    if not info.get("ok"):
        raise RuntimeError("出图失败: %s" % info)
    return info["path"]


def m_silhouette_iou(view_spec, ref_path, ref_box, sat=0.13, v=0.74):
    """内环用：渲染指定视角 → 与参考图比 → 返回 1-IoU（越小越好）。',
    **故意用固定对齐（search=False）**：让目标函数可被搜索对齐"刷分"是对优化的污染
    （模型只要把物体缩放就能迁就轮廓）—— 内环要的是诚实指标，离线 QC 才用搜索对齐。"""
    png = _render_view(view_spec, "silhouette")
    res = json.loads(qc_compare_auto(ref_path, ref_box, png, label="silhouette", search=False))
    v2 = res.get("metrics", {}).get("iou_fixed", res.get("iou", 0.0))
    return float(1.0 - float(v2))


def m_profile_err(view_spec, ref_path, ref_box, bins=24, sat=0.13, v=0.74):
    png = _render_view(view_spec, "profile")
    res = json.loads(qc_compare_auto(ref_path, ref_box, png, label="profile", search=False, bins=bins))
    return float(res["profile"]["diff"]["mean_diff_px"])


# ---------------------------------------------------------------- 渲染 harness 转接（v0.8.8）

def _load_qc_render():
    """拿 qc_render 模块注册到内核的 API —— 只认 K.dsh_qc_render_api。

    qc_render 模块由 preload 注入（config/routing.json 的 qc_render 路由固定
    preload="qc,qc_render"；手写 headless 脚本也要把它带上），注册时把 API
    挂到内核上。旧的"现场找 qc_render.py 再 exec(compile(...))"已删除
    二次加载兜底：领域包布局下那条路径只会加载到 facade 的副本，绕过内核注入，
    可能给出与 preload 版本不一致的结果（静默的错值比明确失败更糟）。缺失时
    由 qc_render_views / qc_render_help 返回带 hint 的明确错误。
    """
    K = _kernel()
    return getattr(K, "dsh_qc_render_api", None) if K is not None else None


def qc_render_views(**kw):
    """qc.py 侧转接：把参数整包转给 qc_render.views（多视角渲染 harness）。

    用法（工具侧）：blender_op(op="qc_render_views", args={views:["iso","front"], budget_s:60, ...})
    """
    api = _load_qc_render()
    if api is None:
        return _j({"ok": False, "error": "qc_render 模块不可用：K.dsh_qc_render_api 缺失（qc_render 未注入内核）",
                   "hint": "headless 用 preload='qc,qc_render'；GUI 走 blender_op(op='qc_render_views')"})
    return api["render_views"](kw)


def qc_render_help():
    api = _load_qc_render()
    if api is None:
        return _j({"ok": False, "error": "qc_render 模块不可用：K.dsh_qc_render_api 缺失（qc_render 未注入内核）",
                   "hint": "headless 用 preload='qc,qc_render'；GUI 走 blender_op(op='qc_render_views')"})
    return api["help"]()


def qc_dispatch(op, args=None):
    """统一入口：工具侧用 qc_<op> 调用（load/crop/mask/iou/profile/compare/help...）"""
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
    fn = qc_ops().get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown qc op", "op": op, "ops": sorted(qc_ops())})
    try:
        r = fn(**kw)
        return r if isinstance(r, str) else _j({"ok": True, "result": r})
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(kw), "sig_hint": qc_help()})


def qc_align_iou_op(ref_path, ref_box=None, render_path=None, render_box=None, pad_frac=0.05):
    """路径级 align_iou：读两张图 → 自适应掩膜 → 对齐后算 IoU。

    catalog 的 qc_align_iou 只声明名字；这里补上路径级入口（qc_align_iou 本身吃
    numpy 数组，工具侧无法直接传）。返回 JSON 安全的标量，mask/canvas 数组不进入回执。
    """
    if not ref_path:
        return _j({"ok": False, "error": "align_iou：缺少 ref_path", "op": "align_iou"})
    if not render_path:
        return _j({"ok": False, "error": "align_iou：缺少 render_path", "op": "align_iou"})
    ref_srgb, ref_alpha = qc_load(ref_path)
    if ref_box:
        ref_srgb = qc_crop(ref_srgb, ref_box)
        ref_alpha = qc_crop(ref_alpha, ref_box)
    ref_mask, ref_info = qc_mask_auto(ref_srgb, ref_alpha)
    ren_srgb, ren_alpha = qc_load(render_path)
    if render_box:
        ren_srgb = qc_crop(ren_srgb, render_box)
        ren_alpha = qc_crop(ren_alpha, render_box)
    ren_mask, ren_info = qc_mask_auto(ren_srgb, ren_alpha)
    # qc_align_iou 的坐标约定要求渲染 alpha 的 bbox 从 (0,0) 开始；先把两边裁到
    # 各自掩膜 bbox（纯平移因此被对齐吃掉，留下的是形状相似度），避免远离原点时
    # 画布越界或系统性低分。
    ref_bb = _bbox(ref_mask)
    ren_bb = _bbox(ren_mask)
    if ref_bb is None or ren_bb is None:
        return _j({"ok": False, "error": "align_iou：参考或渲染前景掩膜为空", "op": "align_iou",
                   "masks": {"ref": ref_info, "render": ren_info}})
    rx0, ry0, rx1, ry1 = ref_bb
    nx0, ny0, nx1, ny1 = ren_bb
    ref_crop = ref_srgb[ry0:ry1 + 1, rx0:rx1 + 1]
    ref_mask_crop = ref_mask[ry0:ry1 + 1, rx0:rx1 + 1]
    ren_crop = ren_srgb[ny0:ny1 + 1, nx0:nx1 + 1]
    ren_mask_crop = ren_mask[ny0:ny1 + 1, nx0:nx1 + 1]
    rgba = np.dstack([ren_crop, np.asarray(ren_mask_crop, dtype=np.float32)])
    aligned = qc_align_iou(ref_crop, ref_mask_crop, rgba, pad_frac=pad_frac)
    if aligned is None:
        return _j({"ok": False, "error": "align_iou：参考或渲染前景掩膜为空", "op": "align_iou",
                   "masks": {"ref": ref_info, "render": ren_info}})
    return _j({"ok": True, "op": "align_iou", "iou": round(float(aligned["iou"]), 4),
               "inter": int(aligned["inter"]), "union": int(aligned["union"]),
               "ref_px": int(aligned["ref_px"]), "render_px": int(aligned["render_px"]),
               "scale": aligned["scale"], "masks": {"ref": ref_info, "render": ren_info}})


def qc_ops():
    return {"load": qc_load, "crop": qc_crop, "mask": qc_mask, "mask_auto": qc_mask_auto, "iou": qc_iou,
            "profile": qc_profile, "profile_diff": qc_profile_diff,
            "compare": qc_compare_auto, "compare_auto": qc_compare_auto, "compare_basic": qc_compare,
            "align_search": _align_search, "align_iou": qc_align_iou_op, "align_rotate": qc_align_rotate, "rotate_mask": _rotate_mask,
            "gif_to_png": gif_to_png, "metrics": qc_metrics, "self_check": qc_self_check,
            "robustness_check": qc_robustness_check, "mask_sweep": qc_mask_sweep,
            "resize_mask": qc_resize_mask, "write_png": write_png, "help": qc_help,
            # v0.8.8：渲染 harness（真实现在 qc_render.py，这里转接）
            "render_views": qc_render_views, "qc_render_views": qc_render_views, "render_help": qc_render_help}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
def qc_selftest():
    # QC 自检：IoU/缩放/剖面都用**已知答案**的合成掩码验（收敛到 kit 后数字必须仍然对）
    import json

    def _d(x):
        return json.loads(x) if isinstance(x, str) else x

    import numpy as np
    ev = {}
    try:
        a = np.zeros((10, 10), bool)
        a[:5, :] = True            # 50 px
        b = np.zeros((10, 10), bool)
        b[:, :5] = True            # 50 px，交集 25、并集 75 => IoU = 1/3
        r = _d(qc_iou(a, b))
        ev["iou_known"] = abs(float(r.get("iou")) - (25.0 / 75.0)) < 0.002 and int(r.get("inter")) == 25 and int(r.get("union")) == 75
        m = qc_resize_mask(a, 5, 5)
        ev["resize_shape"] = tuple(np.asarray(m).shape) == (5, 5)
        prof = qc_profile(a, bins=5)
        ev["profile_len"] = len(prof) == 5
        return _j({"ok": all(x is True for x in ev.values()), "evidence": ev, "iou": r.get("iou")})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})



if _K is not None:
    _K.dsh_qc_api = _KIT.Api({"version": QC_VERSION, "dispatch": _KIT.wrap_dispatch(qc_dispatch, {"selftest": qc_selftest}), "compare": qc_compare_auto,
                     "compare_basic": qc_compare, "mask_auto": qc_mask_auto, "metrics": qc_metrics,
                     "self_check": qc_self_check, "mask_sweep": qc_mask_sweep, "load": qc_load, "crop": qc_crop, "mask": qc_mask,
                     "iou": qc_iou, "profile": qc_profile, "profile_diff": qc_profile_diff,
                     "compare_basic": qc_compare, "resize_mask": qc_resize_mask, "write_png": write_png,
                     "render_views": qc_render_views, "align_search": _align_search, "iou_pair": _iou_pair,
                     "align_rotate": qc_align_rotate, "rotate_mask": _rotate_mask,
                     "gif_first_frame": _gif_first_frame, "gif_to_png": gif_to_png,
                     "help": qc_help,
        "selftest": qc_selftest})
    _K.dsh_measure = {"aabb_err": m_aabb_err, "silhouette_iou": m_silhouette_iou,
                      "profile_err": m_profile_err}