# Security Policy

只对**最新发布版**提供安全修复（当前版本见 `package.json` 的 `version`）。旧版本请先升级。

## 上报漏洞

请**不要**用公开 issue 报安全问题，走 GitHub 的私密通道：

1. <https://github.com/sixtysevenlf/dsh-blender-plugin/security/advisories/new>
   （仓库 → **Security** → **Advisories** → *Report a vulnerability*）
2. 若该通道不可用，可以开一个 issue **只写**「我想上报一个安全问题，请给私密联系方式」，**不要附细节**。

报告请包含：影响版本、复现步骤、影响面（谁能触发、需要什么前置条件）、以及（如有）PoC。
我们会在 7 天内首次回应；修复发布后会在 advisory 里致谢（除非你要求匿名）。

## 本插件的执行面（评估信任时请一并考虑）

这个插件**按设计**就会执行代码并启动进程，不是只读的数据插件：

| 面 | 说明 |
|---|---|
| Blender 侧 Python | `blender_rt_do` / `blender_rt_headless` / `blender_rt_worker` / `blender_rt_plan` 等会把调用方给的 Python 交给 Blender 的 `bpy` 执行；`K` 是跨调用保留的持久内核 |
| 本地进程 | 会 spawn 后端 `node runtime/server.mjs`、无头 `blender -b`、以及 `runtime/ext/` 下的外部校验器（glTF-Validator / open3d）；必要时用 `SIGTERM`→`SIGKILL` 清残留后端 |
| 本地网络 | 后端只监听 `127.0.0.1`（默认 9877）；与 Blender addon 走 `127.0.0.1:9876` 的 TCP。**没有**外呼遥测 |
| 文件读写 | 默认工作目录 `~/.dsh-blender-rt`（可用 `DSH_BLENDER_*` 环境变量或包根 `dsh-blender.config.json` 覆盖）：渲染产物、日志、作业台账 |
| gate 表达式 | `gate_run` 的 `pass_if` / `degrade_if` 是**受限表达式**：先过 `runtime/gate.py` 的 `_check_ast` 语法白名单，再由 `_eval_node` 逐节点求值（**不调用 `eval` / `exec`**）；禁用下划线属性，函数仅限 `min` / `max` / `len` / `abs` / `round` / `all` / `any` / `sum` / `float` / `int` / `str` |
| 凭据 | 插件**不读取、不存储**任何 API key / token / 凭据文件 |

## 第三方依赖 / Blender 侧 addon

- Blender 侧需要一个**第三方 addon**（`MCP for Blender` 血统，或兼容 category-action 协议的实现）。
  它的代码**不在本仓库**，安装前请自行评估其来源与版本。
- 发布包由 `npm pack` 生成，只含 `package.json` 的 `files` 白名单内容；
  构建依赖来自 DSH checkout，运行期依赖由宿主注入（`peerDependencies`）。
- 建议把本仓库加入 Dependabot（见 `.github/dependabot.yml`）。
