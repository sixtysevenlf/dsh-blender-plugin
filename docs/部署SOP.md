# 部署与自检 SOP（写给 AI 执行的标准作业程序）

> 格式借鉴 lurenjia-l/dsh-blender-stylized-shading 的 AI_DEPLOY.md：**目标与完成标准 → 环境探测 → 安装 → 连接 → 自检 → 汇报 → 排障**。
> 原则：**先探测，别猜**；每一步都要有可验证的判据；失败给出明确的下一步而不是笼统报错。

> **当前版本口径（2026-10-28）**：插件 **v2.6.2** · **15 个模型工具** · plan catalog **34 family / 228 op** ·
> addon **v1.10**（protocol **11**）· 后端 `127.0.0.1:9877` / addon `127.0.0.1:9876`。
> 验收判据：**GUI 路线 = `doctor` 回 `kind=ok`**；**纯无头路线**（不装 addon）= `rt_worker(start)` + `see` 出图自证
> （doctor 的 bpy 往返不可达属预期）。**默认先开 GUI**；无 GUI 时交互式无头（持久 worker + see/watch/reset）也能独立建模，但它是备用路径。

## 0. 目标与完成标准

目标：让 DSH 侧 **15 个模型工具**在本机调通；有 GUI 时走 GUI，没有 GUI 时用**交互式无头**（`blender_rt_worker`）同样能建模/看图。
plan catalog 应能列出 **34 family / 228 op**。

完成标准（**通用项 + 你选的路线**全部满足才算装好）：
- **通用**：`blender_rt_plan(op="catalog")` 能列出 **34 family / 228 op**；
- **通用**：`blender_rt_commands` 能列出 addon **39 条命令**（24 常驻 + 15 集成门控）；
- **通用**：`blender_rt_perf(op="status")` 能返回 `engine` 字段（默认 `BLENDER_EEVEE`；QC/白模渲染用 Workbench）；
- **通用**：`blender_rt_plan(op="qc_self_check")` 返回 `iou_self = 1.0`；任一渲染类调用产出的 PNG **不是黑图**（可看 `qc_compare` 对照图）；
- **GUI 路线**：`blender_viewport(op="doctor")` 返回 `kind=ok`（TCP + ping + bpy 往返三级都通）；
- **纯无头路线**（不装 addon）：`blender_rt_worker(op="start")` → `op="see"` 能回一帧图即视为通道可用
  （doctor 的 bpy 往返不可达属预期，不是装坏）。

## 1. 环境探测（先做，别猜）

| 探测项 | 命令 | 期望 |
|---|---|---|
| Blender 进程 | 任务管理器 / `tasklist` 里有 `blender.exe` | 有 GUI 最方便；**没有也可以**（headless worker 独立跑） |
| addon socket | `blender_viewport(op="status")` | `9876` 通（addon v1.10 / protocol 11） |
| 插件后端 | `blender_viewport(op="doctor")` | `kind=ok`；若 `blender-unreachable` 先启动 Blender 或改用无头 |
| 写通道租约 | `blender_viewport(op="who")` | 别的会话持有时写操作会 409（只读 op 豁免，`force=true` 可抢） |
| 引擎 | `blender_rt_perf(op="status")` | 默认 `engine=BLENDER_EEVEE`；QC/白模用 Workbench |

## 2. 安装插件

~~~bash
cd <插件目录>          # 解包后的 dsh-blender-plugin/
ls lib/index.js        # ← 有这个文件就**不用构建**（本仓已提交 lib/）

# 只有要改 src/ 时才需要构建：
# DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh
~~~

然后按 bundle 方式装进 profile：`node_modules/@dsh-external/dsh-blender-plugin` 建软链 →
profile 的 `package.json` 里 `dependencies`（`link:`）+ `dsh.profile.bundles` 各加一行 →
`dsh --profile <profile> --dump-config` 预演 → 重启 DSH。

## 3. 连接 Blender addon（人工一步）

1. Blender 里启用本仓 **DSH Blender Bridge** addon（`addon/dsh_blender_bridge.py`，**v1.10+** / protocol 11）。v2.3 起旧 `MCP for Blender` 不再支持，如已安装请先停用（见 [../MIGRATIONS.md](../MIGRATIONS.md)）；
2. 3D 视口按 `N` → **DSH Blender Bridge** 面板 → **Connect**（socket 默认 `127.0.0.1:9876`）；
3. 回到 DSH 跑 `blender_viewport(op="doctor")` 复核；
4. 或者直接 `blender_viewport(op="launch")` 一键拉起 GUI Blender 并自动 Connect（幂等）。

## 4. 自检（部署后必须做）

~~~text
blender_viewport(op="doctor")                     # 通道三级体检（kind=ok）
blender_rt_plan(op="catalog")                     # 34 family / 228 op
blender_rt_commands()                             # 39 条命令 + 5 个集成状态
blender_rt_perf(op="status")                      # 引擎/采样/光追状态
blender_rt_plan(op="qc_self_check")               # QC 算法自检（自比 IoU 应为 1.0）
blender_rt_see()                                  # 回一帧视口（有 GUI 时）
blender_rt_headless(script="print('HEADLESS {ok:1}')")  # 无头进程 + 引擎前导
blender_rt_worker(op="start")                     # 交互式无头热会话（备用；默认先开 GUI）
blender_rt_worker(op="see", from="4,-4,3", look_at="0,0,1")  # 无头出图
~~~

## 5. 向用户汇报的格式

1. 结论（能用 / 不能用，以及是 GUI 还是无头通路）；2. 判据（doctor 的 kind、catalog 的 34/228、engine、qc_self_check 的 iou_self）；
3. 未判明项与下一步（如果某步失败，给出具体修法）。

## 6. 排障速查

| 症状 | 先看 | 常见原因 |
|---|---|---|
| 工具报"后端不可用" | 是否 `blender.exe` 在跑 / 后端 9877 是否在 | Blender 没启动；或 addon 没 Connect；或后端没起来（`blender_viewport(op="start")`） |
| `doctor` 报 `main-thread-busy` | 是不是刚跑过重活 | 主线程被占；重活改走 `blender_rt_headless` / `blender_rt_worker` |
| 工具数不是 15、catalog 不是 34/228 | 插件版本 / 是否重启 | 安装 v2.6.2 后**重启 DSH**；`--dump-config` 确认 profile bundle 已加载 |
| `rt_do`/`rt_see` 连不上但无头可用 | 是否有 GUI | GUI 不可用时的备用：用 `session="auto"` / `rt_worker` 走交互式无头 |
| 渲染暗部纯黑 | `docs/EEVEE-工作要点.md` §2 | `Shader to RGB` 只含直接光；用双 Background + Is Camera Ray 或加环境灯 |
| 渲染很慢 | 是否冷启动 | EEVEE 首帧着色器编译约 16 s → 用 `blender_rt_worker` 热会话 |
| QC 分数与历史不可比 | `engine_at_qc` | 换了引擎（EEVEE ↔ Cycles）；判据只在同引擎内可比 |

> 相关文档：`docs/操作教程.md`（工具逐个上手）· `docs/配置参考.md`（配置项 + 引擎索引）· `docs/EEVEE-工作要点.md`（引擎与材质踩坑）·
> `docs/AI-安装提示词.md`（可粘贴给任意 AI 的安装提示词）。
