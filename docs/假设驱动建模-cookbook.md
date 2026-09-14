<!--
name: hypothesis-driven-modeling
description: 假设驱动建模方法论：当你需要「证据不足时明确报 unresolved 而不是二选一」、需要把语言判断翻译成可执行行为区间、需要用探针翻转假设、或需要让 Agent 说清「为什么这么建模」时使用。含三层：领域方法论 / 平台原语（本插件工具）/ 交付与验收 SOP。
-->

# 假设驱动建模 cookbook（三层技能结构）

> 结构说明（v0.8.x 起）：本文件按**三层技能法**组织（借鉴 lurenjia-l/dsh-blender-stylized-shading 的技能分层：领域知识 / 平台自举 / 交付 SOP）。
> 每层都写清「**何时用 / 用什么 / 产出什么 / 怎么算过关**」，便于 AI 按触发条件取用而不是通读全文。

## 层 1 · 领域方法论（为什么这么做）

**何时用**：你要在「模型替你猜结构」与「让证据决定结构」之间做选择；或者已经出现「外形像但结构没依据」的情况。

- 核心立场：**白盒化不是让 Agent 多解释几句**，而是「显式假设 → 翻译成行为区间 → 程序在区间内搜索与检查 → 证据翻转假设」。
- 三态判定：`supported` / `refuted` / **`unresolved`（证据不足是一等状态）**；不允许在证据不足时二选一。
- 可辨识性：一个参数在允许范围内变化而**外部证据完全不变**（极差≈0）→ 必须报不可辨识，并说明需要什么探针。
- 探针优先：能拆遮挡/加辅助测量就做探针，别用置信度掩盖。
- 破坏性操作前置门控：连接未判别时禁止 Boolean/Weld/合并。

**怎么算过关**：每个几何决策都能回答「依据是什么、什么证据能推翻它」。

## 层 2 · 平台原语（用什么做）

**何时用**：进入执行阶段，需要具体调用。

| 要做的事 | 工具 / API | 产出 |
|---|---|---|
| 记录假设与行为区间 | `blender_rt_plan(op="register_connection")`（candidates / params.range / forbidden / evidence_required） | 连接契约 |
| 只读校验（不动物体） | `op="check_envelope" / "check_interference" / "check_interface"` | 越界/干涉/间隙结果 |
| 破坏性门控 | `op="destructive_guard"` | 允许/拦截 + 阻塞明细 |
| 区间内自动搜索 | `blender_rt_loop`（setup/step/measure + penalize/anneal/board） | 候选表 + 最优参数 |
| 判据（客观比对） | `blender_rt_plan(op="qc_compare" / "qc_self_check" / "qc_robustness_check")` | IoU/Dice/缺多面积/边界距离 + 对照图 |
| 翻转与判定 | `op="verify" / "flip"` | supported/refuted/unresolved + 历史 |
| 证据与报告 | `op="evidence" / "ledger" / "report"` | 出图 + md5 + provenance 报告 |
| 可回退实验 | `blender_rt_txn`（mark/revert；大改前 snapshot） | 回滚点 |
| 跑长脚本 / 批量 | `blender_rt_do(file=…)` / `rt_headless` / `rt_worker` | 产物 + 日志 |
| 看画面（不动场景） | `blender_rt_see({from, look_at, shading})` | 帧 + hash |

**怎么算过关**：所有判定由程序给出（含 unresolved），不靠叙述。

## 层 3 · 交付与验收 SOP（怎么交付才算完）

**何时用**：收尾、交接、或要给用户一个可信结论时。

1. **目标与完成标准**先写下来（做到什么算完，用什么量测）；
2. **环境探测**（先做别猜）：`blender_viewport(op="doctor")` 通道体检、`rt_commands` 集成状态、`rt_perf(op="status")` 引擎与采样；
3. **执行**：按层 2 的原语做，过程出图/日志留档；
4. **自查**：`qc_self_check`（算法自检）+ `qc_robustness_check`（扰动鲁棒性）+ 关键结论的 md5 证据；
5. **汇报**：给结论 + 判据数字 + 未判明项（unresolved 要显式列出，并写清需要什么探针）；
6. **归档与排障**：成品进 `D:\Blender\<项目>\`，QC/探针留在工作区；排障先看 `rt_perf status` / `rt_worker status` / 日志路径。

**怎么算过关**：写者 ≠ 验者（独立验证）；每个数字可复现；未判明项没有被"平均掉"。

---
# 假设驱动建模 Cookbook（S0）

> 目的：把「模型直接开建 → 最后只看到结果」换成**假设 → 行为区间 → 程序搜索 → 证据判定 → 假设翻转**的可审计闭环。
> 来源：本仓库 issue #3（白盒化 + 规划器建议）的第一阶段落地物；配套可复现实验见 `docs/examples/chair-backrest/`。
> 适用：任何"被遮挡/证据不足/有不可逆操作风险"的建模任务。**简单任务请走轻量模式**（见 §1）。

---

## 1. 什么时候用（阈值）

| 情形 | 走哪条 |
|---|---|
| 无遮挡、单物体、可逆（改尺寸/位置） | **轻量模式**：直接 `blender_rt_do` + `blender_rt_see`，不写假设 |
| 有遮挡 / 证据不足（"看不清但必须决定"） | **本 cookbook**：显式假设 + 区间 + 探针计划 |
| 涉及 Boolean / 焊接 / 合并 / apply transform | **必须先过契约**（§6 禁止项）：判别清楚前一律不做这些操作 |
| 多组件装配、接口约定多、多人/多 agent 并行 | 契约层（S1）+ 对象图（S3） |

判定"证据不足"的经验判据（本仓库实测）：**同一份可见证据下，两个不同假设都能达到同一残差** —— 见 §4 的 184 / 184。

---

## 2. 一条假设要写什么（模板）

```json
{
  "id": "backrest_joint",
  "statement": "靠背与座椅的连接：板贴附在座椅后表面，还是板内部有榫头插进座椅",
  "candidates": {
    "attach": "板贴在后表面，内部没有榫",
    "insert": "同一块板 + 内部榫头插入座椅（外部轮廓完全相同）"
  },
  "params": {
    "dy":        {"range": [0.000, 0.030], "unit": "m",   "meaning": "板相对座椅后表面的法向偏移"},
    "tilt":      {"range": [-2.0, 6.0],    "unit": "deg"},
    "dz":        {"range": [-0.020, 0.020], "unit": "m"},
    "tenon_len": {"range": [0.030, 0.100], "unit": "m",   "only_for": "insert"}
  },
  "confidence": "medium",
  "forbidden_until_resolved": ["boolean_union", "weld", "apply_transform"],
  "evidence_planned": ["external_side", "external_rear", "probe_dismount_measure"],
  "flip_condition": "区间内所有参数都达不到轮廓容差，或探针读数与假设矛盾 → refuted，切另一候选"
}
```

**规则**

1. `statement` 写成**可判别的二选一/多选一**，不写"大概是……"；
2. 每个参数必须有 `range` 与单位（区间就是"允许做什么、允许改多少"）；
3. `forbidden_until_resolved` 明确写"判别前禁止什么"；
4. `flip_condition` 必须能被程序检查（数值判据），不写"如果感觉不对"。

---

## 3. 区间搜索怎么跑（两种都可用）

**① GUI 会话：用内环（不花模型回合）**

```text
blender_rt_loop(op="start", spec={
  "setup":   "<只跑一次：建场景 / 取参考>",
  "step":    "<在区间内采样参数：ns['params'] = {...}>",
  "measure": "<候选与参考比：ns['score']=轮廓误差；ns['metrics']={...}；ns['violations']={...}>",
  "iterations": 5000, "budget_ms": 30000, "measure_every": 1, "top_k": 20, "interval": 0
})
blender_rt_loop(op="board")                        # 候选表：哪些参数组合、各自误差
blender_rt_loop(op="export", path="<宿主路径>")     # 落成可复用脚本
```

**② 无头进程：有界阻塞搜索（`blender_rt_headless`）**

> ⚠️ 实测：`bpy.app.timers` 在 `blender -b` 下**不会触发**（本仓库实测 1.2 s 内 0 次）→
> 无头里别用内环，直接在脚本里循环；本仓库示例 `run.py` 就是这么跑的，3 s 出全部结果。

---

## 4. 可辨识性：不是"找到最优就完事"

对每个参数统计"该参数各取值下的最小误差"，看**极差**：

- 极差大 → 该参数被证据钉住（**可辨识**）；
- 极差 ≈ 0 → 在现有证据下**不可辨识** → 程序必须**说出来**，不能随手取一个值。

本仓库椅子靠背实验实测（外部证据 = 侧视图 + 后视图，接缝被挡板遮住；每个假设 48 个候选）：

| 参数 | 各取值下的最小外部误差 | 极差 | 可辨识 |
|---|---|---|---|
| `dy` | 0.000:476 / 0.010:184 / 0.020:485 / 0.030:974 | **790** | 是（真值 0.010） |
| `tilt` | -2.0:184 / 6.0:495 | 311 | 是 |
| `dz` | -0.020:186 / 0.020:184 | **2** | 否（平坦） |
| `tenon_len` | 0.030:184 / 0.065:184 / 0.100:184 | **0** | 否（平坦） |

而且**两个假设的外部残差完全相同（attach 184 / insert 184）** → 外部证据不足，判定必须是 `unresolved`。

---

## 5. 判定规则（三态 + 探针）

```text
best_err <= tol 且关键参数可辨识  → supported（附证据）
best_err >  tol                  → refuted
best_err <= tol 但关键参数不可辨识 → unresolved + 「需要什么探针」
多个候选假设都 <= tol             → unresolved（列出全部）
```

**探针要写成可执行动作清单**（非破坏性优先）。本示例：拆掉遮挡套 + 隐藏座椅 + 深度规量榫伸出量。

| 阶段 | attach 读数 | insert 读数 | 真值 | 判定 |
|---|---|---|---|---|
| 外部视图 | 残差 184（与 insert 相同） | 残差 184 | — | **都 unresolved** |
| 探针（深度规） | **0.000 m** | **0.065 m** | 0.060 m | attach **refuted** · insert **supported** |

> 探针读数不是"更准的图"，而是**换一个能决定问题的物理量**。
> 本示例里图像探针仍被姿态估计误差污染（第一版踩过：探针视图里板的位置差异压过了榫长差异），换成深度规后判定立刻干净。

---

## 6. 禁止项与门控（判别前不要毁掉可回退性）

| 操作 | 为什么危险 | 判别前的规则 |
|---|---|---|
| `boolean_union` | 拓扑被改写，假设翻转后无法恢复 | 禁止（契约层直接拦） |
| `weld` / 拓扑焊接 | 同上，且难以检测 | 禁止 |
| `apply_transform` | 参数化丢失（区间失去意义） | 禁止 |
| `opt join` / 合并 | 组件消失，接口证据没了 | 先 `save_before`，且不得与"未判别的连接"冲突 |

> S1 会把这三条做成**契约层门控**（不是建议，是拦下来的错误），并在报告里给 `Unsupported Destructive Merge` 这类诊断。

---

## 7. provenance：每一步都能回溯

| 字段 | 本示例的实际值 |
|---|---|
| 假设 | `insert`（同尺寸板 + 内榫） |
| 区间 | `tenon_len 0.03~0.10 m`、`dy 0~0.03 m` |
| 搜索 | 外部 48 个候选 + 探针 5 个候选 |
| 证据 | `gt_external_side.png` / `gt_external_rear.png` / `gt_probe_no_seat.png`（带 md5） |
| 判定 | 外部 `unresolved` → 探针后 `supported`（读数 0.065 m，真值 0.060 m） |
| 反例 | `attach` 被探针读数 0.000 m 推翻 |
| 复现 | `blender -b --factory-startup --python docs/examples/chair-backrest/run.py -- <outdir>` |

报告可自动生成：示例目录里的 `results.md` 就是脚本产出的（含判定依据、可辨识性表、复现命令）。

---

## 8. 实测踩坑（每一个都会让你得到"看起来对"的错结论）

| # | 坑 | 现象 | 做法 |
|---|---|---|---|
| 1 | 改完场景不更新 depsgraph | 参考图里挡板还是 1 m 立方体，整幅图被填满 → 掩码全 True、误差恒 0 | 出图前 `view_layer.update()` + `evaluated_depsgraph_get().update()` |
| 2 | 无头里用 `bpy.app.timers` | 内环一次都不触发（实测 0 次） | 无头用阻塞循环；内环留给 GUI 会话 |
| 3 | 探针视角在"错误的一侧" | 榫在板后面，从 +y 看被挡住 → 两个假设读数一样 | 探针必须看得见**被判别的那一面**（本例从 -y 侧） |
| 4 | 遮挡物没一起拆 | 遮挡套正好在榫前方，探针视图里榫被挡住 | 探针 = 拆掉遮挡 + 隐藏座体（可复原） |
| 5 | 测量把隐藏对象算进来 | attach（无榫）量出 0.07 m —— 那是上一轮隐藏的榫 | 量测只看可见对象（`hide_render / hide_viewport` 过滤） |
| 6 | 姿态误差污染探针 | 图像探针里"板差 2°"压过"榫差 3 cm" | 换物理量（深度规/间隙规），或先固定姿态只搜内部参数 |
| 7 | 掩码阈值把背景当物体 | 误差恒 0 或恒最大 | 出图关掉视口背景（`background:false`）+ 与 clear 色对比阈值 |

---

## 9. 与后续阶段的关系

| 阶段 | 内容 | 本 cookbook 的位置 |
|---|---|---|
| **S0**（本文） | 假设/区间/可辨识性/探针/判定/provenance 的流程与模板 | 已可用：零代码 + 一个可复现示例 |
| S1 | 契约层：注册 Component/Connection、包络/干涉/接口校验、破坏性门控、证据账本 | 把 §2 / §6 / §7 变成机器可查的 API |
| S2 | 假设生命周期 `proposed → testing → supported/refuted` + 无解自动翻转 + 报告自动生成 | 把 §5 / §7 自动化 |
| S3 | 对象图（Component/Connection/Feature）+ 编译到 bpy + 依赖诊断 | 把 §2 的候选与参数升级成可规划的图 |

---

## 10. 最小用法（今天就能用）

1. 按 §2 写一条假设（JSON）；
2. 按 §3 二选一在区间内搜索（GUI 内环 / 无头阻塞）；
3. 按 §4 算可辨识性 —— 把**不可辨识的参数写进交付说明**；
4. 按 §5 判三态，需要时执行探针；
5. 按 §7 存档 provenance + 复现命令。

> 示例目录 `docs/examples/chair-backrest/` 内含：`run.py`（可复跑）、`results.md/json`（自动报告与原始数据）、`evidence/`（证据图）。
