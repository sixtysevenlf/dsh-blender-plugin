# DSH × Blender 直连实时插件

[English](README.md)

DSH × Blender 把 DeepSeek Harness 接到 Blender，让 AI 能在真实 Blender 会话里建模、检查、验收和导出。默认走 GUI：先打开 Blender、连接 addon，AI 对着你正在看的场景做“看一眼 → 改一步 → 再确认”。没有 GUI 时，持久无头会话提供同样的交互闭环，也能出图。

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
- **无头备用，但仍有视觉反馈。** 没有 GUI 时启动持久 worker，AI 仍然能执行代码并拿到 Workbench 画面。它是备用路径，不是默认路径；目的是让 AI 在没有窗口时也不瞎。
- **参考图量测。** 建模前量轮廓、孔、对称、可读特征和比例；没有标定时返回像素口径，不猜毫米。
- **参数化曲面。** 用截面放样 / 沿路径扫掠 / 四边补片生成可复现的曲面网格，跑连续性、曲率、自交、厚度体检，再导出 GLB；同一份规格得到同一个 `spec_hash` 与同一批顶点，不靠手工捏。
- **装配审计与修复。** 校验装配清单，分类穿模、允许的隐藏交叠、接触、间隙、浮空和完全包含，做安全修复，过门后导出 GLB / FBX / OBJ，并生成 manifest 与合并账本。
- **白模与视频参考包。** 用声明式零件搭低模，渲染正交/透视参考视图，输出转台 MP4，供 Seedance 这类视频模型使用。
- **视觉验收与交付门。** 同机位对比渲染与参考图，输出带裁剪和量测的缺陷报告，跑缺陷注入评估；门不是 pass 就拦住交付。
- **自动化与长任务。** 一次性无头脚本、后台长任务、参数搜索、批量校验，都不占用 GUI。
- **原生选中。** 在 Blender 里选中物体或集合，一键送进当前 DSH 草稿。
- **dsh-TUI 里也能用。** 在 dsh-TUI 里，Blender 选中会以上下文 chip 的形式挂到下一条消息上；页脚显示 Blender 通道状态（连接 / AI 暂停 / 渲染 / 作业）；`@` 可以提及场景里的对象、材质和集合；`/blender status|doctor|jobs` 不经过模型直接查状态。这些都要 dsh-TUI 提供对应接缝，没有就自动不启用。

## 快速开始

**先做这一步：安装随包 addon。** 只能用本仓库提供的 **addon/dsh_blender_bridge.py**，不要安装 MCP for Blender 或任何其他 Blender addon。请先在 Blender 里安装并启用它，再做后面的步骤。如果按下面安装后仍然用不了，先回来检查是不是这个 addon、有没有启用、有没有 Connect。

### 前置条件

- 已安装并可用的 DSH（DeepSeek Harness）。
- Node.js 20 或更高版本（后端运行环境）。
- Blender 4.x 或 5.x。GUI 模式必须先安装随包 addon；无头模式只需要能找到 Blender 可执行文件。
- 可选：如果你想重新构建插件，需要 DSH 源码 checkout。

### 方案 A：让 AI 帮你装

把下面这段提示词交给能执行命令的 AI：

    请先安装并启用本仓库提供的 addon：addon/dsh_blender_bridge.py；
    不要使用 MCP for Blender 或其他 Blender addon。
    然后从 https://github.com/sixtysevenlf/dsh-blender-plugin
    安装 DSH × Blender 插件到我的 DSH profile。
    先读 docs/AI-安装提示词.md 并严格照做；
    最后必须验收：GUI 路线 doctor 返回 kind=ok，
    纯无头路线用 worker start + see 能返回一帧图。
    不要跳过验收，也不要在没验收时声称安装成功。

把完整安装提示词拉到本地：

    curl -fsSL https://raw.githubusercontent.com/sixtysevenlf/dsh-blender-plugin/main/docs/AI-安装提示词.md -o AI-安装提示词.md

安装独立的建模 skill（只给不加载本插件的宿主用；DSH 会随插件自动注册，不需要这一步）：

    curl -fsSL https://raw.githubusercontent.com/sixtysevenlf/dsh-blender-plugin/main/skills/blender-modeling/install.sh | bash

### 方案 B：手动安装

1. **先安装随包 addon。**

   从本仓库的 addon/ 目录下载 **addon/dsh_blender_bridge.py**。打开 Blender，进入 Edit > Preferences > Add-ons > Install，选择该文件，启用 DSH Blender Bridge；在 3D 视图按 N 打开面板，点 Connect。addon 监听 127.0.0.1:9876。

   只能用这个 addon。继续之前请停用 MCP for Blender 或其他 Blender addon。如果后面的安装用不了，先回来检查这一步。

2. 克隆仓库；一般不需要构建。仓库已提交构建后的 lib 目录。

        git clone https://github.com/sixtysevenlf/dsh-blender-plugin.git
        cd dsh-blender-plugin
        # 可选：只有改了 src/ 才需要
        DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh

3. 把插件加入 DSH profile。先把 checkout 软链到 profile，再在 profile 的 package.json 里加依赖和 bundle 条目。

        ln -s /absolute/path/to/dsh-blender-plugin <profile>/node_modules/@dsh-external/dsh-blender-plugin

    profile 的 package.json：

        {
          "dependencies": {
            "@dsh-external/dsh-blender-plugin": "link:/absolute/path/to/dsh-blender-plugin"
          },
          "dsh": {
            "profile": {
              "bundles": ["@dsh-external/dsh-blender-plugin"]
            }
          }
        }

    校验 profile，然后重启 DSH：

        dsh --profile <profile> --dump-config

4. 验收。

    GUI 路线：

        curl -sS http://127.0.0.1:9877/health
        curl -sS http://127.0.0.1:9877/doctor

    GUI 路线唯一验收判据：doctor 返回 kind=ok。没到就是没装好。

    也可以等 DSH 重启后，调用 blender_viewport 的 launch 操作：它会自动拉起 Blender、启用 addon 并 Connect。

    纯无头路线（不装 addon）：

        blender_rt_worker(op="start", name="smoke")
        blender_rt_worker(op="see", name="smoke")

    能返回一帧图就说明无头备用通路正常。此时 doctor 的 bpy 往返不可达是预期现象。

## 工具

插件提供 15 个模型工具。plan、审计、装配、导出、评估等操作都收在 blender_rt_plan 里，所以工具列表不会膨胀。

### 交互与控制

| 工具 | 它能给你什么 |
|---|---|
| blender_rt_see | 当前 GUI 视口的一帧，或指定相机角度的一帧。 |
| blender_rt_do | 在 Blender 里执行 Python，内核跨调用保留；可顺带回一帧。 |
| blender_rt_watch | 采样时间窗或帧区间，判断画面有没有变化。 |
| blender_rt_worker | 没有 GUI 时使用的持久无头交互会话。 |

### 规划与运维

| 工具 | 它能给你什么 |
|---|---|
| blender_rt_plan | 能力目录 + 执行入口，覆盖建模、审计、装配、交付、评估操作。不知道用什么时先查它。 |
| blender_viewport | 通道健康、doctor、启动 Blender、写租约，用于协调多个会话。 |

### 自动化与长任务

| 工具 | 它能给你什么 |
|---|---|
| blender_rt_headless | 独立 Blender 后台进程，用于批处理、校验或渲染。 |
| blender_rt_job | 后台长任务，按 job id 收集结果；超时不丢结果。 |
| blender_rt_loop | 在 Blender 内跑参数搜索，返回最佳候选。 |

### 场景工具

| 工具 | 它能给你什么 |
|---|---|
| blender_rt_txn | 文件级快照和对象级标记，用于回滚。 |
| blender_rt_preset | 保存、列出、套用、导出可复用参数配方。 |
| blender_rt_perf | 诊断并应用渲染性能设置。 |
| blender_rt_opt | 找安全可合并的对象，降低场景开销。 |

### addon 访问

| 工具 | 它能给你什么 |
|---|---|
| blender_rt_cmd | 直接调用任意 addon 命令。 |
| blender_rt_commands | 查看可用 addon 命令和集成状态。 |

## 文档

- 曲面算法：docs/曲面算法.md
- 操作教程：docs/操作教程.md
- 配置参考：docs/配置参考.md
- AI 安装提示词：docs/AI-安装提示词.md
- 部署 SOP：docs/部署SOP.md
- 第三方安装清单：docs/给第三方的安装清单.md
- 建模 skill：skills/blender-modeling/
- 版本说明：GitHub Releases 与 CHANGELOG.md

## 常见问题

| 现象 | 处理 |
|---|---|
| doctor 没回 kind=ok | 检查 Blender 是否在跑、addon 是否 Connect。GUI 路线只认这一条。 |
| 没有 GUI | 用持久 worker 备用通路：启动 worker，再执行代码并要一帧图。 |
| 另一个会话正在写同一个场景 | 用 blender_viewport 拿写租约；并行建模用独立无头 worker。 |
| 装过旧 MCP for Blender | 停用它，改用随包的 DSH Blender Bridge addon。 |
| 图生成了但没附上 | 升级到最新构建（已包含 Windows 路径修复）；仍不行就直接打开返回的路径。 |
| 按下面步骤装完还是用不了 | 先检查 addon：只支持 addon/dsh_blender_bridge.py，并且必须启用、必须 Connect。 |

## 开发

- 离线测试：npm test
- 改 src 后重建：DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh
- 随包 skill 真源：skills/blender-modeling/

## 支持

如果这个项目对你有帮助，欢迎在爱发电支持开发：<https://afdian.com/a/sixtysevenlf>

## 许可

插件本体为 BSD-3-Clause。随包建模 skill 为 MIT；见 skills/blender-modeling/LICENSE 与 NOTICE.md。Blender 是 Blender Foundation 的商标。
