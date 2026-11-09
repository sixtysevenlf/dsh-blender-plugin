# 为 DSH × Blender 做贡献

[English](../../CONTRIBUTING.md)

感谢你抽出时间参与。这个项目让 AI 助手能直接、可验证地操作一个真实的 Blender 会话。Issue 和 Pull
Request 中英文都欢迎。

动手之前请先读 [中文 README](./README.md)（或 [English README](../../README.md)）和 [AGENTS.md](../../AGENTS.md)。
AGENTS.md 说明各层怎么拼在一起、什么算“装好了”，以及命令的唯一事实源在哪里。

## 可以怎么贡献

- **Bug 报告**：附上复现步骤、`package.json` 里的确切版本，以及 `doctor` 端点的返回。
- **文档**：两个 README、`docs/`、架构说明，以及 `skills/blender-modeling/` 下的随包 skill。面向用户的
  中文指南保持中文；面向 AI 的文档保持英文。
- **测试**：能复现真实 bug 的失败测试，比没有测试的修复更有价值。
- **代码**：`src/`（TypeScript 宿主侧）、`runtime/`（后端）、`extension/`（Blender 侧）。

## 开 issue 之前

1. 先搜索已有 issue 和 README 的常见故障一节。
2. 在最新版本上复现，不要用旧 tag。
3. **安全问题请走 [SECURITY.md](SECURITY.md)**，不要开公开 issue。

比 bug 修复更大的改动——新工具、协议变更、新依赖——请先开 issue 把方向谈清楚，再投入实现。

## 开发环境

前置条件：

- 开发与测试使用 Node.js **24.9** 或更高版本；CI 使用 Node 24。
- Blender **4.2 LTS**（已验证）；Blender 5.x 尚未验证（GUI 模式需要随包 addon；无头模式只需要可执行文件）。
- Python **3.11+**；`npm run setup:dev` 会创建 `.venv` 并装好 Python 测试依赖。测试会自动
  探测 `.venv`；只有需要换解释器时才把 `PYTHON` 指向别处（Windows 另设
  `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`）。
- 可选：只有需要重新构建 `lib/` 时才需要 DSH 源码 checkout。

克隆后用一条命令配好开发环境：

    git clone https://github.com/sixtysevenlf/dsh-blender-plugin.git
    cd dsh-blender-plugin
    npm run setup:dev

`npm run setup:dev` 按 `PYTHON` → `python` → `python3` →（Windows）`py -3` 的顺序找 Python 3.11+
解释器，创建或复用 `.venv`，安装 `requirements-dev.txt`、`requirements-typecheck.txt` 和 npm
依赖（仅当 `node_modules` 缺失时才跑 `npm ci --legacy-peer-deps`）。脚本幂等：
`npm run setup:dev -- --check` 只验证不写入，`npm run setup:dev -- --force` 重建 `.venv`。脚本
结束时会打印 venv 的 `PYTHON` 路径。`.venv` 与 `node_modules` 都不入库。

构建、typecheck、测试、lint、打包命令只在 [AGENTS.md §1](../../AGENTS.md#1-commands-single-source)
定义一次。请使用那里的命令，不要从更旧的文档里抄变体。正常 clone 已不再携带 `lib/`，本地跑后端前
需要先构建。

### 编辑器与 TypeScript 工程配置

编辑器只认文件名恰好是 `tsconfig.json` 的配置，所以需要在编辑器里做类型检查的代码范围，都要在源码旁放
一个：仓库根的 `tsconfig.json` 覆盖 `src/`，`runtime/tsconfig.json` 是只 `extends` 构建配置
`tsconfig.runtime.json` 的两行适配文件。构建和 CI 从不读这个适配文件：`npm run typecheck` 和
`scripts/build.mjs` 都显式传构建配置。新增只给编辑器用的配置时，保持它是纯 `extends`，不要加入构建
配置没有的选项。

Blender 侧：从 release 的 zip 安装 addon，启用并点 **Connect**。只有 `doctor` 返回 `kind=ok`
（GUI 路线），或 worker 加 `see` 能返回一帧图（纯无头路线），才算装好。

## 仓库结构

| 路径 | 是什么 |
|---|---|
| `src/` | 插件宿主侧的 TypeScript 源码，按 tools、host、plan、contracts 拆分 |
| `lib/` | 构建产物——生成物，3.0.0 起不入库，不要手改 |
| `runtime/` | 后端：engine、server、catalog、worker、无头 runner、Python 领域包 |
| `extension/` | Blender addon 包——唯一受支持的 addon |
| `skills/` | 随包的 `blender-modeling` skill，自动注册 |
| `tests/` | 单元、集成、端到端三层；见 [tests/README.md](../../tests/README.md) |
| `docs/` | 用户指南（中文）、架构说明、重构规格与证据包 |

## 测试

分层和各层命令见 [tests/README.md](../../tests/README.md)。迭代时跑与改动对应的那一层，开 PR 前跑完整的
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
- 使用 [.github/PULL_REQUEST_TEMPLATE/](../../.github/PULL_REQUEST_TEMPLATE/) 中的对应模板（`bug_fix`、`feature`、`docs_update`、`refactor`）：范围、行为矩阵行、测试
  证据、风险、回滚、披露、检查清单。
- PR 完整修复某个 issue 时，在描述里写 `Fixes #123`（每个 issue 一个关键词）；PR 合并进默认分支（`main`）后 GitHub 自动关闭。只修复一部分或仅关联用 `Refs #123`。关键词放在 PR 描述正文，不要放在评论或代码块里。
- [README.md](../../README.md) 与 [中文 README](./README.md) 要同步，
  [CONTRIBUTING.md](../../CONTRIBUTING.md) 与 [中文 CONTRIBUTING](./CONTRIBUTING.md) 同理；它们是同一份
  文档的两个版本，不是各自独立的页面。
- 在 [CHANGELOG.md](../../CHANGELOG.md) 的下一个版本标题下更新；有破坏性变更还要更新
  [MIGRATIONS.md](../../MIGRATIONS.md)。
- 修改边界或契约前先读 [docs/architecture.md](../../docs/architecture.md)，并让架构文档与代码同步。
- AI 协助要在 commit trailer 和 PR 两处披露；见 AGENTS.md §6。
- 不要提交 `node_modules`、虚拟环境、`__pycache__`、`.tgz` 产物或依赖软链。控制评审体积：单个 PR
  目标不超过 400 行改动。

## 发布流程

发布流程由发布清单与 [docs/architecture.md](../../docs/architecture.md) 负责。简言之：先核对发布门禁，同一次改动里更新
`package.json` 与 `src/constants.ts` 的 `PLUGIN_VERSION`，由 `prepack` 构建 `lib/`，把 extension zip 与
npm tarball 挂到 release，并在打 tag 前演练回滚。详细清单保存在本地重构工作区，不随仓库发布。

### 手动 CD

[.github/workflows/release.yml](../../.github/workflows/release.yml) 是手动发布流水线，仅支持
`workflow_dispatch`：**Actions → Manual CD → Run workflow**。输入：

- `version` 必须与 `package.json`、`src/constants.ts` 的 `PLUGIN_VERSION` 一致；
- `dry_run` 默认 `true`，只验证和打包；
- `dry_run=false` 在 `release` environment 审批后创建 **draft** GitHub Release 与 tag；
- `publish_npm` 默认 `false`；开启后把打包好的 tarball 带 provenance 发布到 npm。

流水线不会自行 push `main` 或 tag；`workflow_dispatch` 只有该文件在默认分支后才可见，因此请在合并到 `main` 后运行（dry-run 阶段也可在包含该 workflow 的分支上运行）。真 Blender E2E 与 GitHub Release 的最终发布仍是人工步骤。为 `release`
environment 配置 required reviewers，保护 `v*` tag。npm 发布使用 trusted publishing（OIDC），不需要在 environment 中保存 `NPM_TOKEN`。

## 许可

宿主插件与仓库代码使用 [BSD-3-Clause](../../LICENSE)。随包的 Blender 扩展位于 [`extension/`](../../extension/)，
按 GPL-3.0-or-later 分发（见 [extension/LICENSE](../../extension/LICENSE)）；它是 MIT 上游的衍生版本，
归属声明见 [extension/NOTICE](../../extension/NOTICE)。提交贡献即表示你同意按所修改组件的许可证授权。
请保持尊重、假定善意；设计分歧没问题，人身攻击不行。