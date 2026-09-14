# CHANGELOG — @dsh-external/dsh-blender-plugin

> 其他会话/agent 请先读这里，再看 `MIGRATIONS.md`（路径变更）与 `README.md`（用法）。

## v0.4.1（2026-09-14）—— addon 协议适配：同时支持 ahujasid 与 harveyxiacn 两种 addon

> 来源：外部贡献 [PR #2](https://github.com/sixtysevenlf/dsh-blender-plugin/pull/2)（[@yihefeikong-rgb](https://github.com/yihefeikong-rgb)，对应 issue #1），**已合并**（merge commit `b0a04b3`）。
> 状态：作者在 Windows 11 + Blender 5.2.1 LTS + harveyxiacn `blender_mcp_addon` 实测；维护侧合并前独立复核：`node tests/protocol_selftest.mjs` → **28/28 通过**，并用官方 addon 真机验证无回归（`/doctor` → `protocol=ahujasid from=auto`，`execute_code` 与自定义视角取帧正常）。
> 新增 `addonProtocol` 配置项，默认 `auto`。**不改变既有行为**：仍然是扁平协议优先，装官方 addon 的机器零配置。

- **新增 `runtime/addon-protocol.mjs`**：把两种 addon 的差异（分帧方式 / 命令词汇 / 应答封套）收敛成协议描述符，`AddonClient` 与 `freshPing` 共用同一套 `plan / encode / decode`。
- **新增配置 `addonProtocol`**（`auto` / `ahujasid` / `category-action`，等价环境变量 `DSH_BLENDER_ADDON_PROTOCOL`）：`auto` 首次使用时探测一次并缓存 —— 先发**不带换行**的扁平 ping（只有扁平 addon 会回），没回再发带换行的 category/action ping，并按回包封套形状纠错（避免 addon 偶尔忙一下把协议判错）。
- **诊断升级**：`/doctor` 输出 `protocol` / `protocol_label` / `protocol_from`；原先"端口通但 ping 不回"一律归为 `addon-thread-stuck`，现在会再探一次协议，确属协议不匹配时给出 `detected_protocol` 与「把 `addonProtocol` 改成 X」的修法；`diagnose()` 的面板提示也随协议变化。
- **category-action 侧适配**：`execute_code → utility.execute_python`（回包重新包成 `{executed,result}`，否则 `/act` 的 stdout 与 `LOOP` 标记解析全空）、`get_viewport_screenshot` / `get_world_state_snapshot` 由插件侧生成 python（对面同名 handler 的参数与 Blender 4 老 API 都不兼容）、遥测类命令走本地桩、5 条资产集成明确报错。
- **新增自检 `tests/protocol_selftest.mjs`**（`npm test`）：两个 mock addon 覆盖探测 / 解析 / 两种协议下的调用 / 错误传播 / 缺能力不假装成功，共 28 项断言，**不需要 Blender**。
- 文档：`docs/配置参考.md` §6 新增「两种 addon 协议」小节；`README.md` 与 `README.zh-CN.md` 的前置条件、故障表各补一行；`dsh-blender.config.example.json` 加 `addonProtocol`。

## v0.4.0（2026-09-13，本会话）—— 三批次增强：诊断 / 自定义视角 / 租约 / 无头进程

> 状态：**已实现并逐条实测**（下方每条都带实测值，不是计划稿）。工具数 9 → 10。

**① 可观测性（诊断）**
- 工具报错不再只有 `connect timeout`：超时/连不上时自动三级判定并给出人话原因（`kind` + `summary` + `fix`）：
  - TCP 不通 → `blender-unreachable`（Blender 没跑 / addon 没监听 9876）
  - TCP 通 + `ping` 通 → `main-thread-busy`（主线程被占：渲染/模态操作）
  - TCP 通 + `ping` 不通 → `addon-thread-stuck`（addon 客户端线程卡住）
- 新增 `GET /doctor` + `blender_viewport {op:"doctor"}`：**真跑一次 bpy 往返**（`status`）通了才报 `ok`（实测往返 97 ms / ping 50 ms），不通才落到上面的三级判定。
- 新增 `GET /who` + `{op:"who"}`：租约持有者 + TTL + 通道指标（calls/errors/timeouts/inflight/lastCmd/lastCmdAgeMs/views/headlessRuns）。
- `/status` 带 `metrics`，且**只在有 lastError 时**才带 `diagnosis`（成功即清零，不显示过期结论）；各写/读路由的错误体统一带 `diagnosis`。

**② 自定义视角取帧（新增 `runtime/view.py`）**
- `blender_rt_see` 新增 `from / look_at / lens / ortho / ortho_scale / view_size / shading / overlays / view_mode`；给 `from+look_at` 即走自定义视角，**不建相机对象、不动 `scene.camera`、不动用户视口**；`shading/overlays` 临时改写、出图即还原。
- 矩阵自建并与 `calc_matrix_camera` 对拍：landscape / portrait / ortho 三种 **最大偏差 1.2e-7**（前向/正交/位移参数与相机语义一致）。
- PNG 手写（zlib+CRC）：帧缓冲读回已是 sRGB 8bit，直写字节避免二次 gamma（用 `bpy.data.images` 那条路会二次编码）；读出 `memoryview(tex.read())` 展平 1920×1080 仅 **1 ms**。
- 实测：live 900×506 **276 ms**、640×360 170–261 ms、无头 320×180 **85 ms**；同参数重复出图 **md5 相同**（hash `3a7bee2a`，确定性）。
- `view_mode:"render"` 兜底：临时相机 + `render.opengl`（640×360 → 1157 ms），结束删相机并逐项还原；viewport 失败自动降级并在返回里带 `fallback_from`。
- Blender 5.2 的 `gpu.init()` 让 `-b` 无头进程也能离屏绘制 → 批量多角度出图可完全离线做。

**③ 写通道租约（`/lease` `/release` `/who` + `{op:"lease|release|who"}`）**
- 语义：**无租约时零阻力**；有租约时别的会话的写路由（`/act /cmd /loop /perf /opt /headless`）返回 `409 {error:"leased", holder, expiresInMs, hint}`；`force:true` 抢占；`renewOnly:true` 只续期不抢。
- **只读 op 豁免**：`perf status/help`、`loop status/board/help`、`opt analyze`（看现状不该被挡）。
- 插件自动带 `holder`（`plugin-pid-<pid>`，`DSH_BLENDER_HOLDER` 可覆盖）；15 s 看护仅在**本来就持有**时心跳续期。
- 实测 9 条路径：别人持有→409 · 无 holder→409 · force 抢占并写成功 · who 显示 holder/TTL/续期 · 自己心跳续期 · 别人心跳不抢 · release 后可写。

**④ 独立渲染进程（新工具 `blender_rt_headless`）**
- WSL 侧 `spawn` Windows 的 `blender.exe -b`（binfmt 互操作），stdout/stderr 直连回传；`file` 收 WSL 路径（`/mnt/d/...` 自动映射 `D:\\...`）或 Windows 路径（内部路径→UNC）。
- 默认 `--factory-startup`（快、**不抢 9876 端口**）；`file / outdir / args / timeout_ms / factory_startup / bootstrap` 可控；`print("HEADLESS {json}")` 解析成 `result`；`outdir` 新文件按时间列出；超时 SIGKILL。
- `preload:"view,perf"` 把 `runtime/*.py` 源码拼到脚本开头 → 无头进程也能用 `K.dsh_view_api` / `K.dsh_perf_api`。
- 实测：建 3 方块场景 + view.py 自检 + 出 2 张自定义视角图 = **2.66 s**；打开 1821 对象的 `FISTONE_v3.9_S5_master_delivery.blend` 读设置 = **1.24 s**。

**文档**
- `README.md` 新增 §4 三批次增强（含全部实测值与 9 条租约用例）、工具表 9 → 10、已知限制补 3 条、移植说明补环境变量。
- 旧版写的「顾问式租约 / `require_lease` / `{op:"render"}` / `resolution` 参数名」与实际实现不符，本版已按实现改写（见 §①③④）。
- `MIGRATIONS.md` 追加 v0.4.0 API 变更条目。

## v0.3.0（2026-09-13）—— 独立成包

- 包名 `@dsh-external/dsh-blender-viewport` → **`@dsh-external/dsh-blender-plugin`**；目录 `plugins/dsh-blender-viewport/` → **`dsh-blender-plugin/`**（自带 `runtime/`，路径全相对）。
- 新增 `blender_rt_perf`（渲染性能诊断/预设/还原）与 `blender_rt_opt`（对象精简：合并候选/安全合并）。
- 后端 15 s 看护（掉线自动拉起）；`blender_viewport op=restart`。

## v0.2.0 / v0.1.0

- 直连 addon socket（绕过 MCP/CLI）：`blender_rt_see / rt_do / rt_watch / rt_cmd / rt_commands` + 持久内核 `K`。
- `blender_rt_loop` 内环（timers 驱动，160 tick/s，罚项/退火/候选表/导出）。
