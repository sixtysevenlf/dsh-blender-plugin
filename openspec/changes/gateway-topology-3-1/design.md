# Design

## Context

3.1 是一次拓扑升级，不是 3.0.x 的增量补丁。3.0.6、terminal-state-closeout 和 PR #82 已合并，当前 `origin/main` 为 `82a44bc`（本地 main 可能落后，实施前先 fetch）；仓库已有可复用的契约机器（routing.json、contracts.json、exceptions.json、BEHAVIOR_MATRIX、extension 之外的结构门禁），本设计在这些机器上扩展，不另起一套规则文件。

基线：15 个模型工具 / 34 families / 228 ops / 38 registered commands（+ping=39 wire）/ 29 HTTP routes / protocol 11 / addon 1.11。3.1 目标：6 个模型工具 / 7 个 capability / 25 registered commands / 26 wire commands（21 条可由 `blender_inspect` 执行的只读命令 + 4 条内部；wire 另含 ping）/ protocol 12 / addon 2.0.0 / extension manifest 2.0.0 / 插件 3.1.0。

已验收的 A2 PoC v2.1 证据：P1 反向连接与重连 10/10（重连 102.44ms）；P2 多实例路由 6/6；P4 队列模拟 7/7；P5 `blender_see` 延迟 P50 60.402ms / P95 64.193ms，但 crop / pixel probe / dedupe / Cycles 未验证；P3 虚拟显示 BLOCKED；P6 六工具收益 BLOCKED；P7 CLI 只有 stub，CLI 已从 3.1 范围删除。虚拟显示和六工具行为都必须在本 change 内重新验收，不能拿 PoC 结论代替。

仓库约束（AGENTS.md）：结构门禁、契约计数、先红后绿、例外只能进 `config/exceptions.json`、不新增规则文件、旧文件在同一次变更删除。3.1 的 BREAKING 变更必须在 change 中显式声明，并在 CHANGELOG、README、操作教程同步。

## Goals / Non-Goals

**Goals:**

- 让 Gateway 成为唯一服务端，addon 反向连接、可多实例、可重连，旧 addon 被拒绝。
- 让首调失败可执行：统一四问 envelope、start/doctor 一致、P95 ≤3s。
- 把会话收敛为 user-gui / virtual-gui；virtual-gui 的 Windows provider 是 3.1 GA 发布门槛，Linux provider 只作 preview、不阻塞 GA。
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
- 不承诺 Cycles 下 `blender_see` 亚秒；`blender_see` 只服务 GUI 会话，不服务 headless。
- 不在 V0 结论前写死 Windows provider 默认值、virtual-gui 默认实例数或驱动安装方案。
- 不保留 `cmd` 可写执行器，也不把写命令藏进只读 `blender_inspect`。

## Decisions

### D1 — Gateway 单端点与 addon 反向连接

- Gateway 取代当前 runtime 的 `server.ts`，是唯一 server。默认物理进程为 host、Gateway、Blender 三个；host 嵌入 Gateway 的可选模式不在 3.1 交付范围，避免两套生命周期。
- Gateway 的生命周期归 DSH host/plugin，不归 Blender、CLI 或 job-queue：插件启用时注册 owner，首次后端调用时拉起；插件停用或 DSH host 退出时停止并清理；插件重载先停止旧 Gateway，再由新插件实例重新拉起；不 `detached`，也不以 `unref` 让子进程脱离 host 生命周期。Gateway 崩溃由 host 有界重启（退避 + 次数上限），超限返回 `stage=spawn`，不得在旧端口无限重试；Gateway 本身不是 job，job-queue 只承载 `render`/`headless` 两类 Blender 作业。
- Gateway 是每 DSH host 的单例：启动时在状态目录原子写 `gateway-<port>.json`（gatewayId、hostId、pid、endpoint、startedAt）。端口已有 listener 时先请求 `/health`：属于另一个 DSH host 就返回 `stage=config` 的 `GATEWAY_IN_USE`（含 hostId、pid、port），绝不 attach、复用或共享；属于本 host 的插件重载不传递旧句柄：旧 Gateway 必须先停止，新插件实例再启动新的 Gateway；不允许两个 Gateway 并存或留下孤儿。端口被非 Gateway 进程占用仍按 `port_conflict` 处理；两种情况都必须 fail closed。
- Gateway 只监听一个 loopback HTTP 端口（默认 39877；9877 仅作为显式配置保留）。host/tools 走 HTTP；addon 在同一端口通过 WebSocket upgrade 路径 `/addon` 建立控制通道。不新增第二个 listener，旧 addon 的入站监听不存在。
- WebSocket 端点使用仓库内最小 RFC6455 实现（`runtime/transport/addon-ws.ts` 与 addon 侧 `transport.py`），不新增 npm/pip 依赖。P1 已证明该方向可行；如果实现阶段发现同端口 WS 需要超出预算的复杂度，回到 design 修改契约，不能静默加一个控制端口。
- addon 删除入站 server：`extension/dsh_blender_bridge/server.py` 的 accept/listen 路径删除，改为 client 连接、心跳、重连。`bpy.app.background` 仍拒绝加载 addon。
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
- 诊断预算：`config` 失败立即返回；`spawn` 使用显式 executable 检查；`transport`/`handshake`/`addon`/`bpy` 各自有硬超时。首调失败 P95 ≤3s；当前 3.0.6 的 failed-first-call 基线约 status 7.5s、doctor 6.5s，是待收紧的基线，不是目标。
- P95 口径：在本地 loopback、Gateway 已 ready、每个新 DSH session 的第一次 session-bound 调用上采样；A1–A6 每个失败类至少 20 次冷调用（不含安装、同意和 Gateway 启动），从 tool 入口计时到 envelope 返回，合并样本计算 P95。stage 子预算：config/session 决策 ≤100ms，spawn 预检 ≤250ms，transport/handshake/extension/bpy/queue/render 各自有 ≤3s 硬超时；不允许用健康暖调用替代失败首调。
- 本机健康暖调用基线（2026-11-07，3.0.6，插件已启用、Blender 已打开；N=30，每样本新连接）：`/health` P50 0.8ms / P95 19.4ms；`/status` 53.2ms / 53.9ms；`/doctor` 106.5ms / 108.0ms。该数据只说明健康路径的传输/探测下限，不能替代 failed-first-call P95；A1–A6 失败场景仍需按上述口径采样。
- 移除 3.0.x 的嵌套重试链：诊断只做有界的单层探测，超时返回结构化 timeout，不把多个失败合并成 backend unreachable。
- `fix` 必须可执行（命令、配置键、安装步骤）；`fallback` 必须安全（显式不可用或降级路径），不得启动可见 GUI、伪造输出或回退旧拓扑。
- 3.0.6 的 A1–A6 场景作为回归用例保留：无 exe、Steam 探测、半开 addon、commands 断连、全门禁、五步时间线。

### D3 — 会话归属、scope grant 与人机协作

- 会话只有两种 kind：`user-gui` 与 `virtual-gui`。`headless` 是一次性 job，不是 session；`blender_session(kind="headless")` 必须返回校验错误。
- `user-gui` 只 attach 用户已启动的实例，不由 agent 再拉一个可见 GUI。attach 在单实例时可省略 ID，多实例必须显式，否则 ambiguity。
- 首调会话门：`blender_session` 的 `list`/`status`/`doctor`/`start`/`stop` 是 pre-session 例外（前三者只读，`start`/`stop` 管理 virtual-gui）；执行型 `blender_op`、`blender_py`、`blender_see` 以及需要 ready 会话的 `blender_session` 子操作解析不到 ready GUI session（`user-gui` 或本 DSH session 拥有的 `virtual-gui`）时，不得自动启动 `virtual-gui`，也不得把调用转给 headless/worker；必须返回 `stage=session` 的决策请求，明确告知“Blender 没起来”，并给出两个显式选项：拉起 Blender 后 attach，或用户同意后启动 virtual-gui。`blender_op` 的 `catalog`/dry-run 等本地只读操作不要求会话，直接返回目录/校验结果；只有真正执行 `blender_op` 才进入这道门。`blender_session start kind=virtual-gui` 受宿主许可门控制：默认由插件配置 `allowVirtualGui=false` 关闭，人类可在 DSH 的 Plugins → Blender 配置卡（或右侧栏 Blender Presence 面板）打开；配置卡在启用前检测依赖并询问是否安装，拒绝则保持关闭，已启用后缺依赖会提示安装或关闭；未获许可时工具在探测/拉起 Gateway 之前返回 `stage=session` 的 `virtual_gui_not_allowed`，不创建会话。许可状态只存在 DSH host 进程，经浏览器认证 RPC 读写；Gateway 与 virtual-gui 的 Blender 子进程都不保存它。这是工具级/UX 门，不是对抗已拿到 `blender_py` 任意 Python 的隔离边界。3.0.x 的 GUI→worker/headless 自动降级链在 3.1 删除，降级只能是用户确认后的显式动作。
- 会话归属：一个 `user-gui` 同一时刻只属于一个 DSH session，owner 为 `(hostId, dshSessionId)`。owner 的任一 agent 首次 `blender_session attach` 时 claim；owner 显式 detach、DSH session 结束、host 退出或 crash/TTL 到期时释放。释放前另一个 DSH session 不得 attach、读场景、`blender_see`、执行 `blender_op`/`blender_py`，只能得到 `stage=session` 的 `session_in_use`（含 owner 与过期时间），不是每次写 409；释放后另一个 DSH session 才可重新 claim。owner 下的主 agent 与 subagent 可同时 attach；ownership heartbeat 30s、TTL 120s；main 是 owner claim 时记录的 `agentId`（host 注入 `role=main`），Gateway 只接受 main 执行 grant/revoke。
- 同一 owner session 内不再使用会话级单写者，写协调改为 `scope grant`：写操作必须声明 scope，`exclusive` 用于全局 mutation（join/apply modifier/boolean/`blender_py` 执行/全局导入导出/场景设置），`collection:<name>`、`prefix:<name>`、`object:<session_uid>` 用于对象范围（可组合成集合；`session_uid` 不可用时用 `instanceId:scene:name:type` 作为稳定标识，回执同时带可读名称）。重叠按对象覆盖判定：`exclusive` 与任何非空 scope 重叠，`collection`/`object` 按对象成员，`prefix` 按名称前缀。互不重叠的 grant 可并行；重叠返回 `409 scope_conflict`，含 holder、scope 与过期时间。读、`blender_see`、presence 不需要 grant。
- grant 的 holder 是 `(hostId, dshSessionId, agentId)`，TTL 30s、heartbeat 10s；调用结束、agent 断线、owner 释放时立即失效，过期可回收。默认 fail-fast；可选有界 `wait_ms`（≤5s）。只有 owner claim 时记录的 main agent（host 注入 `role=main`）可显式 grant/revoke 给 subagent，subagent 默认不继承父 grant；revoke 后下一次写失败并要求重读。job 记录 owner，collect/cancel 允许 owner、父 agent 或 Team Lead。
- `blender_py` 写占用：`blender_py` 固定 `exclusive`，不接受 collection/prefix/object 窄 scope；exclusive grant 就是占用凭证，不引入 job queue。同一时刻只有一个 `blender_py` 调用：第二个 `blender_py` 返回 `py_busy`（含 holder、started_at、retry_after），可选 `wait_ms` ≤5s；活动 `blender_py` 期间 grant 不按 30s TTL 过期，完成/失败/超时/断线才释放。`pause` 拒绝新的 `blender_py`，但不中断已运行 Python；超时回执标 `py_still_running`，会话保持 busy，直到 addon 返回或连接断开。
- human 是 Blender UI 里的真人，不是 DSH caller/agent：不进入 presence、不持有 grant，始终可以直接编辑。N 面板提供 pause/resume；pause 阻止所有 agent 写，但不阻止读、`blender_see`、presence。addon 记录人改过的对象 revision；agent 的 grant 覆盖被改对象时，下一次写返回 `human_modified`，要求重读该 scope，不静默覆盖。3.1 不做同对象自动合并、事务或 per-actor undo。
- 同一 user-gui 的多个 agent 必须有 presence：Gateway 保存每个 caller 的 display name、颜色、观察位（from/look_at/lens 或 view matrix）、选中对象 `session_uid`、活动与持有 scope，另记录 human pause 状态；addon 通过 `SpaceView3D.draw_handler_add` 的 POST_VIEW/POST_PIXEL 绘制视锥、标签和选中包围盒，不修改用户视口、不写场景。人可以在自己的视口里看见 agent 正在看哪里；观察位由 `blender_see` 的 custom/camera 视角更新（见下）；`blender_session` 的 `presence` 只读查询返回同一份数据。presence 仅限 loopback，不把 Blender 数据发到网络；human 不作为 agent 绘制。
- `virtual-gui` 由 Gateway 通过 DisplayProvider 启动/停止，agent 专用、用户不可察；归属创建它的 DSH session，只有 owner 的 agents 可 attach，owner 结束/断线时 Gateway stop 并清理，另一个 DSH session 只能创建自己的实例；只有 addon handshake 成功后会话才算 ready。实例上限可配，默认值由 V0 决定；低资源时降级策略也由 V0 决定，不在本设计写死。
- 会话隔离：每个实例独立 scene/Python namespace/session ownership/scope grant；一个实例断线不改变其他实例 readiness。跨实例执行返回错误，不能落到默认实例。
- `blender_see` 只属于 GUI 会话，走 `GPUOffScreen` viewport capture；`worker` 的 Workbench see 从模型面移除，worker 只保留内部执行/CI 诊断，不作为 session kind 或 tool。`blender_see` 失败不得回退 headless Workbench。
- `blender_see` 的 viewport/custom 预览是固定档位，不接受任意数值：`aspect` = `1:1|4:3|16:9`；`max_size` 按比例只能取 `1:1` 的 `544|768|1024|1280`、`4:3` 的 `640|960|1280`、`16:9` 的 `1024|1152|1280`。非法值或非法组合返回 `invalid_params` 并列出允许项，**不夹取**。实际尺寸分别为 `544×544|768×768|1024×1024|1280×1280`、`640×480|960×720|1280×960`、`1024×576|1152×648|1280×720`；最小总像素不低于 `544×544`，最大不超过 `1300×1300`（1:1 的 1280² = 1,638,400 < 1,690,000）。模型面删除 `view_size`；`full=true`（仅 `mode=frame`）固定 1024 长边并保留区域比例，不使用 `aspect`/`max_size`。默认 `aspect=16:9`、`max_size=1024`；只给比例不给 `max_size` 时取该比例最小档（544/640/1024）。比例变化不得拉伸场景，也不得改场景、选择或用户视口；回执必须报告实际宽高。子代理交接块与工具描述同步 viewport/custom 档位。
- `blender_see` 用 `viewpoint` 选择取景来源，和 `mode`（采集模式，区别于 `view_mode` 的渲染模式）正交：`viewport`（默认，当前 GUI viewport）、`camera`（场景相机 camera view）、`custom`（`from`/`look_at` 的离屏机位）。三者并列，不允许混用。`viewpoint=custom` 必须同时给 `from` 和 `look_at`；`viewpoint=viewport` 不接受 `camera`/`from`/`look_at`/`lens`/`ortho`/`ortho_scale`；`viewpoint=camera` 不接受 `from`/`look_at`/`lens`/`ortho`/`ortho_scale`/`full`/`area`/`aspect`，只允许 `mode=frame`，`camera` 参数可选（`true`=活动 `scene.camera`，字符串=指定相机对象）。camera view 只读当前帧的 evaluated 相机（object matrix、约束、驱动器、lens/sensor/shift/ortho 等动画数据），不调用 `frame_set`、不插/改关键帧、不改 `scene.camera`/用户视口/选择/当前帧；画面比例取最终渲染分辨率，`max_size` 只作长边上限，实际宽高按相机比例计算并回执；camera 预览不套用 viewport/custom 的 `aspect`/固定档位/最小总像素限制；`max_size` 默认 1024，允许 544/768/1024/1280，只作长边上限。`PANO` 返回 `unsupported`（提示 `blender_run(kind=render)`），DOF/运动模糊不承诺，标 `preview_only`。相机动画预览不走 see，见 D5 的 render。
- `blender_see` 用 `mode`（采集模式，区别于 `view_mode` 的渲染模式）区分两种采集模式，共用上面的 GUI 会话边界和错误契约（固定档位仅适用于 viewport/custom）；`mode` 默认 `frame`：
  - `mode=frame`（单帧）：按 `viewpoint` 抓取 viewport、camera 或 custom，一次回执 1 张图；字段按上一条的 viewpoint 归属校验；回执带 viewpoint、实际宽高、hash、capture_ms，camera 额外带相机名、frame 和 evaluated 标记。
  - `mode=sequence`（多帧/序列）：只支持 `viewpoint=viewport` 或 `viewpoint=custom`；`viewpoint=camera` 返回 `invalid_params` 并提示用 `blender_run(kind=render)` 做相机动画预览。在 `seconds`（默认 2，范围 0.2–20）和 `fps`（默认 4，范围 0.5–8）指定的墙钟时间窗内采样：`viewpoint=viewport` 采样当前 GUI viewport（拒绝 `from`/`look_at`）；`viewpoint=custom` 必须给 `from`+`look_at`，用固定离屏矩阵采样自定义视角（不建相机、不改 `scene.camera`/用户视口/选择/当前帧）；不根据参数自动推断 viewpoint。`round(seconds×fps)` 必须在 1–32，超出返回 `invalid_params`，不夹取；不跳转场景帧、不执行 code；`full`/`area`/`diagnostics`/`force`/`code` 一律 `invalid_params`；`lens`/`ortho`/`ortho_scale`/`shading`/`overlays`/`view_mode` 仅随自定义视角允许；使用同一套 `aspect`/`max_size` 固定档位和默认值。回执带 `timeline`（每项 `t_ms` 相对采样开始、`capture_ms`、`hash`，附图帧带 `image_index`）、`distinct`、`static`、`avg_ms`；最多附 6 张按 distinct hash 均匀抽取的帧，全静态只附 1 张；单帧采样失败记入 `timeline.error` 并继续，全部失败返回最后一次错误；不回落 headless/worker，不返回逐像素分析（分析留在后续 `blender_op` vision change）。
  - 两种模式都只接受 ready GUI session：`blender_session` 单实例可省略、多实例必须显式，解析不到返回 `stage=session` 决策请求；`auto` 与 worker 名移除。`blender_see` 不接受 `code`；旧 `watch` 的 code 用法先调 `blender_py`，再调 `blender_see(mode=sequence)`。`frame` 传 `seconds`/`fps` 返回 `invalid_params`；`sequence` 传 `code`/`full`/`area`/`diagnostics`/`force` 返回 `invalid_params`；`full`/`area` 只属于 `viewpoint=viewport`，`custom` 传 `full`/`area` 返回 `invalid_params`；`from`/`look_at` 只属于 `custom`；`camera` 只属于 `camera`。
  - `blender_see` 与 presence：custom 的成功回执把该 caller 的观察位发布为 `(from,look_at,lens,ortho,ortho_scale)`；camera 的成功回执发布 evaluated `view_matrix` + 相机名 + 当前 frame；`sequence` 在首个成功帧发布一次固定 pose，不逐帧刷新；当前 viewport 标记 `follow-user`，不画独立视锥。presence 更新是 caller 级只读状态，不需要 scope grant，也不改用户视口；camera 预览不创建相机、不改 `scene.camera`。
- 资源协调：每个 virtual-gui 实例占 1 个 GPU slot；virtual-gui 占用 GPU 时，render 作业排队；会话与队列由 Gateway 的同一个资源账本管理，不超卖 GPU。

### D4 — DisplayProvider 与 virtual-gui V0

- 抽象接口：`start(profile)`、`stop(handle)`、`health(handle)`、`capabilities()`。`start` 返回 provider handle、display endpoint、Blender 启动描述；`stop` 幂等并释放进程树/显示资源；`capabilities()` 报告平台、GPU/GL、prerequisites、是否需要用户授权。
- Windows W1（即早期讨论的 W0；首选且必需）：自写 Win32 helper（C++，无第三方依赖）实现 `CreateDesktopW` + `CreateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `CreateProcessW(CREATE_SUSPENDED, lpDesktop)` + `AssignProcessToJobObject` + `ResumeThread`；Blender 是隐藏 desktop 上的 normal window，不是最小化，默认任务栏/Alt-Tab 不显示。helper 代理 stdout/stderr/exit，失败必须 fail closed，不能回退到用户默认 desktop。
- helper 源码放 `tools/win-hidden-desktop/`，Windows CI 用 MSVC 构建并作为 Gateway 资源打包；不在仓库提交编译产物，发布包必须包含与源码版本匹配的 helper。
- Windows W2（即早期讨论的 W1；仅 fallback）：签名 IDD 虚拟显示驱动，只有在 W1 未通过 V0 的 GPU/GL 或 viewport parity 时才启用；W1 通过时 W2 不进入门禁，W2 失败不单独阻断 3.1。安装必须用户明确同意、不能静默，报告驱动/UAC 前提和许可证/分发限制。
- Linux L1（首选）：Xvfb 提供 X display，VirtualGL 把 GLX/EGL 转发到真实 GPU；每个实例独立 `:N`。provider 启动 Blender 前必须强制 X11（清除 `WAYLAND_DISPLAY`、`XDG_SESSION_TYPE=x11`），不能继承 WSLg/Wayland；VirtualGL 必须显式走 EGL device（如 `vglrun -d egl`），不能依赖 `vglrun -d :N` 默认选到 GLX/llvmpipe。stop 时按进程树终止 Blender、vglrun、Xvfb，重复 stop 幂等；只读 `-ac` 仅用于 loopback provider。GL renderer 为 llvmpipe/软件实现只能标 `preview-only`：feature gate 未显式开启 Linux preview 时必须在 start 时 fail closed，不能通过 gate；开启 preview 后也不得冒充硬件 GL，也不得作为 Windows GA 验收证据；非 root 场景必须在 spawn 前检查 `/dev/dri` 读权限，无权限时 fail closed 或只标 preview-only，不能静默回退 llvmpipe。
- 不采用 WSL/WSLg/WSL+Xvfb、Xdummy、Weston headless 作为 3.1 provider；未来版本再评估。
- V0 bake-off 是 **Windows GA** 的发布门槛：Windows W1（W0）跑验收；W1 通过即满足 3.1 GA 门禁，W2 不要求，W2 失败不单独阻断。W1 失败才评估 W2；W1/W2 都失败则 Windows GA 不发布。V0 不是 apply 前代码门槛；可以先实现和合并代码。Linux L1 是 **preview 质量门禁**，不是 3.1 GA 门禁：不要求再做真实 GPU 实机 PoC，但开启 Linux preview 前必须通过自动化 `blender_see` 链路测试与 prerequisites 检查；Linux preview 不得被描述为 GA 支持，默认关闭（见 D4.1）。
- **Windows GA V0 验收**（Blender 基线固定 4.2.10 LTS；5.x 结果只作技术探查）：`bpy.app.background == False`；GL renderer 为真实 GPU，非 llvmpipe/软件实现；`blender_see` 通过插件 addon 的 GUI capture，并与同一 GPU/驱动/Blender 版本的真实 user-gui viewport 结果 parity（独立 Blender 截图或仅按文件大小判定不算）；2 个并发实例严格互不串场景（验收窗口内不得有第三个实例）；stop 幂等（SIGTERM/timer 正常退出、重复 stop 无孤儿）；截图像素内容校验（非全黑/非单色，双实例内容可区分）；安装前提可查询；锁屏/RDP/无显示环境行为已记录。
- **Linux preview 验收（不是 GA 门禁）**：不要求新的实机 GPU PoC；用 Linux CI（Xvfb + Blender 4.2.10 + 真实 extension/Gateway）自动化验证插件 `blender_see` 返回有效 GUI PNG、像素非全黑/非单色、错误路径不回落 headless/Workbench；llvmpipe 只验证调用链，不作为硬件 GL 证据。已有真实 Ubuntu + RX 7900 XTX 的 EGL/Xorg/双实例/stop 证据作为参考，不作为 GA 承诺；非 root 账号必须能读 `/dev/dri`，否则 provider 必须 fail closed 或 preview-only；`user-gui` parity 是 Linux preview 的已知限制。
- user-gui parity 环境必须是真实 GPU 桌面：物理桌面，或同机真实 Xorg（Xvfb 之外的 Xorg + VNC/dummy display）优先；普通 VM 的 virtio-gpu/virgl/llvmpipe 桌面不算，不能拿普通 VM 截图当 parity；GPU passthrough VM 只有在完整记录硬件/驱动/Blender 版本并经评审后才能作为 parity 环境。
- 缺依赖是代码级硬门：provider 的 start 路径在 spawn 任何进程之前检查 prerequisites，缺失即拒绝；不存在 force/unsafe/skip-check 开关，agent 的意图不能绕过。UI 与 agent 都只能看到“不可用 + 缺什么 + 怎么装”，不能“先试一下”。
- 依赖引导不是静默安装：provider `capabilities()` 报告 prerequisites 与安装状态；host 的 `doctor --fix` 与 addon 偏好设置按钮共用同一张 prerequisites 描述表；Ubuntu 22.04/24.04 与 Debian 12 的 `xvfb` 走发行版 apt（`pkexec` 一键安装）；`virtualgl` 先探测发行版仓库，Ubuntu 24.04 无包时给官方 `.deb`/官方仓库步骤，绝不能给不可执行的 `apt install virtualgl`；Fedora/Arch/无 polkit 环境只给准确命令；GPU 驱动只检测不安装；安装后必须复检 capabilities，失败保留原始 stderr 且不得把 provider 标为 ready。
- 若 Windows W1（W0）或 W1 失败后的 W2 都没有 provider 通过 Windows GA V0：3.1 不发布，也不允许把 render/headless 当作替代；发布报告必须写明失败的测量项。Linux preview 不阻塞 3.1 GA；Linux preview 未通过自动化 `blender_see`/能力门禁时默认关闭，不能宣称 Linux virtual-gui 可用。

#### D4.1 — virtual-gui feature gate 与 Linux preview

- virtual-gui 是现有 bundle 内的显式 feature gate，不拆第二个 bundle/包；配置为 `features.virtualGui`（实现时定 `off` / `auto` 等枚举），Windows 在 W1 V0 通过后默认 `auto`，Linux 默认 `off`，preview 需用户显式开启。
- `capabilities()` 返回 `enabled`、`platform`、`preview`、`supported`、`prerequisites`、`missing`、`requiresApproval`；`doctor` 与 `blender_session start` 在 off、缺依赖、缺 `/dev/dri` 权限时返回 `stage=config` 与可执行 fix，不 spawn 任何进程，不静默安装，不回落 headless/worker/llvmpipe。
- Linux preview 仍必须走真实 GUI addon capture；自动化测试用 Xvfb + Blender 4.2.10 + 真实 extension/Gateway 验证 `blender_see` 的 PNG、像素和错误路径，llvmpipe 只作链路验证，不能作为硬件 GL/GA 证据。不要求新的实机 GPU PoC；已有实机证据只作参考。
- feature gate 关闭时工具面仍是 6 个工具；`blender_see`/`blender_op`/`blender_py` 在无 ready GUI session 时按 D3 会话门拒绝，不因关闭 virtual-gui 而转 headless。

#### Linux L1 V0 预验证记录（2026-11-07，两轮，PARTIAL，Linux preview 参考，不计入 Windows GA）

- 第一轮（初测，Blender 5.2.3）：Ubuntu 24.04.5 / AMD RX 7900 XTX / VirtualGL 3.1.5 / Xvfb；`vglrun -d egl` 为真实 AMD GPU，`vglrun -d :N` 为 llvmpipe；但同步退出挂死、GLX/no-vgl 12 KB 全黑截图 `screenshot_ok=true`、双实例重叠期存在第三个 Blender（s11），且未走插件 `blender_see`、无 user-gui parity、非 root 与 stop 幂等未验。
- 第二轮（follow-up，`local-workspace/linux-v0-followup-2026-11-07/`）：真实 Ubuntu 24.04.5 裸机（`systemd-detect-virt=none`）/ AMD RX 7900 XTX / Blender 4.2.10 LTS（tarball sha256 `7b888b36…`）/ VirtualGL 3.1.5 / Xvfb 21.1.12；`vglrun -d egl` 为 `AMD Radeon RX 7900 XTX (radeonsi, navi31, ...)`、GL 4.6；`vglrun -d :N` 负向为 llvmpipe + 12,007 B 全黑 screen。
- 第二轮已补齐：4.2.10 基线；严格 2 实例（timer 版 0.4–2.2s 采样为 2，keepalive 变体 t+3s 为 2，无第三实例）；红/蓝 render sha 不同且像素分离；TERM 后 0.23s 消失、二次 TERM ESRCH、无孤儿；render/screen 像素统计非全黑/非单色；真实 Xorg `:1` + openbox + x11vnc 下为 amdgpu/glamor/radeonsi，user-gui 与 EGL 的 render sha256 完全相同（`7bb6593c…`）。
- 第二轮仍未关闭：插件 `blender_see` BLOCKED（目标机无 DSH/3.0.6 插件，未用独立截图冒充）；非 root 用户不在 `video`/`render` 组，`/dev/dri` 不可读，`vglrun -d egl` 回退 llvmpipe（未改组，属部署前置）；Xorg 用 1024×768 初始 framebuffer，主机 4 个输出全 disconnected，无物理显示器/交互登录会话，parity 只能算条件性。
- 结论：Linux L1 V0 = PARTIAL，但 Linux 只作 preview，不阻塞 3.1 GA；5.20a–5.20c、5.20f 的预验证已补齐，5.20e1 把非 root `/dev/dri` 权限列为部署前置。插件 `blender_see` 改由 Linux CI 自动化链路测试覆盖（Xvfb + Blender 4.2.10 + 真实 extension/Gateway；llvmpipe 只验证链路），不再要求目标机实机 PoC；5.20d 作为 preview 门禁保留，未通过前 Linux virtual-gui 默认关闭。

#### WSL PoC 记录（2026-11-07，补充证据，不计入 V0）

- 环境：WSL 3.0.3.0 / Ubuntu 24.04 / Blender 4.2.10（`/opt/blender/blender`）/ Xvfb / Mesa 25.2.8 / RTX 4070 SUPER；VirtualGL 3.1.5 从 GitHub Release 的 `virtualgl_3.1.5_amd64.deb` 安装（Ubuntu 24.04 仓库无 `virtualgl` 包）。
- GUI 与 GL：`bpy.app.background == False`；默认 GL 为 `llvmpipe`（只能 preview-only）；设置 `GALLIUM_DRIVER=d3d12` 后 renderer 为 `D3D12 (NVIDIA GeForce RTX 4070 SUPER)`，`glxinfo -B` 报 Accelerated: yes。
- 截图与并发：单实例 Workbench 640x480 截图成功；Xvfb :101/:102 上两个实例并发 8s，红色/蓝色场景输出互不串。
- 显示服务器差异（关键）：WSL 默认设置 `WAYLAND_DISPLAY=wayland-0`，即使设置 `DISPLAY=:110`，Blender 4.2.10 仍选择 Wayland；`bpy.ops.wm.quit_blender()` 在这种情况下 exit 139，日志先出现 `GHOST/Wayland: xdg_wm_base@... protocol error 4`。这不是“无 DE”导致，也不是 Mesa teardown 的必然结果。
- 强制 X11 后：`WAYLAND_DISPLAY=` + `XDG_SESSION_TYPE=x11` + `GDK_BACKEND=x11`，Xvfb :110 + openbox 下 API 退出 exit 0；WSLg 的 XWayland `:0` 下 API 退出 exit 0；alt+F4 关窗 exit 0；SIGTERM exit 143 且无孤儿；`blender -b` exit 0。
- VirtualGL：`vglrun -d :110` + 强制 X11 + Xvfb D3D12 下，Blender GUI 的 `DSH_RENDERER=D3D12 (NVIDIA GeForce RTX 4070 SUPER)`，`render.opengl` 与 `screen.screenshot` 成功，`wm.quit_blender` exit 0；两个 `vglrun` Blender 实例并发均 exit 0，截图成功。`glxspheres64` 也报告 D3D12 renderer。注意 `vglrun glxinfo` 在 Xvfb 上会 exit 139（VirtualGL 的 glxinfo 问题），不能作为 VirtualGL 通过/失败的唯一判据。
- 结论：WSL 不是 3.1 目标平台，也不是 Linux preview 的验收环境；WSLg 的 Wayland 退出崩溃可通过强制 X11 规避，VirtualGL 在 WSL + 强制 X11 下能跑 Blender GUI，但这些只作技术参考，不能作为 Linux L1/V0 通过证据。Linux preview 不再要求新的实机 GPU PoC，由 5.20d 的 Linux CI 自动化 `blender_see` 链路测试收口；已有真实 Ubuntu + RX 7900 XTX 证据作为参考，不作为 GA 承诺。证据存本机 `local-workspace/wsl-poc/`（不入库）。

#### Windows GA V0 报告（2026-11-08，结论：PASS）

- 环境：Windows 11 / RTX 4070 SUPER / Blender 4.2.10 LTS（`D:/blender-medium/blender-4.2.10-windows-x64/blender.exe`，Medium 完整性，避开 D:\DSH 整树的 Low 标签）/ 真实 Gateway + committed extension zip + helper 3.1.0；非管理员控制台会话。RDP 无会话不可测，锁屏未执行（会锁住用户桌面），两者都记录为未覆盖场景，不作为通过项。
- 产品路径：不设 `DSH_BLENDER_VIRTUAL_GUI`，走 V0 后的默认 feature `auto`；两个 caller 经宿主许可后并发 `blender_session start kind=virtual-gui`。`tests/e2e/windows_ga_v0.mjs` **32/32 PASS**，原始报告/日志/截图在 `local-workspace/5.5-windows-ga-v0/`。
- 测量结果：capability `feature=auto`、`feature_source=default:v0`、`ready=true`；两个实例的 `instanceId`/Blender pid/私有 desktop 互异，desktop 均为 `dsh-vgui-…` 且各 14 个窗口（默认任务栏/Alt-Tab 不可见）；两实例 renderer 均为 `NVIDIA GeForce RTX 4070 SUPER/PCIe/SSE2`（非 llvmpipe）；GPUOffScreen `method=offscreen`、800×447 截图成功，两图 sha256 不同，红/蓝材质像素均值不串；各自 `execute_code` 的 marker 不跨实例；stop 后无 `blender.exe`/`dsh-blender-hidden-desktop.exe` 残留，二次 stop 幂等返回 `stopped=false`。
- 默认/fallback 顺序：Windows 为 `win-hidden-desktop` → `win-idd`。W1/W0 通过，因此 W2（IDD）不要求、不评估、不阻断；`win-idd` 在 3.1 保持未实现，provider 选择不会静默安装驱动，也不会回退到默认 desktop。Linux 顺序为 `linux-xvfb-virtualgl`（preview）。
- 默认实例上限与低资源阈值（由本报告确定）：`features.virtualGuiMax` 默认 4、范围 1–16、env `DSH_BLENDER_VIRTUAL_GUI_MAX` 可覆盖；总内存 <16 GiB 或可用内存 <4 GiB 时自动降为 2。两个并发实例的验收覆盖 4 上限内的启动与隔离，未以 4 个实例做压力测试（上限/低资源判定由 `tests/unit/js/virtual-gui-lifecycle.test.ts` 与 limits 单测覆盖）。
- Linux preview（单列，不进入本 GA 门禁）：两轮 Linux L1 PARTIAL 记录见上；`tests/e2e/linux_preview_see.mjs` 在 WSL Ubuntu 24.04 + Blender 4.2.10 + llvmpipe 软件路径 **22/22 PASS**（真实 Gateway + addon，`bpy.app.background=False`、`blender_see` 1024×576 非全黑/非单色、stop 无孤儿、stop 后不回落 headless），PR CI 已新增 `linux-preview-see` job（ubuntu-latest + Xvfb + Blender 4.2.10），首次 CI 实跑待推送后补记；Linux 默认 `off`，只有显式 `on` 才进入 preview，llvmpipe 结果不冒充硬件 GL。
- 结论：Windows GA V0 通过，3.1 可以发布；没有失败测量项需要写入发布阻断记录。Linux preview 保持 opt-in，不作为 3.1 GA 承诺。

### D5 — 作业队列

- Gateway 内唯一队列，承载 `render` 和 `headless` 两种 kind。`blender_run` 是唯一模型入口，提交立即返回 `jobId`；没有同步执行路径，没有 `run-`/`job-` 双 ID。
- `render` 只接受声明式参数（file/frames/camera/engine/resolution/samples/output/format/priority），由内部固定入口执行，不执行任意 Python；相机动画预览走 `render`，不走 `blender_see`：低 `resolution`/`samples` 即可做快速预览，关键帧、约束、驱动器由 Blender 在后台渲染进程正常求值，输出 MP4/GIF 或帧序列；`blender_see` 的 camera 只读当前帧，`sequence` 不接受 camera。`headless` 接受 script/script_file/file/outdir/preload/timeout_ms/out_json/args，在 fresh `blender -b` 中执行，保留 `HEADLESS {json}` 结果契约。
- 状态机：`queued -> assigned -> running -> completed | failed | canceled`；终态不可变。每次状态变化写事件（时间、slot、pid、原因），status 可查。
- 容量：pending 上限 64；默认 GPU slot 1、CPU slot 2；每个 virtual-gui 实例占用 1 个 GPU slot；render 用 GPU，headless 默认 CPU，显式声明 GPU 时占 GPU slot。满队列返回 `QUEUE_FULL`，不无限等待；FIFO + 优先级 + 配额。
- 持久化：`runtime/state/queue.json`（或仓库既有数据目录下的等价 JSON），原子写；pending 上限只有 64，JSON 足够，不引入 SQLite；Gateway 重启后 queued 保留、running 标记 `failed/gateway_restart`、终态和产物引用保留；不重放 running。
- 取消：幂等，终止目标进程树（Windows job object / Unix process group），清理临时目录，不留下 Blender；cancel 终态后 status/collect 仍可读。
- 等待：`wait` 有界（默认 120s，上限 600s），超时只返回 timeout，不 cancel；collect 返回退出结果、日志、产物清单。
- 失败 envelope 与 D2 相同；job 失败在进程退出后仍可读。每个 virtual-gui 占 1 个 GPU slot；virtual-gui 占用 GPU 时 render 排队，headless CPU 不受影响；队列调度器是唯一分配 GPU 的地方。

### D6 — Blender Extension

- 单个产物：`extension/dsh_blender_bridge/` 增加 `blender_manifest.toml`；zip 只包含一个顶层 `dsh_blender_bridge/` 文件夹，manifest、`__init__.py` 和模块都在该文件夹内（单文件夹布局），没有 wrapper 或根级散文件；删除 `bl_info`，删除 legacy `dsh_blender_bridge.zip` 安装路径与文档。
- manifest：`schema_version = "1.0.0"`、`id = "dsh_blender_bridge"`、`version = "2.0.0"`、`name`、`tagline`、`maintainer`、`type = "add-on"`、`blender_version_min = "4.2.0"`、`license = ["SPDX:GPL-3.0-or-later"]`；`[permissions]` 的 `network` 与 `files` 必须是带 reason 的字符串；真实字段以 Blender 4.2 文档为准，用 `blender --command extension validate` 校验。
- 构建：`scripts/build-extension.mjs` 继续作为确定性 builder（固定时间戳/顺序），只是输入布局改为 extension；CI/release 额外跑 Blender 4.2 CLI validate。官方 CLI build 不作为唯一 builder，避免产物哈希不可复现；若 validate 要求官方 builder 的字段，按报错补齐后仍保持自定义 builder。
- namespace：`compat.ADDON_MODULE = __package__` 保留；`runtime/launch/resolve.ts` 改为扫描 `bpy.context.preferences.addons` 与 `sys.modules`，接受 `dsh_blender_bridge` 或 `bl_ext.<repo>.dsh_blender_bridge`；版本读 `ADDON_VERSION`/manifest，不再读 `bl_info`；zip fallback 删除。
- 安装/启用：用户通过 Blender Extensions 安装；host 只检测/报告，不静默安装。`virtual-gui` 启动前必须检测 extension 已安装且启用；缺失时返回 install/enable 步骤。
- 权限：manifest 声明 network/files；asset 网络调用前检查 `bpy.app.online_access`；关闭 online access 时本地 Gateway 仍工作，网络能力返回 policy error。
- 许可：addon GPL-3.0-or-later；插件根 BSD-3-Clause；保留上游 MIT 声明和 attribution。GPL 文本/声明进入 addon artifact 与文档；不把插件本体改成 GPL。
- 发布：首次上架人工提交 + Blender ID + 人工审核；后续 GitHub Release workflow 通过官方 API 上传 version file + release notes，token 走 secret；token 缺失时 skip/fail 明确，不伪造发布。`publish_extension` 是显式输入，默认 false。
- 版本：插件 3.1.0、addon 2.0.0、manifest 2.0.0、protocol 12；旧 addon 被 Gateway 拒绝；不支持双栈。

### D7 — 六工具面

- 命名规则：公共模型工具名一律带 `blender_` 前缀（`blender_session`、`blender_op`、`blender_py`、`blender_see`、`blender_run`、`blender_inspect`）；capability/family、`session kind`、`headless` 等内部概念保留不带前缀的名字，避免和工具名混淆。旧的无前缀工具名不注册、不进入 catalog；catalog 与契约测试必须拒绝无前缀注册名。
- 模型面固定 `blender_session`、`blender_op`、`blender_py`、`blender_see`、`blender_run`、`blender_inspect`，不注册第七个工具。旧工具名不出现在 catalog；调用旧名返回迁移提示。
- 映射与职责（每条是「新工具 ← 旧工具」的等价迁移，不是新增能力；`blender_py` 就是 `do` 的改名：执行 agent 写的脚本与短 Python，是执行通道而非建模引擎；早期叙事把第六个工具叫 `render`，本设计统一为 `blender_run`，它同时承载 `kind=render` 与 `kind=headless`）：
  - `blender_session` ← viewport / txn：list/attach/claim/detach/release/status/doctor/start/stop/grant/revoke/grants/pause/resume/presence，以及 txn snapshot/restore/mark/revert/list/prune/drop；txn restore/mark/revert 是写操作，必须持有 `exclusive` grant 并受 human pause 约束。只服务 user-gui/virtual-gui；`start`/`stop` 只作用于 virtual-gui；user-gui 用 attach/detach，不控制 Gateway——Gateway 生命周期归 host（D1）；`presence` 是只读查询，用户视口里的 agent 视锥由 addon 绘制，不是新工具。
  - `blender_op` ← plan / loop / opt / preset：执行 catalog 契约算子。catalog 条目带 `requires_session` 与 `write_scope` 元数据：`catalog`/dry-run 的 `requires_session=false`（本地只读），`blender_op` 执行型为 true；`write_scope` 为 none/exclusive/collection:/prefix:/object:（或条目声明的模板）。会话门检查的是元数据，不是工具名或 schema 分支；`blender_op` 执行型按 `write_scope` 申请 scope grant，入参与回执形状保持统一。
  - `blender_py` ← do：在 GUI 会话内执行 agent 写的 Python 脚本或短指令；没有会话时拒绝，不启动 headless。`blender_py` 固定 `exclusive`，不接受更窄的 collection/prefix/object scope；exclusive grant 是占用凭证，同一时刻只有一个 `blender_py` 写者（见 D3）。
  - `blender_see` ← see / watch：只捕获 GUI 会话的 viewport/camera/custom view；不服务 headless。`mode=frame`（默认，单帧）与 `mode=sequence`（有界多帧时间窗，只支持 viewport/custom，详见 D3）；`viewpoint=viewport|camera|custom` 并列，camera 只做当前帧、输出渲染比例，camera 动画预览走 `blender_run`；viewport/custom 预览使用固定档位：`aspect` 为 1:1/4:3/16:9，`max_size` 按比例只能是 1:1 的 544/768/1024/1280、4:3 的 640/960/1280、16:9 的 1024/1152/1280；非法值/组合报 `invalid_params`，不夹取；模型面删除 `view_size`；默认 16:9 + 1024，只给比例时取该比例最小档；`full=true`（仅 `mode=frame`）固定 1024 长边。`sequence` 只采集帧/hash/时间线，不执行 code、不改场景、不回落 worker。
  - `blender_run` ← headless / job / perf：submit/status/wait/collect/cancel/list/perf，唯一作业入口；render/headless 由 kind 区分，render 承担相机动画预览。
  - `blender_inspect` ← commands：只读查询与诊断。`blender_inspect({command:"help"})` 只返回 `blender_inspect` 可执行的只读命令（至少 name/description/access/gate/enabled/pause_safe/args）；`blender_inspect` 只执行这些命令；已知写命令名返回 `wrong_tool` 并指向 `blender_py`/`blender_op`/`blender_run`/`blender_see`/`blender_session`，未知命令返回 `unknown_command` + `did_you_mean`。内部传输命令（`execute_code`、`get_viewport_screenshot`、`send_*`）不列入 help、不可执行；没有 `mode=execute`、没有 write receipt、没有 `cmd` 执行器。
- 命令面迁移：9 条写命令从 addon 命令注册表删除，由 `blender_py`/`blender_op` 承担（`export_scene`→`blender_op deliver_export`/`blender_py`，`set_texture`→`blender_op material_apply`/`blender_py`，`download_*`/`import_*`/`create_*_job`→`blender_op` 资产/生成 family 或 `blender_py`）；`execute_code` 保留为 `blender_py` 的内部传输命令，`get_viewport_screenshot` 保留为 `blender_see` 的内部捕获命令，`send_*_to_dsh` 保留为 host 内部动作；三者都不进入 `blender_inspect`，也不作为模型可见命令。`query/status/search/get_*` 归 `blender_inspect`；telemetry/drain 删除。`blender_inspect` 的 `help` 只列 `blender_inspect` 可执行的只读命令。
- `worker` 不作为 session kind 或 tool；内部执行器只服务 op/loop/CI，模型不可见。
- 行为先等价、工具面后替换：先把旧 15 工具的行为契约与测试用例映射到 6 个新工具（fail-first），切片 7 用新工具整体替换模型面；旧工具的定义、注册、catalog 条目与路由在同一次变更删除。通用实现可以下沉为内部模块，但禁止保留旧工具后加一层新→旧转发壳（facade），也禁止新旧工具并存。`blender_inspect` 没有写模式；写命令按上面的迁移表分别由 `blender_py`/`blender_op`/`blender_run`/`blender_see`/`blender_session` 承担，`cmd` 不再存在。
- 计数：6 tools；7 capabilities；34 families / 228 ops 保持（除非 catalog 显式变更）；命令面从 38 registered / 39 wire 降到 25 registered / 26 wire（21 条可由 `blender_inspect` 执行的只读命令 + 4 条内部：`execute_code`、`get_viewport_screenshot`、`send_selection_to_dsh`、`send_collection_to_dsh`；wire 另含 ping）；删除 9 条写命令、3 条 telemetry 命令和 1 条已注册但 catalog 未列出的 preview（`get_polyhaven_asset_preview`）；HTTP routes 目标为 29 条（不含 `/addon` WebSocket upgrade，单独按 upgrade 登记）；若实现新增/删除 HTTP route，必须在同一 PR 显式更新目标计数、契约源与 `test:matrix`；protocol 12。
- 旧工具名到新工具名的迁移表写入 CHANGELOG 和操作教程；JSON schema 参数名尽量保持可迁移，但 tool 名不保留别名。

### D7.1 — 34 family 目标面

所有 family 只允许 G/R/L 三种目标，不允许 W（worker 不是模型面，只保留内部执行/CI）。G = GUI 会话（user-gui 或 virtual-gui）；R = 走 `blender_run(kind=render)` 的最终渲染作业；L = host 本地执行、不要求 Blender 会话。

| family       | 3.1 target | 备注                                    |
| ------------ | ---------- | --------------------------------------- |
| qc_render    | G          | 最终帧输出走 R，不在 GUI 会话里同步渲染 |
| qc           | G          |                                         |
| assembly     | G          |                                         |
| audit        | G          |                                         |
| motion       | G          |                                         |
| deliver      | G          |                                         |
| generator    | G          |                                         |
| visualreport | G          |                                         |
| shapesolve   | G          |                                         |
| gate         | G          |                                         |
| img          | G          |                                         |
| face         | G          |                                         |
| human        | G          |                                         |
| clearance    | G          |                                         |
| vehicle      | G          |                                         |
| shape        | G          |                                         |
| blockout     | G          |                                         |
| eval         | G          |                                         |
| surface      | G          |                                         |
| material     | G          |                                         |
| sculpt       | G          |                                         |
| fix          | G          |                                         |
| uv           | G          |                                         |
| print        | G          |                                         |
| sweep        | G          |                                         |
| plan         | G          |                                         |
| montage      | G          |                                         |
| pipe         | G          |                                         |
| calib        | G          |                                         |
| gui          | G          |                                         |
| render       | G          | 最终帧输出走 R                          |
| contract     | G          |                                         |
| ext          | L          | host 侧扩展检查，不要求会话             |
| render_guard | L          | host 侧渲染守卫，不要求会话             |

### D8 — addon 零遥测、零数据采集与无推广入口

- 删除 `extension/dsh_blender_bridge/commands/telemetry.py`，以及 `commands/activity.py:19-33` 的 `drain_human_activity`；`commands/activity.py` 只保留 `get_activity_state`。旧 host 调用这三个命令时返回 unknown-command/upgrade-required，不做 shim。
- 删除 `ui/preferences.py:12-23` 的 `telemetry_consent` 与 `_on_telemetry_consent_changed`、`:62-83` 的 Telemetry & Privacy 区块与 `dshbb.open_terms` 调用；删除 `ui/operators.py:226-240` 的 `DSHBB_OT_OpenTerms` 及 `registration.py` 的注册/反注册。Hyper3D/fal.ai/Tencent Cloud 等凭据帮助链接保留。
- 删除 `recorders/edit_capture.py` 整条 consent-gated 采集路径；`command_registry.start_runtime_handlers/stop_runtime_handlers` 不再注册/反注册它，`server.py` 不再 import/调用 `drain_edit_recorder`，`pyrightconfig.extension.json:20` 删除该文件。
- 将 `recorders/user_edit.py` 的 `UserEditRecorder` 收敛为只维护 agent command depth/suppression 的 scope（`begin_agent_command`/`end_agent_command`/`ActivityTracker._suppressed` 所依赖的最小接口）；删除事件 deque、`record_undo`、`poll_operators`、`drain`。`recorders/activity.py` 的 modal/selection/undo/redo 本地判定保留，`get_activity_state` 继续工作且不写盘、不发网络。
- 删除 `config.py:70` 的 `DISCORD_URL`、`ui/panels/index.py:9` 的 import、`:46` 的 `_draw_community` 调用和 `:252-267` 的函数；N 面板不再出现 “Stay updated”/“Join Discord”。若 `textwrap` 因此无其他用途一并删除。
- `command_registry.py`：删除三个 telemetry 命令、9 条写命令与 `get_polyhaven_asset_preview`，并删除 `TelemetryCommands` mixin；`REGISTERED_COMMAND_COUNT` 38→25，`WIRE_COMMAND_COUNT` 39→26；`PAUSE_SAFE_EXTRA` 删除 `drain_human_activity`；`get_activity_state` 仍为 pause-safe。`runtime/catalog/commands.ts`（21 条 `blender_inspect` 可执行只读 + 4 条 internal，internal 不进 help；ping 仅 wire）、`runtime/_probe_tools.ts`、`src/tools/inspect/*` 的 help fixture 同步。
- 测试：删除 telemetry/consent/drain 用例，新增负向用例（三个命令 unknown、preferences 无 telemetry 属性、N 面板无 Discord、代码无 `discord.gg`/`Allow Telemetry`）；更新 `BEHAVIOR_MATRIX`、`test_addon_protocol.py`、`test_addon_command_registry.py`、`test_addon_config.py`、`test_addon_manifest.py`、`test_addon_activity_commands.py`、integration dispatch/matrix。
- 文档：`SECURITY.md`/`docs/zh/SECURITY.md` 不再声明 consent 采集或 drain，改为“addon 无遥测；activity 仅本地内存”；`docs/宿主集成-接口契约-v2.3增补.md:89`、`docs/操作教程.md:1305`、README/CHANGELOG/llms.txt 同步。
- 迁移：3.1 是 breaking；旧 host 调用已删命令得到结构化 unknown + 升级指引；不提供旧命令别名。

### D9 — 版本、兼容与迁移顺序

- 3.1.0 是 breaking minor：插件与 addon 必须同步升级。建议顺序：先停旧 Gateway/Blender，升级插件，再通过 Extensions 升级 addon；旧 addon 连接会被拒绝并给出步骤。
- Gateway/HTTP API 不保留 3.0.x 兼容；addon protocol 11 拒绝；不做双栈。
- 实施顺序：基线分支 → Gateway 反向连接/握手 → availability → sessions → virtual-gui V0 → job queue → tool surface → extension → 文档/CI → 真机验收 → 发布。每个切片可独立 revert，避免一次合并所有风险。
- 回滚：每个切片单独 PR；Gateway 反向连接与 tool surface 是 breaking，回滚必须整体回滚 3.1.0，不承诺部分兼容。

## Risks / Trade-offs

- **同端口 WebSocket 复杂度**：最小 RFC6455 实现需要帧解析/心跳/背压；P1 已验证方向，但仍是实现风险。缓解：先做 spike + 负向帧测试；若超出预算，回 design 修改单端点定义，不静默加端口。
- **W1 隐藏 desktop 的 GL 行为未知**：CreateDesktop 不保证 GPU/GL 正常，Windows GA V0 是唯一判据。缓解：W1 失败即 fail closed，IDD 只作为显式 fallback；W1 通过即不阻断 3.1，W1/W2 都失败才不发 Windows GA。
- **VirtualGL/Xvfb 安装前提**：Linux 用户可能没有 Xvfb/VirtualGL/GPU 驱动；插件不能静默安装。缓解：Linux 只作 preview、feature gate 默认关闭；capabilities 先报 prerequisites，文档给发行版命令，软件 GL 只标 preview-only。
- **feature gate 变成第二套分叉**：offscreen 关闭路径容易出现“只测 enabled 路径”。缓解：off/缺依赖/无权限三条路径都做负向测试，工具面仍只有 6 个工具，不引入第二套 bundle。
- **队列持久化与取消竞态**：重启、取消、进程退出可能交错；用原子写 + 状态机 CAS + 进程树测试覆盖。
- **六工具迁移破坏现有调用**：3.1.0 是 breaking；旧名直接报错，文档给映射。缓解：行为等价测试先迁移，CHANGELOG/README/教程同步，不用别名掩盖。
- **extension 审核/GPL**：首次上架可能被拒或延迟；GPL 与 BSD 边界必须清楚。缓解：先本地 validate + 人工检查，token/CD 只负责后续版本，不承诺自动通过。
- **构建产物与校验**：自定义 builder 与官方 CLI validate 可能不一致；以 validate 为准补齐 manifest，保持确定性 zip 哈希门禁。
- **工作区冲突**：3.1 改动跨 src/runtime/extension/docs/CI；从最新 main 开分支，按切片合并，避免与 terminal-state-closeout 的旧分支混用。

## Migration Plan

1. 前置：从最新 `origin/main` 切 `feat/gateway-topology-3-1`（或先把 plan 合入 main；不写死 commit 哈希）；记录基线门禁和计数。
2. Gateway/transport：新 Gateway 入口、addon WS client、握手/registry/重连；负向旧 addon、addon 进程无监听 socket 测试。
3. Availability：共享 envelope、stage 分类、status/doctor 同源、预算收紧；A1–A6 回归。
4. Sessions：user-gui 归属与 scope grant、human pause、virtual-gui lifecycle、see 边界；worker 退出模型面。
5. Virtual-gui：Windows W1（W0）helper 的 GA V0、IDD 仅在 W1 失败时评估；Linux Xvfb+VirtualGL 只作 preview，用 Linux CI 的插件 `blender_see` 链路测试收口，不再要求实机 GPU PoC。
6. Job queue：render/headless、状态机、持久化、取消/collect、GPU/CPU slot；实现 `blender_run` 的内部提交面（切片 7 暴露为模型工具）。
7. Tool surface：6 工具面重写（删除旧工具定义与注册，不做 facade）、旧名与命令迁移错误、inspect 只读、契约计数更新。
8. Extension：manifest/无 bl_info、namespace、构建/validate、GPL/权限、CD 上传。
9. 文档/CI：README、操作教程、配置参考、架构、SKILL、CHANGELOG、中英同步；PR 门禁和 release workflow。
10. 验收与发布：Windows GA V0（W1 通过即满足，IDD 仅在 W1 失败时）、A1–A6、6 工具、队列、extension 安装升级、pack/install smoke；Linux preview 由自动化 `blender_see` 链路测试收口，不作为 3.1 GA 门禁。通过后发 3.1.0，归档本 change。

## Open Questions

- 无。V0 报告已在上方 D4 收口：Windows provider 顺序、默认实例上限与低资源阈值均已确定。
