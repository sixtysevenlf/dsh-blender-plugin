# -*- coding: utf-8 -*-
"""DSH 同机位 QC 视觉报告（verdict-first）—— 把"像不像"变成一页可执行的结论。

现场问题：qc_render_views 给出图，模型还是要"看"；而 VLM 看两张图说不出"差在哪、差多少 mm、
下一步改什么"。本模块只做**同机位**的客观比对（同一相机、同一分辨率/裁切），输出：

    verdict  pass / fail / degraded          ← **第一字段**（degraded ≠ 通过）
    metrics  IoU / Dice / missing / extra / boundary（缺多少、多多少、边界平均偏差）
    top_defects  最大的 N 个缺陷：bbox_px / 面积差 / 位置 mm / kind(missing|extra) / 对应视图
    crops    每个缺陷一张放大的差分图（≤3 张写盘）
    images   overlay（红=参考有渲染无 / 蓝=渲染多出 / 黄=重合）、sheet（参考|渲染|叠加三联）
    fix_hint 可执行的下一步（按缺陷方向给）

ops（走 blender_op 或 K.dsh_visualreport_api）：
    report(ref, render | views, px_per_mm, targets, out_dir, max_defects, ref_box_px, render_box_px,
           label, iou_pass, iou_fail, max_missing_frac, max_extra_frac, save_images)
    selftest / help

参数：
    ref           参考图路径（必填）
    render        渲染图路径（与 views 二选一）
    views         [{name, path}] 多视图同机位比对（最多 8 个；与 render 二选一）
    ref_box_px    参考图的视图裁切 [x0,y0,x1,y1]（或 {"x0","y0","x1","y1"} / {"x","y","w","h"}）
    render_box_px 渲染图的视图裁切（同上）—— 给出时按 bbox 归一；不给则整图对齐并 warnings
    px_per_mm     渲染图每毫米像素数（可选；给了才能把缺陷 bbox/面积换成 mm，targets 也靠它）
    targets       [{key, expect_mm, tol_mm}] 已知尺寸核对；key ∈ width|height|diag|area
    out_dir       产物根目录（默认 K.out_dir；每次跑建 <out_dir>/visualreport_<label>_<时间戳>/）
    max_defects   返回 / 定位的缺陷数（默认 3；crop 最多写 3 张）
    iou_pass      通过阈值（默认 0.95）｜iou_fail 失败阈值（默认 0.85）
    max_missing_frac / max_extra_frac 缺/多面积占比上限（默认各 0.02 = 2%）

判定（先看 verdict，再看 metrics；三态语义与 gate 一致）：
    fail       IoU < iou_fail，或缺/多面积占比超限，或 targets 有超标（尺寸不对）
    degraded   能算但证据不足：掩膜为空、参考/渲染尺寸不同被缩放、targets 缺 px_per_mm 判不了、
               IoU 落在 fail/pass 之间、或 qc/imgtools 交叉核验都不可用
    pass       其余（IoU ≥ iou_pass 且缺/多面积在阈值内、targets 全过）

度量来源（三级降级，回执里 metrics_source / crosscheck 写明）：
    ① preload 有 qc：掩膜用 qc.mask_auto，指标用 qc.metrics（与 qc.compare 同一套 IoU/Dice/边界算法），
       并用 qc.compare 交叉核验 + 拿它的 overlay/sheet；
    ② 没有 qc 但有 imgtools：numpy 自算掩膜/指标，imgtools 的 diff/scan 交叉核验（IoU/缺多像素/bbox）；
    ③ 两者都没有：纯 numpy 兜底（boundary 为膨胀近似，回执 note 说明）。
本模块自带 overlay/sheet/crop 生成（纯 numpy + bpy 存 PNG），不依赖 montage/PIL。

诚实边界：
  · **同机位**才可用：机位/焦距/分辨率/裁切不一致时 IoU 会系统性偏低（那是输入问题，不是模型问题）；
  · 只做轮廓/几何类客观比对；材质/颜色/审美判断不在本模块（色差请用 qc.compare 的剖面/图看）；
  · 参考图与渲染图的掩膜都来自"前景 vs 背景"启发式，照片类参考图需先抠背景（否则掩膜不可信）；
  · top_defects 的面积是**像素差**，px_per_mm 缺失时只给像素口径（mm 字段为 null）。
"""
import json as json
import math
import os
import re
import sys
import time

import bpy as bpy

import numpy as np  # noqa: F401  # re-exported to the split visualreport modules


VISUALREPORT_VERSION = 1

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("visualreport.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")
_j = _KIT.j

DEFAULT_MAX_DEFECTS = 3
MAX_VIEWS = 8
TARGET_KEY_ALIASES = {
    "width": "width", "w": "width", "x": "width", "bbox_w": "width", "bbox_width": "width",
    "height": "height", "h": "height", "y": "height", "bbox_h": "height", "bbox_height": "height",
    "diag": "diag", "diagonal": "diag", "d": "diag", "diag_mm": "diag",
    "area": "area", "area_mm2": "area", "area_mm^2": "area",
}


# ────────────────────────────── 小工具 ──────────────────────────────
def _err(msg, hint=None, op=None, **kw):
    d = {"ok": False, "error": str(msg)[:320]}
    if hint:
        d["hint"] = str(hint)[:420]
    if op:
        d["op"] = op
    d.update(kw)
    return d


def _safe_token(s, what="label", maxlen=48):
    t = re.sub(r"[^\w.-]+", "_", str(s if s is not None else "").strip(), flags=re.UNICODE).strip("_.-")
    return (t or "qc")[:maxlen]


def _num(v, what, default=None, gt=None, minv=None, maxv=None):
    if v is None:
        if default is None:
            raise ValueError("%s 必填" % what)
        return float(default)
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s 不是数字：%r" % (what, v))
    if not math.isfinite(x):
        raise ValueError("%s 不是有限数：%r" % (what, v))
    if gt is not None and not x > gt:
        raise ValueError("%s 必须 > %g（收到 %g）" % (what, gt, x))
    if minv is not None and x < minv:
        raise ValueError("%s 必须 >= %g（收到 %g）" % (what, minv, x))
    if maxv is not None and x > maxv:
        raise ValueError("%s 必须 <= %g（收到 %g）" % (what, maxv, x))
    return x


def _bool(v, what="参数", default=False):
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "y")
    raise ValueError("%s 必须是 bool（收到 %r）" % (what, v))


def _win(p):
    s = str(p)
    try:
        if s.startswith("/") and _K is not None and hasattr(_K, "win_path"):
            return _K.win_path(s)
    except Exception:
        pass
    return s


def _norm_path(p, base=None):
    s = str(p)
    if s.startswith("/"):
        s = _win(s)
    elif base and not os.path.isabs(s) and not re.match(r"^[A-Za-z]:[\\/]", s):
        s = os.path.join(str(base), s)
    return s


def _root_outdir(raw):
    if raw:
        p = _norm_path(raw)
        try:
            os.makedirs(p, exist_ok=True)
        except Exception as e:
            raise ValueError("out_dir 建不出来：%s（%s）" % (p, str(e)[:120]))
        return p
    base = getattr(_K, "out_dir", None) or getattr(_K, "workdir", None) or os.path.join(os.path.expanduser("~"), "dsh_out")
    base = _norm_path(base)
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except Exception:
        return _norm_path(os.path.expanduser("~"))


def _file_info(path):
    p = _norm_path(path)
    return {"path": p, "bytes": int(os.path.getsize(p)), "exists": os.path.isfile(p)}


def _md5(path):
    import hashlib
    h = hashlib.md5()
    with open(_norm_path(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stamp():
    return time.strftime("%Y%m%d_%H%M%S")




__all__ = [name for name in list(globals()) if not name.startswith("__")]
