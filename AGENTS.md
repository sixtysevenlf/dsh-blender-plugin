# AGENTS.md —— 给 AI 助手的仓库说明

> 这个文件会被 DSH / Claude Code / Cursor / Codex 等助手**自动加载**。动手前先读完（约 1 分钟）。
> 人类读者请直接看 [README.zh-CN.md](README.zh-CN.md)（[English](README.md)）。

> **当前版本（2026-10-28）**：插件 **v2.6.2** · **15 个模型工具** · plan catalog **34 个 family / 228 个 op** ·
> Blender 侧 addon **v1.10**（protocol **11**）· 后端 `127.0.0.1:9877` / addon `127.0.0.1:9876`。
> **验收判据**：GUI 路线 = `doctor` 回 `kind=ok`；纯无头路线（不装 addon）= `blender_rt_worker(start)` + `see` 出图自证
> （此时 doctor 的 bpy 往返不可达属预期）。**GUI 优先（无头备用）**：GUI 首选，交互式无头（持久 worker + see/watch/reset）
> 与 `rt_do/rt_see`（`session/auto`）、`blender_rt_plan(session="auto")` 都可独立工作。

## 0. 硬规则：先读 README，再动手

凡是**安装 / 部署 / 配置 / 排查**类任务，先完整读 `README.zh-CN.md`（或 `README.md`）的这三节：

| 读哪节 | 为什么 |
|---|---|
| **§2 前置条件（5 条）** | 缺一条都装不起来。注意：Blender 侧 addon **只支持本包附带的 `addon/dsh_blender_bridge.py`（v1.10+ / protocol 11）**；旧 `MCP for Blender` 自 v2.3 起不支持，必须停用旧 addon 再启用本包 addon（见 [MIGRATIONS.md](MIGRATIONS.md)） |
| **§3 安装（5 步）** | 构建（**可选**，本仓已提交 `lib/`）→ 配置 → 装进 profile → 起 Blender（**GUI 首选**）→ 验证 |
| **§8 常见故障**（英文 README 为 §9） | 报错先查表，别自己发明修法 |

**不要凭直觉编**：路径、端口（addon `9876` / 后端 `9877`）、配置项名，一律从 `docs/配置参考.md` 取。
工具名 / op 名不确定时先 `blender_rt_plan(op="catalog")`（本地直出，不占往返，34 个 family / 228 个 op）；写错会回 `did_you_mean`，照它给的骨架改。

## 1. 「装好了」的判据（不是「命令跑完了」）

三条全绿才算装好；缺一条就是没装好，按 §8 排查（英文 README 为 §9）：

1. `curl -sS http://127.0.0.1:9877/doctor` → `kind=ok`（**GUI 路线唯一判据**）
2. `blender_viewport(op="doctor")` → `kind=ok`（GUI 路线）
3. `blender_rt_plan(op="catalog")` → **34 个 family / 228 个 op**（工具数应为 **15**）
4. GUI 可用时 `blender_rt_see(max_size=560)` 回一帧图；**纯无头路线**（不装 addon）用 `blender_rt_worker(op="start")` + `op="see"` 回一帧图自证（doctor 的 bpy 往返不可达属预期）

**没到 `kind=ok` 不要对用户说「安装成功」。** 典型失败与含义：

| 回执 | 含义 | 处置 |
|---|---|---|
| `blender-unreachable` | Blender 没跑，或 addon 没 Connect | `blender_viewport(op="launch")`；或人工在 Blender 里按 `N` → Connect；纯无头改用 `blender_rt_worker` |
| `main-thread-busy` | 主线程被渲染 / 模态操作占住 | 等它空下来，或改走无头（`rt_headless` / `rt_worker`） |
| 后端不可用 | 后端进程没起来 | `blender_viewport(op="start")` |
| 工具数不是 15 / catalog 不是 34-228 | 插件版本旧，或升级后没重启 DSH | 安装 v2.6.2 → **重启 DSH** → 复核 |
| `value is not lossless JSON` | 宿主与插件回执契约不匹配（旧版/未重载） | 升级 v2.6.2 后**重启 DSH** |
| 写操作 409 `leased` | 别的会话持有写租约 | `blender_viewport(op="who")`，别强抢 |

## 2. 当前能力面（v2.6.2 · 15 个模型工具）

工具参考（完整参数见 `blender_rt_plan(op="catalog")` / `docs/操作教程.md`）：

| 工具 | 一句话 |
|---|---|
| `blender_rt_see` | 取一帧视口 / 自定义视角（GUI 可用时；`session/auto` 走无头） |
| `blender_rt_do` | 主线程执行 Python（持久内核 `K`；`session/auto` 走无头） |
| `blender_rt_watch` | 时间窗采样 ≤6 帧 + hash；支持无头 `session` |
| `blender_rt_loop` | 主线程内环搜索（`setup/step/measure`；`target="session"` 不依赖 timers） |
| `blender_rt_cmd` / `blender_rt_commands` | 透传 addon 命令 / 查 39 条命令与 5 个集成状态 |
| `blender_rt_perf` / `blender_rt_opt` | 渲染性能诊断与优化 / 对象精简安全合并 |
| `blender_rt_headless` | 独立 `blender -b` 进程（不占 GUI；长活可转 job 作业层收结果） |
| `blender_rt_plan` | 契约层 + 规划器 + **catalog（34/228）**；`session="auto"` 同步无头路由 |
| `blender_rt_worker` | 交互式无头热会话：`start/exec/see/watch/reset/stop`、`preload`/`warmup`、空闲 TTL |
| `blender_rt_txn` / `blender_rt_preset` | 事务回滚（文件级/对象级）/ 参数配方库 |
| `blender_rt_job` | 长任务作业层（`start/status/collect/wait/kill/list`，超时不丢结果） |
| `blender_viewport` | 通道运维：`status/start/stop/restart/doctor/who/lease/release/launch` |

**P0–P2 新能力族（都在 plan catalog 里，不再新加模型工具）**：
- 交互式无头：`rt_worker`（see/watch/reset/preload/warmup，worker 空闲 TTL 默认 15 min）· `rt_do/rt_see session/auto` · `plan session="auto"` · `rt_watch` 无头；
- 参考图量具：`img_digest / img_probe / img_measure / img_calib`；
- 装配：`asm_scan / asm_manifest / asm_merge / asm_repair / asm_export / asm_gate`；
- 白模与参考包：`blockout_base / batch / export / turntable / refsheet`（refsheet = 4 视图 + 拼图 + JSON + 可选转台 MP4）；
- 同机位 QC：`visualreport_report`（verdict-first，qc→imgtools→numpy 三级降级）；
- 无头轮廓拟合：`shapesolve_solve`（正交解析投影 + numpy 光栅化，不渲染）；
- 缺陷/基线评估：`eval_run`；强制门：`gate_preflight / gate_checkpoint / gate_deliver` + `deliver_export(require_gate=true)`。

> **明确不覆盖 / 未安装依赖**：landmark / 人脸（无 cv2、MediaPipe）、OCR、DXF、
> Open3D / manifold3d / glTF-Validator **均未安装**，不要写成已支持；catalog 的"不覆盖声明"还写明
> 生物/有机造型、电影级布光/体积/合成、角色动画绑定不覆盖。

## 3. 装完 / 改完之后的规矩

- **插件升级后必须重启 DSH**（插件是 DSH 启动时加载的模块）；只改了后端才用 `blender_viewport(op="restart")`。
- **GUI 优先（无头备用）**：默认先开 GUI Blender + Connect addon；只有在长活/批处理/无 GUI 场景才走 `blender_rt_headless` 或 `blender_rt_worker`。GUI 不在时 `rt_do/rt_see` 用 `session/auto` 自动降级；无头也能 rt_see，不会失明。
- 长任务（> 1 min、长渲染、批量几何）走 `blender_rt_job(op="start"/"wait")` 或 `blender_rt_headless(as_job=true)`；
  **别用同步 headless 硬等** —— 客户端超时 ≠ 任务失败，按 `runId` 回收结果。
- 拓扑类改动要回滚，用 `blender_rt_txn` 的文件级 `snapshot/restore`；对象级 `mark/revert` **不含**拓扑 / UV 改动。
- QC / 验收看 **verdict 三态**（`pass / fail / degraded`，`degraded ≠ 通过`）；强制门用 `gate_preflight/checkpoint/deliver`。

## 4. 改这个仓库本身

- 构建：`DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh`（`src/` → `lib/`；本仓已提交 `lib/`，改 `src/` 后才必须重建）。
- 离线自检：`npm test`；真机 HTTP 验收：`node tests/host_routes_selftest.mjs`（需要后端在跑）。
- 改 `runtime/*.py` 前先看 `tests/README.md`；每个能力族都有对应的 `*_selftest`（含 P0–P2 新增模块与真机脚本）。
- 文档事实源在包内：`README.zh-CN.md` · `docs/操作教程.md` · `docs/配置参考.md` · `CHANGELOG.md`。
- **发版流程**（v1.0.0 起）：① 版本号**三处同步**（`package.json` / `src/index.ts` 的 `PLUGIN_VERSION` / 重建后的 `lib/`）；
  ② `npm test` 全绿；③ 提交 → `git tag -a vX.Y.Z` → 推 `main` 与 tag → `gh release create`。
- **发布 npm**：`npm pack` → `npm publish <tgz> --access public`（包名 `@dsh-external/dsh-blender-plugin`，需维护者 npm 登录）。
  打包面靠 `runtime/.npmignore` + `package.json` 的 `files` 否定模式把 `runtime/ext/venv`（1.2 G）挡在包外 ——
  **改 `files` 后必须 `npm pack --dry-run` 核对**（v2.6.0 实测约 **1.6 MB / 163 文件**（解包 4.8 MB）；历史反例：曾漏成 458 MB / 12638 文件）。
- **随包 skill 的真源**：`skills/blender-modeling/`（SKILL.md + references + MIT LICENSE/NOTICE；当前 v2.6，GUI 优先（无头备用））。
  插件加载时注册 bundled provider（`ctx.inject(['skills'], …)`，rank 600；用户 `~/.dsh/skills` 的 400 副本仍优先）。
  **改 skill 直接改这里**，独立仓库 `dsh-skill-blender-modeling` 仅作非 DSH 宿主的镜像；改完跑 `node tests/skill_bundle_selftest.mjs`。
- **选中桥（v2.3 起）**：DSH 侧视口面板 / `blender_rt_pick` / TUI 侧栏面板**已删除**（入口改到 Blender 原生选中）。
  addon 侧 `ActivityTracker` + 5 条命令（`get_activity_state / get_selection / send_selection_to_dsh / get_collections / send_collection_to_dsh`，addon v1.10）；
  addon v1.10 起编辑模式点/线/面、姿态骨骼、曲线/晶格控制点也进入 `selection.sub`（见契约 §S1.3），N 面板另有「暂停 AI 操作」。
  契约唯一真源 `docs/宿主集成-接口契约-v2.3增补.md`（顶部有 v2.6 兼容声明）；回归
  `node tests/selection_store_selftest.mjs` + `node tests/client_bundle_selftest.mjs`（改后两者都必须在 `npm test` 里绿）。
  **dsh-TUI 宿主半边（v2.6.1）**：`src/tui-host.ts` 用 dsh-TUI 接缝补上 TUI 里的选中（上下文 chip）/ 页脚状态字段 / `@` 提及 / `/blender` 命令，
  按接缝是否存在启用（契约 §S6；回归 `node tests/tui_host_selftest.mjs`）。
  两个 N 面板模式：**多选 / 集合**（大装配体用集合）。**装完/升级完必须主动告诉用户这些功能**（话术见 `docs/AI-安装提示词.md` §③.5）。

## 5. 给用户的一段提示词

[`docs/AI-安装提示词.md`](docs/AI-安装提示词.md) 是可直接粘贴给任意 AI 助手的安装提示词（含 v2.6 验收判据、失败对照表与新能力通知清单）。
