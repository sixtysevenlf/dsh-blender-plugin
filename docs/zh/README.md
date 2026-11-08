# DSH × Blender 直连实时插件

[English](../../README.md)

DSH × Blender 把 DeepSeek Harness 接到 Blender，让 AI 能在真实 Blender 会话里建模、检查、验收和导出。默认走 GUI：先打开 Blender、连接 addon，AI 对着你正在看的场景做“看一眼 → 改一步 → 再确认”。没有 GUI 时，后台无头作业（`blender_run(kind="headless")`）仍能执行脚本并返回渲染图。

## 目录

- [功能](#功能)
- [快速开始](#快速开始)
- [工具](#工具)
- [文档](#文档)
- [常见问题](#常见问题)
- [开发](#开发)
- [支持](#支持)
- [许可](#许可)

## 功能

- **GUI 优先的交互式建模。** AI 直接操作你面前的 Blender 会话：查看场景、在持久内核里跑 Python、截取视口或自定义角度、一步一步改并确认。
- **无头作业，但仍有视觉反馈。** 没有 GUI 时，AI 提交后台无头作业（`blender_run(kind="headless")`），从中收集脚本日志并取回渲染图。逐步交互需要 GUI 会话；作业通路用于长活或全新场景，保证没人盯着也能干完。
- **参考图量测。** 建模前量轮廓、孔、对称、可读特征和比例；没有标定时返回像素口径，不猜毫米。
- **参数化曲面。** 用截面放样 / 沿路径扫掠 / 四边补片生成可复现的曲面网格，跑连续性、曲率、自交、厚度体检，再导出 GLB；同一份规格得到同一个 `spec_hash` 与同一批顶点，不靠手工捏。
- **装配审计与修复。** 校验装配清单，分类穿模、允许的隐藏交叠、接触、间隙、浮空和完全包含，做安全修复，过门后导出 GLB / FBX / OBJ，并生成 manifest 与合并账本。
- **白模与视频参考包。** 用声明式零件搭低模，渲染正交/透视参考视图，输出转台 MP4，供 Seedance 这类视频模型使用。
- **视觉验收与交付门。** 同机位对比渲染与参考图，输出带裁剪和量测的缺陷报告，跑缺陷注入评估；门不是 pass 就拦住交付。
- **自动化与长任务。** `blender_run` 提交渲染或无头作业后立即返回 job id；参数搜索与批量校验在 GUI 会话里通过 `blender_op` 执行。两者都不会全程占用 GUI。
- **原生选中。** 在 Blender 里选中物体或集合，一键送进当前 DSH 草稿。
- **dsh-TUI 里也能用。** 在 dsh-TUI 里，Blender 选中会以上下文 chip 的形式挂到下一条消息上；页脚显示 Blender 通道状态（连接 / AI 暂停 / 渲染 / 作业）；`@` 可以提及场景里的对象、材质和集合；`/blender status|doctor|jobs` 不经过模型直接查状态。这些都要 dsh-TUI 提供对应接缝，没有就自动不启用。

## 快速开始

**先做这一步：安装随包 addon。** 用本仓库提供的 **extension/dsh_blender_bridge.zip**，它必须是你唯一的 Blender 侧 agent 桥：不要安装或启用 `MCP for Blender`、`blender_mcp` 或任何自带 socket 服务的同类 addon（旧 MCP 桥仍监听 127.0.0.1:9876；3.1 本仓 addon 只主动外连，不占入站端口，但两个 Blender 侧 agent 桥会互相干扰）。无关的普通 addon（建模、材质、渲染、资产库）不受影响。请先在 Blender 里安装并启用它，再做后面的步骤。如果按下面安装后仍然用不了，先回来检查是不是这个 addon、有没有启用、有没有 Connect。

### 前置条件

- 已安装并可用的 DSH（DeepSeek Harness）。
- 从 npm 安装时不需要单独安装 Node.js；只有从源码构建才需要 Node.js **24.9** 或更高版本。Node.js **24.9** 是最低支持版本，CI 使用 Node 24。
- Blender **4.2 LTS** 是已验证版本；4.x 可能可用，5.x 尚未验证。GUI 模式必须先安装随包 addon；无头模式只需要能找到 Blender 可执行文件。
- addon 兼容性：随包 addon 是唯一需要、也是唯一支持的 agent 桥。其他 addon 与之不冲突；旧的 `MCP for Blender` / `blender_mcp` 不受支持（仍可能占用 127.0.0.1:9876），停用即可，不必卸载。
- 可选：如果你想重新构建插件，需要 DSH 源码 checkout。

### 方案 A：从 npm 安装

这是支持的安装方式，不需要 `git clone`、`npm ci` 或 `npm run build`。

1. **先安装随包 addon。** 从 [最新 GitHub Release](https://github.com/sixtysevenlf/dsh-blender-plugin/releases/latest) 下载 `dsh_blender_bridge.zip`；在 Blender 中打开 `Edit > Preferences > Extensions > Install from Disk...`，选择 zip，启用 **DSH Blender Bridge**，在 3D 视图按 `N` 打开面板，点 **Connect**。

2. **从 npm 安装 DSH 插件。** 按你使用的 DSH 宿主选择：

   - **DSH Web / DSH TUI：** 使用你实际运行的 profile：

         dsh plugin --profile <profile> add dsh-blender-plugin@3.1.0

     装完后按第 3 步重启 DSH。

   - **DSH Desktop：** 打开 Plugins 页面，安装 bundle `dsh-blender-plugin@3.1.0`。DSH Desktop 通过 Plugins 页面安装后不需要重启 DSH。

3. **重启 DSH（仅 DSH Web / DSH TUI）。** 从 DSH Desktop 的 Plugins 页面安装的用户跳过这一步。

4. **验证安装。** GUI 路线的唯一验收判据是 `doctor` 返回 `kind` 等于 `ok`：

        curl -sS http://127.0.0.1:39877/doctor

   GUI 路线：`curl -sS http://127.0.0.1:39877/health` 和 `/doctor`，`doctor.kind` 必须是 `ok`。

   纯无头路线（不装 addon）：`blender_run(action="submit", kind="headless", script="print('ok')")` 后接 `wait`/`collect`，必须返回作业及其产物。

addon 仍然是唯一支持的 Blender 侧 agent 桥。其他 Blender 侧 agent 桥要停用（旧 MCP 桥仍占 9876；3.1 addon 不占入站端口）；普通建模、材质、渲染、资产类 addon 不受影响。

### 方案 B：让 AI 帮你装

把下面这段提示词交给能执行命令的 AI：

    请先安装并启用最新 GitHub Release 提供的 addon：dsh_blender_bridge.zip。
    如果已有桥接类 addon（MCP for Blender / blender_mcp，或任何 Blender 侧桥接服务
    提供服务的 addon），先停用它；不要卸载用户的其他 addon，也不要要求用户这么做。
    然后从 npm 安装 DSH × Blender 插件到我的 DSH profile：
    dsh plugin --profile <profile> add dsh-blender-plugin@3.1.0
    先读 docs/AI-安装提示词.md 并严格照做；
    最后必须验收：GUI 路线 doctor 返回 kind=ok，
    纯无头路线用 blender_run 的 headless 提交 + wait 能返回产物。
    不要跳过验收，也不要在没验收时声称安装成功。

把完整安装提示词拉到本地：

    curl -fsSL https://raw.githubusercontent.com/sixtysevenlf/dsh-blender-plugin/main/docs/AI-安装提示词.md -o AI-安装提示词.md

安装独立的建模 skill（只给不加载本插件的宿主用；DSH 会随插件自动注册，不需要这一步）：

    curl -fsSL https://raw.githubusercontent.com/sixtysevenlf/dsh-blender-plugin/main/skills/blender-modeling/install.sh | bash

### 方案 C：从源码构建（开发者）

1. **先安装随包 addon。**

   从本仓库的 extension/ 目录下载 **extension/dsh_blender_bridge.zip**。打开 Blender，进入 Edit > Preferences > Extensions > Install from Disk...，选择该 zip，启用 DSH Blender Bridge；在 3D 视图按 N 打开面板，点 Connect。addon 会在 Connect 后主动连接 DSH Gateway（默认 127.0.0.1:39877），不监听端口；升级/替换 addon 后必须重启 Blender（无热重载，旧 3.0.x addon 的 9876 listener 不会自动释放）。如果之前装过单文件 `dsh_blender_bridge.py`，请先停用并移除：3.1 只通过 Blender Extensions 安装这个扩展包。

   只能用这个 addon 作为 agent 桥。若已启用 `MCP for Blender` / `blender_mcp`（或其他 Blender 侧 agent 桥），停用它即可，不必卸载；其他 addon 可以留着。如果后面的安装用不了，先回来检查这一步。

2. **开发者路径：** 克隆仓库并构建。lib/ 是构建产物，不再入库，所以新 clone 必须先构建一次。`npm run setup:dev` 会装好 npm 与 Python 开发依赖（并创建 `.venv`）；除非你在开发这个插件，否则请使用上面的 npm 安装。

        git clone https://github.com/sixtysevenlf/dsh-blender-plugin.git
        cd dsh-blender-plugin
        npm run setup:dev
        npm run build

3. 把插件加入 DSH profile。先把 checkout 软链到 profile，再在 profile 的 package.json 里加依赖和 bundle 条目。

        ln -s /absolute/path/to/dsh-blender-plugin <profile>/node_modules/dsh-blender-plugin

    profile 的 package.json：

        {
          "dependencies": {
            "dsh-blender-plugin": "link:/absolute/path/to/dsh-blender-plugin"
          },
          "dsh": {
            "profile": {
              "bundles": ["dsh-blender-plugin"]
            }
          }
        }

    校验 profile，然后重启 DSH：

        dsh --profile <profile> --dump-config

4. 验收。

    GUI 路线：

        curl -sS http://127.0.0.1:39877/health
        curl -sS http://127.0.0.1:39877/doctor

    GUI 路线唯一验收判据：doctor 返回 kind=ok。没到就是没装好。

    Blender 与 Gateway 由宿主启动：请自行打开 Blender、启用 addon 并点 Connect；3.1 没有模型侧的启动工具。

    纯无头路线（不装 addon）：

        blender_run(action="submit", kind="headless", script="print('ok')")
        blender_run(action="wait", id="<jobId>")

    作业返回且产物里带脚本日志，就说明无头通路正常。此时 doctor 的 bpy 往返不可达是预期现象。

## 工具

插件提供 6 个模型工具；plan、审计、装配、导出、评估等操作都收在 `blender_op`（34 family / 228 op）里，所以工具面保持很小。

| 工具 | 它能给你什么 |
|---|---|
| blender_session | GUI 会话生命周期与归属：list/status/doctor、attach、claim/release、写作用域 grant/revoke、暂停状态、presence，以及 `action=txn` 快照/标记回滚。Blender 与 Gateway 由宿主管理。 |
| blender_op | 能力目录（`op="catalog"`，34 family / 228 op）与执行入口，覆盖建模、审计、装配、交付、评估；另有 `action=loop|opt|preset`。执行 op 需要 ready 的 GUI 会话。 |
| blender_py | 在已 attach 的 GUI 会话里执行 Python，内核跨调用保留；`see=true` 顺带回一帧。仅 GUI，没有无头回退。 |
| blender_see | 仅用于 GUI 取帧：`viewpoint=viewport|camera|custom`、`mode=frame|sequence`，固定宽高比分档。相机动画与批量渲染走 `blender_run(kind="render")`。 |
| blender_run | 唯一作业入口：`action=submit` 配 `kind=render|headless` 立即返回 job id；`status/wait/collect/cancel/list` 管理作业；`action=tune` 管渲染性能。 |
| blender_inspect | 列出并执行 addon 的可执行只读命令（`command=` + `args=`）；已删除的写命令返回 `wrong_tool` 提示。 |

3.0 的旧工具名不再注册；迁移表见 [MIGRATIONS.md](../../MIGRATIONS.md) §0.3.

## 虚拟显示（virtual-gui）

Windows 与 Linux 上 Gateway 可以为 agent 拉起一个隔离的 Blender GUI。Windows 在 V0 验收后默认 `auto`；Linux 是需要显式开启的 preview。缺依赖时绝不静默启动：`blender_session start kind=virtual-gui` 返回 `stage=config` 和确切安装命令，`/blender doctor --fix`（或 addon 偏好设置按钮）只执行同一份安装计划。一键安装仅限 Ubuntu 22.04/24.04 与 Debian 12 的 `xvfb`（走 `pkexec`）；VirtualGL 必须来自官方仓库，GPU 驱动只检测不安装，不支持的平台给手动命令。详见 [配置参考 §5.7](../配置参考.md)。

## 文档

- 曲面算法：[skills/blender-modeling/references/曲面算法.md](../../skills/blender-modeling/references/曲面算法.md)
- 操作教程：[docs/操作教程.md](../操作教程.md)
- 配置参考：[docs/配置参考.md](../配置参考.md)
- AI 安装提示词：[docs/AI-安装提示词.md](../AI-安装提示词.md)
- 部署 SOP：[docs/部署SOP.md](../部署SOP.md)
- 第三方安装清单：[docs/给第三方的安装清单.md](../给第三方的安装清单.md)
- DSH 升级适配：[docs/DSH-更新适配.md](../DSH-更新适配.md)
- 架构说明：[docs/architecture.md](../architecture.md)
- 建模 skill：[skills/blender-modeling/](../../skills/blender-modeling/)
- 版本说明：[GitHub Releases](https://github.com/sixtysevenlf/dsh-blender-plugin/releases) 与 [CHANGELOG.md](../../CHANGELOG.md)

## 常见问题

| 现象 | 处理 |
|---|---|
| doctor 没回 kind=ok | 检查 Blender 是否在跑、addon 是否 Connect。GUI 路线只认这一条。 |
| 没有 GUI | 逐步交互需要 GUI 会话。用 `blender_run(kind="headless")` 提交后台作业，再用 `action="wait"`/`collect` 收取；渲染图从作业产物里取。 |
| 另一个会话正在写同一个场景 | 用 `blender_session(action="grant", scopes=...)` 声明写作用域，或 attach 另一个 GUI 会话；并行作业走 `blender_run` 即可。 |
| 还在启用旧 MCP for Blender / blender_mcp | 停用它（不必卸载），改用随包的 DSH Blender Bridge addon；3.1 桥主动外连不占端口，但两个 agent 桥会互相干扰；旧 MCP 桥仍监听 127.0.0.1:9876。 |
| 图生成了但没附上 | 升级到最新构建（已包含 Windows 路径修复）；仍不行就直接打开返回的路径。 |
| 按下面步骤装完还是用不了 | 先检查 addon：只支持 extension/dsh_blender_bridge.zip，并且必须启用、必须 Connect。 |

## 开发

- Node.js **24.9** 是最低支持版本；CI 使用 Node 24。
- 开发环境：克隆后运行 `npm run setup:dev`，它会探测 Python 3.11+、创建或复用 `.venv`，安装
  `requirements-dev.txt`、`requirements-typecheck.txt` 与 npm 依赖（仅当 `node_modules` 缺失时才跑
  `npm ci --legacy-peer-deps`）。`npm run setup:dev -- --check` 只验证不写入；
  `npm run setup:dev -- --force` 重建 `.venv`。`.venv` 与 `node_modules` 都不入库。
- Python 测试需要装有 `pytest` 的虚拟环境（virtualenv，由 `npm run setup:dev` 创建）。测试会自动探测
  `.venv`；需要换解释器时用 `PYTHON` 指向别处（Windows 另设 `PYTHONIOENCODING=utf-8`、
  `PYTHONUTF8=1`）；测试直接导入 TypeScript 源码。
- 命令的唯一事实源（构建、typecheck、测试、lint、打包）见
  [`AGENTS.md` §1](../../AGENTS.md#1-commands-single-source)；`lib/` 由 `src/` 重新构建，不要手改。
- 测试分层、fixture 与各层命令：[`tests/README.md`](../../tests/README.md)。
- 贡献流程：[English CONTRIBUTING](../../CONTRIBUTING.md) · [中文 CONTRIBUTING](./CONTRIBUTING.md)。
- 随包 skill 真源：`skills/blender-modeling/`。

## 支持

如果这个项目对你有帮助，欢迎在爱发电支持开发：<https://afdian.com/a/sixtysevenlf>

## 许可

DSH 宿主插件本体为 BSD-3-Clause（见仓库根 [LICENSE](../../LICENSE)）。随包的 Blender 扩展位于
[`extension/`](../../extension/)，按 GPL-3.0-or-later 分发（见 [extension/LICENSE](../../extension/LICENSE)），
并包含源自 [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) 的 MIT 代码；其版权与 MIT 声明保留在
[extension/NOTICE](../../extension/NOTICE)。随包建模 skill 为 MIT；见 skills/blender-modeling/LICENSE 与 NOTICE.md。
Blender 是 Blender Foundation 的商标。