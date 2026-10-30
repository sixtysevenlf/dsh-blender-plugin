# -*- coding: utf-8 -*-
"""DSH 无头多视轮廓拟合（shapesolve）v1 —— P1。

要解决的问题：参数化形体（盒体/支架/车壳基础体）"看轮廓反推尺寸"目前只能靠渲染出图 + 肉眼，
GUI/timers/bpy.render 在 headless 里都不可靠。本模块给一条**纯计算**通路：
  参考图 → 前景 mask（alpha 或 Otsu）→ 对当前场景做**正交解析投影 + numpy 三角形光栅化**（256²）
  → IoU + 剖面差打分 → 随机搜索 + 坐标细化 → best 参数 + 逐视 IoU + 敏感性 + 可复现脚本。

为什么不用 bpy.render：① headless 下 EEVEE 编译/GPU 依赖重；② 求解要跑上百次评估，渲染太慢；
③ 正交轮廓只需投影三角形，几何真值直接来自**求值网格**（Boolean/修改器都算数）。

口径（写死，改算法先改这里）：
  * 相机：视线 f = normalize(look_at - from)，up 参考 +Z（f 近 Z 轴时换 +Y），right = normalize(f × up)，
    up' = right × f。像素映射：u = res/2 + dot(p-look_at, right) × res/ortho_scale，
    v = res/2 - dot(p-look_at, up') × res/ortho_scale —— 即 ortho_scale 是画面**宽**的世界尺寸，
    look_at 投到画面中心，像素中心在 (i+0.5, j+0.5)。**参考图必须按同一相机参数生成**。
  * mask：上→下（Blender image 像素是底→上，读入后翻转）；参考图有有效 alpha 用 alpha，否则 Otsu。
  * 打分：score = Σ_view w·(w_iou·(1-IoU) + w_profile·profile_penalty) / Σw；
    profile_penalty = 行宽剖面 + 列高剖面 的平均 L1（归一化到 [0,1]）。
  * 优化：确定性（np.random.default_rng(seed)）；随机采样 + 逐坐标步进；iterations / budget_ms 双上限。
  * 敏感性：best ±1% 量程 单参数扰动 → 平均 IoU 下降；strong / weak / unidentifiable。

API 挂 K.dsh_shapesolve_api：solve / selftest / help（模块尾部 _KIT.register 一行）。
纯 bpy + numpy，不引 PIL/scipy/cv2。

无头跑法：
    blender_rt_headless(preload="shapesolve", engine="none", script_file="tests/shapesolve_selftest.py")
"""
import json
import math
import os
import sys
import time

import bpy
import numpy as np

SHAPESOLVE_VERSION = 1
MASK_MODES = ("auto", "alpha", "otsu", "luma")

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("shapesolve.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")

_j = _KIT.j


# ────────────────────────────── 基础 helper ──────────────────────────────
def _kernel():
    return sys.modules.get("dsh_rt_kernel")


def _win(p):
    """WSL 路径 → Blender（Windows 进程）可用形态。"""
    s = str(p or "")
    if not s or not s.startswith("/"):
        return s
    K = _kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            return str(K.win_path(s))
        except Exception:
            pass
    return s


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def _vec3(v):
    if isinstance(v, dict):
        v = v.get("xyz")
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    out = []
    for x in v:
        f = _num(x)
        if f is None:
            return None
        out.append(f)
    return out


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _r(x, n=6):
    try:
        return round(float(x), int(n))
    except Exception:
        return None


def _receipt(body, t0=None):
    d = dict(body or {})
    d.setdefault("units", {"mm_per_unit": _r(_KIT.units(), 6)})
    if t0 is not None:
        d["ms"] = int((time.perf_counter() - t0) * 1000)
    return d


def _err(msg, **kw):
    d = {"ok": False, "error": str(msg)[:400], "hint": kw.pop("hint", None)}
    d.update(kw)
    return _j(_receipt(d))


# ────────────────────────────── 入参校验 ──────────────────────────────
def _norm_params(raw):
    if not isinstance(raw, (list, tuple)) or not raw:
        return None, "params 必填：[{name,min,max,init,integer}]，至少 1 个参数"
    out, seen = [], set()
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            return None, "params[%d] 不是对象" % i
        name = str(p.get("name") or "").strip()
        if not name:
            return None, "params[%d] 缺 name" % i
        if name in seen:
            return None, "参数名重复: %s" % name
        seen.add(name)
        lo, hi, ini = _num(p.get("min")), _num(p.get("max")), _num(p.get("init"))
        if lo is None or hi is None:
            return None, "参数 %s 的 min/max 必须是数字" % name
        if not (hi > lo):
            return None, "参数 %s 的 max 必须 > min（min=%.6g max=%.6g）" % (name, lo, hi)
        if ini is None:
            ini = (lo + hi) / 2.0
        integer = bool(p.get("integer") or False)
        if integer:
            ini = float(int(round(ini)))
        ini = min(max(ini, lo), hi)
        out.append({"name": name, "min": float(lo), "max": float(hi), "init": float(ini),
                    "integer": integer,
                    "desc": str(p.get("desc") or "")})
    return out, None


def _norm_views(raw, resolution):
    if not isinstance(raw, (list, tuple)) or not raw:
        return None, "views 必填：[{ref,from,look_at,ortho_scale}]，至少 1 个视图"
    out = []
    for i, v in enumerate(raw):
        if not isinstance(v, dict):
            return None, "views[%d] 不是对象" % i
        ref = v.get("ref")
        if not ref:
            return None, "views[%d] 缺 ref（参考图路径）" % i
        p = _win(ref)
        if not os.path.isfile(p):
            return None, "views[%d] 的参考图不存在: %s" % (i, p)
        vf, vl = _vec3(v.get("from")), _vec3(v.get("look_at"))
        if vf is None or vl is None:
            return None, "views[%d] 的 from/look_at 必须是 3 个数字" % i
        d = np.linalg.norm(np.array(vl, dtype=np.float64) - np.array(vf, dtype=np.float64))
        if d < 1e-9:
            return None, "views[%d] 的 from 与 look_at 重合（视线方向无定义）" % i
        osc = _num(v.get("ortho_scale"))
        if osc is None or osc <= 0:
            return None, "views[%d] 的 ortho_scale 必须是正数（正交画面宽度的世界尺寸）" % i
        mode = str(v.get("mask_mode") or "auto").lower()
        if mode not in MASK_MODES:
            return None, "views[%d] mask_mode 只支持 %s，收到 %r" % (i, list(MASK_MODES), v.get("mask_mode"))
        w = _num(v.get("weight"))
        if w is None:
            w = 1.0
        if w <= 0:
            return None, "views[%d] weight 必须 > 0" % i
        out.append({"index": i, "ref": p, "ref_raw": str(ref),
                    "from": vf, "look_at": vl, "ortho_scale": float(osc),
                    "weight": float(w), "invert": bool(v.get("invert") or False),
                    "mask_mode": mode})
    return out, None


def _norm_objective(raw):
    raw = dict(raw or {}) if isinstance(raw, dict) else {}
    wi = _num(raw.get("iou"))
    wp = _num(raw.get("profile"))
    wi = 1.0 if wi is None else wi
    wp = 0.2 if wp is None else wp
    if wi < 0 or wp < 0:
        return None, "objective.iou / objective.profile 必须 ≥ 0"
    if wi + wp <= 1e-12:
        return None, "objective.iou 与 profile 不能同时为 0（没有打分就没有解）"
    return {"iou": float(wi), "profile": float(wp)}, None
# ────────────────────────────── 参考图 → mask ──────────────────────────────
def _otsu_threshold(lum):
    """Otsu 阈值（0..1），直方图 256 档；常数图返回 None。"""
    hist, _edges = np.histogram(lum, bins=256, range=(0.0, 1.0))
    total = float(hist.sum())
    if total <= 0:
        return None
    p = hist.astype(np.float64) / total
    omega = np.cumsum(p)
    idx = np.arange(256, dtype=np.float64)
    mu = np.cumsum(p * idx)
    mu_t = float(mu[-1])
    denom = omega * (1.0 - omega)
    denom[denom <= 1e-12] = 1e-12
    sigma = (mu_t * omega - mu) ** 2 / denom
    k = int(np.argmax(sigma))
    return (k + 0.5) / 256.0


def _load_ref_mask(path, res, mask_mode="auto", invert=False):
    """读参考图 → 上→下 bool mask（res×res）+ 取证信息；失败回 (None, 原因)。

    前景判定：① 有效 alpha（有 <0.5 也有 >0.5）→ alpha>0.5；
             ② 否则 Otsu：把像素分成明/暗两类，**背景更贴边** —— 边界像素占比少的一类判为前景；
             ③ 两类都贴满/全同色 → 失败（不猜）。
    """
    p = _win(path)
    if not p or not os.path.isfile(p):
        return None, "参考图不存在: %s" % p
    img = None
    try:
        img = bpy.data.images.load(p, check_existing=False)
        w, h = int(img.size[0]), int(img.size[1])
        if w <= 0 or h <= 0:
            return None, "参考图尺寸非法: %dx%d (%s)" % (w, h, p)
        px = np.array(img.pixels[:], dtype=np.float64)
        ch = max(1, len(px) // max(1, w * h))
        arr = px.reshape(h, w, ch)[::-1]     # Blender 像素底→上；翻成上→下
    except Exception as e:
        return None, "参考图读失败: %s: %s" % (type(e).__name__, str(e)[:160])
    finally:
        if img is not None:
            try:
                bpy.data.images.remove(img)
            except Exception:
                pass
    if arr.shape[2] < 3:
        return None, "参考图通道数异常: %d" % arr.shape[2]
    mode = str(mask_mode or "auto").lower()
    info = {"path": p, "orig_size": [w, h], "mode": None, "threshold": None}
    use_alpha = False
    if arr.shape[2] >= 4 and mode in ("auto", "alpha"):
        alpha = arr[..., 3]
        if np.nanmin(alpha) < 0.5 and np.nanmax(alpha) > 0.5:
            mask = alpha > 0.5
            use_alpha = True
            info["mode"] = "alpha"
        elif mode == "alpha":
            return None, "参考图 alpha 不分明（min=%.3f max=%.3f），不能当 alpha mask 用" % (
                float(np.nanmin(alpha)), float(np.nanmax(alpha)))
    if not use_alpha:
        rgb = np.clip(arr[..., :3], 0.0, 1.0)
        lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        thr = _otsu_threshold(lum)
        if thr is None:
            return None, "参考图是常数图，Otsu 分不出前景/背景"
        bright = lum > thr
        info["threshold"] = _r(thr, 6)
        info["mode"] = "otsu"
        bw = max(2, int(round(min(w, h) * 0.05)))
        border = np.zeros((h, w), dtype=bool)
        border[:bw, :] = True
        border[-bw:, :] = True
        border[:, :bw] = True
        border[:, -bw:] = True
        b_bright = int(np.logical_and(bright, border).sum())
        b_dark = int(np.logical_and(~bright, border).sum())
        if b_bright > b_dark:
            mask = ~bright          # 亮色贴边 ⇒ 背景是亮的 ⇒ 前景是暗的
        elif b_dark > b_bright:
            mask = bright
        else:
            # 边界两类一样多（物体满框/纯色边） → 取面积小的一类
            mask = bright if int(bright.sum()) <= int((~bright).sum()) else ~bright
    if invert:
        mask = ~mask
    fg = int(mask.sum())
    total = int(mask.size)
    if fg == 0 or fg == total:
        return None, ("参考图分不出前景/背景（前景 %d / 总 %d，mode=%s）—— 换 alpha PNG 或显式 invert/mask_mode"
                      % (fg, total, info["mode"]))
    if w != int(res) or h != int(res):
        yi = np.clip((np.arange(int(res)) * h / float(res)).astype(np.int64), 0, h - 1)
        xi = np.clip((np.arange(int(res)) * w / float(res)).astype(np.int64), 0, w - 1)
        mask = mask[np.ix_(yi, xi)]
        info["resized_from"] = [w, h]
    info["foreground_px"] = int(mask.sum())
    info["foreground_frac"] = _r(float(mask.mean()), 6)
    return {"mask": mask.astype(bool), "info": info}, None


# ────────────────────────────── 正交解析投影 + 光栅化 ──────────────────────────────
def _view_basis(vfrom, vlook):
    """正交相机基：f=视线方向（from→look_at），right/up' 与 f 正交（右手系）。"""
    f = np.array(vlook, dtype=np.float64) - np.array(vfrom, dtype=np.float64)
    n = float(np.linalg.norm(f))
    if n < 1e-12:
        raise ValueError("from 与 look_at 重合")
    f = f / n
    up_ref = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(f, up_ref))) > 0.99:
        up_ref = np.array([0.0, 1.0, 0.0])
    right = np.cross(f, up_ref)
    rn = float(np.linalg.norm(right))
    if rn < 1e-12:
        raise ValueError("视线与 up 退化，无法构造相机基")
    right = right / rn
    up = np.cross(right, f)
    return f, right, up


def _project_uv(world_verts, view, res):
    _f, right, up = _view_basis(view["from"], view["look_at"])
    rel = np.asarray(world_verts, dtype=np.float64) - np.array(view["look_at"], dtype=np.float64)
    s = float(res) / float(view["ortho_scale"])
    u = rel @ right * s + float(res) / 2.0
    v = float(res) / 2.0 - rel @ up * s
    return u, v


def _rasterize_tris(u, v, tris, res):
    """numpy 三角形光栅化（像素中心在 i+0.5）→ res×res bool mask。"""
    res = int(res)
    mask = np.zeros((res, res), dtype=bool)
    if len(tris) == 0:
        return mask
    U = np.asarray(u, dtype=np.float64)[np.asarray(tris, dtype=np.int64)]
    V = np.asarray(v, dtype=np.float64)[np.asarray(tris, dtype=np.int64)]
    for k in range(len(U)):
        x0, x1, x2 = U[k]
        y0, y1, y2 = V[k]
        ix0 = int(math.floor(min(x0, x1, x2) - 0.5))
        ix1 = int(math.ceil(max(x0, x1, x2) - 0.5))
        iy0 = int(math.floor(min(y0, y1, y2) - 0.5))
        iy1 = int(math.ceil(max(y0, y1, y2) - 0.5))
        ix0 = max(0, ix0); iy0 = max(0, iy0)
        ix1 = min(res - 1, ix1); iy1 = min(res - 1, iy1)
        if ix1 < ix0 or iy1 < iy0:
            continue
        xs = np.arange(ix0, ix1 + 1, dtype=np.float64) + 0.5
        ys = np.arange(iy0, iy1 + 1, dtype=np.float64) + 0.5
        X, Y = np.meshgrid(xs, ys)
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-12:
            continue
        w0 = ((y1 - y2) * (X - x2) + (x2 - x1) * (Y - y2)) / den
        w1 = ((y2 - y0) * (X - x2) + (x0 - x2) * (Y - y2)) / den
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        mask[iy0:iy1 + 1, ix0:ix1 + 1] |= inside
    return mask


def _collect_scene_tris(max_tris=300000):
    """当前场景全部可见 mesh 的**求值后**世界三角面 → (V,T) 或 ("error", 原因)。"""
    dg = bpy.context.evaluated_depsgraph_get()
    verts, tris, base, skipped = [], [], 0, []
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        try:
            if ob.hide_render or ob.hide_viewport:
                continue
        except Exception:
            pass
        obe = None
        try:
            obe = ob.evaluated_get(dg)
            me = obe.to_mesh()
            if me is None:
                skipped.append(str(ob.name))
                continue
            me.calc_loop_triangles()
            n = len(me.vertices)
            nt = len(me.loop_triangles)
            if n == 0 or nt == 0:
                skipped.append(str(ob.name))
                continue
            v = np.empty(n * 3, dtype=np.float64)
            me.vertices.foreach_get("co", v)
            v = v.reshape(-1, 3)
            idx = np.empty(nt * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", idx)
            idx = idx.reshape(-1, 3)
            mw = np.array(obe.matrix_world, dtype=np.float64)
            wv = v @ mw[:3, :3].T + mw[:3, 3]
            verts.append(wv)
            tris.append(idx + base)
            base += n
        except Exception as e:
            skipped.append("%s(%s)" % (ob.name, str(e)[:60]))
        finally:
            try:
                if obe is not None:
                    obe.to_mesh_clear()
            except Exception:
                pass
    if not verts:
        return None, "场景里没有可用 mesh（求值后 0 面）：%s" % (skipped[:6] or "空场景")
    V = np.concatenate(verts, axis=0)
    T = np.concatenate(tris, axis=0)
    if len(T) > int(max_tris):
        return None, ("求值网格 %d 面 > max_tris %d —— 轮廓求解只适合基础体/中低模；"
                      "先减面或只留目标对象" % (len(T), int(max_tris)))
    return (V, T), None


def _profile_penalty(ref_mask, cur_mask):
    """剖面差：行宽剖面 + 列高剖面 的平均 L1（归一化到 [0,1]）。"""
    res = float(ref_mask.shape[0])
    rr = ref_mask.sum(axis=1) / res
    cr = ref_mask.sum(axis=0) / res
    rc = cur_mask.sum(axis=1) / res
    cc = cur_mask.sum(axis=0) / res
    return float((np.abs(rr - rc).mean() + np.abs(cr - cc).mean()) / 2.0)


def _evaluate(apply_fn, param_vals, views, refs, resolution, objective):
    """apply(params) → 求值网格 → 逐视投影/光栅 → (总 score, 逐视明细, masks)。"""
    apply_fn(dict(param_vals))
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    got, err = _collect_scene_tris()
    if err:
        raise RuntimeError("求值网格不可用: %s" % err)
    V, T = got
    rows, masks = [], []
    total, wsum = 0.0, 0.0
    for view, ref in zip(views, refs):
        u, v = _project_uv(V, view, resolution)
        mask = _rasterize_tris(u, v, T, resolution)
        masks.append(mask)
        iou = _KIT.iou(mask, ref["mask"])[0]
        iou = 0.0 if iou is None else float(iou)
        pen = _profile_penalty(ref["mask"], mask)
        w = float(view.get("weight") or 1.0)
        s = w * (float(objective["iou"]) * (1.0 - iou) + float(objective["profile"]) * pen)
        rows.append({"view": int(view.get("index", len(rows))), "iou": _r(iou, 6),
                     "profile_penalty": _r(pen, 6), "objective": _r(s / max(w, 1e-12), 6),
                     "weighted_score": _r(s, 6), "foreground_px": int(mask.sum()),
                     "ref_foreground_px": int(ref["mask"].sum())})
        total += s
        wsum += w
    return (total / max(wsum, 1e-9)), rows, masks
# ────────────────────────────── setup 编译 / 参数夹取 ──────────────────────────────
def _compile_setup(setup):
    ns = {"__name__": "shapesolve_setup", "bpy": bpy, "np": np, "math": math}
    K = _kernel()
    if K is not None:
        ns["K"] = K
    try:
        exec(compile(setup, "<shapesolve setup>", "exec"), ns)
    except Exception as e:
        import traceback
        raise RuntimeError("setup 执行失败: %s: %s | %s"
                           % (type(e).__name__, str(e)[:200], traceback.format_exc()[-300:]))
    fn = ns.get("apply")
    if not callable(fn):
        raise RuntimeError("setup 必须定义一个 callable apply(params_dict)：参数名→值，负责把场景几何设成该参数"
                           "（现有全局名：%s）" % sorted(k for k in ns if not k.startswith("__"))[:12])
    return fn, ns


def _clip(defn, v):
    lo, hi = float(defn["min"]), float(defn["max"])
    try:
        x = float(v)
    except Exception:
        x = float(defn["init"])
    if not math.isfinite(x):
        x = float(defn["init"])
    x = min(max(x, lo), hi)
    if defn.get("integer"):
        x = float(min(hi, max(lo, int(round(x)))))
    return x


def _param_values(defs, init):
    out = {}
    for p in defs:
        v = (init or {}).get(p["name"], p["init"])
        out[p["name"]] = _clip(p, v)
    return out


def _mean_iou(rows):
    if not rows:
        return 0.0
    return float(sum(float(r.get("iou") or 0.0) for r in rows) / len(rows))


# ────────────────────────────── 优化器（随机 + 坐标细化，确定性） ──────────────────────────────
def _optimize(apply_fn, defs, views, refs, resolution, objective, iterations, budget_ms, seed, init):
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    names = [p["name"] for p in defs]
    defmap = {p["name"]: p for p in defs}
    t0 = time.perf_counter()

    def _elapsed_ms():
        return (time.perf_counter() - t0) * 1000.0

    def _budget_hit():
        return int(budget_ms) > 0 and _elapsed_ms() > float(budget_ms)

    best = _param_values(defs, init)
    best_score, best_rows, _m = _evaluate(apply_fn, best, views, refs, resolution, objective)
    eval_count, runs, budget_hit, no_improve = 1, 0, False, 0
    steps = {n: 0.25 * (float(defmap[n]["max"]) - float(defmap[n]["min"])) for n in names}
    min_step = {n: max(1e-8, 1e-4 * (float(defmap[n]["max"]) - float(defmap[n]["min"]))) for n in names}

    def _random_cand(center=None, sigma=0.18):
        out = {}
        for n in names:
            d = defmap[n]
            span = float(d["max"]) - float(d["min"])
            base = center[n] if center else float(d["init"])
            out[n] = _clip(d, base + float(rng.normal(0.0, sigma)) * span)
        return out

    for it in range(int(iterations)):
        if _budget_hit():
            budget_hit = True
            break
        if rng.random() < 0.45:
            cand = _random_cand(best, 0.18)
        else:
            n = names[it % len(names)]
            sign = 1.0 if rng.random() < 0.5 else -1.0
            cand = dict(best)
            cand[n] = _clip(defmap[n], cand[n] + sign * steps[n])
        cand = {n: _clip(defmap[n], cand[n]) for n in names}
        if all(abs(cand[n] - best[n]) < 1e-12 for n in names):
            cand = _random_cand(best, 0.10)
        try:
            score, rows, _m2 = _evaluate(apply_fn, cand, views, refs, resolution, objective)
        except Exception as e:
            raise RuntimeError("第 %d 次评估失败（apply/求值网格出错）: %s: %s"
                               % (it + 1, type(e).__name__, str(e)[:200]))
        eval_count += 1
        runs = it + 1
        if score < best_score - 1e-12:
            best, best_score, best_rows = cand, score, rows
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= 2 * len(names):
                for n in names:
                    steps[n] = max(min_step[n], steps[n] * 0.6)
                no_improve = 0
    return {"best": best, "best_score": float(best_score), "best_rows": best_rows,
            "eval_count": eval_count, "iterations_run": runs, "budget_hit": bool(budget_hit),
            "optimize_ms": int(_elapsed_ms())}


def _sensitivity(apply_fn, defs, views, refs, resolution, objective, best, best_score, base_iou):
    """单参数 ±1% 量程扰动 → 平均 IoU 下降 → strong / weak / unidentifiable。"""
    out = {}
    for p in defs:
        name = p["name"]
        span = float(p["max"]) - float(p["min"])
        delta = 0.01 * span
        trials = []
        for sign, label in ((1.0, "+1%"), (-1.0, "-1%")):
            cand = dict(best)
            cand[name] = _clip(p, cand[name] + sign * delta)
            if abs(cand[name] - best[name]) < 1e-12:
                cand[name] = _clip(p, cand[name] - sign * delta)
            try:
                s, rows, _m = _evaluate(apply_fn, cand, views, refs, resolution, objective)
            except Exception as e:
                trials.append({"direction": label, "state": "error", "error": "%s: %s"
                               % (type(e).__name__, str(e)[:160])})
                continue
            iou = _mean_iou(rows)
            trials.append({"direction": label, "value": _r(cand[name], 8),
                           "score": _r(s, 6), "iou": _r(iou, 6),
                           "iou_drop": _r(base_iou - iou, 6),
                           "score_delta": _r(s - best_score, 6)})
        drops = [t["iou_drop"] for t in trials if isinstance(t.get("iou_drop"), (int, float))]
        deltas = [t["score_delta"] for t in trials if isinstance(t.get("score_delta"), (int, float))]
        iou_drop = max(drops) if drops else 0.0
        score_delta = max(deltas) if deltas else 0.0
        if iou_drop >= 0.01 or score_delta >= 0.01:
            cls = "strong"
        elif iou_drop >= 0.002 or score_delta >= 0.002:
            cls = "weak"
        else:
            cls = "unidentifiable"
        out[name] = {"class": cls, "perturb": _r(delta, 8),
                     "fraction_of_range": 0.01, "iou_drop": _r(iou_drop, 6),
                     "score_delta": _r(score_delta, 6), "trials": trials,
                     "why": ("±1%% 量程就让平均 IoU 掉 %.4f ⇒ 该参数可从轮廓辨识" % iou_drop)
                            if cls != "unidentifiable"
                            else ("±1%% 量程对轮廓几乎无影响 ⇒ 该参数在当前视角/参考图下不可辨识"
                                  "（补一个能看到它的视图，或缩参数量程）")}
    return out
# ────────────────────────────── op: solve ──────────────────────────────
def shapesolve_solve(setup=None, params=None, views=None, objective=None, iterations=200,
                     budget_ms=10000, seed=0, out_dir=None, resolution=256, init=None,
                     max_tris=300000):
    """多视正交轮廓拟合：反推 params，让当前场景在给定相机下的轮廓最贴参考图。

    入参（全量）：setup / params / views / objective / iterations / budget_ms / seed /
                  out_dir / resolution / init（可选：覆盖参数初值）/ max_tris
    回执：ok / best / score / best_iou_mean / per_view / sensitivity / repro / …
    """
    t0 = time.perf_counter()
    if not isinstance(setup, str) or not setup.strip():
        return _err("setup 必填：Python 代码，必须定义 apply(params_dict)（负责按参数设置场景几何）",
                    stage="validate")
    defs, perr = _norm_params(params)
    if perr:
        return _err(perr, stage="validate")
    res = _num(resolution)
    if res is None or int(res) != res or int(res) < 64 or int(res) > 512:
        return _err("resolution 必须是 64..512 的整数（默认 256）", stage="validate")
    res = int(res)
    nviews, verr = _norm_views(views, res)
    if verr:
        return _err(verr, stage="validate")
    obj, oerr = _norm_objective(objective)
    if oerr:
        return _err(oerr, stage="validate")
    it = _num(iterations)
    if it is None or int(it) != it or int(it) < 1:
        return _err("iterations 必须是 ≥1 的整数", stage="validate")
    it = int(it)
    bud = _num(budget_ms)
    if bud is None or bud < 0:
        return _err("budget_ms 必须是 ≥0 的数字（0 = 不限）", stage="validate")
    sd = _num(seed)
    if sd is None or int(sd) != sd:
        return _err("seed 必须是整数", stage="validate")
    sd = int(sd)
    mt = _num(max_tris)
    if mt is None or int(mt) < 1:
        mt = 300000
    mt = int(mt)
    init_vals = {}
    if init is not None:
        if not isinstance(init, dict):
            return _err("init 必须是 {参数名: 值} 对象", stage="validate")
        allowed = {p["name"] for p in defs}
        for k, v in init.items():
            if str(k) not in allowed:
                return _err("init 里不认识的参数 %r" % k, allowed=sorted(allowed), stage="validate")
            nv = _num(v)
            if nv is None:
                return _err("init.%s 必须是数字" % k, stage="validate")
            init_vals[str(k)] = nv
    try:
        apply_fn, _ns = _compile_setup(setup)
    except Exception as e:
        return _err(str(e), stage="setup")
    refs = []
    try:
        for vi, vw in enumerate(nviews):
            r, rerr = _load_ref_mask(vw["ref"], res, mask_mode=vw["mask_mode"], invert=vw["invert"])
            if rerr:
                return _err("视图 %d 的参考图不可用: %s" % (vi, rerr), stage="ref")
            refs.append(r)
    except Exception as e:
        return _err("参考图处理异常: %s: %s" % (type(e).__name__, str(e)[:200]), stage="ref")
    try:
        _got, gerr = _collect_scene_tris(mt)
    except Exception as e:
        gerr = "%s: %s" % (type(e).__name__, str(e)[:200])
    if gerr:
        return _err("初始场景没有可用于投影的网格：%s —— setup 里要先造出基线体，apply 再去改它" % gerr,
                    stage="scene", hint="例：setup 里 create_cube 一次 + apply(p) 只改 object.scale")
    try:
        opt = _optimize(apply_fn, defs, nviews, refs, res, obj, it, bud, sd, init_vals)
    except Exception as e:
        import traceback
        return _err("优化失败: %s: %s" % (type(e).__name__, str(e)[:240]), stage="optimize",
                    traceback=traceback.format_exc()[-600:])
    best = opt["best"]
    try:
        final_score, final_rows, _fm = _evaluate(apply_fn, best, nviews, refs, res, obj)
    except Exception as e:
        return _err("收尾 apply(best) 失败: %s: %s" % (type(e).__name__, str(e)[:200]), stage="final")
    base_iou = _mean_iou(final_rows)
    sens = _sensitivity(apply_fn, defs, nviews, refs, res, obj, best, final_score, base_iou)
    # 收尾：场景停在 best（敏感性扰动会改场景，必须再 apply 一次）
    try:
        _evaluate(apply_fn, best, nviews, refs, res, obj)
    except Exception:
        pass
    repro_args = {
        "setup": setup,
        "params": [dict(p, init=_r(best[p["name"]], 10)) for p in defs],
        "views": [{"ref": v["ref_raw"], "from": v["from"], "look_at": v["look_at"],
                   "ortho_scale": v["ortho_scale"], "weight": v["weight"],
                   "invert": v["invert"], "mask_mode": v["mask_mode"]} for v in nviews],
        "objective": obj, "iterations": it, "budget_ms": int(bud), "seed": sd,
        "resolution": res, "max_tris": mt,
    }
    repro_script = ('K.dsh_shapesolve_api("solve", '
                    + json.dumps(repro_args, ensure_ascii=False) + ')')
    out = {
        "ok": True, "version": SHAPESOLVE_VERSION,
        "best": {k: _r(v, 10) for k, v in best.items()},
        "score": _r(final_score, 6),
        "best_iou_mean": _r(base_iou, 6),
        "per_view": final_rows,
        "refs": [{"view": r["info"].get("mode"), "path": r["info"].get("path"),
                  "orig_size": r["info"].get("orig_size"),
                  "foreground_frac": r["info"].get("foreground_frac"),
                  "threshold": r["info"].get("threshold")} for r in refs],
        "params": [{k: p[k] for k in ("name", "min", "max", "init", "integer")} for p in defs],
        "objective": obj, "resolution": res,
        "iterations": it, "iterations_run": int(opt["iterations_run"]),
        "eval_count": int(opt["eval_count"]), "budget_hit": bool(opt["budget_hit"]),
        "seed": sd, "optimize_ms": int(opt["optimize_ms"]),
        "sensitivity": sens,
        "repro": {"seed": sd, "best": {k: _r(v, 10) for k, v in best.items()},
                  "args": repro_args, "script": repro_script,
                  "apply_best": "apply(%s)" % json.dumps({k: _r(v, 10) for k, v in best.items()},
                                                          ensure_ascii=False)},
        "scene_final": "apply(best) 已再次应用 —— 场景停在 best 参数上（不是最后一次扰动）",
        "note": "ortho_scale 是画面**宽**的世界尺寸；参考图必须用同一 from/look_at/ortho_scale 生成",
        "honest_limits": [
            "正交轮廓只约束投影形状：沿视线方向的尺寸需要至少一个正面视图才可辨识（敏感性会标 unidentifiable）",
            "参考图按 bbox 外的绝对像素对齐（不是按 bbox 拉伸），所以 ortho_scale 写错会整体判错",
            "三角形光栅化是面积覆盖（无 AA），参考图有抗锯齿边缘时 IoU 会有 ~1px 级噪声",
            "只求轮廓：材质/内部结构/遮挡不参与打分；多物体场景按全部可见 mesh 的并集轮廓算",
        ],
    }
    if out_dir:
        try:
            d = _win(out_dir)
            if d:
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, "shapesolve_receipt.json")
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump({k: v for k, v in out.items() if k != "repro"}, fh,
                              ensure_ascii=False, indent=2)
                out["out"] = {"dir": d, "receipt": p}
        except Exception as e:
            out["out"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    return _j(_receipt(out, t0))
# ────────────────────────────── 模块内 selftest / help ──────────────────────────────
def _mask_to_png(path, mask_topdown, res):
    """bool mask（上→下）→ RGBA PNG（alpha=前景）；Blender 像素是底→上，写入前翻转。"""
    img = bpy.data.images.new("__shapesolve_ref", width=int(res), height=int(res), alpha=True)
    arr = np.zeros((int(res), int(res), 4), dtype=np.float32)
    arr[..., 0:3] = 1.0
    arr[..., 3] = np.asarray(mask_topdown, dtype=np.float32)[::-1]
    try:
        img.pixels.foreach_set(arr.reshape(-1))
    except Exception:
        img.pixels[:] = arr.reshape(-1).tolist()
    img.filepath_raw = _win(path)
    img.file_format = "PNG"
    img.save()
    try:
        bpy.data.images.remove(img)
    except Exception:
        pass


def _analytic_box_mask(view, dims, res):
    """独立解析实现：轴对齐盒体在正交视下的精确矩形 mask（不经过光栅化器，作参考图真值）。"""
    _f, right, up = _view_basis(view["from"], view["look_at"])
    dx, dy, dz = [float(x) for x in dims]
    corners = np.array([[sx * dx / 2.0, sy * dy / 2.0, sz * dz / 2.0]
                        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=np.float64)
    rel = corners - np.array(view["look_at"], dtype=np.float64)
    s = float(res) / float(view["ortho_scale"])
    u = rel @ right * s + float(res) / 2.0
    v = float(res) / 2.0 - rel @ up * s
    xs = np.arange(int(res), dtype=np.float64) + 0.5
    ys = np.arange(int(res), dtype=np.float64) + 0.5
    return ((ys[:, None] >= float(v.min())) & (ys[:, None] <= float(v.max())) &
            (xs[None, :] >= float(u.min())) & (xs[None, :] <= float(u.max())))


def shapesolve_selftest():
    """模块内快速自检：合成 ref（解析矩形）→ 反演盒体尺寸 → 坏参数 ok:false。"""
    import tempfile
    ev, tmp = {}, None
    t0 = time.perf_counter()
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    hidden = []
    objs = []
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001
        for ob in bpy.context.scene.objects:
            if ob.type == "MESH" and not ob.hide_render:
                hidden.append(ob)
                ob.hide_render = True
        import bmesh
        me = bpy.data.meshes.new("__ss_box")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new("__ss_box", me)
        bpy.context.scene.collection.objects.link(ob)
        objs.append(ob)
        setup = (
            "import bpy\n"
            "_ob = bpy.data.objects.get('__ss_box')\n"
            "def apply(p):\n"
            "    _ob.scale = (p['sx'], p['sy'], p['sz'])\n"
            "    bpy.context.view_layer.update()\n"
        )
        true_dims = (40.0, 20.0, 30.0)
        res = 128
        tmp = tempfile.mkdtemp(prefix="dsh_shapesolve_selftest_")
        views = [
            {"ref": os.path.join(tmp, "ref_a.png"), "from": [0, -200, 0], "look_at": [0, 0, 0],
             "ortho_scale": 100.0},
            {"ref": os.path.join(tmp, "ref_c.png"), "from": [0, 0, 200], "look_at": [0, 0, 0],
             "ortho_scale": 100.0},
        ]
        dims_by_view = [true_dims, true_dims]
        for vw, dims in zip(views, dims_by_view):
            _mask_to_png(vw["ref"], _analytic_box_mask(vw, dims, res), res)
        r = json.loads(shapesolve_solve(
            setup=setup,
            params=[{"name": "sx", "min": 5, "max": 60, "init": 30},
                    {"name": "sy", "min": 5, "max": 60, "init": 30},
                    {"name": "sz", "min": 5, "max": 60, "init": 30}],
            views=views, iterations=90, budget_ms=8000, seed=3, resolution=res))
        best = r.get("best") or {}
        errs = {}
        for i, k in enumerate(("sx", "sy", "sz")):
            got = float(best.get(k) or 0.0)
            errs[k] = abs(got - true_dims[i]) / true_dims[i]
        ev["solve_ok"] = r.get("ok") is True
        ev["param_rel_err_ok"] = bool(errs) and max(errs.values()) <= 0.10
        ev["iou_ok"] = all(float(p.get("iou") or 0.0) >= 0.85 for p in (r.get("per_view") or []))
        ev["repro_has_seed"] = "seed" in str((r.get("repro") or {}).get("script") or "")
        r_bad = json.loads(shapesolve_solve(setup=setup, params=[], views=views))
        ev["bad_params_okfalse"] = r_bad.get("ok") is False
        r_bad2 = json.loads(shapesolve_solve(setup="x = 1", params=[{"name": "a", "min": 0, "max": 1}],
                                             views=views))
        ev["bad_setup_okfalse"] = r_bad2.get("ok") is False
        ok = all(v is True for v in ev.values())
        return _j({"ok": bool(ok), "version": SHAPESOLVE_VERSION, "evidence": ev,
                   "best": best, "per_view": r.get("per_view"),
                   "rel_err": {k: _r(v, 5) for k, v in errs.items()},
                   "ms": int((time.perf_counter() - t0) * 1000)})
    except Exception as e:
        import traceback
        return _j({"ok": False, "version": SHAPESOLVE_VERSION, "evidence": ev,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "traceback": traceback.format_exc()[-600:]})
    finally:
        bpy.context.scene.unit_settings.scale_length = prev_scale
        for ob in objs:
            try:
                me2 = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if me2 is not None and me2.users == 0:
                    bpy.data.meshes.remove(me2)
            except Exception:
                pass
        for ob in hidden:
            try:
                ob.hide_render = False
            except Exception:
                pass
        if tmp:
            try:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass


def shapesolve_help():
    return _j({
        "module": "shapesolve.py", "version": SHAPESOLVE_VERSION,
        "what": "无头多视正交轮廓拟合：参考图 mask ↔ 求值网格解析投影 + numpy 光栅化 → IoU/剖面打分 → "
                "随机+坐标细化求参数 → 敏感性/可复现脚本；不依赖 GUI/timers/bpy.render",
        "ops": {
            "solve": {"sig": "setup, params[{name,min,max,init,integer}], views[{ref,from,look_at,ortho_scale,"
                            "weight,invert,mask_mode}], objective{iou,profile}, iterations=200, "
                            "budget_ms=10000, seed=0, out_dir?, resolution=256, init?, max_tris=300000",
                      "out": "best / score / best_iou_mean / per_view(iou,profile_penalty) / "
                             "sensitivity(strong|weak|unidentifiable) / repro.script / budget_hit"},
            "selftest": {"sig": "无参", "out": "合成 ref 反演盒体尺寸 + 坏参数 ok:false"},
            "help": {"sig": "无参"},
        },
        "protocol": [
            "setup 里先造**基线几何**（一次 create_cube），apply(p) 只改尺寸/位置 —— 每轮评估都会调它",
            "参考图必须用同一 from/look_at/ortho_scale 生成；ortho_scale 是画面**宽**的世界尺寸，look_at 投到画面中心",
            "mask：参考图有有效 alpha 用 alpha，否则 Otsu（背景更贴边 → 边界占比小的一类判前景）；可分不出就 ok:false",
            "参数 ≥3 时给 ≥2–3 个不同朝向的视图；敏感性 unidentifiable = 该参数的投影在当前视角下没信息",
            "可复现：回执 repro.script 原样包含 seed/视图像素口径；repro.args 的 params.init 已是 best",
        ],
        "mask_modes": ["auto（默认：alpha 优先，否则 Otsu）", "alpha", "otsu", "luma（同 otsu，亮度口径）"],
        "deps": {"required": "K.dsh_kit（_KIT.iou 也来自内核）", "optional": "无（纯 bpy + numpy）"},
        "honest_limits": [
            "正交轮廓只约束投影形状；沿视线尺寸需正面视图",
            "绝对像素对齐：ortho_scale/参考图分辨率写错会整体判错（不会按 bbox 自适应拉伸）",
            "光栅化无抗锯齿；参考图有 AA 时 IoU 有 ~1px 噪声",
            "只比轮廓并集：材质/内部结构/遮挡不参与",
        ],
    })


# 注册：模块尾部一行（K.dsh_shapesolve_api；ops = solve / selftest / help）
_KIT.register("shapesolve", SHAPESOLVE_VERSION, {
    "solve": shapesolve_solve, "selftest": shapesolve_selftest, "help": shapesolve_help})
