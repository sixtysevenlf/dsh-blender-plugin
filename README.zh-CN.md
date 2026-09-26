# DSH × Blender 直连实时插件 · 分享版

> 🌐 **语言 / Language：** [English](README.md) · **简体中文（本页）**
>
> 🧩 **配套 skill：[blender-modeling](https://github.com/sixtysevenlf/dsh-skill-blender-modeling)** —— 建模 / 装配的流程与判据（多 Agent 分工范式、参考图形体还原、六条坑、数值门装配审计）。
> 插件管"通道"，skill 管"怎么建、怎么验收"，两个一起用才完整。

> 让 **AI 模型真正驱动 Blender**：不用人点鼠标、不截屏喂图、不装 MCP 服务端，
> 通过一条 TCP 直连通道拿到 10 个原语：**看视口 / 改场景 / 连续观察 / 内环搜索 / 渲染优化 / 对象精简 / 无头跑重活 / 通道运维**。
>
> 版本 **0.9.4** —— 修返回通道（P0）+ 一键拉起 Blender + 死会话租约回收（[落地记录](docs/feedback/改进落地记录-2026-09-25.md)）：
> **⓪ 返回通道 P0**：v0.9.3 的回执里有一个 `promoted: undefined`，被宿主的 lossless-JSON 门拒收 →
> `blender_rt_headless` **每次都失败**、`rt_job` 的 status/collect/wait 全部收不回结果（**不是非有限数**）；
> 现在所有工具出口统一消毒（undefined→丢键 · NaN/±Infinity→null · -0→0），且改动**不静默**。
> **⑦ 一键启动**：`blender_viewport(op="launch")` —— agent 没有人手点「N 面板 → Connect」，这条把
> "启动 GUI Blender + 让 addon 起 server"固化成一个幂等调用（写 boot 脚本 → spawn → 轮询 9876 → 顺手 doctor）。
> **⑧ 死会话租约自动回收**：会话崩掉后它的写租约不再把别人的写通道挡到 TTL 结束（按 `plugin-pid-<pid>` 探活，判死即回收）。
>
> 0.9.3 的内容（headless 第一路径等）：
> **① 路线决定：headless 批处理 = 第一路径**（[决策文档](docs/headless优先-路线决定.md)；GUI 直连降为「改一步看一眼」的增益，不删工具）。
> **② 超时不再吞结果**：headless 的客户端等待窗口（`DSH_HEADLESS_WAIT_MS`，默认 100 s）到点回 `{kind:"promoted", jobId:"run-…"}`，用 `blender_rt_job(op="collect"|"wait", id=…)` 收；预期 >100 s 请直接 `as_job=true`。
> **③ 回执结构化**：第 1 个 text block 是单行 JSON 信封（可直接 `JSON.parse`：`status/resultJson/resultPath/resultTruncated/stdoutTail/inputFile/shots/pathWarnings/…`），第 2 个是人读摘要 —— 不必再手写正则。
> **④ 长任务可观测**：三处 spawn 注入 `PYTHONUNBUFFERED=1`；脚本里 `dsh_stage("building")` 打心跳，`op=status` 回 `stage/idleMs/lines/logBytes/pidAlive`。
> **⑤ 路径**：`outdir/out_json/file` 收 WSL 路径并回传两种真实路径；`shots=[…]` 一次出多视角（带 `coverage_estimate` 与 <5% 告警）。
> **⑥ 作业层**：`op=start` 与 headless 同形参（`script_file/args/env/…`），新增 `op=wait`，run 与 job 同一 id 空间，stale 不再谎报 running。
> 验收：`npm run test:acceptance`（62 条断言 / 真机 Blender 5.2.2）。
>
> 版本 **0.9.2** —— DSH 更新适配（取消/超时契约 + 宿主 API 自证 + 图片静默失败 + bundle 声明）。见 [docs/DSH-更新适配.md](docs/DSH-更新适配.md)。
>> 版本 **0.8.0** —— 默认渲染引擎改为 **EEVEE + 光追**（纯 GPU、不依赖设备偏好；实测迭代帧 1.35 s vs Cycles GPU 3.13 s = 2.3×，Cycles 仍可选）。 —— 按外部实测反馈加固：**GPU 语义**（无头 Cycles 静默回落 CPU，实测 **15.4×**）、**热无头会话**（`blender_rt_worker`）、**长任务流式**（客户端 `fetch` 响应头超时 300 s，实测 `UND_ERR_HEADERS_TIMEOUT`）、结构化失败、产物过滤。见 §4.6。
>
> 版本 **0.5.0** —— 新增 **契约层**（假设/区间/校验/证据/破坏性门控）与 **规划器**（Component·Connection·Feature 对象图编译到 bpy），对应新工具 `blender_rt_plan`。见 §4.5。
>
> 版本 **0.4.1** —— 新增 **addon 协议适配**（`addonProtocol`，默认 `auto`）：扁平协议的官方 `MCP for Blender` 与 `harveyxiacn/blender-mcp` 的 category/action addon 都能用（由 [@yihefeikong-rgb](https://github.com/yihefeikong-rgb) 贡献，PR #2）。
>
> 版本 **0.4.0**（分享版）· 自带 runtime（node + python），包内路径全相对，**换机器不用改源码**。
> 作者自用版把这些配置写死在本机路径上；分享版全部走 `runtime/config.mjs`（自动探测 + 配置文件 + 环境变量）。
> **操作教程：[docs/操作教程.md](docs/操作教程.md) · 配置：[docs/配置参考.md](docs/配置参考.md)**

---

## 1. 30 秒速览：这包能干什么

| 能力 | 工具 | 典型耗时 |
|---|---|---|
| 看一眼现在的视口 | `blender_rt_see` | 50–100 ms |
| **从任意角度出图**（不建相机、不动用户视口） | `blender_rt_see {from, look_at, ...}` | 85–280 ms |
| 改一步 + 立刻验证 | `blender_rt_do {see:true}` | ≈105 ms |
| 判断"动没动 / 对不对" | `blender_rt_watch` | ≤6 帧/次 |
| **内环搜索**（几千次迭代不花模型回合） | `blender_rt_loop` | 160 tick/s |
| 透传 addon 任意命令 / 查命令面 | `blender_rt_cmd` / `blender_rt_commands` | 25–55 ms |
| 渲染性能诊断与优化预设 | `blender_rt_perf` | analyze 十几秒 |
| 对象精简（合并，几何零损失） | `blender_rt_opt` | ≈1.4 ms/对象 |
| **无头进程**跑重渲染 / 批量几何 | `blender_rt_headless` | 冷起 0.8 s |
| **契约层 + 规划器** | **`blender_rt_plan`** | 307 对象：AABB 扫描 24 ms · BVH 0.17 ms/对 |
| **热无头会话** | **`blender_rt_worker`** |
| **事务 / 回滚** | **`blender_rt_txn`** | 快照 96.7MB / 824ms（300 对象）· mark→revert 实测通过 | 常驻 `blender -b` 复用（免去每次 0.9–1.2 s 冷启动；`K` 跨调用保留） |
| **配方库** | **`blender_rt_preset`** | 参数配方 save/apply/export；实测 720 → 640 生效 |
| 通道体检 / 租约（多会话共存） | `blender_viewport` | 体检 70–100 ms |

---

## 2. 前置条件（5 条，缺一不可）

1. **Blender 4.x / 5.x（GUI 模式）** —— 本通道要的是"能看见的 Blender"；后台 `-b` 只用于无头工具那条线。
2. **Blender 里的 addon：`MCP for Blender`**（**不在本包内**，需自备）：
   - 它在 Blender 内起一个 TCP 服务，默认监听 `127.0.0.1:9876`；
   - 至少提供这些命令：`ping`、`get_scene_info`、`get_world_state_snapshot`、`get_object_info(name)`、`get_viewport_screenshot(max_size, filepath, format)`、`execute_code(code)`；
   - 另有 5 个可选集成（PolyHaven / Hyper3D / Sketchfab / Poly Pizza / Hunyuan3D），开关由 addon 自己的 scene 属性控制。
   - **也支持 harveyxiacn 增强版 `blender_mcp_addon`**（协议不同，插件侧自动适配，配置项 `addonProtocol`，默认 `auto`；见 `docs/配置参考.md` §6）。该实现没有上面那 5 条资产集成通道，相关命令会明确报错。
   - 装好后：3D 视图按 `N` → 找到「MCP for Blender」面板 → **Connect**。
   - 兼容性自检：`node runtime/_probe_tools.mjs`（逐条探测命令，输出 ok / 耗时）；协议自检：`node tests/protocol_selftest.mjs`（不需要 Blender）。
3. **Node.js ≥ 20**（跑后端与插件宿主）。
4. **DSH（DeepSeek Harness）**：本包以 DSH 插件形态提供工具（`inject: ['tools']`）。
   - 若你在 **WSL** 里跑 DSH、Blender 在 Windows：需要 WSL 互操作开启（默认开），这样 `spawn` 能直接起 `blender.exe`；
   - 若 DSH 与 Blender **同在 Windows**：把 `blenderExe` 配成 `D:\...\blender.exe` 即可，路径映射自动退化。
5. **macOS** —— 已支持。宿主与 Blender 同机，不涉及跨 OS 路径映射：Blender 自动探测 `/Applications` 下的 `Blender*.app`（也扫 `~/Applications` 与 `/Volumes/*/Applications`），工作目录默认 `~/.dsh-blender-rt`。只有装在非标准位置时才需要配 `DSH_BLENDER_EXE` / `blenderExe`；`blender_viewport op=doctor` 会打印探测结果。

---

## 3. 安装（5 步）

```bash
# ① 构建（需要指向一个 DSH 源码 checkout，用于链接 cordis/schemastery/dsh-tools）
cd dsh-blender-plugin
DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh          # 产出 lib/

# ② 配置（不设也能靠自动探测跑；要改就复制样例）
cp dsh-blender.config.example.json dsh-blender.config.json        # 见 docs/配置参考.md

# ③ 装到 DSH（本包已声明 dsh.bundle → 走官方 bundle 装配）
#    a) 软链进 <profile>/node_modules/@dsh-external/dsh-blender-plugin
#    b) profile package.json：dependencies 加 "link:<本目录绝对路径>"，
#       dsh.profile.bundles 加 "@dsh-external/dsh-blender-plugin"
#    c) 预演：dsh --profile <profile> --dump-config → 重启 DSH

# ④ 启动 Blender + Connect addon —— 两条路：
#    agent / 无人值守：blender_viewport(op="launch")   ← v0.9.4：写 boot 脚本 + spawn + 轮询 9876，幂等
#    人工：启动 Blender → 按 N →「MCP for Blender」→ Connect（监听 127.0.0.1:9876）

# ⑤ 验证（三条命令；doctor 是关键）
curl -sS http://127.0.0.1:9877/health      # 后端活着
curl -sS http://127.0.0.1:9877/doctor      # ⭐ 真跑一次 bpy 往返 + 打印生效配置：kind=ok 才算通
curl -sS http://127.0.0.1:9877/who         # 租约 + 通道指标 + config
```

插件加载时会自动拉起后端（`node runtime/server.mjs`，默认 `127.0.0.1:9877`）并带 **15 秒看护**：进程被杀 / 宿主重启会自动拉起；`blender_viewport op=stop` 会暂停看护。

---

## 4. 第一次使用：三条命令建立信心

```text
blender_viewport(op="doctor")                                # ① 体检：通道健康 + 生效配置
blender_rt_see(max_size=560)                                 # ② 看一帧：模型眼里的视口
blender_rt_see(from="9,-9,6", look_at="0,0,1")               # ③ 换个角度：不打扰你正在看的视口
```

期望：① `kind=ok`（含 `roundTripMs` / `ping_ms` / `config`）；②③ 返回内联图片 + 毫秒数与 hash。

---

## 4.5 契约层 + 规划器（v0.5.0）

回答"白盒化"：*模型凭什么说这么建是对的？证据不够时怎么办？*

- **契约 op**（`op=…`）：`register_component` / `register_connection`（候选类型、参数区间、禁止项、置信度、所需证据）/ `register_envelope`；
  `check_envelope` / `check_interference`（AABB 扫描 + BVH 精查）/ `check_interface`；
  `destructive_guard` —— 连接**未判别**时拦下 `boolean_union / weld / merge / apply_transform`（返回 `Unsupported Destructive Merge`）；
  `evidence` / `ledger`（每次证据记 **md5**）/ `report`（provenance 报告）。
- **假设生命周期**：`verify` 出三态 —— `supported` / `refuted` / **`unresolved`**（外部误差达标但决定性问题不可辨识）；`flip` / `advance` 记历史。
- **规划器 op**（`plan_…`）：`plan_load` / `plan_validate` / `plan_order` / `plan_build`（先 `dry_run`）/ `plan_graph`（mermaid/dot）；
  对象图 = Component（box/cylinder/sphere/mesh_copy）· Connection（候选/状态/禁止项/偏移）· Feature（array/grid/mirror）；
  IDE 式诊断：`UnresolvedConnection` / `UnsupportedDestructiveMerge` / `MissingComponent` / `Cycle` / `ParamOutOfRange`；硬错误直接拒绝编译。

**最关键的一条**：两个假设对可见证据的解释力相同时（实测残差 **184 vs 184**），判定必须是 `unresolved` **+ 还需要什么探针**，不是二选一。
带数字的完整例子见 `docs/假设驱动建模-cookbook.md`，可在 `docs/examples/chair-backrest/` 里 3 秒复跑；自检 `tests/contract_selftest.py`（24/24）与 `tests/plan_selftest.py`（18/18）。

## 4.6 外部反馈加固（v0.6.0）

来自重用户（22 轮建模、45 次流水线）的四条问题，全部复现并修掉：

| 问题 | 实测 | 处置 |
|---|---|---|
| 无头渲染**静默跑 CPU** | 1821 对象工程 480×270/32spp：**CPU 2.78 s vs GPU 预热 0.18 s = 15.4×**；`--factory-startup` 清偏好，且 `factory_startup=false` 也没继承到 | 默认注入 **GPU 前导**（`gpu:"auto"`，OPTIX→CUDA→HIP→ONEAPI→METAL），每个结果回传 `gpu` 字段（before/after/configured/fell_back_to_cpu）；`use_user_config:true` 透传 `BLENDER_USER_CONFIG/SCRIPTS`；`gpu:"true"` 没 GPU 就 `ok=false` |
| 没有**热无头会话** | addon 的 socket 服务在 background 直接 return，无头只能冷启动 | 新增 `runtime/worker.py` + 工具 **`blender_rt_worker`**：常驻 `blender -b`，**阻塞 accept 跑主线程**（无头下 timers 实测 0 次触发），复用**持久内核 K**（`K.n` 跨 exec 保留） |
| 长调用 `fetch failed` | 客户端 `fetch` **响应头超时 300 s**（独立实验：延迟 330 s → `UND_ERR_HEADERS_TIMEOUT`）；且客户端断开**不会**杀掉服务端子进程（产物仍落盘） | `/headless`、`/worker` 改 **ndjson 流式**：响应头立刻返回 + 15 s 心跳 + 最后一行是结果 |
| 失败信息不结构化 / 产物有噪音 | — | 全量 stdout/stderr 落盘（`logs.*`）、抽 `lastException` 与 `traceback`、给 `reason`；脚本里出现字面量 `\n` 时给转义提示；`outdir` 默认过滤 `__pycache__ / *.pyc / *.blend1|2 / tmp*` 并回传过滤计数 |

**长任务语义**：客户端超时/断连 ≠ 任务失败（子进程继续跑完，产物在 `outdir`，日志路径在 `logs`）；超 5 分钟优先用热会话。

### 4.7 事务 / QC / 可观测性（v0.7.0）

- **事务**：新工具 **`blender_rt_txn`** —— 文件级 `snapshot`/`restore`（`copy=True` 不动当前 filepath；300 对象工程实测 **96.7MB / 824ms**）、对象级 `mark`/`revert`（transform/材质/可见性/修改器开关；**不含拓扑/UV 改动**）。
- **内置 QC**：`runtime/qc.py`，入口 `blender_rt_plan(op="qc_compare")`（另有 `qc_compare_basic` / `qc_self_check` / `qc_robustness_check` / `qc_help`）。相对参考实现做了优化：自适应掩膜（alpha 判定 / 边框估背景 + Otsu）、质心对齐 + 尺度平移搜索、可行动指标（Dice/缺面积/多面积/边界距离/剖面差）、防刷分（`iou` vs `iou_fixed` + 尺度漂移告警 + 内环固定对齐）。自检：自比 **1.0000**、平移 12px 仍 **1.0000**；扰动 12px+6% 时朴素比法塌到 **0.534**、搜索对齐保持 **0.866**。
- **流式回执修复**（v0.8.9）：`/headless` `/worker` `/txn` `/preset` 在结果前**每 15 s 先写一行心跳**（NDJSON），而客户端旧实现整段 `JSON.parse` → **任何超过 15 s 的调用都会降级成 `exit=undefined`，尽管子进程已 exitCode=0 跑完**。现在客户端按行取最后一条非心跳 JSON，并回报心跳条数。实测：20 s 脚本从 `HEADLESS 失败 · exit=undefined · undefinedms` 变成 `HEADLESS ok · exit=0 · 21406ms`（result/日志齐全）；20 s 的 `rt_worker op=exec` 返回 `WORKER exec ok · 20000ms`。同版本还修：作业层支持 `preload`、`args` 引号分词、读图零尺寸守卫（GIF 被 Blender 静默读成 0×0）、两条错误提示（`open_mainfile` 后的失效 `StructRNA` 引用、`context is incorrect`）、>60 s 自动提示 `as_job`。
- **多视角渲染 harness**（v0.8.8，P2-1）：`runtime/qc_render.py`，入口 `blender_rt_plan(op="qc_render_views", args={file, views[], res, samples, budget_s|thr, outdir, ref_path})`。一次调用出 N 张：按所有可见 mesh 合并 AABB **自动取景**（逐角解算 + `margin`）、临时建**固定三点光**（SUN key 3.2 / fill 1.0 / rim 2.4，角度固定且相对相机方位角；调用内建、出图后删除并还原场景设置）、逐张 PNG + 计时 + md5、写 `<outdir>/render_views.jsonl`（每行 `{view, ms, bytes, hash, frame{bbox, margin_px, coverage}}`）。累计 `budget_s` 超限**立即停**并标 `within_budget=false`（已出的图保留）；给 `ref_path` 时逐张走 `qc_compare`（默认固定对齐）。`args.asJob=true` 自动转作业层（无头进程 = 场景天然隔离）。350 可见 mesh 工程实测（512×512 / 64 采样 / EEVEE+RT）：iso **2124ms**（首张含着色器编译）、front **798ms**、right **565ms**，3 张共 **3487ms**，jsonl 3 行，512 画幅下四边余量 36px（预测 bbox 与实出 alpha bbox 差 ≤2px）；预算 1.0s 的对照跑出一张即停、`within_budget=false`、图保留。
- **可观测性**：异常也回传 partial `stdout`/`stderr`/`traceback`（标记包裹）；执行类错误不再被误报为 `main-thread-busy`；`rt_do` 报告主线程占用，>1s 提示改走 headless/worker。
- **路径**：Blender 内可用 `K.win_path / K.wsl_path / K.blend_path / K.out_dir`（GUI 与无头通用），另有 `K.run(path, reload_modules=True)` 与 `blender_rt_do(file=...)`。

### 4.8 装配级判据、数值化漂移与判定失效（v0.9.0）

来源：对 [SpatiaOS/Procedura](https://github.com/SpatiaOS/Procedura)（MIT）逐文件精读后的移植评估 —— **只搬判据与算法，不搬技术栈**（不引 OpenSCAD / Isaac）。评估表与逐项落地状态见 `docs/Procedura-融合分析.md`。工具数不变（仍 15 个），新能力全走 `blender_rt_plan` / `blender_rt_txn` 的 op 面。

- **★ 修一个真 bug**：`docs/` 一直文档化的 `blender_rt_plan(op="audit_mesh"/"audit_scene"/"audit_duplicates")` **从来没接通**（实测返回 `unknown contract op`，`audit.py` 只在 preload 通道可达）。本版把 `audit_*` 与 `montage` 正式接进引擎路由。
- **装配级连通门**：`audit_connectivity` / `audit_gate` —— 判据是**每个分量都问「你有没有跟别的分量相接」**（不再用「最大分量=主体、其余都是浮块」，两个等大零件时那条规则会随机挑一个当浮体）。可见浮块 = 最长 bbox 边 ≥ 全模型 1%；**bbox 粗筛 + BVH 网格级复核**（多对象装配里 bbox 普遍重叠 → 间隙恒为 0，只看 bbox 会一律放行）。口径 `micro_gap_mm` 随返回回传：**0.3 mm = 单一实体/3D 打印**，带设计间隙的装配件按工艺给（1–2 mm）。实测（14 对象 / 97,438 面）：0.3 mm 口径 → **178 浮块 / 970 相接**；2 mm 口径 → 63。面数超 50 万跳过复核 → 门**降级**（`ok=null`），未确认不得读作已通过。
- **数值化漂移**：`audit_drift` 是对称 Chamfer **形状**漂移（点到曲面）：20k 采样 **21,397 ms → 46 ms**，同网格读 **0.000000**。⚠ 归一化会消除平移与整体缩放 → 位移看 `bbox_delta.moved_mm`。另有 `audit_measure`（bbox + 逐轴间隙/重叠 mm/% + 邻居）与 `audit_snap_floaters`（默认只报告，只对「整件属于该浮块」的对象平移，不撕焊接体）。
- **判定会过期**：`fingerprint` 绑几何摘要；`ledger` 逐条对拍标 `stale`；`verify` 记 bbox 快照，之后漂移 >10% 对角线 / >20% 尺寸 → `supported` **自动降级 unresolved**（带动画的对象跳过）。
- **配合门与干涉**：`mate_check` 在**实际网格**上量接触面积占比 / 单边中位间隙 / 侵入深度（四档 fit：clearance 0.25 · location 0.15 · press −0.05 · snap 0.20 mm 单边），`fit_help` 读 `runtime/assembly_features.json`，`interference_report` 报深度/体积/严重度并对已注册连接豁免（超 `depth_eps` 仍报）。实测单边 **0.1498 mm**（设计 0.1500）→ supported。
- **编辑纪律与交付**：`blender_rt_txn` 新增 `edit_begin / edit_check / edit_accept(verified) / edit_revert(why) / edit_status`（接受必须有复核 + 一句说明；撤销免预算但有上限 + 防抖）；`deliver_export / deliver_verify`（单位盒归一化 + 多组 OBJ/MTL + manifest md5，改一字节必 FAIL）。
- **渲染 harness 三件**：`mode="parts_color"`（逐部件配色 + 图例 + 出图后逐项还原）、jsonl 每行**实测 `device`**（防 Cycles 静默回落 CPU ≈7×）、`qc_render_catalog`（22 视角 / 别名 / 未知名回传，不再静默当 iso 渲）。
- **机构**：`motion_*` —— 关节轴/锚点**实测**（旋转对称 + 接触带两条独立证据，冲突一律 unresolved 并把两条轴都报出来）、**扫掠验证**（运动学+BVH，不是物理仿真；零干涉且确实位移 → supported，撞上就点名）、URDF/USDA 导出（单树合成、退化关节降级并告警、结构自检）。自检 25/25。
- **生成器（代码化建模轻量版）**：`generator_save / generator_run / generator_list|get|diff` —— 程序即形状 + **全新无头进程复现当编译门** + 源码 hash 缓存 + 源码一变回执过期。（本机两侧都没装 OpenSCAD；真接它需要 Manifold 后端。）
- **可追溯**：每次顶层调用写 `<工作目录>/trajectory/blender_rt-<日期>.jsonl`（route/op/ms/ok/参数摘要，超 16MB 轮转；`DSH_TRAJ=0` 关）。
- **顺带修的基础设施问题**：preload 多模块互相覆写 helper（engine 改为各占命名空间）；headless `ok:true/exit=0` ≠ 脚本成功（Blender 异常时退出码仍 0）；源码注入通道没有 `__file__`。
- 自检：contract **33/33** · mate **57/57** · txn **55/55** · deliver **53/53** · motion **25/25** · qc_render **14+15** · 另有 audit / generator 合成自检。

### 4.9 现场反馈修复（v0.9.1）—— rifle-build《93 反馈》14 条

四条 P0「静默失败」全部复现并修掉；D2（自定义视角回空帧）**未能复现** —— 那是瞄空（`from`/`look_at` 指到了没有几何的地方），
所以修法不是改相机，而是让出图**自诊断**。

- **A1 未知名视角不再静默替换**：`views` 非空且一个都认不出 → `ok:false` + `unknown_views` + `available_views`（22 名目录）+ `hint`，**一张都不渲**；
  计数（`requested_count/rendered_count/unknown_count`）总会给。补拼写别名（`side_left → left`、`iso_right → iso_br`…），
  **语义模糊的一律不猜**（`muzzle_end`/`breech_end`）。省略 `views` 仍是 v0.8.11 的 **3 视角**（iso/front/right）——补丁版不静默改默认产物集。
- **A2 无头 QC 默认 `view_transform="Standard"`**（AgX 洗淡会让阈值判读整轮报废）；beauty 图传 `"scene"` 沿用场景。
  实际值在返回体**和每行 jsonl**都有，出图后场景逐项还原（实测：返回 Standard、场景仍是 AgX）。
- **A3 结构化结果不再只能从 stdout 切片**：`blender_rt_headless(out_json=<path>)`；>4KB 自动落 `<workdir>/results/<runId>.json`（`resultPath`/`resultBytes`）；
  工具文本里 `result` 2KB→6KB；plan 通道 4KB→12KB。
- **A4 假失败分档**：`status ∈ finished / script_error / blender_error / timeout / gpu_required_missing / blender_exe_missing` + `failure_hint`。
  实测：`--python` 脚本抛异常时 Blender **退出码仍是 0**，所以不能只看 exit。每次都有 `runId` —— 客户端超时 ≠ 任务失败，按 id 回收。
- **B1/B2 API 一致性**：`K.dsh_*_api("op", {…})` → **dict**；`api["dispatch"](op, json_str)` 仍是 str。
- **B3 `script_file=<.py>`**（`file=` 传 `.py` 也会自动识别），不必再"写盘→读回→当字符串传"。
- **B4 参数/环境契约**：`env={…}` + 自动注入 `DSH_RUN_ID/DSH_OUTDIR/DSH_ARGS/DSH_SESSION/DSH_PLUGIN_VERSION`，脚本内 `K.args/K.run_id/K.session/K.env` 可读。
  （实测坑：WSL→Windows 的 env 不过界 → 自动设 `WSLENV` + 脚本内再注入一次。）
- **C1/C2 文件级算子**：`audit_overlap(file_a,obj_a,file_b,obj_b)`（BVH 面对 + 真交线段 + 交叠 bbox）、
  `audit_interference(...)`（交集体积 **mm³ + 95% 置信区间 + 三态**；蒙特卡洛射线奇偶校验，只在两侧 AABB 交盒里采样；开放/非流形 → `unresolved` 不给假数）；
  `audit_connectivity/audit_gate/audit_measure` 都支持 `file=`（临时加载 → 同一链路 → 无论成败都清理，`cleanup` 明细可查）。
- **D1 GUI 原语**：`gui_frame(object=…) / gui_shading(mode=…) / gui_open(path=…) / gui_help()` 在**真 UI 上下文**执行
  （`rt_do` 里 `bpy.context.screen` 是 None）。实测 `gui_frame(object="Cube")` → `framed:"Cube"`。
- **D2 自定义视角自诊断**：`coverage_estimate`（底色取众数，`background=true` 骗不过）+ `objects_in_frame` + `scene_bbox`；
  数不到几何时给 `warning{code:"frame_looks_empty", suggest:{from,look_at,lens}}` —— 实测照 suggest 再出一次，覆盖 **0.00% → 68.11%**（不必按 Home）。
  顺带修：`x-dsh-view` 头只带 5 字段、非 ASCII 会 502 → 改 URL 编码。
- **E1 渲染队列/锁**（替代成员自建的 `render_lock.py`）：跨进程文件锁 `<workdir>/locks/render.lock`；
  `qc_render_views` 自动 acquire/release，`waited_ms`/holder/stale 进返回体与每行 jsonl。实测：第二个持有者 `wait_s=1` → 拒绝 + `waited_ms=1001`；过期锁被打破。
- **E2 产物路径带会话**：`DSH_SESSION`（缺省 `plugin-pid-<pid>`）→ evidence 默认 `dsh_evidence_<session>.png`；`status` 里给 `session`/`resultsDir`。
- 集成时还修了三个工具层真 bug：plan 工具的 `args` 会被**静默丢弃**（有时是 JSON 字符串却判成空，`ok:true` 但参数没生效）、`args` 数组被 `String()` 变成 `"[delta]"`、`perf`/`opt` 无参 op 多传 `{}`。
- 新增回归：`tests/engine_probe.sh`（**16/16**）· `tests/view_diag_selftest.py`（**16/16**）· `qc_render` 自检 **29/29**。

### 4.10 返回通道修复 + 一键启动（v0.9.4）

- **P0 · 回执必须过宿主的 lossless-JSON 门**。宿主校验器 `dsh-util-values/walkJsonValue` 拒收
  **undefined 值 / NaN / ±Infinity / -0 / Date / Map / Set / 类实例 / 循环引用**（比 `JSON.stringify` 严：
  stringify 会把 undefined 键丢掉，所以肉眼看不出来）。v0.9.3 的回执里有一个 `promoted: undefined`，
  直接把 `blender_rt_headless` 与 `blender_rt_job` 的 status/collect/wait 全部打死
  （"任务能交出去但收不回来"）。v0.9.4：
  ① 该字段改"有才给键"；② **所有工具出口统一 `losslessSanitize()`**（undefined→丢键、非有限数→null、-0→0…），
  且**改动不静默**（回执里追加 `⚠️ 回执已消毒 N 处…`）。
  回归门：`npm run test:lossless`（离线 40 断言）/ `npm run test:lossless:live`（真机 47 断言），
  用的是**宿主的真校验器**。
- **P1 · `blender_viewport(op="launch")`**：agent 没有人手点"N 面板 → Connect"，这条把
  "启动 GUI Blender + 让 addon 起 socket server"固化成一个调用：写 boot 脚本 → detached spawn →
  **轮询 9876**（唯一可信判据）→ 顺手 doctor。幂等；支持 `wait_ms / file / exe / addon_module / addon_file / dry_run`；
  boot 结论落盘 `launch-status.json`。
- **P1+ · 死会话的写租约自动回收**：holder 命名是 `plugin-pid-<pid>`（与后端同机）→ 写请求到达时按
  `process.kill(pid, 0)` 探活，**判死即回收**（否则会话崩掉后它的租约会把别人的写通道挡到 TTL 结束，实测 57 min）。
  `/who` `/health` 的 `lease.holderAlive` 给三态：`true` / `false` / `null`（判不了 → 保守挡，不误抢）。
  回归：`node tests/lease_stale_selftest.mjs`（16 断言）。
- ⚠ 插件是 DSH 启动时加载的模块：**升级后要重启 DSH** 才会在当前会话生效
  （**后端那次改动只需 `blender_viewport op=restart`**）。

## 5. 目录结构

```text
dsh-blender-plugin/
├── package.json                       # 插件包定义（@dsh-external/dsh-blender-plugin）
├── dsh-blender.config.example.json    # 配置样例（复制成 dsh-blender.config.json 生效）
├── tsconfig.json · scripts/build.sh   # 构建：链接 checkout 依赖 + tsc → lib/
├── src/index.ts                       # host：10 个工具 + 后端看护 + 租约心跳
├── runtime/
│   ├── config.mjs                     # ⭐ 分享版核心：env → 配置文件 → 自动探测 → 默认
│   ├── engine.mjs                     # 直连引擎：addon socket 客户端 + 命令目录 + view/headless
│   ├── server.mjs                     # 后端 HTTP：/health /status /doctor /who /commands /frame.png
│   │                                  #   /act /cmd /loop /perf /opt /view /headless /lease /release
│   ├── runner.py                      # 内环 runner v2（timers 循环 + 限额急停 + 罚项/退火/候选表/导出）
│   ├── perf.py                        # 渲染性能预设（降噪器自动判定）+ 对象精简
│   ├── contract.py                    # S1+S2：组件/连接/包络 · 校验 · 破坏性门控 · 证据账本 · 三态判定
│   ├── planner.py                     # S3：Component·Connection·Feature 对象图 → 诊断 → 编译到 bpy
│   ├── worker.py                      # v0.6.0：常驻 blender -b（阻塞 accept 跑主线程）→ 热会话
│   ├── view.py                        # 自定义视角捕获（自建矩阵 + 离屏绘制 + 手写 PNG）
│   └── _probe_tools.mjs               # addon 命令面兼容性自检
├── docs/
│   ├── 操作教程.md                    # ⭐ 从零上手：安装 → 连通 → 工具 → 配方 → 排错
│   ├── 假设驱动建模-cookbook.md       # ⭐ 假设 → 区间 → 搜索 → 证据 → 判定（S0）
│   └── examples/chair-backrest/      # 可复跑示例 + 归档结果与证据图
│   ├── 配置参考.md                    # ⭐ 配置文件 / 环境变量 / 路径映射 / 自动探测
│   ├── 操作教程.md   # 机制与踩坑（作者机器实测记录）
│   ├── 操作教程.md          # 给 AI 建模的双层循环方案
│   └── 操作教程.md            # 作者自用版说明（写死本机路径的那一版，仅作对照）
└── tests/
    ├── README.md                      # 可复跑验证（矩阵对拍 / PNG 字节 / 租约 7 步）
    └── png_decode.py                  # PNG 字节级反查（验色彩空间与行序）
```

---

## 6. 与「作者自用版」的差异（排查问题先看这里）

| 项 | 作者自用版 | 分享版（本包） |
|---|---|---|
| 工作目录（帧 / 脚本） | 写死 `D:\DSH\blender\tmp` ↔ `/mnt/d/DSH/blender/tmp` | `runtime/config.mjs` 自动探测（Windows `%LOCALAPPDATA%\dsh-blender-rt`）或配置覆盖 |
| `blender.exe` | 写死 Steam 路径 | 自动扫描 `Program Files/Blender Foundation/Blender*` + Steam 常见位置 + `PATH`，或配置指定 |
| 端口 | 9876 / 9877 写死 | 默认相同，可配置（支持两台 Blender 并存） |
| 降噪器默认 | 固定 `OPTIX` | 按本机 `compute_device_type` 自动判（OptiX ↔ OpenImageDenoise） |
| 路径映射 | 只按作者的 WSL / Windows 形态 | WSL · 纯 Windows · macOS |
| 教程文档 | 面向作者自己的工作区 | 附带 `docs/操作教程.md` + `docs/配置参考.md`（自包含） |

其余**通道机制、10 个工具行为、租约语义、内环语义与本机版完全一致**。

---

## 7. 常见故障（详见 `docs/操作教程.md` §5）

| 症状 | 先跑这个 | 多半是 |
|---|---|---|
| 工具报"后端不可用" | `blender_viewport op=start` | 后端进程没起来 / 端口被占 |
| `blender-unreachable` | `blender_viewport op=launch`（v0.9.4 一键拉起 GUI + 自动 Connect） | Blender 没跑，或 addon 没 Connect |
| `main-thread-busy` | 等它空下来，或改无头 | Blender 主线程被渲染 / 模态操作占住 |
| `addon-thread-stuck` | 别连发，等它结束 | 上一条长命令还在跑（`blender_rt_loop op=stop` 可急停内环） |
| `addon-thread-stuck` 且带 `detected_protocol` | 按提示改 `addonProtocol` 后 `blender_viewport op=restart` | 装的是另一种 addon，协议不匹配（扁平 vs category/action） |
| 出图报路径错误 | `blender_viewport op=doctor` 看 `config.workDir` | 工作目录两端不互通（改 `workDir`） |
| 写操作 409 `leased` | `blender_viewport op=who` | 另一会话持有写权限租约（`op=lease force=true` 可抢） |
| `blender_rt_headless` 起不来 | 同上，看 `config.blenderExe` | 没找到 blender.exe（三种配置方式见 `docs/配置参考.md`） |
| 工具返回 `value is not lossless JSON` | 升级到 **v0.9.4** 并**重启 DSH** | v0.9.3 的回执里有无 undefined 值（宿主门拒收）→ 已修，见 `CHANGELOG.md` v0.9.4 |
| `blender_viewport op=launch` 等不到端口 | 读回执里的 `statusFile`（`launch-status.json` 的 boot 结论） | addon 没被扫到（试 `addon_module` / `addon_file`）/ exe 路径不对 |

---

## 8. 来源与边界

- 本包**不含** Blender addon 本体（`MCP for Blender`），也不含任何模型 / 贴图资源；addon 需自备并遵守其自身许可。
- 不含人肉面板 / MJPEG 推流 / 鼠标键盘注入（作者侧已明确移除这条路线：注入输入容易把系统按键状态搞坏）。
- 通道只监听 `127.0.0.1`（默认），不对外网开放；`execute_code` 是有意留下的"万能通道"，请只在可信环境使用。
- 作者机器的实测数据（帧延迟、矩阵偏差、租约用例等）保留在 `docs/操作教程.md` 与 `tests/README.md`，可当作你环境的对照基线。

## 致谢 / Credits

- **@lurenjia-l** — [dsh-blender-stylized-shading](https://github.com/lurenjia-l/dsh-blender-stylized-shading)：其 `material_pipeline.md` 的 EEVEE 实测踩坑（Shader to RGB 只含直接光、漫射拓展光源压暗、渐变组控制器绑定）已并入本插件 `docs/EEVEE-工作要点.md`；其 `stylized-shading` 技能已适配本插件直连通道（见 `~/.dsh/skills/stylized-shading/`）。
