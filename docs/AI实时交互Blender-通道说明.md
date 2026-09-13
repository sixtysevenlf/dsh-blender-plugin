# AI 实时交互 Blender —— 通道说明（这才是"可交互"的正解）
>
> ⚠️ **分享版说明**：本文是作者机器上的实测与踩坑记录，文中出现的 `D:\DSH\...`、`/home/sixtyseven67/...`、`launch-blender.ps1` 等都是**作者环境的示例**；
> 在你的环境里对应的路径由 `runtime/config.mjs` 自动解析（见 [配置参考.md](配置参考.md)）。机制、踩坑与结论是通用的。


> 2026-09-13 更新 · Blender 5.2.1 LTS · addon MCP for Blender v1.6 / protocol 5
> 本次更新：命令面扩到 **30 条**（新增 `blender_rt_cmd` / `blender_rt_commands`）· 五个集成开关全部打开并**写入启动文件** · 默认引擎改为 **Cycles + GPU(OptiX)** · 根治 **ze_loader 崩溃**（细节见 §7 / §8）
> 目标：让**模型自己**以接近实时的频率「看视口 → 改场景 → 再看」，而不是让人用鼠标去点。
> 人肉面板（网页 UI + Windows 输入注入）已按需求**移除**：`viewport/ui.html`、`tools/win_input_daemon.ps1`、`win_input_probe.ps1`、插件的 client 面板与 `/`、`/stream.mjpg`、`/input` 三条路由全部删除。

## 1. 十个原语（模型可直接调用）

| 工具 | 作用 | 实测延迟 |
|---|---|---|
| **blender_rt_see** | 取一帧视口，内联成图片返回 | 54-100 ms/帧 |
| **blender_rt_do** | 在 Blender 里跑一段 Python，可同时回一帧（= 一个完整的「做+看」步） | ACT 11-32 ms · STEP ≈ 100 ms |
| **blender_rt_watch** | 可选先跑代码，然后在 N 秒内按 fps 连续采样，回最多 6 帧 + 逐帧 hash | 10 帧/2.5s，平均 67 ms/帧 |
| **blender_rt_loop** | **Blender 侧内环**：一次调用跑几百到几千次迭代（`setup/step/measure` + 指标历史 + 急停） | 实测 **162.8 tick/s**（977 次/6 s） |
| **blender_rt_cmd** | 直连透传**任意 addon 命令**（30 个名字），含 MCP 不暴露的 5 条与全部资产类命令 | 25-55 ms（网络类看服务端） |
| **blender_rt_commands** | 报当前可用命令清单 + 5 个集成真实状态（开关/缺什么 key） | ~150 ms |
|（运维）blender_viewport | status / start / stop 后端 | ~ms |

对照：旧的 CLI/MCP 通道每次调用要 spawn 一个 python MCP 服务端，**0.9-1.5 s/次**；直连 addon socket 后 **≈0.05 s/次**，快 20 倍以上。

## 2. 关键机制

### 2.1 直连 addon socket（不走 MCP/python）
    TCP 127.0.0.1:9876
      → {"type":"get_viewport_screenshot","params":{"max_size":560,"filepath":"D:\\DSH\\blender\\tmp\\dsh_live_viewport.png"}}
      ← {"status":"success","result":{"success":true,"width":560,"height":330,"method":"offscreen"}}
    TCP 127.0.0.1:9876
      → {"type":"execute_code","params":{"code":"..."}}
      ← {"status":"success","result":{"executed":true,"result":"<stdout>"}}
固定文件名覆盖写 → 无残留文件；离屏 GPUOffScreen 渲染 → 不依赖窗口是否前台。

### 2.2 持久内核 K（REPL 语义）
addon 的 execute_code **每次都在全新命名空间**里跑（实测：上次定义的变量下次读不到），但 sys.modules 是同一个进程。所以每次调用前自动注入引导：

    import sys as _sys, types as _types
    K = _sys.modules.get("dsh_rt_kernel") or _types.ModuleType("dsh_rt_kernel")
    import bpy, math, mathutils
    Vector = mathutils.Vector

用法（实测通过）：

    blender_rt_do(code="K.x = 41")            → stdout: set K.x = 41
    blender_rt_do(code="print(K.x + 1)")      → stdout: read-back K.x + 1 = 42   # 跨调用保留

### 2.3 命令面：直连能调 30 个 addon 命令（MCP 只有 28 个工具）

`blender_rt_cmd {name, params}` 直接透传；`blender_rt_commands` 先看清单。构成：

- **常驻 14 条 + ping**：`get_scene_info` `get_world_state_snapshot` `get_addon_info` `get_object_info` `get_viewport_screenshot` `execute_code` `drain_human_activity` `get_telemetry_consent` `set_telemetry_consent` + 5 条集成 `*_status`
- **集成门控 15 条**：PolyHaven 4（categories/search/download/set_texture）· Hyper3D 3 · Sketchfab 3 · Poly Pizza 2 · Hunyuan3D 3
- **MCP 不暴露的 5 条**：`get_addon_info` `get_world_state_snapshot` `drain_human_activity` `get_telemetry_consent` `set_telemetry_consent`（直连可直接调）
- 参数必须匹配 addon 真实签名：`get_scene_info` 无参、`get_object_info` 用 `name`、**不要**传 MCP 才有的 `user_prompt`

集成状态（2026-09-13 已修复并**持久化进启动文件**）：`polyhaven=on`（免 key，已实测拉到分类与搜索）· `hyper3d=on`（addon 自带免费试用 key，MAIN_SITE）· `hunyuan3d=on`（LOCAL_API，已有本地 api_url）· `sketchfab=off`、`polypizza=off`（**需要各自申请 API key**，调用会返回 `API key is not configured`）。
开关与 key 都是 **scene 属性**（`blendermcp_use_*` / `blendermcp_*_api_key`）→ 已通过 `bpy.ops.wm.save_homefile()` 写入 `startup.blend`，**新建文件自动继承**；既有 `.blend` 仍需各自开一次并存盘。也可用环境变量 `BLENDERMCP_SKETCHFAB_API_KEY` / `BLENDERMCP_POLYPIZZA_API_KEY` 注入。

### 2.4 Blender 侧自主循环（真正的"实时"）
模型不需要每帧都在场：一次调用里注册定时器，让 Blender 自己按帧驱动，模型隔一会儿再 watch 采样：

    def tick():
        ...改参数/驱动动画...
        return 0.05          # 50ms 后再来
    bpy.app.timers.register(tick)

实测：注册后 2.5 s 内 10 次采样全部画面不同（hash 10/10 互异），watch 返回的 6 张帧可以肉眼看出视口在转。

### 2.5 主线程铁律：GPU / 驱动相关操作必须走 `bpy.app.timers`

addon 的 socket 线程会**直接**跑 `execute_code`，在那里调 GPU 驱动接口会出事。实测：从 socket 线程调 `preferences.addons["cycles"].preferences.get_devices()` → `ze_loader.dll` 里 `EXCEPTION_ACCESS_VIOLATION`，**整个 Blender 崩溃**（2026-09-13 14:48:59，crash 文件在 `D:\DSH\blender\tmp\blender.crash.txt`）；同一调用改到主线程定时器里执行则 1 秒内正常返回。

已验证的写法（命令立刻返回，随后轮询结果）：

    def job():
        p = bpy.context.preferences.addons["cycles"].preferences
        p.compute_device_type = "OPTIX"
        p.get_devices()                      # 主线程里安全
        for d in p.devices:
            d.use = (d.type == "OPTIX")
        bpy.context.scene.cycles.device = "GPU"
        bpy.ops.wm.save_userpref()
        K.dev = {"ok": True}
        return None
    K.dev = "running"
    bpy.app.timers.register(job, first_interval=0.2)
    # 之后用短小的 execute_code 轮询 K.dev

`bpy.ops.wm.save_userpref()` / `bpy.ops.wm.save_homefile()` 同理优先放主线程（socket 线程直调曾把连接打死）。

### 2.6 自定义视角捕获（v0.4.0，`runtime/view.py`）

看点：**不建相机对象、不改 `scene.camera`、不动用户视口**，就能从任意位置/朝向/焦距出一帧。

- 矩阵自建：`view = (Translation(from) @ dir.to_track_quat('-Z','Y').to_matrix().to_4x4()).inverted()`；
  投影按"传感器贴长边"算半宽/半高 → `p00 = lens/hx`、`p11 = lens/hy`（正交同构，AUTO/FORCED 都支持）。
  **与 `bpy.types.Camera.calc_matrix_camera()` 逐元素对拍**：landscape / portrait / ortho 三种 **最大偏差 1.2e-7**。
- 绘制：`GPUOffScreen(w,h)` + `draw_view3d(scene, vl, space_view3d, region, view, proj)`（与"取用户视口帧"同一条 GPU 通路，观感一致：着色、覆盖物、网格都按视口设置）。
- 读回：`memoryview(offscreen.texture_color.read())` → 一次性展平 8.3 MB（**1 ms**；逐行逐像素要 30 ms，`foreach_set` 传 2D Buffer 会报 "expected sequence size"）。
- 写盘：**手写 PNG**（IHDR/IDAT/IEND + zlib.crc32）。帧缓冲读回已是 **sRGB 8bit**，走 `bpy.data.images`（线性语义）会被二次 gamma 编码 —— 直写字节 = 所见即所得（自检：清屏 25/153/51 → PNG 解出的就是 25/153/51）。行序必须**上下翻转**（GL 读回自下而上，实测不翻转图是倒的）。
- 零残留：`shading/overlays` 临时改写后还原；`view_mode:"render"` 的临时相机在 finally 里删掉并还原渲染设置。
- 无头也能用：Blender 5.2 起 `gpu.init()` 可在 `-b` 下初始化 GPU 模块（实测无头 320×180 出图 85 ms）→ 离线批量多角度。

### 2.7 无头进程（v0.4.0，`blender_rt_headless`）

- WSL 侧 `spawn` Windows 的 `blender.exe -b`：WSL2 binfmt 直接执行 PE，stdout/stderr 直连回传（无需 PowerShell 包装）。
- 缺省 `--factory-startup`：干净、启动快、**不会去抢 9876 端口**（用户 startup 若含本 addon，无头进程也会尝试监听）。
- 路径映射：`/mnt/d/x` → `D:\\x`；WSL 内部路径 → `\\\\wsl.localhost\\<distro>\\...`（脚本优先落 `D:\\DSH\\blender\\tmp`，不可写则退回包内 `tmp/` + UNC）。
- `preload:"view,perf"`：把 `runtime/*.py` 源码拼到脚本开头 → 无头进程里 `K.dsh_view_api` / `K.dsh_perf_api` 直接可用。
- 约定：脚本 `print("HEADLESS {json}")` → 工具把最后一行解析成 `result`；`outdir` 新文件按时间列出；超时 SIGKILL。

### 2.8 写通道租约（v0.4.0）

- 状态在后端进程里：`holder + token + ttlMs + expiresAt + renewals`；`/lease`（`force` 抢、`renewOnly` 只续）、`/release`、`/who`。
- 门禁只加在写路由（`/act /cmd /loop /perf /opt /headless`），**只读 op 豁免**（`perf status/help`、`loop status/board/help`、`opt analyze`）—— 否则"看一眼现状"都会被挡。
- 插件每次写操作自动带 `holder`（`plugin-pid-<pid>`，`DSH_BLENDER_HOLDER` 可覆盖）：多会话时后到者拿到 409 + 可行动提示；单 agent 无感。看护每 15 s 只在**本来就持有**时续期。

## 3. 典型用法（模型视角）

    # 1) 看一眼现在什么样
    blender_rt_see(max_size=560)

    # 2) 改一步 + 立刻看结果（一个原子步）
    blender_rt_do(code="bpy.data.objects['Cube'].rotation_euler.z += 0.4", see=True)

    # 3) 起一个 Blender 侧循环，然后观察它动得对不对
    blender_rt_watch(code="...bpy.app.timers.register(tick)...", seconds=2.5, fps=4, max_size=420)

    # 4) 从别的角度验收（不打扰用户正在看的视口）
    blender_rt_see(from="9,-9,6", look_at="0,0,1")              # 右前上方
    blender_rt_see(from="0,0,12", look_at="0,0,1", ortho=True, ortho_scale=12)   # 正交顶视

    # 5) 重活丢给无头进程（GUI 通道继续可用）
    blender_rt_headless(preload="view,perf", outdir=r"D:\DSH\blender\out",
                        script="...多角度出图 / Cycles 成品渲染...", timeout_ms=600000)

    # 6) 通道体检 / 多会话共存
    blender_viewport(op="doctor")     # 真跑一次 bpy 往返，不通才三级判定
    blender_viewport(op="who")        # 谁持有写权限租约

## 4. 实测记录（本次）

| 动作 | 结果 |
|---|---|
| blender_rt_see 420px | 100 ms（冷）/ hash d39b9071 |
| blender_rt_do 设 K.x | ACT ok 11 ms |
| blender_rt_do 读 K.x+1 | 42（**跨调用状态保留**） |
| blender_rt_do(act 5 连发，直连) | 平均 50.8 ms/次 |
| see 5 连发（直连） | 平均 54.2 ms/次 |
| blender_rt_watch 2.5s@4fps | 10 帧 · 平均 67 ms/帧 · 不同画面 10/10 · 附 6 帧 |
| **（v0.4.0）自定义视角 900×506** | **276 ms**（live）；640×360 170–261 ms；同参数重复出图 md5 相同（hash 3a7bee2a） |
| **（v0.4.0）自定义视角 320×180（无头）** | **85 ms** |
| **（v0.4.0）view_mode="render" 640×360** | 1157 ms（Workbench 快渲；viewport 不可用时自动降级） |
| **（v0.4.0）doctor 体检** | 往返 97 ms · ping 50 ms · kind=ok |
| **（v0.4.0）无头：建场景+自检+出 2 图** | **2.66 s**（`blender -b` 冷起 0.8 s） |
| **（v0.4.0）无头：打开 1821 对象工程** | **1.24 s**（读回 CYCLES/GPU/4096spp/persistent/OPTIX 均已固化） |
| **（v0.4.0）租约 9 条路径** | 别人持有→409 · 无 holder→409 · force 抢占成功 · who/心跳/release 全部符合预期 |

## 5. 天花板与注意

- **模型每一步仍需一次工具调用**（回合边界），所以「实时」= 单步 ~100 ms、一轮对话内可连做几十到上百步；要毫秒级持续反应，请把循环写在 Blender 侧（timers / handlers / drivers），模型负责观察与调参。
- 每帧离屏渲染占 Blender 主线程 ~55-100 ms：8 fps 会明显卡人，建议 2-4 fps；纯 AI 观察时可短时拉高。
- 视口帧是**3D 视口区域**（不含 Blender UI 面板）；要看整窗口/其他区域 UI，用 `blender_rt_see {full:true}`（走 `screenshot_area`，已并入直连通道）。
- 后端（runtime/server.mjs，127.0.0.1:9877）由插件自动拉起；`blender_viewport op=stop` 停掉后，下次调用 RT 工具会自动再拉起。
- **自定义视角**每帧成本与普通取帧同量级（85–276 ms），但**完全不改场景/不改用户视口** → 可以放心在"用户正在手动看模型"时并行做多角度验收（§2.6）。
- **重活一律走 `blender_rt_headless`**：GUI 会话里连续 `render.render()` 会撞 CUDA context 且全程占住主线程；无头进程独立、可并行、超时可杀（§2.7）。
- **并发**：多个会话/agent 同时驱动一个 Blender 时，先 `blender_viewport op=who`；写路由在别人持有租约时返回 409（只读 op 豁免），`op=lease force=true` 可接管（§2.8）。

## 6. 文件

| 文件 | 作用 |
|---|---|
| `dsh-blender-plugin/src/index.ts` | 十个工具：rt_see（含自定义视角）/ rt_do / rt_watch / rt_loop / rt_cmd / rt_commands / rt_perf / rt_opt / **rt_headless** / **blender_viewport**（doctor/who/lease） |
| `dsh-blender-plugin/runtime/engine.mjs` | 直连引擎：addon socket 客户端 + `KERNEL_BOOTSTRAP`(K) + `COMMAND_CATALOG`(30) + act/cmd/frame/loop/perf/**view/headless** + 诊断/指标 |
| `dsh-blender-plugin/runtime/server.mjs` | 后端 HTTP：`/health /status /doctor /who /commands /frame.png /act /cmd /loop /perf /opt /view /headless /lease /release`（127.0.0.1:9877）+ 租约门禁 |
| `dsh-blender-plugin/runtime/runner.py` | **内环 runner v2**（Blender 侧）：timers 驱动 + 限额/急停 + penalize/anneal/候选表/导出；API 挂在 `K.dsh_loop_api` |
| `dsh-blender-plugin/runtime/perf.py` | 渲染性能预设 + 对象精简；API 挂在 `K.dsh_perf_api` |
| `dsh-blender-plugin/runtime/view.py` | **自定义视角捕获**（自建矩阵 + 离屏绘制 + 手写 PNG）；API 挂在 `K.dsh_view_api` |
| `dsh-blender-plugin/runtime/_probe_tools.mjs` | 诊断：列出当前可用命令与耗时 |
| `blender/README.md` | 工作区总说明：通道与工具、日常使用、限制与回滚 |

## 7. 渲染与集成的持久化设置（2026-09-13 一次性落地）

配置目录是 **`D:\DSH\blender\config`**（`launch-blender.ps1` 设 `BLENDER_USER_CONFIG` 指向此处，**不是** `%APPDATA%`）。

| 项 | 值 | 载体 |
|---|---|---|
| 默认渲染引擎 | **CYCLES**（原 EEVEE） | `startup.blend` |
| 渲染设备 | GPU · `compute_device_type=OPTIX` · 设备列表只勾 RTX 4060 的 OPTIX 行（CUDA 行与 CPU 行不勾） | `userpref.blend` + 场景 |
| 采样/降噪 | 4096 samples · 降噪开（Blender 默认） | `startup.blend` |
| 集成开关 | 五个全 `true` | `startup.blend`（scene 属性） |
| Hyper3D key | addon 自带免费试用 key（`MAIN_SITE`） | `startup.blend` |

**为什么以前 Cycles 走 CPU**（两个原因，缺一不可）：

1. `scene.cycles.device = "CPU"`（场景级，每个 .blend 各自保存，新文件默认就是 CPU）；
2. Cycles 设备清单为空（`preferences.devices` 长度 0）。根因是**枚举设备会崩**：Blender 会加载 Intel Level Zero 运行时 `C:\Windows\System32\ze_loader.dll`，在 `zeInitDrivers` 抛 `EXCEPTION_ACCESS_VIOLATION` → 整个进程崩溃（crash 文件已存证）。

**根治**（提权 PowerShell，日志 `D:\DSH\blender\logs\ze_fix.log`）：

    takeown /f C:\Windows\System32\ze_loader.dll /a
    icacls  C:\Windows\System32\ze_loader.dll /grant *S-1-5-32-544:F
    C:\Windows\System32\ze_loader.dll  ->  ze_loader.dll.bak-20260913

改名后枚举恢复正常，设备列表出现 `[4060/CUDA] [i5-13500HX/CPU] [4060/OPTIX]`。**回滚**：把 `.bak-20260913` 改回原名即可（需管理员）。该 DLL 属于 Intel DCH 显卡驱动包，本机没有 Intel 独显在跑，移除基本无副作用。

**端到端验证**（独立后台实例，读启动文件后**不改任何设置**直接渲染）：

    RTVERIFY2 engine=CYCLES cycles_device=GPU compute_device_type=OPTIX devices=[4060/OPTIX/true] flags=全 true
    RENDER_DONE seconds=1.4 engine=CYCLES device=GPU          ← 320x240 / 8 spp
    渲染期间 nvidia-smi --query-compute-apps 把 blender.exe 列为计算进程   ← GPU 实证

生效范围：`startup.blend` 只影响**新建文件**；既有 `.blend` 的引擎/设备是文件级属性，需各自设置一次。

## 8. 踩坑记录（按代价排序）

| # | 坑 | 现象 | 结论 / 做法 |
|---|---|---|---|
| 1 | **在 addon socket 线程调 GPU 枚举** | `ze_loader.dll` ACCESS_VIOLATION，Blender 整个崩（14:48:59） | 这类操作一律用 `bpy.app.timers` 排到主线程（见 §2.5） |
| 2 | **重启 Blender 丢未保存的 scene 属性** | 五个集成开关、API key 全回默认；第一次 `save_homefile` 存下的启动文件里 flags 全是 false | 设完开关立刻 `save_homefile`；重启后要复核 |
| 3 | `get_viewport_screenshot` 清理失败（**MCP 时代**） | 沙箱下 `/mnt/d` 只读 → `os.remove` 抛 EROFS，把已读到的帧连同异常一起丢（`Screenshot failed: [Errno 30]`） | 当时用 `blender_mcp_winpath.py::_install_cleanup_patch()` 降级为警告；**该 shim 已随 CLI/MCP 通道删除**，直连通道自带固定 filepath，从根上绕开 |
| 4 | 跨 OS 临时路径（**MCP 时代**） | 服务端造 `/tmp/x.png` 交给 Windows Blender → 写成 `C:\tmp\x.png`，Linux 侧查不到 → "Screenshot file was not created" | 当时靠 shim 换写成 Windows 路径；**现在帧一律由直连引擎指定 Windows 绝对路径** |
| 5 | 离屏保存写 UNC 静默失败 | `\\wsl.localhost\...` 目标"返回成功但没有文件" | 帧一律落 `D:\DSH\blender\tmp`（Windows 路径） |
| 6 | `execute_code` 无状态 | 上次定义的变量下次读不到 | 持久内核 `K`（`sys.modules["dsh_rt_kernel"]`） |
| 7 | 参数名/签名错配 | 传 `user_prompt` / `object_name` → `unexpected keyword argument` | 直连没有 schema 层，按 addon 真签名写（`get_scene_info` 无参、`get_object_info(name)`） |
| 8 | 后端进程残留 | 热重载后旧后端仍占 9877，路由是旧的 | `blender_viewport op=stop`（按 `/proc` 命令行兜底杀）+ `op=start` |
| 9 | 人肉面板的鼠标注入残留（已移除该功能） | 一次 `MIDDLEUP` 丢失 → 系统认定中键按住 → 全局点击失效 | 已删除注入通道；教训：注入输入必须带释放兜底 |
| 10 | **走 `bpy.data.images` 存离屏帧 → 颜色发白**（v0.4.0） | `.pixels` 是线性语义，帧缓冲读回已是 sRGB 8bit → 二次编码 | 直接手写 PNG（`view.write_png`）；实测清屏 25/153/51 解出仍是 25/153/51 |
| 11 | **离屏读回按 GL 行序 → 图上下颠倒** | 不翻转时"上"指向地面 | 写 PNG 时行倒序（`flip=True`）；用"左下角画红块"验证过落点 |
| 12 | **无头进程里 `GPUOffScreen` 抛 SystemError** | `requires the gpu module to be initialized` | 先 `gpu.init()`（无头专用；GUI 下已初始化，抛错忽略） |
| 13 | **租约把"只读"也挡了**（v0.4.0 初次实现） | 别人持租约时 `perf status` 也 409 → 连现状都看不到 | 只读 op 白名单豁免（见 §2.8） |

## 9. 相关文档

- **`blender/AI建模双层循环-方案.md`** —— 本会话分析的落地方案：外环（模型）定目标 + 内环（Blender）跑迭代，含 5 条守则、Cookbook、反模式与实测
- `blender/README.md` —— 工作区总说明（三条通道、日常使用、自检、回滚）
- `blender/AI建模双层循环-方案.md` —— 给 AI 建模用的双层循环方案（内环 runner + 五条守则 + Cookbook）
- `blender/接入检测报告.md` / `blender/可交互视口会话-方案.md` —— 早期检测与方案分析（含已移除的人肉面板方案）

