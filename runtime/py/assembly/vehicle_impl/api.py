# -*- coding: utf-8 -*-
"""Split from vehicle.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .base import vehicle_base, vehicle_sections
from .loft import vehicle_loft
from .panels import vehicle_panels, vehicle_regions
from .fit import vehicle_fit

def vehicle_selftest():
    """自检：比例门该过/该挂 + 建壳 + 轮与壳**零互穿**（解析轮眉的硬承诺）+ 尺寸吻合。"""
    ev = {}
    try:
        sp = vehicle_spec(type="sports", length_mm=4300.0, width_mm=1850.0, height_mm=1250.0,
                          wheelbase_mm=2600.0, front_axle_from_front_mm=900.0, wheel_r_mm=340.0,
                          wheel_w_mm=245.0, track_mm=1580.0, hood_len_mm=1150.0, roof_len_mm=950.0)
        pk = json.loads(vehicle_package(spec=sp))
        ev["package"] = {"ok": pk.get("ok"), "failed": pk.get("failed")}
        bad = vehicle_spec(type="sports", wheel_r_mm=150.0)          # 轮太小 ⇒ WBR 挂
        ev["bad_wbr"] = json.loads(vehicle_package(spec=bad)).get("failed")
        r = json.loads(vehicle_base(spec=sp, name="__dsh_veh_selftest", stations=24, section_pts=14))
        ev["base"] = {k: r.get(k) for k in ("ok", "size_mm", "vertices", "polygons")}
        ev["wheels"] = len(r.get("wheels") or [])
        # 零互穿：车壳 vs 四个轮，用 audit_clearance（同一进程里若已注入该模块）
        aud = __import__("sys").modules.get("dsh_rt_kernel")
        api = getattr(aud, "dsh_clearance_api", None) if aud is not None else None
        if api is not None and r.get("wheel_objects"):
            cl = api("check", {"pairs": [{"id": "wheel_vs_shell", "a": [r["wheel_objects"][0]],
                                          "b": [r["object"]], "min_mm": 0.0}]})
            p0 = (cl.get("pairs") or [{}])[0]
            ev["clearance"] = {"gap_mm": p0.get("gap_mm"), "interpenetrating": p0.get("interpenetrating"),
                               "why": p0.get("why")}
        else:
            ev["clearance"] = "clearance 模块未注入（preload 里加 clearance 可一并验零互穿）"
        size = r.get("size_mm") or {}
        ok = (pk.get("ok") is True and ev["bad_wbr"] and "wheel_to_body_ratio" in ev["bad_wbr"]
              and r.get("ok") is True and abs((size.get("len") or 0) - 4300.0) < 60.0
              and len(r.get("wheels") or []) == 4)
        if isinstance(ev.get("clearance"), dict) and ev["clearance"].get("interpenetrating") is not None:
            ok = ok and ev["clearance"].get("interpenetrating") is False
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        for o in [x for x in bpy.data.objects if x.name.startswith("__dsh_veh_selftest")]:
            me = o.data if o.type == "MESH" else None
            bpy.data.objects.remove(o, do_unlink=True)
            if me is not None and me.users == 0:
                try:
                    bpy.data.meshes.remove(me, do_unlink=True)
                except Exception:
                    pass
        c = bpy.data.collections.get("__dsh_veh_selftest_marks")
        if c is not None:
            bpy.data.collections.remove(c)


def vehicle_help():
    return _j({
        "module": "vehicle.py", "version": VEHICLE_VERSION,
        "what": "车辆外壳：参考图/参数 → 纵向剖面 → 放样成壳（解析轮眉）→ 比例门 + 零互穿保证",
        "ops": ["vehicle_spec", "vehicle_package", "vehicle_sections", "vehicle_loft", "vehicle_panels", "vehicle_regions", "vehicle_fit", "vehicle_base", "vehicle_selftest", "vehicle_help"],
        "types": TYPES, "defaults": DEFAULTS,
        "protocol": ["① 先从参考图量：车长/车高/轴距/轮径（img_scan 的 bbox + row_profile，或直接给像素点）",
                     "② vehicle_package 过比例门（WBR / 轴距比 / 高长比 / 轮距比）—— 不过就别建壳",
                     "③ vehicle_base 建壳 + 轮（轮眉解析扣出 ⇒ 零互穿）",
                     "④ qc_render_views 正交侧视图 + img_diff 对参考图算 IoU，改剖面参数再跑"],
        "limits": ["超级椭圆截面是**体量级**外壳：贴参考图靠剖面参数与 IoU 迭代，不靠雕曲面",
                   "轮眉扣的是轮心圆柱（不含悬挂行程）；要留行程就把 arch_clear_mm 加大",
                   "没做 A 柱/玻璃分件与细节件（灯/格栅/后视镜）—— 那些用硬表面单独做再挂"],
    })


def vehicle_dispatch(op, args_json):
    args = {}
    if isinstance(args_json, str) and args_json.strip():
        try:
            args = json.loads(args_json)
        except Exception as e:
            return _j({"ok": False, "error": "args 不是合法 JSON: %s" % str(e)[:120]})
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    try:
        kw = _KIT.resolve_refs(kw)   # 单步调用也支持 "@工件"
    except KeyError as e:
        return _j({"ok": False, "error": "引用解析失败: %s" % str(e)[:160]})
    ops = {"spec": lambda **k: _j(vehicle_spec(**k)), "package": vehicle_package, "base": vehicle_base,
           "sections": vehicle_sections, "loft": vehicle_loft, "panels": vehicle_panels,
           "regions": vehicle_regions, "fit": vehicle_fit,
           "selftest": vehicle_selftest, "help": vehicle_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown vehicle op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_vehicle_api = _DshApi({"version": VEHICLE_VERSION, "dispatch": vehicle_dispatch, "spec": vehicle_spec,
                                  "package": vehicle_package, "base": vehicle_base, "selftest": vehicle_selftest,
                                  "help": vehicle_help})