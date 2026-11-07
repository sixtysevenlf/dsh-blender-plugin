# MIGRATIONS —— 从旧版本迁到 v3.1

> **当前版本（2026-11-08）**：插件 **v3.1.0** · **15 个模型工具** · plan catalog **34 family / 228 op** ·
> addon command **38** · addon **v2.0.0**（protocol **12**）· Gateway `127.0.0.1:39877` · addon 主动连接 Gateway（无入站监听）。
> **旧 `MCP for Blender` 自 v2.3 起不再支持**；v3.0 是工程重构 + addon 安装方式 breaking，按下文 §0/§1 迁。

## 0. 3.1 连接模型与端口迁移

> 3.1 起唯一入站端口是 Gateway `127.0.0.1:39877`；addon 改为主动连接 Gateway，**不再监听 9876**。
> 升级/替换 addon 后必须重启 Blender：addon 没有热重载，旧 3.0.x addon 的 9876 listener 不会自动释放。
> 3.0.x 的后端 9877 → 3.1 的 Gateway 39877；`9877` 只在显式配置时生效。
> 旧 `MCP for Blender` 仍不受支持；它可能继续监听 9876，但不要与本 addon 同时启用。

## 0.1 最短路径（任何旧版 → v3.0.0）

1. 更新 DSH 插件包到 **v3.0.0**：`dsh plugin --profile <profile> add dsh-blender-plugin@3.0.0` → **重启 DSH**；旧「Blender 视口」tab / 点击进消息在 v2.3 已删除，这是预期。
2. Blender 里停用并移除旧 addon（含旧的单文件 `dsh_blender_bridge.py`）→ 安装本仓 `addon/dsh_blender_bridge.zip`（**v2.0.0**）→ 启用 → N → Connect。
3. `blender_rt_cmd get_addon_info` → `addon_version=[2,0,0]`、`protocol=12`；
   GUI 路线 `curl http://127.0.0.1:39877/doctor` → `kind=ok`（纯无头路线用 `rt_worker(start)` + `see` 出图自证）；
   `blender_rt_plan(op="catalog")` → **34 family / 228 op**。
4. 选中入口：Blender 原生选中 → `N` →「Selection → DSH」→ 发送（多选 / 集合）；编辑模式选点/面、姿态选骨骼也会一起发。
5. 新能力（交互式无头、`img_*`、`asm_*`、`blockout`、`visualreport`、`shapesolve`、`eval_run`、强制门等）用
   `blender_rt_plan(op="catalog")` 查骨架；除 v3.0 明确移除的 Premium/Tripo 通道外，旧 op 不删除、只增不改。

## 0.1 v3.0.0 明确移除的能力（breaking）

- **Premium 通道**：Premium 许可 / 额度 / Supabase 客户端 / 默认 quality 与激活 UI 全部删除；不再有 premium 生成入口。
- **Tripo 通道**：Tripo 没有 BYOK 路径，因此 get_tripo_status、create_tripo_job、poll_tripo_job_status、import_generated_asset_tripo 与 N 面板开关一并删除；addon command 39 → 38。
- **扁平 Python 模块路径移除**：runtime/*.py 的 38 个 re-export shim 删除，generator.py / uv_tools.py 移入 runtime/py/geometry/；preload / module 的公开模块名不变，但旧式 `import runtime.<name>`、或按 `runtime/<name>.py` 直接加载不再可用——改用 `runtime.py.<domain>.<name>` 或按 `runtime/py/modules.json` 定位。
- **替代路径**：Hyper3D 与 Hunyuan3D 继续支持自有 API key；需要 Tripo 时请在外部服务完成生成后把资产导入 Blender。
- 除上述声明移除项外，原有 op/参数/返回结构保持兼容；不要因为本版重构而改写既有调用。

## 1. Blender 侧 addon：必须换成 `DSH Blender Bridge` v2.0.0（Blender 4.2 extension）

v2.3 的原生选中 / 集合 / activity 依赖本仓 addon 的 5 条命令与 N 面板入口，旧 addon 没有。
3.1 addon 主动连接 Gateway，不再监听 `127.0.0.1:9876`；旧 `MCP for Blender` 仍可能占用该端口，两者不要同时启用。

1. `Edit ▸ Preferences ▸ Add-ons` → 停用旧条目（`MCP for Blender` / `blender_mcp` / `blender_mcp_addon`）。
2. `Extensions ▸ Install from Disk...` → 选本仓 [`addon/dsh_blender_bridge.zip`](addon/dsh_blender_bridge.zip) → 勾选启用 **DSH Blender Bridge**（v2.0.0 / protocol 12）。v3.0 起不再支持单文件安装。
3. 3D 视图按 `N` →「DSH Blender Bridge」→ **Connect**（addon 主动连接 Gateway，默认 `39877`）；或直接 `blender_viewport(op="launch")`。
4. `blender_rt_cmd get_addon_info` → `addon_version` 应为 `[2,0,0]`、`protocol=12`，capabilities 含
   `get_activity_state` / `get_selection` / `get_collections` / `send_*_to_dsh`。

> 旧名 `blender_mcp` 的"已启用"记录是死引用，可以移除；插件**不再做双名回退**。

## 2. DSH 侧：升级插件后必须重启 DSH

- 客户端半边（`@` 提及 + 原生选中桥）是启动时装载的；不重启会继续跑旧 bundle（里面还有已删除的面板）。
- 旧「Blender 视口」tab、点击进消息、`blender_rt_pick` 在 v2.3 全部消失，这是**预期行为**。
- 选中入口改为：Blender 原生选中 → `N` →「Selection → DSH」→ 发送（多选 / 集合两种模式）。
- v2.6 升级后同样需要**重启 DSH**；只改后端时可用 `blender_viewport(op="restart")`。

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
- 新增能力（P0–P2）：交互式无头（`rt_worker` see/watch/reset/preload/warmup、`rt_do/rt_see` 可 `session/auto`、
  `plan session="auto"`、worker 空闲 TTL）、`img_digest/probe/measure/calib`、`asm_scan/manifest/merge/repair/export/gate`、
  `blockout_base/batch/export/turntable/refsheet`、`visualreport_report`、`shapesolve_solve`、`eval_run`、
  `gate_preflight/checkpoint/deliver` + `deliver_export(require_gate)` —— 都是**新增 op**，用 catalog 查，不替换旧用法。

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
3. **重启 DSH**；`blender_rt_cmd get_addon_info` 应回到 v1.10 / protocol 11，`blender_rt_plan(op="catalog")` 应回到 34 family / 228 op。
4. v2.6.2 tag 自带 `lib/` 与旧 `runtime/*.mjs`，不需要构建；v3.0.0 的 `lib/runtime` 与 `.ts` 不要混用。
5. 回滚演练记录保存在本地重构工作区，不随仓库发布。

