# 部署与自检 SOP（写给 AI 执行的标准作业程序）

> 格式借鉴 lurenjia-l/dsh-blender-stylized-shading 的 AI_DEPLOY.md：**目标与完成标准 → 环境探测 → 安装 → 连接 → 自检 → 汇报 → 排障**。
> 原则：**先探测，别猜**；每一步都要有可验证的判据；失败给出明确的下一步而不是笼统报错。

## 0. 目标与完成标准

目标：让 DSH 侧 14 个 `blender_rt_*` 工具在本机能调通，且渲染走默认 EEVEE + 光追。

完成标准（全部满足才算装好）：
- `blender_viewport(op="doctor")` 返回 `kind=ok`（TCP + ping + bpy 往返三级都通）；
- `blender_rt_commands` 能列出 30 条 addon 命令；
- `blender_rt_perf(op="status")` 能返回 `engine` 字段（预期 `BLENDER_EEVEE`）；
- `blender_rt_plan(op="qc_self_check")` 返回 `iou_self = 1.0`；
- 任一渲染类调用产出的 PNG **不是黑图**（可看 `qc_compare` 的对照图）。

## 1. 环境探测（先做，别猜）

| 探测项 | 命令 | 期望 |
|---|---|---|
| Blender 进程 | 任务管理器 / `tasklist` 里有 `blender.exe` | 在跑（**GUI 模式**；`-b` 只由 headless 工具用） |
| addon socket | `blender_viewport(op="status")` | `9876` 通 |
| 插件后端 | `blender_viewport(op="doctor")` | `kind=ok`；若 `blender-unreachable` 先启动 Blender |
| 写通道租约 | `blender_viewport(op="who")` | 别的会话持有时写操作会 409（只读 op 豁免，`force=true` 可抢） |
| 引擎 | `blender_rt_perf(op="status")` | `engine=BLENDER_EEVEE`（v0.8.0 起默认） |

## 2. 安装插件

```bash
cd <插件目录>          # 解包后的 dsh-blender-plugin/
bash scripts/build.sh  # 链接依赖 + 编译 lib/（需要 bash + node/npm 在 PATH）
```

然后按 bundle 方式装进 profile：`node_modules/@dsh-external/dsh-blender-plugin` 建软链 →
profile 的 `package.json` 里 `dependencies`（`link:`）+ `dsh.profile.bundles` 各加一行 →
`dsh --profile <profile> --dump-config` 预演 → 重启 DSH。

## 3. 连接 Blender addon（人工一步）

1. Blender 里启用 **MCP for Blender** addon；
2. 3D 视口按 `N` → **MCP for Blender** 面板 → **Connect**（socket 默认 `127.0.0.1:9876`）；
3. 回到 DSH 跑 `blender_viewport(op="doctor")` 复核。

## 4. 自检（部署后必须做）

```text
blender_viewport(op="doctor")                     # 通道三级体检
blender_rt_commands()                             # 30 条命令 + 5 个集成状态
blender_rt_perf(op="status")                      # 引擎/采样/光追状态
blender_rt_plan(op="qc_self_check")               # QC 算法自检（自比 IoU 应为 1.0）
blender_rt_see()                                  # 回一帧视口（确认画面通道）
blender_rt_headless(script="print('HEADLESS {"ok":1}')")  # 无头通道 + 引擎前导
```

## 5. 向用户汇报的格式

1. 结论（能用 / 不能用）；2. 判据（doctor 的 kind、engine、qc_self_check 的 iou_self）；3. 未判明项与下一步（如果某步失败，给出具体修法）。

## 6. 排障速查

| 症状 | 先看 | 常见原因 |
|---|---|---|
| 工具报"后端不可用" | 是否 `blender.exe` 在跑 | Blender 没启动；或 addon 没 Connect |
| `doctor` 报 `main-thread-busy` | 是不是刚跑过重活 | 主线程被占；重活改走 `blender_rt_headless` / `blender_rt_worker` |
| `rt_do` 没 stdout / 异常看不到 | 插件版本 | v0.7.0–0.8.0 有此回归（Issue #4），升到 **v0.8.1+** |
| 渲染暗部纯黑 | `docs/EEVEE-工作要点.md` §2 | `Shader to RGB` 只含直接光；用双 Background + Is Camera Ray 或加环境灯 |
| 渲染很慢 | 是否冷启动 | EEVEE 首帧着色器编译约 16 s → 用 `blender_rt_worker` 热会话 |
| QC 分数与历史不可比 | `engine_at_qc` | 换了引擎（EEVEE ↔ Cycles）；判据只在同引擎内可比 |

> 相关文档：`docs/操作教程.md`（工具逐个上手）· `docs/配置参考.md`（配置项 + 引擎索引）· `docs/EEVEE-工作要点.md`（引擎与材质踩坑）。
| 想跑 .py 却把路径给了 headless | 用对工具 | headless 的 file 是 .blend 工程；.py 脚本用 blender_rt_do(file=...) 或 headless(script=...) |
