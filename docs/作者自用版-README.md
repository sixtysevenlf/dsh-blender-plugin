# DSH × Blender 直连实时插件（standalone）

> 📌 这是**作者自用版**的说明，仅作对照：里面的路径（`D:\DSH\blender\tmp`、`/home/sixtyseven67/...`）都是作者机器的写死值。分享版请读 `README.md` 与 `docs/操作教程.md`。

> 版本 0.4.0 · 本会话全部优化的固化版：**一条直连通道 + 10 个工具 + 自带 runtime**。
> 整个文件夹可以搬到任意位置（WSL 或 Windows），包内**全部路径相对**（`runtime/` 与 `lib/` 同级）。
>
> v0.4.0 三批次增强：① 诊断（doctor/who/metrics）② 自定义视角取帧 + 写通道租约 ③ 无头进程工具。见 §4。

## 1. 它是什么

绕过 MCP/CLI，直接通过 TCP 与 Blender 的 addon socket（`127.0.0.1:9876`）对话，给 AI 模型一套低延迟原语：

| 工具 | 作用 | 实测 |
|---|---|---|
| `blender_rt_see` | 取一帧 3D 视口（`full=true` 走 `screenshot_area` 看整窗）；给 `from/look_at` 走**自定义视角**（不动物体、不动用户视口） | 视口帧 170–280 ms / 自定义视角 85–276 ms |
| `blender_rt_do` | 跑 Python（持久内核 `K`，跨调用保留状态） | ACT 10–50 ms |
| `blender_rt_watch` | 时间窗连续采样（≤6 帧 + 逐帧 hash） | 平均 67 ms/帧 |
| `blender_rt_loop` | **内环**：一次调用在 Blender 主线程跑几千次迭代（setup/step/measure + 罚项/退火/候选表/导出脚本） | 160 tick/s |
| `blender_rt_cmd` | 透传 addon 任意命令（30 个名字） | 25–55 ms |
| `blender_rt_commands` | 可用命令 + 5 个集成状态 | ~150 ms |
| **`blender_rt_perf`** | **渲染性能**：analyze 差分测速 / apply 优化预设 / revert / status | — |
| **`blender_rt_opt`** | **对象精简**：analyze 合并候选 / join 安全合并（几何零损失） | — |
| **`blender_rt_headless`** | **无头进程**：`blender.exe -b` 跑脚本（不占 GUI 通道），支持 `file/outdir/args/preload` | 起进程 0.8 s / 建场景+出 2 图 2.7 s |
| `blender_viewport` | 后端 status / **doctor 体检** / **who·lease·release 租约** / start / stop / restart（15 s 看护自动拉起） | 体检往返 97 ms |

## 2. 目录结构（自包含）

```
dsh-blender-plugin/
├── package.json            # 插件包定义（@dsh-external/dsh-blender-viewport）
├── tsconfig.json           # host 侧 tsc 编译配置
├── scripts/build.sh        # 构建：链接 checkout 依赖 + tsc → lib/
├── src/index.ts            # host：10 个工具 + 后端看护 + 租约心跳（相对路径定位 runtime）
├── runtime/                # 运行时（node + python）
│   ├── engine.mjs          # 直连引擎：addon socket 客户端 + 命令目录 + loop/perf/view/headless
│   ├── server.mjs          # 后端 HTTP 127.0.0.1:9877：/health /status /doctor /who /commands /frame.png
│   │                       #   /act /cmd /loop /perf /opt /view /headless /lease /release
│   ├── runner.py           # 内环 runner v2（timers 循环 + 限额急停 + penalize/anneal/候选表/导出）
│   ├── perf.py             # 渲染性能预设 + 对象精简（本会话优化的固化版）
│   ├── view.py             # 自定义视角捕获（自建矩阵 + GPUOffScreen.draw_view3d + 手写 PNG）
│   └── _probe_tools.mjs    # 诊断：逐条探测 addon 命令
└── docs/
    ├── AI实时交互Blender-通道说明.md
    └── AI建模双层循环-方案.md
```

## 3. 安装 / 构建 / 注入

```bash
# 构建（需要指向 DSH checkout 以拿到 cordis/schemastery/dsh-tools 的符号链接）
DSH_CHECKOUT=/home/sixtyseven67/dsh-harness bash scripts/build.sh
# 或注入器环境内：
#   dev_build_plugin   {"dir": "<本目录>"}
#   dev_inject_plugin  {"dir": "<本目录>"}
```

注入后加载器里是一个 entry：`@dsh-external/dsh-blender-viewport`；插件加载时会自动拉起后端（`node runtime/server.mjs --port 9877`）并启动 15 秒看护。

**前置**：GUI Blender 在跑（addon 自动监听 9876）。启动：`pwsh -File D:\DSH\blender\launch-blender.ps1`。

## 4. v0.4.0 三批次增强

### 4.1 诊断：把"连不上"拆成可行动的原因

| 入口 | 行为 |
|---|---|
| `blender_viewport {op:"doctor"}` / `GET /doctor` | **真跑一次 bpy 往返**（`status`）→ 通了报 `ok`（实测往返 97 ms / ping 50 ms）；不通才三级判定：`blender-unreachable`（934 端口没监听）/ `main-thread-busy`（ping 通、命令被主线程挡）/ `addon-thread-stuck`（ping 不通），每条都带 `summary` + `fix` |
| `GET /status` | `region` + `stats` + `metrics` + **仅在有 lastError 时**才带 `diagnosis`（成功即清零，不显示过期结论） |
| `GET /who` | 租约持有者 + TTL + 通道计数（calls/errors/timeouts/inflight/lastCmd/lastCmdAgeMs） |
| 各写/读路由的错误体 | 带上 `diagnosis` 对象（超时/断连时自动附着） |

### 4.2 自定义视角取帧（`runtime/view.py`）

`blender_rt_see {from:"x,y,z", look_at:"x,y,z", lens, ortho, ortho_scale, view_size:"宽x高", shading, overlays, view_mode:"viewport|render"}`

- **零场景改动**：自建 view/projection 矩阵 + `GPUOffScreen.draw_view3d`，**不建相机对象、不动 `scene.camera`、不动用户视口**；`shading/overlays` 临时改写、出图即还原。
- 矩阵与真相机对拍：`calc_matrix_camera` 逐元素一致，**最大偏差 1.2e-7**（landscape / portrait / ortho 三种都验过）；镜头/传感/正交/位移参数与相机语义一致（AUTO 时传感器贴长边）。
- PNG **手写**（zlib+CRC）而非走 `bpy.data.images`：帧缓冲读回已是 sRGB 8bit，直写字节 = 所见即所得，避免二次 gamma；读出用 `memoryview(tex.read())`，1920×1080 展平 **1 ms**。
- 实测：live 900×506 → **276 ms**；640×360 → 170–261 ms；**同参数重复出图 md5 相同**（hash `3a7bee2a`）；无头 320×180 → **85 ms**。
- `view_mode:"render"`：临时相机 + `bpy.ops.render.opengl`（Workbench 快渲，640×360 → 1157 ms），结束逐项还原并删掉临时相机；`viewport` 失败会自动降级到它，并在返回里带 `fallback_from`。
- Blender 5.2 起 `gpu.init()` 让无头（`-b`）也能用离屏绘制 → 无头批量多角度出图：`blender_rt_headless {preload:"view", script:...}`。

### 4.3 写通道租约（多会话/多 agent 共存）

- 路由：`POST /lease`（`holder, ttlMs, force, renewOnly`）、`POST /release`、`GET /who`；插件工具：`blender_viewport {op:"who"|"lease"|"release"}`。
- 语义：**无租约时零阻力**；一旦某会话持有租约，别的会话的**写路由**（`/act /cmd /loop /perf /opt /headless`）返回 `409 {error:"leased", holder, expiresInMs, hint}`，带 `force:true` 可抢占。
- **只读 op 不受限**：`perf status/help`、`loop status/board/help`、`opt analyze`（不能连"看现状"都被挡）。
- 插件自动带 `holder`（`plugin-pid-<pid>`，可用 `DSH_BLENDER_HOLDER` 覆盖）→ 单 agent 用起来完全无感；15 s 看护只在**本来就持有**时续期，空闲不抢占。
- 实测 9 条路径全过：别人持有 → 409；无 holder 写 → 409；force → 抢占成功且写成功；who 显示 holder/TTL/续期次数；心跳（自己）续期 & （别人）不抢；release 后可写。

### 4.4 无头进程：`blender_rt_headless`

```python
blender_rt_headless(script="print('HEADLESS {"ok":1}')", preload="view", outdir=r"D:\DSH\blender\out", timeout_ms=120000)
```

- WSL 侧 `spawn` Windows 的 `blender.exe -b`（走 binfmt 互操作，stdout/stderr 直连回传）；`file` 两种路径都收（`/mnt/d/...` 自动映射成 `D:\...`，WSL 内部路径映射成 UNC）。
- 默认 `--factory-startup`（干净、快、**不会去抢 9876 端口**）；要用用户启动文件/插件时 `factory_startup=false`。
- 脚本默认注入持久内核 `K`；`preload:"view,perf"` 会把 `runtime/*.py` 源码拼到脚本开头 → 无头进程直接用 `K.dsh_view_api` / `K.dsh_perf_api`。
- `print("HEADLESS {json}")` → 解析成 `result`；`outdir` 里的新文件按时间列出（名字/字节/"新"标记）；超时 SIGKILL。
- 实测：出厂场景建 3 个方块 + view.py 自检 + 出 2 张自定义视角图 = **2.66 s**；打开 1821 对象的工程 `FISTONE_v3.9_S5_master_delivery.blend` 并读设置 = **1.24 s**（顺带验证：该文件里 CYCLES/GPU/4096 spp/persistent_data/OPTIX/auto_tile off 均已固化）。

## 5. 本会话固化的优化（`blender_rt_perf` / `blender_rt_opt`）

### 5.1 诊断结论（fistone-build 实测，2318 对象 / 118k 面）

| 构成 | 数值 |
|---|---|
| 一次性启动（OptiX kernel JIT + 贴图解码 + 首次 BVH） | ~14–16 s（仅首次） |
| 每轮 CPU 侧场景同步（persistent off） | ≈4.2 s（2318 对象 → **≈1.4 ms/对象**） |
| persistent_data 后重复渲染 | 1080p/4spp **0.79 s**（off 时 14.6 s → **18×**） |
| GPU 单采样 | ≈0.2 s（1080p） |
| 采样数对 CPU 侧成本影响 | ≈0（砍采样不解决 CPU 瓶颈） |
| GPU 利用率 | CPU 卡住 2–4%/21 W → 优化后 90–93%/65 W |

### 5.2 预设内容（`blender_rt_perf op=apply`）

| 设置 | 值 | 依据 |
|---|---|---|
| `render.use_persistent_data` | true | 免除每轮重新同步（最大收益） |
| `cycles.denoiser` | OPTIX | 把降噪从 CPU 搬到 GPU |
| `cycles.denoising_use_gpu` | true | 同上 |
| `cycles.use_auto_tile` | false | GPU 上少一层 tile 调度 |
| `cycles.samples` | 1024 | 纯上限保护（自适应阈值不变，画质不变） |
| 快照 | 自动存 `K.dsh_perf_saved` | `op=revert` 一键还原 |

### 5.3 对象精简（`blender_rt_opt`）

- 合并规则（安全）：同集合 / 同材质 / 同父级 / 无修改器 / 无动画 / 无形态键 / **无自定义属性** / 无实例 / 非库链接
- 实测（fistone-build）：**2318 → 1821 对象**、几何零损失（面 161,498 不变、顶点 192,920 不变）、每轮同步 −0.45 s
- 更激进的空间（→101 对象，再省 ~3 s/轮）需要放弃"自定义属性"保护，**默认不做**

### 5.4 选哪条路（工具决策表）

| 你要做的事 | 用哪个 | 为什么 |
|---|---|---|
| 看一眼现在长什么样 | `blender_rt_see` | 50–100 ms，最便宜 |
| 换个角度看（不想动用户视口） | `blender_rt_see {from, look_at}` | 零场景改动，85–276 ms，可重复（md5 稳定） |
| 改一步就想验证 | `blender_rt_do {see:true}` | 一个原子步 ≈105 ms |
| 判断"动没动 / 对不对" | `blender_rt_watch` | 时间窗连续采样 + 逐帧 hash，一次最多 6 帧 |
| 要搜索/拟合参数（几千次迭代） | `blender_rt_loop` | 内环在 Blender 主线程跑，不花模型回合（160 tick/s） |
| **测量/目标必须是"多目标 + 约束"** | `blender_rt_loop` + `penalize` | 罚项把违规量并入单一 score；`top_k/group_key` 出候选表 |
| 收敛后要复核 / 复用 | 换一条通路测（`blender_rt_do` 独立算一遍）+ `blender_rt_loop op=export` | 防 Goodhart：内环只优化你写的目标函数 |
| 要 Cycles 成品渲染 / 大批量几何 / 数据校验 | **`blender_rt_headless`** | 独立进程：不占 GUI 通道、不撞 CUDA context、超时可杀 |
| 离线多角度批量出图 | `blender_rt_headless {preload:"view"}` | 无头进程里直接 `K.dsh_view_api`（实测 320×180 出图 85 ms） |
| 渲染一慢就想调参 | `blender_rt_perf {op:"status"}` 先看现状 → `analyze` → `apply` | 先量后调；预设可 `revert` |
| 场景对象太多导致每轮同步慢 | `blender_rt_opt {op:"analyze"}` → `join` | ≈1.4 ms/对象；合并几何零损失，务必先 `save_before` |
| 通道报错但不知道卡在哪 | `blender_viewport {op:"doctor"}` | 真跑一次 bpy 往返 + 三级判定 + 修法 |
| 多个会话/agent 抢同一个 Blender | `blender_viewport {op:"who"}` → `op:"lease"` | 写路由 409 明确告知持有者与剩余时间；只读 op 不受限 |

## 6. 使用示例

```python
# 看一眼
blender_rt_see(max_size=560)
# 改一步 + 回帧
blender_rt_do(code="bpy.data.objects['Cube'].scale = (2,1,1)", see=True)
# 内环：拟合 6 维参数（详见 docs/AI建模双层循环-方案.md）
blender_rt_loop(op="start", spec={...})
# 渲染性能：先诊断再应用预设
blender_rt_perf(op="analyze")
blender_rt_perf(op="apply")
# 对象精简：先看再合并（务必给回退点）
blender_rt_opt(op="analyze")
blender_rt_opt(op="join", args={dry_run=False, save_before="D:/Blender/x/blend/pre_join.blend"})
# 自定义视角（不动物体/不动用户视口）：从右前方看 + 正交顶视
blender_rt_see(from="9,-9,6", look_at="0,0,1", lens=50)
blender_rt_see(from="0,0,12", look_at="0,0,1", ortho=True, ortho_scale=12, shading="MATERIAL")
# 通道体检 / 租约（多会话共存时先 who）
blender_viewport(op="doctor")
blender_viewport(op="who")
# 无头进程：预载 view.py，多角度批量出图（不占 GUI 通道）
blender_rt_headless(preload="view", outdir=r"D:\DSH\blender\out", script="...")
```

## 7. 已知限制

1. **必须 GUI Blender**（addon 的 `start()` 在 background 模式直接 return）。
2. **绝不在 addon 的 socket 线程里调 GPU 枚举**（`get_devices()` 会崩 Blender）；这类操作用 `bpy.app.timers` 排主线程（`runtime/runner.py` 就是这么跑的）。
3. 帧文件固定落 `D:\DSH\blender\tmp\dsh_live_viewport.png`（Blender 写 UNC 会静默失败，所以必须 Windows 路径）。
4. `blender_rt_perf op=analyze` / `blender_rt_opt op=join` 会占用 Blender 主线程数秒到数十秒。
5. 自定义视角的 `viewport` 模式依赖 addon 所在那个 GUI 进程（无头进程走 `gpu.init()` 也可以，但需 Blender ≥4.x 且本机 GPU 可用）；`render` 模式走 Workbench/EEVEE 快渲，不是 Cycles 成品渲染 —— 要 Cycles 成品图请用 `blender_rt_headless` 跑 `bpy.ops.render.render(write_still=True)`。
6. 租约是**进程级**的：它只保护同一个后端（127.0.0.1:9877）后面的那个 Blender；两个会话若各自起了后端（不同端口），租约互不可见。

## 8. 回滚

- 后端：`blender_viewport op=stop`（会暂停 15 s 看护）；`op=start` 恢复。
- 渲染设置：`blender_rt_perf op=revert`。
- 插件：注入器 `dev_uninject_plugin {"match":"dsh-blender-plugin"}`。

## 9. 分享 / 移植说明（当前口径）

> 2026-09-13 按用户决定**回退**了"帧目录可配置"那版改动：现在路径是**硬编码**的，针对本机形态（DSH 在 WSL、Blender 在 Windows）。

| 常量 | 值 | 位置 |
|---|---|---|
| `WIN_TMP` | `D:\DSH\blender\tmp`（Blender 侧写） | `runtime/engine.mjs` |
| `WSL_TMP` | `/mnt/d/DSH/blender/tmp`（宿主侧读） | `runtime/engine.mjs` |
| `DSH_BLENDER_EXE` | 无头进程的 blender 可执行文件（默认 `/mnt/d/SteamLibrary/steamapps/common/Blender/blender.exe`） | 环境变量 |
| `DSH_BLENDER_HOLDER` | 本会话在写通道上的租约身份（默认 `plugin-pid-<pid>`） | 环境变量 |
| `VIEW_PNG_WIN` | `D:\DSH\blender\tmp\dsh_view_capture.png`（自定义视角默认输出） | `runtime/engine.mjs` |

**要拿到别的机器用**：改 `runtime/engine.mjs` 里这两个常量（指向两端都能读写的目录）+ 对方的 DSH/Blender/addon 环境，然后 `DSH_CHECKOUT=<对方的 dsh checkout> bash scripts/build.sh` 重新构建注入。

**其它环境前提**：
- `「MCP for Blender」addon` 启用且监听 `127.0.0.1:9876`（必须 GUI；addon 本体不在本包内）。
- `blender_rt_perf op=apply` 的默认降噪器是 **OPTIX**（NVIDIA）；非 N 卡改成 `OPENIMAGEDENOISE`/`HIP`。
- 设备启用（`compute_device_type` + 勾设备）在 **Blender 用户偏好**里，不在工程内 —— 本机曾因 Intel Level Zero `ze_loader.dll` 崩过，详见 `docs/AI实时交互Blender-通道说明.md` §8。

**本包不包含**：addon 本体、人肉面板/MJPEG 推流/鼠标键盘注入。
