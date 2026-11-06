# Proposal

## Why

3.0.x 的连接模型是 host → runtime(HTTP 9877) → addon(TCP 9876)：addon 必须监听，runtime 主动连 addon。这个模型能跑通单会话，但没有给多实例、虚拟显示、headless 作业队列和可验证的会话边界留位置；可用性诊断也只是散落在 status/doctor/worker 里的特例，而不是一条契约。

2026-11-04 的场景建模会话暴露了代价：模型可见的插件调用 5 次、任务级成功 0 次；`op=start` 说“后端已在运行”，`doctor` 随后说“后端可能没起”；agent 在 90 秒内放弃插件，之后 67.6 分钟没有重试，改用 108 次 `blender -b` 和 104 次 `read_image` 完成交付。随包的独立评审把权重判为：配置/环境约 60–70%，错误契约/诊断约 20–30%，工具命名/数量约 0–10%；同时确认 `see` 的 crop/zoom/像素采样有证据，但“亚秒级”只是推断，`op` 尚无调用证据。

2026-11-06 又出现同一类故障的另一面：Blender 5.2.3 上 Blender Lab 的 MCP 扩展与 DSH addon 都以 SO_REUSEADDR 监听 127.0.0.1:9876，Windows 把新连接交给 MCP 扩展，插件 8/8 次调用全部超时（doctor 6.5–8.1s），而 addon 面板仍显示“已在监听”。固定默认端口 + 静默共存的代价是：TCP 连得上、通道不可用，模型只能反复超时。

v3.0.6（PR #75）已经修复了这组 P0：Windows Steam 探测、`blender_exe_missing` 预检、status/doctor 有界超时与诚实归因、`commands` 的 next_action。但修复仍是 3.0.x 拓扑内的补丁：addon 仍然监听入站端口，没有队列、没有 Gateway 管理的虚拟显示，15 个工具中仍有 `plan`/`do` 这类内部实现名泄漏。如果直接把队列和 virtual-gui 叠在 3.0.x 假设上，就会产生第二套连接路径和新的中间层。

现在 3.0.6 已发布、main 干净、重构终态治理已完成，是把这些能力收口成 3.1 契约的窗口：先把可用性、连接、会话、队列变成可验证的一等公民，再开放工具面。CLI 不再作为 3.1 的交付物；agent 的提效由 tool 的 batch/transaction 承担，而不是让模型多走一层进程。addon 目前以 legacy zip 随 GitHub Release 分发，用户更新靠手动下载；3.1 同时把它收口为 Blender 4.2 extension，进入官方 Extensions Platform 的审核与更新通道。

## What Changes

- **BREAKING** **Gateway 单端点拓扑**：删除 runtime 作为“连 addon 的客户端”和 addon 的 9876 入站监听；Gateway 成为唯一 server，addon 作为 client 主动连接。host 与 tools 只连 Gateway；端口被占用时 fail closed；不做双栈兼容。
- **默认端口迁移与端口冲突 fail closed**：Gateway 默认端口从 9877 改为 39877（避开 Blender MCP 生态常用的 98xx 段与 Windows 动态端口段）；端口被占用时 fail closed 并报告占用者（port / pid / process），禁止与既有 listener 静默共存（不用 SO_REUSEADDR 共享）；doctor/status 必须区分“端口被他人占用”与“addon 未启动或卡住”。
- **BREAKING** **addon 反向连接与握手**：addon 注册 `instanceId / kind / version / project / capabilities`；Gateway 按 instanceId 管理多实例、路由与断线重连；协议版本不匹配直接拒绝（负向测试覆盖旧 addon）。
- **可用性契约**：首调失败返回统一四问 `stage / root_cause / fix / fallback`（附 `evidence`）；`start` 与 `doctor` 不得互相矛盾；`/health` 通不等于 bpy 可用。3.0.6 已实测 `/status` 约 7.5s、`/doctor` 约 6.5s，作为 3.1 的预算基线；目标是在不牺牲诚实性的前提下收紧到评审建议的 P95 ≤3s；不把“无响应”写成“后端没起”。
- **会话与资源**：`user-gui`（用户拉起、agent 附着、单写者/lease/pause、无上限）、`virtual-gui`（agent 专用、用户不可察，本 change 的发布门槛，不是可选增强）。`headless` 不属于 session：它是一次性 `blender -b` 无头任务，只通过 `job-queue` 提交。`virtual-gui` 必须启动真正的 GUI Blender（`bpy.app.background == False`），具备可用 GPU/GL、可截图/see、可多实例、可清理；交互式视觉只属于 user-gui/virtual-gui。`worker` 的 Workbench see 从模型工具面移除：worker 只保留为内部执行器/CI 诊断，不暴露为模型工具。平台显示做成 `DisplayProvider` 抽象；V0 先做 provider bake-off：Windows 首选自写的隐藏 desktop helper（Win32，无第三方依赖，不需要驱动/管理员），签名 IDD 仅作为 fallback（需要用户安装驱动/UAC，不能静默）；Linux 首选 Xvfb+VirtualGL，Xvfb 软件渲染只能 `preview-only`。WSL/WSLg 路线不再考虑。V0 通过前不写死“Windows 原生 IDD 默认”，也不写死“默认 4 个 virtual-gui”；通过后才定 default/fallback、实例上限和安装前提。**如果 Windows 没有 provider 通过，3.1 不发布，不能退回 headless 冒充 virtual-gui。**
- **首调会话门与禁止静默降级**：会话级能力（`session`/`op`/`py`/`see`）在解析不到 `user-gui` 时不得自动启动 `virtual-gui`，也不得转给 headless/worker；必须停下返回 `stage=session`，说明 Blender 没起来，并让用户选择“拉起 Blender 后 attach”或“用 virtual-gui 完成任务”。只有用户确认后才启动 virtual-gui，3.0.x 的自动降级路径删除。
- **headless 作业队列**：`run` 提交两种作业：`kind=render`（声明式最终渲染，GPU slot 1）与 `kind=headless`（通用服务器侧无头任务：批量渲染/导出/几何处理/脚本，CPU slot 2；声明 GPU 时占 GPU slot）；队列容量 64；提交即返回 `jobId`；状态机 `queued → assigned → running → completed | failed | canceled`；有界队列满时返回 `QUEUE_FULL`；FIFO + 优先级 + 配额；SQLite 或 JSON 持久化；支持取消与断线后 collect；render/headless 共用同一套 status/wait/collect/cancel/list、日志与产物清单，不设独立 job 工具。virtual-gui 占用 GPU 时 render 排队，不超卖。
- **BREAKING** **工具面 15→6 是等价迁移与改名，不是新增能力**：六个目标工具按用户意图命名；括号内是它吸收的旧工具，能力先等价迁移，不新增建模/渲染语义。`session`（viewport、txn）、`op`（plan/loop/opt/preset）、`py`（do：在已 attach 的 GUI 会话内执行 agent 写的脚本与短 Python，是执行通道而不是建模引擎；没有会话时拒绝，不启动 headless）、`see`（see/watch）、`run`（headless/job/perf：唯一作业入口，用 `kind=render/headless` 区分两类作业；早期叙事里的 `render` 工具名在本 change 统一为 `run`）、`inspect`（commands/cmd：默认只读，`cmd` 的写操作必须显式 `mode=execute` 并返回 write receipt）；`worker` 不再作为模型可见工具（Workbench see 同步移除；内部执行器只服务 plan/loop/CI，不暴露工具名）。`cmd` 是可写执行器，与只读 `inspect` 的边界单列，不把可写能力藏进只读语义。行为先等价迁移，再按 PoC 结果验收命名。
- **BREAKING** **addon 改为 Blender Extension**：addon 从 legacy zip 迁到 Blender 4.2+ extension：新增 `blender_manifest.toml`、删除 `bl_info`、zip 根为 manifest + `__init__.py`；模块名变为 `bl_ext.<repo>.dsh_blender_bridge`，偏好设置与内部引用统一走 `__package__`；host 的 addon 探测、自动启用、`get_addon_info` 版本校验不再依赖 `bl_info`/固定模块名；旧 zip 安装路径删除，只保留一个 extension 产物。addon 采用 `GPL-3.0-or-later`（官方平台要求），插件本体保持 BSD-3-Clause，保留上游 MIT 版权声明。
- **addon 版本与发布**：插件 3.1.0、addon 2.0.0、协议 12、extension manifest 2.0.0；官方 Extensions Platform 首次上架走人工提交/审核，后续版本由 CD 调 API 上传（`POST /api/v1/extensions/dsh_blender_bridge/versions/upload/`，token 走 secret）；`blender --command extension validate` 作为发布门禁，Blender 4.2 CLI 进入 CI；声明 `network`/`files` 权限并遵守 `bpy.app.online_access`；不承诺自动审核通过。
- **迁移与文档**：一次性升级，插件与 addon 必须同步；协议版本 bump 到 12；addon 从 legacy zip 迁移到 extension，旧 zip 安装入口删除；删除“动态端口/兼容旧 addon”的表述，旧拓扑只保留在历史文件；CHANGELOG 与操作教程按 3.1.0 更新。

## Capabilities

### New Capabilities

- `blender/gateway`: Gateway 单端点、addon 反向连接、握手身份、多实例路由、断线重连、版本匹配 fail-closed、无 9876 监听。
- `blender/availability`: 首调四问 envelope、分层探测、`start`/`doctor` 一致性、阶段预算、fallback 与 evidence。
- `blender/sessions`: user-gui / virtual-gui 的会话边界与生命周期、lease 与单写者、实例上限与低资源降级；headless task 不属于 session，由 `blender/job-queue` 管理；交互式 see 只属于 user-gui/virtual-gui；worker 仅作为内部执行器，不作为模型工具。
- `blender/virtual-display`: virtual-gui 的 DisplayProvider 契约；Windows 自写隐藏 desktop helper、签名 IDD fallback，Linux Xvfb+VirtualGL；安装前提（隐藏桌面零驱动/零管理员，IDD 需驱动/UAC，Linux 需 Xvfb/VirtualGL/GPU 驱动）、GUI Blender/GPU/GL、截图 parity、多实例与清理；V0 选定的 default/fallback 与支持矩阵。
- `blender/job-queue`: `render` 与 `headless` 两类无头作业的 jobId 提交、状态机、有界队列与 `QUEUE_FULL`、优先级/配额、持久化、取消与 collect；render 是声明式渲染子类，headless 是通用服务器侧 `blender -b` 任务。
- `blender/addon-extension`: Blender 4.2+ extension 包格式（manifest、无 `bl_info`、extension 根布局）、`bl_ext.*` namespace 与 `__package__` 偏好、host 侧探测/自动启用、GPL-3.0-or-later、`network`/`files` 权限与 `bpy.app.online_access`、`extension validate` 门禁；官方 Extensions Platform 首次人工上架 + 后续 CD 版本上传，不保留 legacy zip。
- `blender/tool-surface`: 15→6 工具映射、六个入口的职责边界、`inspect` 只读与 `cmd` 可写的分界、工具计数与 catalog 一致性。

### Modified Capabilities

无。项目当前 `openspec/specs/` 为空，3.0.6 的行为修复先作为本 change 的回归基线，不另建旧 spec。

## Impact

- **代码**：`src/tools/*`（15→6 与 facade）、`src/plan/headless-target.ts`、`src/host/*`、`src/plugin*.ts`；`runtime/http/*`、`runtime/core/*`、`runtime/transport/*`、`runtime/execution/*`、`runtime/catalog/*`、`runtime/launch/*`（`bl_ext.*` 探测/自动启用）；`addon/dsh_blender_bridge/*`（连接方向、握手、队列接入、extension 包化：`blender_manifest.toml`、去 `bl_info`）、`scripts/build-addon.mjs`、`tests/e2e/blender/package_check.py`；新增 Gateway 入口与 DisplayProvider 集成；`tests/{unit,integration,e2e}/*`、`config/*`、`scripts/*`。
- **API/协议**：addon 协议版本从 11 bump 到 12，addon 版本从 1.11 bump 到 2.0.0，插件 3.1.0；Gateway HTTP 端点契约变化；6 个模型工具名与参数面变化（**BREAKING**）；旧 addon 拒绝接入；extension manifest 与平台元数据成为 addon 的发布契约；不支持 3.0.x 混合部署；不提供 CLI 旁路接口。
- **依赖/平台**：默认不引入外部运行时服务；队列持久化优先使用 Node 内置能力或仓库已有的 JSON 存储。virtual-gui 是发布门槛：Windows 首选自写隐藏 desktop helper（Win32，无第三方依赖，不需要驱动/管理员）；签名 IDD 仅 fallback，可能要求管理员安装和第三方签名驱动（需核对许可证、分发与卸载）；Linux 首选 Xvfb+VirtualGL（Xvfb 为发行版包，VirtualGL 需官方仓库/包，GPU 驱动按发行版/显卡准备，安装需要 root、运行不需要）。WSL/WSLg 不再考虑。任何 provider 都不得静默安装驱动；支持矩阵和安装前提必须在文档中明示。仅软件 GL 的 provider 只能标 `preview-only`，不能算 virtual-gui 通过。addon 官方发布要求 Blender 4.2+ 与 GPL-3.0-or-later；CI/CD 需要 Blender CLI 做 extension build/validate，以及平台 token secret；插件本体保持 BSD-3-Clause。
- **测试与门禁**：新增反向连接/重连、多实例、无 9876 监听、队列上限与取消（render + headless 共用同一 jobId/状态机）、start/doctor 一致性、旧 addon 拒绝的测试、首调会话门（无 user-gui 不启 virtual-gui、不转 headless）、依赖安装不静默；新增 virtual-display V0 验收（`background == False`、GPU/GL、截图 parity、2 个实例、stop 后无孤儿、安装前提）；现有 15 工具的契约测试改写为 6 个工具族（worker 不再作为模型工具，第六族为 `run`），计数从 15 tools 变为 6 tools；新增 extension manifest/license/permissions/`bl_ext.*` namespace/安装与升级测试，`blender --command extension validate` 必须在 PR 与 release 门禁通过；`tests/e2e/blender/package_check.py` 从“legacy 不适用”改为“extension 必须通过”；3.0.6 的 A1–A6 回归必须继续绿。
- **文档/用户可见**：`docs/architecture.md`、`docs/操作教程.md`、`docs/配置参考.md`、`skills/blender-modeling/*`、README 与中英版本同步；安装说明改为“同时升级插件与 addon”，addon 安装改为 Blender Extensions（官方市场/Install from Disk），删除 legacy zip 下载与安装说明；新增 GPL/上游 MIT 声明、extension 权限、首次审核与 CD 上传说明；新增 virtual-gui provider 支持矩阵、安装前提与限制；删除 CLI 相关文档。
- **非目标**：CLI 不作为 3.1 交付物；不保留 3.0.x 双栈；不把 worker 作为模型可见工具，不用 Workbench see 冒充交互式视觉；不采用第三方隐藏桌面依赖（offstage/RunHidden）；不引入 WSL/WSLg；不把六工具命名 A/B 实验当作发布门槛；不承诺 see 在 Cycles 下亚秒；不在本 change 引入云服务或新的外部后端；不在 V0 结论前把 Windows IDD 或“默认 4 个 virtual-gui”写成已定事实；不保留 legacy addon zip；不支持 Blender <4.2；不把自建 extension repository 作为主发布渠道；不把插件本体改成 GPL；不承诺首次上架自动通过审核。
