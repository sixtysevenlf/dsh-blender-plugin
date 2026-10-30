# -*- coding: utf-8 -*-
"""tests/surface_selftest.py —— 曲面算法包（runtime/surface.py）真机自检。

跑法：
    blender_rt_headless(preload="surface,audit", engine="none", factory_startup=True,
        timeout_ms=300000, script_file="<包路径>/tests/surface_selftest.py")

覆盖：
  * M0：help/ops、spec 校验（坏 kind/单位/tessellation/矩形控制网格 → ok:false）；
  * M1：瓶身 loft / 翼子板 sweep / 头盔简化 patch 三基准生成（verts/tris/bbox/spec_hash/无 NaN）+ 确定性；
  * M2：三基准 validate=pass；自交负例 fail、薄壁 fail、厚壁 pass、G1 不连续 fail；
        开放曲面厚度为 n/a（不是 fail）；对象模式连续性只给参考值；
  * tessellate：spec→对象、对象→烘焙对象；
  * M5：blend/offset 必须 ok:false 且 error 含 OCCT（不假装实现）；
  * 场景安全：测试对象 __surface_eval_ 前缀，结束清理；用户对象不变。
"""
import json
import math
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
RT = os.path.normpath(os.path.join(HERE, "..", "runtime"))


def _ensure_kernel():
    import types
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    if getattr(K, "dsh_surface_api", None) is not None:
        return K
    for nm in ("kit.py", "surface.py"):
        p = os.path.join(RT, nm)
        with open(p, "r", encoding="utf-8") as fh:
            src = fh.read()
        exec(compile(src, p, "exec"), {"__name__": nm[:-3], "__file__": p})
    return sys.modules["dsh_rt_kernel"]


K = _ensure_kernel()
API = getattr(K, "dsh_surface_api", None)


def call(op, *args, **kw):
    if args and isinstance(args[0], dict):
        kw = dict(args[0], **kw)
    out = API(op, kw)
    return json.loads(out) if isinstance(out, str) else out


def _safe(op, *args, **kw):
    try:
        if API is None:
            return {"ok": False, "error": "K.dsh_surface_api 不存在"}
        return call(op, *args, **kw)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}


import bpy


EVID = {}
ASSERT = []
MADE = []


def _rec(key, cond, **nums):
    ASSERT.append(key)
    EVID[key] = bool(cond)
    for k, v in nums.items():
        EVID[key + "." + k] = v
    return bool(cond)


def _cleanup():
    removed = []
    for nm in MADE:
        ob = bpy.data.objects.get(str(nm))
        if ob is None:
            continue
        me = ob.data if ob.type == "MESH" else None
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            removed.append(str(nm))
        except Exception:
            pass
        if me is not None and me.users == 0:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
    return removed


def _circle(n, rx, ry=None):
    ry = rx if ry is None else ry
    return [[rx * math.cos(2.0 * math.pi * k / n), ry * math.sin(2.0 * math.pi * k / n)] for k in range(n)]


def _vertex_snapshot(ob_name, n=100):
    ob = bpy.data.objects.get(str(ob_name))
    if ob is None or ob.type != "MESH":
        return None
    return [tuple(round(float(c), 9) for c in v.co) for v in ob.data.vertices[:n]]


def _thin_box(name, sx, sy, sz):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= sx
        v.co.y *= sy
        v.co.z *= sz
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    MADE.append(name)
    return ob


def main():
    if API is None:
        return {"ok": False, "error": "K.dsh_surface_api 不存在（preload 带 surface）", "evidence": EVID}
    user_before = sorted(str(o.name) for o in bpy.data.objects
                         if not str(o.name).startswith("__surface_eval_"))
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001       # 1 单位 = 1 mm
        h = _safe("help", {})
        _rec("api.version", h.get("version") == 1, version=h.get("version"))
        need = {"loft", "sweep", "patch", "tessellate", "validate", "selftest", "help"}
        _rec("api.help_ops", need.issubset(set((h.get("ops") or {}).keys())),
             ops=sorted((h.get("ops") or {}).keys()))

        # ---- M1 三基准 ----
        sections = []
        for k in range(8):
            t = k / 7.0
            sections.append({"z_mm": 160.0 * t,
                             "points_mm": _circle(24, 30.0 + 8.0 * math.sin(math.pi * t))})
        bottle = _safe("loft", sections=sections, degree=3, continuity="G1", closed=True,
                       tessellation={"u": 32, "v": 24}, name="__surface_eval_bottle")
        if bottle.get("object"):
            MADE.append(bottle["object"])
        _rec("loft.ok", bottle.get("ok") is True, error=bottle.get("error"))
        _rec("loft.mesh", bottle.get("verts", 0) > 0 and bottle.get("tris", 0) > 0,
             verts=bottle.get("verts"), tris=bottle.get("tris"))
        _rec("loft.spec_hash_64", len(str(bottle.get("spec_hash") or "")) == 64)
        _rec("loft.bbox_z_160mm",
             abs(float(((bottle.get("bbox_mm") or {}).get("size_mm") or [0, 0, 0])[2]) - 160.0) <= 0.01,
             bbox_mm=(bottle.get("bbox_mm") or {}).get("size_mm"))

        fprof = []
        for k in range(24):
            ang = math.pi / 2.0 + 2.0 * math.pi * k / 24.0      # 接缝放顶部（低曲率）
            fprof.append([30.0 * math.cos(ang), 14.0 * math.sin(ang)])
        fender = _safe("sweep", profile_mm=fprof,
                       rail_mm=[[0, 0, 0], [30, 5, 2], [60, 12, 4], [90, 20, 6], [120, 28, 8]],
                       orientation="auto", twist_deg=20.0, scale=[1.2, 1.1, 1.0, 0.95, 0.9],
                       tessellation={"u": 32, "v": 32}, closed=True, name="__surface_eval_fender")
        if fender.get("object"):
            MADE.append(fender["object"])
        _rec("sweep.ok", fender.get("ok") is True and fender.get("tris", 0) > 0, error=fender.get("error"))

        grid = []
        for ii in range(4):
            row = []
            x = -40.0 + 80.0 * ii / 3.0
            for jj in range(4):
                y = -40.0 + 80.0 * jj / 3.0
                row.append([x, y, 55.0 - 20.0 * (x / 40.0) ** 2 - 15.0 * (y / 40.0) ** 2])
            grid.append(row)
        helmet = _safe("patch", control_grid=grid, degree_u=3, degree_v=3,
                       tessellation={"u": 28, "v": 28}, name="__surface_eval_helmet")
        if helmet.get("object"):
            MADE.append(helmet["object"])
        _rec("patch.ok", helmet.get("ok") is True and helmet.get("tris", 0) > 0, error=helmet.get("error"))

        # ---- M2 三基准 validate ----
        for key, r in (("bottle", bottle), ("fender", fender), ("helmet", helmet)):
            v = _safe("validate", spec_or_object=r.get("spec"))
            _rec("validate.%s_pass" % key, v.get("ok") is True and v.get("verdict") == "pass",
                 verdict=v.get("verdict"), defects=v.get("defects"), error=v.get("error"))
        vb = _safe("validate", spec_or_object=bottle.get("spec"))
        _rec("validate.open_thickness_na", ((vb.get("checks") or {}).get("thickness") or {}).get("state") == "n/a")
        _rec("validate.metrics_curvature", (vb.get("metrics") or {}).get("curvature") is not None)

        # ---- 确定性 ----
        bottle2 = _safe("loft", sections=sections, degree=3, continuity="G1", closed=True,
                        tessellation={"u": 32, "v": 24}, name="__surface_eval_bottle2")
        if bottle2.get("object"):
            MADE.append(bottle2["object"])
        _rec("deterministic.spec_hash", bottle.get("spec_hash") == bottle2.get("spec_hash"))
        _rec("deterministic.verts",
             _vertex_snapshot(bottle.get("object")) == _vertex_snapshot(bottle2.get("object")))

        # ---- M2 负例：自交 ----
        rail_u = [[10.0 * math.sin(math.radians(a)), 10.0 * math.cos(math.radians(a)), 0.0]
                  for a in (-90, -60, -30, 0, 30, 60, 90)]
        bad = _safe("sweep", profile_mm=_circle(18, 16.0), rail_mm=rail_u,
                    tessellation={"u": 20, "v": 28}, closed=True, name="__surface_eval_selfint")
        if bad.get("object"):
            MADE.append(bad["object"])
        vbad = _safe("validate", spec_or_object=bad.get("object"), thickness=False)
        _rec("fail.self_intersection", vbad.get("verdict") == "fail"
             and any(d.get("check") == "self_intersection" for d in vbad.get("defects") or []),
             verdict=vbad.get("verdict"))

        # ---- M2 负例：薄壁 / 正例：厚壁 ----
        _thin_box("__surface_eval_thin", 100.0, 100.0, 0.5)
        vthin = _safe("validate", spec_or_object="__surface_eval_thin", min_thickness_mm=0.8,
                      continuity=False, curvature=False)
        _rec("fail.thin_wall", vthin.get("verdict") == "fail"
             and any(d.get("check") == "thickness" for d in vthin.get("defects") or []),
             verdict=vthin.get("verdict"), thickness=(vthin.get("metrics") or {}).get("thickness"))
        _thin_box("__surface_eval_thick", 40.0, 40.0, 20.0)
        vthick = _safe("validate", spec_or_object="__surface_eval_thick", min_thickness_mm=5.0,
                       continuity=False, curvature=False)
        _rec("pass.thick_box", vthick.get("verdict") == "pass", verdict=vthick.get("verdict"))

        # ---- M2 负例：G1 不连续（spec 有 continuity 目标才判 fail） ----
        zig = []
        for k in range(4):
            zig.append({"z_mm": 30.0 * k,
                        "points_mm": _circle(12, 8.0, 8.0)})
            # 摆心交替：让相邻截面中心错开 20mm 形成折角
            zig[-1]["points_mm"] = [[p[0] + (20.0 if k % 2 else 0.0), p[1]] for p in zig[-1]["points_mm"]]
        rz = _safe("loft", sections=zig, degree=1, continuity="G1", closed=True,
                   tessellation={"u": 16, "v": 20}, name="__surface_eval_zig")
        if rz.get("object"):
            MADE.append(rz["object"])
        vz = _safe("validate", spec_or_object=rz.get("spec"), thickness=False, self_intersection=False)
        _rec("fail.continuity", vz.get("verdict") == "fail"
             and any(d.get("check") == "continuity" for d in vz.get("defects") or []),
             verdict=vz.get("verdict"), defects=vz.get("defects"))

        # ---- tessellate ----
        ts = _safe("tessellate", spec=bottle.get("spec"), quality="draft", name="__surface_eval_ts")
        if ts.get("object"):
            MADE.append(ts["object"])
        to = _safe("tessellate", spec_or_object=bottle.get("object"), name="__surface_eval_to")
        if to.get("object"):
            MADE.append(to["object"])
        _rec("tessellate.spec", ts.get("ok") is True and ts.get("mode") == "spec")
        _rec("tessellate.object", to.get("ok") is True and to.get("mode") == "object_bake")

        # ---- M5：明确未实现 ----
        rb = _safe("blend", a="x", b="y")
        ro = _safe("offset", distance_mm=1.0)
        _rec("m5.blend_okfalse", rb.get("ok") is False and "OCCT" in str(rb.get("error") or ""))
        _rec("m5.offset_okfalse", ro.get("ok") is False and "OCCT" in str(ro.get("error") or ""))

        # ---- 坏参数 ok:false ----
        badc = {
            "loft_no_sections": _safe("loft", sections=[]),
            "loft_bad_units": _safe("loft", sections=sections, units="inch"),
            "loft_big_tess": _safe("loft", sections=sections, tessellation={"u": 9999, "v": 99}),
            "patch_ragged": _safe("patch", control_grid=[[[0, 0, 0], [1, 0, 0]], [[0, 1, 0]]]),
            "validate_missing": _safe("validate", spec_or_object="__surface_no_such"),
            "validate_bad_spec": _safe("validate", spec_or_object={"kind": "loft", "sections": []}),
            "unknown_op": _safe("nope_op"),
        }
        _rec("bad.all_okfalse", all(v.get("ok") is False for v in badc.values()),
             detail={k: v.get("ok") for k, v in badc.items()})

        # ---- NaN ----
        nan_ok = True
        for nm in MADE:
            ob = bpy.data.objects.get(nm)
            if ob is None or ob.type != "MESH":
                continue
            import numpy as np
            co = np.empty(len(ob.data.vertices) * 3, dtype=np.float64)
            ob.data.vertices.foreach_get("co", co)
            if not np.isfinite(co).all():
                nan_ok = False
        _rec("no_nan_vertices", nan_ok)

        # ---- 模块自检 ----
        st = _safe("selftest")
        _rec("module.selftest_ok", st.get("ok") is True, error=st.get("error"))
    finally:
        _cleanup()
        try:
            bpy.context.scene.unit_settings.scale_length = prev_scale
        except Exception:
            pass
    user_after = sorted(str(o.name) for o in bpy.data.objects
                        if not str(o.name).startswith("__surface_eval_"))
    leftovers = [str(o.name) for o in bpy.data.objects if str(o.name).startswith("__surface_eval_")]
    _rec("scene.user_unchanged", user_before == user_after,
         before=len(user_before), after=len(user_after))
    _rec("scene.no_leftovers", not leftovers, leftovers=leftovers[:6])


def main_safe():
    try:
        main()
        # ASSERT 是显式断言清单（evidence 里 "assert.subkey" 只是旁证数字，不参与判定）
        bad = sorted([k for k in ASSERT if EVID.get(k) is not True])
        return {"ok": len(bad) == 0, "failed": bad, "evidence": EVID}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:400]),
                "traceback": traceback.format_exc()[-1500:], "evidence": EVID}


if __name__ == "__main__":
    res_out = main_safe()
    print("HEADLESS " + json.dumps(res_out, ensure_ascii=False))
    print("SURFACE_SELFTEST " + ("ALL PASS" if res_out.get("ok") else "FAIL"))
    if not res_out.get("ok"):
        sys.exit(1)
