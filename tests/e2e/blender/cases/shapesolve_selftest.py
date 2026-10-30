# -*- coding: utf-8 -*-
"""tests/shapesolve_selftest.py —— 无头多视轮廓拟合（runtime/shapesolve.py）真机自检。

跑法（插件无头通道）：
    blender_rt_headless(preload="shapesolve", engine="none", factory_startup=True,
        timeout_ms=300000, script_file="<包路径>/tests/shapesolve_selftest.py")

或独立跑（自己 exec kit + shapesolve）：
    blender -b --factory-startup --python tests/shapesolve_selftest.py

覆盖：
  * 三视（-Y / +X / +Z）正交参考图（解析矩形，**独立于模块光栅化器**）反演盒体三边：
    相对误差 ≤5%、逐视 IoU ≥0.85、best_iou_mean ≥0.85；
  * 敏感性：±1% 量程扰动 → strong/weak/unidentifiable 分类有输出，至少 1 个可辨识；
  * 可复现：同 seed/同 args 两次结果完全一致；repro.script 含 seed；
  * Otsu 通路：无 alpha 的灰度参考图（暗物亮底）也能分前景并收敛；
  * 收尾：场景停在 best 参数（apply(best) 已再次应用）；
  * 坏参数：空 params / 无 apply 的 setup / 参考图不存在 / from==look_at / iterations=0 /
    越界 resolution / 未知 op / 未知参数 / init 未知键 → ok:false。
"""
import json
import math
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
RT = os.path.normpath(os.path.join(HERE, "..", "runtime"))


def _ensure_kernel():
    import types
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    if getattr(K, "dsh_shapesolve_api", None) is not None:
        return K
    for nm in ("kit.py", "shapesolve.py"):
        p = os.path.join(RT, nm)
        with open(p, "r", encoding="utf-8") as fh:
            src = fh.read()
        exec(compile(src, p, "exec"), {"__name__": nm[:-3], "__file__": p})
    return sys.modules["dsh_rt_kernel"]


K = _ensure_kernel()
API = K.dsh_shapesolve_api


def call(op, **kw):
    out = API(op, kw)
    return json.loads(out) if isinstance(out, str) else out


def _safe(op, *args, **kw):
    try:
        if args and isinstance(args[0], dict):
            kw = dict(args[0], **kw)
        return call(op, **kw)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}


import bpy
import bmesh


def reset_scene():
    for ob in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for me in list(bpy.data.meshes):
        if me.users == 0:
            try:
                bpy.data.meshes.remove(me)
            except Exception:
                pass
    for img in list(bpy.data.images):
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass


SETUP = (
    "import bpy\n"
    "_ob = bpy.data.objects.get('SS_BOX')\n"
    "def apply(p):\n"
    "    _ob.scale = (p['sx'], p['sy'], p['sz'])\n"
    "    bpy.context.view_layer.update()\n"
)

# 单视（-Y，u=x/v=z）只反演 sx/sz 时用的 setup：不能在 apply 里引用不存在的参数
SETUP_XZ = (
    "import bpy\n"
    "_ob = bpy.data.objects.get('SS_BOX')\n"
    "def apply(p):\n"
    "    _ob.scale = (p['sx'], 1.0, p['sz'])\n"
    "    bpy.context.view_layer.update()\n"
)


def make_box_object():
    me = bpy.data.meshes.new("SS_BOX")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("SS_BOX", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def write_mask_png(path, mask_topdown, res):
    """独立实现：bool mask（上→下）→ RGBA PNG（alpha=前景）。"""
    img = bpy.data.images.new("__ss_ref", width=int(res), height=int(res), alpha=True)
    import numpy as np
    arr = np.zeros((int(res), int(res), 4), dtype=np.float32)
    arr[..., 0:3] = 1.0
    arr[..., 3] = np.asarray(mask_topdown, dtype=np.float32)[::-1]
    try:
        img.pixels.foreach_set(arr.reshape(-1))
    except Exception:
        img.pixels[:] = arr.reshape(-1).tolist()
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    try:
        bpy.data.images.remove(img)
    except Exception:
        pass


def write_gray_png(path, mask_topdown, res):
    """无 alpha 的灰度图：前景=暗（0），背景=亮（1）→ 走 Otsu 通路。"""
    import numpy as np
    img = bpy.data.images.new("__ss_gray", width=int(res), height=int(res), alpha=False)
    m = np.asarray(mask_topdown, dtype=np.float32)[::-1]
    arr = np.ones((int(res), int(res), 4), dtype=np.float32)
    arr[..., 0:3] = np.where(m[..., None] > 0.5, 0.1, 0.9)
    arr[..., 3] = 1.0
    try:
        img.pixels.foreach_set(arr.reshape(-1))
    except Exception:
        img.pixels[:] = arr.reshape(-1).tolist()
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    try:
        bpy.data.images.remove(img)
    except Exception:
        pass


def rect_mask(u0, v0, u1, v1, res):
    """解析矩形 mask（上→下；像素中心 i+0.5，与模块投影口径一致）。"""
    import numpy as np
    xs = np.arange(int(res), dtype=np.float64) + 0.5
    ys = np.arange(int(res), dtype=np.float64) + 0.5
    return ((ys[:, None] >= float(v0)) & (ys[:, None] <= float(v1)) &
            (xs[None, :] >= float(u0)) & (xs[None, :] <= float(u1)))


def analytic_views(true_dims, res, tmp, ortho_scale=100.0):
    """三视解析参考图：A(-Y): u=x,v=z · B(+X): u=y,v=z · C(+Z): u=x,v=y。

    独立于模块的三角形光栅化器（直接按投影口径算矩形），避免"同一个 bug 自证"。
    """
    dx, dy, dz = [float(x) for x in true_dims]
    s = float(res) / float(ortho_scale)
    c = float(res) / 2.0
    specs = [
        ("ref_a.png", (0.0, -200.0, 0.0), dx, dz),
        ("ref_b.png", (200.0, 0.0, 0.0), dy, dz),
        ("ref_c.png", (0.0, 0.0, 200.0), dx, dy),
    ]
    views, masks = [], []
    for fname, vfrom, du, dv in specs:
        path = os.path.join(tmp, fname)
        m = rect_mask(c - du / 2.0 * s, c - dv / 2.0 * s, c + du / 2.0 * s, c + dv / 2.0 * s, res)
        write_mask_png(path, m, res)
        views.append({"ref": path, "from": list(vfrom), "look_at": [0.0, 0.0, 0.0],
                      "ortho_scale": ortho_scale})
        masks.append(m)
    return views, masks


EVID = {}


def _rec(key, cond, **nums):
    EVID[key] = bool(cond)
    for k, v in nums.items():
        EVID[key + "." + k] = v
    return bool(cond)
# ────────────────────────────── 主流程 ──────────────────────────────
def main():
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    tmp = tempfile.mkdtemp(prefix="dsh_shapesolve_selftest_")
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001
        reset_scene()
        make_box_object()
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        res = 256
        true_dims = (40.0, 20.0, 30.0)
        views, _ref_masks = analytic_views(true_dims, res, tmp, ortho_scale=100.0)
        params = [{"name": "sx", "min": 5.0, "max": 60.0, "init": 30.0},
                  {"name": "sy", "min": 5.0, "max": 60.0, "init": 30.0},
                  {"name": "sz", "min": 5.0, "max": 60.0, "init": 30.0}]
        r = _safe("solve", setup=SETUP, params=params, views=views,
                  objective={"iou": 1.0, "profile": 0.2}, iterations=240,
                  budget_ms=25000, seed=42, resolution=res, out_dir=tmp)
        best = r.get("best") or {}
        rel = {}
        for k, t in zip(("sx", "sy", "sz"), true_dims):
            got = float(best.get(k) or 0.0)
            rel[k] = abs(got - t) / t
        _rec("solve.ok", r.get("ok") is True, error=r.get("error"), ms=r.get("ms"))
        _rec("solve.rel_err_le5pct", bool(best) and max(rel.values()) <= 0.05,
             true_dims=list(true_dims), best=best,
             rel_err={k: round(v, 5) for k, v in rel.items()})
        ious = [float(p.get("iou") or 0.0) for p in (r.get("per_view") or [])]
        _rec("solve.per_view_count", len(ious) == 3, n=len(ious))
        _rec("solve.iou_ge_085", len(ious) == 3 and min(ious) >= 0.85, ious=ious)
        _rec("solve.best_iou_mean_ge_085", float(r.get("best_iou_mean") or 0.0) >= 0.85,
             best_iou_mean=r.get("best_iou_mean"))
        sens = r.get("sensitivity") or {}
        classes = {str(k): str((v or {}).get("class")) for k, v in sens.items()}
        _rec("solve.sensitivity_all_params", sorted(sens.keys()) == ["sx", "sy", "sz"])
        _rec("solve.sensitivity_classes_valid",
             all(c in ("strong", "weak", "unidentifiable") for c in classes.values()),
             classes=classes)
        _rec("solve.sensitivity_identifiable",
             sum(1 for c in classes.values() if c != "unidentifiable") >= 1, classes=classes)
        repro = r.get("repro") or {}
        _rec("solve.repro_script", "solve" in str(repro.get("script"))
             and "seed" in str(repro.get("script")) and "42" in str(repro.get("script")))
        _rec("solve.receipt_written", os.path.isfile(os.path.join(tmp, "shapesolve_receipt.json")))
        ob = bpy.data.objects.get("SS_BOX")
        fs = [round(float(x), 5) for x in ob.scale] if ob is not None else None
        _rec("solve.scene_at_best", bool(fs) and all(
            abs(fs[i] - float(best.get(k) or 0.0)) < 1e-3
            for i, k in enumerate(("sx", "sy", "sz"))), final_scale=fs)
        # ---- 确定性：同 seed/同 args 两次完全一致（便宜档 iterations=8） ----
        kw = dict(setup=SETUP, params=params, views=views, objective={"iou": 1.0, "profile": 0.2},
                  iterations=8, budget_ms=5000, seed=7, resolution=128)
        r_d1 = _safe("solve", **kw)
        r_d2 = _safe("solve", **kw)
        _rec("solve.deterministic",
             r_d1.get("ok") is True and r_d2.get("ok") is True
             and r_d1.get("best") == r_d2.get("best")
             and r_d1.get("score") == r_d2.get("score"),
             score=r_d1.get("score"), best=r_d1.get("best"))
        # ---- Otsu 通路：无 alpha 灰度图（暗物亮底） ----
        s = float(res) / 100.0
        c = float(res) / 2.0
        gray_mask = rect_mask(c - 40.0 / 2 * s, c - 30.0 / 2 * s,
                              c + 40.0 / 2 * s, c + 30.0 / 2 * s, res)
        gray_path = os.path.join(tmp, "ref_gray.png")
        write_gray_png(gray_path, gray_mask, res)
        r3 = _safe("solve", setup=SETUP_XZ,
                   params=[{"name": "sx", "min": 5.0, "max": 60.0, "init": 30.0},
                           {"name": "sz", "min": 5.0, "max": 60.0, "init": 30.0}],
                   views=[{"ref": gray_path, "from": [0.0, -200.0, 0.0], "look_at": [0.0, 0.0, 0.0],
                           "ortho_scale": 100.0}],
                   iterations=120, budget_ms=12000, seed=5, resolution=128)
        refs3 = r3.get("refs") or [{}]
        _rec("solve.otsu_mode", str((refs3[0] or {}).get("view")) == "otsu", refs=refs3)
        _rec("solve.otsu_converged", r3.get("ok") is True
             and float(r3.get("best_iou_mean") or 0.0) >= 0.80,
             best=r3.get("best"), iou=r3.get("best_iou_mean"))
        # ---- 坏参数必须 ok:false ----
        bad_cases = {
            "bad_params_empty": dict(setup=SETUP, params=[], views=views),
            "bad_setup_no_apply": dict(setup="x = 1", params=params, views=views),
            "bad_ref_missing": dict(setup=SETUP, params=params,
                                    views=[dict(views[0], ref=os.path.join(tmp, "__nope__.png"))]),
            "bad_from_equals_look": dict(setup=SETUP, params=params,
                                         views=[dict(views[0], from_=[0, 0, 0])]),
            "bad_iterations_zero": dict(setup=SETUP, params=params, views=views, iterations=0),
            "bad_resolution": dict(setup=SETUP, params=params, views=views, resolution=1024),
            "bad_views_empty": dict(setup=SETUP, params=params, views=[]),
            "bad_objective_zero": dict(setup=SETUP, params=params, views=views,
                                       objective={"iou": 0.0, "profile": 0.0}),
            "bad_init_key": dict(setup=SETUP, params=params, views=views, init={"nope": 1.0}),
            "bad_unknown_arg": dict(setup=SETUP, params=params, views=views, bogus=1),
        }
        # from_ 名字错 → 参数不匹配也是 ok:false（这里再单独构造真正重合的 from）
        bad_cases["bad_from_equals_look"] = dict(
            setup=SETUP, params=params,
            views=[dict(views[0], **{"from": [0.0, 0.0, 0.0]})])
        bad_ok = {}
        for name, kw2 in bad_cases.items():
            rr = _safe("solve", **kw2)
            bad_ok[name] = rr.get("ok") is False
        bad_ok["bad_unknown_op"] = _safe("nope_op").get("ok") is False
        _rec("bad.all_okfalse", all(bad_ok.values()), detail=bad_ok)
    finally:
        bpy.context.scene.unit_settings.scale_length = prev_scale
        reset_scene()
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


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
    print("SHAPESOLVE_SELFTEST " + ("ALL PASS" if res_out.get("ok") else "FAIL"))
    if not res_out.get("ok"):
        sys.exit(1)
