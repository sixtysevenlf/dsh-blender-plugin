# Design

## Context

3.1 是一次拓扑升级，不是 3.0.x 的增量补丁。3.0.6、terminal-state-closeout 和 PR #82 已合并，当前 `origin/main` 为 `82a44bc`（本地 main 可能落后，实施前先 fetch）；仓库已有可复用的契约机器（routing.json、contracts.json、exceptions.json、BEHAVIOR_MATRIX、extension 之外的结构门禁），本设计在这些机器上扩展，不另起一套规则文件。

基线：15 个模型工具 / 34 families / 228 ops / 38 registered commands（+ping=39 wire）/ 29 HTTP routes / protocol 11 / addon 1.11。3.1 目标：6 个模型工具 / 7 个 capability / protocol 12 / addon 2.0.0 / extension manifest 2.0.0 / 插件 3.1.0。

已验收的 A2 PoC v2.1 证据：P1 反向连接与重连 10/10（重连 102.44ms）；P2 多实例路由 6/6；P4 队列模拟 7/7；P5 see 延迟 P50 60.402ms / P95 64.193ms，但 crop / pixel probe / dedupe / Cycles 未验证；P3 虚拟显示 BLOCKED；P6 六工具收益 BLOCKED；P7 CLI 只有 stub，CLI 已从 3.1 范围删除。虚拟显示和六工具行为都必须在本 change 内重新验收，不能拿 PoC 结论代替。

仓库约束（AGENTS.md）：结构门禁、契约计数、先红后绿、例外只能进 `config/exceptions.json`、不新增规则文件、旧文件在同一次变更删除。3.1 的 BREAKING 变更必须在 change 中显式声明，并在 CHANGELOG、README、操作教程同步。

## Goals / Non-Goals

**Goals:**

- 让 Gateway 成为唯一服务端，addon 反向连接、可多实例、可重连，旧 addon 被拒绝。
- 让首调失败可执行：统一四问 envelope、start/doctor 一致、P95 ≤3s。
- 把会话收敛为 user-gui / virtual-gui；virtual-gui 成为 3.1 发布门槛。
- 建立有界、可持久化、可取消的 render/headless 作业队列。
- 把 addon 收口为 Blender 4.2 extension，并接入官方市场审核/更新通道。
- 把模型工具面收敛为 6 个产品能力，行为等价迁移后再做命名验收。
- 全部改动有可复现的门禁、真机证据和迁移文档；不保留 3.0.x 双栈。

**Non-Goals:**

- 不交付 CLI 旁路（用户 m03405 已取消；P7 只作为历史 PoC）。
- 不保留 3.0.x 双栈，不兼容旧 addon，不做协议自动降级。
- 不引入云服务、外部队列服务或新的运行时依赖。
- 不采用第三方隐藏桌面库（offstage/RunHidden），W1 使用自写 Win32 helper。
- 不采用 WSL/WSLg/WSL+Xvfb 路线。
- 不把六工具命名 A/B 当发布门槛；行为边界先成立，命名按本设计固定。
- 不承诺 Cycles 下 see 亚秒；see 只服务 GUI 会话，不服务 headless。
- 不在 V0 结论前写死 Windows provider 默认值、virtual-gui 默认实例数或驱动安装方案。
- 不把 `cmd` 的可写能力伪装成只读 `inspect`。

## Decisions

### D1 — Gateway 单端点与 addon 反向连接

- Gateway 取代当前 runtime 的 `server.ts`，是唯一 server。默认物理进程为 host、Gateway、Blender 三个；host 嵌入 Gateway 的可选模式不在 3.1 交付范围，避免两套生命周期。
- Gateway 的生命周期归 DSH host/plugin，不归 Blender、CLI 或 job-queue：插件启用时注册 owner，首次后端调用时拉起；插件停用或 DSH host 退出时停止并清理；不 `detached`，也不以 `unref` 让子进程脱离 host 生命周期。Gateway 崩溃由 host 有界重启（退避 + 次数上限），超限返回 `stage=spawn`，不得在旧端口无限重试；Gateway 本身不是 job，job-queue 只承载 `render`/`headless` 两类 Blender 作业。
- Gateway 只监听一个 loopback HTTP 端口（默认 39877；9877 仅作为显式配置保留）。host/tools 走 HTTP；addon 在同一端口通过 WebSocket upgrade 路径 `/addon` 建立控制通道。不新增第二个 listener，9876 不再存在。
- WebSocket 端点使用仓库内最小 RFC6455 实现（`runtime/transport/addon-ws.ts` 与 addon 侧 `transport.py`），不新增 npm/pip 依赖。P1 已证明该方向可行；如果实现阶段发现同端口 WS 需要超出预算的复杂度，回到 design 修改契约，不能静默加一个控制端口。
- addon 删除入站 server：`addon/dsh_blender_bridge/server.py` 的 accept/listen 路径删除，改为 client 连接、心跳、重连。`bpy.app.background` 仍拒绝加载 addon。
- 握手字段：`instanceId`、`kind`（user-gui/virtual-gui）、`version`、`protocol`、`project`、`capabilities`。`instanceId` 在 Blender 进程生命周期内稳定；重连携带同一 ID。
- Gateway 维护 instance registry：`connected`、`ready`、`lastSeen`、`connectionEpoch`、`project`、`capabilities`。请求带 `requestId`，响应必须回显；旧 epoch 的响应丢弃，不重放。
- 多实例路由：tool/session 显式 `instanceId`；只有一个实例时可省略；多个实例省略时返回 ambiguity 错误并列出 ID。`instanceId` 相同的重连在握手校验后替换旧连接，旧请求失败，不执行两次。
- 协议不匹配（含旧 addon 的 protocol 11）直接拒绝并给出升级指引；不做 legacy 回退。
- 端口占用、Gateway 未启动、addon 未连接都走 availability envelope；不允许把 Gateway 不可达写成 Blender 未安装。
- 端口归属与冲突：bind 失败时禁止静默共享（不接受 SO_REUSEADDR 与既有 listener 共存），必须返回 `stage=config` 的 envelope 并给出 port 与占用者（Windows `GetExtendedTcpTable` / `netstat -ano`，Linux `/proc/net/tcp` + `/proc/<pid>/cmdline`）。默认端口迁出 98xx 段的直接依据是 2026-11-06 现场：Blender 5.2.3 上 Blender Lab MCP 扩展与 3.0 addon 同绑 9876，插件 8 次调用 8 次超时（doctor 6.5–8.1s），面板仍显示监听。

### D2 — 可用性统一契约

- 新增共享 schema `config/availability.json`（stage 枚举、字段约束、fallback 类型）和 TS/Python 各自的 `availability` helper；所有 tool、route、job 失败从同一构造器出 envelope：`stage / root_cause / fix / fallback / evidence`。
- `stage` 固定枚举：`config`、`session`、`spawn`、`transport`、`handshake`、`addon`、`bpy`、`queue`、`render`。每个失败只归一个 stage；缺字段视为契约缺陷，测试直接红。
- status/doctor 使用同一 readiness 快照：Gateway liveness、addon control、addon bpy probe、instance identity。`/health` 只返回进程存活，不参与 readiness。start 成功必须能在同一快照里被 doctor 复现；start/doctor 矛盾由 contract test 拦截。
- 诊断预算：`config` 失败立即返回；`spawn` 使用显式 executable 检查；`transport`/`handshake`/`addon`/`bpy` 各自有硬超时。首调失败 P95 ≤3s；当前 3.0.6 实测 status 约 7.5s、doctor 约 6.5s，是待收紧的基线，不是目标。
- 移除 3.0.x 的嵌套重试链：诊断只做有界的单层探测，超时返回结构化 timeout，不把多个失败合并成 backend unreachable。
- `fix` 必须可执行（命令、配置键、安装步骤）；`fallback` 必须安全（显式不可用或降级路径），不得启动可见 GUI、伪造输出或回退旧拓扑。
- 3.0.6 的 A1–A6 场景作为回归用例保留：无 exe、Steam 探测、半开 addon、commands 断连、全门禁、五步时间线。

### D3 — 会话边界与 lease

- 会话只有两种 kind：`user-gui` 与 `virtual-gui`。`headless` 是一次性 job，不是 session；`session(kind="headless")` 必须返回校验错误。
- `user-gui` 只 attach 用户已启动的实例，不由 agent 再拉一个可见 GUI。attach 在单实例时可省略 ID，多实例必须显式，否则 ambiguity。
- 首调会话门：会话级能力（`session`、**执行型** `op`、`py`、`see`）解析不到 ready `user-gui` 时，不得自动启动 `virtual-gui`，也不得把调用转给 headless/worker；必须返回 `stage=session` 的决策请求，明确告知“Blender 没起来”，并给出两个显式选项：拉起 Blender 后 attach，或用户同意后启动 virtual-gui。`op` 的 `catalog`/dry-run 等本地只读操作不要求会话，直接返回目录/校验结果；只有真正执行 op 才进入这道门。3.0.x 的 GUI→worker/headless 自动降级链在 3.1 删除，降级只能是用户确认后的显式动作。
- 每个会话有单写者 lease：`acquire`、`release`、`heartbeat`、`pause`、`resume`。写操作要求 lease；pause 阻止写、允许读；lease 冲突返回 holder 信息。用户手动操作与 agent 写入的冲突通过 pause/lease 显式化，不靠重试掩盖。
- `virtual-gui` 由 Gateway 通过 DisplayProvider 启动/停止，agent 专用、用户不可察；只有 addon handshake 成功后会话才算 ready。实例上限可配，默认值由 V0 决定；低资源时降级策略也由 V0 决定，不在本设计写死。
- 会话隔离：每个实例独立 scene/Python namespace/lease；一个实例断线不改变其他实例 readiness。跨实例执行返回错误，不能落到默认实例。
- `see` 只属于 GUI 会话，走 `GPUOffScreen` viewport capture；`worker` 的 Workbench see 从模型面移除，worker 只保留内部执行/CI 诊断，不作为 session kind 或 tool。`see` 失败不得回退 headless Workbench。
- 资源协调：virtual-gui 占用 GPU 时，render 作业排队；会话与队列由 Gateway 的同一个资源账本管理，不超卖 GPU。

### D4 — DisplayProvider 与 virtual-gui V0

- 抽象接口：`start(profile)`、`stop(handle)`、`health(handle)`、`capabilities()`。`start` 返回 provider handle、display endpoint、Blender 启动描述；`stop` 幂等并释放进程树/显示资源；`capabilities()` 报告平台、GPU/GL、prerequisites、是否需要用户授权。
- Windows W1（首选）：自写 Win32 helper（C++，无第三方依赖）实现 `CreateDesktopW` + `CreateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `CreateProcessW(CREATE_SUSPENDED, lpDesktop)` + `AssignProcessToJobObject` + `ResumeThread`；Blender 是隐藏 desktop 上的 normal window，不是最小化，默认任务栏/Alt-Tab 不显示。helper 代理 stdout/stderr/exit，失败必须 fail closed，不能回退到用户默认 desktop。
- helper 源码放 `tools/win-hidden-desktop/`，Windows CI 用 MSVC 构建并作为 Gateway 资源打包；不在仓库提交编译产物，发布包必须包含与源码版本匹配的 helper。
- Windows W2（仅 fallback）：签名 IDD 虚拟显示驱动，只有在 W1 未通过 V0 的 GPU/GL 或 viewport parity 时才启用；安装必须用户明确同意、不能静默，报告驱动/UAC 前提和许可证/分发限制。
- Linux L1（首选）：Xvfb 提供 X display，VirtualGL 把 GLX/EGL 转发到真实 GPU；每个实例独立 `:N`，stop 时终止 Blender、vglrun、Xvfb；只读 `-ac` 仅用于 loopback provider。Xvfb + llvmpipe 只能标 `preview-only`，不能通过 gate。
- 不采用 WSL/WSLg/WSL+Xvfb、Xdummy、Weston headless 作为 3.1 provider；未来版本再评估。
- V0 bake-off 是本 change 的发布门槛：Windows W1、Windows W2（仅 W1 失败时）、Linux L1 各跑同一组验收；通过后才在文档和配置里写默认 provider、fallback 顺序、virtual-gui 默认上限和安装前提。
- V0 验收（每个通过的 provider 必须具备）：`bpy.app.background == False`；GL renderer 非 llvmpipe；`see` 走 GUI capture 且与 user-gui 的 viewport 结果 parity；2 个并发实例互不串场景；stop 后无 Blender/Xvfb/helper 孤儿；安装前提可查询；锁屏/RDP/无显示环境行为已记录。
- 缺依赖是代码级硬门：provider 的 start 路径在 spawn 任何进程之前检查 prerequisites，缺失即拒绝；不存在 force/unsafe/skip-check 开关，agent 的意图不能绕过。UI 与 agent 都只能看到“不可用 + 缺什么 + 怎么装”，不能“先试一下”。
- 依赖引导不是静默安装：provider `capabilities()` 报告 prerequisites 与安装状态；host 的 `doctor --fix` 与 addon 偏好设置按钮共用同一张 prerequisites 描述表；Ubuntu 22.04/24.04 与 Debian 12（apt）用 `pkexec` 真一键安装 `xvfb`/`virtualgl`，Fedora/Arch/无 polkit 环境只给准确命令；GPU 驱动只检测不安装；安装后必须复检 capabilities，失败保留原始 stderr 且不得把 provider 标为 ready。
- 若 Windows 或 Linux 任一平台没有 provider 通过 V0：3.1 不发布，也不允许把 render/headless 当作替代；发布报告必须写明失败的测量项。

### D5 — 作业队列

- Gateway 内唯一队列，承载 `render` 和 `headless` 两种 kind。`run` 是唯一模型入口，提交立即返回 `jobId`；没有同步执行路径，没有 `run-`/`job-` 双 ID。
- `render` 只接受声明式参数（file/frames/camera/engine/resolution/samples/output/format/priority），由内部固定入口执行，不执行任意 Python；`headless` 接受 script/script_file/file/outdir/preload/timeout_ms/out_json/args，在 fresh `blender -b` 中执行，保留 `HEADLESS {json}` 结果契约。
- 状态机：`queued -> assigned -> running -> completed | failed | canceled`；终态不可变。每次状态变化写事件（时间、slot、pid、原因），status 可查。
- 容量：pending 上限 64；默认 GPU slot 1、CPU slot 2；render 用 GPU，headless 默认 CPU，显式声明 GPU 时占 GPU slot。满队列返回 `QUEUE_FULL`，不无限等待；FIFO + 优先级 + 配额。
- 持久化：`runtime/state/queue.json`（或仓库既有数据目录下的等价 JSON），原子写；pending 上限只有 64，JSON 足够，不引入 SQLite；Gateway 重启后 queued 保留、running 标记 `failed/gateway_restart`、终态和产物引用保留；不重放 running。
- 取消：幂等，终止目标进程树（Windows job object / Unix process group），清理临时目录，不留下 Blender；cancel 终态后 status/collect 仍可读。
- 等待：`wait` 有界（默认 120s，上限 600s），超时只返回 timeout，不 cancel；collect 返回退出结果、日志、产物清单。
- 失败 envelope 与 D2 相同；job 失败在进程退出后仍可读。virtual-gui 占用 GPU 时 render 排队，headless CPU 不受影响；队列调度器是唯一分配 GPU 的地方。

### D6 — Blender Extension

- 单个产物：`addon/dsh_blender_bridge/` 增加 `blender_manifest.toml`，zip 根为 manifest + package（无 wrapper）；删除 `bl_info`，删除 legacy `dsh_blender_bridge.zip` 安装路径与文档。
- manifest：`type = add-on`、`blender_version_min = 4.2.0`、`version = 2.0.0`、`license = [SPDX:GPL-3.0-or-later]`、`[permissions] network = "..."` 与 `files = "..."`；真实字段以 Blender 4.2 文档为准，用 `blender --command extension validate` 校验。
- 构建：`scripts/build-addon.mjs` 继续作为确定性 builder（固定时间戳/顺序），只是输入布局改为 extension；CI/release 额外跑 Blender 4.2 CLI validate。官方 CLI build 不作为唯一 builder，避免产物哈希不可复现；若 validate 要求官方 builder 的字段，按报错补齐后仍保持自定义 builder。
- namespace：`compat.ADDON_MODULE = __package__` 保留；`runtime/launch/resolve.ts` 改为扫描 `bpy.context.preferences.addons` 与 `sys.modules`，接受 `dsh_blender_bridge` 或 `bl_ext.<repo>.dsh_blender_bridge`；版本读 `ADDON_VERSION`/manifest，不再读 `bl_info`；zip fallback 删除。
- 安装/启用：用户通过 Blender Extensions 安装；host 只检测/报告，不静默安装。`virtual-gui` 启动前必须检测 extension 已安装且启用；缺失时返回 install/enable 步骤。
- 权限：manifest 声明 network/files；asset 网络调用前检查 `bpy.app.online_access`；关闭 online access 时本地 Gateway 仍工作，网络能力返回 policy error。
- 许可：addon GPL-3.0-or-later；插件根 BSD-3-Clause；保留上游 MIT 声明和 attribution。GPL 文本/声明进入 addon artifact 与文档；不把插件本体改成 GPL。
- 发布：首次上架人工提交 + Blender ID + 人工审核；后续 GitHub Release workflow 通过官方 API 上传 version file + release notes，token 走 secret；token 缺失时 skip/fail 明确，不伪造发布。`publish_extension` 是显式输入，默认 false。
- 版本：插件 3.1.0、addon 2.0.0、manifest 2.0.0、protocol 12；旧 addon 被 Gateway 拒绝；不支持双栈。

### D7 — 六工具面

- 模型面固定 `session`、`op`、`py`、`see`、`run`、`inspect`，不注册第七个工具。旧工具名不出现在 catalog；调用旧名返回迁移提示。
- 映射与职责（每条是「新工具 ← 旧工具」的等价迁移，不是新增能力；`py` 就是 `do` 的改名：执行 agent 写的脚本与短 Python，是执行通道而非建模引擎；早期叙事把第六个工具叫 `render`，本设计统一为 `run`，它同时承载 `kind=render` 与 `kind=headless`）：
  - `session` ← viewport / txn：list/attach/status/doctor/start/stop/lease/pause/resume，以及 txn snapshot/restore/mark/revert/list/prune/drop。只服务 user-gui/virtual-gui；`start`/`stop` 只作用于 virtual-gui；user-gui 用 attach/detach，不控制 Gateway——Gateway 生命周期归 host（D1）。
  - `op` ← plan / loop / opt / preset：执行 catalog 契约算子。catalog 条目带 `requires_session` 元数据：`catalog`/dry-run 为 false（本地只读），执行型 op 为 true；会话门检查的是元数据，不是工具名或 schema 分支，op 的入参与回执形状保持统一。
  - `py` ← do：在 GUI 会话内执行 agent 写的 Python 脚本或短指令；没有会话时拒绝，不启动 headless。
  - `see` ← see / watch：只捕获 GUI viewport；不服务 headless。
  - `run` ← headless / job / perf：submit/status/wait/collect/cancel/list/perf，唯一作业入口；render/headless 由 kind 区分。
  - `inspect` ← commands / cmd：commands/metrics/queue/provider 健康等只读查询；`cmd` 的可写执行必须显式 `mode=execute`，只允许仓库维护的 allowlist，返回 write receipt。默认只读，不把写能力藏在只读语义里。
- `worker` 不作为 session kind 或 tool；内部执行器只服务 op/loop/CI，模型不可见。
- 行为先等价、工具面后替换：先把旧 15 工具的行为契约与测试用例映射到 6 个新工具（fail-first），切片 7 用新工具整体替换模型面；旧工具的定义、注册、catalog 条目与路由在同一次变更删除。通用实现可以下沉为内部模块，但禁止保留旧工具后加一层新→旧转发壳（facade），也禁止新旧工具并存。`inspect` 的写模式单独测试边界，不让 `cmd` 变成隐式写入口。
- 计数：6 tools；7 capabilities；34 families / 228 ops 保持（除非 catalog 显式变更）；38 commands + ping = 39 wire；HTTP routes 以 Gateway 实际路由更新并跑 matrix；protocol 12。
- 旧工具名到新工具名的迁移表写入 CHANGELOG 和操作教程；JSON schema 参数名尽量保持可迁移，但 tool 名不保留别名。

### D8 — 版本、兼容与迁移顺序

- 3.1.0 是 breaking minor：插件与 addon 必须同步升级。建议顺序：先停旧 Gateway/Blender，升级插件，再通过 Extensions 升级 addon；旧 addon 连接会被拒绝并给出步骤。
- Gateway/HTTP API 不保留 3.0.x 兼容；addon protocol 11 拒绝；不做双栈。
- 实施顺序：基线分支 → Gateway 反向连接/握手 → availability → sessions → virtual-gui V0 → job queue → tool surface → extension → 文档/CI → 真机验收 → 发布。每个切片可独立 revert，避免一次合并所有风险。
- 回滚：每个切片单独 PR；Gateway 反向连接与 tool surface 是 breaking，回滚必须整体回滚 3.1.0，不承诺部分兼容。

## Risks / Trade-offs

- **同端口 WebSocket 复杂度**：最小 RFC6455 实现需要帧解析/心跳/背压；P1 已验证方向，但仍是实现风险。缓解：先做 spike + 负向帧测试；若超出预算，回 design 修改单端点定义，不静默加端口。
- **W1 隐藏 desktop 的 GL 行为未知**：CreateDesktop 不保证 GPU/GL 正常，V0 是唯一判据。缓解：W1 失败即 fail closed，IDD 只作为显式 fallback；没有 provider 通过就不发 3.1。
- **VirtualGL/Xvfb 安装前提**：Linux 用户可能没有 Xvfb/VirtualGL/GPU 驱动；插件不能静默安装。缓解：capabilities 先报 prerequisites，文档给发行版命令，软件 GL 只标 preview-only。
- **队列持久化与取消竞态**：重启、取消、进程退出可能交错；用原子写 + 状态机 CAS + 进程树测试覆盖。
- **六工具迁移破坏现有调用**：3.1.0 是 breaking；旧名直接报错，文档给映射。缓解：行为等价测试先迁移，CHANGELOG/README/教程同步，不用别名掩盖。
- **extension 审核/GPL**：首次上架可能被拒或延迟；GPL 与 BSD 边界必须清楚。缓解：先本地 validate + 人工检查，token/CD 只负责后续版本，不承诺自动通过。
- **构建产物与校验**：自定义 builder 与官方 CLI validate 可能不一致；以 validate 为准补齐 manifest，保持确定性 zip 哈希门禁。
- **工作区冲突**：3.1 改动跨 src/runtime/addon/docs/CI；从最新 main 开分支，按切片合并，避免与 terminal-state-closeout 的旧分支混用。

## Migration Plan

1. 前置：确认 `origin/main=82a44bc`、3.0.6 已发布；从 `chore/gateway-topology-3-1-plan` 切 `feat/gateway-topology-3-1`（或先把 plan 合入 main）；记录基线门禁和计数。
2. Gateway/transport：新 Gateway 入口、addon WS client、握手/registry/重连；负向旧 addon、无 9876 监听测试。
3. Availability：共享 envelope、stage 分类、status/doctor 同源、预算收紧；A1–A6 回归。
4. Sessions：user-gui attach/lease/pause、virtual-gui lifecycle、see 边界；worker 退出模型面。
5. Virtual-gui V0：Windows W1 helper、Linux Xvfb+VirtualGL、IDD fallback 评估；V0 报告作为发布门禁。
6. Job queue：render/headless、状态机、持久化、取消/collect、GPU/CPU slot；实现 `run` 的内部提交面（切片 7 暴露为模型工具）。
7. Tool surface：6 工具面重写（删除旧工具定义与注册，不做 facade）、旧名迁移错误、inspect 写边界、契约计数更新。
8. Extension：manifest/无 bl_info、namespace、构建/validate、GPL/权限、CD 上传。
9. 文档/CI：README、操作教程、配置参考、架构、SKILL、CHANGELOG、中英同步；PR 门禁和 release workflow。
10. 真机验收与发布：V0、A1–A6、6 工具、队列、extension 安装升级、pack/install smoke；通过后发 3.1.0，归档本 change。

## Open Questions

- V0 通过后确定的 Windows provider 默认/fallback 顺序、virtual-gui 默认实例上限、低资源降级阈值（由 V0 报告决定，不在实现前写死）。
