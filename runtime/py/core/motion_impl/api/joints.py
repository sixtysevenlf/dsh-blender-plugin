# -*- coding: utf-8 -*-
"""motion status/help surface: joint registry introspection (moved out of api.py)."""
from ..common import *  # noqa: F401,F403


# ============================================================ status / help

def motion_status():
    st = _store()
    js = {}
    counts = {"supported": 0, "refuted": 0, "unresolved": 0}
    for k, v in st["joints"].items():
        mv = (v.get("measured") or {}).get("verdict")
        av = v.get("axis_verdict") or "unresolved"
        vv = mv or av
        counts[vv if vv in counts else "unresolved"] = counts.get(vv if vv in counts else "unresolved", 0) + 1
        js[k] = {"kind": v["kind"], "parent": v["parent"], "child": v["child"],
                 "axis": v.get("axis"), "anchor": v.get("anchor"),
                 "axis_source": v.get("axis_source"), "axis_verdict": av,
                 "limits": v.get("limits"), "note": v.get("note"),
                 "measured": ({"verdict": mv, "moved": (v.get("measured") or {}).get("moved"),
                               "phases": len((v.get("measured") or {}).get("phases") or []),
                               "reason": (v.get("measured") or {}).get("reason"),
                               "restored_ok": (v.get("measured") or {}).get("restored_ok"),
                               "t": (v.get("measured") or {}).get("t")} if v.get("measured") else None),
                 "warnings": v.get("warnings") or []}
    return _j({"ok": True, "op": "status", "version": MOTION_VERSION,
               "joints": js, "joint_count": len(js),
               "verdict_counts": counts,
               "exports": st["exports"], "export_count": len(st["exports"]),
               "log_tail": st["log"][-10:],
               "units": {"length": "scene_unit（1 单位 = %g m）" % _scene_mpu(), "angle": "deg"},
               "boundary": "关节轴可实测（旋转对称/接触带）；扫掠是运动学验证不是物理验证；"
                           "USDA 不是完整 UsdPhysics schema"})


def motion_help():
    return _j({
        "version": MOTION_VERSION,
        "ops": {
            "joint": "motion_joint(jid, parent, child, kind='revolute', axis=None, anchor=None, limits=None, "
                     "note='') —— parent/child 是对象名或对象名列表（刚性组）；axis/anchor 不给则实测推断",
            "infer_axis": "motion_infer_axis(jid=None, objects=None, parent=None, sample_cap=3000, "
                          "min_score=0.55, max_distance=None) → 证据 + 三态（薄平板绕铰链轴无旋转对称 ⇒ unresolved）",
            "joints": "motion_joints() → 已登记关节 + 轴判定 + 实测摘要",
            "measure": "motion_measure(jid=None, phases=8, sweep_deg=60, margin_frac=0.05, exclude=None, "
                       "chain_depth='full', max_candidates=64, max_samples=400) → 扫掠（干涉对+最小间距）",
            "export_urdf": "motion_export_urdf(dir, name='model', joints=None, meters_per_unit=0.001, "
                           "density=1000, masses=None, mesh_format='obj', effort=1000, velocity=100) "
                           "→ <dir>/<name>.urdf + <dir>/meshes/*.obj + 结构自检",
            "export_usda": "motion_export_usda(dir, name='model', joints=None, meters_per_unit=0.001, "
                           "density=1000, masses=None) → 最小可用 ASCII USDA + 结构自检",
            "status": "motion_status() → 关节 + 判定 + 导出历史（K.dsh_motion）",
            "selftest": "motion_selftest() → 合成铰链自检（无头可跑，自带场景/清理）",
            "help": "motion_help()",
        },
        "typical": "K.dsh_motion_api['dispatch']('joint', {'jid':'hinge','parent':['Base'],"
                   "'child':['Door','Pin'],'kind':'revolute','limits':[-90,90]}) → "
                   "K.dsh_motion_api['dispatch']('measure', {'jid':'hinge','phases':8,'sweep_deg':60}) → "
                   "K.dsh_motion_api['dispatch']('export_urdf', {'dir': K.out_dir, 'name':'door'})",
        "rules": {
            "verdict": "supported / refuted / unresolved 三态；证据不足一律 unresolved（不许二选一凑）",
            "ok": "ok=false ⇔ unresolved 或空产出（0 link / 0 joint / 没轴 / 没 child 几何）",
            "units": "revolute 的 limits/相位=deg；prismatic=场景单位；间距同时给 scene_unit 与 m；URDF=SI",
            "restore": "扫掠结束逐对象对拍 matrix_world（全部对象），mismatch 写在 restored 里",
        },
        "boundary": [
            "扫掠 = 运动学 + BVH 干涉，不是 Isaac 那种物理验证（无质量/摩擦/驱动动力学）",
            "最小间距是采样近似（偏保守/偏大）；干涉判定用 BVH 三角面相交（精确）",
            "零干涉需要至少一个'旁观者'对象，否则是空洞证据 → 该情形判 unresolved",
            "轴推断只对可辨识几何有效；接触带证据需要父组 + max_distance 内有足够接触点",
            "两条证据（旋转对称 / 接触带）矛盾时一律 unresolved，不替用户选",
            "导出要求各关节分组互不重叠（一个对象只能属于一个 link），否则 ok=false",
            "USDA 是最小可用 ASCII，不是完整 UsdPhysics schema",
        ],
    })
