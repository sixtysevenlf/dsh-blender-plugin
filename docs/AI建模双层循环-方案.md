# AI 建模双层循环方案（2026-09-13）
>
> ⚠️ **分享版说明**：本文是作者机器上的实测与踩坑记录，文中出现的 `D:\DSH\...`、`/home/sixtyseven67/...`、`launch-blender.ps1` 等都是**作者环境的示例**；
> 在你的环境里对应的路径由 `runtime/config.mjs` 自动解析（见 [配置参考.md](配置参考.md)）。机制、踩坑与结论是通用的。


> 来源：本会话对"AI 实时交互 Blender"的全部实测与推演。目标不是让模型**反应更快**，而是让模型**不必待在循环里**。
> 配套实现：`dsh-blender-plugin/runtime/runner.py`（内环）+ 工具 `blender_rt_loop`（起停/查/候选表/导出）+ `blender_rt_see/do/watch/cmd/commands/perf/opt/headless`。
>
> **v0.4.0 给本方案补的两块拼图**：
> - **视觉验收不再打扰用户视口**：`blender_rt_see {from, look_at, ...}` 可以随时从任意角度出一帧（85–276 ms，零场景改动）→ 外环的"审美裁决"能做到多角度、可重复（同参数 md5 相同，可当验收指纹）。
> - **重活可以并行**：`blender_rt_headless` 把 Cycles 成品渲染 / 大批量几何 / 数据校验丢给独立进程，GUI 通道与内环都不受影响（实测起进程 0.8 s、建场景+出 2 图 2.66 s、打开 1821 对象工程 1.24 s）。

## 0. 一句话方案

**模型做外环（定目标、写判据、定参数空间、验收），Blender 做内环（高频迭代、测量、收敛）。**
内环在 Blender 主线程用 `bpy.app.timers` 跑，指标以**数字**回传，图片只用于关键节点的审美裁决。

## 1. 为什么必须分两层（本会话实测数据）

| 约束 | 实测值 | 后果 |
|---|---|---|
| 模型 → Blender 单步延迟 | **50–100 ms**（直连）；CLI/MCP 通道 0.9–1.5 s | 外环循环上限 **~10 Hz** |
| 模型自身回合 | 每步秒级（思考 + 生成） | 外环真正的瓶颈不是 I/O 而是**回合边界** |
| 图片回传预算 | `watch` 一次最多 6 帧（token 限制） | 观察带宽稀缺且昂贵 → 判据要数字化 |
| Blender 离屏渲染 | 55–120 ms/帧（主线程）；自定义视角 85–276 ms | 视觉反馈天然慢，且会卡人 |
| 无头进程（v0.4.0） | 冷起 0.8 s；建场景+出 2 图 2.66 s；载 1821 对象工程 1.24 s | 重活可离开 GUI 通道 → 内环与人工操作都不被阻塞 |
| Blender 事件循环 tick | **实测 162.8 tick/s**（空闲 GUI，interval=0） | 内环比外环快 **约 16 倍**，且不花模型回合 |

结论：外环适合"决策"，内环适合"搜索"。把搜索放在外环，等于用最贵的资源（模型回合）做最不值钱的事。

## 2. 架构

```
┌──────────── 外环：模型 ─────────────┐
│ 意图 → 拆解 → 目标函数/判据 → 参数空间  │
│ 验收：独立指标 + 视口帧（审美裁决）       │
└───────────────┬────────────────────┘
      blender_rt_loop(start/status/stop)  ← 一次调用 = 几百到几千次迭代
                │
┌───────────────▼────────────────────┐
│ 内环：runner.py（Blender 主线程 timers）│
│  setup → [step → measure]*N → best     │
│  限额：iterations / budget_ms / interval │
│  产出：best{score,i,params} + history    │
└───────────────┬────────────────────┘
   blender_rt_see / rt_watch（少量图片）+ blender_rt_cmd（读数字）
```

## 3. 工具面（模型侧共 7 个）

| 工具 | 用途 | 何时用 |
|---|---|---|
| `blender_rt_see` | 取一帧视口（50–100 ms） | 审美/结构判断的抽查 |
| `blender_rt_do` | 跑 Python（可选回帧） | 单步操作、读状态 |
| `blender_rt_watch` | 时间窗连续采样（≤6 帧 + 逐帧 hash） | 动态/动画手感验收 |
| **`blender_rt_loop`** | **起/停/查内环**（spec 见 §4） | 参数搜索、拟合、批量 QC |
| `blender_rt_cmd` | 透传任意 addon 命令（30 个名字） | 世界快照、资产类命令 |
| `blender_rt_commands` | 查可用命令 + 集成状态 | 排障/发现 |
| `blender_viewport` | 后端 status/start/stop | 运维 |

## 4. 内环契约（spec）

```python
blender_rt_loop(op="start", spec={
  "setup":   "只跑一次：建对象、定 apply()、初始化 params/best",
  "step":    "每个 tick：改一步（可 raise StopIteration 提前收敛）",
  "measure": "必须给 ns[score] 赋数值；参数写进 ns[params]（dict）",
  "iterations": 20000, "budget_ms": 6000, "interval": 0.0,
  "measure_every": 1, "minimize": True, "redraw_every": 0, "history_max": 200,
})
```

- `setup/step/measure` 是**源码字符串**，共享同一命名空间 `ns`：step 里写的变量 measure 直接可读。
- 收敛后 `status` 返回：`best{score,i,params,t_ms}`、`history_tail`、`tps`、`stop_reason`（`iterations|budget|stopped|converged|error`）、`error`（traceback）。
- 安全阀：迭代上限 + 墙钟上限 + `op=stop` 急停 + 出错自动停并留栈；`step/measure` 预编译（compile 一次），框架开销实测 **0.1 µs/iter**。

## 5. 五条守则（这套方案的成败都在这）

1. **目标先行**：先写清楚"什么算更好"（标量）与"什么算对"（约束/独立指标），再写 step。目标函数错的循环只会**更快地做错**。
2. **指标优先**：让内环输出数字表（误差、非流形数、最小壁厚、UV 拉伸、离地间隙…），图片只做抽查。观察带宽是稀缺资源。
3. **独立验证（反 Goodhart）**：验收必须换一条**不同的计算通路** + 一次视觉确认。本会话的反例：用 `Object.dimensions` 验证旋转后的包围盒 —— 它是"局部 bbox × scale"，**不含旋转**，会给出假结论；正确做法是 `matrix_world @ v.co` 求世界 AABB。
4. **可停可限**：任何内环都必须有 `iterations` 与 `budget_ms`；不许在 step 里做文件 I/O；`redraw_every=0`（纯计算）或 ≥8（要看着它动）。
5. **沉淀复用**：跑通的 spec 存成脚本/模板，下次换模型直接复用 —— 一次调参变成一条产线。
## 6. Cookbook（五种已成型模式）

| 模式 | 目标函数（示例） | step 形态 | 验收 |
|---|---|---|---|
| 拟合/反解 | 与目标尺寸或参考轮廓的 L2 误差 | 1+1 爬山 / 坐标下降 / 退火 | 换通路重算误差 + 一帧图 |
| 参数扫掠 | 无（遍历） | 网格/随机采样，写进 ns[params]，逐个 apply | 拼图（多帧）+ 指标表 |
| 约束求解 | 罚项：穿透深度、非共面度、最小壁厚违例 | 松弛/推挤迭代 | 独立检查器（bmesh 计算）+ 局部放大帧 |
| 动态手感 | 沉降时间/超调/峰值误差 | 按帧推进 + 采样曲线 | `blender_rt_watch` 拖尾 + 曲线数值 |
| 批量 QC | 违规计数（非流形/翻转法线/UV 重叠/命名） | 逐对象扫描（一次 tick 一个） | 问题清单表（数字）+ 抽样帧 |

## 7. 实测（本会话跑通的 Demo）

任务：把一个立方体的**世界 AABB** 调到 (3.2, 1.4, 0.9)，参数 = 缩放 3 维 + 旋转 3 维（共 6 维），目标函数 = 解析 AABB 的 L2 误差。

```
bench（框架空跑）        : 5000 iter / 0.52 ms     → 0.1 µs/iter
loop start               : iterations=20000, budget_ms=6000, interval=0
运行                     : 978 ticks / 6005.7 ms   → 162.8 tick/s（外环 ~10 Hz 的 16 倍）
best                     : score=0.0406 @ i=252（t=1797 ms），stop_reason=budget
独立验证（顶点×世界矩阵）  : world AABB = [3.1723, 1.3828, 0.9242] vs 目标 [3.2, 1.4, 0.9]
                         → independent_error = 0.0406（与内环指标一致，通道对上）
反面参照                 : Object.dimensions = [3.1063, 0.8221, 0.6458]（不含旋转，不可用作验证）
```

解读：6 维问题上 1+1 爬山在第 252 次迭代到达 0.04 精度后**停滞**（history 尾迹全等）。这正是"模型该介入的时刻"：换退火调度、加坐标下降、或换目标（例如对轮廓 IoU 而不是 AABB）。**内环负责跑到停，模型负责发现为什么停。**

## 8. 反模式（会白干）

| 反模式 | 后果 |
|---|---|
| 目标函数写错/写偏 | 循环高效地产出错误结果 |
| 用同一条通路验证 | 自证循环，误差看不见 |
| 把内环当渲染循环 | 每 tick 渲染 = 主线程 55–120 ms/帧，tick/s 崩到个位数 |
| step 里做文件 I/O 或刷屏 print | 吞吐骤降，日志污染 |
| 没有迭代/时间上限 | 一个跑飞的 timer 会锁死 Blender |
| 频繁 tag_redraw | 每 tick 重绘 = 把内环拖回几十 Hz |
| 指望内环自己知道对不对 | runner 只优化你给的函数，不理解你的意图 |

## 9. 与外环独跑对比

| 维度 | 外环独跑（现有能力） | 双层（本方案） |
|---|---|---|
| 迭代速率 | ~10 次/秒，且每次要一次工具调用 | **约 160 次/秒**，模型 0 次调用 |
| 一次交互能做的事 | 几十步 | 几千到几万步 |
| 反馈形态 | 图片为主（token 贵） | **数字为主**（token 便宜）+ 少量图 |
| 适用任务 | 单步编辑、判断、审美 | 拟合、搜索、约束求解、批量 QC、动态调参 |
| 失败模式 | 慢、来回折腾 | 目标写错时高效地错 |

## 10. 扩展项（2026-09-13 全部实现并实测，已并入 runner v2 与插件）

| 扩展 | 实现（runner v2 / 工具） | 实测证据 |
|---|---|---|
| **多目标 + 约束罚项** | `penalize(errors, violations, weights, lam)`，预置进 `ns`；measure 里一行出分 | 批量 Demo 用三目标加权 + 旋转幅度罚项（λ=20）跑通 |
| **退火 / 自适应步长** | `anneal(v0, v1, frac, power)` + `ns["i"]` / `ns["frac"]` 每次 tick 注入 | 同一 Demo 里步长 0.45 → 0.03 随进度退火，160 tick/s |
| **候选表** | `spec.top_k` + `record(...)` 手动登记；`blender_rt_loop {op:"board"}` 一次取回 | 每组 3 个候选、按 score 排序去重 |
| **跨对象批量** | `spec.group_key="obj"`：按对象分组各留 top_k | 3 个立方体各自拟合目标 AABB，分组最优互不干扰 |
| **导出可复用脚本** | `dsh_loop_export(path, top, include_variants)` + `{op:"export"}`：内嵌 setup 源码、best 与分组变体 | 导出 `batch_fit_export.py` 后**清空对象重跑该脚本**，3 个对象全部复现（独立通路验证） |

**批量 Demo 实测（一次调用完成 3 个对象的拟合）**：

```
loop start  : iterations=9000, budget_ms=6000, top_k=3, group_key="obj"
运行        : 963 ticks / 6004 ms → 160.4 tick/s，stop_reason=budget
分组最优    : obj0 E=0.138 · obj1 · obj2（op=board 取回，每组 3 个候选）
导出并重跑  : APPLIED ... variants_applied=2  →  3 个对象全部复现
独立验证    : obj0 err=0.115 · obj1 err=0.125 · obj2 err=0.56（顶点×世界矩阵，与内环通路无关）
```

> 过程中发现并修掉一个真 bug：导出候选时原先是"全局 top-k"，批量化后会把最强对象挤掉（obj2 完全没被应用）。现改为 `group_key` 存在时**每组取自己的最优**。
