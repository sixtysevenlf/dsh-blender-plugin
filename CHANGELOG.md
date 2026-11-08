# CHANGELOG — dsh-blender-plugin

## Unreleased

- 行为修复（Web 客户端）：Blender 选中写入草稿成功后不再弹提示，失败提示改用自动消失的 error Toast（约 4 秒）。此前成功后调用 `notify("info", …)` 显示"已接收 Blender 选中（已加入草稿，未发送）"，而 DSH 把 `info` 渲染成输入栏里的常驻 banner（无清除接口，发送消息后仍不消失），于是横幅一旦出现就一直在（后续选中只替换文案，不会清掉）。现成功静默（草稿出现文本即是反馈），桥轮询失败首帧改用 `notify("error", …)`。回归：`tests/integration/js/client-selection-notice.test.ts`；客户端契约文档中该步骤同步改写。
- 行为修复：`qc_compare_auto` / `qc_align_iou` 现在能真正分派。此前 catalog 声明了这两个 op，但 Python 侧 `qc_ops()` 没有对应键，调用返回 `unknown qc op`；现补 `compare_auto` 分派与 `qc_align_iou` 的路径级封装（掩膜 bbox 紧裁后算 IoU，只返回 JSON 标量）。`config/contracts.json` 的 qc `catalogExtras` 随之删除，parity 测试与 native E2E 覆盖分派成功。`qc_help` 补 `compare_auto` / `align_iou` 的签名说明，`qc_align_iou` 缺路径参数时返回明确错误，不再在 `qc_load(None)` 上失败。
- 行为修复（addon 快照）：`get_world_state_snapshot` 的动画段在 Blender 4.4+ 的分层（layered / slotted）action 上会静默丢数据 —— `_snapshot_animation` 直接遍历 `action.fcurves`，而 5.2 已删除该属性，外层 `except Exception` 把 `AttributeError` 吞成 `{}`，`channels` / `keyframe_count` / `frame_range` 全部消失且不报错。现改走 channelbag 入口（优先 `bpy_extras.anim_utils.action_get_channelbag_for_slot`，缺失或抛错时回退 `layers → strips → channelbag(slot)` 手工遍历），按 `obj.animation_data.action_slot` 取该对象自己的曲线；去重键用 `fcurve.as_pointer()` 而不是 `(data_path, array_index)`（多对象共用一个 action 的多 slot 场景按路径去重会把两份动画并成一份）。Blender 4.3 及更早的 `Action.fcurves` 路径不变。回归：`tests/unit/python/test_addon_world_snapshot.py`（10 例，修复前全部失败）；文档侧（SKILL.md 的 A.6.5.6 / A.6.5.6a）已由 #84 合入。
- 上述两条修复本身不改变 API / 协议 / 工具计数；3.1 的协议与计数变化见下方「3.1 addon 命令面收缩与零遥测（breaking）」等小节。

### 治理

- 例外有主化：addon 8 个命令模块对 `integrations/*_client.py` 私有 helper 的 41 处跨包导入登记为 `python-private-import`（`config/exceptions.json`，owner `addon-integrations`，reviewBy 2027-06-30），并由纯 ast 契约测试逐对校验；`config/doc-links-baseline.json` 的 7 条冻结失效引用补 `review {owner, reviewBy, reason}`，`check:doc-paths -- --enforce` 拒绝无主/过期的 baseline。
- 注释去叙事：47 个重构期文件的注释（45 个文件头 + 2 个非头部）去掉“从 X 拆出 / 行为冻结”叙事、改为职责描述；`addon/dsh_blender_bridge/protocol.py` 删除指向不存在的 `addon_manager` / `trajectory` 的同步注释；注释约定写入 `docs/architecture.md`。
- 流程收尾：`converge-3-0-terminal-state` 归档到 `openspec/changes/archive/`；清理 19 个干净的历史 worktree（保留 3 个有未提交改动的），`.dsh/`、`.dsh-ops/`、`.review-tmp/` 加入 `.gitignore`；`.review-tmp/` 同时加入 plan-ID 扫描的跳过目录，三项一并加入 ESLint 忽略配置。

## v3.0.6（2026-11-05）—— 首调失败修复与重构终态收口（治理 + 开发环境）

- Windows 自动探测补 Steam 库：扫描 `%ProgramFiles%\Steam`、`%ProgramFiles(x86)%\Steam`、`%ProgramW6432%\Steam` 与 `{C,D,E,F}\{Program Files\Steam, Program Files (x86)\Steam, Steam, SteamLibrary}`，并解析 `steamapps/libraryfolders.vdf` 中的其它库；命中来源记 `auto:steam`。这是「Blender 装在 Steam 库里、首调直接失败」的根因。
- `blender_rt_worker(op="start")` / `blender_rt_job(op="start")` 在 spawn 前预检 blender.exe：为空时立即返回结构化 `blender_exe_missing`（含 `DSH_BLENDER_EXE` / `dsh-blender.config.json` / Steam 路径示例），不再抛 Node 的 `TypeError: The "file" argument must be of type string`。
- `blender_viewport(op="start")` 不再把「9877 后端进程在」当成「Blender/addon 通道可用」：/health 通后再做有界 /status，区分「后端在 + addon/bpy 可用」与「后端在、addon/bpy 未响应」。
- `op="doctor"` 与 `GET /status` 说真话：9876 TCP 通但 addon 在预算内没回 bpy 往返时，返回 `kind: "addon-unresponsive"`（不再落成 `blender-unreachable`）；addon 明确回了错误（非超时，如「AI 操作已暂停」）时归因 `kind: "addon-error"`，不再写成 `main-thread-busy`；/status 透出 `addonError`，宿主超时报 `backend_timeout` 而不是 `backend_unreachable`。
- 预算分层并保持倒挂关系：删除启动阶段未 await、占用串行 addon 队列头的 `get_addon_info` 探测后，/status 回调到 5s、doctor 回调到 4s（实测冷/热调用相差 <2s，此前只能取 3.5s/3s）；/commands 的 FLAGS 15s + 5 个集成各 3s（每次失败还会串一次 ~2.5s 诊断，最坏 ≈42.5s < 客户端 45s < 宿主静态截止 60s）；`blender_rt_commands` 客户端预算改为 45s，先于宿主 deadline 收口；`op="start"` 的 /status 探测预算提到 12s（覆盖后端刚起首调），不再让宿主先 abort、模型看不到 addonError。
- `blender_rt_commands` 在 socket 级失败时返回 `addon_unreachable`，next_action 指向 N 面板 Connect + 有界 `op=doctor`，不再指向会阻塞的 `op=status`。
- addon 面板文案改为「Bridge listening / Bridge server stopped」等，不再暗示它连接了 MCP server。
- `blender_viewport(op="status")` 的失败分类修正：连接级失败（ETIMEDOUT / connect timeout / ECONNREFUSED 等）统一报 `backend_unreachable`，不再误报 `backend_timeout`；Blender 正在渲染时单列 `busy_render` 并指向 `render_wait`，不再归成 addon 坏。`errorText` 的 cause 链加深度/环保护。
- `/doctor` 在 addon 明确回错时优先展示 `addon.kind/summary/fix`（不再读历史 `metrics.lastDiagnosis`）；`BUSY_RENDER` 的修法指向等待渲染。
- `/worker` 只透出 `blender_exe_missing` 这一种约定 code；Node 内部错误码（`ERR_INVALID_ARG_TYPE` 等）不再变成模型可见的结构化错误码。
- `blender_rt_headless` 现在真的把运行时的 `blender_exe_missing` 透出（含配置指引），与文档一致；此前一律吞成 `backend_error`。
- Windows Steam 探测补注册表 `SteamPath`（HKCU / HKLM WOW6432Node）与 G–H 盘常见根。
- 回归测试加固：`doctor-bounded` 先预热再计时（预算 12s，稳态 6–9s）、补 macOS/Linux 探测用例与 HKCU SteamPath 用例、worker 源码顺序断言改成正则。
- **行为变更（唯一一处）**：`blender_rt_plan` 在无头 worker 会话（`session=...`）下调用 `qc_render_*` 现在路由到 `dsh_qc_render_api` 并预载 `qc_render`；此前 worker 路径命中 `dsh_qc_api` 的 `render_views` 转接函数，而转接依赖 GUI 侧才有的 `K.dsh_qc_render_api`，worker 只 preload `qc,imgtools` 时必然失败（GUI 路径行为不变）。
- 无 API / 协议 / 工具计数变化（15 tools / 34 family / 228 op / addon 1.11 / protocol 11）。

### 治理：重构终态收口

- 路由与契约单一真源：新增 `config/routing.json` 与 `scripts/gen-routes.mjs`，生成 `src/plan/routes.generated.ts` 与 `runtime/core/dispatcher/routes.generated.ts`，`npm run check:routes` 校验生成物与源数据一致；`config/contracts.json` 把 catalog 的 228 op 与 Python dispatch 字面表对齐，新增 AST parity 测试（`qc_compare_auto` / `qc_align_iou` 是已知 catalog-only 漂移，已登记待独立修复）。
- 例外与预算登记：新增 `config/exceptions.json`（路径级例外，含 reviewBy 过期检查）与 `config/function-budget-allowlist.json`（106 个 80–120 行函数按 8 类登记，reviewBy 2027-06-30）；`check:exceptions` 接入 `npm run lint`；>120 行函数保持零例外。
- addon 命令真源：handshake capabilities 由 `command_registry.COMMAND_NAMES` 派生（不再硬编码 16 个）；host catalog 与 addon registry 双向 parity；口径统一为 38 registered + ping = 39 wire。
- public facade 收口：`runtime/engine.ts` 88 → 22 个导出（删除 38 个 py `*_PATH` 与其余 facade-dead），`config/facades.json` 登记两个公共 facade 及其消费者，`check:no-shims` 拒绝未登记导出；删除 `src/host/backend/port-holders.ts`、`engine-api/frame.ts` 纯转发、`qc_impl/api.py` 的 `exec(compile(...))` 二次加载，`runtime/recover.ts` 并入 `execution/ledger/recover.ts`；addon 凭据改为单一所有者（add-on preferences + session override + `DSHBB_*`/`BLENDERMCP_*` env 后备），删除 Scene 镜像属性；删除 `detectBlenderExe` 再导出。
- 死代码与打包：删除 5 个未使用导出、重复的 `ReceiptContext`、addon `import_model_files` 死链、未使用的 compat 函数、永不存在的 `runtime/ext/pkg.tgz` 打包条目，并清理工作区 400+ 个陈旧 `.pyc`。
- 文档终态：3 篇被取代文档归档到 `docs/archive/`；SKILL 标题重编号；曲面算法收敛为单一来源；addon 用户文案 MCP/Claude → Bridge（保留上游署名与 `BLENDERMCP_*` 兼容）；新增 `check:plan-ids` 与 `check:doc-paths` 的 warn→enforce 门禁，清理重构计划编号并修复文档链接/入链；`BEHAVIOR_MATRIX` 行号改为文件 + 符号并由门禁校验；SECURITY 的凭据说明改为准确描述。

### 3.1 addon 命令面收缩与零遥测（breaking）

- addon 命令面从 38 registered / 39 wire 收缩到 25 registered / 26 wire（18 常驻 + 7 集成门控；21 条只读 + 4 条内部传输；wire 另含 ping）。删除 9 条写命令（`export_scene`、`set_texture`、资产下载/生成导入）、3 条采集/同意命令（`get_telemetry_consent`、`set_telemetry_consent`、`drain_human_activity`）与 1 条未进 catalog 的预览命令（`get_polyhaven_asset_preview`）；旧名不再注册，调用返回 unknown command 或 `command_removed` 升级提示，不提供别名。
- addon 零遥测：删除 telemetry consent 偏好与 Telemetry & Privacy 区块、编辑事件采集/缓冲与 `drain_human_activity`，删除 Discord / “Stay updated” / Terms 推广入口；`get_activity_state` 只保留本地内存判定，不落盘、不外发。
- 契约同步：`command_registry.py` 的 counts、handshake capabilities、catalog（`runtime/catalog/commands.ts` 增 `access: read|internal`）、PAUSE_SAFE 列表、BEHAVIOR_MATRIX（333 rows）、JS/Python 契约测试与安装文档全部改为 25/26 口径；addon extension zip 74 entries（sha256 `db59b7fc…`）。

### 3.1 virtual-gui 能力门

- 新增 `features.virtualGui` 能力门（env `DSH_BLENDER_VIRTUAL_GUI`）：Windows 在 V0 验收前默认 `off`、通过后默认 `auto`；Linux/macOS 默认 `off`，Linux 仅显式 `on` 进入 preview；非法值回退 `off` 并标记 `invalid`。`/status`、`doctor` 与 `blender_session start` 共用 capabilities（enabled/preview/ready/prerequisites/missing/reason/fix）。
- 新增 DisplayProvider 契约与选择顺序（Windows hidden-desktop → IDD fallback；Linux Xvfb+VirtualGL preview；macOS 未实现）。Windows 构建已带 W1 provider：helper 解析顺序为 env `DSH_HIDDEN_DESKTOP_HELPER` → `vendor/win-hidden-desktop` → `tools/win-hidden-desktop/out`；`blender_session start` 通过 helper 在私有 desktop 启动 Blender，代理 stdout/stderr/exit 并保留 tail，失败返回结构化 `helper_missing` / `blender_exe_missing` / `helper_exited` / `provider_timeout` 等错误，绝不回落默认 desktop；stop 由 job object 连带清理 Blender 且重复 stop 幂等。selection 只在 capabilities ready（feature 开启 + 依赖就绪）时返回 provider，否则仍 fail closed；`blender_session start` 在 feature 关闭、依赖缺失或 provider 未实现时只返回 `stage=config`，不 spawn Blender/Xvfb，也不回落 headless。
- 依赖表收敛到 `runtime/display/prerequisites.ts`（包名/检测/安装/是否 root/复检），VirtualGL 明确 Ubuntu 24.04 无 apt 包、须官方仓库安装。
- 发布包新增 `vendor/win-hidden-desktop` helper（Windows CI 用 MSVC 构建；`npm run check:win-helper` 校验 version/commit/sourceSha256/sha256/protocol，`--packed` 断言 npm tarball 含 exe+build.json；仓库不提交二进制）。
- 回归：`tests/unit/js/display-capabilities.test.ts` 19 项（feature 默认与非法值、三条负向启动、provider 顺序与 Windows 选择条件、status 暴露、consent 与已有 user-gui 拒绝）；`tests/unit/js/win-hidden-desktop.test.ts` 11 项（ready/error/早退/超时、缺 helper/缺 Blender 不 spawn、stop 幂等、输出继续代理、helper 解析顺序）；真机 `tests/e2e/win_hidden_desktop_provider.mjs` 12/12（GUI 非最小化、私有 desktop、双实例隔离、stop 无孤儿；`local-workspace/5.4-windows-provider.md`）。

### 3.1 virtual-gui 会话生命周期与上限

- `blender_session start` 成功后的会话 id 为 `virtual-gui:<instanceId>`；只有 Gateway 拉起的那个 Blender 以同一 `instanceId` 完成 addon 握手才标记 `ready`（provider 注入 `DSHBB_INSTANCE_ID` / `DSHBB_SESSION_KIND=virtual-gui`，addon 握手按该环境报告 kind），握手超时 30 秒。provider 启动失败或握手超时会先停 provider，不留 Blender 进程或半注册会话；`stop` 只接受会话所有者，进程/会话一起清理，重复 `stop` 幂等。
- 新增 `features.virtualGuiMax`（env `DSH_BLENDER_VIRTUAL_GUI_MAX`）实例上限，默认 4、范围 1–16；总内存 <16 GiB 或空闲 <4 GiB 时自动降为 2。超限时 `start` 在 spawn 前返回 `stage=session` / `capacity`（evidence `used`/`limit`/`configured`/`low_resource`/`reason`），不排队、不 spawn。
- 回归：`tests/unit/js/virtual-gui-lifecycle.test.ts` 7 项（握手前不可见、握手失败清理、provider 错误结构化透传、上限、stop 幂等、attach 归属、limits clamp/低内存）；真机 `tests/e2e/virtual_gui_session.mjs` 20/20（consent 拒绝、真实 helper 启动 GUI Blender、同 instanceId 握手、display/pid、stop 无孤儿、重复 stop、缺 helper fail closed）。
- Windows virtual-gui V0 通过 GA 验收（5.5），默认值由 `off` 翻为 `auto`：`tests/e2e/windows_ga_v0.mjs` 走默认 feature 的产品路径启动 2 个并发实例，32/32 通过（NVIDIA 硬件 GL、GPUOffScreen 800x447 截图、红/蓝材质与私有 desktop 互不串、stop 无 Blender/helper 残留、二次 stop 幂等、非管理员会话）；锁屏/RDP 作为未执行场景记录。验收同时修复多实例路由缺陷：engine 的 `addon.send` 指标包装丢弃第 4 个 `opts` 参数，导致第二个 addon 连接后带 `instanceId` 的调用报 ambiguous，现转发 opts 并加单测。

### 3.1 Linux preview（Xvfb + VirtualGL）与 Blender 平台路径

- 新增 Linux DisplayProvider `linux-xvfb-virtualgl`（preview）：每个实例启动独立 Xvfb `:N`，强制 X11（清除 `WAYLAND_DISPLAY`、`XDG_SESSION_TYPE=x11`、`GDK_BACKEND=x11`）；只有 `vglrun` 存在且 `/dev/dri/renderD*` 可读时才走 `vglrun -d egl` 硬件 GL，否则以 llvmpipe 软件路径启动并标 `preview-only`（capabilities 新增 `hardware_gl`）。feature 未显式 `on` 时 fail closed；Linux 必需的 `xvfb`/`blender` 与可选 `virtualgl`/`gpu-device` 在 prerequisites/missing/fix 里分开展示，可选缺失不阻塞 preview。stop 先 TERM 进程组再 KILL，清理 Blender/vglrun/Xvfb 与 socket/lock，重复 stop 幂等。
- 新增 `tests/e2e/linux_preview_see.mjs` 与 PR CI `linux-preview-see` job（ubuntu-latest + Xvfb + Blender 4.2.10）：真实 Gateway + addon + virtual-gui 下调用 `blender_see`，断言 GUI 非 background、PNG 可解码且非全黑/非单色、stop 无孤儿、stop 后不回落 headless。WSL Ubuntu 24.04 + llvmpipe 软件链路 **22/22 PASS**（`local-workspace/5.8-linux-preview-see.json`）；llvmpipe 只证明调用链，不作硬件 GL 证据。
- 修正 WSL 里 Linux Blender 的路径形态：Blender 侧 workDir/PNG 路径不再无条件映射成 UNC，而由 `blenderUsesWindowsPaths(platform,isWsl,blenderExe)` 判断——只有 Blender 可执行文件本身是 Windows 程序（`.exe`/盘符）时才做 WSL 映射；WSL 里的 Linux Blender 与原生 Linux/macOS 一样使用同机 POSIX 路径。此前 `blender_see` 在 WSL Linux Blender 上把 PNG 写到字面量 `\\wsl.localhost\...` 路径，随后读原路径必然 `ENOENT`（view http 502）；Windows Blender 行为不变。回归：`tests/unit/js/platform-paths.test.ts` 23 项（含 WSL+Windows/Linux Blender 决策表）。

### 3.1 virtual-gui 前提引导式安装

- `/doctor`（与 `/status`）的 `virtualGui` 新增 `installPlans`：由 `runtime/display/install.ts` 按发行版给出 exact 安装计划（Ubuntu 22.04/24.04、Debian 12 有 `pkexec` 时一键 `pkexec apt-get install -y xvfb`，无 polkit 降级为 `sudo` 命令；Fedora/Arch 只给命令；VirtualGL 永远只给官方仓库步骤，绝不给裸 `apt install virtualgl`；GPU 驱动只检测并给 `usermod -aG video,render`；Windows helper 随包、Blender 只给配置说明）。
- 两个入口共用同一份计划：TUI `/blender doctor --fix` 执行支持的 one-click 后重新拉取 `/doctor` 复检，未 ready 明确返回 error；addon Preferences 的 “Install missing prerequisites” 按钮通过 `ui/install.py` 执行 Gateway 展示的 argv，失败保留 stderr 并停止，装后同样复检 `ready`。不装依赖时 `blender_session start` 仍返回 `stage=config`，不启动、不回落 headless。
- 安全边界变化：addon 唯一新增的进程执行点是审计过的 `addon/dsh_blender_bridge/ui/install.py`，只执行 list argv、无 shell；契约测试 `tests/unit/js/contract/security.test.ts` 改为按文件断言其余模块不含 `subprocess`，并保留 `os.system` / `shell=True` 禁用。回归：`tests/unit/js/install-plans.test.ts` 11 项、`tests/integration/js/tui-commands.test.ts` 的 `doctor --fix` 用例、`tests/unit/python/test_addon_install_ui.py` 9 项。

### 3.1 virtual-gui 显式授权

- `blender_session start kind=virtual-gui` 首次返回 `consent_required`（stage=session，evidence.options 给出 attach/start 两个显式选项）；只有 DSH host 在用户确认后通过内部 `session action=consent` 发放一次性 30s `consent_token`，模型 tool schema 不能自行设置。start 每次重新复检：已有 ready user-gui 时返回 `session_already_available` 且不创建会话；token 缺失/无效/过期/重放都继续要求决策，不自动降级到 headless 或 GUI。回归：`tests/unit/js/display-capabilities.test.ts`、`tests/integration/js/session-api.test.ts`。

### 3.1 human pause 与人改冲突

- addon 现在在非 agent 窗口内记录人类改过的 Object/Collection（内存 revision，不落盘、不上传），通过 event 通道发 `human_edit`；Gateway 将其映射为 session 的 humanModified 记录。
- 写门在 `human_modified` 时拒绝并让 agent 重读：object/collection/prefix/exclusive 按声明 scope 匹配；`blender_session mark_read` 只清除已重读的 scope，再次人改会重新阻塞。human 始终可编辑，pause 只阻止 agent 写，不自动合并、不做 per-actor undo。

### 3.1 多 agent presence 与视口叠加

- Gateway 保存每个 caller 的 display name、颜色、view pose、选中 session_uid、activity；视图/选中更新时通过既有内部 `execute_code` 通道把快照推给 addon，不新增工具或路由。
- addon 新增 `presence_draw.py`：用 `SpaceView3D` 的 POST_VIEW/POST_PIXEL 绘制视锥、标签和选中包围盒；只画视口，不写场景、不改选择、不动相机/帧/用户视角；断开或卸载时静默清除 handler。
- 回归：`tests/unit/js/presence-push.test.ts` 3 项、`tests/unit/python/test_addon_presence_draw.py` 5 项、`tests/integration/js/session-api.test.ts` 的 update_view/update_selection 推送，以及 `tests/integration/js/session-multi-agent.test.ts` 的多 agent 并发/隔离；真机 GUI E2E `tests/e2e/addon_presence_draw.mjs` 15 项（两个 agent 独立颜色/机位、view matrix 与对象/选择不变、两个 agent 并发截帧、清除后 handler 归零）。

### 开发环境

- 新增 `npm run setup:dev`：自动发现 Python（`PYTHON` → `python` → `python3` → Windows `py -3`，含坏 Store alias 的兜底）、创建/复用 `.venv`、安装 `requirements-dev.txt` + `requirements-typecheck.txt`、仅在缺失时执行 `npm ci --legacy-peer-deps`，支持 `--check` / `--force`，幂等可重复执行；`.github/workflows/pr.yml` 与 `release.yml` 复用同一脚本，不再各自内联 venv 步骤。
- `CONTRIBUTING` / `README`（中英）与 `scripts/README` 收敛到同一条开发环境命令。

### 3.1 Gateway 拓扑（breaking）

- 宿主负责 Gateway 生命周期，模型面不再有 launch/restart/lease 工具；Gateway 默认监听 `127.0.0.1:39877`（原 9877），端口被占时 fail closed（`port_conflict`）。
- addon 改为**主动反向连接** Gateway（WebSocket `/addon` upgrade），不再监听 9876；断线可重连，多实例以 `instanceId` 区分；旧协议 addon 被明确拒绝，不做双栈兼容。
- 插件版本 3.1.0 / addon 2.0.0 / protocol 12；addon 只以 Blender **Extension** 安装（`Edit ▸ Preferences ▸ Extensions ▸ Install from Disk...`），旧 zip 的 Add-ons 安装方式删除；升级后需重启 Blender。
- 迁移与排障见 `MIGRATIONS.md` §0.1/§0.2；回归：`tests/e2e/addon_reverse_connect.mjs`（真实 Blender+Gateway 9/9，断言 addon 无 LISTENING、心跳/doctor 协议 12）、`tests/integration/js/host-gateway-lifecycle.test.ts`、`tests/unit/js/contract/ports-gate.test.ts`。

### 3.1 有界作业队列与唯一作业入口

- `blender_run` 是唯一作业面：`action=submit` 立即返回唯一 `jobId`，没有同步 headless / `as_job` / `wait_s` 双路径；`kind=render` 是声明式参数（支持相机 + `frames` 动画），`kind=headless` 跑脚本并回 `HEADLESS {json}`。
- 状态机 `queued → assigned → running → completed|failed|canceled`，终态不可变、每次转移有事件；容量 pending 64 / GPU 1 / CPU 2 / 每个 virtual-gui 预留 1 GPU slot，超出返回 `QUEUE_FULL`；FIFO + priority + owner quota 的排队决策在回执里可解释。
- 重启恢复：queued 保留，running/assigned 标 `failed`（`gateway_restart`）且不重放；`action=cancel` 幂等并清理进程树；`action=wait` 默认 120s、上限 600s，超时只回 `waitTimedOut` 不取消；`collect` 返回日志与产物；collect/cancel 只允许 owner、父 agent 或 Team Lead。
- 旧 Cycles 性能分析保留为 `action=tune`；回归：`tests/unit/js/job-queue.test.ts`（23 项）、`tests/e2e/queue_blender.mjs`（真实 Blender 5 项，`local-workspace/6.14-queue-e2e.log`）。

### 3.1 GUI 视点采集与 Python 写占用

- `blender_see` 只服务 GUI：`viewpoint=viewport|camera|custom`，`mode=frame`（默认）或 `sequence`；viewport/custom 使用固定档位（1:1 544/768/1024/1280、4:3 640/960/1280、16:9 1024/1152/1280，默认 16:9+1024），非法值报错不夹取；`camera` 只允许 frame，读取当前帧 evaluated 相机与渲染比例，**不修改** `scene.camera`/当前帧/视口/选择；PANO 返回 `unsupported` 并指向 `blender_run(kind="render")`，DOF/运动模糊标 `preview_only`。
- 新增 `current` 离屏模式：按用户当前 viewport 朝向重算目标宽高投影，不移动用户视口；真机回归 `tests/e2e/see_modes_gui.mjs` 18/18（current/camera/命名相机/PANO/序列拒绝/前后不变）。
- `blender_py` 固定 exclusive 写占用：并发调用返回 `py_busy`（holder/started_at/retry_after），可选 `wait_ms` ≤5s；活动 grant 不按 30s TTL 过期；human pause 只拒绝新写不打断运行；事务 `blender_session(action="txn")` 的 restore/mark/revert 同样受 exclusive grant 与 human pause 约束。回归：`tests/integration/js/session-api.test.ts`。

### 3.1 六工具面迁移（breaking）

- 模型面从 15 个工具收口为 **6 个**：`blender_session`（会话/归属/权限/事务）、`blender_op`（catalog 与 loop/opt/preset）、`blender_py`（GUI 会话内 Python）、`blender_see`（GUI 视点截图与序列帧）、`blender_run`（唯一作业入口：render / headless）、`blender_inspect`（只读 addon 命令目录与执行）。
- 旧名不再注册，调用会得到迁移提示；完整对照表见 `MIGRATIONS.md` §0.3：`blender_rt_see`→`blender_see`、`blender_rt_watch`→`blender_see(mode="sequence")`、`blender_rt_do`→`blender_py`、`blender_rt_plan`→`blender_op`、`blender_rt_loop/opt/preset`→`blender_op(action=…)`、`blender_rt_perf`→`blender_run(action="tune")`、`blender_rt_headless`/`blender_rt_job`→`blender_run(action="submit"/"status"/"wait"/"collect"/"cancel"/"list")`、`blender_rt_cmd`/`blender_rt_commands`→`blender_inspect`、`blender_rt_txn`→`blender_session(action="txn")`、`blender_viewport`→`blender_session`。
- `blender_run` 是**唯一**作业提交面：submit 立即返回 job id，不再有同步 headless / `as_job` / `wait_s`；render 作业是声明式参数，不执行脚本。
- `blender_rt_worker` 不再是对模型暴露的工具：GUI 内的「改一步看一眼」用 `blender_py` + `blender_see`；没有 GUI 时用 `blender_run(kind="headless")` 后台作业。`session="auto"`、`preload="…worker…"` 与 worker see/watch 不再出现在工具面。
- 计数不变：34 family / 228 op / 25 addon commands（+ping = 26 wire）/ 29 routes / protocol 12；工具数 15 → 6 是本版唯一计数变化。

## v3.0.5（2026-11-04）—— 修复源码注入 __file__ 缺失

- 修复 #73：源码注入的 Python 模块现在会收到真实模块路径作为 `__file__`，`audit` / `qc` / `contract` 等高层算子不再报 `NameError`。
- `preloadChunk` 与 `injectModule` 统一注入 `__file__`；`qc_render` 的 asJob 路径改用 `preloadChunk`。
- 清理 `contract_impl/common.py` 中重构期留下的 `__file__` fallback；保留 `kit.py` 的活跃 fallback。
- 无 API / 协议 / 工具计数变化（15 tools / 34 family / 228 op / addon 1.11 / protocol 11）。

## v3.0.4（2026-11-02）—— 修复 git 源安装

- 修复 #71：`prepack` 只构建 `lib/`，不再在安装路径执行 `check-build`；`check:git-hygiene` 独立成脚本，只由 CI 与本地 clone 执行。
- `scripts/check-build.mjs` 删除 `git ls-files lib` 步骤，因此在没有 `.git` 的解包目录里也能通过。
- 新增 `npm run check:git-hygiene`：缺少 `.git` 或检出根目录不匹配时 fail-closed；PR gate 与 release verify 都会执行。
- 无 API / 协议 / 工具计数变化（15 tools / 34 family / 228 op / addon 1.11 / protocol 11）。

## v3.0.3（2026-11-02）—— Node.js 24.9 基线

- 最低 Node.js 版本提升到 24.9，与 DSH 当前要求一致；CI/CD 使用 Node 24。
- 开发工具链对齐 DSH：TypeScript 6.0.3、jsdom 29.1.1；`@types/node` 对齐 24.x。
- npm 发布改用 trusted publishing（OIDC），不再依赖 `NPM_TOKEN`。
- 3.0.2 的首次 OIDC 发布被 npm registry 暂存为不可见版本，未公开发布；本版改用 3.0.3。

## v3.0.1（2026-11-02）—— Python 静态检查与 IDE 收尾

> 不新增建模能力；清理 Ruff allowlist、pyright 噪音与 native E2E 的全局注入，重建 addon zip。

- 删除 config/python-static-allowlist.json；`npm run check:python-static` 直接跑 Ruff，不再维护历史例外。
- native E2E 的 upstream 模块改用显式 import + `support.bind(ctx)`，不再通过 globals() 注入 PREFIX/api/chk/K。
- 根 pyrightconfig 仅对 legacy native E2E 与 runtime/py 关闭诊断；typed-core 与 pure-logic scope 仍严格。
- 修复 addon 的 2 个 pyright error，逐项 triage 29 个 warning；不新增 baseline。
- 增加 NumPy 类型检查依赖（仅开发/IDE；Blender 运行时自带 NumPy）。
- 重建 addon/dsh_blender_bridge.zip；安装命令为 `dsh plugin --profile <profile> add dsh-blender-plugin@3.0.1`。

## v3.0.0（2026-11-01）—— 工程健康化重构（breaking：addon 改 zip 包）

> 模型工具 15 个；plan catalog 34 family / 228 op；addon command 38 个、protocol 11；
> 本版不新增建模能力，只做结构、测试、类型、CI 与发布工程；B1 分数不与 v2.6.1 混算。

### Breaking changes

- **npm 包名改为 `dsh-blender-plugin`**：此前仓库内使用的 `@dsh-external/dsh-blender-plugin` 未被发布到 npm；v3.0.0 起 npm 包名统一为 `dsh-blender-plugin`，安装命令为 `dsh plugin --profile <profile> add dsh-blender-plugin@3.0.0`。旧 CHANGELOG 条目中的 `@dsh-external/...` 是历史名称。
- **addon 安装方式改为 zip 包**：发布物是 addon/dsh_blender_bridge.zip（包内 __init__.py、bl_info v1.11、protocol 11）；旧的单文件 addon/dsh_blender_bridge.py 不再随包发布，也不再是安装入口。升级时必须先在 Blender Preferences 里停用并移除旧单文件，再安装 zip、启用、N 面板 Connect。
- **Premium / Tripo 生成通道移除**：删除 Premium 许可、额度、Supabase 客户端与 Tripo 开关；Hyper3D 与 Hunyuan3D 只保留自有 API key 通道。Tripo 无 BYOK 通道，因此整条命令与面板入口一并删除（addon command 39 → 38）。
- **lib/ 不再入库**：源码运行时由 runtime/**/*.ts strict 编译到 lib/runtime/**/*.js；从 git 使用先 npm ci --legacy-peer-deps && npm run build，prepack 会自动构建。npm 安装包仍自带 lib/。
- **runtime 不再有 .mjs**：旧 runtime/*.mjs 的 require 用法不再成立；请用包入口或构建后的 lib/runtime/*.js。
- **扁平 Python 模块移除**：删除 runtime/*.py 的 38 个 re-export shim，generator.py / uv_tools.py 移入 runtime/py/geometry/；模块只按 runtime/py/modules.json 解析，旧式 import runtime.<name>（或直接按 runtime/<name>.py 加载）不再可用。preload / module 的公开模块名不变。

### 结构、类型与测试

- src/index.ts 13 行 entry-only；15 个 tool handler 全部拆到 src/tools/*；check:structure 校验 73/73 目标文件、83 文件 / 330 import 边 / 0 环。
- 生产源码行数预算：284/284 文件 ≤ 600 行（runtime/catalog.ts 数据表例外，architecture guide）；runtime/py 167/167 文件 ≤ 1000 行（architecture guide）；13 个原 >1000 行 runtime/py 模块已拆为 facade + 同域 impl 包。函数预算 3,084 个函数全部 ≤120 行，allowlist 已清空（architecture guide）。
- runtime：133 个 TypeScript 模块，0 个 .mjs、0 个 @ts-nocheck；src + runtime 显式 any = 0，由 check:any 与 ESLint 双重门禁。
- addon：69 个模块，最大 558 行；zip 内 69 entries（sha256 ff729426…）；bl_info 1.11、protocol 11；38 registered commands + ping = 39 wire。
- 测试：Vitest 80 files / 816 passed（2 skipped）；其中 unit 61 files/564（2 skipped）、integration 18/248、contract 29/324；pytest 337；addon-unit 108；真实 Blender native E2E 76 passed（10 deselected）；GUI E2E 8 passed（2 skipped）。
- 行为矩阵 346 rows（344 covered / 0 planned / 2 exempt），覆盖 15 tools / 38 commands / 34 families / 228 ops / 29 routes。
- C5 最终树（F6 amendment）：JS 74.69 lines / 70.03 branches / 72.25 functions；changed lines 74.99%；Python pure 82.50 lines / 74.36 branches；critical 与 executionModules floor 全过。

### 修复

- **win32 后端子进程启动失败不再打死宿主（#51）**：`spawnChild` 显式传 `ELECTRON_RUN_AS_NODE=1` 与 `windowsHide:true`，并在 `unref()` 前挂 `error` 监听；win32 残留后端清理走 `runtime/win_proc.ts`（CIM → netstat/tasklist → taskkill /T /F），`EADDRINUSE` 自愈恢复；catalog 探针同修。

### runtime TypeScript 迁移

- 原 60 个 runtime/*.mjs（7,307 行）迁为 strict TypeScript，编译到 lib/runtime/*.js；T5b 与 finalization 又拆出 headless、plan-dispatch、execution 等子模块，当前 runtime TS 为 133 个模块。
- SERVER_PATH 指向 lib/runtime/server.js；JSON / 子进程 / 路径 / Python 桥边界补齐解析函数；构建顺序 runtime → host → client（architecture guide）。
- npm pack 产物含 lib/runtime/**/*.js 与 runtime/**/*.py，不含 .ts；clean clone / tarball 安装 smoke 均过。

### CI、覆盖率与发布工程

- CI：.github/workflows/pr.yml（Node 22.17 + Python 3.11；typecheck/lint/format/build/structure/tests/matrix/pack + coverage）。真实 Blender E2E 目前是本地发布检查，尚未进 CI。
- 手动 CD：.github/workflows/release.yml（workflow_dispatch；dry_run 默认 true、publish_npm 默认 false；release environment 人工审批；draft Release；npm 发布仅在审批后执行）。
- 覆盖率门禁本地通过；远程 CI 与 canonical coverage 校准仍由 T16a/T16c 处理，不在本版发布前声明为已验证。

### 文档与仓库

- 新增 architecture guide、tests/BEHAVIOR_MATRIX.md、tests/MIGRATION.md、四个 PR 模板；重构规格、CI 说明与证据包保存在本地重构工作区，不随仓库发布。
- README / docs/zh/README / CONTRIBUTING / docs/zh/CONTRIBUTING / AGENTS / addon README 全部改为 zip 安装；英文 AI 文档保留英文，中文用户文档保留中文并互链。

### 已知限制

- 远程 CI 尚未执行（T16a 仍 open）；没有 v3.0.0 tag / GitHub Release / npm publish；发布动作需人工确认。
- C5 全局 floor 仍是保守值，待 T16c 用 canonical CI 数据校准；runtime/py 还没有 pyright/覆盖率插桩；函数级大模块仍是后续债务。
- 兼容性：Blender 4.2.10 是唯一 E2E 验证版本，Blender 5.x 未验证；Node 22.17 是测试与 CI 基线，Node 20 未验证。

## v2.6.2（2026-10-28）—— 曲面算法（surface_*）+ 文档/计数一致性

> 模型工具仍 **15 个**；plan catalog 扩到 **34 family / 228 op**（+1 family / +11 op）；addon 仍 **v1.10 / protocol 11**。
> 本版**不包含**实验中的曲面工作台（workbench 面板/路由/模块已放弃，未进入发布）。

### 新增：曲面算法包（M0–M2 / M4）

- **`runtime/surface.py`**（纯 bpy + numpy，不依赖 OCCT/geomdl/trimesh）新增 11 个 op：
  `surface_loft / sweep / patch / tessellate / validate / export / blend / offset / fit / selftest / help`。
  - M0：SurfaceSpec 校验与 `spec_hash`（同 spec + tessellation ⇒ 同哈希、同顶点）；
  - M1：loft（插值型 B-spline 过截面 + Catmull-Rom 环向）、sweep（平行传输标架 + twist + 逐点 scale）、
    patch（张量积 clamped B-spline）、tessellate（spec→网格 / 对象→烘焙）；
  - M2：G0/G1/G2 缝连续性、离散曲率、BVH 自交、射线近似厚度、长宽比/翻转法线/水密，
    `verdict = pass | fail | degraded`，**fail 不静默成 pass**，degraded 不当作通过；
  - export：对象 → GLB；blend/offset 明确 `ok:false`（M5 需 OCCT），fit 保留接口（M3 未实现）。
- **接线**：`runtime/paths.mjs` SURFACE_PATH、engine `SURFACE_READY` + `surface_` 前缀路由、
  catalog 新 family、plan headless 映射（preload `surface,audit`）、只读白名单补 `surface_help`。
- **真机自检** `tests/surface_selftest.py`：三基准（瓶身/翼子板/头盔）+ 自交/薄壁/连续性负例 +
  确定性 + tessellate 双通路 + 7 类坏参数；headless `preload="surface,audit"` 全绿（23 项证据）。

### 文档

- 新增 [docs/曲面算法.md](docs/曲面算法.md)（op 表/SurfaceSpec/verdict/限制/配合链路）；
- README 中/英文各加「参数化曲面」特性与文档入口；
- 操作教程新增 §3.5.6b 参数化曲面；
- skill：SKILL.md §0.7 工具表 + §0.7.1 曲面算法回路（含硬规则）、§9 family 速查；
  新增 `skills/blender-modeling/references/曲面算法.md`，install.sh 同步拉取；skill README 更新 references 清单。

### 修复

- **`scripts/sync_counts.mjs` 三处静默漏刷**：AGENTS 正则只认「N 个 family / N 个 op」（2.6.1 改成不带「个」后永远匹配不到却报“已一致”）；
  只刷工作区 AGENTS、不刷仓内 AGENTS；单次 replace 只改第一处。改为双写法 + 双 AGENTS + `/g`，
  并新增 `docs/操作教程.md` 与 `skills/blender-modeling/SKILL.md` 两个目标（本次实测已把两处 33/217 刷成 34/228）。
- 修正 skill / 操作教程里残留的 `33 family / 217 op` 旧口径。

### 新增（v2.6.1 之后合入）
- **headless：`opts.hardExit`（默认开启）**——无头脚本在 `done` 标记之后显式冲刷 stdout/stderr，
  随即 `os._exit(0)` 跳过 Blender 自身退出拆解。现场：Blender 5.2.3（Steam）在 .blend 加载材质带
  Value→Material Output.Thickness 连线时，glTF 导出**成功、产物完整、done 已打出**之后，
  在退出期命中导出器 AV（0xC0000005），结果被 exitCode≠0 顶成"失败"。
  依赖正常 teardown 的调用方（`bpy.app.handlers` 落盘、延迟自动保存）传 `hardExit=false` 回旧行为。
  作业路径与同步路径同一份实现（`runtime/hard_exit.mjs`）；
  新增 `tests/headless_hard_exit_selftest.mjs`（顺序契约 / 开关语义 / 接线断言，已入 `npm test` 主链，
  `npm run test:hardexit`）。
### 修复
- **headless：`file=` 缺失/空串/参数名误写不再静默成功**（旧症状：`file=""` 或 `blend=<路径>` →
  `ok=true / exitCode=0 / inputFile=null / 零警告`，脚本拿到出厂空场景）：
  引擎侧 `file=` 显式校验（空串→`file-empty`、非字符串→`file-type`，直连 `/headless` 的兜底）；
  工具端（`blender_rt_headless` / `blender_rt_job` op=start|run）拦截参数名误写（`blend=`/`path=` 等六名）
  与空串——工具端在 body 组装前剥 falsy，引擎守卫对工具路径永不触发，故 ALIAS 拦截必须落在工具端。
  行为变化提示：`file=false`（布尔）旧行为被静默当「未传」，现按 `file-type` 明确报错——类型纪律收紧，
  依赖旧语义者请改为省略该参数。
- **headless：「成功但没开档」不再无声**——回执 `inputFile` 的 null 分支换成 `{given, opened, reason}`
  语义对象（形态冻结：truthy 的 `fileFingerprint` 返回值一字未动）；未开档时 `warnings` 追加
  `no_input_file`（含 `.py` 语义切换场景）；台账（headless/job 的 running 与终态）补记
  `file`/`inputFile`，事后取证可区分「没给档」与「给了没开」。
- **headless：同毫秒撞名**——无头脚本名（`staging.mjs`，旧 `Date.now().toString(36)` 同毫秒互相覆写）
  与 stdout/stderr log 名（`engine.mjs`，同毫秒追加写同一文件互脏）都加唯一后缀（脚本名 = 毫秒+进程内
  序号+随机；log 名 = 毫秒+runId）。
- 新增 `runtime/file_guard.mjs`（engine / 工具端 / 单测共用同一份判定）与
  `tests/headless_file_guard_selftest.mjs`（41 项，离线，已入 `npm test` 主链，`npm run test:fileguard`）。

## v2.6.1（2026-10-23）—— 性能优化（选中轮询 / 审计 / 批处理 update）+ 平台与测试修复

> v2.6.0 之后合入的改动打包为本版（含此前 PR 中标注为 v2.7 的 dsh-TUI 宿主半边）。
> 模型工具仍 **15 个**；plan catalog 仍 **33 family / 217 op**；addon 仍 **v1.10 / protocol 11**。

### 性能（真实成品文件实测）
- **选中轮询 1200 ms → 100 ms**（Web 客户端 + dsh-TUI 宿主半边）：端到端 p50 ~993 ms → ~156 ms
  （本机回环，8 次选中变化；CAS consume 语义不变）。
- **`audit._tris` 大网格快路径**：面数 ≥256 时批量 `foreach_get("loop_total")`；
  137k 面 59.3 ms → 15.5 ms（3.8×），逐值等价。
- **批处理 update 第 1/2 步**：新增 `K.dsh_kit.BatchUpdates`；`motion._restore_transforms` 改为
  「先写全部字段 → 一次 update → 统一核对」，真实成品 300 对象还原 32× / 91×
  （行星发动机 / 太空电梯），motion selftest 25/25。
- 实测否定了「msgbus 订阅选中」：Blender 5.2 上 `Object.select` 不是 RNA 属性；
  选中变化继续用 `depsgraph_update_post`（0.3–0.7 ms）。

### 功能（v2.6.0 之后合入）
- **#35 工具卡片**：15 个工具的 `presentCall` / `presentResult`，宿主不再显示整段 JSON。
- **#36 dsh-TUI 宿主半边**：选中上下文 chip、页脚状态字段、`@` 提及、`/blender status|doctor|jobs`；
  按接缝能力检测启用，新增只读 `GET /host/state`。

### 修复
- **#38 / #39 / #43 Windows 终端弹窗**：三处 blender `spawn` + 两处 `execFileSync` 补 `windowsHide: true`，
  并加 `tests/windows_hide_selftest.mjs` 静态自检（进 `npm test`）。
- **#40 / #41 capability lock**：发布 tarball（无 `src/`）不再 ENOENT，降级工具维度并显式打印 SKIP。
- **#42 capability lock families**：逐族取数改用族名字符串 + `full:true` detail，恢复族名 / 逐族 op 维度；
  用 `--update` 重新落锁。

## v2.6.0（2026-10-21）—— 33 family / 217 op · GUI 优先 · 无头备用 · 文档与 skill 重写

> v2.5.0 之后的四轮工作（P0 / P1 / P2 + 文档与 skill 重写）并入本版一次发布。
> 模型工具仍为 **15 个**；plan catalog 从 28 family / 186 op 扩到 **33 family / 217 op**。
> 新增能力全部走 plan catalog，**不新增模型工具**；GUI 仍是最优先；无头能力补齐（含 rt_see），作为无 GUI / 批处理 / 长活的备用。
> **2026-10-21 口径更正**：不是 headless-first；默认先开 GUI，headless 仅备用。

### 测试与文档修正（issue #32 / #33）

- **#32** `tests/linux_selftest.mjs`：裸 `import(path.join(...))` 在 Windows 上抛 `ERR_UNSUPPORTED_ESM_URL_SCHEME`，
  `npm test` 链式中断、其后 13 个用例静默跳过 → 改 `pathToFileURL(...).href`。
- **#33** `tests/workdir_shared_selftest.mjs`：C5 用 `path.sep` 切 POSIX 风格路径，Windows 上恒红
  → 改 `path.posix.basename(...)`。
- **文档口径**：改正「`/host/view/*`、`/host/pick*`、`/host/ready` 路由为兼容保留」的错误表述 ——
  这些路由在 v2.3 已随面板删除，现存宿主路由只有 `/host/selection`（+`/latest`、`/consume`）、
  `/host/scene-items`、`/host/jobs`（+`/log`）。

### 文档与 skill 重写（2026-10-21）

- **全量文本校正**：README 中英、操作教程、配置参考、安装/部署/迁移/AGENTS/CLAUDE 全部对齐 v2.6.0（15 工具 · 33 family / 217 op · addon v1.10 / protocol 11）；删除 28/186、v2.5/v0.9.x 旧口径与“必须 GUI”旧承诺。
- **skill 架构优先重写**：SKILL.md 以“插件就是建模流程”为主线（catalog → plan op → worker session → 回执/verdict → 门/交付），新增架构铁律、标准状态机、工具选择矩阵、P0–P2 配方、反模式、多 Agent 协议；通用 Blender 知识降为附录 A。
- **references 校正**：验收清单补 asm/visualreport/eval/强制门；参考图 SOP 改为 img_digest/measure/calib + shapesolve + visualreport；multiview 与英文 overview 标明与当前插件的关系。
- **工具描述**：rt_do/rt_see/rt_watch/rt_worker/rt_headless/rt_plan 明确 GUI 首选、session 无头、worker see/watch/TTL、plan session=auto；package description 同步 v2.6.0。
- 文档 grep：用户文档无 28/186/v2.5/v0.9.x 旧口径；skill >50KB 且 skill_bundle_selftest 61/61；npm test 全绿。

### P2 第三轮（2026-10-20）

- **装配交付导出** `asm_export`：GLB/FBX/OBJ + manifest.json + append-only merge_ledger.jsonl；`merge=true` 先 `asm_merge`（gate+snapshot，不过门不导出）；67 evidence 真机全绿。
- **低风险修复策略** `asm_repair(policy=safe|manual|aggressive)`：safe 只做 translate/snap、硬禁布尔；manual 只规划不执行；回归 88 evidence 全绿。
- **缺陷注入/基线评估** `eval_run`（evalharness.py）：assembly D1/D3/D4/D5/D7/D8 + reference 12 例 + calib core；缺模块 skipped/degraded 不假通过；assembly capture=1.0、reference 12/12、core degraded 0.875。
- **Seedance 参考包** `blockout_refsheet`（4 正交/透视 PNG + 2x2 拼图 + refsheet.json + 可选转台）+ `blockout_turntable`（Blender 内置 FFMPEG MP4/H264）；22 通过/0 失败；256x256/12 帧 MP4 9192B。
- **无头 watch** `rt_watch(session=...)` + worker `_watch`：逐帧 Workbench 离屏 + hash + 最多 6 帧图；真机 3 帧动画 distinct=3/58ms。
- **无头 plan 路由** 扩展 eval/blockout/asm 等 family；真机 `asm_export` 161ms、`blockout_refsheet` 331ms、`turntable` 334ms。
- catalog 33 family / 217 op；`npm test` 全绿；capability lock / 只读白名单 / 计数同步。

### P1 第二轮（2026-10-20）

- **装配修复**：`asm_repair` + `fix_clash`（执行器，dry_run 默认）+ `fix_interference`（只读规划器）；2mm 互穿 dry 不动场景→执行后 clash=0/float=0/有 snapshot；contained/布尔 subtract/浮块 snap 计划；88 条真机断言。
- **强制门/flow op**：`gate_preflight / gate_checkpoint / gate_deliver`；`deliver_export(require_gate=true)` 真机验证（pass 才写盘，bad gate blocked；deliver+verify 18 项）。
- **无头 plan 路由**：`blender_rt_plan(..., session="auto")` 按 family 自动起持久 worker + preload + dispatch，不依赖 GUI；首次冷启动 ~2s，之后同步复用。
- **会话池**：worker idle TTL（默认 15 min，`DSH_WORKER_TTL_MS`）+ `idleMs` 回执。
- **同机位 QC**：`visualreport_report`（verdict-first，qc→imgtools→numpy 三级降级）；32/32；同图 pass，平移/缺角 fail，缺陷 crop + mm 定位，72–96ms。
- **无头轮廓拟合**：`shapesolve_solve`（正交解析投影+numpy 光栅化，不渲染）；三视反演 40/20/30→误差 0.98%/1.55%/0.35%、IoU [1,1,1]，solve 793ms。
- catalog 32 family / 211 op；`npm test` 全绿；capability lock / 只读白名单 / 计数已同步。

### P0 第一轮（2026-10-20）

- **交互式 headless（重点）**：`rt_worker` 支持 `preload` 启动前导 + `see`（临时相机 + Workbench 离屏）+ `reset` + `warmup`；实测 start 2.24s（含 1.1s 首帧初始化），之后 exec 0–2ms、see 往返 53–85ms（320×180），**不是 job/异步回合**。
- `rt_do` 在 GUI 不可达/显式 `session` 时自动降级到持久无头 worker（exec + see，图片直接回模型）；`rt_see` 增加 `session/auto`；`rt_loop` 增加 `target="session"` 的 `dsh_loop_sync` 阻塞内环（20 次 56ms，不依赖 timers）。
- `rt_headless` 支持 `attach_images`（true/"sheet"）；`rt_plan` 增加单行 receipt.v2 信封（verdict/top_defects/next）+ `attach_images`（≤2 张），plan 主通道不再只有 12KB 文本。
- `imgtools.py` v2：新增 `img_digest`（原生分辨率量测/轮廓/Hough/孔/对称/可读性/原生裁剪/标注 + 可确认数字表）、`img_probe`、`img_measure`、`img_calib`；真机 61/61 断言。
- 新增 `runtime/assembly.py`：`asm_scan`（全对全分类：clash/hidden_ok/contact/gap/float/contained/unresolved + 完全包含补测）、`asm_manifest`（md5/接口帧/allow 校验）、`asm_merge`（id 序 append + 法线 + snapshot + 自动 gate，dry-run 默认）、`asm_gate`（verdict-first，export_allowed 仅 pass）；模块自检 + 测试脚本全绿。
- 新增 `runtime/blockout.py`：`blockout_base/batch/export`（声明式 parts、mm/anchor、Workbench 3 视图、GLB）；真机 33/33，20 件 geom 9ms、3 视 512 约 1.1s（首帧后 25ms/张）、GLB 117KB。
- **可交互 headless 真机建模测试**：参考图→img_digest（2m×0.8m 盒子：width 2000mm/height 800mm/px_per_mm 0.16）→ 12 件白模（GLB 70KB）→ 装配两件 2mm 互穿 scan 检出 depth=2mm/volume≈2.0e6mm³，移开 10mm 后 clash=0。
- 目录 30 family / 201 op；`npm test` 全绿；`capability.lock` / `plan_readonly.baseline` / provenance 计数已同步。

### 主线：七条实测反馈 —— 台账对账 / 5.2 合成器 / 多灯卫生 / 进度通道 / 第五态 / 不覆盖声明 / 写作用域两维

**来源**：一次 4 小时、3 个子代理、479 对象的真实项目（120 m 哥斯拉 + 被毁城市 + 原子吐息）后，
Lead 与 3 个子代理共同复盘出的 7 条改进；按顺序落地，每条都有独立自检。

1. **台账以文件为准（`runtime/recover.mjs`）**：后端重启/被杀后，磁盘台账里只剩 `status=running`、pid 已死，
   但**子进程其实已经跑完、产物/日志都在**。旧行为一律判 `stale`，模型只能自己去 `Get-Process` + `ls` 破案
   （实测两次，各赔一轮往返）。现在 `reconcileLedger` 先做磁盘对账（`summary.json` → `resultPath` → stdout 终态
   `DSH_STAGE done` / `HEADLESS` / `Traceback`），对得上就返回 `status=done|failed` + `recovered:true` +
   `recoveredFrom/recoveredEvidence`；对不上才 `stale`，并附 `evidence` / `evidenceChecked`。
   run 与 job 的台账 running 记录也补了 `stdoutLog`/`stderrLog`/`dir`，后端重启后不用扫目录猜。
2. **Blender 5.2 合成器迁移（skill §6.5.6）**：补 5 行踩坑表 + 一段最小可跑片段 ——
   `scene.node_tree` 已删（改 `compositing_node_group` + `NodeGroupOutput`；`CompositorNodeComposite` 不存在）、
   Glare 的 Type/Quality 变 MENU 插座、`CompositorNodeMixRGB`/`CompositorNodeMath` 被删（改用 `ShaderNodeMix`/`ShaderNodeMath`）、
   `mat.cycles.displacement_method` → `mat.displacement_method`、`surface_render_method` 只有 DITHERED/BLENDED。
3. **多灯卫生**：识别 `Shadow buffer full (N / M)` 并产出结构化 warning（`code=eevee_shadow_pool_full`，含次数/用量/上限/可粘贴修法）；
   新增常驻 `K.light_hygiene(apply=True, max_shadow=4)`（只保留能量最高的 N 盏点/聚光灯投影 + 池设 2048）。
   实测：36 盏灯 → 控制台每帧刷错且 `shadow_pool_size='4096'` 静默无效（枚举上限 2048）。
4. **进度通道**：`dsh_progress(**fields)` / `DSH_PROGRESS {json}` 与 stage 分开（stage=阶段跃迁，progress=阶段内高频进度，留最近 20 条）；
   运行期每 5 s 心跳（`heartbeat`：elapsedMs/logBytes/lines/stage/progressAgeMs，作业还会 append `heartbeat.jsonl`）；
   `op=status/collect` 与 headless 回执都带 `progress/progressAt/heartbeat/progressHistory`。