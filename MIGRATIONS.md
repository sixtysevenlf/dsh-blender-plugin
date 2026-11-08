# MIGRATIONS —— 从旧版本迁到 v3.1

> **当前版本（2026-11-08）**：插件 **v3.1.0** · **6 个模型工具** · plan catalog **34 family / 228 op** ·
> addon command **25**（+ping = 26 wire）· addon **v2.0.0**（protocol **12**）· Gateway `127.0.0.1:39877` · addon 主动连接 Gateway（无入站监听）。
> **旧 `MCP for Blender` 自 v2.3 起不再支持**；v3.0 是工程重构 + addon 安装方式 breaking，按下文 §0/§1 迁。

## 0. 3.1 连接模型与端口迁移

> 3.1 起唯一入站端口是 Gateway `127.0.0.1:39877`；addon 改为主动连接 Gateway，**不再监听 9876**。
> 升级/替换 addon 后必须重启 Blender：addon 没有热重载，旧 3.0.x addon 的 9876 listener 不会自动释放。
> 3.0.x 的后端 9877 → 3.1 的 Gateway 39877；`9877` 只在显式配置时生效。
> 旧 `MCP for Blender` 仍不受支持；它可能继续监听 9876，但不要与本 addon 同时启用。

## 0.1 最短路径（任何旧版 → v3.0.0）

1. 更新 DSH 插件包到 **v3.0.0**：`dsh plugin --profile <profile> add dsh-blender-plugin@3.0.0` → **重启 DSH**；旧「Blender 视口」tab / 点击进消息在 v2.3 已删除，这是预期。
2. Blender 里停用并移除旧 addon（含旧的单文件 `dsh_blender_bridge.py`）→ 安装本仓 `extension/dsh_blender_bridge.zip`（**v2.0.0**）→ 启用 → N → Connect。
3. `blender_inspect(command="get_addon_info")` → `addon_version=[2,0,0]`、`protocol=12`；
   GUI 路线 `curl http://127.0.0.1:39877/doctor` → `kind=ok`（纯无头路线用 `blender_run(action="submit", kind="headless", …)` + `wait`/`collect` 取产物自证）；
   `blender_op(op="catalog")` → **34 family / 228 op**。
4. 选中入口：Blender 原生选中 → `N` →「Selection → DSH」→ 发送（多选 / 集合）；编辑模式选点/面、姿态选骨骼也会一起发。
5. 新能力（交互式无头、`img_*`、`asm_*`、`blockout`、`visualreport`、`shapesolve`、`eval_run`、强制门等）用
   `blender_op(op="catalog")` 查骨架；除 v3.0 明确移除的 Premium/Tripo 通道外，旧 op 不删除、只增不改。

## 0.1 v3.0.0 明确移除的能力（breaking）

- **Premium 通道**：Premium 许可 / 额度 / Supabase 客户端 / 默认 quality 与激活 UI 全部删除；不再有 premium 生成入口。
- **Tripo 通道**：Tripo 没有 BYOK 路径，因此 get_tripo_status、create_tripo_job、poll_tripo_job_status、import_generated_asset_tripo 与 N 面板开关一并删除；addon command 39 → 38。
- **扁平 Python 模块路径移除**：runtime/*.py 的 38 个 re-export shim 删除，generator.py / uv_tools.py 移入 runtime/py/geometry/；preload / module 的公开模块名不变，但旧式 `import runtime.<name>`、或按 `runtime/<name>.py` 直接加载不再可用——改用 `runtime.py.<domain>.<name>` 或按 `runtime/py/modules.json` 定位。
- **替代路径**：Hyper3D 与 Hunyuan3D 继续支持自有 API key；需要 Tripo 时请在外部服务完成生成后把资产导入 Blender。
- 除上述声明移除项外，原有 op/参数/返回结构保持兼容；不要因为本版重构而改写既有调用。

## 0.2 3.1 addon 命令面收缩与零遥测（breaking）

3.1 只保留 25 条注册命令：18 条常驻 + 7 条集成门控，其中 21 条只读 + 4 条内部传输（execute_code、get_viewport_screenshot、send_selection_to_dsh、send_collection_to_dsh）；wire 另含 ping，共 26 条。以下 13 个旧命令已从命令面删除，**不提供别名**：

- 写场景/资产：`export_scene`、`set_texture`、`download_polyhaven_asset`、`create_rodin_job`、`import_generated_asset`、`download_sketchfab_model`、`download_polypizza_model`、`create_hunyuan_job`、`import_generated_asset_hunyuan`
- 采集/同意：`get_telemetry_consent`、`set_telemetry_consent`、`drain_human_activity`
- 预览：`get_polyhaven_asset_preview`

旧调用会得到 unknown command 或 `command_removed` 提示。当前命令面用 `blender_inspect` 查看（默认 help）；写命令已从命令面删除。addon 不再采集人工编辑事件、没有 telemetry 偏好、没有 Discord/Terms 推广入口；`get_activity_state` 的 human/agent/idle/both 判定只在内存中计算，不落盘、不外发。

## 0.3 3.1 模型工具面：15 → 6（breaking）

3.1 模型面从 15 个工具收口为 6 个：`blender_session`（会话/归属/权限/事务）、`blender_op`（catalog 与 loop/opt/preset）、`blender_py`（GUI 会话内执行 Python）、`blender_see`（GUI 视点截图与序列帧）、`blender_run`（唯一作业入口：render / headless）、`blender_inspect`（只读 addon 命令目录与执行）。旧名不再注册，调用旧名会得到迁移提示。

| 3.1 之前 | 3.1 模型面 |
|---|---|
| `blender_rt_see` | `blender_see` |
| `blender_rt_watch` | `blender_see(mode="sequence")`（`code=` 改由 `blender_py` 执行） |
| `blender_rt_do` | `blender_py`（`session` / `preload` / `auto` / `view_size` 参数删除） |
| `blender_rt_plan` | `blender_op`（`action` 默认 `plan`；`op` / `args` / `path` / `attach_images` 不变） |
| `blender_rt_loop` | `blender_op(action="loop", …)` |
| `blender_rt_opt` | `blender_op(action="opt", …)` |
| `blender_rt_preset` | `blender_op(action="preset", …)` |
| `blender_rt_perf` | `blender_run(action="tune", …)` |
| `blender_rt_headless` | `blender_run(action="submit", kind="headless", …)`（异步提交；`wait` / `collect` 收取） |
| `blender_rt_job` | `blender_run(action="submit" \| "status" \| "wait" \| "collect" \| "cancel" \| "list")` |
| `blender_rt_cmd(name=X, params=Y)` | `blender_inspect(command=X, args=Y)` |
| `blender_rt_commands` | `blender_inspect`（默认 help） |
| `blender_rt_txn(op=O, …)` | `blender_session(action="txn", op=O, …)` |
| `blender_viewport` | `blender_session`（status/doctor/presence；lease/launch/restart 改由宿主管理，不再是模型工具） |

`blender_rt_worker` 不再属于模型面：交互式「改一步看一眼」用 `blender_py` + `blender_see`（需要 ready 的 GUI 会话）；没有 GUI 时的脚本/渲染改用 `blender_run(action="submit", kind="headless")`，再 `wait`/`collect` 取产物。`session="auto"`、`preload="…worker…"`、worker see/watch 不再是对用户暴露的能力。

## 1. Blender 侧 addon：必须换成 `DSH Blender Bridge` v2.0.0（Blender 4.2 extension）

v2.3 的原生选中 / 集合 / activity 依赖本仓 addon 的 5 条命令与 N 面板入口，旧 addon 没有。
3.1 addon 主动连接 Gateway，不再监听 `127.0.0.1:9876`；旧 `MCP for Blender` 仍可能占用该端口，两者不要同时启用。

1. `Edit ▸ Preferences ▸ Add-ons` → 停用旧条目（`MCP for Blender` / `blender_mcp` / `blender_mcp_addon`）。
2. `Extensions ▸ Install from Disk...` → 选本仓 [`extension/dsh_blender_bridge.zip`](extension/dsh_blender_bridge.zip) → 勾选启用 **DSH Blender Bridge**（v2.0.0 / protocol 12）。v3.0 起不再支持单文件安装。
3. 3D 视图按 `N` →「DSH Blender Bridge」→ **Connect**（addon 主动连接 Gateway，默认 `39877`）；Gateway/Blender 生命周期由宿主管理，不再有模型侧启动工具。
4. `blender_inspect(command="get_addon_info")` → `addon_version` 应为 `[2,0,0]`、`protocol=12`，capabilities 含
   `get_activity_state` / `get_selection` / `get_collections` / `send_*_to_dsh`。

> 旧名 `blender_mcp` 的"已启用"记录是死引用，可以移除；插件**不再做双名回退**。

## 2. DSH 侧：升级插件后必须重启 DSH

- 客户端半边（`@` 提及 + 原生选中桥）是启动时装载的；不重启会继续跑旧 bundle（里面还有已删除的面板）。
- 旧「Blender 视口」tab、点击进消息、模型点选工具在 v2.3 全部消失，这是**预期行为**。
- 选中入口改为：Blender 原生选中 → `N` →「Selection → DSH」→ 发送（多选 / 集合两种模式）。
- v2.6 升级后同样需要**重启 DSH**；只改后端时重启宿主即可（Gateway 生命周期由宿主管理，没有模型侧 restart 工具）。

## 3. 配置文件清理（可选）

- `addonProtocol` 配置项与 `DSH_BLENDER_ADDON_PROTOCOL` 环境变量**已删除**（只剩本仓扁平协议）。
  旧配置里留着也不影响启动，但建议删掉，避免误以为还能切协议。
- 其他 `DSH_BLENDER_*` 配置项不变，见 [`docs/配置参考.md`](docs/配置参考.md)。

## 4. 仓库使用方式（v3.0.0 / T15 起）

- **npm 包名**：v3.0.0 起为 `dsh-blender-plugin`，不是 `@dsh-external/dsh-blender-plugin`。安装/升级命令：
  `dsh plugin --profile <profile> add dsh-blender-plugin@3.0.0`。发布包自带 `lib/` 构建产物，装完即可用。
- **从 Git 使用**需要先构建一次：`lib/` 不再入库，`git clone` 后依次跑 `npm ci --legacy-peer-deps` 和
  `npm run build`（`DSH_CHECKOUT` 只在需要链接 DSH 宿主包时可选）。`prepack` 会自动构建，
  所以 `npm pack`/发布不需要额外步骤。
- 后端 TypeScript 源码在 `runtime/`，编译产物在 `lib/runtime/`；不要手改 `lib/`。

## 5. 历史接口契约

`docs/宿主集成-接口契约.md`、`宿主集成-接口契约-v2.1增补.md`、`v2.2增补.md` 与 TUI 适配文档已删除；
当前唯一真源是 [`docs/宿主集成-接口契约-v2.3增补.md`](docs/宿主集成-接口契约-v2.3增补.md) —— 该文件顶部有 **v2.6 兼容声明**
（v2.3 契约仍适用；addon v1.10 / protocol 11；新增 P0–P2 op 见 catalog）。

## 6. addon v1.10 / v1.11 / v2.6 追加（无需迁移动作）

- **addon v1.11**：拆成包并改为 zip 安装；命令名、参数、返回结构与 pause 语义不变（protocol 11）。
- **addon v1.10**：N 面板活动状态置顶、环境与角色模型 API 子面板、**暂停 AI 操作**（暂停后只放行只读命令，
  恢复只能由人在 Blender 点）。升级 addon 即可，无配置迁移。
- **v2.6**：回执**只增字段**（`recovered / progress / heartbeat / warnings / scope` 等，不改旧字段语义）；
  `audit_mesh` 新增第五态 `pass_multi_shell`；catalog 增加"不覆盖声明"；写作用域支持集合 + 对象前缀两维。
  升级插件后**重启 DSH**；旧调用参数保持兼容。
- 新增能力（P0–P2）：`img_digest/probe/measure/calib`、`asm_scan/manifest/merge/repair/export/gate`、
  `blockout_base/batch/export/turntable/refsheet`、`visualreport_report`、`shapesolve_solve`、`eval_run`、
  `gate_preflight/checkpoint/deliver` + `deliver_export(require_gate)` —— 都是**新增 op**，用 catalog 查，不替换旧用法。
  3.1 起模型面收口为 6 个工具（§0.3）：GUI 交互用 `blender_py` / `blender_see`，长活和无头用 `blender_run`。

> **不覆盖 / 未安装依赖**（迁移后也不要承诺）：landmark/人脸（无 cv2/MediaPipe）、OCR、DXF、
> Open3D / manifold3d / glTF-Validator 未安装，不在支持范围。

## 7. 卸载与回滚（v3.0.0）

### 7.1 卸载 v3.0.0

1. Blender：`Edit ▸ Preferences ▸ Add-ons` → 停用 **DSH Blender Bridge** → 移除；如残留包目录，删除 `scripts/addons/dsh_blender_bridge/`（或你的 Blender 用户 addons 路径下的同名目录）。
2. DSH：从 profile 的 `node_modules/dsh-blender-plugin` 卸载插件包（或从 profile bundle 列表移除）→ **重启 DSH**；随包注册的 blender-modeling skill 会随插件移除，不需要手删用户技能目录。
3. 旧单文件 `dsh_blender_bridge.py`（v2.6.1 及以前）在升级时就必须停用并移除；v3.0.0 不会再加载它。

### 7.2 回滚到 v2.6.2

1. 安装 v2.6.2 的插件包（tag `refactor-base-v2.6.2` / 对应 tarball），不要用 v3.0.0 的目录覆盖；
2. Blender 里停用 v1.11 zip addon，重新安装 v2.6.2 自带的单文件 `dsh_blender_bridge.py`（或 v2.6.2 的 addon 产物），启用后 N → Connect；
3. **重启 DSH**；回滚后的 v2.6.2 是 15 工具面，按 §0.3 对照表换名调用；`addon_version` 应回到 v1.10 / protocol 11，catalog 应回到 34 family / 228 op。
4. v2.6.2 tag 自带 `lib/` 与旧 `runtime/*.mjs`，不需要构建；v3.0.0 的 `lib/runtime` 与 `.ts` 不要混用。
5. 回滚演练记录保存在本地重构工作区，不随仓库发布。

