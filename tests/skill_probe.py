# -*- coding: utf-8 -*-
"""技能前置探测（skill probe）—— 在 Blender 内运行，判断某技能声明的 Blender 依赖是否就绪。

用法（本插件通道，不需要 MCP）：
    blender_rt_do(file="//tests/skill_probe.py")            # 默认 spec（含 stylized-shading）
    K.dsh_probe_spec = {"name": "某技能", "addons": ["mod"], "ops": ["node.add_xxx"], "node_groups": ["组名"]}
    blender_rt_do(file="//tests/skill_probe.py")            # 自定义 spec
输出：print("HEADLESS {...}") —— ready + 逐项判据 + next_steps。

注意（本文件踩过的坑）：bpy.ops 是动态的，getattr(bpy.ops.node, "不存在的算子") 也会返回代理对象，
所以算子存在性**必须**用 RNA 类型判定：bpy.types.<CATEGORY>_OT_<name>。
"""
import bpy
import json
import sys

DEFAULT_SPEC = {
    "name": "stylized-shading（NPR 风格化着色）",
    "addons": ["blender_Stylized-Shading-Tools", "bl_stylized_shading", "stylized_shading"],
    "ops": ["node.add_gradient_group", "node.add_stylized_shader"],
    "node_groups": [],
    "notes": "该技能假设第三方 NPR 插件已装（节点组/算子由它提供）；缺它时仍可用与 addon 无关的段落（诊断套路/参数语义/EEVEE 限制/方向规则）",
}


def _spec():
    K = sys.modules.get("dsh_rt_kernel")
    s = getattr(K, "dsh_probe_spec", None) if K else None
    return s if isinstance(s, dict) and s.get("name") else DEFAULT_SPEC


def _op_exists(dotted):
    cat, _, name = str(dotted).partition(".")
    if not cat or not name:
        return False
    return hasattr(bpy.types, "%s_OT_%s" % (cat.upper(), name))


def main():
    spec = _spec()
    res = {"skill": spec.get("name"), "ready": True, "checks": [], "next_steps": []}
    enabled = set(bpy.context.preferences.addons.keys())
    import addon_utils
    available = set(m.__name__ for m in addon_utils.modules())
    for a in spec.get("addons") or []:
        hit_e, hit_a = a in enabled, a in available
        res["checks"].append({"kind": "addon", "name": a, "enabled": hit_e, "installed": hit_a, "ok": hit_e or hit_a})
        if not (hit_e or hit_a):
            res["ready"] = False
            res["next_steps"].append("装插件: %s（放到 Blender script_paths 的 addons/ 下）" % a)
        elif not hit_e:
            res["next_steps"].append("已装未启用: bpy.ops.preferences.addon_enable(module=%s)" % a)
    for o in spec.get("ops") or []:
        ok = _op_exists(o)
        res["checks"].append({"kind": "op", "name": o, "ok": ok, "probe": "bpy.types." + str(o).split(".")[0].upper() + "_OT_" + str(o).split(".")[-1]})
        if not ok:
            res["ready"] = False
            res["next_steps"].append("算子缺失: bpy.ops.%s（通常随插件提供）" % o)
    for g in spec.get("node_groups") or []:
        ok = any(ng.name == g for ng in bpy.data.node_groups)
        res["checks"].append({"kind": "node_group", "name": g, "ok": ok})
        if not ok:
            res["next_steps"].append("节点组缺失: %s（若属插件自带，先启用插件再新建材质）" % g)
    res["next_steps"].append("前置就绪：可直接照技能正文执行" if res["ready"] else
                             "降级方案：只用技能里与 addon 无关的段落（诊断套路 / 参数语义 / 引擎限制 / 方向规则）")
    res["notes"] = spec.get("notes")
    print("HEADLESS " + json.dumps(res, ensure_ascii=False))


main()