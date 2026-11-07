# Tasks

## 1. 前置与基线

- [x] 1.1 同步并开分支：`git fetch origin --tags`；先提交并推送当前 planning artifacts（proposal/design/tasks/7 spec），确保 worktree 干净；`git fetch origin --tags`，以 `origin/main` 为准（当前审计时为 `82a44bc`，不要把这个哈希写死为永久基线）；从 `chore/gateway-topology-3-1-plan` rebase/merge 到最新 main 后切 `feat/gateway-topology-3-1`；验证基线 = 最新 `origin/main` + plan 提交，工作树只有明确保留的未跟踪证据（`local-workspace/` 等），不把 active change 混进实现 PR。（2026-11-07：plan PR #85 已合并到 main `2362508`；已从 main 切 `feat/gateway-topology-3-1`。）
- [x] 1.2 记录基线：在分支上记录 `git rev-parse HEAD`，跑 `npm run build`、`check:finalization`、`check:structure`、`check:exceptions`、`check-plan-ids --enforce`、`test:matrix`、`check:python-static`、`typecheck`、`lint`、`format:check`、`test:unit`、`test:contract`、`test:python`、`check:addon`、`check:build`、`check:git-hygiene`；验证全部 exit 0，并记录 15 tools / 34 families / 228 ops / 38 commands / 29 routes / protocol 11。（2026-11-07 HEAD `2362508`；16 个命令全部 exit 0；日志 `local-workspace/1.2-baseline-2026-11-07T14-17-54-398Z.log`，摘要 `local-workspace/1.2-baseline-summary.md`。）
- [x] 1.3 先红（Gateway）：新增“addon 进程无监听 socket、旧 addon 被拒”的 contract/integration 测试，在未实现前应失败；把失败输出贴到 PR 草稿。
- [x] 1.4 冻结 PoC 输入：把 A2 PoC v2.1 的 P1/P2/P4/P5 证据路径、已知 BLOCKED 项写进 PR 草稿；确认 P3 virtual-display 仍为 BLOCKED，不能作为验收依据。
- [x] 1.5 建立 V0 真机矩阵：列出 Windows 本机、Windows 无管理员、Linux 有 GPU/无 GPU、锁屏/RDP 四个环境；每个环境记录 Blender 4.2.10 LTS 路径、GPU、驱动、Xvfb/VirtualGL 版本；Linux 的 user-gui parity 环境先按“物理桌面，或同机真实 Xorg + VNC/dummy display”登记；普通 VM 的软件 GL 不算，GPU passthrough VM 需单独记录。
- [x] 1.6 清理 active changes：`terminal-state-closeout` 已归档到 `openspec/changes/archive/2026-11-07-terminal-state-closeout`；`vision-motion-analysis` 已移到 `openspec/future/` 并记录在 `openspec/future/README.md`；确认 `openspec list` 只剩本 change。

## 2. Gateway 单端点与 addon 反向连接

- [x] 2.1 实现 Gateway 进程入口与 HTTP 装配：新增 `runtime/gateway.ts`（替代 `runtime/server.ts`）作为唯一进程入口，装配 HTTP server、`/health`、`/addon` WebSocket upgrade 与路由矩阵；默认端口 39877；验证进程可独立启动、`/health` 可探、无第二个 listener。
- [x] 2.2 删除 addon 入站 server 路径：`addon/dsh_blender_bridge/server.py` 的 listen/accept 改为 client 连接；先保留协议编解码单测，验证 addon 进程自身无 listening socket（按 PID 检查；不做机器级 9876 端口断言）。
- [x] 2.3 实现同端口 WebSocket 控制通道：`runtime/transport/addon-ws.ts` 负责 upgrade、握手、帧、ping/pong、关闭；addon 侧 `transport.py` 对应实现；新增帧边界/半包/非法帧单测，验证无新增 npm/pip 依赖。
- [x] 2.3a WebSocket spike 先行：以 2 天为时间盒验证同端口 upgrade、帧解析、心跳与背压；若超出预算，回 design 修改单端点定义，不静默新增控制端口；记录 spike 结论到 PR 草稿。
- [x] 2.4 定义握手 schema 到 `config/gateway.json`（instanceId/kind/version/protocol/project/capabilities 字段、protocol 12 约束）；TS 与 Python 都从同一 schema 读取或生成，验证 schema 漂移会红。
- [x] 2.5 实现 instance registry：连接注册、ready 状态、lastSeen、connectionEpoch、project、capabilities；验证两个实例可独立列出，断线后实例离线但保留最后身份。
- [x] 2.6 实现按 instanceId 路由：单个实例可省略，多实例省略返回 ambiguity 并列出 ID；验证请求只到达目标实例，错 ID 返回结构化错误。
- [x] 2.7 实现重连替换：相同 instanceId 的新连接在握手后替换旧 epoch；旧请求返回 disconnect，不重放；验证 P1 的 10/10 与重连延迟记录。
- [x] 2.8 实现协议/版本拒绝：protocol 11、缺少字段、未知 kind 全部拒绝并给升级/修复提示；验证旧 addon 负向用例通过且不会回退旧入站拓扑。
- [x] 2.9 收口 Gateway 生命周期：唯一 HTTP 端口，端口占用 fail closed；host/tools 只连 Gateway；删除 runtime 直连 addon 的分支；按 PID 验证 addon 进程无监听 socket；机器级只检查 Gateway 端口的归属，不断言 9876 无监听。
- [x] 2.10 定义默认进程模型：host + Gateway + Blender 三个进程；Gateway 生命周期归 DSH host/plugin（插件启用/首次后端调用时拉起，host 退出或插件卸载时停止；插件重载先停旧 Gateway 再启新，不 detached、不交给 Blender/job-queue）；改造 `src/host/backend/spawn.ts`：去掉 3.0.x 的 `detached`/`unref` 存活语义，stdio 接日志文件；host 启动/停止顺序、日志、退出清理可测；Gateway 崩溃由 host 有界重启（退避 + 上限）；验证 host 退出后无 Gateway 残留、崩溃不会在旧端口无限重试。
- [x] 2.11 更新 addon 连接错误文案：addon 未启动、Gateway 未启动、版本不匹配分别给出 stage 和 fix；验证三类错误不会互相冒充。
- [x] 2.12 删除 3.0.x server 相关死代码/测试/文档入口；验证 `check-no-shims`、`check-structure` 不因删除产生未入册例外。
- [x] 2.12a 迁移 `runtime/server.ts` → `runtime/gateway.ts` 的路径引用：`src/constants.ts` 的 `SERVER_PATH`、`src/host/backend/*`、`src/tui/*`、`src/client/index.js`、`scripts/check-build.mjs`/`ensure-build.mjs`、`src/tools/viewport/*`、`src/tools/worker/execute.ts`、`src/tools/do/execute.ts`、`tests/integration/*` 与 `runtime/_probe_tools.ts`；删除 `server.ts`，更新 `check-no-shims` 的 facade/retired 登记、build/parity/install-smoke 引用；新增 grep gate 禁止 `runtime/server`/`server.js` 残留。
- [x] 2.13 记录 Gateway 的单端点测试：`curl /health`、WS `/addon` 握手、非法 upgrade、端口占用、进程退出；作为 PR CI 的 integration 层。
- [x] 2.14 默认端口迁移：Gateway 默认 9877 → 39877；同步 `config/*`、`src/constants.ts`、`src/host/backend/*`、`src/tui/*`、`src/client/index.js`、`src/tools/viewport/*`、`docs/配置参考`、操作教程、测试 fixture、`scripts/*` 与升级说明（写明 addon 升级/替换后必须重启 Blender：无热重载，旧 listener 不会自动释放）；验证默认配置只监听 39877，9877 仅显式配置时生效，并新增 grep gate 检查非测试/非历史文件中的 9877。
- [x] 2.15 端口占用 fail closed 与归因：bind 失败时解析占用者（Windows `GetExtendedTcpTable` / `netstat -ano`，Linux `/proc/net/tcp` + `/proc/<pid>/cmdline`），返回 `stage=config` envelope（port、pid、process）；验证 3s 内失败且不留第二个 listener。
- [x] 2.16 禁止 SO_REUSEADDR 静默共享：Gateway 与 addon 都不以 REUSEADDR 与既有 listener 共存，启动前显式检测端口归属；验证同址双绑无法复现（对照 2026-11-06 的 9876 现场）。
- [x] 2.17 端口冲突回归用例：模拟外部 listener 占用默认端口（含 Blender Lab MCP 扩展真机场景），验证插件 3s 内报 `port_conflict`（含占用者），而不是 `addon timeout`；接入 PR CI。
- [x] 2.18 实现 Gateway 单 host 归属：启动写 `gateway-<port>.json`（gatewayId/hostId/pid/endpoint/startedAt）；`/health` 暴露 owner；第二个 DSH host 请求时返回 `GATEWAY_IN_USE`（stage=config，含 hostId/pid/port），不 attach、不复用；同 host 插件重载先停旧 Gateway、再由新插件实例启动新 Gateway，不传旧句柄。验证两个 host 并发启动只有一个 Gateway、第二个 3s 内失败且没有请求被路由到既有 Gateway；加入 integration 回归。

## 3. 可用性统一契约

- [x] 3.1 新增 `config/availability.json`：stage 枚举、envelope 必填字段、fallback 类型；新增 schema 校验测试，缺字段/未知 stage 必红。
- [x] 3.2 在 `src/contracts/availability.ts` 与 runtime 侧实现同一构造器/序列化；所有 tool/route/job 失败改走该构造器；验证旧 generic error 的对照测试变红后修复。
- [x] 3.3 实现 stage 归因：config/session/spawn/transport/handshake/addon/bpy/queue/render 各自判定函数，禁止 unknown 折叠；用 3.0.6 A1–A6 的六类故障做参数化测试。
- [x] 3.4 统一 status/doctor readiness：Gateway liveness、addon control、bpy probe、instance identity 同源；验证 start 成功时 doctor 同一实例 ready，反之不 ready。
- [x] 3.5 把 `/health` 降为 liveness：不参与 readiness；验证 Gateway 存活但 addon 未连时 status 不报 ready。
- [x] 3.6 收紧诊断预算：config/session 决策 ≤100ms、spawn 预检 ≤250ms、transport/handshake/addon/bpy/queue/render 各自硬超时（≤3s）；失败首调 P95 口径按 design D2：本地 loopback、Gateway 已 ready、每个新 DSH session 第一次 session-bound 调用，A1–A6 每类至少 20 次冷调用，从 tool 入口计时到 envelope 返回，合并样本算 P95，排除安装/同意/Gateway 启动；验证半开 addon、无 exe、无会话、commands 断连、队列满或渲染失败都在预算内返回。本机健康暖调用基线已记录到 design D2（2026-11-07，`/status` P95 53.9ms、`/doctor` P95 108.0ms），但该基线不能替代失败首调采样。证据（2026-11-08）：`tests/e2e/first-call-budget.mjs` 每类 N=20，报告 `local-workspace/3.6-first-call-budget.json`：no-exe（A1）p95 6ms、half-open（A3）p95 2522ms、commands 断连（A4）p95 8ms、timeline（A6）p95 18ms，合并 80 个冷样本 P95 2519ms ≤3000ms；无会话 attach 决策 ≤100ms、queue_full 立即返回 <100ms 由 `tests/integration/js/fallback-safety.test.ts` 与 `tests/unit/js/job-queue.test.ts` 断言；预算常量锁定在 `tests/unit/js/contract/diagnosis-budget.test.ts`；A2 Steam 由 `tests/unit/js/blender-detect.test.ts` 的合成 registry/vdf fixture 覆盖，A5 全门禁由 PR CI 覆盖。
- [x] 3.7 删除嵌套重试链：诊断只做单层有界探测；验证超时返回结构化 timeout，不出现 backend_unreachable 冒充 addon 故障。证据：`tests/integration/js/doctor-bounded.test.ts` 记录 fake addon 请求，/status 与 /doctor 各只发 1 次 execute_code；超时直接返回 addon-unresponsive（stage=addon），无 ping/二次探测。
- [x] 3.8 实现安全 fallback：显式不可用/降级路径；禁止可见 GUI、伪造渲染、旧拓扑回退；用“无 provider”“无 addon”“无 exe”三组测试验证 fallback 文案。证据：`tests/integration/js/fallback-safety.test.ts` 五例覆盖 virtual-gui 无 provider、无 addon、无 exe、commands 断连、无会话；断言 stage/root_cause/fix/fallback 齐全、fallback.kind=unavailable，且 fallback/fix 不含旧拓扑/伪造成功文案（config 段仍展示 legacy 配置键，不在断言范围）。
- [x] 3.9 把 3.0.6 A1–A6 变成回归用例并接入 PR CI；验证无 exe、Steam、半开 addon、commands、全门禁、五步时间线全部通过。证据：PR CI `.github/workflows/pr.yml` 跑 test:unit/integration/contract/python/matrix；A1=fallback-safety + worker/headless/job-exe-guard，A2=blender-detect，A3=doctor-bounded，A4=commands-next-action + fallback-safety，A5=门禁步骤，A6=readiness-agreement。

## 4. 会话归属与 scope grant

- [ ] 4.1 实现会话生命周期、归属与 scope grant：list/attach/claim/detach/release/status/doctor/start/stop/grant/revoke/grants/pause/resume/presence；`start/stop` 只作用于 `virtual-gui`，不控制 Gateway 生命周期（Gateway 由 host/plugin 管理）；本切片只改内部 handler 与 Gateway API，不注册新模型工具；旧 viewport/worker/txn 的测试先迁移到内部调用面，验证切片 7 可整体替换，不出现新旧工具并存。
- [x] 4.2 实现 user-gui attach：只附着已连实例；单实例可省略 ID、多实例必须指定；验证两个实例时省略 ID 返回 ambiguity。
- [x] 4.3 实现会话归属与 scope grant：owner claim/release、grant/revoke/grants、TTL/heartbeat、pause/resume；写操作要求匹配的 scope grant，`blender_op` catalog 提供 `write_scope`、`blender_py` 固定 exclusive；验证第二个 DSH session 得到 session_in_use、同 session 两个 agent 非重叠写可并行、重叠 409 scope_conflict、pause 阻止写但允许读。
- [ ] 4.4 实现 virtual-gui 生命周期：通过 DisplayProvider start/stop，只在 addon handshake 后 ready；验证 start 失败不留下半实例，stop 清理进程与显示资源。
- [ ] 4.5 实现会话实例隔离：scene/Python namespace/session ownership/scope grant 不共享；验证一个实例断线不影响另一个，跨实例执行被拒。
- [ ] 4.6 收口 `blender_see` 的 viewpoint 与 frame/sequence：只接受 `blender_session` 目标，走 GUI capture；验证 headless job 不能 `blender_see`，GUI capture 失败不回落 Workbench。`viewpoint=viewport|camera|custom` 并列：viewport 默认；custom 必须 `from`+`look_at`；camera 仅 `mode=frame`，`camera=true` 用活动 `scene.camera`、字符串选指定相机，只读当前帧 evaluated 相机（含关键帧/约束/驱动器/相机数据），不 `frame_set`、不插/改关键帧、不改 `scene.camera`/用户视口/选择/当前帧；camera 输出用渲染比例，`max_size` 默认 1024、允许 544/768/1024/1280，为长边上限，`PANO` 报 `unsupported`，DOF/运动模糊标 `preview_only`。`mode=frame` 支持三种 viewpoint；`mode=sequence` 只支持 viewport/custom，`camera` 返回 `invalid_params` 并提示 `blender_run(kind=render)`。`full`/`area` 仅 viewport，custom 传 `full`/`area` 报 `invalid_params`；custom 参数仅 custom；camera 拒绝 `from`/`look_at`/`lens`/`ortho`/`ortho_scale`/`full`/`area`/`aspect`；`sequence` 的 `seconds`/`fps`/范围/`timeline`/静态去重/不执行 code 规则不变；`frame` 的 `seconds`/`fps` 与非适用字段继续 `invalid_params`。presence：custom 发布 `from`/`look_at`/`lens`/`ortho`/`ortho_scale`，camera 发布 evaluated view matrix + 相机名 + frame，viewport 标 follow-user。viewport/custom 预览固定档位：`aspect` 1:1/4:3/16:9，`max_size` 按比例固定，非法值报错不夹取；删除 `view_size`；`full=true`（仅 frame）固定 1024 长边。viewport frame 走内部 `get_viewport_screenshot`（其实现已用 GPUOffScreen），该命令保留为 `blender_see` 的内部传输，不进入 `blender_inspect`；custom/camera 走独立 view 路径。验证三种 viewpoint、活动/命名/非相机对象、关键帧相机不改帧/不插帧、渲染比例尺寸、PANO/DOF、非法组合、camera sequence 拒绝、序列范围/32 帧/静态/去重、headless 拒绝、presence。
- [ ] 4.7 把 worker 降为内部执行器：移除模型 tool/session kind 与 Workbench see 的对外路径；op/loop/CI 仍可内部使用；验证 6 工具计数中没有 worker。
- [ ] 4.8 把 txn 迁移到 `blender_session` 子操作：snapshot/restore/mark/revert/list/prune/drop；restore/mark/revert 必须持有 `exclusive` grant 并受 human pause 约束；验证文件级与对象级回滚行为不变，无 grant 时拒绝，旧 txn tool 不再注册。
- [ ] 4.9 实现 virtual-gui 上限与低资源策略：上限可配，默认值留待 V0；超限返回 capacity 错误；验证不因等待而启动额外进程。
- [ ] 4.10 新增会话 integration 测试：多实例选择、session_in_use、ownership 释放/claim、scope grant 并行与冲突、human pause、virtual-gui 停止、断线隔离、`blender_see` 三种 viewpoint、camera 活动/命名/关键帧/渲染比例/PANO/DOF/非法组合、camera sequence 拒绝、单帧/序列边界、尺寸/比例/非法档位、序列自定义视角/不动用户视口/更新 presence、序列不回落；验证 P2 的 6/6 场景继续成立。
- [ ] 4.11 首调会话门：执行型 `blender_op`、`blender_py`、`blender_see` 和需要 ready 会话的 `blender_session` 子操作在没有 ready GUI session（`user-gui` 或本 DSH session 拥有的 `virtual-gui`）时返回 `stage=session` 决策请求；`blender_session` 的 list/status/doctor/start/stop 是 pre-session 例外，`blender_op` 的 catalog/dry-run 等本地只读调用不要求会话；验证不 spawn Blender、不启 provider、P95 ≤3s、错误含两个显式选项。
- [ ] 4.12 显式授权启动 virtual-gui：`blender_session start kind=virtual-gui` 首次返回 `consent_required`，DSH host 取得用户确认后发放一次性短 TTL `consent_token`，模型不能自行设置 consent；start 前复检仍无 user-gui；验证拒绝后不创建会话，后续调用继续要求决策而不自动降级。
- [ ] 4.13 删除自动降级路径：移除 3.0.x 的 GUI→worker/headless 兜底与嵌套重试；验证无 Blender 时首调不产生任何 Blender 进程，错误含可执行 fix。
- [x] 4.14 调用身份透传：在 `src/contracts/tool.ts:32-36` 捕获 `exec.agent`，host 侧 `src/host/http.ts` 为每次调用带 `hostId / dshSessionId / agentId / parentAgentId / callId / turnId`；不再用 `src/constants.ts:135` 的 plugin-pid 代表调用者。验证两个 DSH session 的调用在 Gateway 侧可区分，subagent 的 parentAgentId 正确。
- [x] 4.15 实现 session ownership 与 caller 身份：user-gui 归属 `(hostId,dshSessionId)`；把 `runtime/http/lease.ts:88-99` 的 plugin-pid 互斥改造成 ownership + scope grant，holder 为 `(hostId,dshSessionId,agentId)`；ownership heartbeat 30s、TTL 120s；grant TTL 30s、heartbeat 10s；断线/结束立即释放；移除 `src/plugin-lifecycle.ts:33-37` 的全局 `/lease renewOnly`；保留 `:66-71` 的 Gateway 子进程清理，并让 plugin unload/reload 先停旧 Gateway、再启新 Gateway（不传旧 handle）。验证第二个 DSH session 得到 session_in_use、同 session 两个 agent 可 attach、stale ownership 可回收。
- [x] 4.16 scope 委派与 job owner：只有 host 注入 `role=main` 的 main agent 可 grant/revoke，subagent 默认不继承；不重叠 scope 可并行，重叠 409 scope_conflict；job 记录 owner，collect/cancel 允许 owner、父 agent、Team Lead。验证委派后子 agent 需自行取得 scope、main revoke 生效、job 权限矩阵通过。
- [ ] 4.17 实现 agent presence：Gateway 保存每个 caller 的 display name/color/view pose/selected `session_uid`s/activity/held scopes；addon 用 `SpaceView3D.draw_handler_add` 的 POST_VIEW/POST_PIXEL 绘制视锥、标签、选中包围盒；`blender_session` 的 `presence` 只读查询复用同一数据。验证两个 agent 颜色/视锥独立、绘制不移动用户视口、不改场景、不新增工具。
- [ ] 4.18 同一 DSH session 多 agent 集成测试：一个 owner DSH session 下两个 agent attach 同一 user-gui，并发 `blender_see`/presence 成功，非重叠写并行、重叠 409 scope_conflict；第二个 DSH session 被 session_in_use 拒绝；owner 释放后第二个 session 可 claim；用户视口能看到两个 agent 的视锥。验证无重复 scene/Python namespace，presence 不泄漏到 loopback 之外。
- [ ] 4.19 同步 `blender_see` 的模型契约、子代理交接块与文档：更新 `src/tools/see/register.ts` 的 `blender_see` schema 与描述（按符号定位）（`viewpoint` + `camera` + `mode` + `aspect` + 按比例固定的 `max_size` 枚举，camera 仅 frame、渲染比例、PANO/DOF 规则与 view matrix presence，`sequence` 的 `seconds/fps`、自定义视角机位与 timeline 回执，presence 观察位更新；删除 `view_size`）、`runtime/catalog/tools.ts:12` 的说明、`runtime/classes.ts` 的交接块（按符号定位）（加入固定档位：1:1 544/768/1024/1280、4:3 640/960/1280、16:9 1024/1152/1280，非法值报错；先压掉旧文案保持 ≤3500 字符，当前 3503）、`docs/操作教程.md` 的 `blender_see` 参数表（删除 `view_size` 行，换成档位表）、`llms.txt`、`docs/AI-安装提示词.md`、`skills/blender-modeling/SKILL.md` 的 `blender_see` 段落；同时写明旧 `watch` 的 `code` 先走 `blender_py`、多帧观察走 `blender_see(mode=sequence)`；把 `tests/unit/js/contract/guidance.test.ts` 的 `it.fails` 改为正常断言。验证 guidance selftest 的长度门与内容断言、catalog 文本、文档示例均不再把 560/1600 当作 `blender_see` 默认/上限，也不再把 `view_size` 写成可用的模型参数；同时写明 camera 只做 frame、相机动画预览走 `blender_run(kind=render)`，不在 `blender_see` 里切帧；以上文件引用按符号/段落定位，不依赖行号。
- [ ] 4.20 实现 human pause 与人改冲突：N 面板 pause/resume 控制 agent 写；addon 记录人改过的对象 revision；agent 对被人改过的 scope 写返回 `human_modified` 并重读；验证 human 始终可编辑、pause 只阻止 agent 写、不做自动合并或 per-actor undo。
- [x] 4.21 实现 `blender_py` 写占用：`blender_py` 固定 exclusive，第二个 `blender_py` 返回 `py_busy`（holder/started_at/retry_after），可选 `wait_ms` ≤5s；活动 grant 不按 30s TTL 过期；human pause 拒绝新 `blender_py` 但不中断运行；验证并发 `blender_py` 被拒、长任务不被 TTL 回收、timeout 回执 `py_still_running`。
- [x] 4.22 收口 `blender_session` detach 与 pause 权限：`blender_session` 提供显式 detach；human pause 只能由 Blender UI 的 resume 解除，agent 调 resume 被拒绝；验证 owner 释放后另一 DSH session 可 claim，agent 不能自行解除 pause。

## 5. virtual-gui 与 DisplayProvider V0

- [ ] 5.1 定义 DisplayProvider 接口与配置：start/stop/health/capabilities、provider 选择顺序、capabilities 字段；验证 provider 缺实现时启动失败而不是假装可用。
- [ ] 5.1a 实现 virtual-gui feature gate（同一 bundle，不新增 bundle/包）：配置 `features.virtualGui` 默认 Windows V0 前 off、V0 后 auto，Linux off（preview 显式开启）；capabilities/doctor 暴露 enabled/preview/prerequisites/missing；关闭/未就绪时 `blender_session start` 返回 stage=config、不 spawn、不回落；验证 off、缺 Xvfb/VirtualGL、缺 `/dev/dri` 权限三条负向路径和 6 工具面不变。
- [ ] 5.2 实现 Windows W1（W0，自写 helper）源码：`tools/win-hidden-desktop/` 使用 CreateDesktopW、CreateJobObject、KILL_ON_JOB_CLOSE、CREATE_SUSPENDED、lpDesktop、ResumeThread；单测/手工验证失败路径不落到默认 desktop。
- [ ] 5.3 接入 Windows helper 构建与打包：Windows CI 用 MSVC 编译；发布包包含 helper；验证包内 helper 版本与源码 commit 匹配，未提交二进制。
- [ ] 5.4 实现 Windows provider：通过 helper 启动 Blender，代理 stdout/stderr/exit；验证 `bpy.app.background == False`、窗口非最小化、默认任务栏/Alt-Tab 不可见。
- [ ] 5.5 跑 Windows GA V0 验收（W1/W0 helper）：GL renderer 非软件、GPUOffScreen 截图成功、2 个并发实例隔离、stop 无孤儿、锁屏/RDP/无管理员场景记录；W1 通过即满足 3.1 GA 门禁；把原始日志和截图存入证据目录。
- [ ] 5.6 若 W1 失败，评估 Windows W2（IDD）fallback：只在失败测量项上启用；W1 通过时 W2 不要求、不阻断；验证不静默安装驱动、报告 UAC/签名/许可证前提、用户拒绝时不启动。
- [ ] 5.7 实现 Linux L1 provider（preview）：Xvfb + VirtualGL，强制 X11（清除 `WAYLAND_DISPLAY`、`XDG_SESSION_TYPE=x11`），显式使用 `vglrun -d egl`，每个实例独立 display；验证 `bpy.app.background == False`、硬件 GL 路径 renderer 为真实 GPU；llvmpipe/GLX 只能标 preview-only，feature gate 未开启 preview 时 fail closed；stop 按进程树清理 Blender/vglrun/Xvfb 且重复 stop 幂等。
- [ ] 5.8 跑 Linux preview 验收（不是 3.1 GA 门禁）：不要求新的实机 GPU PoC；用 Linux CI（Xvfb + Blender 4.2.10 + 真实 addon/Gateway）自动化验证插件 `blender_see` 返回有效 GUI PNG、像素非全黑、错误不回落；llvmpipe 只验证链路，不作硬件 GL 证据；已有实机证据作参考；不通过时 Linux preview 默认关闭，不阻塞 Windows GA。
- [ ] 5.9 实现 provider capabilities/readiness 查询：列出平台、prerequisites、安装状态、是否需用户批准；验证未就绪 provider 立即失败并给 fix。
- [ ] 5.10 汇总 V0 报告：Windows GA 的通过/失败/PARTIAL 测量、默认/fallback 顺序、virtual-gui 默认实例上限、低资源阈值；Linux preview 单列，包含两轮 PARTIAL 证据与 5.20d 自动化 `blender_see` 状态，不进入 3.1 GA 门禁附件。
- [ ] 5.11 如果 Windows W1（W0）与 W1 失败后的 W2 都无 provider 通过 Windows GA V0：阻止 3.1 发布，记录失败测量项；Linux preview 不阻塞 3.1 GA，但 Linux provider 未通过 preview 验收时必须默认关闭；验证没有“headless 冒充 virtual-gui”的路径。
- [ ] 5.12 更新 virtual-display 文档：feature gate 的默认值与开启方式（同一 bundle，Linux preview 需显式开启）、Windows helper/IDD 前提、Linux Xvfb/VirtualGL/GPU 驱动命令与发行版矩阵、一键安装入口与 sudo/polkit 前提、锁屏/RDP 限制、preview-only 语义与“不装依赖就不可用”的提示；验证中英版本同步。
- [ ] 5.13 依赖描述表：把每个 provider 的 prerequisites（包名、检测命令、安装命令、是否需要 root、装后复检）收敛成一张表，capabilities 与 doctor 共用；验证缺依赖时 3s 内返回 `stage=config` 与可执行命令。
- [ ] 5.14 引导式安装：host `doctor --fix` 与 addon 偏好设置按钮共用同一入口；Ubuntu 22.04/24.04、Debian 12 用 `pkexec` 执行 apt 安装 `xvfb`；`virtualgl` 先探测发行版仓库，Ubuntu 24.04 无包时给官方 `.deb`/官方仓库步骤，不得给不可执行的 `apt install virtualgl`；Fedora/Arch/无 polkit 只给命令；GPU 驱动只检测不安装；装后复检 capabilities，失败保留 stderr。
- [ ] 5.15 无依赖/关闭 feature gate 回归：没有 xvfb/virtualgl 的容器或 `features.virtualGui=off` 时 virtual-gui 不得 ready、不得启动 Xvfb、不得回落 headless；验证按钮/命令输出与真实包管理器错误一致，测试不需要 root。
- [ ] 5.16 缺依赖/feature 关闭代码级硬门：provider start 在 spawn 前检查 prerequisites 与 feature gate，缺失或关闭直接返回 `stage=config`；确认不存在 force/unsafe/skip-check 覆盖路径；验证显式 start 也不产生 Xvfb/VirtualGL/Blender 进程，装好依赖并开启 gate 后 capabilities 下一次查询即变为可用。
- [ ] 5.17 核实 Ubuntu 22.04/24.04 与 Debian 12 仓库中的 `virtualgl` 包可用性；若发行版仓库没有该包，文档与 `doctor --fix` 必须给出官方仓库/包安装步骤，不能给出不可执行的 `apt install virtualgl` 命令。（WSL Ubuntu 24.04 已实测仓库无包；22.04/Debian 12 仍待核实。）
- [x] 5.18 记录 WSL PoC 补充证据：WSL 3.0.3.0 + Ubuntu 24.04 + Blender 4.2.10 + Xvfb；默认 llvmpipe，`GALLIUM_DRIVER=d3d12` 下为 D3D12 (RTX 4070 SUPER)；单实例截图与双实例并发通过；WSLg Wayland 下 `wm.quit_blender` exit 139，但强制 X11 后 API 退出/alt+F4/SIGTERM 均正常；VirtualGL 3.1.5 安装后 `vglrun Blender` exit 0。记录到 design.md D4；结论仍是 WSL 不是目标平台、不能替代 Linux V0。
- [x] 5.19 定位 Blender GUI 自然退出 segfault：根因是 WSL 默认 `WAYLAND_DISPLAY=wayland-0`，Blender 选择 Wayland 后触发 `xdg_wm_base protocol error 4`，随后 exit 139；强制 X11（`WAYLAND_DISPLAY=`、`XDG_SESSION_TYPE=x11`、`GDK_BACKEND=x11`）后 Xvfb/WSLg XWayland 的 API 退出与 alt+F4 均 exit 0，SIGTERM 143。结论：provider 启动必须在 Xvfb 下显式固定 X11，不能继承 WSLg Wayland；stop 仍保留 SIGTERM/kill 兜底。测试中已清理 WSL crash dump，避免 10G 级堆积。
- [ ] 5.20 记录 Linux L1 V0 PARTIAL（2026-11-07，两轮）：第一轮 Blender 5.2.3 的 EGL/双实例/退出问题与第二轮 follow-up 的 4.2.10 证据见 design.md D4；第二轮已补齐版本基线、EGL 真实 GPU、严格 2 实例、像素内容、stop 幂等、真实 Xorg parity。证据存 `local-workspace/linux-v0-2026-11-07/` 与 `local-workspace/linux-v0-followup-2026-11-07/`（均不入库）。Linux 只作 preview，该记录不阻塞 3.1 GA；5.20d 的自动化 `blender_see` 链路测试未通过前 Linux preview 默认关闭。
- [x] 5.20a 补 Linux V0 版本基线：已用 Blender 4.2.10 LTS 在真实 Ubuntu 裸机重跑 provider 验收（`blender_see` 除外）；二进制 `/root/opt/blender-4.2.10-linux-x64/blender`，tarball sha256 `7b888b36cf5bd7e35d495eb9717f7a9b645ea5f2fb4fe9850991800b8f08d531`，binary sha256 `0a90dcae…`；Blender 5.2.3 只作技术探查，不计入 V0。
- [x] 5.20b 补 Linux GL 路径：`vglrun -d egl` 已验证 renderer 为 `AMD Radeon RX 7900 XTX (radeonsi, navi31, ...)`、GL 4.6；`vglrun -d :N` 负向为 `llvmpipe` + 12,007 B 全黑 screen，不得作为通过路径。实现 provider 时必须把该路径 fail closed 或只标 preview-only；`vglrun glxinfo` 的已知 exit 139 不作为唯一判据。
- [x] 5.20c 补严格 2 实例：timer 版 0.4–2.2s 采样均为 2，另跑 `quit_mode=none` keepalive 变体在 t+3s 计数为 2，无第 3 个 Blender；`:141` 红/`:142` 蓝 render sha 不同、mean 颜色分离、各自的 GUI 截图只有自己的立方体。
- [ ] 5.20d 补 Linux preview 的插件 `blender_see` 自动化测试：用 Linux CI（Xvfb + Blender 4.2.10 + 真实 addon/Gateway）验证 `blender_see` 返回有效 GUI PNG、像素非全黑/非单色、错误路径不回落 headless/Workbench；llvmpipe 只验证调用链，不作硬件 GL 证据；不要求实机 GPU PoC，也不要求目标机安装 DSH。
- [x] 5.20d1 取消 Linux 实机插件 `blender_see` PoC：目标机无 DSH 不再阻断；改为 5.20d 的 Linux CI 自动化链路测试；不再要求另一台 Linux 机器。
- [x] 5.20e 记录 stop/权限/内容证据：stop 已验（TERM 后 0.23s 消失、二次 TERM ESRCH、无孤儿），截图像素非全黑/非单色、双实例内容可区分；非 root 用户不在 `video`/`render` 组、`/dev/dri` 不可读时硬件 GL 不成立，此后的 provider 必须 fail closed 或 preview-only。
- [x] 5.20e1 将非 root `/dev/dri` 读权限记录为 Linux preview 的部署前置；不要求再做目标机复验，不阻塞 3.1 GA；provider 的 capabilities/doctor 必须显式报权限缺失。
- [x] 5.20f 补 user-gui parity 环境：已在真实 Ubuntu 主机用 Xorg `:1` + openbox + x11vnc 跑通（amdgpu/glamor/radeonsi），非普通 VM；4 个输出全 disconnected、无物理显示器/交互登录会话，作为 Linux preview 的条件性证据接受，不外推为真实用户桌面或 GA 证据。

## 6. 作业队列

- [x] 6.1 定义 job schema 与持久化：jobId、kind、params、state、priority、slot、pid、timestamps、result、artifacts、error；原子写 JSON；验证损坏文件恢复策略。 — types.ts/store.ts：JobRecord + 原子 JSON + 损坏隔离；tests/unit/js/job-queue.test.ts::quarantines/round-trips
- [x] 6.2 先红：submit 立即返回 jobId、状态 queued；未实现同步/as_job 路径；验证旧 headless 同步 API 的测试被替换而不是保留双路径。 — submit 同步返回 queued/id，无同步执行路径；旧 headless 工具面在 7.1 删除
- [x] 6.3 实现 render kind：声明式参数 + 固定内部入口，不接受 script；验证任意 Python 被拒绝，渲染参数落到 Blender CLI。 — spawn-executor.renderPrelude 固定入口；单测拒绝 script；真机 6.13 渲染 2 帧
- [x] 6.4 实现 headless kind：fresh `blender -b`、script/script_file、outdir、preload、timeout、HEADLESS {json}；验证不启动 GUI、不进入 session 列表。 — spawn-executor headless -b --factory-startup --python；真机 HEADLESS json 解析通过
- [x] 6.5 实现状态机与事件：queued/assigned/running/completed/failed/canceled，终态不可变；验证非法转移被拒、每次转移有事件。 — manager.transition 记录事件；单测状态序列与终态不可变
- [x] 6.6 实现容量与 slot：pending 64、GPU 1、CPU 2、每个 virtual-gui 占 1 个 GPU slot、QUEUE_FULL；验证 render 占 GPU、headless 默认 CPU、virtual-gui 占 GPU 时 render 排队、超限立即失败不排队无限等待。 — scheduler.nextAdmission + DEFAULT_CAPACITY；单测 GPU 不超卖/CPU 并发/QUEUE_FULL/virtual-gui 预留
- [x] 6.7 实现调度：FIFO + priority + quota，virtual-gui 占 GPU slot 时 render 等待；验证不超卖、不饿死、优先级与配额可解释。 — priority+FIFO+ownerQuota；单测 decision.reason 可观察且不饿死
- [x] 6.8 实现重启恢复：queued 保留、running 标 failed/gateway_restart、终态/产物保留；验证重启后同 jobId 可 status/collect。 — restore 将 assigned/running 标 gateway_restart；integration restart 用例
- [x] 6.9 实现 cancel：幂等、终止进程树、清理临时目录；验证 Windows job object 与 Unix process group 都不留 Blender。 — cancel 幂等 + taskkill /T/进程组；真机 e2e 取消后 tasklist 无孤儿
- [x] 6.10 实现 wait/collect/list/perf：wait 默认 120s、上限 600s、超时不取消；collect 返回日志/产物、list 支持过滤、perf 返回队列指标；验证大日志不阻塞。 — wait clamp 120s/600s 超时不取消；collect tail；list 过滤；perf 指标；单测覆盖
- [x] 6.11 实现 `blender_run` 的内部提交面：submit/status/wait/collect/cancel/list/perf，唯一 job 入口；本切片不注册模型工具，切片 7 统一暴露为 `blender_run`；验证内部 handler 覆盖 render/headless 两种 kind。 — JobSurface + /job surface=queue；integration 路由用例覆盖 submit/status/list/perf
- [x] 6.12 统一 job 失败 envelope：spawn/queue/render/runtime 失败可归因，进程退出后仍可读；验证四问字段完整。 — failureForRestart/spawn/render/runtime envelope；integration 断言 stage/root_cause/fix/fallback/evidence
- [x] 6.13 新增队列 integration：P4 的 7/7 场景 + 重启恢复 + 取消 + 容量 + 两种 kind 混跑；验证真机 `blender -b` 至少一例成功。 — tests/integration/js/job-queue-api.test.ts + tests/e2e/queue_blender.mjs（真机 headless/render/取消 4 项 PASS）
- [ ] 6.14 相机动画预览：验证 `render` 以 `camera` + `frames` + 低 `resolution`/`samples` 提交时在后台求值关键帧/约束/驱动器并产出帧/MP4，不改变 GUI 当前帧；验证 `blender_see(viewpoint=camera, mode=sequence)` 被拒绝并指向 `blender_run(kind=render)`，不存在 `blender_see` 内切帧的路径。
- [x] 6.15 实现 job owner 与调度可解释性：job 记录 `(hostId,dshSessionId,agentId,parentAgentId)`；collect/cancel 只允许 owner、父 agent、Team Lead；FIFO + priority + quota 的准入决策写入 job status；验证非 owner 不能取消、优先级不饿死 FIFO、配额拒绝有结构化原因。 — job-owner 复用；单测 owner/父/主权限、quota、priority；integration 非 owner 拒绝

## 7. 六工具面

- [ ] 7.1 重写 6 工具面（不是 facade）：按 `blender_session`/`blender_op`/`blender_py`/`blender_see`/`blender_run`/`blender_inspect` 实现 schema、handler 与错误契约；在 catalog 契约测试中断言注册名必须带 `blender_` 前缀且不得暴露无前缀 alias；删除旧 15 工具的定义、注册、catalog 条目与路由，通用实现下沉为内部模块；验证 `src/tools/index.ts` 只注册 6 个，仓库内无旧工具定义残留。
- [ ] 7.2 迁移旧工具参数/响应：尽量保持字段可迁移，但 tool 名不保留别名；`blender_see` 新增 `viewpoint=viewport/camera/custom` 与 `mode=frame/sequence`，旧 `watch` 的 `code` 迁到 `blender_py`、多帧观察迁到 `blender_see(mode=sequence)`；验证旧名调用返回迁移提示和对应新名，旧 `watch` 的 `code` 不再由 `blender_see` 接受。
- [ ] 7.3 行为等价测试：把 15 工具的关键用例分别映射到 6 工具族（括号内是旧工具来源，不是新能力），先红后绿；覆盖 viewport/txn、plan/loop/opt/preset、`do`（→`blender_py`，含执行脚本/短 Python 与无会话拒绝）、`blender_see` 三种 viewpoint（camera 仅 frame、当前帧 evaluated、输出渲染比例）、单帧/序列（watch 的 code 迁 py、多帧迁 mode=sequence；序列支持自定义离屏视角；camera sequence 拒绝）、headless/job/perf、commands（`blender_inspect` 只读与写命令迁移）。
- [ ] 7.4 实现 `blender_inspect` 只读：`blender_inspect` 的 help 只返回它可执行的只读命令及 access 元数据；内部传输命令不列入；只执行 `access=read` 的命令；写命令返回 `wrong_tool` 并指向 `blender_py`/`blender_op`/`blender_run`/`blender_see`/`blender_session`；不存在 `mode=execute`/write receipt/cmd。验证 help 只含只读命令、内部命令不可见、查询可执行、写命令被拒、未知命令返回 did_you_mean。
- [ ] 7.5 实现 `blender_run` 与 `blender_py`/`blender_session` 的边界：`blender_run` 不执行任意 GUI Python；`blender_py` 无会话拒绝；`blender_see` 不服务 headless，`sequence` 不执行 code/不改场景，自定义视角不移动用户视口/相机/选择；camera 只 frame、不切帧/不插关键帧，camera sequence 拒绝并指向 `blender_run` render；验证这些负向用例。
- [ ] 7.6 移除 worker 模型面：不注册 tool、不作为 session kind；内部执行器只在 op/loop/CI 使用；验证行为矩阵和 catalog 无 worker。
- [ ] 7.7 更新契约计数与矩阵：6 tools、7 capabilities、34 families/228 ops（除非显式变更）；HTTP routes 目标 29（不含 `/addon` upgrade；若实现改变，必须在 7.7a 显式声明并同步契约源、`BEHAVIOR_MATRIX` 与 `test:matrix`）；addon 命令计数在 8.18 统一改为 25 registered / 26 wire；协议 12；验证 `test:matrix` 行数与计数一致。
- [ ] 7.7a 冻结 Gateway 路由目标：在 2.1 实现前列出 Gateway HTTP route 表，确认 29 条 HTTP routes 的目标面；`/addon` WebSocket upgrade 单列为 upgrade，不混入 29；若必须新增/删除 HTTP route，先改 design/本任务并显式声明新计数，禁止实现完成后再说。
- [ ] 7.8 更新 catalog/param schema/工具文档：6 个工具的 op/handler 映射、旧名与命令迁移表（含 `blender_inspect` help 的 access/tool）、错误码；验证 `check-doc-paths` 与 catalog 契约测试通过。
- [ ] 7.9 六工具 A/B 只做验证记录，不作为发布门槛：记录 PoC P6 的 BLOCKED 事实；验证没有因命名实验阻塞行为迁移。
- [ ] 7.10 按 design D7.1 的 34 family 目标面更新 catalog 与矩阵：25 个 G+W family 标 G（qc_render/render 最终帧走 R）、7 个 GUI-only 标 G、2 个 host-side（ext/render_guard）标 L、0 个 W；catalog 条目补 `requires_session` 与 `write_scope`；验证 matrix 行数 34 且没有 family 目标为 W。

## 8. Blender Extension

- [ ] 8.1 新增 `addon/dsh_blender_bridge/blender_manifest.toml`：quoted schema_version 1.0.0、id dsh_blender_bridge、version 2.0.0、name/tagline/maintainer、type add-on、blender_version_min 4.2.0、GPL-3.0-or-later、network/files 权限带 reason；验证字段符合 Blender 4.2 manifest 规范。
- [ ] 8.2 删除 `bl_info` 与相关注册路径：`__init__.py` 不再依赖 bl_info；验证 addon 注册/偏好/版本读取不经过 bl_info。
- [ ] 8.3 改造 `scripts/build-addon.mjs`：zip 只含一个顶层 `dsh_blender_bridge/` 文件夹，manifest、`__init__.py` 与模块都在其中，无 wrapper 或根级散文件；确定性时间戳/顺序；验证 zip 可重复构建、sha 稳定且单文件夹布局通过 validate。
- [ ] 8.4 接入 `blender --command extension validate`：CI 下载固定 4.2 LTS、缓存、校验产物；验证 validate 失败会红，通过后记录版本。
- [ ] 8.5 改造 `runtime/launch/resolve.ts`：扫描 `bpy.context.preferences.addons` 与 `sys.modules`，接受 `dsh_blender_bridge` 和 `bl_ext.*.dsh_blender_bridge`；删除固定模块名与 bl_info 读取。
- [ ] 8.6 实现 extension 检测/启用错误：未安装、未启用、版本不匹配分别给 install/enable/upgrade 步骤；验证 virtual-gui 启动前检测，不导入 legacy shim。
- [ ] 8.7 迁移测试：`tests/e2e/blender/package_check.py` 从 legacy 不适用改为 extension 必须通过；`test_addon_manifest.py` 改断言 manifest/namespace；验证旧 bl_info 测试删除。
- [ ] 8.8 处理许可与 attribution：addon GPL-3.0-or-later、插件 BSD-3-Clause、上游 MIT 声明保留；验证 artifact 和文档都含对应声明。
- [ ] 8.9 权限与 online_access：manifest 声明 network/files；asset 网络调用检查 `bpy.app.online_access`；验证关闭网络时本地 bridge 可用、网络能力返回 policy error。
- [ ] 8.10 实现 extension 安装/升级冒烟：从 zip 安装到 Blender 4.2、启用、握手、升级到 2.0.0；验证 namespace 为 bl_ext.*，旧 zip 安装入口不存在。
- [ ] 8.11 更新 release workflow：`publish_extension` 输入默认 false；token 缺失时 skip/fail 明确；首次上架人工提交说明；验证不上传时 workflow 不报告已发布。
- [ ] 8.12 实现官方 API 上传：multipart version_file + release_notes、Authorization bearer secret；验证 dry-run/失败路径不伪造版本，成功记录版本号。
- [ ] 8.13 更新版本：插件 3.1.0、addon 2.0.0、manifest 2.0.0、protocol 12；验证 package.json、manifest、protocol、docs 一致。
- [ ] 8.14 删除 legacy zip 产物/安装文档/测试入口；验证 `npm pack --dry-run` 与 release 资产只有一个 extension 产物，`check:addon` 改为校验 extension。
- [ ] 8.15 删除 addon 遥测 consent 与采集：删除 `commands/telemetry.py`、`ui/preferences.py` 的 `telemetry_consent`/`_on_telemetry_consent_changed`/Telemetry & Privacy 区块、`recorders/edit_capture.py`，以及 `server.py`/`command_registry.py` 的注册与清理路径；删除 `dshbb.open_terms` operator 及注册。验证 addon 无 telemetry 偏好、无 consent-gated handler、无事件缓冲。
- [ ] 8.16 删除 `drain_human_activity` 并收敛 recorder：`commands/activity.py` 只保留 `get_activity_state`；`recorders/user_edit.py` 去掉事件队列/记录/drain，仅保留 agent command depth/suppression；更新 `activity.py`/`command_registry.py` 引用。验证 `get_activity_state` 的 human/agent/idle/both 与 selection 行为不变，且不写盘、不发网络。
- [ ] 8.17 删除社区/条款入口：删除 `config.py` 的 `DISCORD_URL`、`ui/panels/index.py` 的 `_draw_community` 与调用、`ui/preferences.py` 的 Terms 按钮；验证 N 面板无 “Stay updated”/“Join Discord”，Preferences 无 Telemetry & Privacy，凭据帮助链接保留。
- [ ] 8.18 同步命令计数与契约：`REGISTERED_COMMAND_COUNT` 38→25、`WIRE_COMMAND_COUNT` 39→26；删除 9 条写命令、3 条 telemetry 命令与 `get_polyhaven_asset_preview`；更新 `PAUSE_SAFE_EXTRA`、`runtime/catalog/commands.ts`（21 条可由 `blender_inspect` 执行的只读命令 + 4 条 internal；ping 仅 wire）、`runtime/_probe_tools.ts`、`src/tools/inspect/*` 的 help fixture、`BEHAVIOR_MATRIX`、`test_addon_protocol.py`、`test_addon_command_registry.py`、`test_addon_config.py`、`test_addon_manifest.py`、`test_addon_activity_commands.py`、integration dispatch/matrix；新增删除命令 unknown 的负向用例与“addon 无 telemetry/Discord”静态断言。
- [ ] 8.19 更新隐私/迁移文档：`SECURITY.md`/`docs/zh/SECURITY.md`、`docs/宿主集成-接口契约-v2.3增补.md`、`docs/操作教程.md`、README、CHANGELOG、`llms.txt`；说明 3.1 addon 不采集/不缓存/不发送遥测，activity 仅本地内存；旧命令返回升级提示，不提供别名。

## 9. 集成、文档与发布

- [ ] 9.1 全量门禁：`npm run check:finalization`、`check:structure`、`check:exceptions`、`check-plan-ids --enforce`、`test:matrix`、`check:python-static`、`typecheck`、`lint`、`format:check`、`test:unit`、`test:contract`、`test:python`、`check:addon`、`check:build`、`test:parity` 全部 exit 0。
- [ ] 9.2 计数验收：6 tools、7 capabilities、34 families/228 ops（或显式更新）、29 HTTP routes（按 7.7a；不含 `/addon` upgrade；若变更须显式声明）、25 registered / 26 wire commands、protocol 12、addon 2.0.0；验证所有契约源和文档一致。
- [ ] 9.3 真机验收：Windows GA V0（W1/W0；W2 仅在 W1 失败时评估）、user-gui、virtual-gui、queue render/headless、extension 安装/升级；用真实 Blender 4.2，记录日志/截图/耗时。Linux preview 用 5.20d 的 Linux CI 自动化 `blender_see` 链路测试收口，不再要求 Linux 实机 PoC。
- [ ] 9.4 打包与安装冒烟：干净构建后 `npm pack --dry-run` 不含 openspec/、.dsh、lib 旧文件；`npm run smoke:install` 或等价脚本验证 6 tools/extension/doctor。
- [ ] 9.5 更新文档：README、docs/操作教程、docs/配置参考、docs/architecture、AGENTS.md、skills/blender-modeling、MIGRATIONS、CHANGELOG、docs/zh、llms.txt、docs/AI-安装提示词.md、package.json 描述，以及 `src/ui/cards.ts`、`src/tui/commands.ts`、`src/tui/footer.ts`、`src/client/index.js`、`src/tools/viewport/presenter.ts`、`src/tools/worker/execute.ts`、`src/tools/do/execute.ts` 的旧工具名/端口文案；中英同步，链接检查通过。
- [ ] 9.5a 文档清扫门：跑 `grep`/文档路径检查覆盖 `9877`、`15 tools`、`38 commands`、`protocol 11`、`legacy zip`、旧工具名（viewport/worker/plan/do/headless/job/perf/commands/cmd）、`MCP`、`Claude`、`view_size`；每处命中逐条判定（历史迁移/第三方署名可保留，当前事实必须改），并把检查命令写进 PR。
- [ ] 9.6 删除 CLI 相关文档/脚本/测试（若残留）；验证文档和目录不再暗示 CLI 是 3.1 交付物。
- [ ] 9.7 写 PR：用 refactor/feature 模板，列出 breaking 变更、6 工具映射、extension 迁移、V0 证据、测试命令和 AI 披露。
- [ ] 9.8 发布 3.1.0：更新 CHANGELOG、tag、GitHub Release；release notes 明确 Windows virtual-gui 为 GA、Linux virtual-gui 为 opt-in preview（默认关闭、不承诺 GA），并写明安装 Xvfb/VirtualGL 的前置条件；首次 extension 上架人工提交/审核，后续版本走 CD；发布后更新 `llms.txt`/README。
- [ ] 9.9 归档本 change：先处理 1.6 的 active changes（`terminal-state-closeout` 已归档；`vision-motion-analysis` 移出或拆 issue），再 `openspec archive gateway-topology-3-1`，确认 `openspec list` 无 active change；把 V0/真机证据索引写入仓库或 PR 附件。
- [ ] 9.10 发布后跟进：记录 Windows hidden-desktop 的 GL/parity 观察、Linux preview 的自动化 `blender_see` 结果与依赖安装反馈、extension 审核反馈、用户升级问题；Linux preview 升级为 GA 的条件单独开 issue，不在本 change 内继续扩大范围。

## Workflow follow-up

- 本 change 的 planning artifacts 先提交到独立分支供另一台设备继续；实现从 `/openspec-apply-change` 开始，不在 explore 模式写源码。
- 每个切片单独 PR；Gateway 与六工具面是 breaking，回滚必须整体回滚 3.1.0。
- Windows GA V0（W1 通过）前不发布 Windows virtual-gui；Linux preview 不阻塞 3.1 GA，但 5.20d 未通过时默认关闭且不得宣称可用；extension 首次审核未通过不发布 3.1.0 的 addon 更新，但可继续修包/重新提交。