# -*- coding: utf-8 -*-
"""DSH 评测基座（evalharness）v1 —— P2-B：把"插件到底抓不抓得住缺陷"变成一条可复跑命令。

为什么有它：calib 只标定 gate；assembly/scan 与 imgtools 的缺陷-检出能力没有统一回放入口。
本模块做三件事（**全部真机、全部可在无头里跑**）：
  1) 合成缺陷注入（assembly suite）：D1 浅穿模 / D3 完全包含 / D4 12mm 设计内隐藏交叠（allow）/
     D5 面贴 / D7 浮块 / D8 反法线 → 调 K.dsh_assembly_api("scan"/"gate") → 逐例 caught/missed；
  2) 合成参考图回放（reference suite）：矩形/圆环/斜线 PNG → K.dsh_img_api("digest"/"probe"/"measure")
     → 断言 bbox / px_per_mm / 可读性 / needs_scale；
  3) 门基线（core suite）：K.dsh_calib_api("run") → capture_rate / baseline_pass。
输出 verdict 三态 + 每套件 cases + capture_rate / missed / false_positive / baseline_pass +
out_json（路径 + md5）+ ms。**缺模块就 skipped + degraded，不假装通过。**

纪律：
  * 只碰 __eval_ 前缀的测试对象；结束（无论成败）清理；场景单位用完复位；
  * 用户场景里已有对象**不动**（scan/gate 只传测试对象名；calib 走它自己的 __dsh_calib_ 前缀）；
  * 坏参数 ok:false；依赖缺失/套件崩 = skipped/degraded/fail，绝不静默当 pass；
  * 纯 bpy + numpy（不引 PIL/scipy/cv2），参考图用 bpy image 写 PNG。

跑法：
    blender_rt_headless(preload="evalharness,assembly,audit,contract,clearance,gate,imgtools,calib,mesh_fix",
        engine="none", script_file="tests/evalharness_selftest.py")
"""
import json
import math
import os
import sys
import tempfile
import time

import bpy
import numpy as np

EVALHARNESS_VERSION = 1
PFX = "__eval_"
SUITES = ("core", "assembly", "reference")
# 每个套件的**硬依赖**（缺 → 该套件 skipped）；assembly/audit 等为参考项（有则用更严通路）
SUITE_DEPS = {
    "core": {"required": ("calib",), "optional": ("gate", "audit", "printcheck", "contract")},
    "assembly": {"required": ("assembly",), "optional": ("audit", "contract", "clearance", "gate")},
    "reference": {"required": ("imgtools",), "optional": ()},
}
MODULE_ATTR = {"assembly": "dsh_assembly_api", "audit": "dsh_audit_api", "calib": "dsh_calib_api",
               "imgtools": "dsh_img_api", "gate": "dsh_gate_api", "contract": "dsh_contract_api",
               "clearance": "dsh_clearance_api", "mesh_fix": "dsh_fix_api",
               "printcheck": "dsh_print_api", "txn": "dsh_txn_api", "shapesolve": "dsh_shapesolve_api"}

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("evalharness.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")

_j = _KIT.j


# ────────────────────────────── 基础 helper ──────────────────────────────
def _kernel():
    return sys.modules.get("dsh_rt_kernel")


def _api(name):
    K = _kernel()
    if K is None:
        return None
    return getattr(K, MODULE_ATTR.get(name, "dsh_%s_api" % name), None)


def _present():
    out = {}
    for name in sorted(set(list(MODULE_ATTR.keys()))):
        out[name] = _api(name) is not None
    return out


def _call(name, op, args=None):
    """调 K.dsh_<name>_api 的子 op → (dict|None, 错误)。"""
    api = _api(name)
    if api is None:
        return None, "K.%s 不存在（preload 里带 %s）" % (MODULE_ATTR.get(name, name), name)
    try:
        out = api(str(op), dict(args or {}))
    except Exception as e:
        return None, "K.%s.%s 调用失败: %s: %s" % (name, op, type(e).__name__, str(e)[:200])
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except Exception:
            return None, "K.%s.%s 返回不是 JSON" % (name, op)
    if not isinstance(out, dict):
        return None, "K.%s.%s 返回类型异常: %s" % (name, op, type(out).__name__)
    return out, None


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def _r(x, n=4):
    try:
        return round(float(x), int(n))
    except Exception:
        return None


def _win(p):
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


def _sha256_file(path):
    import hashlib
    try:
        h = hashlib.sha256()
        with open(_win(path), "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
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


# ────────────────────────────── 场景 / PNG 工具 ──────────────────────────────
def _purge():
    """删掉所有 __eval_ 对象/网格（幂等；只碰自己的前缀）。"""
    removed = []
    for ob in [o for o in bpy.data.objects if str(o.name).startswith(PFX)]:
        try:
            nm = str(ob.name)
            me = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            removed.append(nm)
            if me is not None and me.users == 0:
                try:
                    bpy.data.meshes.remove(me, do_unlink=True)
                except Exception:
                    pass
        except Exception:
            pass
    for me in [m for m in bpy.data.meshes if str(m.name).startswith(PFX)]:
        try:
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass
    return removed


def _cube(name, size, loc=(0.0, 0.0, 0.0), flip=False):
    """造一个 __eval_ 前缀的立方体（size 单位 = 场景单位；调用方保证 mm 口径）。"""
    import bmesh
    nm = str(name)
    if not nm.startswith(PFX):
        nm = PFX + nm
    me = bpy.data.meshes.new(nm)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co *= float(size)
    if flip:
        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(nm, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = tuple(float(x) for x in loc)
    return ob.name


def _update():
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def _write_png(path, rgb_topdown, alpha_topdown=None):
    """top-down 的 RGB(A) numpy → PNG（Blender 像素底→上，写入前翻转）。"""
    h, w = int(rgb_topdown.shape[0]), int(rgb_topdown.shape[1])
    img = bpy.data.images.new(os.path.basename(str(path)), width=w, height=h, alpha=True)
    arr = np.ones((h, w, 4), dtype=np.float32)
    rgb = np.asarray(rgb_topdown, dtype=np.float32)
    if rgb.ndim == 2:
        rgb = np.stack([rgb, rgb, rgb], axis=-1)
    arr[..., 0:3] = np.clip(rgb[::-1], 0.0, 1.0)
    if alpha_topdown is not None:
        arr[..., 3] = np.clip(np.asarray(alpha_topdown, dtype=np.float32)[::-1], 0.0, 1.0)
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
    return _win(path)
# ────────────────────────────── assembly suite：合成缺陷注入 ──────────────────────────────
def _pair_key(a, b):
    x, y = str(a), str(b)
    return (x, y) if x <= y else (y, x)


def _pairs_by_key(scan):
    out = {}
    for p in scan.get("pairs") or []:
        if isinstance(p, dict):
            out[_pair_key(p.get("a"), p.get("b"))] = p
    return out


def _floater_objects(scan):
    names = set()
    for f in scan.get("floaters") or []:
        if not isinstance(f, dict):
            continue
        if f.get("object"):
            names.add(str(f.get("object")))
        for o in f.get("objects") or []:
            names.add(str(o))
    return names


def _gate_state(gate, gid):
    for g in (gate or {}).get("gates") or []:
        if isinstance(g, dict) and str(g.get("id")) == str(gid):
            return g
    return {}


def _suite_deps(suite, present):
    req = list(SUITE_DEPS[suite]["required"])
    opt = list(SUITE_DEPS[suite]["optional"])
    return {"required": req, "optional": opt,
            "present": [x for x in req + opt if present.get(x)],
            "missing": [x for x in req if not present.get(x)],
            "optional_missing": [x for x in opt if not present.get(x)]}


def _run_assembly(min_capture=0.8):
    """assembly suite：D1 浅穿模 / D3 完全包含 / D4 12mm allow / D5 面贴 / D7 浮块 / D8 反法线。"""
    t0 = time.perf_counter()
    present = _present()
    res = {"id": "assembly", "state": "skipped", "dependencies": _suite_deps("assembly", present),
           "cases": [], "capture_rate": None, "missed": [], "false_positive": 0,
           "baseline_pass": None, "warnings": [], "errors": [], "evidence": {}}
    if not present.get("assembly"):
        res["warnings"].append("K.dsh_assembly_api 不存在 —— assembly suite skipped（未安装/未 preload）")
        res["ms"] = int((time.perf_counter() - t0) * 1000)
        return res
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001     # 1 单位 = 1 mm
        _purge()
        names = []
        names.append(_cube("D1_a", 20.0, (0.0, 0.0, 0.0)))
        names.append(_cube("D1_b", 20.0, (19.0, 0.0, 0.0)))           # 浅穿模 1mm
        names.append(_cube("D3_big", 40.0, (100.0, 0.0, 0.0)))
        names.append(_cube("D3_small", 6.0, (100.0, 0.0, 0.0)))       # 完全包含（面无相交）
        names.append(_cube("D4_a", 40.0, (0.0, 100.0, 0.0)))
        names.append(_cube("D4_b", 40.0, (0.0, 100.0, 28.0)))         # z 向 12mm 设计内交叠
        names.append(_cube("D5_a", 20.0, (0.0, 200.0, 0.0)))
        names.append(_cube("D5_b", 20.0, (20.0, 200.0, 0.0)))         # 面贴 gap=0
        names.append(_cube("D7_float", 6.0, (240.0, 0.0, 0.0)))       # 浮块
        names.append(_cube("D8_inv", 20.0, (300.0, 300.0, 0.0), flip=True))  # 法线朝内
        _update()
        spec = {"allow": [{"id": "ALLOW_D4", "a": PFX + "D4_a", "b": PFX + "D4_b",
                           "max_depth_mm": 13.0, "max_volume_mm3": 25000.0,
                           "reason": "evalharness：12mm 设计内隐藏交叠（留 1mm 采样容差）"}]}
        scan, serr = _call("assembly", "scan",
                           {"objects": list(names), "spec": spec, "summary_only": False,
                            "max_pairs": 500, "report_gap_mm": 5.0})
        if serr:
            res["errors"].append("assembly.scan: %s" % serr)
        gate, gerr = _call("assembly", "gate",
                           {"objects": list(names), "spec": spec, "summary_only": True, "strict": True})
        if gerr:
            res["errors"].append("assembly.gate: %s" % gerr)
        pairs = _pairs_by_key(scan or {})
        floats = _floater_objects(scan or {})
        norm_gate = _gate_state(gate, "normals")
        inverted = [str(x) for x in (norm_gate.get("inverted") or [])]
        offenders = set()
        for p in (scan or {}).get("offenders") or []:
            if isinstance(p, dict):
                offenders.add(_pair_key(p.get("a"), p.get("b")))
        cases = []
        unverified = []

        def _pair_case(cid, a, b, expect, allowed=False):
            rec = pairs.get(_pair_key(a, b), {})
            got = rec.get("class") if rec else "missing"
            ok = got == expect
            if expect != "missing" and not rec and not (scan or {}).get("ok"):
                unverified.append(cid)
                ok = False
            cases.append({"id": cid, "expect": expect, "got": got, "ok": bool(ok),
                          "declared": bool(rec.get("declared")),
                          "depth_mm": rec.get("depth_mm"), "volume_mm3": rec.get("volume_mm3")})
            if allowed and _pair_key(a, b) in offenders:
                res["false_positive"] += 1
                cases[-1]["false_positive"] = True
            return ok

        _pair_case("D1_shallow_clash", PFX + "D1_a", PFX + "D1_b", "clash")
        _pair_case("D3_contained", PFX + "D3_big", PFX + "D3_small", "contained")
        _pair_case("D4_hidden_ok_allowed", PFX + "D4_a", PFX + "D4_b", "hidden_ok", allowed=True)
        _pair_case("D5_face_contact", PFX + "D5_a", PFX + "D5_b", "contact")
        d7 = PFX + "D7_float"
        got7 = "float" if d7 in floats else ("unresolved" if not floats else "missing")
        cases.append({"id": "D7_floater", "expect": "float", "got": got7, "ok": got7 == "float"})
        if not present.get("audit") and got7 != "float":
            unverified.append("D7_floater")
        d8 = PFX + "D8_inv"
        got8 = str(norm_gate.get("state") or "missing")
        d8_ok = bool(got8 == "fail" and d8 in inverted)
        cases.append({"id": "D8_inverted_normals", "expect": "gate_normals_fail", "got": got8,
                      "ok": d8_ok, "inverted": inverted[:6]})
        if not present.get("audit") and not d8_ok:
            unverified.append("D8_inverted_normals")
        # 干净基线：两件面贴 + manifest 接口 → gate 必须 pass（防"永远 fail"的假检出）
        try:
            _purge()
            ca = _cube("clean_a", 20.0, (0.0, 0.0, 0.0))
            cb = _cube("clean_b", 20.0, (20.0, 0.0, 0.0))
            _update()
            man = {"schema": "dsh-assembly-manifest/1", "units": "mm", "assembly": "eval-clean",
                   "parts": [{"id": ca, "objects": [ca]}, {"id": cb, "objects": [cb]}],
                   "interfaces": [{"id": "IF-clean", "a": ca, "b": cb, "fit": "location",
                                   "nominal_mm": 0.0, "tol_mm": 0.5}],
                   "allow_pairs": []}
            g2, g2err = _call("assembly", "gate",
                              {"objects": [ca, cb], "manifest": man, "summary_only": True, "strict": True})
            if g2err:
                res["errors"].append("baseline gate: %s" % g2err)
            res["baseline_pass"] = bool((g2 or {}).get("verdict") == "pass"
                                        and (g2 or {}).get("export_allowed") is True)
            res["evidence"]["baseline_gate"] = {"verdict": (g2 or {}).get("verdict"),
                                                "export_allowed": (g2 or {}).get("export_allowed")}
        finally:
            _purge()
        caught = [c for c in cases if c.get("ok") is True]
        res["cases"] = cases
        res["capture_rate"] = _r(len(caught) / float(len(cases)), 4) if cases else None
        res["missed"] = [c["id"] for c in cases if c.get("ok") is not True]
        res["evidence"].update({
            "scan_ok": bool((scan or {}).get("ok")),
            "scan_verdict": (scan or {}).get("verdict"),
            "scan_counts": (scan or {}).get("counts"),
            "scan_coverage": {k: (scan or {}).get("coverage", {}).get(k)
                              for k in ("complete", "truncated", "budget_hit")},
            "floaters": sorted(floats)[:10],
            "gate_verdict": (gate or {}).get("verdict"),
            "gate_export_allowed": (gate or {}).get("export_allowed"),
            "gate_normals": {"state": norm_gate.get("state"), "inverted": inverted[:6]},
            "offenders": sorted(["%s|%s" % k for k in offenders])[:10],
        })
        scan_ok = bool((scan or {}).get("ok")) and (scan or {}).get("verdict") == "fail"
        gate_ok = bool((gate or {}).get("verdict") in ("fail", "degraded")
                       and (gate or {}).get("export_allowed") is False)
        rate = res["capture_rate"] or 0.0
        if res["errors"]:
            res["state"] = "fail"
        elif unverified:
            res["state"] = "degraded"
            res["warnings"].append("依赖缺失导致 %d 类无法确证：%s（%s）"
                                   % (len(unverified), unverified, res["dependencies"]["optional_missing"]))
        elif rate < float(min_capture) or res["false_positive"] or res["baseline_pass"] is not True                 or not scan_ok or not gate_ok:
            res["state"] = "fail"
        else:
            res["state"] = "pass"
        if not scan_ok:
            res["warnings"].append("scan 没有给 verdict=fail（%r）—— 缺陷注入的预期结论没出现"
                                   % (scan or {}).get("verdict"))
        if not gate_ok:
            res["warnings"].append("gate 没有拒绝（verdict=%r export_allowed=%r）"
                                   % ((gate or {}).get("verdict"), (gate or {}).get("export_allowed")))
    except Exception as e:
        import traceback
        res["state"] = "fail"
        res["errors"].append("%s: %s" % (type(e).__name__, str(e)[:240]))
        res["traceback"] = traceback.format_exc()[-800:]
    finally:
        try:
            bpy.context.scene.unit_settings.scale_length = prev_scale
        except Exception:
            pass
        _purge()
    res["ms"] = int((time.perf_counter() - t0) * 1000)
    return res
# ────────────────────────────── reference suite：合成参考图回放 ──────────────────────────────
def _gen_ref_images(tmp):
    """造三张合成参考图：矩形（已知尺寸+alpha）、圆环（alpha）、斜线（不透明灰度→Otsu）。"""
    res = 512
    yy, xx = np.mgrid[0:res, 0:res]
    rect = np.zeros((res, res), dtype=bool)
    rect[100:250, 80:380] = True
    rgb_rect = np.where(rect, 0.10, 1.0)
    p_rect = _write_png(os.path.join(tmp, "eval_rect.png"), rgb_rect, rect.astype(np.float32))
    cy = cx = 256.0
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    ring = (d <= 100.0) & (d >= 50.0)
    rgb_ring = np.where(ring, 0.10, 1.0)
    p_ring = _write_png(os.path.join(tmp, "eval_ring.png"), rgb_ring, ring.astype(np.float32))
    s45 = math.sqrt(0.5)
    u = (xx - cx) * s45 + (yy - cy) * s45
    v = -(xx - cx) * s45 + (yy - cy) * s45
    line = (np.abs(u) <= 120.0) & (np.abs(v) <= 8.0)
    rgb_line = np.where(line, 0.10, 0.90)          # 不透明：走 Otsu（暗物亮底）
    p_line = _write_png(os.path.join(tmp, "eval_slant.png"), rgb_line, None)
    return {"rect": p_rect, "ring": p_ring, "slant": p_line,
            "rect_bbox": [80, 100, 300, 150], "ring_diameter_px": 100.0}


def _run_reference(min_capture=0.8, keep=False):
    """reference suite：imgtools digest/probe/measure 对合成图（矩形/圆环/斜线）的断言。"""
    t0 = time.perf_counter()
    present = _present()
    res = {"id": "reference", "state": "skipped", "dependencies": _suite_deps("reference", present),
           "cases": [], "capture_rate": None, "missed": [], "false_positive": 0,
           "baseline_pass": None, "warnings": [], "errors": [], "evidence": {}}
    if not present.get("imgtools"):
        res["warnings"].append("K.dsh_img_api 不存在 —— reference suite skipped（未安装/未 preload）")
        res["ms"] = int((time.perf_counter() - t0) * 1000)
        return res
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix="dsh_evalharness_ref_")
        paths = _gen_ref_images(tmp)
        res["evidence"]["tmp_dir"] = tmp
        cases = []

        def _case(cid, ok, expect=None, got=None, **extra):
            row = {"id": cid, "ok": bool(ok), "expect": expect, "got": got}
            row.update(extra)
            cases.append(row)
            return bool(ok)

        # ① 矩形 + 已知长度 → digest：bbox / px_per_mm / verdict=ready_for_spec
        dig, derr = _call("imgtools", "digest",
                          {"path": paths["rect"], "views": ["front", "side"],
                           "known_len": {"mm": 100.0, "p1_px": [80, 175], "p2_px": [380, 175]}})
        if derr:
            res["errors"].append("img.digest(rect): %s" % derr)
        dig = dig or {}
        bbox = ((dig.get("silhouette") or {}).get("bbox_px") or [])
        want_bbox = paths["rect_bbox"]
        bbox_ok = (len(bbox) >= 4 and all(abs(float(bbox[i]) - float(want_bbox[i])) <= 3.0
                                          for i in range(4)))
        ppm = _num((dig.get("scale") or {}).get("px_per_mm"))
        ppm_ok = ppm is not None and abs(ppm - 3.0) / 3.0 <= 0.01
        _case("digest_rect_ok", dig.get("ok") is True and dig.get("verdict") == "ready_for_spec",
              "ok+ready_for_spec", dig.get("verdict"), verdict=dig.get("verdict"))
        _case("digest_rect_bbox", bbox_ok, want_bbox, bbox)
        _case("digest_rect_px_per_mm", ppm_ok, 3.0, ppm)
        # ② probe：水平线宽 / 垂直线高（像素）
        pr_y, _e = _call("imgtools", "probe", {"path": paths["rect"], "op": "scan_y", "y_px": 175})
        pr_y = pr_y or {}
        wpx = _num(pr_y.get("width_px"))
        _case("probe_scan_y_width", (pr_y.get("ok") is True and wpx is not None
                                     and abs(wpx - 300.0) <= 4.0), 300.0, wpx)
        pr_x, _e = _call("imgtools", "probe", {"path": paths["rect"], "op": "scan_x", "x_px": 230})
        pr_x = pr_x or {}
        hpx = _num(pr_x.get("height_px"))
        _case("probe_scan_x_height", (pr_x.get("ok") is True and hpx is not None
                                      and abs(hpx - 150.0) <= 4.0), 150.0, hpx)
        # ③ measure：像素→mm 与期望比
        meas, _e = _call("imgtools", "measure",
                         {"path": paths["rect"],
                          "targets": [{"key": "width", "p1_px": [80, 175], "p2_px": [380, 175],
                                       "expect_mm": 100.0, "tol_pct": 0.02}]})
        meas = meas or {}
        rows = meas.get("rows") or []
        row0 = rows[0] if rows else {}
        vm = _num(row0.get("value_mm"))
        _case("measure_width_ok", (meas.get("ok") is True and row0.get("verdict") == "pass"
                                   and vm is not None and abs(vm - 100.0) <= 1.0),
              100.0, vm, verdict=row0.get("verdict"))
        # ④ 圆环：probe circle 直径
        dig_r, rerr = _call("imgtools", "digest", {"path": paths["ring"], "views": ["front", "side"]})
        if rerr:
            res["errors"].append("img.digest(ring): %s" % rerr)
        dig_r = dig_r or {}
        circ, _e = _call("imgtools", "probe",
                         {"path": paths["ring"], "op": "circle", "x_px": 256, "y_px": 256})
        circ = circ or {}
        c = circ.get("circle") or {}
        dpx = _num(c.get("diameter_px"))
        _case("digest_ring_ok", dig_r.get("ok") is True, True, dig_r.get("verdict"),
              verdict=dig_r.get("verdict"))
        _case("probe_circle_diameter", (circ.get("ok") is True and dpx is not None
                                        and abs(dpx - 100.0) <= 6.0
                                        and c.get("is_circle") is True), 100.0, dpx)
        # ⑤ 斜线：主方向 45°（模 180 距离）、细长
        dig_l, lerr = _call("imgtools", "digest", {"path": paths["slant"]})
        if lerr:
            res["errors"].append("img.digest(slant): %s" % lerr)
        dig_l = dig_l or {}
        axis = _num((dig_l.get("silhouette") or {}).get("principal_axis_deg"))
        aspect = _num((dig_l.get("silhouette") or {}).get("aspect_wh"))
        fill = _num((dig_l.get("silhouette") or {}).get("fill_ratio"))
        axis_ok = axis is not None and abs(((float(axis) - 45.0 + 90.0) % 180.0) - 90.0) <= 6.0
        _case("digest_slant_axis", dig_l.get("ok") is True and axis_ok, 45.0, axis,
              aspect=aspect)
        # 45° 斜线的 bbox 是正方形（aspect≈1），细长要看 bbox 填充率
        _case("digest_slant_thin", fill is not None and fill < 0.5, "<0.5", fill, aspect=aspect)
        # ⑥ needs_scale 逻辑：未标定不放行 mm
        _case("digest_uncalibrated_needs_scale", dig_l.get("verdict") == "needs_scale",
              "needs_scale", dig_l.get("verdict"))
        meas2, _e = _call("imgtools", "measure",
                          {"path": paths["slant"], "targets": [{"key": "w", "px": 200.0}]})
        _case("measure_needs_scale_okfalse", (meas2 or {}).get("ok") is False
              and (meas2 or {}).get("verdict") == "needs_scale",
              "ok=false+needs_scale", (meas2 or {}).get("verdict"))
        # readble 断言：检出特征若 >=8px 必须标 readable；unreadable 行必须为 0
        fps = 0
        feats = list(dig_r.get("features") or []) + list(dig.get("features") or [])
        for f in feats:
            if not isinstance(f, dict):
                continue
            size = _num(f.get("size_px")) or 0.0
            if size >= 8.0 and f.get("readable") is not True:
                fps += 1
        # unreadable 里的 <8px 角点是**诚实的低读性报告**，不是误报；只有 >=8px 却标不可读才算
        res["false_positive"] = int(fps)
        res["evidence"]["readability"] = {
            "features": len(feats),
            "unreadable_lt8px": (len(dig.get("unreadable") or []) + len(dig_r.get("unreadable") or [])),
        }
        dig_ok = [c for c in cases if c.get("ok") is True]
        res["cases"] = cases
        res["capture_rate"] = _r(len(dig_ok) / float(len(cases)), 4) if cases else None
        res["missed"] = [c["id"] for c in cases if c.get("ok") is not True]
        res["baseline_pass"] = bool(
            dig.get("ok") is True and dig.get("verdict") == "ready_for_spec"
            and bbox_ok and ppm_ok and meas.get("ok") is True
            and dig_l.get("verdict") == "needs_scale" and (meas2 or {}).get("ok") is False)
        res["evidence"].update({
            "rect": {"bbox_px": bbox, "want_bbox_px": want_bbox, "px_per_mm": ppm,
                     "verdict": dig.get("verdict")},
            "probe": {"scan_y_width_px": wpx, "scan_x_height_px": hpx},
            "measure": {"value_mm": vm, "verdict": row0.get("verdict")},
            "ring": {"circle_diameter_px": dpx, "digest_verdict": dig_r.get("verdict")},
            "slant": {"principal_axis_deg": axis, "aspect_wh": aspect,
                      "verdict": dig_l.get("verdict")},
        })
        rate = res["capture_rate"] or 0.0
        if res["errors"]:
            res["state"] = "fail"
        elif rate < float(min_capture) or res["false_positive"] or res["baseline_pass"] is not True:
            res["state"] = "fail"
        else:
            res["state"] = "pass"
    except Exception as e:
        import traceback
        res["state"] = "fail"
        res["errors"].append("%s: %s" % (type(e).__name__, str(e)[:240]))
        res["traceback"] = traceback.format_exc()[-800:]
    finally:
        if tmp and not keep:
            try:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass
    if keep and tmp:
        res["warnings"].append("keep=true：参考图临时目录保留在 %s（默认会删）" % tmp)
    res["ms"] = int((time.perf_counter() - t0) * 1000)
    return res


# ────────────────────────────── core suite：门基线（calib） ──────────────────────────────
def _run_core(min_capture=0.8):
    t0 = time.perf_counter()
    present = _present()
    res = {"id": "core", "state": "skipped", "dependencies": _suite_deps("core", present),
           "cases": [], "capture_rate": None, "missed": [], "false_positive": 0,
           "baseline_pass": None, "warnings": [], "errors": [], "evidence": {}}
    if not present.get("calib"):
        res["warnings"].append("K.dsh_calib_api 不存在 —— core suite skipped（未安装/未 preload）")
        res["ms"] = int((time.perf_counter() - t0) * 1000)
        return res
    r, err = _call("calib", "run", {})
    if err:
        res["state"] = "fail"
        res["errors"].append("calib.run: %s" % err)
        res["ms"] = int((time.perf_counter() - t0) * 1000)
        return res
    r = r or {}
    res["capture_rate"] = _r(r.get("capture_rate"), 4) if r.get("capture_rate") is not None else None
    res["baseline_pass"] = bool(r.get("baseline_pass")) if r.get("baseline_pass") is not None else None
    res["missed"] = [str(x) for x in (r.get("missed") or [])]
    cases = [{"id": "baseline_gate", "expect": True, "got": res["baseline_pass"],
              "ok": res["baseline_pass"] is True}]
    for row in r.get("defects") or []:
        if isinstance(row, dict):
            cases.append({"id": str(row.get("id")), "expect": row.get("expect"),
                          "got": bool(row.get("caught")), "ok": row.get("caught") is True,
                          "caught_by": row.get("caught_by"),
                          "why_missed": row.get("why_missed")})
    res["cases"] = cases
    res["evidence"] = {"calib_ok": bool(r.get("ok")), "unit_ok": r.get("unit_ok"),
                       "valid_run": r.get("valid_run"), "defects": r.get("total"),
                       "caught": r.get("caught"),
                       "gate_errors": (r.get("gate_errors") or [])[:6],
                       "warnings": (r.get("warnings") or [])[:6]}
    miss_opt = res["dependencies"]["optional_missing"]
    rate = res["capture_rate"] or 0.0
    if miss_opt:
        res["state"] = "degraded"
        res["warnings"].append("core 依赖不全（%s）⇒ calib 基线可能不可信；只报数字不判通过" % miss_opt)
    elif res["baseline_pass"] is not True:
        res["state"] = "fail"
        res["warnings"].append("健康基线没过 ⇒ 本次标定无效（门在误报/门配置错/场景不干净）")
    elif rate < float(min_capture):
        res["state"] = "fail"
    elif r.get("ok") is not True:
        res["state"] = "degraded"
        res["warnings"].append("calib ok=false（看 gate_errors/missed），不当作通过")
    else:
        res["state"] = "pass"
    res["ms"] = int((time.perf_counter() - t0) * 1000)
    return res
# ────────────────────────────── op: run ──────────────────────────────
SUITE_RUNNERS = {"core": _run_core, "assembly": _run_assembly, "reference": _run_reference}
SUITE_ORDER = ("core", "assembly", "reference")


def _norm_suites(suite):
    if suite is None:
        return list(SUITE_ORDER), None
    raw = suite
    if isinstance(raw, (list, tuple, set)):
        items = [str(x) for x in raw]
    else:
        s = str(raw).strip().lower()
        if s in ("", "all", "*"):
            return list(SUITE_ORDER), None
        items = [x for x in s.replace("+", ",").replace(" ", ",").split(",") if x]
    items = [x.strip().lower() for x in items if str(x).strip()]
    bad = [x for x in items if x not in SUITE_RUNNERS]
    if bad:
        return None, "未知 suite: %s（支持 %s 或 all）" % (bad, list(SUITE_ORDER))
    if not items:
        return None, "suite 为空（支持 %s 或 all）" % list(SUITE_ORDER)
    dedup = []
    for x in SUITE_ORDER:
        if x in items:
            dedup.append(x)
    return dedup, None


def _aggregate(suites):
    states = [s.get("state") for s in suites]
    if any(st == "fail" for st in states):
        verdict = "fail"
    elif any(st in ("degraded", "skipped") for st in states):
        verdict = "degraded"
    elif states and all(st == "pass" for st in states):
        verdict = "pass"
    else:
        verdict = "degraded"
    caught = sum(1 for s in suites for c in (s.get("cases") or []) if c.get("ok") is True)
    total = sum(1 for s in suites for c in (s.get("cases") or []))
    missed = []
    for s in suites:
        for c in s.get("cases") or []:
            if c.get("ok") is not True:
                missed.append("%s:%s" % (s.get("id"), c.get("id")))
    fps = sum(int(s.get("false_positive") or 0) for s in suites)
    baselines = [s.get("baseline_pass") for s in suites if s.get("baseline_pass") is not None]
    baseline = None
    if baselines:
        baseline = all(bool(x) for x in baselines)
    return {"verdict": verdict,
            "capture_rate": _r(caught / float(total), 4) if total else None,
            "caught": caught, "total": total, "missed": missed, "false_positive": fps,
            "baseline_pass": baseline}


def evalharness_run(suite="all", out_json=None, keep=False, min_capture=0.8):
    """跑评测套件（core|assembly|reference|all），输出 verdict/套件明细/捕获率/JSON 落盘。

    只读用户场景：测试对象全部 __eval_ 前缀，结束清理；场景单位用完复位。
    """
    t0 = time.perf_counter()
    suites_req, serr = _norm_suites(suite)
    if serr:
        return _err(serr, stage="validate", allowed=list(SUITE_ORDER) + ["all"])
    mc = _num(min_capture)
    if mc is None or mc < 0.0 or mc > 1.0:
        return _err("min_capture 必须是 0..1 的数字（默认 0.8）", stage="validate")
    present = _present()
    user_before = set(str(o.name) for o in bpy.data.objects if not str(o.name).startswith(PFX))
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    suites = []
    kept_dirs = []
    try:
        for sid in suites_req:
            runner = SUITE_RUNNERS[sid]
            try:
                if sid == "reference":
                    r = runner(float(mc), bool(keep))
                else:
                    r = runner(float(mc))
            except Exception as e:
                import traceback
                r = {"id": sid, "state": "fail", "cases": [], "capture_rate": None,
                     "missed": [], "false_positive": 0, "baseline_pass": None,
                     "warnings": [], "errors": ["%s: %s" % (type(e).__name__, str(e)[:240])],
                     "traceback": traceback.format_exc()[-600:],
                     "dependencies": _suite_deps(sid, present), "ms": None}
            suites.append(r)
            if keep and sid == "reference":
                d = (r.get("evidence") or {}).get("tmp_dir")
                if d:
                    kept_dirs.append(d)
    finally:
        try:
            bpy.context.scene.unit_settings.scale_length = prev_scale
        except Exception:
            pass
        _purge()
    leftovers = [str(o.name) for o in bpy.data.objects if str(o.name).startswith(PFX)]
    user_after = set(str(o.name) for o in bpy.data.objects if not str(o.name).startswith(PFX))
    agg = _aggregate(suites)
    body = {"ok": True, "version": EVALHARNESS_VERSION, "suite": list(suites_req),
            "verdict": agg["verdict"],
            "capture_rate": agg["capture_rate"], "caught": agg["caught"], "total": agg["total"],
            "missed": agg["missed"], "false_positive": agg["false_positive"],
            "baseline_pass": agg["baseline_pass"],
            "suites": suites,
            "modules": present,
            "modules_missing": [k for k, v in present.items() if not v],
            "min_capture": _r(mc, 4),
            "out_json": None,
            "kept_dirs": kept_dirs if keep else [],
            "scene": {"user_objects_before": len(user_before), "user_objects_after": len(user_after),
                      "user_scene_unchanged": bool(user_before == user_after),
                      "eval_leftovers": leftovers,
                      "scene_clean": bool(not leftovers and user_before == user_after)},
            "verdict_line": "评测 %s（capture_rate=%s missed=%d fp=%d baseline=%s）"
                            % (agg["verdict"], agg["capture_rate"], len(agg["missed"]),
                               agg["false_positive"], agg["baseline_pass"]),
            "note": ("degraded = 有 skipped/degraded 套件或依赖缺失，不是通过；"
                     "fail = 有套件断言失败/门误报基线不过。读 verdict 再读 per-suite cases。"),
            "honest_limits": [
                "capture_rate 是**本基座合成的缺陷集**上的检出率，不等于真实项目验收率",
                "assembly suite 的几何是 1 单位=1mm 合成件；真实模型的面数/拓扑差异会改变结果",
                "reference suite 判 bbox/px_per_mm/可读性；照片（透视/光照/弱对比）不在覆盖内",
                "core suite 依赖 calib 的 8 类缺陷集；缺 printcheck 等门依赖时只报 degraded 数字",
                "用户场景只保证不被本基座修改（测试对象 __eval_ 前缀，结束清理并复位单位）",
            ]}
    if out_json:
        try:
            p = _win(out_json)
            d = os.path.dirname(p)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            payload = dict(body)
            payload["scene"] = body["scene"]
            payload["out_json"] = {"path": p, "md5": None}
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            md5 = _sha256_file(p)
            body["out_json"] = {"path": p, "md5": md5,
                                "bytes": (os.path.getsize(p) if os.path.isfile(p) else None)}
        except Exception as e:
            return _err("out_json 写盘失败: %s: %s" % (type(e).__name__, str(e)[:200]),
                        stage="out_json", verdict=agg["verdict"], suites=suites)
    return _j(_receipt(body, t0))


# ────────────────────────────── selftest / help ──────────────────────────────
def evalharness_selftest():
    """模块内快速自检：跑一遍 assembly suite（若装配层在）→ 断言捕获率/JSON/坏参数。"""
    ev = {}
    t0 = time.perf_counter()
    present = _present()
    try:
        if present.get("assembly"):
            r = json.loads(evalharness_run(suite="assembly"))
            ev["assembly_ok"] = bool(r.get("ok"))
            ev["assembly_capture_ge_08"] = (r.get("capture_rate") or 0.0) >= 0.8
            ev["json_serializable"] = bool(json.dumps(r, ensure_ascii=False))
            ev["scene_clean"] = bool((r.get("scene") or {}).get("scene_clean"))
            ev["bad_suite_okfalse"] = json.loads(evalharness_run(suite="nope")).get("ok") is False
            ev["bad_min_capture_okfalse"] = json.loads(
                evalharness_run(suite="assembly", min_capture=2.0)).get("ok") is False
        else:
            ev["assembly_skipped"] = True
        ok = all(v is True for v in ev.values())
        return _j({"ok": bool(ok), "version": EVALHARNESS_VERSION, "evidence": ev,
                   "present": present, "ms": int((time.perf_counter() - t0) * 1000)})
    except Exception as e:
        import traceback
        return _j({"ok": False, "version": EVALHARNESS_VERSION, "evidence": ev,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "traceback": traceback.format_exc()[-600:]})


def evalharness_help():
    return _j({
        "module": "evalharness.py", "version": EVALHARNESS_VERSION,
        "what": "评测基座：合成缺陷注入（assembly）+ 合成参考图回放（reference）+ 门基线（calib core）"
                "→ verdict/capture_rate/missed/false_positive/baseline_pass/out_json",
        "ops": {
            "run": {"sig": "suite='all'|core,assembly,reference（可逗号组合）, out_json?, keep=false, "
                           "min_capture=0.8",
                    "out": "verdict(pass|fail|degraded) + suites[] + capture_rate + missed + "
                           "false_positive + baseline_pass + out_json{path,md5} + ms",
                    "rules": "fail：任一 fail；degraded：任一 skipped/degraded 或依赖缺失；全部 pass ⇒ pass"},
            "selftest": {"sig": "无参", "out": "跑 assembly suite + 坏参数 ok:false"},
            "help": {"sig": "无参"},
        },
        "suite_coverage": {
            "assembly": "D1 浅穿模 / D3 完全包含 / D4 12mm allow / D5 面贴 / D7 浮块 / D8 反法线 + 干净基线",
            "reference": "矩形(bbox/px_per_mm/probe/measure) / 圆环(圆孔直径) / 斜线(主方向) + needs_scale",
            "core": "calib 8 类缺陷捕获率 + 健康基线（需要 calib[+gate/audit/printcheck]）",
        },
        "deps": {"required": {"core": "calib", "assembly": "assembly", "reference": "imgtools"},
                 "meaning": "缺 required ⇒ 该套件 skipped，整体 verdict 至少 degraded；"
                            "缺 optional ⇒ 部分用例无法确证，套件 degraded"},
        "protocol": [
            "无头干净场景跑最稳（assembly/calib 会临时建对象，结束清理）",
            "读 verdict 三态；capture_rate 是本基座合成集上的检出率，不是项目验收率",
            "out_json 落盘含 suites/cases 明细；md5 可用于跨会话对拍",
            "要对比两版插件：同 suite/同 seed 跑两次，比 capture_rate + missed + false_positive",
        ],
        "honest_limits": ["合成缺陷集不等于真实项目", "照片类参考图/透视不在 reference 覆盖内",
                          "core 依赖 calib 的门配置；缺门依赖时只给 degraded 数字",
                          "只碰 __eval_ 前缀对象；用户场景不动"],
    })


# 注册：模块名 evalharness（K.dsh_evalharness_api）+ 任务约定的入口别名 K.dsh_eval_api
_API = _KIT.register("evalharness", EVALHARNESS_VERSION, {
    "run": evalharness_run, "selftest": evalharness_selftest, "help": evalharness_help})
_KK = _kernel()
if _KK is not None:
    _KK.dsh_eval_api = _API
