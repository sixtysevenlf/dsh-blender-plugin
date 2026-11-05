# -*- coding: utf-8 -*-
"""DSH 规格驱动门包（v0.9.6 · 现场反馈 #3）—— 把"每个项目重写一遍门"变成一份 spec。

现场背景：一次 95 对象 / 3.05 M 面的装配建模里，作者自己写了 interference / redcheck /
selfint_diag 三个脚本约 600 行来做门。这类东西应该是插件的一部分：**判据来自 spec 文件，
执行与取证由插件负责**。

spec 形态（JSON 或 .py 模块，二选一）：
    {"name":"MK1","units":"mm","tolerance_mm":3.0,
     "gates":[
       {"id":"mesh",    "op":"audit_scene",        "args":{"summary_only":true,"top_k":5,"self_intersect":true},
        "pass_if":"clean == True"},
       {"id":"floaters","op":"audit_connectivity", "args":{"scope":"COL_Geo"},
        "pass_if":"gate['verdict'] == 'pass'"},
       {"id":"clash",   "op":"audit_interference", "args":{"objects_a":["Body"],"objects_b":["Lid"]},
        "pass_if":"verdict == 'refuted'"},
       {"id":"print",   "op":"print_report",       "args":{"min_mm":2.0},
        "pass_if":"thin_samples_total == 0 and walls_ok == True"}
     ]}
.py 形态：模块里给 SPEC = {...}（或模块级 NAME/UNITS/TOLERANCE_MM/GATES）。

**三态，不是两态**：每个门的结果是 pass / fail / degraded / error，外加第四种"没跑"的 skipped；
总 verdict = 任一 fail/error → fail；否则任一 degraded **或 skipped** → degraded；否则 pass。
@@ok@@ **只在 verdict==pass 时为 true** —— 现场教训："降级长得太像绿"，所以顶层永远读 @@verdict@@，别读 @@ok@@。

**v3 收紧（Lead 否决"pass + skipped = pass"）**：`skipped`（未配置来源 / 该门不适用）**不再是透明的** ——
只要包里有一门 skipped，顶层就是 `degraded`、`ok=false`。理由：assembly preset 的 `no_clash` 默认 skipped，
旧口径会让"没量过干涉"的健康场景拿绿；**跳过 ≠ 通过，也不许为"健康默认 preset"把标准降下来**。
要拿 pass 只有一条路：**每一门都真跑过且都过**（给 preset 补 `overrides={门id:{args…}}`，或写自己的 spec）。
"默认未配干涉来源 ⇒ 顶层 degraded" 是**正确行为**，不是 bug。

**三态不总在顶层（v0.9.6 复审修复）**：`audit_connectivity` 的顶层 `ok` 只表示"分析跑完了"，
门结论在**嵌套 `gate`** 里（`gate.state` / `gate.verdict`）—— 顶层没有 `state` 可读，旧写法
`pass_if="state == 'pass'"` 会让该门直接 `error`。正确写法是 `pass_if="gate['verdict'] == 'pass'"`
（回执自己给的已验证三态）。`_status_of` 也认嵌套三态，于是"未分析/未确认 ⇒ degraded"既不会被
pass_if 压成 fail、也不会被抬成 pass（`degraded ≠ 通过`）。域内名 `unresolved`（干涉"证据不足"）
同样读作 degraded。

**skipped 是第四种"结果"（v0.9.6；v3 收紧汇总口径）**：门可以声明 `skip_if_missing`（一组组"候选键名或"）——
每组一个都没给时，该门记 `state=skipped`（**未配置 / 不适用 ⇒ 没跑**）。它**不进** fail/degraded 的逐门点名，
但**会把整包压成 degraded**（`_aggregate`：任一 skipped 或 degraded ⇒ degraded，fail 优先），并且一定显式出现在
回执的 `skipped[]` 与 `coverage` 里（`skipped ≠ 通过`：没跑过的门不许被读成绿）。典型例子：
`audit_interference / audit_overlap` 是**两两**算子，preset 无从知道"哪两件比"，所以 assembly preset 的
`no_clash` 默认 skipped —— 于是**默认 assembly preset 的顶层就是 degraded**（正确；不是 bug）；要真判就写 spec 给
`objects_a`/`objects_b`，或 `gate_run(preset="assembly", overrides={"no_clash":{...}})` ——
**三门都真跑过且都过时才 pass**。

**degrade_if**：门的判据为真、但**证据本身不可信**时用 `degrade_if`（同样是受限表达式）把该门降级 ——
典型是 print preset：`walls_ok=true` 只说明"没量到薄壁"，而 `min_mm` 低于本网格的测量分辨率时这句"没量到"
**没有意义**（`print_walls` 只把它写进 per-object 的 `detail.walls.objects[].warning`/`resolution_mm`，顶层没有这些字段）。
print preset 现在用 `degrade_if = params['min_mm'] < detail['walls']['objects'][0]['resolution_mm']` 读**真实字段** ⇒
分辨率不可信时该门 `degraded`（不许假通过）；`echo=[…]` 把这几个真实读数（分辨率 / min_mm / warning）带进回执，可审计。

@@pass_if@@ 是一个**受限表达式**（ast 白名单：比较、布尔、算术、属性/下标、min/max/len/abs/round/all/any/sum），
变量从该门回执的顶层字段解析 —— 写成什么字段，回执里就只回**那几个字段**（自带瘦身）。`degrade_if` 用**同一套**
受限表达式与同一套变量解析；`echo` 走点路径（`detail.walls.objects.0.resolution_mm`）取真实字段做证据。
"""
import ast as ast
import hashlib
import json as json
import os as os
import sys
import time as time

import bpy as bpy



import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("gate 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


def _kernel():
    """模块命名空间里没有 K（K 在 dsh_rt_kernel 模块上）—— 与 sculpt.py / uv_tools.py 同一套取法。"""
    return sys.modules.get("dsh_rt_kernel")

GATE_VERSION = 3   # 3（v0.9.6 最终集成）：**任一 skipped/degraded ⇒ 顶层 degraded**（旧口径 pass+skipped=pass
                   #   被 Lead 否决）+ gate.degrade_if / gate.echo（不可信分辨率 ⇒ degraded，不许假通过）；
                   #   2（v0.9.6 复审修复）：skipped 状态 + gate.skip_if_missing + gate_run(overrides=) +
                   #   回执 coverage / 嵌套三态（audit_connectivity 的 gate.verdict）

# plan 级 op → (内核里的模块 API 属性, 该模块的子 op)
OP_MAP = {
    "audit_scene": ("dsh_audit_api", "scene"),
    "audit_mesh": ("dsh_audit_api", "mesh"),
    "audit_connectivity": ("dsh_audit_api", "connectivity"),
    "audit_interference": ("dsh_audit_api", "interference"),
    "audit_overlap": ("dsh_audit_api", "overlap"),
    "audit_gate": ("dsh_audit_api", "gate"),
    "audit_drift": ("dsh_audit_api", "drift"),
    "audit_duplicates": ("dsh_audit_api", "duplicates"),
    "print_report": ("dsh_print_api", "report"),
    "print_walls": ("dsh_print_api", "walls"),
    "uv_stats": ("dsh_uv_api", "stats"),
    "material_scan": ("dsh_material_api", "scan"),
    "fix_repair": ("dsh_fix_api", "repair"),
    "motion_measure": ("dsh_motion_api", "measure"),
    "sweep_analyze": ("dsh_sweep_api", "analyze"),
    "deliver_verify": ("dsh_deliver_api", "verify"),
    "audit_measure": ("dsh_audit_api", "measure"),
    "human_base": ("dsh_human_api", "base"),
    "human_measure": ("dsh_human_api", "measure"),
    "human_head": ("dsh_human_api", "head"),
    "human_spec": ("dsh_human_api", "spec"),
    "audit_file_mesh": ("dsh_audit_api", "file_mesh"),
    "interference_report": ("dsh_contract_api", "interference_report"),
    "audit_clash_all": ("dsh_assembly_api", "scan"),
    "asm_scan": ("dsh_assembly_api", "scan"),
    "asm_gate": ("dsh_assembly_api", "gate"),
}
ALLOWED_FUNCS = {"min": min, "max": max, "len": len, "abs": abs, "round": round,
                 "all": all, "any": any, "sum": sum, "float": float, "int": int, "str": str}

# 现场手写过的三个"包"，做成 preset（可直接 gate_run(preset="…")）
PRESETS = {
    "assembly": {
        "name": "assembly", "units": "mm", "tolerance_mm": 3.0,
        "gates": [
            # （v0.9.6 复审修复）：**必须显式 self_intersect=true** —— audit_scene 默认跳过自交检查，
            # 而新口径把"跳过检查"如实读成 degraded/clean=false（"没查"不许读成"没问题"）。不写这一条，
            # 再健康的场景也拿不到 pass。
            {"id": "mesh_health", "op": "audit_scene",
             "args": {"summary_only": True, "top_k": 5, "self_intersect": True},
             "pass_if": "clean == True",
             "note": "全场景网格体检。self_intersect=true 是**门的前提**：默认(false)会如实给 degraded（clean=false）"},
            # （v0.9.6 复审修复）：读回执**嵌套 gate 的已验证三态** —— audit_connectivity 顶层没有
            # state/verdict（顶层 ok 只表示"分析跑完了"），写 state == 'pass' 必 error。
            {"id": "no_floaters", "op": "audit_connectivity", "args": {},
             "pass_if": "gate['verdict'] == 'pass'",
             "note": "装配级连通门：判的是「某个分量与其它分量都不相接」（读回执嵌套 gate 的真实三态）。"
                     "单件/单连通分量场景这条判据**无从成立**（回执 components[].evidence=single-body）——"
                     "单件内部壳的贴合/互穿请用 audit_mesh(island_split=true)，别拿装配门当单件门。"
                     "未分析/未确认 ⇒ degraded（pass_if 不会把它抬成 pass）"},
            # （v0.9.6 复审修复；v3 收紧汇总口径）：audit_interference 是**两两**算子
            # （a/b 或 objects_a/objects_b 或 file_a/file_b），preset 无从知道零件对 ⇒ 空参必 error。
            # 这里声明 skip_if_missing：未配置来源 ⇒ state=skipped（显式列在回执 skipped[]/coverage 里）。
            # v3：skipped **会把整包压成 degraded** —— 所以**默认 assembly preset 的顶层是 degraded、ok=false**
            # （这是正确行为：没量过干涉就不许读成绿），要 pass 必须把两侧来源补上。
            {"id": "no_clash", "op": "audit_interference", "args": {},
             "skip_if_missing": [["a", "objects_a", "file_a"], ["b", "objects_b", "file_b"]],
             "pass_if": "verdict == 'refuted'",
             "note": "跨件干涉要**两侧来源**：refuted=可证无干涉(过) / supported=真干涉(fail) / "
                     "unresolved=证据不足(degraded)。未配置来源时本门是 skipped（未检查，不是通过；"
                     "v3 起 skipped 会把顶层压成 degraded）——"
                     "要真判：gate_run(preset=\"assembly\", overrides={\"no_clash\":{objects_a:[…],objects_b:[…]}})，"
                     "或写自己的 spec"},
        ],
    },
    "print": {
        "name": "print", "units": "mm", "tolerance_mm": 1.0,
        "gates": [
            # 同一条缺陷类别（v0.9.6 复审修复）：旧 pass_if 引用了回执里不存在的 thin_samples /
            # resolution_mm / min_mm ⇒ 该门必 error。改成 print_report 真实顶层字段：
            #   thin_samples_total=所有对象低于 min_mm 的采样数；walls_ok=逐对象 ok（含"射线没命中"）。
            # （v3 · 最终集成）：**只判 walls_ok 会假通过** —— 粗网格（体素/低模）的测量分辨率可能
            # 比 min_mm 大好几个数量级，此时"没量到薄壁"毫无意义：print_walls 只把这件事写进 per-object
            # 的 warning/resolution_mm，顶层 pass_if 看不见它。⇒ 加 degrade_if 读**真实字段**
            # （params.min_mm vs detail.walls.objects[0].resolution_mm）：分辨率不可信 ⇒ 该门 degraded
            # （不是 fail，也不是 pass），echo 把三个真实读数带进回执供审计。
            {"id": "walls", "op": "print_report", "args": {"min_mm": 2.0, "scope": "ACTIVE"},
             "pass_if": "thin_samples_total == 0 and walls_ok == True",
             "degrade_if": ("params['min_mm'] < detail['walls']['objects'][0]['resolution_mm']"
                            " or len(warnings) > 0"),
             "echo": ["params.min_mm", "detail.walls.objects.0.resolution_mm",
                      "detail.walls.objects.0.warning", "warnings",
                      "params.bed_mode", "params.bed_level_mm"],
             "note": "制造门：壁厚 < min_mm 的采样数必须为 0，**且这个结论得可信**。分辨率口径在 per-object 的 "
                     "detail.walls.objects[].warning / resolution_mm 上（顶层没有这个字段）⇒ degrade_if 直接读它们："
                     "min_mm 低于本网格测量分辨率时判 degraded（顶层变 degraded，不是假绿）；"
                     "v1.0.5（issue #14）起悬垂一侧同理：顶层 warnings[] 非空（print_overhang 的 bed_warning —— "
                     "平台面判不准，可能把真实悬垂当贴床面排掉）也判 degraded。"
                     "判的是活动对象（scope=ACTIVE，objects[0]）；多对象/多分辨率请自己写 spec 逐对象判"},
        ],
    },
    "delivery": {
        "name": "delivery", "units": "mm", "tolerance_mm": 1.0,
        "gates": [
            # 同一条缺陷类别：旧 pass_if "zero_area_faces == 0" 的字段不存在（uv_stats 顶层只有
            # ok/objects/note；零面积 UV 面在 objects[].degenerate_uv_faces）⇒ 该门必 error。
            {"id": "uv", "op": "uv_stats", "args": {}, "pass_if": "ok == True",
             "note": "uv_stats 顶层 ok = 逐对象「有 UV 层 且 零面积 UV 面=0」（明细在 objects[].degenerate_uv_faces）。"
                     "scope 默认 ACTIVE ⇒ 交付门要先选好活动对象"},
            {"id": "materials", "op": "material_scan", "args": {}, "pass_if": "count > 0",
             "note": "material_scan 在对象没有材质时直接抛错（不返回 count）⇒ 该门会 error；先 material_build/apply。"
                     "scope 默认 ACTIVE"},
        ],
    },
}



_j = _KIT.j  # 共享内核（原自带实现已删）
def _md5_text(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- spec 解析



__all__ = [name for name in list(globals()) if not name.startswith("__")]
