# 路线决定：headless 优先，GUI 直连为交互式增益（v0.9.3）

| 项 | 值 |
|---|---|
| 决定 | **无头批处理（`blender_rt_headless` / `blender_rt_job`）是第一路径；直连 GUI（9876 addon + `rt_do` / `rt_see` / `rt_watch` / `rt_cmd`）是交互式增益** |
| 状态 | **已定**（v0.9.3 起生效），本文是这条路线唯一口径 |
| 依据 | `docs/feedback/插件改进交接-2026-09-24.md` §1 / §5 / §6 / 附录 A（7 名协作者 · ≈120 次工具调用样本） |
| 影响面 | 工具默认值（D1 等待窗口 / `as_job`）、回执结构、文档与示例、**任务模板**、并行分工 |
| 反悔条件 | 见 §6（出现「必须在 live 场景上做」的多数任务，或 headless 冷启动成为瓶颈） |

> 配套阅读：新参数与陷阱见 [操作教程.md](操作教程.md) §7；读图与量测纪律见 [证据分级与读图纪律.md](证据分级与读图纪律.md) §6/§7；
> 环境变量与路径空间见 [配置参考.md](配置参考.md) §3/§10。

---

## 0. 一句话

> **默认 headless；只有「要看一眼 / 要改用户手上那个活场景 / 要吃 GUI 上下文」时才 Connect。**

这条是**排序**，不是砍功能：15 个工具一个不删，`rt_do/rt_see/rt_watch/rt_loop/rt_cmd` 的语义、租约、doctor 全部保留。

---

## 1. 决定内容

1. **第一路径 = headless**：凡「能离线跑完」的活（建模脚本、批量几何、校验、QC 出图、成品渲染、交付导出）默认走 `blender_rt_headless` / `blender_rt_job` —— **不要求 Blender GUI 在跑，也不要求 addon Connect**。
2. **GUI 直连 = 交互式增益**：只在「人在回路里、看一步改一步」时用（`rt_see` / `rt_do {see:true}` / `rt_watch` / `rt_loop` / `rt_cmd`）。
3. **并行分工默认不探索 GUI**：模板里不写「先跑 doctor」，一个任务不需要 GUI 就不写「请 Connect」（§4.3 给填空式模板）。
4. **纯 bpy 的 op 必须能在 headless 直调**：`audit_*` / `qc_*` / `deliver_*` / `motion_*` / `generator_*` / `montage` / `contract` / `plan` —— 逐条示例见 §4.1。
5. **超时不再吞结果**（D1 落地）：客户端等待窗口到点回 `{kind:"promoted", jobId:"run-…"}`，结果永远能从作业层取回（§4.5）。
6. **长任务可观测**：三处 spawn 注入 `PYTHONUNBUFFERED=1` + 脚本侧 `dsh_stage()` 心跳 → `op=status.stage`（§4.6）。

---

## 2. 证据（2026-09-23~24，《星际公民》RSI Aurora Mk I MR 分件建模）

| 观察 | 数字 |
|---|---|
| 协作者全程 headless | **7 / 7** |
| addon 9876 `Connect` 次数 | **0**（开局 `blender_viewport(op="doctor")` 即 `blender-unreachable`） |
| `rt_do` / `rt_see` / `rt_watch` / `rt_cmd` / `rt_commands` / `blender_viewport` 调用次数 | **各 0** |
| 主力通道 | `blender_rt_headless(script= / script_file=, args=, env=, engine=, timeout_ms=)`；Lead 侧长渲染走 `blender_rt_job` |
| 单次最长任务 | 6 张 1920×1080 / 256 samples ≈ **20 分钟**（作业层） |
| 想用但没用上的 op | `audit_duplicates` / `audit_snap_floaters` / `audit_*` / `qc_compare` —— 被手写脚本替代（hull_fore ≈60 行世界顶点哈希 + AABB；hull_aft 自写 BVH 最近距离扫描；reviewer 主体掩膜搓了 3 遍） |

**三条原因**（协作者原话收敛）：

1. **没有 GUI 会话** —— 9876 从未 Connect，`rt_do/rt_see` 依赖主线程实时通道；
2. **并行安全** —— 6 路分件同时跑，动 live 场景会互相污染 + 抢主线程；headless 每次是干净进程；
3. **不可用即绕过** —— 连 `doctor` 都没跑就直接选 headless。

**结论**：GUI 通道在并行分工场景里**默认不会被探索**。要么在模板里显式要求，要么承认它是可选的第二路径 —— 本决定选后者。

**代价最大的一条**：`audit_* / qc_*` 本来就能 headless 直调，但描述里没有「传集合名、逐件出报告」的可抄示例 → 全员手写。§4.1 逐条补齐。

---

## 3. 影响面

| 面 | 变化（v0.9.3） |
|---|---|
| headless 默认行为 | 等待窗口（默认 100 s，`DSH_HEADLESS_WAIT_MS`）到点**不再丢结果** → 回 `{kind:"promoted", jobId:"run-…"}` |
| headless 新参数 | `shots`（多视角一体化）· `as_job` · `wait_s` |
| 作业层 | `op=start` 收 `script_file/args/env/factory_startup/bootstrap/workdir/include_noise`；新增 `op=wait`；`kill` 对未知 id 不报错；`status` 增 `stage/stageAt/lastOutputAt/idleMs/lines/pidAlive` 与 stale 收敛 |
| 回执 | **两个 text block**：第 1 个是单行 JSON 信封（可直接 `JSON.parse`），第 2 个是人读摘要 → 不再手写正则 |
| 路径 | `outdir` / `out_json` 接受 WSL 路径，并回传真实写入路径 |
| 文档 | 本文 + 操作教程 §7 + 证据分级 §6/§7 + 配置参考 §10 |
| 任务模板 | 增一行硬声明「本任务是否需要 GUI + Connect」（§4.3） |

---

## 4. 迁移指引

### 4.1 哪些 op 可以在 headless 直调（附 copy-paste 示例）

| 你要做的事 | GUI 里的写法（v0.9.2） | headless 直调（推荐） |
|---|---|---|
| 网格体检（自交 / 非流形 / 退化面 / 法线） | `blender_rt_plan(op="audit_mesh", args={objects:[...]})` | `preload="audit"` → `K.dsh_audit_api('mesh', {...})` |
| 全场景体检 / 越界件 | `audit_scene` | 同上，`('scene', {...})` |
| 连通分量 / 装配门 | `audit_connectivity` / `audit_gate` | 同上 |
| 同名资源 / 孤儿清理 | `audit_duplicates` / `audit_purge_orphans` | 同上 |
| 跨件 / **跨文件**干涉与重叠（v0.9.1） | `audit_interference` / `audit_overlap` | 同上，给 `file_a` / `file_b` |
| 形状漂移 / 测量包 / 浮块 | `audit_drift` / `audit_measure` / `audit_snap_floaters` | 同上 |
| 参考图 vs 渲染剪影对拍 | `blender_rt_plan(op="qc_compare", args={...})` | `preload="qc"` → `K.dsh_qc_api["compare"](ref, box, render, label=…)` |
| 多视角 QC 出图 | `qc_render_views` | **`blender_rt_headless(shots=[...])`（v0.9.3 新）**，或 `preload="qc,qc_render"` |
| 拼图（L2 证据） | `blender_rt_plan(op="montage")` | `preload="montage"` → `K.dsh_montage_api["montage"]({...})` |
| 交付导出 + 逐项核对 | `deliver_export` / `deliver_verify` | `preload="deliver"` → `K.dsh_deliver_api['export']({...})` / `['verify'](dir)` |
| 机构关节 / 扫掠 / URDF | `motion_*` | `preload="motion"` → `K.dsh_motion_api(...)` |
| 生成器复现 + 对拍 | `generator_run` | `preload="generator"` → `K.dsh_generator_api["dispatch"]("run", …)` |
| 契约层（包络 / 干涉 / 三态 / 账本） | `blender_rt_plan(op=…)` | `preload="contract"` → `K.dsh_contract_api["dispatch"](op, args_json)` |
| 规划器（对象图 → bpy） | `blender_rt_plan(op="plan_build")` | `preload="planner"` → `K.dsh_plan_api["dispatch"](op, args_json)` |
| 渲染性能预设 / 对象精简 | `rt_perf` / `rt_opt` | `preload="perf"` → `K.dsh_perf_api["apply"]` / `["opt_join"]` |
| 事务 / 配方库 | `rt_txn` / `rt_preset` | `preload="txn"` / `preload="preset"` |
| 内环搜索 | `rt_loop` | `preload="runner"` → `K.dsh_loop_api["start"](json)` |

> 记忆点：**preload 的名字 = runtime 下的模块名**（`view, perf, runner, contract, planner, txn, qc, qc_render, preset, audit, montage, motion, deliver, generator`）。
> `K.dsh_<name>_api` 既支持 `api["fn"](...)`（老写法，返回字符串），也支持 `api("fn", {...})`（v0.9.1 起返回**已解析的 dict**）。

**① 网格体检（不跑 doctor、不 Connect）**

```python
blender_rt_headless(
  file="/mnt/d/work/aurora/parts/hull_aft.blend",     # WSL 路径可以（v0.9.3）
  preload="audit",
  script="""
import json
r = K.dsh_audit_api('connectivity', {'scope': 'Hull_Aft', 'micro_gap_mm': 2.0})
print('HEADLESS ' + json.dumps(r, ensure_ascii=False))
""")
```

**② 跨文件干涉（分件方并行时最常用）**

```python
blender_rt_headless(
  preload="audit",
  script="""
import json
r = K.dsh_audit_api('interference', {
    'file_a': '/mnt/d/work/aurora/parts/05_barrel.blend',   'a': '05_barrel',
    'file_b': '/mnt/d/work/aurora/parts/07_handguard.blend','b': '07_handguard',
    'samples': 200000})
print('HEADLESS ' + json.dumps(r, ensure_ascii=False))
""")
```

**③ 剪影对拍（`qc_compare` 的 headless 形态）**

```python
blender_rt_headless(
  preload="qc",
  script="""
import json
r = K.dsh_qc_api["compare"]("/mnt/d/work/ref/ref_front.png", [9, 38, 444, 545],
                             "/mnt/d/work/out/front.png", label="front",
                             out_dir="/mnt/d/work/out/qc")
print('HEADLESS ' + (r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)))
""")
```

**④ 多视角出图（`shots`，v0.9.3）**

```python
blender_rt_headless(
  file="/mnt/d/work/aurora/Aurora.blend",
  outdir="/mnt/d/work/out/shots",
  as_job=True,                     # 3 视角 1024×576 必然超过等待窗口 → 直接后台化
  shots=[
    {"name": "iso",   "from": [9, -9, 6],  "look_at": [0, 0, 1], "lens": 35, "res": [1024, 576], "samples": 64},
    {"name": "front", "from": [0, -14, 2], "look_at": [0, 0, 1], "lens": 50, "res": [1024, 576], "samples": 64},
    {"name": "top",   "from": [0, 0, 16],  "look_at": [0, 0, 1], "ortho": True, "ortho_scale": 16, "res": 1024},
  ])
# 回执 res.shots = [{name, path, ms, bytes, md5, coverage_estimate, warning?}]
```

**⑤ 交付导出（`deliver_export` + `deliver_verify`）**

```python
blender_rt_headless(
  file="/mnt/d/work/house/House.blend", preload="deliver",
  script="""
import json
print('HEADLESS ' + json.dumps(K.dsh_deliver_api['export'](
    {'dir': '/mnt/d/work/deliver/house', 'scope': 'House', 'name': 'house'}), ensure_ascii=False))
print('HEADLESS ' + json.dumps(K.dsh_deliver_api['verify']('/mnt/d/work/deliver/house'), ensure_ascii=False))
""")
```

### 4.2 GUI 通道什么时候才值得用

**判据：下面三条任一为真才 Connect；三条都不为真就走 headless。**

| # | 条件 | 典型动作 | 为什么非 GUI 不可 |
|---|---|---|---|
| ① | **人在回路**：需要「看一眼结果再决定下一步」 | `rt_see` → 判断 → `rt_do {see:true}`（≈105 ms/步） | 交互延迟 55–105 ms vs headless 冷启动 1 s+ 导入 |
| ② | **live 场景本身就是产物**：改的是用户手上那个没保存的场景 | `rt_do` / `rt_loop` / `rt_txn` | headless 打开的是磁盘上的 .blend，改不到内存里那份 |
| ③ | **需要 GUI 上下文** | 依赖 `bpy.context.screen` / 视口 / 模态的 `bpy.ops`；`gui_frame` / `gui_shading` / `gui_open`；`rt_see(full=true)` 看整窗 UI | 无头进程 `bpy.context.screen is None` |

辅助判断（不单独构成理由，但可以叠加）：

- `rt_watch`（时间窗采样 + 逐帧 hash）：判断「动没动」最便宜 —— 但无头里也能跑 `dsh_stage` + 日志增量；
- `rt_perf(op="analyze")`（差分实测占主线程十几秒）：只在你要在**当前 GUI 会话**里马上改设置时才划算；
- 多会话共存要 `blender_viewport {op:"who"/"lease"}`：**只读 op 不需要**。

### 4.3 任务模板里怎么写「本任务是否需要 GUI + Connect」

把这一段直接复制进任务书（或派活 prompt）开头，**填空即用**：

```markdown
## 执行约定
- GUI: **否**          # 否 | 是；填「是」→ 开工前必须 blender_viewport(op="doctor") 得到 kind=ok 才继续
- 通道: headless       # headless | gui | 混合
- 预期时长: ~8 min     # >100 s 一律 as_job=true（或让默认等待窗口到点转 promoted）
- 产物目录: /mnt/d/work/out/<task>/    # 不要写 /tmp（见 操作教程 §7.5）
- 结果契约: print("HEADLESS " + json.dumps({...}, ensure_ascii=False))
```

三种任务的填法：

```markdown
# A. 一次性脚本（几何自检 / 数据校验，2–5 s）
- GUI: 否 | 通道: headless | 预期时长: ~3 s | as_job: 不需要
- 调用: blender_rt_headless(preload="audit", script="…")

# B. 长渲染（多视角 / 成品图，分钟级）
- GUI: 否 | 通道: headless(作业层) | 预期时长: ~20 min | as_job: **是**
- 调用: blender_rt_headless(file=…, shots=[…], as_job=True) → 收 {kind:"promoted", jobId} 或直接 jobId
- 观测: blender_rt_job(op="status", id="run-…") 看 stage / idleMs

# C. 交互式建模（用户开着 Blender 在等）
- GUI: **是**（先 doctor → kind=ok）| 通道: gui | 预期时长: 每步 ~105 ms
- 调用: blender_rt_do(code=…, see=True)；长渲染仍丢给 headless，别占主线程
```

> **红线**：填「GUI: 是」的任务，开工第一件事是 `doctor`；拿不到 `kind=ok` 就**降级为 headless 或停下报错**，
> 不要靠连发命令试出来（这正是本轮 6/7 协作者的摩擦来源）。

### 4.4 旧写法 → 新写法对照

| 旧（v0.9.2 习惯） | 新（v0.9.3） |
|---|---|
| 外层 `run_code` 不传 `timeoutMs`，120 s 后只回一行 deadline 报错 | 传 `as_job=true`；或直接收 `{kind:"promoted", jobId}` 再 `op=collect` |
| 手写正则 `result: (\{.*?\})` 抠 JSON | `JSON.parse` 第 1 个 text block（单行信封） |
| `rt_job(op="start")` + wrapper 脚本 + JSON 参数文件传参 | `op="start"` 直接收 `script_file` / `args` / `env` |
| 循环 `op=status` 轮询（撞 harness 的 repeated-call 检测） | `op="wait"`（阻塞到完成或超时，返回结构化回执） |
| 手拼 `\\wsl.localhost\Ubuntu\…` UNC 字符串 | `outdir="/home/…"` 直接给 WSL 路径 |
| 每个建模方自写 50~60 行相机 / 三点光 / AgX 样板 | `shots=[{name, from, look_at, lens, res, samples, ortho, ortho_scale, margin}]` |
| 20 分钟任务全程黑盒（日志 0 字节） | `PYTHONUNBUFFERED=1` + `dsh_stage("building")` → `op=status.stage` |
| 手写世界顶点哈希 / BVH 浮空扫描 | `audit_duplicates` / `audit_snap_floaters` / `audit_connectivity`（§4.1） |

### 4.5 长活的三条出口（记住这一张表）

| 情形 | 怎么起 | 怎么收 |
|---|---|---|
| 预期 < 等待窗口（默认 100 s） | `blender_rt_headless(script=…)` | 直接拿第 1 个 block 的信封（`status:"finished"`） |
| 预期 > 100 s | `blender_rt_headless(…, as_job=True)` | 回 `{kind:"promoted", jobId:"run-…"}` → `blender_rt_job(op="collect", id=…)` |
| 想等一会儿再说 | `blender_rt_headless(…, as_job=True, wait_s=15)` | 15 s 内完成就当场给结果；没完回 `{kind:"promoted", jobId}` |
| 已经跑起来了 | — | `op="wait"`（阻塞）/ `op="status"`（看 stage、idleMs）/ `op="kill"`（幂等） |

`run-…` 与 `job-…` **同一个 id 空间**：`status / collect / kill / wait` 都收两种前缀。

### 4.6 长任务可观测（脚本侧契约）

```python
# 脚本里打阶段心跳（bootstrap 注入的 dsh_stage，等价 K.progress）
dsh_stage("loading")          # → 插件侧解析出 stage="loading"
dsh_stage("building", part="hull_aft")     # 可带自定义键
for i, ob in enumerate(objs):
    ...
    dsh_stage("rendering", done=i + 1, total=len(objs))
```

插件侧把最后一条解析进 `op=status.stage`（同时给 `stageAt` / `lastOutputAt` / `idleMs` / `lines` / `pidAlive`）；
配合三处 spawn 已注入的 `PYTHONUNBUFFERED=1`，`<jobdir>/stdout.log` 在**运行中**就有增量。

```python
blender_rt_job(op="status", id="run-abc123")   # → stage / idleMs / pidAlive
blender_rt_job(op="wait",   id="run-abc123", timeout_ms=600000)
blender_rt_job(op="kill",   id="job-不存在的id")   # 不报错：回「已结束 / 不存在」
```

---

## 5. 迁移检查单（接手会话照着走）

1. 任务书里先填 §4.3 那五行（GUI / 通道 / 预期时长 / 产物目录 / 结果契约）。
2. 「GUI: 否」→ **不要**跑 `doctor`，直接 headless；需要体检就按 §4.1 ①②③ 抄。
3. 预期 > 100 s → `as_job=True`；回执拿到 `jobId` 就先记下来，再决定 `wait` 还是干别的。
4. 回执一律按「两个 text block」解析（信封 + 摘要），不再写正则。
5. 临时文件落**工作区**，不落 `/tmp`（理由见 [操作教程.md](操作教程.md) §7.5）。
6. 读图量测按 [证据分级与读图纪律.md](证据分级与读图纪律.md) §6/§7（`[::-1]`、BVHTree、provenance）。

---

## 6. 边界与反悔条件

**本决定不成立（应当回到 GUI）的情形**：

- 任务的**中间判断**必须由人看着画面做（审美微调、手动摆位）；
- 产物就是**用户内存里那个未保存的场景**；
- 依赖 GUI 上下文的 `bpy.ops` / 模态操作 / 视口截图。

**反悔条件（出现任一就重新评估本决定）**：

1. 「必须在 live 场景上做」的任务占比 **> 50%**；
2. headless 的冷启动 + 导入成本成为瓶颈（→ 先用 `blender_rt_worker` 热会话，仍不够再谈）；
3. GUI 通道出现 headless 做不到的**新能力**（例如只有 GUI 能拿到的渲染/交互特性）。

**并行的硬约束**（不因本决定改变）：多进程同时渲 EEVEE 会抢同一块 GPU ——
`qc_render_views` 已内置 `render_lock`（`<workdir>/locks/render.lock`，默认等锁 120 s），
分件方**不要**自造渲染锁。

---

## 7. 验收：怎么确认这条路线真的生效

**① 不开 GUI、不 Connect，也能出体检报告**（复制即跑）：

```python
blender_rt_headless(preload="audit", script="""
import json
print('HEADLESS ' + json.dumps(K.dsh_audit_api('selftest'), ensure_ascii=False))
""")
# 期望：信封 status="finished"，resultJson.ok 为真 —— 全程没有任何 9876 交互
```

**② promoted 语义**（D1 的验收，等价于交接文档 §8 第 1 条）：

```python
blender_rt_headless(script="import time; time.sleep(200); print('HEADLESS {"a": 1}')", timeout_ms=900000)
# 期望：等待窗口（默认 100 s）内先回 {kind:"promoted", jobId:"run-…"}，而不是 deadline 报错
blender_rt_job(op="collect", id="run-…")     # → resultJson.a == 1
```

**③ 长任务可观测**（D5 的验收）：

```python
blender_rt_headless(script="""
import time
for i in range(10):
    dsh_stage('tick', i=i); time.sleep(2)
print('HEADLESS {"done": true}')
""", as_job=True)
# 期间：blender_rt_job(op="status", id=…) 的 stage 在变、idleMs 在涨；stdout.log 有增量
```

三条都过 = 本路线在你的环境里成立。
