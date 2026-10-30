# 为 DSH × Blender 做贡献

[English](CONTRIBUTING.md)

感谢你抽出时间参与。这个项目让 AI 助手能直接、可验证地操作一个真实的 Blender 会话。Issue 和 Pull
Request 中英文都欢迎。

动手之前请先读 [zh-README.md](zh-README.md)（或 [README.md](README.md)）和 [AGENTS.md](AGENTS.md)。
AGENTS.md 说明各层怎么拼在一起、什么算“装好了”，以及命令的唯一事实源在哪里。

## 可以怎么贡献

- **Bug 报告**：附上复现步骤、`package.json` 里的确切版本，以及 `doctor` 端点的返回。
- **文档**：两个 README、`docs/`、ADR，以及 `skills/blender-modeling/` 下的随包 skill。面向用户的
  中文指南保持中文；面向 AI 的文档保持英文。
- **测试**：能复现真实 bug 的失败测试，比没有测试的修复更有价值。
- **代码**：`src/`（TypeScript 宿主侧）、`runtime/`（后端）、`addon/`（Blender 侧）。

## 开 issue 之前

1. 先搜索已有 issue 和 README 的常见故障一节。
2. 在最新版本上复现，不要用旧 tag。
3. **安全问题请走 [SECURITY.md](SECURITY.md)**，不要开公开 issue。

比 bug 修复更大的改动——新工具、协议变更、新依赖——请先开 issue 把方向谈清楚，再投入实现。

## 开发环境

前置条件：

- 开发与测试使用 Node.js **22**；后端在 Node.js 20 及以上可运行。
- Blender **4.2 LTS 或 5.x**（GUI 模式需要随包 addon；无头模式只需要可执行文件）。
- Python **3.11+**，装在带 `pytest` 的虚拟环境（virtualenv）里，用 `PYTHON` 指向它
  （Windows 另设 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`）。
- 可选：只有需要重新构建 `lib/` 时才需要 DSH 源码 checkout。

安装、构建、typecheck、测试、lint、打包命令只在 [AGENTS.md §1](AGENTS.md#1-commands-single-source)
定义一次。请使用那里的命令，不要从更旧的文档里抄变体。正常 clone 已不再携带 `lib/`，本地跑后端前
需要先构建。

Blender 侧：从 release 的 zip 安装 addon，启用并点 **Connect**。只有 `doctor` 返回 `kind=ok`
（GUI 路线），或 worker 加 `see` 能返回一帧图（纯无头路线），才算装好。

## 仓库结构

| 路径 | 是什么 |
|---|---|
| `src/` | 插件宿主侧的 TypeScript 源码，按 tools、host、plan、contracts 拆分 |
| `lib/` | 构建产物——生成物，3.0.0 起不入库，不要手改 |
| `runtime/` | 后端：engine、server、catalog、worker、无头 runner、Python 领域包 |
| `addon/` | Blender addon 包——唯一受支持的 addon |
| `skills/` | 随包的 `blender-modeling` skill，自动注册 |
| `tests/` | 单元、集成、端到端三层；见 [tests/README.md](tests/README.md) |
| `docs/` | 用户指南（中文）、ADR、重构规格与证据包 |

## 测试

分层和各层命令见 [tests/README.md](tests/README.md)。迭代时跑与改动对应的那一层，开 PR 前跑完整的
PR 门禁。大多数测试既不需要 Blender 也不需要 GUI；端到端层需要，且只在打标签和发布候选时跑，不进入
每次 PR。

测试失败是信息。不要用 skip、重试或放宽断言让它变绿；修因，或者报告。flaky 测试要有 issue、owner
和到期日。

## 修改 catalog

`runtime/` 里的 catalog 是 family、op 和计数的唯一事实源。改完后按 AGENTS.md §1 的命令刷新计数和
capability lock，再跑契约测试。在 PR 里说明 lock 为什么变；不要手改 lock 文件。

## Pull Request

- 一个逻辑改动对应一个任务；无关的格式调整不要混进 diff。
- 提交标题用约定式：`feat:`、`fix:`、`docs:`、`test:`、`perf:`、`chore:`。
- 使用 [.github/pull_request_template.md](.github/pull_request_template.md)：范围、行为矩阵行、测试
  证据、风险、回滚、披露、检查清单。
- [README.md](README.md) 与 [zh-README.md](zh-README.md) 要同步，
  [CONTRIBUTING.md](CONTRIBUTING.md) 与 [zh-CONTRIBUTING.md](zh-CONTRIBUTING.md) 同理；它们是同一份
  文档的两个版本，不是各自独立的页面。
- 在 [CHANGELOG.md](CHANGELOG.md) 的下一个版本标题下更新；有破坏性变更还要更新
  [MIGRATIONS.md](MIGRATIONS.md)。
- 决策难以撤回时，在 [docs/adr/](docs/adr/README.md) 增加 ADR。
- AI 协助要在 commit trailer 和 PR 两处披露；见 AGENTS.md §6。
- 不要提交 `node_modules`、虚拟环境、`__pycache__`、`.tgz` 产物或依赖软链。控制评审体积：单个 PR
  目标不超过 400 行改动。

## 发布流程

发布流程由计划的 T16、[ADR 0003](docs/adr/0003-addon-package-release.md) 与
[ADR 0004](docs/adr/0004-lib-build-release.md) 负责。简言之：先核对
[docs/refactor/spec.md](docs/refactor/spec.md) 里的门禁，同一次改动里更新 `package.json` 与
`src/index.ts` 的 `PLUGIN_VERSION`，由 `prepack` 构建 `lib/`，把 addon zip 与 npm tarball 挂到
release，并在打 tag 前演练回滚。详细清单在发布证据目录里。

## 许可

项目使用 [BSD-3-Clause](LICENSE)。提交贡献即表示你同意以相同条款授权。请保持尊重、假定善意；设计
分歧没问题，人身攻击不行。
