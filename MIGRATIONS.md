# MIGRATIONS —— 从旧版本迁到 v2.6

> **当前版本（2026-10-20）**：插件 **v2.6.2** · **15 个模型工具** · plan catalog **34 family / 228 op** ·
> addon **v1.10**（protocol **11**）· 后端 `127.0.0.1:9877` / addon `127.0.0.1:9876`。
> **旧 `MCP for Blender` 自 v2.3 起不再支持**；v2.3/v2.4 的迁移变更与 addon v1.10 的面板更新仍然适用，
> v2.6 以**新增字段/能力**为主，按下文 §0 最短路径迁完即可。

## 0. 最短路径（任何旧版 → v2.6.2）

1. 更新 DSH 插件包到 **v2.6.2** → **重启 DSH**；旧「Blender 视口」tab / 点击进消息在 v2.3 已删除，这是预期。
2. Blender 里停用旧 addon → 安装本仓 `addon/dsh_blender_bridge.py`（**v1.10**）→ 启用 → N → Connect。
3. `blender_rt_cmd get_addon_info` → `addon_version=[1,10]`、`protocol=11`；
   GUI 路线 `curl http://127.0.0.1:9877/doctor` → `kind=ok`（纯无头路线用 `rt_worker(start)` + `see` 出图自证）；
   `blender_rt_plan(op="catalog")` → **34 family / 228 op**。
4. 选中入口：Blender 原生选中 → `N` →「Selection → DSH」→ 发送（多选 / 集合）；编辑模式选点/面、姿态选骨骼也会一起发。
5. 新能力（交互式无头、`img_*`、`asm_*`、`blockout`、`visualreport`、`shapesolve`、`eval_run`、强制门等）用
   `blender_rt_plan(op="catalog")` 查骨架；旧 op 不删除、只增不改。

## 1. Blender 侧 addon：必须换成 `DSH Blender Bridge` v1.10+

v2.3 的原生选中 / 集合 / activity 依赖本仓 addon 的 5 条命令与 N 面板入口，旧 addon 没有。
两个 addon 都默认监听 `127.0.0.1:9876`，**不能同时启用**。

1. `Edit ▸ Preferences ▸ Add-ons` → 停用旧条目（`MCP for Blender` / `blender_mcp` / `blender_mcp_addon`）。
2. `Install...` → 选本仓 [`addon/dsh_blender_bridge.py`](addon/dsh_blender_bridge.py) → 勾选启用 **DSH Blender Bridge**（v1.10 / protocol 11）。
3. 3D 视图按 `N` →「DSH Blender Bridge」→ **Connect**（端口 9876）；或直接 `blender_viewport(op="launch")`。
4. `blender_rt_cmd get_addon_info` → `addon_version` 应为 `[1,10]`、`protocol=11`，capabilities 含
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

## 4. 历史接口契约

`docs/宿主集成-接口契约.md`、`宿主集成-接口契约-v2.1增补.md`、`v2.2增补.md` 与 TUI 适配文档已删除；
当前唯一真源是 [`docs/宿主集成-接口契约-v2.3增补.md`](docs/宿主集成-接口契约-v2.3增补.md) —— 该文件顶部有 **v2.6 兼容声明**
（v2.3 契约仍适用；addon v1.10 / protocol 11；新增 P0–P2 op 见 catalog）。

## 5. addon v1.10 / v2.6 追加（无需迁移动作）

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
