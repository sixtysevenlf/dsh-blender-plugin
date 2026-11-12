# 安全策略

[English](../../SECURITY.md)

只对**最新发布版**提供安全修复（当前版本见 `package.json` 的 `version`）。旧版本请先升级。

## 上报漏洞

请**不要**用公开 issue 报安全问题，走 GitHub 的私密通道：

1. <https://github.com/sixtysevenlf/dsh-blender-plugin/security/advisories/new>
   （仓库 -> **Security** -> **Advisories** -> *Report a vulnerability*）。
2. 若该通道不可用，可以开一个 issue **只写**「我想上报一个安全问题，请给私密联系方式」，
   **不要附细节**。

报告请包含：影响版本、复现步骤、影响面（谁能触发、需要什么前置条件），以及（如有）PoC。
我们会在 7 天内首次回应；修复发布后会在 advisory 里致谢（除非你要求匿名）。

## 本插件的执行面（评估信任时请一并考虑）

这个插件**按设计**就会执行代码并启动进程，不是只读的数据插件：

| 面 | 说明 |
|---|---|
| Blender 侧 Python | `blender_py`（GUI 会话）与 `blender_run(kind="headless")` 会把调用方给的 Python 交给 Blender 的 `bpy` 执行；`K` 是 GUI 会话内跨调用保留的持久内核 |
| 本地进程 | 会 spawn 后端 `node lib/runtime/gateway.js`（先 `npm run build`）、无头 `blender -b`、`runtime/ext/` 下的外部校验器（glTF-Validator / open3d），以及可选的 vision Python worker / 一次性 probe（仅 `features.dnn` 开启时；worker 用 stdin/stdout，不监听端口）；必要时用 `SIGTERM` -> `SIGKILL` 清残留后端 |
| 本地网络 | Gateway 只监听 `127.0.0.1`（默认 39877）；Blender addon 主动连接 Gateway 的 WebSocket，不开放入站端口。除这条本地控制通道外，插件**没有**遥测 |
| 文件读写 | 持久数据固定在 home：`DSH_BLENDER_HOME` → `$DSH_HOME/dsh-blender` → `~/.dsh/dsh-blender`（见 `docs/配置参考.md` §2.2），包含配置、作业台账、日志、渲染缓存与 vision venv/模型；旧 `~/.dsh-blender-rt` 等目录只读迁移、不删除。临时产物仍可用 `workDir` / 已弃用的 `DSH_BLENDER_WORKDIR` 覆盖 |
| gate 表达式 | `gate_run` 的 `pass_if` / `degrade_if` 是**受限表达式**：先过 `runtime/py/core/gate.py` 的 `_check_ast` 语法白名单，再由 `_eval_node` 逐节点求值（**不调用 `eval` / `exec`**）；禁用下划线属性，函数仅限 `min` / `max` / `len` / `abs` / `round` / `all` / `any` / `sum` / `float` / `int` / `str` |
| 凭据 | 宿主插件**不读取、不存储、不传输**任何 API key / token / 凭据文件。随包 addon 提供可选的资产集成凭据（Hyper3D、Sketchfab、Poly Pizza、Hunyuan3D），以密码字段形式保存在 Blender 的 add-on preferences 中；也可通过 `DSHBB_*` 环境变量提供（旧的 `BLENDERMCP_*` 名称仍兼容），且只发送给你主动调用的对应第三方 API |
| 遥测 | 没有。随包 addon 不含遥测、统计、同意偏好或人工编辑事件缓冲；活动状态只在内存中计算，不落盘也不外发 |

## 第三方依赖 / Blender 侧 addon

- Blender 侧需要本仓附带的 addon（`DSH Blender Bridge` v2.0.0 / protocol 12，以 `extension/dsh_blender_bridge.zip`
  分发，源码在 `extension/dsh_blender_bridge/`）。旧 `MCP for Blender` / category-action 实现**不在本仓库**，
  自 v2.3 起不再支持；不要为它安装或授予信任。
- 发布包由 `npm pack` 生成，只含 `package.json` 的 `files` 白名单内容；
  构建依赖来自 DSH checkout，运行期依赖由宿主注入（`peerDependencies`）。
- 建议把本仓库加入 Dependabot（见 `.github/dependabot.yml`）。
