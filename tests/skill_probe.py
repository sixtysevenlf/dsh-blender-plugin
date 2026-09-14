# -*- coding: utf-8 -*-
"""技能前置探测（skill probe）—— 在 Blender 内运行，判断某技能声明的 Blender 依赖是否就绪。

用途：技能文档常假设某些插件/算子/节点组存在（例如 stylized-shading 技能假设已装 blender_Stylized-Shading-Tools）。
本脚本把「先探测，别猜」固化成可复用动作：读 K.dsh_probe_spec（或默认 spec），逐项判定并给出下一步。

用法（经本插件通道，不需要 MCP）：
    blender_rt_do(file="//tests/skill_probe.py")                      # 用默认 spec（当前含 stylized-shading）
    # 自定义 spec：
    K.dsh_probe_spec = {"name": "某技能", "addons": ["模块名"], "ops": ["node.add_xxx"], "node_groups": ["组名"]}
    blender_rt_do(file="//tests/skill_probe.py")
输出：print("HEADLESS {...}") —— ready 布尔 + 缺失项 + 每项的下一步。
"""
import bpy, json

DEFAULT_SPEC = {
    "name": "stylized-shading（NPR 风格化着色）",
    "addons": ["blender_Stylized-Shading-Tools", "bl_stylized_shading", "stylized_shading"],
    "ops": ["node.add_gradient_group", "node.add_stylized_shader"],
    "node_groups": [],
    "notes": "技能正文假设该插件已装（其节点组/算子由插件提供）；缺它时：① 插件级代码不可用 ② 与 addon 无关的段落（诊断套路/参数语义/EEVEE 限制）仍可用",
}


def _spec():
    K = None
    import sys
    K = sys.modules.get("dsh_rt_kernel")
    s = getattr(K, "dsh_probe_spec", None) if K else None
    return s if isinstance(s, dict) and s.get("name") else DEFAULT_SPEC


def _op_exists(dotted):
    cur = bpy.ops
    for part in str(dotted).split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return False
    return True


def main():
    spec = _spec()
    res = {"skill": spec.get("name"), "ready": True, "checks": [], "next_steps": []}
    enabled = set(bpy.context.preferences.addons.keys())
    import addon_utils
    available = set(m.__name__ for m in addon_utils.modules())
    for a in spec.get("addons") or []:
        hit_e, hit_a = a in enabled, a in available
        ok = hit_e or hit_a
        res["checks"].append({"kind": "addon", "name": a, "enabled": hit_e, "installed": hit_a, "ok": ok})
        if not ok:
            res["ready"] = False
            res["next_steps"].append("装/启用插件: %s（本机 addons 目录 = Blender 的 script_paths 里的 addons/）" % a)
        elif not hit_e:
            res["next_steps"].append("插件 %s 已安装但未启用：bpy.ops.preferences.addon_enable(module=\"%s\")" % (a, a))
    for o in spec.get("ops") or []:
        ok = _op_exists(o)
        res["checks"].append({"kind": "op", "name": o, "ok": ok})
        if not ok:
            res["ready"] = False
            res["next_steps"].append("算子缺失: bpy.ops.%s（通常随插件提供）" % o)
    for g in spec.get("node_groups") or []:
        ok = any(ng.name == g for ng in bpy.data.node_groups)
        res["checks"].append({"kind": "node_group", "name": g, "ok": ok})
        if not ok:
            res["next_steps"].append("节点组缺失: %s（如属插件自带，先启用插件再新建材质）" % g)
    if res["ready"]:
        res["next_steps"].append("前置就绪：可直接照技能正文执行")
    else:
        res["next_steps"].append("降级方案：只使用技能里与 addon 无关的段落（诊断套路 / 参数语义 / 引擎限制 / 方向规则）")
    res["notes"] = spec.get("notes")
    print("HEADLESS " + json.dumps(res, ensure_ascii=False))


main()