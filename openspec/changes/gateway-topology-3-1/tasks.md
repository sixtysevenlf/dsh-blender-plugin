# Tasks

## 1. 前置与基线

- [ ] 1.1 同步并开分支：`git fetch origin --tags`、`git switch main`、`git pull --ff-only` 到 `8061e7a`，从 main 开 `feat/gateway-topology-3-1`；验证 `git rev-parse --short HEAD` 与 `origin/main` 一致、工作树只有本地未跟踪产物。
- [ ] 1.2 记录基线：在分支上跑 `npm run check:finalization`、`check:structure`、`check:exceptions`、`check-plan-ids --enforce`、`test:matrix`、`check:python-static`、`test:unit`、`test:contract`、`test:python`；验证全部 exit 0，并记录 15 tools / 34 families / 228 ops / 38 commands / 29 routes / protocol 11。
- [ ] 1.3 先红（Gateway）：新增“addon 不监听 9876、旧 addon 被拒”的 contract/integration 测试，在未实现前应失败；把失败输出贴到 PR 草稿。
- [ ] 1.4 冻结 PoC 输入：把 A2 PoC v2.1 的 P1/P2/P4/P5 证据路径、已知 BLOCKED 项写进 PR 草稿；确认 P3 virtual-display 仍为 BLOCKED，不能作为验收依据。
- [ ] 1.5 建立 V0 真机矩阵：列出 Windows 本机、Windows 无管理员、Linux 有 GPU/无 GPU、锁屏/RDP 四个环境；每个环境记录 Blender 4.2 路径、GPU、驱动、Xvfb/VirtualGL 版本。

## 2. Gateway 单端点与 addon 反向连接

- [ ] 2.1 删除 addon 入站 server 路径：`addon/dsh_blender_bridge/server.py` 的 listen/accept 改为 client 连接；先保留协议编解码单测，验证 addon 启动后 `Get-NetTCPConnection -LocalPort 9876` 无监听。
- [ ] 2.2 实现同端口 WebSocket 控制通道：`runtime/transport/addon-ws.ts` 负责 upgrade、握手、帧、ping/pong、关闭；addon 侧 `transport.py` 对应实现；新增帧边界/半包/非法帧单测，验证无新增 npm/pip 依赖。
- [ ] 2.3 定义握手 schema 到 `config/gateway.json`（instanceId/kind/version/protocol/project/capabilities 字段、protocol 12 约束）；TS 与 Python 都从同一 schema 读取或生成，验证 schema 漂移会红。
- [ ] 2.4 实现 instance registry：连接注册、ready 状态、lastSeen、connectionEpoch、project、capabilities；验证两个实例可独立列出，断线后实例离线但保留最后身份。
- [ ] 2.5 实现按 instanceId 路由：单个实例可省略，多实例省略返回 ambiguity 并列出 ID；验证请求只到达目标实例，错 ID 返回结构化错误。
- [ ] 2.6 实现重连替换：相同 instanceId 的新连接在握手后替换旧 epoch；旧请求返回 disconnect，不重放；验证 P1 的 10/10 与重连延迟记录。
- [ ] 2.7 实现协议/版本拒绝：protocol 11、缺少字段、未知 kind 全部拒绝并给升级/修复提示；验证旧 addon 负向用例通过且不会回退 9876。
- [ ] 2.8 收口 Gateway 生命周期：唯一 HTTP 端口，端口占用 fail closed；host/tools 只连 Gateway；删除 runtime 直连 addon 的分支；验证 `netstat` 只有 Gateway 一个 loopback 监听，9876 无监听。
- [ ] 2.9 定义默认进程模型：host + Gateway + Blender 三个进程；host 启动/停止 Gateway 的顺序、日志、退出清理可测；验证 Gateway 崩溃后 host 不在旧端口重试到无限。
- [ ] 2.10 更新 addon 连接错误文案：addon 未启动、Gateway 未启动、版本不匹配分别给出 stage 和 fix；验证三类错误不会互相冒充。
- [ ] 2.11 删除 3.0.x server 相关死代码/测试/文档入口；验证 `check-no-shims`、`check-structure` 不因删除产生未入册例外。
- [ ] 2.12 记录 Gateway 的单端点测试：`curl /health`、WS `/addon` 握手、非法 upgrade、端口占用、进程退出；作为 PR CI 的 integration 层。
- [ ] 2.13 默认端口迁移：Gateway 默认 9877 → 39877；同步 `config/*`、`docs/配置参考`、操作教程、测试 fixture 与升级说明（写明 addon 升级/替换后必须重启 Blender：无热重载，旧 listener 不会自动释放）；验证默认配置只监听 39877，9877 仅显式配置时生效。
- [ ] 2.14 端口占用 fail closed 与归因：bind 失败时解析占用者（Windows `GetExtendedTcpTable` / `netstat -ano`，Linux `/proc/net/tcp` + `/proc/<pid>/cmdline`），返回 `stage=config` envelope（port、pid、process）；验证 3s 内失败且不留第二个 listener。
- [ ] 2.15 禁止 SO_REUSEADDR 静默共享：Gateway 与 addon 都不以 REUSEADDR 与既有 listener 共存，启动前显式检测端口归属；验证同址双绑无法复现（对照 2026-11-06 的 9876 现场）。
- [ ] 2.16 端口冲突回归用例：模拟外部 listener 占用默认端口（含 Blender Lab MCP 扩展真机场景），验证插件 3s 内报 `port_conflict`（含占用者），而不是 `addon timeout`；接入 PR CI。

## 3. 可用性统一契约

- [ ] 3.1 新增 `config/availability.json`：stage 枚举、envelope 必填字段、fallback 类型；新增 schema 校验测试，缺字段/未知 stage 必红。
- [ ] 3.2 在 `src/contracts/availability.ts` 与 runtime 侧实现同一构造器/序列化；所有 tool/route/job 失败改走该构造器；验证旧 generic error 的对照测试变红后修复。
- [ ] 3.3 实现 stage 归因：config/spawn/transport/handshake/addon/bpy/queue/render 各自判定函数，禁止 unknown 折叠；用 3.0.6 A1–A6 的六类故障做参数化测试。
- [ ] 3.4 统一 status/doctor readiness：Gateway liveness、addon control、bpy probe、instance identity 同源；验证 start 成功时 doctor 同一实例 ready，反之不 ready。
- [ ] 3.5 把 `/health` 降为 liveness：不参与 readiness；验证 Gateway 存活但 addon 未连时 status 不报 ready。
- [ ] 3.6 收紧诊断预算：config 立即、spawn 显式检查、transport/handshake/addon/bpy 各自硬超时；验证半开 addon、无 exe、commands 断连都在预算内返回，记录 P50/P95。
- [ ] 3.7 删除嵌套重试链：诊断只做单层有界探测；验证超时返回结构化 timeout，不出现 backend_unreachable 冒充 addon 故障。
- [ ] 3.8 实现安全 fallback：显式不可用/降级路径；禁止可见 GUI、伪造渲染、旧拓扑回退；用“无 provider”“无 addon”“无 exe”三组测试验证 fallback 文案。
- [ ] 3.9 把 3.0.6 A1–A6 变成回归用例并接入 PR CI；验证无 exe、Steam、半开 addon、commands、全门禁、五步时间线全部通过。

## 4. 会话边界与 lease

- [ ] 4.1 新增 `session` facade：list/attach/status/doctor/start/stop/lease/pause/resume；旧 viewport/worker/txn 测试先迁移到 session 调用面，验证旧入口不再模型可见。
- [ ] 4.2 实现 user-gui attach：只附着已连实例；单实例可省略 ID、多实例必须指定；验证两个实例时省略 ID 返回 ambiguity。
- [ ] 4.3 实现 lease/pause：acquire/release/heartbeat/pause/resume；写操作要求 lease；验证第二写者被拒、pause 阻止写但允许读。
- [ ] 4.4 实现 virtual-gui 生命周期：通过 DisplayProvider start/stop，只在 addon handshake 后 ready；验证 start 失败不留下半实例，stop 清理进程与显示资源。
- [ ] 4.5 实现会话实例隔离：scene/Python namespace/lease 不共享；验证一个实例断线不影响另一个，跨实例执行被拒。
- [ ] 4.6 收口 see：只接受 session 目标，走 GUI viewport capture；验证 headless job 不能 see，GUI capture 失败不回落 Workbench。
- [ ] 4.7 把 worker 降为内部执行器：移除模型 tool/session kind 与 Workbench see 的对外路径；op/loop/CI 仍可内部使用；验证 6 工具计数中没有 worker。
- [ ] 4.8 把 txn 迁移到 `session` 子操作：snapshot/restore/mark/revert/list/prune/drop；验证文件级与对象级回滚行为不变，旧 txn tool 不再注册。
- [ ] 4.9 实现 virtual-gui 上限与低资源策略：上限可配，默认值留待 V0；超限返回 capacity 错误；验证不因等待而启动额外进程。
- [ ] 4.10 新增会话 integration 测试：多实例选择、lease 冲突、pause、virtual-gui 停止、断线隔离、see 边界；验证 P2 的 6/6 场景继续成立。
- [ ] 4.11 首调会话门：`session`/`op`/`py`/`see` 在解析不到 ready user-gui 且未显式请求 virtual-gui 时返回 `stage=session` 决策请求；验证不 spawn Blender、不启 provider、P95 ≤3s、错误含两个显式选项。
- [ ] 4.12 显式授权启动 virtual-gui：只有用户确认后才 start，start 前复检仍无 user-gui；验证拒绝后不创建会话，后续调用继续要求决策而不自动降级。
- [ ] 4.13 删除自动降级路径：移除 3.0.x 的 GUI→worker/headless 兜底与嵌套重试；验证无 Blender 时首调不产生任何 Blender 进程，错误含可执行 fix。

## 5. virtual-gui 与 DisplayProvider V0

- [ ] 5.1 定义 DisplayProvider 接口与配置：start/stop/health/capabilities、provider 选择顺序、capabilities 字段；验证 provider 缺实现时启动失败而不是假装可用。
- [ ] 5.2 实现 Windows W1 helper 源码：`tools/win-hidden-desktop/` 使用 CreateDesktopW、CreateJobObject、KILL_ON_JOB_CLOSE、CREATE_SUSPENDED、lpDesktop、ResumeThread；单测/手工验证失败路径不落到默认 desktop。
- [ ] 5.3 接入 Windows helper 构建与打包：Windows CI 用 MSVC 编译；发布包包含 helper；验证包内 helper 版本与源码 commit 匹配，未提交二进制。
- [ ] 5.4 实现 Windows provider：通过 helper 启动 Blender，代理 stdout/stderr/exit；验证 `bpy.app.background == False`、窗口非最小化、默认任务栏/Alt-Tab 不可见。
- [ ] 5.5 跑 W1 V0 验收：GL renderer 非软件、GPUOffScreen 截图成功、2 个并发实例隔离、stop 无孤儿、锁屏/RDP/无管理员场景记录；把原始日志和截图存入证据目录。
- [ ] 5.6 若 W1 失败，评估 Windows IDD fallback：只在失败测量项上启用；验证不静默安装驱动、报告 UAC/签名/许可证前提、用户拒绝时不启动。
- [ ] 5.7 实现 Linux L1 provider：Xvfb + VirtualGL，每个实例独立 display；验证 `bpy.app.background == False`、GL renderer 为真实 GPU、stop 清理 Blender/vglrun/Xvfb。
- [ ] 5.8 跑 Linux V0 验收：与 Windows 同一组测量（GUI、GPU、see parity、2 实例、清理、prerequisites）；验证软件 GL 只标 preview-only 且不通过 gate。
- [ ] 5.9 实现 provider capabilities/readiness 查询：列出平台、prerequisites、安装状态、是否需用户批准；验证未就绪 provider 立即失败并给 fix。
- [ ] 5.10 汇总 V0 报告：每个 provider 的通过/失败测量、默认/fallback 顺序、virtual-gui 默认实例上限、低资源阈值；把报告作为 3.1 发布门禁附件。
- [ ] 5.11 如果 Windows 无 provider 通过：阻止 3.1 发布 virtual-gui，记录原因；验证没有“headless 冒充 virtual-gui”的路径。
- [ ] 5.12 更新 virtual-display 文档：Windows helper/IDD 前提、Linux Xvfb/VirtualGL/GPU 驱动命令与发行版矩阵、一键安装入口与 sudo/polkit 前提、锁屏/RDP 限制、preview-only 语义；验证中英版本同步。
- [ ] 5.13 依赖描述表：把每个 provider 的 prerequisites（包名、检测命令、安装命令、是否需要 root、装后复检）收敛成一张表，capabilities 与 doctor 共用；验证缺依赖时 3s 内返回 `stage=config` 与可执行命令。
- [ ] 5.14 引导式安装：host `doctor --fix` 与 addon 偏好设置按钮共用同一入口；Ubuntu 22.04/24.04、Debian 12 用 `pkexec` 执行 apt 安装 `xvfb`/`virtualgl`，Fedora/Arch/无 polkit 只给命令；GPU 驱动只检测不安装；装后复检 capabilities，失败保留 stderr。
- [ ] 5.15 无依赖环境回归：没有 xvfb/virtualgl 的容器里 virtual-gui 不得 ready、不得启动 Xvfb、不得回落 headless；验证按钮/命令输出与真实包管理器错误一致，测试不需要 root。
- [ ] 5.16 缺依赖代码级硬门：provider start 在 spawn 前检查 prerequisites，缺失直接返回 prerequisite 失败；确认不存在 force/unsafe/skip-check 覆盖路径；验证显式 start 也不产生 Xvfb/VirtualGL/Blender 进程，装好依赖后 capabilities 下一次查询即变为可用。

## 6. 作业队列

- [ ] 6.1 定义 job schema 与持久化：jobId、kind、params、state、priority、slot、pid、timestamps、result、artifacts、error；原子写 JSON；验证损坏文件恢复策略。
- [ ] 6.2 先红：submit 立即返回 jobId、状态 queued；未实现同步/as_job 路径；验证旧 headless 同步 API 的测试被替换而不是保留双路径。
- [ ] 6.3 实现 render kind：声明式参数 + 固定内部入口，不接受 script；验证任意 Python 被拒绝，渲染参数落到 Blender CLI。
- [ ] 6.4 实现 headless kind：fresh `blender -b`、script/script_file、outdir、preload、timeout、HEADLESS {json}；验证不启动 GUI、不进入 session 列表。
- [ ] 6.5 实现状态机与事件：queued/assigned/running/completed/failed/canceled，终态不可变；验证非法转移被拒、每次转移有事件。
- [ ] 6.6 实现容量与 slot：pending 64、GPU 1、CPU 2、QUEUE_FULL；验证 render 占 GPU、headless 默认 CPU、超限立即失败不排队无限等待。
- [ ] 6.7 实现调度：FIFO + priority + quota，virtual-gui 占 GPU 时 render 等待；验证不超卖、不饿死、优先级可解释。
- [ ] 6.8 实现重启恢复：queued 保留、running 标 failed/gateway_restart、终态/产物保留；验证重启后同 jobId 可 status/collect。
- [ ] 6.9 实现 cancel：幂等、终止进程树、清理临时目录；验证 Windows job object 与 Unix process group 都不留 Blender。
- [ ] 6.10 实现 wait/collect/list/perf：wait 有界且超时不取消、collect 返回日志/产物、list 支持过滤、perf 返回队列指标；验证大日志不阻塞。
- [ ] 6.11 接入 `run` tool：submit/status/wait/collect/cancel/list/perf，唯一 job 入口；验证不存在 headless/job/render 工具。
- [ ] 6.12 统一 job 失败 envelope：spawn/queue/render/runtime 失败可归因，进程退出后仍可读；验证四问字段完整。
- [ ] 6.13 新增队列 integration：P4 的 7/7 场景 + 重启恢复 + 取消 + 容量 + 两种 kind 混跑；验证真机 `blender -b` 至少一例成功。

## 7. 六工具面

- [ ] 7.1 新增 6 工具 facade：session/op/py/see/run/inspect；旧 15 工具的实现保留为内部模块，不直接注册；验证 `src/tools/index.ts` 只注册 6 个。
- [ ] 7.2 迁移旧工具参数/响应：尽量保持字段可迁移，但 tool 名不保留别名；验证旧名调用返回迁移提示和对应新名。
- [ ] 7.3 行为等价测试：把 15 工具的关键用例分别映射到 6 工具族（括号内是旧工具来源，不是新能力），先红后绿；覆盖 viewport/txn、plan/loop/opt/preset、do（→py，含执行脚本/短 Python 与无会话拒绝）、see/watch、headless/job/perf、commands/cmd。
- [ ] 7.4 实现 inspect 只读与 execute 写边界：默认只读；`mode=execute` 仅 allowlist 命令可写，返回 write receipt；验证默认模式不能改 scene/addon。
- [ ] 7.5 实现 run 与 py/session 的边界：run 不执行任意 GUI Python；py 无会话拒绝；see 不服务 headless；验证三条负向用例。
- [ ] 7.6 移除 worker 模型面：不注册 tool、不作为 session kind；内部执行器只在 op/loop/CI 使用；验证行为矩阵和 catalog 无 worker。
- [ ] 7.7 更新契约计数与矩阵：6 tools、7 capabilities、34 families/228 ops（除非显式变更）、38 commands（+ping=39）、协议 12；验证 `test:matrix` 行数与计数一致。
- [ ] 7.8 更新 catalog/param schema/工具文档：6 个工具的 op/handler 映射、旧名迁移表、错误码；验证 `check-doc-paths` 与 catalog 契约测试通过。
- [ ] 7.9 六工具 A/B 只做验证记录，不作为发布门槛：记录 PoC P6 的 BLOCKED 事实；验证没有因命名实验阻塞行为迁移。

## 8. Blender Extension

- [ ] 8.1 新增 `addon/dsh_blender_bridge/blender_manifest.toml`：type add-on、blender_version_min 4.2.0、version 2.0.0、GPL-3.0-or-later、network/files 权限；验证字段符合 Blender 4.2 manifest 规范。
- [ ] 8.2 删除 `bl_info` 与相关注册路径：`__init__.py` 不再依赖 bl_info；验证 addon 注册/偏好/版本读取不经过 bl_info。
- [ ] 8.3 改造 `scripts/build-addon.mjs`：extension 根布局、manifest 在 zip 根、无 wrapper、确定性时间戳/顺序；验证 zip 可重复构建且 sha 稳定。
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

## 9. 集成、文档与发布

- [ ] 9.1 全量门禁：`npm run check:finalization`、`check:structure`、`check:exceptions`、`check-plan-ids --enforce`、`test:matrix`、`check:python-static`、`typecheck`、`lint`、`format:check`、`test:unit`、`test:contract`、`test:python`、`check:addon`、`check:build`、`test:parity` 全部 exit 0。
- [ ] 9.2 计数验收：6 tools、7 capabilities、34 families/228 ops（或显式更新）、38 commands（+ping=39）、protocol 12、addon 2.0.0；验证所有契约源和文档一致。
- [ ] 9.3 真机验收：Windows W1/IDD（若用）、Linux L1、user-gui、virtual-gui、queue render/headless、extension 安装/升级；用真实 Blender 4.2，记录日志/截图/耗时。
- [ ] 9.4 打包与安装冒烟：干净构建后 `npm pack --dry-run` 不含 openspec/、.dsh、lib 旧文件；`npm run smoke:install` 或等价脚本验证 6 tools/extension/doctor。
- [ ] 9.5 更新文档：README、docs/操作教程、docs/配置参考、docs/architecture、skills/blender-modeling、MIGRATIONS、CHANGELOG；中英同步，链接检查通过。
- [ ] 9.6 删除 CLI 相关文档/脚本/测试（若残留）；验证文档和目录不再暗示 CLI 是 3.1 交付物。
- [ ] 9.7 写 PR：用 refactor/feature 模板，列出 breaking 变更、6 工具映射、extension 迁移、V0 证据、测试命令和 AI 披露。
- [ ] 9.8 发布 3.1.0：更新 CHANGELOG、tag、GitHub Release；首次 extension 上架人工提交/审核，后续版本走 CD；发布后更新 `llms.txt`/README。
- [ ] 9.9 归档本 change：`openspec archive gateway-topology-3-1`，确认 `openspec list` 无 active change；把 V0/真机证据索引写入仓库或 PR 附件。
- [ ] 9.10 发布后跟进：记录未通过的 provider/限制、extension 审核反馈、用户升级问题；拆成后续 issue，不在本 change 内继续扩大范围。

## Workflow follow-up

- 本 change 的 planning artifacts 先提交到独立分支供另一台设备继续；实现从 `/openspec-apply-change` 开始，不在 explore 模式写源码。
- 每个切片单独 PR；Gateway 与六工具面是 breaking，回滚必须整体回滚 3.1.0。
- V0 未通过前不发布 virtual-gui；extension 首次审核未通过不发布 3.1.0 的 addon 更新，但可继续修包/重新提交。