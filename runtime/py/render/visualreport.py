# -*- coding: utf-8 -*-
"""DSH 同机位 QC 视觉报告（P1 · verdict-first）—— 把"像不像"变成一页可执行的结论。

现场问题：qc_render_views 给出图，模型还是要"看"；而 VLM 看两张图说不出"差在哪、差多少 mm、
下一步改什么"。本模块只做**同机位**的客观比对（同一相机、同一分辨率/裁切），输出：

    verdict  pass / fail / degraded          ← **第一字段**（degraded ≠ 通过）
    metrics  IoU / Dice / missing / extra / boundary（缺多少、多多少、边界平均偏差）
    top_defects  最大的 N 个缺陷：bbox_px / 面积差 / 位置 mm / kind(missing|extra) / 对应视图
    crops    每个缺陷一张放大的差分图（≤3 张写盘）
    images   overlay（红=参考有渲染无 / 蓝=渲染多出 / 黄=重合）、sheet（参考|渲染|叠加三联）
    fix_hint 可执行的下一步（按缺陷方向给）

ops（走 blender_rt_plan 或 K.dsh_visualreport_api）：
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
import json
import math
import os
import re
import sys
import time

import bpy
import numpy as np

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


# ────────────────────────────── 读写图 / 掩膜 ──────────────────────────────
def _srgb_from_linear(rgb):
    rgb = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(np.maximum(rgb, 0.0), 1 / 2.4) - 0.055)


def _load_rgb_alpha(path):
    """读图 → (srgb[h,w,3], alpha[h,w])；行序上→下，颜色口径与 qc.load 一致。"""
    p = _norm_path(path)
    if not os.path.isfile(p):
        raise ValueError("图不存在：%s" % p)
    try:
        img = bpy.data.images.load(p, check_existing=False)
    except Exception:
        st = getattr(_K, "stage", None)
        staged = None
        if callable(st):
            try:
                staged = st(path)
            except Exception:
                staged = None
        if not (isinstance(staged, dict) and staged.get("ok")):
            raise ValueError("读图失败：%s（Blender 支持 PNG/JPEG/WebP/BMP/TGA/TIFF/EXR）" % p)
        p = _norm_path(staged.get("staged") or p)
        img = bpy.data.images.load(p, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        if w <= 0 or h <= 0:
            raise ValueError("图尺寸非法（0×0）：%s" % p)
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
    a = buf.reshape(h, w, 4)[::-1].copy()
    return _srgb_from_linear(a[:, :, :3]), np.clip(a[:, :, 3], 0.0, 1.0)


def _fill_rc(m):
    m = m.copy()
    for r in range(m.shape[0]):
        ii = np.where(m[r])[0]
        if len(ii) > 1:
            m[r, int(ii.min()):int(ii.max()) + 1] = True
    for c in range(m.shape[1]):
        ii = np.where(m[:, c])[0]
        if len(ii) > 1:
            m[int(ii.min()):int(ii.max()) + 1, c] = True
    return m


def _bg_from_border(srgb):
    edge = np.concatenate([srgb[0, :, :], srgb[-1, :, :], srgb[:, 0, :], srgb[:, -1, :]], axis=0)
    return np.median(edge, axis=0).astype(np.float32)


def _otsu(vals):
    hist, _edges = np.histogram(np.clip(vals, 0.0, 1.0), bins=256, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    total = float(hist.sum())
    if total <= 0:
        return 0.10
    p = hist / total
    omega = np.cumsum(p)
    idx = np.arange(256, dtype=np.float64)
    mu = np.cumsum(p * idx)
    mu_t = float(mu[-1])
    sigma = (mu_t * omega - mu) ** 2 / np.maximum(omega * (1.0 - omega), 1e-12)
    k = int(np.nanargmax(sigma))
    return float((k + 0.5) / 256.0)


def _mask_auto_own(srgb, alpha=None):
    """numpy 兜底掩膜：可信 alpha 优先；否则边框背景色 + Otsu（与 qc.mask_auto 同思路）。"""
    h, w = srgb.shape[:2]
    if alpha is not None and getattr(alpha, "shape", None) == (h, w):
        a = np.asarray(alpha, dtype=np.float32)
        frac_mid = float(np.mean((a > 0.02) & (a < 0.98)))
        frac_on = float(np.mean(a > 0.5))
        if 0.0005 < frac_mid < 0.9 and 0.002 < frac_on < 0.998:
            return _fill_rc(a > 0.5), {"source": "alpha", "frac_mid": round(frac_mid, 5),
                                       "frac_on": round(frac_on, 5)}
    bg = _bg_from_border(srgb)
    dist = np.abs(srgb - bg.reshape(1, 1, 3)).max(axis=2)
    thr = float(min(max(_otsu(dist), 0.03), 0.45))
    m = dist > thr
    return _fill_rc(m), {"source": "otsu", "bg": [round(float(x), 4) for x in bg],
                         "thr": round(thr, 4), "fg_frac": round(float(m.mean()), 4)}


def _resize_mask(m, h, w):
    mh, mw = m.shape
    yi = np.clip((np.arange(h) * (mh / float(h))).astype(np.int32), 0, mh - 1)
    xi = np.clip((np.arange(w) * (mw / float(w))).astype(np.int32), 0, mw - 1)
    return m[yi][:, xi]


def _resize_rgb(arr, h, w):
    ah, aw = arr.shape[:2]
    yi = np.clip((np.arange(h) * (ah / float(h))).astype(np.int32), 0, ah - 1)
    xi = np.clip((np.arange(w) * (aw / float(w))).astype(np.int32), 0, aw - 1)
    return arr[yi][:, xi]


def _dilate1(m):
    p = np.pad(m, 1)
    out = m.copy()
    h, w = m.shape
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            out |= p[dy:dy + h, dx:dx + w]
    return out


def _erode1(m):
    return ~_dilate1(~m)


def _boundary_approx(a, b, max_k=8):
    """边界平均偏差（像素）的膨胀近似（没有 qc 时的兜底；粒度 1px，上限 max_k）。"""
    def edge(m):
        return m & ~_erode1(m)

    def one(src_edge, dst_mask):
        if not bool(src_edge.any()):
            return None
        cur = dst_mask.copy()
        rem = src_edge.copy()
        total = int(rem.sum())
        acc = 0.0
        covered = 0
        for k in range(1, max_k + 1):
            cur = _dilate1(cur)
            hit = rem & cur
            n = int(hit.sum())
            if n:
                acc += k * n
                covered += n
                rem &= ~cur
            if covered >= total:
                break
        acc += max_k * int(rem.sum())
        return acc / float(max(1, total))

    vals = [x for x in (one(edge(a), b), one(edge(b), a)) if x is not None]
    if not vals:
        return {"mean_px": None, "p95_px": None, "source": "none", "note": "有一侧没有边界"}
    return {"mean_px": round(float(sum(vals) / len(vals)), 3), "p95_px": None,
            "source": "dilate_approx", "max_k": max_k}


def _iou_pair(a, b):
    inter = int(np.logical_and(a, b).sum())
    union = int(np.logical_or(a, b).sum())
    return ((inter / float(union)) if union else 0.0), inter, union


def _metrics_own(rm, rn):
    iou, inter, union = _iou_pair(rm, rn)
    sa, sb = int(rm.sum()), int(rn.sum())
    missing = int(np.logical_and(rm, ~rn).sum())
    extra = int(np.logical_and(rn, ~rm).sum())
    dice = (2.0 * inter / float(sa + sb)) if (sa + sb) else 0.0
    return {"iou": round(float(iou), 4), "dice": round(float(dice), 4), "ref_px": sa, "render_px": sb,
            "inter_px": int(inter), "union_px": int(union), "missing_px": missing, "extra_px": extra,
            "missing_frac": round(missing / float(max(1, sa)), 4),
            "extra_frac": round(extra / float(max(1, sb)), 4),
            "boundary": _boundary_approx(rm, rn)}


def _components(mask):
    """二值掩膜的连通域（行游程 + 并查集；8 邻接）→ [{bbox, area_px, centroid_px}]，按面积降序。"""
    h = mask.shape[0]
    parent, runs = [], []

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    prev = []
    for y in range(h):
        row = np.asarray(mask[y], dtype=bool)
        if not row.any():
            prev = []
            continue
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        cur = []
        for x0, x1 in zip(starts.tolist(), ends.tolist()):
            idx = len(runs)
            runs.append((int(y), int(x0), int(x1)))
            parent.append(idx)
            cur.append((int(x0), int(x1), idx))
        i = j = 0
        while i < len(prev) and j < len(cur):
            px0, px1, pi = prev[i]
            cx0, cx1, ci = cur[j]
            if px0 <= cx1 and cx0 <= px1:
                union(pi, ci)
                if px1 < cx1:
                    i += 1
                elif cx1 < px1:
                    j += 1
                else:
                    i += 1
                    j += 1
            elif px1 < cx0:
                i += 1
            else:
                j += 1
        prev = cur
    groups = {}
    for idx, (y, x0, x1) in enumerate(runs):
        r = find(idx)
        g = groups.get(r)
        if g is None:
            g = groups[r] = {"x0": 10 ** 9, "y0": 10 ** 9, "x1": -1, "y1": -1, "area": 0, "sx": 0.0, "sy": 0.0}
        g["x0"] = min(g["x0"], x0)
        g["x1"] = max(g["x1"], x1 - 1)
        g["y0"] = min(g["y0"], y)
        g["y1"] = max(g["y1"], y)
        n = x1 - x0
        g["area"] += n
        g["sx"] += ((x0 + x1 - 1) / 2.0) * n
        g["sy"] += float(y) * n
    out = []
    for g in groups.values():
        area = int(g["area"])
        out.append({"bbox": [int(g["x0"]), int(g["y0"]), int(g["x1"]), int(g["y1"])],
                    "area_px": area,
                    "centroid_px": [round(g["sx"] / max(1, area), 2), round(g["sy"] / max(1, area), 2)]})
    out.sort(key=lambda c: -c["area_px"])
    return out


def _bbox_of(m):
    if not bool(m.any()):
        return None
    ys, xs = np.nonzero(m)
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


# ────────────────────────────── 存图（overlay / sheet / crop）──────────────────────────────
_PNG_SEQ = [0]


def _save_png_rgba(arr, path):
    p = _norm_path(path)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    h, w = int(arr.shape[0]), int(arr.shape[1])
    _PNG_SEQ[0] += 1
    img = bpy.data.images.new("VR_TMP_%d" % _PNG_SEQ[0], width=w, height=h, alpha=True)
    try:
        buf = np.ascontiguousarray(np.clip(arr[::-1], 0.0, 1.0).astype(np.float32))
        img.pixels.foreach_set(buf.ravel())
        img.filepath_raw = p
        img.file_format = "PNG"
        img.save()
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
    if not os.path.isfile(p):
        raise RuntimeError("PNG 写盘失败：%s" % p)
    rec = _file_info(p)
    rec["md5"] = _md5(p)
    return rec


def _rgba_from_rgb(rgb):
    h, w = rgb.shape[:2]
    arr = np.ones((h, w, 4), dtype=np.float32)
    arr[:, :, :3] = np.clip(rgb, 0.0, 1.0)
    return arr


def _make_overlay(rm, rn):
    h, w = rm.shape[:2]
    ov = np.ones((h, w, 4), dtype=np.float32)
    both = np.logical_and(rm, rn)
    miss = np.logical_and(rm, ~rn)
    extra = np.logical_and(rn, ~rm)
    ov[both, 0], ov[both, 1], ov[both, 2] = 0.85, 0.82, 0.45
    ov[miss, 0], ov[miss, 1], ov[miss, 2] = 0.92, 0.18, 0.18
    ov[extra, 0], ov[extra, 1], ov[extra, 2] = 0.18, 0.38, 0.92
    return ov


def _make_sheet(panels, gap=6):
    if not panels:
        raise ValueError("没有可拼的面板")
    H = max(int(p.shape[0]) for p in panels)
    W = sum(int(p.shape[1]) for p in panels) + gap * (len(panels) - 1)
    out = np.ones((H, W, 4), dtype=np.float32)
    x = 0
    for p in panels:
        if int(p.shape[0]) != H:
            raise ValueError("面板高度不一致：%s vs %s" % (p.shape[0], H))
        out[:, x:x + int(p.shape[1]), :] = p
        x += int(p.shape[1]) + gap
    return out


def _write_defect_crop(overlay, defect, run_dir, index):
    h, w = overlay.shape[:2]
    x0, y0, x1, y1 = defect["bbox_px"]
    bw, bh = max(1, x1 - x0 + 1), max(1, y1 - y0 + 1)
    pad = max(6, int(0.35 * max(bw, bh)))
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(w, x1 + 1 + pad), min(h, y1 + 1 + pad)
    sub = overlay[cy0:cy1, cx0:cx1].copy()
    for t in range(2):
        if sub.shape[0] > t:
            sub[t, :, :3] = 0.05
            sub[sub.shape[0] - 1 - t, :, :3] = 0.05
        if sub.shape[1] > t:
            sub[:, t, :3] = 0.05
            sub[:, sub.shape[1] - 1 - t, :3] = 0.05
    name = "defect_%02d_%s_%s.png" % (index, _safe_token(defect.get("view"), "view", 24),
                                     defect.get("kind", "x"))
    rec = _save_png_rgba(sub, os.path.join(run_dir, name))
    rec.update({"view": defect.get("view"), "kind": defect.get("kind"),
                "bbox_px": list(defect.get("bbox_px") or []), "index": index})
    return rec


# ────────────────────────────── 参数校验 ──────────────────────────────
def _parse_box(v, what, W, H):
    if v is None:
        return None
    if isinstance(v, dict):
        if all(k in v for k in ("x0", "y0", "x1", "y1")):
            vals = [v["x0"], v["y0"], v["x1"], v["y1"]]
        elif all(k in v for k in ("x", "y", "w", "h")):
            x, y, w, h = float(v["x"]), float(v["y"]), float(v["w"]), float(v["h"])
            vals = [x, y, x + w, y + h]
        else:
            raise ValueError("%s 用 dict 时必须给 x0/y0/x1/y1 或 x/y/w/h（收到键 %s）"
                             % (what, sorted(v.keys())))
    elif isinstance(v, (list, tuple)) and len(v) == 4:
        vals = list(v)
    else:
        raise ValueError("%s 必须是 [x0,y0,x1,y1] 或 {x0,y0,x1,y1}（收到 %r）" % (what, v))
    try:
        x0, y0, x1, y1 = [int(round(float(t))) for t in vals]
    except (TypeError, ValueError):
        raise ValueError("%s 的四个值都必须是数字（收到 %r）" % (what, vals))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("%s 是空框：%r（要 x1>x0 且 y1>y0）" % (what, [x0, y0, x1, y1]))
    clip = [max(0, min(x0, W)), max(0, min(y0, H)), max(0, min(x1, W)), max(0, min(y1, H))]
    if clip[2] - clip[0] < 4 or clip[3] - clip[1] < 4:
        raise ValueError("%s 裁到图内后太小：%r（图 %dx%d）" % (what, clip, W, H))
    return {"box": clip, "clipped": clip != [x0, y0, x1, y1], "given": [x0, y0, x1, y1]}


def _parse_targets(raw):
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("targets 必须是 list（收到 %r）" % (type(raw).__name__,))
    out = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ValueError("targets[%d] 必须是 dict（{key,expect_mm,tol_mm}）" % i)
        unknown = sorted(set(str(k) for k in t.keys()) - set(["key", "expect_mm", "tol_mm"]))
        if unknown:
            raise ValueError("targets[%d] 不认识的键 %s（可用：key,expect_mm,tol_mm）" % (i, unknown))
        rk = str(t.get("key") or "").strip().lower()
        key = TARGET_KEY_ALIASES.get(rk)
        if key is None:
            raise ValueError("targets[%d].key 不支持 %r（可用：%s）"
                             % (i, rk, ", ".join(sorted(set(TARGET_KEY_ALIASES.values())))))
        expect = _num(t.get("expect_mm"), "targets[%d].expect_mm" % i, gt=0.0)
        tol = _num(t.get("tol_mm"), "targets[%d].tol_mm" % i, gt=0.0)
        out.append({"raw_key": rk, "key": key, "expect_mm": round(expect, 4), "tol_mm": round(tol, 4)})
    return out


def _measure_targets(render_mask, targets, px_per_mm):
    rows = []
    if not targets:
        return rows
    bb = _bbox_of(render_mask)
    for t in targets:
        row = dict(t)
        if px_per_mm is None:
            row.update({"measured_mm": None, "err_mm": None, "status": "unresolved",
                        "reason": "缺 px_per_mm，无法把像素换成 mm"})
            rows.append(row)
            continue
        if bb is None:
            row.update({"measured_mm": None, "err_mm": None, "status": "unresolved",
                        "reason": "渲染掩膜为空，量不到尺寸"})
            rows.append(row)
            continue
        w_px = float(bb[2] - bb[0] + 1)
        h_px = float(bb[3] - bb[1] + 1)
        if t["key"] == "width":
            measured = w_px / px_per_mm
        elif t["key"] == "height":
            measured = h_px / px_per_mm
        elif t["key"] == "diag":
            measured = math.hypot(w_px, h_px) / px_per_mm
        else:
            measured = float(int(render_mask.sum())) / (px_per_mm * px_per_mm)
        err = measured - float(t["expect_mm"])
        status = "pass" if abs(err) <= float(t["tol_mm"]) else "fail"
        row.update({"measured_mm": round(float(measured), 3), "err_mm": round(float(err), 3),
                    "status": status})
        rows.append(row)
    return rows


# ────────────────────────────── 跨界调用（qc / imgtools）──────────────────────────────
def _api_call(api, op, args):
    """qc/imgtools 的 Api 调用：优先 Api(op,args)（返回 dict），否则直接调 dict 里的函数。"""
    if api is None:
        return None
    try:
        if callable(api):
            r = api(op, args)
        else:
            fn = api.get(op)
            if fn is None:
                return {"ok": False, "error": "op 不存在：%s" % op}
            r = fn(**args)
        if isinstance(r, str):
            r = json.loads(r)
        return r if isinstance(r, dict) else None
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _qc_masks(qc, srgb, alpha):
    m, info = qc["mask_auto"](srgb, alpha)
    return np.asarray(m, dtype=bool), (info if isinstance(info, dict) else {})


def _qc_metrics(qc, rm, rn):
    m = qc["metrics"](rm, rn)
    if not isinstance(m, dict):
        raise RuntimeError("qc.metrics 没返回 dict")
    b = m.get("boundary")
    if isinstance(b, dict):
        m["boundary"] = {"mean_px": b.get("mean_px"), "p95_px": b.get("p95_px"), "source": "qc"}
    else:
        m["boundary"] = {"mean_px": (round(float(b), 3) if isinstance(b, (int, float)) else None),
                         "p95_px": None, "source": "qc"}
    for k in ("iou", "dice", "missing_frac", "extra_frac"):
        if k in m and m[k] is not None:
            m[k] = round(float(m[k]), 4)
    for k in ("ref_px", "render_px", "inter_px", "union_px", "missing_px", "extra_px"):
        if k in m and m[k] is not None:
            m[k] = int(m[k])
    return m


def _qc_crosscheck(qc, ref_path, ref_box, render_path, render_box, run_dir, tag):
    args = {"ref_path": ref_path, "render_path": render_path, "label": tag,
            "out_dir": run_dir, "search": True}
    if ref_box:
        args["ref_box"] = list(ref_box)
    if render_box:
        args["render_box"] = list(render_box)
    r = _api_call(qc, "compare", args)
    if not r or not r.get("ok"):
        return {"ok": False, "source": "qc.compare",
                "error": str((r or {}).get("error") or "qc.compare 无结果")[:240]}
    m = r.get("metrics") or {}
    b = m.get("boundary") or {}
    return {"ok": True, "source": "qc.compare", "iou": r.get("iou"),
            "dice": m.get("dice"), "missing_px": m.get("missing_px"), "extra_px": m.get("extra_px"),
            "missing_frac": m.get("missing_frac"), "extra_frac": m.get("extra_frac"),
            "boundary": {"mean_px": (b.get("mean_px") if isinstance(b, dict) else b), "source": "qc.compare"},
            "overlay": r.get("overlay"), "sheet": r.get("sheet"),
            "warnings": (m.get("warnings") or [])[:6]}


def _img_crosscheck(img, ref_path, render_path, run_dir, tag):
    if img is None:
        return {"ok": False, "source": "imgtools", "error": "imgtools 未注入"}
    out_png = os.path.join(run_dir, "imgdiff_%s.png" % _safe_token(tag, "tag", 32))
    d = _api_call(img, "diff", {"a": ref_path, "b": render_path, "out": out_png, "align": True})
    scan_ref = _api_call(img, "scan", {"path": ref_path})
    scan_ren = _api_call(img, "scan", {"path": render_path})
    if not d or not d.get("ok"):
        return {"ok": False, "source": "imgtools.diff",
                "error": str((d or {}).get("error") or "imgtools.diff 无结果")[:240]}
    return {"ok": True, "source": "imgtools.diff", "iou": d.get("iou"),
            "missing_px": d.get("a_only_px"), "extra_px": d.get("b_only_px"),
            "intersection_px": d.get("intersection_px"), "union_px": d.get("union_px"),
            "out": d.get("out"), "aligned_bbox": d.get("aligned_bbox"),
            "worst_columns": (d.get("worst_columns") or [])[:3],
            "worst_rows": (d.get("worst_rows") or [])[:3],
            "scan": {"ref": (scan_ref or {}), "render": (scan_ren or {})}}


# ────────────────────────────── 单视图分析 ──────────────────────────────
def _process_view(ref_srgb, ref_alpha, ref_path, ref_box, view, run_dir, tag, qc, img, opts):
    t0 = time.perf_counter()
    warnings = []
    view_path = _norm_path(view["path"])
    ren_srgb, ren_alpha = _load_rgb_alpha(view_path)
    ref_c, ref_a = ref_srgb, ref_alpha
    ren_c, ren_a = ren_srgb, ren_alpha
    if ref_box:
        x0, y0, x1, y1 = ref_box
        ref_c, ref_a = ref_srgb[y0:y1, x0:x1], ref_alpha[y0:y1, x0:x1]
    if opts.get("render_box"):
        x0, y0, x1, y1 = opts["render_box"]
        ren_c, ren_a = ren_srgb[y0:y1, x0:x1], ren_alpha[y0:y1, x0:x1]
    # 掩膜
    if qc is not None:
        try:
            rm, rinfo = _qc_masks(qc, ref_c, ref_a)
            rn, ninfo = _qc_masks(qc, ren_c, ren_a)
            mask_source = "qc.mask_auto"
        except Exception as e:
            warnings.append("qc.mask_auto 失败（%s: %s）→ numpy.otsu 兜底"
                            % (type(e).__name__, str(e)[:120]))
            rm, rinfo = _mask_auto_own(ref_c, ref_a)
            rn, ninfo = _mask_auto_own(ren_c, ren_a)
            mask_source = "numpy.otsu"
    else:
        rm, rinfo = _mask_auto_own(ref_c, ref_a)
        rn, ninfo = _mask_auto_own(ren_c, ren_a)
        mask_source = "numpy.otsu"
    canvas = [int(rn.shape[0]), int(rn.shape[1])]
    resized = False
    if rm.shape != rn.shape:
        if qc is not None:
            try:
                rm = np.asarray(qc["resize_mask"](rm, rn.shape[0], rn.shape[1]), dtype=bool)
            except Exception:
                rm = _resize_mask(rm, rn.shape[0], rn.shape[1])
        else:
            rm = _resize_mask(rm, rn.shape[0], rn.shape[1])
        resized = True
        warnings.append("参考/渲染的比对画布尺寸不同（%s vs %s）：参考掩膜已最近邻缩放到渲染画布"
                        "（同机位 QC 建议同分辨率/同裁切）" % (list(ref_c.shape[:2]), list(rn.shape)))
    # 指标
    if qc is not None and bool(rm.any()) and bool(rn.any()):
        try:
            metrics = _qc_metrics(qc, rm, rn)
            metrics_source = "qc.metrics"
        except Exception as e:
            metrics = _metrics_own(rm, rn)
            metrics_source = "numpy"
            warnings.append("qc.metrics 失败（%s: %s）→ numpy 兜底" % (type(e).__name__, str(e)[:120]))
    else:
        metrics = _metrics_own(rm, rn)
        metrics_source = "numpy"
        if qc is None:
            warnings.append("qc 未注入：IoU/Dice/边界用 numpy 兜底（boundary 为膨胀近似）")
    # 缺陷（缺 / 多）
    defects = []
    sa = max(1, int(rm.sum()))
    sb = max(1, int(rn.sum()))
    for kind, cmask in (("missing", np.logical_and(rm, ~rn)), ("extra", np.logical_and(rn, ~rm))):
        for c in _components(cmask):
            area = int(c["area_px"])
            if area <= 0:
                continue
            defects.append({"kind": kind, "view": view["name"], "bbox_px": c["bbox"],
                            "area_px": area, "centroid_px": c["centroid_px"],
                            "area_frac": round(area / float(sa if kind == "missing" else sb), 4),
                            "canvas": canvas})
    defects.sort(key=lambda d: (-d["area_frac"], -d["area_px"]))
    # 尺寸 target
    target_rows = _measure_targets(rn, opts.get("targets") or [], opts.get("px_per_mm"))
    # 叠加图 / 三联图
    overlay = _make_overlay(rm, rn)
    ov_rec = _save_png_rgba(overlay, os.path.join(run_dir, "overlay_%s.png" % _safe_token(tag, "tag", 40)))
    ref_panel = _rgba_from_rgb(_resize_rgb(ref_c, canvas[0], canvas[1]))
    ren_panel = _rgba_from_rgb(_resize_rgb(ren_c, canvas[0], canvas[1]))
    sh_rec = _save_png_rgba(_make_sheet([ref_panel, ren_panel, overlay]),
                            os.path.join(run_dir, "sheet_%s.png" % _safe_token(tag, "tag", 40)))
    # 交叉核验（qc.compare 优先；否则 imgtools diff/scan）
    cross = None
    if not bool(rm.any()) or not bool(rn.any()):
        warnings.append("有一侧掩膜为空：无法判读前景（照片类参考图请先抠背景；渲染图检查透明背景/取景）")
    elif qc is not None:
        cross = _qc_crosscheck(qc, ref_path, list(ref_box) if ref_box else None, view_path,
                               list(opts["render_box"]) if opts.get("render_box") else None,
                               run_dir, tag)
        if not cross.get("ok"):
            warnings.append("qc.compare 交叉核验失败：%s" % str(cross.get("error"))[:180])
    if (cross is None or not cross.get("ok")) and img is not None:
        cross = _img_crosscheck(img, ref_path, view_path, run_dir, tag)
        if not cross.get("ok"):
            warnings.append("imgtools 交叉核验也失败：%s" % str(cross.get("error"))[:180])
    if cross and cross.get("ok"):
        ci = cross.get("iou")
        if isinstance(ci, (int, float)) and metrics.get("iou") is not None:
            gap = abs(float(ci) - float(metrics["iou"]))
            if gap > 0.10:
                warnings.append("交叉核验 IoU（%.4f）与固定像素指标（%.4f）差 %.3f：可能是对齐/掩膜口径差异"
                                "（本报告以固定像素指标判 verdict）"
                                % (float(ci), float(metrics["iou"]), gap))
        for w in (cross.get("warnings") or []):
            warnings.append("交叉核验：%s" % str(w)[:160])
    if cross is None or not cross.get("ok"):
        warnings.append("交叉核验不可用（qc.compare 与 imgtools.diff 都没跑成）—— 指标仍按本模块固定像素口径给")
    verdict, reasons, degraded = _judge(metrics, target_rows,
                                        {"resized": resized,
                                         "masks_empty": (not bool(rm.any())) or (not bool(rn.any()))},
                                        opts)
    detail = {"name": view["name"], "ref": ref_path, "render": view_path,
              "metrics": metrics, "metrics_source": metrics_source, "mask_source": mask_source,
              "alignment": {"canvas": canvas,
                            "ref_crop": [int(ref_c.shape[1]), int(ref_c.shape[0])],
                            "render_crop": [int(ren_c.shape[1]), int(ren_c.shape[0])],
                            "ref_box_px": (list(ref_box) if ref_box else None),
                            "render_box_px": (list(opts["render_box"]) if opts.get("render_box") else None),
                            "resized_ref_mask": bool(resized)},
              "mask_info": {"ref": rinfo, "render": ninfo},
              "targets": target_rows, "defects": defects, "crosscheck": cross,
              "images": {"overlay": ov_rec, "sheet": sh_rec},
              "verdict": verdict, "reasons": reasons, "degraded_reasons": degraded,
              "warnings": warnings,
              "ms": int(round((time.perf_counter() - t0) * 1000.0))}
    return detail, overlay


def _judge(metrics, target_rows, flags, opts):
    reasons, degraded = [], []
    if flags.get("masks_empty"):
        return "degraded", [], ["有一侧掩膜为空：无法判读前景"]
    try:
        iou = float(metrics.get("iou") or 0.0)
        mf = float(metrics.get("missing_frac") or 0.0)
        ef = float(metrics.get("extra_frac") or 0.0)
    except Exception:
        return "degraded", [], ["metrics 不可解析"]
    if iou < float(opts["iou_fail"]):
        reasons.append("IoU %.4f < fail 阈值 %.2f" % (iou, opts["iou_fail"]))
    if mf > float(opts["max_missing_frac"]):
        reasons.append("缺失面积占比 %.1f%% > 上限 %.1f%%（参考有、渲染无）"
                       % (mf * 100.0, opts["max_missing_frac"] * 100.0))
    if ef > float(opts["max_extra_frac"]):
        reasons.append("多余面积占比 %.1f%% > 上限 %.1f%%（渲染多出）"
                       % (ef * 100.0, opts["max_extra_frac"] * 100.0))
    tf = [t for t in target_rows if t.get("status") == "fail"]
    if tf:
        reasons.append("尺寸 target 不达标：%s"
                       % "；".join("%s 实测 %.2fmm 期望 %.2f±%.2fmm"
                                  % (t["key"], float(t.get("measured_mm") or 0.0),
                                     float(t["expect_mm"]), float(t["tol_mm"])) for t in tf[:3]))
    if any(t.get("status") == "unresolved" for t in target_rows):
        degraded.append("targets 因缺 px_per_mm（或掩膜为空）未判")
    if flags.get("resized"):
        degraded.append("参考/渲染尺寸不同：参考掩膜已缩放对齐")
    if reasons:
        return "fail", reasons, degraded
    if degraded:
        return "degraded", [], degraded
    if iou < float(opts["iou_pass"]):
        return "degraded", [], ["IoU %.4f 落在 fail/pass 阈值之间（%.2f..%.2f）"
                                % (iou, opts["iou_fail"], opts["iou_pass"])]
    return "pass", [], []


def _fix_hint(top_verdict, views, top_defects, all_targets, opts):
    if top_verdict == "pass":
        return ("无明显几何偏差：继续看 overlay/crop 复核边缘与细节；换机位/换分辨率/换引擎后的历史分数不可直接比")
    hints = []
    tf = [t for t in all_targets if t.get("status") == "fail"]
    if tf:
        hints.append("尺寸不达标：%s → 回改对应部件尺寸（targets 是硬判据）"
                     % "；".join("%s 实测 %.2fmm 期望 %.2f±%.2fmm"
                                % (t["key"], float(t.get("measured_mm") or 0.0),
                                   float(t["expect_mm"]), float(t["tol_mm"])) for t in tf[:2]))
    miss = [d for d in top_defects if d.get("kind") == "missing"]
    extra = [d for d in top_defects if d.get("kind") == "extra"]
    if miss:
        hints.append("参考有、渲染无（%d 处，最大 %.1f%%）：按 crop 的 bbox 查对应视图缺失的几何/被裁件"
                     % (len(miss), max(float(d["area_frac"]) for d in miss) * 100.0))
    if extra:
        hints.append("渲染多出（%d 处，最大 %.1f%%）：查多余件/穿模/比例偏大或背景未抠净"
                     % (len(extra), max(float(d["area_frac"]) for d in extra) * 100.0))
    if not hints:
        ious = [(v["name"], float((v.get("metrics") or {}).get("iou") or 0.0)) for v in views]
        low = min((x[1] for x in ious), default=0.0)
        hints.append("整体轮廓偏差（最低 IoU %.3f）：先核对机位/画幅/裁切是否与参考一致（同机位应同分辨率），"
                     "再按 overlay 的红/蓝区域改形" % low)
    if any(t.get("status") == "unresolved" for t in all_targets):
        hints.append("给 px_per_mm（渲染图每毫米像素数）才能把像素差换成 mm 并判 targets")
    return "；".join(hints)[:600]


# ────────────────────────────── op: report ──────────────────────────────
def visualreport_report(ref=None, render=None, views=None, px_per_mm=None, targets=None, out_dir=None,
                        max_defects=DEFAULT_MAX_DEFECTS, ref_box_px=None, render_box_px=None,
                        label=None, iou_pass=0.95, iou_fail=0.85,
                        max_missing_frac=0.02, max_extra_frac=0.02, save_images=True):
    t_start = time.perf_counter()
    try:
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("ref 必填（参考图路径）")
        ref_clean = _norm_path(ref.strip())
        if not os.path.isfile(ref_clean):
            raise ValueError("参考图不存在：%s" % ref_clean)
        if render is not None and views is not None:
            raise ValueError("render 与 views 二选一，不能同时给")
        view_list = []
        if views is not None:
            if not isinstance(views, (list, tuple)) or not views:
                raise ValueError("views 必须是非空 list：[{name,path}]")
            if len(views) > MAX_VIEWS:
                raise ValueError("views 最多 %d 个（收到 %d）" % (MAX_VIEWS, len(views)))
            for i, v in enumerate(views):
                if not isinstance(v, dict):
                    raise ValueError("views[%d] 必须是 dict（{name,path}）" % i)
                unknown = sorted(set(str(k) for k in v.keys()) - set(["name", "path"]))
                if unknown:
                    raise ValueError("views[%d] 不认识的键 %s（可用：name,path）" % (i, unknown))
                vp = v.get("path")
                if not isinstance(vp, str) or not vp.strip():
                    raise ValueError("views[%d].path 必填" % i)
                vp = _norm_path(vp.strip())
                if not os.path.isfile(vp):
                    raise ValueError("views[%d] 的图不存在：%s" % (i, vp))
                view_list.append({"name": _safe_token(v.get("name") or ("v%d" % i), "views[%d].name" % i, 40),
                                  "path": vp})
            names = [v["name"] for v in view_list]
            if len(set(names)) != len(names):
                raise ValueError("views 的 name 不能重复：%s" % names)
        else:
            if not isinstance(render, str) or not render.strip():
                raise ValueError("render 必填（渲染图路径），或给 views=[{name,path}]")
            rp = _norm_path(render.strip())
            if not os.path.isfile(rp):
                raise ValueError("渲染图不存在：%s" % rp)
            view_list.append({"name": "render", "path": rp})
        ppm = None
        if px_per_mm is not None:
            ppm = _num(px_per_mm, "px_per_mm", gt=0.0)
            if ppm > 10000:
                raise ValueError("px_per_mm 大得不合理（%g）" % ppm)
        target_rows = _parse_targets(targets)
        label_s = _safe_token(label or "qc", "label")
        md = int(_num(max_defects, "max_defects", default=DEFAULT_MAX_DEFECTS, minv=0.0, maxv=10.0))
        iou_p = _num(iou_pass, "iou_pass", default=0.95, gt=0.0, maxv=1.0)
        iou_f = _num(iou_fail, "iou_fail", default=0.85, gt=0.0, maxv=1.0)
        if iou_f > iou_p:
            raise ValueError("iou_fail（%.2f）不能大于 iou_pass（%.2f）" % (iou_f, iou_p))
        mmf = _num(max_missing_frac, "max_missing_frac", default=0.02, minv=0.0, maxv=1.0)
        mef = _num(max_extra_frac, "max_extra_frac", default=0.02, minv=0.0, maxv=1.0)
        save_img = _bool(save_images, "save_images", True)
        root = _root_outdir(out_dir)
    except ValueError as e:
        return _err(e, hint="report 参数见 K.dsh_visualreport_api['help']()；ref/render 必须是存在的图片路径",
                    op="report")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), hint="参数校验失败（未读/写任何产物）", op="report")

    run_dir = os.path.join(root, "visualreport_%s_%s" % (label_s, _stamp()))
    try:
        os.makedirs(run_dir, exist_ok=True)
    except Exception as e:
        return _err("产物目录建不出来：%s（%s）" % (run_dir, str(e)[:120]), op="report")

    qc = _KIT.api("qc")
    img = _KIT.api("img")
    method = {"mask": "qc.mask_auto" if qc is not None else "numpy.otsu",
              "metrics": "qc.metrics" if qc is not None else "numpy",
              "crosscheck": "qc.compare" if qc is not None else ("imgtools.diff" if img is not None else None),
              "qc_available": qc is not None, "imgtools_available": img is not None}
    warnings = []
    try:
        ref_srgb, ref_alpha = _load_rgb_alpha(ref_clean)
    except Exception as e:
        return _err("参考图读不了：%s: %s" % (type(e).__name__, str(e)[:200]),
                    hint="换 PNG/JPEG；GIF 先用 qc 的 gif_to_png；WSL 路径会自动转 Windows", op="report")
    H, W = int(ref_srgb.shape[0]), int(ref_srgb.shape[1])
    try:
        ref_box = _parse_box(ref_box_px, "ref_box_px", W, H) if ref_box_px is not None else None
    except ValueError as e:
        return _err(e, hint="ref_box_px 用 [x0,y0,x1,y1]（像素）", op="report")
    if ref_box is None:
        warnings.append("未给 ref_box_px：整图对齐（构图/取景差异会直接算进指标；同机位 QC 建议给视图裁切）")
    if ref_box and ref_box.get("clipped"):
        warnings.append("ref_box_px 超出参考图范围，已裁到图内：%s" % ref_box["box"])
    opts = {"px_per_mm": ppm, "targets": target_rows, "iou_pass": iou_p, "iou_fail": iou_f,
            "max_missing_frac": mmf, "max_extra_frac": mef, "render_box": None}
    view_results = []
    overlays = {}
    for view in view_list:
        tag = "%s_%s" % (label_s, view["name"])
        try:
            if render_box_px is not None:
                rs, _ra = _load_rgb_alpha(view["path"])
                rb = _parse_box(render_box_px, "render_box_px", int(rs.shape[1]), int(rs.shape[0]))
                if rb.get("clipped"):
                    warnings.append("render_box_px 超出视图 %r 范围，已裁到图内：%s" % (view["name"], rb["box"]))
                opts["render_box"] = rb["box"]
            else:
                opts["render_box"] = None
            detail, overlay = _process_view(ref_srgb, ref_alpha, ref_clean,
                                            ref_box["box"] if ref_box else None,
                                            view, run_dir, tag, qc, img, opts)
            overlays[view["name"]] = overlay
            view_results.append(detail)
        except Exception as e:
            return _err("视图 %r 分析失败：%s: %s" % (view["name"], type(e).__name__, str(e)[:240]),
                        hint="确认图片可读、掩膜不是全空（照片参考图先抠背景）；坏输入不会静默当通过",
                        op="report", view=view["name"], run_dir=run_dir)
    if not save_img:
        for v in view_results:
            v["images"] = {k: None for k in v.get("images", {})}
    vs = [v["verdict"] for v in view_results]
    if "fail" in vs:
        verdict = "fail"
    elif "degraded" in vs:
        verdict = "degraded"
    else:
        verdict = "pass"
    all_defects = []
    for v in view_results:
        for d in v.get("defects") or []:
            all_defects.append(d)
    all_defects.sort(key=lambda d: (-float(d.get("area_frac") or 0.0), -int(d.get("area_px") or 0)))
    top = all_defects[:max(0, md)]
    crops = []
    if save_img:
        for i, d in enumerate(top[:3]):
            try:
                rec = _write_defect_crop(overlays[d["view"]], d, run_dir, i)
                d["crop"] = rec["path"]
                crops.append(rec)
            except Exception as e:
                warnings.append("缺陷 crop 写盘失败（#%d）：%s" % (i, str(e)[:120]))
    for d in top:
        d.setdefault("crop", None)
    for d in top:
        d["center_px"] = list(d.get("centroid_px") or [])
        if ppm:
            d["center_mm"] = [round(d["centroid_px"][0] / ppm, 3), round(d["centroid_px"][1] / ppm, 3)]
            d["size_mm"] = [round((d["bbox_px"][2] - d["bbox_px"][0] + 1) / ppm, 3),
                            round((d["bbox_px"][3] - d["bbox_px"][1] + 1) / ppm, 3)]
            d["area_mm2"] = round(d["area_px"] / (ppm * ppm), 3)
        else:
            d["center_mm"] = None
            d["size_mm"] = None
            d["area_mm2"] = None
    for v in view_results:
        v["defects_total"] = len(v.get("defects") or [])
        v["defects"] = (v.get("defects") or [])[:max(0, md)]
    all_targets = []
    for v in view_results:
        for t in v.get("targets") or []:
            row = dict(t)
            row["view"] = v["name"]
            all_targets.append(row)
    per = [{"view": v["name"], "info": v.get("metrics") or {}} for v in view_results]
    ious = [float(p["info"].get("iou") or 0.0) for p in per if p["info"]]
    dices = [float(p["info"].get("dice") or 0.0) for p in per if p["info"]]
    bnds = [float((p["info"].get("boundary") or {}).get("mean_px") or 0.0) for p in per if p["info"]]
    summary = {
        "iou_min": round(min(ious), 4) if ious else None,
        "iou_mean": round(sum(ious) / len(ious), 4) if ious else None,
        "dice_min": round(min(dices), 4) if dices else None,
        "missing_px_total": int(sum(int(p["info"].get("missing_px") or 0) for p in per)),
        "extra_px_total": int(sum(int(p["info"].get("extra_px") or 0) for p in per)),
        "boundary_mean_px_max": round(max(bnds), 3) if bnds else None,
        "views": [{"view": p["view"], "iou": p["info"].get("iou"), "dice": p["info"].get("dice"),
                   "missing_px": p["info"].get("missing_px"), "extra_px": p["info"].get("extra_px"),
                   "missing_frac": p["info"].get("missing_frac"),
                   "extra_frac": p["info"].get("extra_frac"),
                   "boundary_mean_px": (p["info"].get("boundary") or {}).get("mean_px")} for p in per],
    }
    fix = _fix_hint(verdict, view_results, top, all_targets, opts)
    first = view_results[0] if view_results else {}
    images = {"overlay": (first.get("images") or {}).get("overlay"),
              "sheet": (first.get("images") or {}).get("sheet"),
              "crops": crops,
              "per_view": [{"view": v["name"],
                            "overlay": (v.get("images") or {}).get("overlay"),
                            "sheet": (v.get("images") or {}).get("sheet"),
                            "crosscheck_overlay": (v.get("crosscheck") or {}).get("overlay"),
                            "crosscheck_sheet": (v.get("crosscheck") or {}).get("sheet"),
                            "crosscheck_image": (v.get("crosscheck") or {}).get("out")}
                           for v in view_results]}
    reasons = []
    degraded_reasons = []
    for v in view_results:
        for r in (v.get("reasons") or []):
            reasons.append("%s: %s" % (v["name"], r))
        for r in (v.get("degraded_reasons") or []):
            degraded_reasons.append("%s: %s" % (v["name"], r))
    out = {
        "verdict": verdict,
        "ok": True,
        "version": VISUALREPORT_VERSION,
        "op": "report",
        "label": label_s,
        "summary": _summary_text(verdict, summary, top, all_targets),
        "reasons": reasons,
        "degraded_reasons": degraded_reasons,
        "metrics": summary,
        "top_defects": top,
        "fix_hint": fix,
        "images": images,
        "views": view_results,
        "targets": all_targets,
        "method": method,
        "warnings": warnings,
        "run_dir": run_dir,
        "ref": ref_clean,
        "render": [v["path"] for v in view_list],
        "params": {"px_per_mm": ppm, "ref_box_px": (ref_box["box"] if ref_box else None),
                   "render_box_px": (list(render_box_px) if isinstance(render_box_px, (list, tuple))
                                     else render_box_px),
                   "max_defects": md, "iou_pass": iou_p, "iou_fail": iou_f,
                   "max_missing_frac": mmf, "max_extra_frac": mef},
        "ms": int(round((time.perf_counter() - t_start) * 1000.0)),
    }
    return out


def _summary_text(verdict, summary, top, targets):
    bits = ["verdict=%s" % verdict]
    if summary.get("iou_min") is not None:
        bits.append("IoU min %.4f / mean %.4f" % (summary["iou_min"], summary.get("iou_mean") or 0.0))
    if summary.get("missing_px_total"):
        bits.append("缺 %d px" % summary["missing_px_total"])
    if summary.get("extra_px_total"):
        bits.append("多 %d px" % summary["extra_px_total"])
    if top:
        bits.append("top_defect %s %.1f%%" % (top[0]["kind"], float(top[0]["area_frac"]) * 100.0))
    if targets:
        npass = sum(1 for t in targets if t.get("status") == "pass")
        bits.append("targets %d/%d 过" % (npass, len(targets)))
    return "；".join(bits)


# ────────────────────────────── op: selftest / help ──────────────────────────────
def _mk_test_png(arr_rgb, path):
    return _save_png_rgba(_rgba_from_rgb(arr_rgb), path)


def _self_image_base(n=128, square=72, at=28, notch=0):
    img = np.ones((n, n, 3), dtype=np.float32)
    x0, y0 = at, at
    x1, y1 = at + square, at + square
    img[y0:y1, x0:x1, :] = 0.18
    if notch:
        img[y0:y0 + notch, x1 - notch:x1, :] = 1.0
    return img


def visualreport_selftest():
    t0 = time.perf_counter()
    ev = {}
    run_root = _root_outdir(None)
    d = os.path.join(run_root, "visualreport_selftest")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        import tempfile
        d = tempfile.mkdtemp(prefix="dsh_visualreport_selftest_")
    try:
        base = _self_image_base()
        same_png = os.path.join(d, "same.png")
        notch_png = os.path.join(d, "notch.png")
        _mk_test_png(base, same_png)
        _mk_test_png(_self_image_base(notch=22), notch_png)
        r_same = visualreport_report(ref=same_png, render=same_png, out_dir=d, label="selftest_same")
        r_bad = visualreport_report(ref=same_png, render=notch_png, out_dir=d, label="selftest_notch")
        ev["same"] = {"ok": r_same.get("ok"), "verdict": r_same.get("verdict"),
                      "iou": (r_same.get("metrics") or {}).get("iou_min"),
                      "defects": len(r_same.get("top_defects") or []),
                      "warnings": len(r_same.get("warnings") or []),
                      "error": r_same.get("error")}
        first_bad = (r_bad.get("views") or [{}])[0]
        ev["notch"] = {"ok": r_bad.get("ok"), "verdict": r_bad.get("verdict"),
                       "iou": (r_bad.get("metrics") or {}).get("iou_min"),
                       "missing_px": (first_bad.get("metrics") or {}).get("missing_px"),
                       "defects": len(r_bad.get("top_defects") or []),
                       "crops": len((r_bad.get("images") or {}).get("crops") or []),
                       "error": r_bad.get("error")}
        ev["same_pass"] = (ev["same"]["ok"] is True and ev["same"]["verdict"] == "pass"
                           and ev["same"]["defects"] == 0)
        ev["notch_fail"] = (ev["notch"]["ok"] is True and ev["notch"]["verdict"] == "fail"
                            and ev["notch"]["defects"] > 0 and (ev["notch"]["missing_px"] or 0) > 0)
        bad = []
        bad.append(("ref_missing", visualreport_report(ref=os.path.join(d, "nope.png"), render=same_png)))
        bad.append(("no_render", visualreport_report(ref=same_png)))
        bad.append(("both", visualreport_report(ref=same_png, render=same_png,
                                                views=[{"name": "a", "path": same_png}])))
        bad.append(("max_defects", visualreport_report(ref=same_png, render=same_png, max_defects=-1)))
        bad.append(("bad_ppm", visualreport_report(ref=same_png, render=same_png, px_per_mm=0)))
        bad.append(("bad_target", visualreport_report(ref=same_png, render=same_png,
                                                      targets=[{"key": "weight", "expect_mm": 1, "tol_mm": 1}])))
        bad.append(("bad_box", visualreport_report(ref=same_png, render=same_png,
                                                   ref_box_px=[10, 10, 5, 5])))
        ev["bad_params"] = [{"case": k, "ok": bool(v.get("ok")), "error": str(v.get("error"))[:100]}
                            for k, v in bad]
        ev["bad_params_ok"] = all(not x["ok"] for x in ev["bad_params"])
        ok = bool(ev["same_pass"] and ev["notch_fail"] and ev["bad_params_ok"])
        return {"ok": ok, "evidence": ev, "ms": int(round((time.perf_counter() - t0) * 1000.0))}
    except Exception as e:
        return {"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}


def visualreport_help():
    return {
        "module": "visualreport.py", "version": VISUALREPORT_VERSION,
        "what": "同机位 QC 视觉报告：verdict-first（pass/fail/degraded）+ IoU/Dice/缺多面积/边界差 + "
                "最大缺陷 bbox/面积/位置 mm + 缺陷 crop + overlay/sheet + fix_hint",
        "ops": {"report": {"doc": "出报告（不建/不改几何）",
                           "args": ["ref", "render | views", "px_per_mm", "targets", "out_dir",
                                    "max_defects", "ref_box_px", "render_box_px", "label",
                                    "iou_pass", "iou_fail", "max_missing_frac", "max_extra_frac",
                                    "save_images"]},
                "selftest": {"doc": "合成两张已知差异图（同图应 pass / 缺角应 fail）+ 坏参数拒绝"},
                "help": {"doc": "本说明"}},
        "report_schema": {
            "ref": "参考图（PNG/JPEG/WebP）",
            "render": "渲染图路径（与 views 二选一）",
            "views": "[{name,path}] 多视图（最多 8 个）",
            "ref_box_px": "[x0,y0,x1,y1]：参考大图里的视图裁切（给了就按 bbox 归一）",
            "render_box_px": "渲染图的裁切（可选）",
            "px_per_mm": "渲染图每毫米像素数（给了才有 mm 口径 / 才能判 targets）",
            "targets": "[{key:width|height|diag|area, expect_mm, tol_mm}]",
            "max_defects": "返回/定位的缺陷数（默认 3；crop 最多 3 张）",
            "iou_pass": "0.95（默认）",
            "iou_fail": "0.85（默认）",
            "max_missing_frac": "0.02（默认，缺面积占比上限）",
            "max_extra_frac": "0.02（默认，多面积占比上限）",
            "save_images": "true（默认）：写 overlay/sheet/crop"},
        "verdict_rules": ["fail：IoU < iou_fail 或缺/多面积占比 > 上限或 targets 超标",
                          "degraded：掩膜空/尺寸不一致被缩放/targets 缺 px_per_mm/IoU 落在阈值之间"
                          "（degraded ≠ 通过）",
                          "pass：IoU >= iou_pass 且缺多面积在阈值内且 targets 全过"],
        "outputs": ["verdict / ok / summary / reasons",
                    "metrics{iou_min,iou_mean,dice_min,missing_px_total,extra_px_total,"
                    "boundary_mean_px_max,views[]}",
                    "top_defects[{kind,view,bbox_px,area_px,area_frac,center_px,center_mm,area_mm2,crop}]",
                    "images{overlay,sheet,crops[],per_view[]}",
                    "fix_hint / views[]（每视图 metrics/crosscheck/defects/verdict/reasons）/ warnings"],
        "protocol": ["先跑 qc_render_views 出同机位图（ref_path 可顺带算 IoU），再 visualreport_report 出 verdict 报告；",
                     "verdict 不是 pass 就不许当通过：degraded 要补证据（给 px_per_mm/裁切框/透明背景渲染图）；",
                     "把 fix_hint 与 top_defects 的 crop 一起给模型，它才知道「改哪里、改多少」；",
                     "同机位才是前提：换机位/分辨率/裁切/引擎后的分数不可跨版本比较。"],
        "limits": ["只做轮廓/几何客观比对；材质/颜色/审美不在本模块",
                   "照片类参考图需先抠背景（掩膜启发式分不出复杂背景）",
                   "px_per_mm 缺失时位置/面积为像素口径（mm 字段 null）",
                   "qc 未注入时 IoU/Dice/边界用 numpy 兜底（boundary 为膨胀近似）"],
        "kernel": "共享内核 K.dsh_kit v%s" % getattr(_KIT, "version", "?"),
    }


# 注册（一行）：K.dsh_visualreport_api["report"] / K.dsh_visualreport_api("report", {...})
_KIT.register("visualreport", VISUALREPORT_VERSION,
              {"report": visualreport_report, "selftest": visualreport_selftest,
               "help": visualreport_help})
