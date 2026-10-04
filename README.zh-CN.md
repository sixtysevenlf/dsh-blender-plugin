# DSH × Blender 直连实时插件 · 分享版

> 🤖 **想让 AI 帮你装？** 别让它猜 —— 把仓库地址和这句话一起发给它：
> 「先完整读 README 的 §2 前置条件 / §3 安装 / §8 常见故障 与 AGENTS.md，再动手；装完必须 `doctor` 回 `kind=ok`，没到就不算装好，失败按 §8 排查。」
>
> 现成提示词可直接复制：[docs/AI-安装提示词.md](docs/AI-安装提示词.md)

---

## 支持本项目 · 请我喝咖啡

这个插件是**开源、免费**的（BSD-3-Clause）：所有功能对所有人开放 —— 没有付费墙、没有授权码、也没有"赞助者专享版"。
如果它帮你省下了时间，可以请我喝杯咖啡：**这是自愿赞赏，不是购买**。

| 支付宝 | 微信赞赏码 |
|---|---|
| <a href="docs/images/sponsor-alipay.jpg"><img src="docs/images/sponsor-alipay.jpg" alt="支付宝赞赏码" width="280"></a> | <a href="docs/images/sponsor-wechat-reward.png"><img src="docs/images/sponsor-wechat-reward.png" alt="微信赞赏码" width="280"></a> |
| 打开支付宝 → 扫一扫 | 打开微信 → 扫一扫 |

**几句话先说清楚（避免误会）：**

- **赞赏 ≠ 购买**：不构成任何交易，不换取服务、授权、定制或优先响应；issue / PR 只看问题本身，与是否赞赏无关。
- **赞赏不改变许可**：代码始终是 BSD-3-Clause，无论赞赏与否功能完全一致。
- 二维码是**个人赞赏码**，只用于个人小额、自愿的赠与；**不接受**任何经营性 / 商业付款。需要商业支持、定制开发或合作，请开 issue 谈，走正规合同与发票。
- 请勿转发、二次发布这两个二维码，也不要用于本项目之外的任何用途。
- 图片就在仓库里（`docs/images/`），**点一下图可打开原图**（1080×1621 / 1027×1027）；若在中国大陆访问 GitHub 图片不稳定，直接在仓库里打开原图即可。

---

> 🌐 **语言 / Language：** [English](README.md) · **简体中文（本页）**
>
> 🧩 **配套 skill：[blender-modeling](https://github.com/sixtysevenlf/dsh-skill-blender-modeling)** —— 建模 / 装配的流程与判据（多 Agent 分工范式、参考图形体还原、六条坑、数值门装配审计）。
> 插件管"通道"，skill 管"怎么建、怎么验收"，两个一起用才完整。

> 让 **AI 模型真正驱动 Blender**：不用人点鼠标、不截屏喂图、不装 MCP 服务端，
> 通过一条 TCP 直连通道拿到 **15 个模型侧工具**：**看视口 / 改场景 / 连续观察 / 内环搜索 / addon 命令面 / 渲染优化 / 对象精简 / 无头跑重活 / 热无头会话 / 作业层 / 事务回滚 / 配方库 / 判定与验收（28 family · 183 op）/ 通道运维**。
>
> **版本 v1.0** —— 自带 runtime（node + python），包内路径全相对，**换机器不用改源码**；配置走 `runtime/config.mjs`（env → 配置文件 → 自动探测 → 默认）。
> **操作教程：[docs/操作教程.md](docs/操作教程.md) · 配置：[docs/配置参考.md](docs/配置参考.md) · 部署 SOP：[docs/部署SOP.md](docs/部署SOP.md)**

---

## v1.0 亮点（What's new in v1.0）

**1.0 = 能力冻结线**：15 个模型侧工具 + `blender_rt_plan` 的 28 个 family / 183 个 op 是对外稳定面；包版本（`package.json` 的 `1.0.0`）、工具描述前缀（`[v1.0.0] …`）与 npm 包名三者一致。相对上一版，**功能增量只有 macOS 支持**：跨 OS 路径改写退化为恒等（`/Users/…` 不再被拼成 UNC），`/Applications` 下的 `Blender*.app` 自动探测，工作目录默认 `~/.dsh-blender-rt`。

这一版对外承诺的能力面（正文逐条展开）：

- **建模 / 造型**：`sculpt_*` 程序化雕刻（numpy 位移笔刷 + 体素 / multires / dyntopo + 遮罩）· `fix_*` 网格修复闭环 · `uv_*` UV 四件套 · `sweep_*` 沿路径扫掠 · `shape_plan → shape_sections → shape_loft` 参考图还原 · `vehicle_*` 车辆外壳 · `material_build / material_apply / material_bake` 材质节点图与 bake。
- **判定 / 验收**：`audit_scene` / `audit_mesh`（网格体检）· `audit_gate`（装配门）· `audit_interference` / `audit_overlap`（干涉 / 重叠）· `qc_render_views` / `qc_compare`（对照图与 IoU）· `print_report`（薄壁 + 悬垂）· `deliver_export` / `deliver_verify`（OBJ/MTL + md5）· `motion_joints` / `motion_measure` / `motion_export_urdf` · `gate_plan(preset="assembly") → gate_run(spec_path=…)` 读 `verdict` 三态 · 契约层三态判定 `supported·refuted·unresolved`。
- **可发现性 / 稳定性**：`blender_rt_plan(op="catalog")` 三级渐进披露（插件本地直出；未知 op 回 `did_you_mean` 带骨架）· 渲染中主线程写命令秒回 `BUSY_RENDER`（`render_state / render_wait / render_reset`）· `runtime/offline_bootstrap.py` 离线逃生口 · 验收不再随规模退化（7 类面数门槛默认"不限、全跑"）。
- **addon 支持**：v1.7（protocol 11，19 常驻 + 15 集成门控）与扁平协议 v1.6 都通，`addonProtocol=auto` 自动适配。
- **回归**：`tests/capability.lock.json` 锁 28 / 183 / 15 · 路由契约静态 28/28、真机 70/70 · `npm test` 13 步 · macOS `npm run test:mac` 45 断言、GUI 通道 11/11。

---

## 1. 30 秒速览：这包能干什么

| 能力 | 工具 | 典型耗时 |
|---|---|---|
| 看一眼现在的视口 | `blender_rt_see` | 50–100 ms |
| **从任意角度出图**（不建相机、不动用户视口） | `blender_rt_see {from, look_at, …}` | 85–280 ms |
| 改一步 + 立刻验证 | `blender_rt_do {see:true}` | ≈105 ms |
| 判断"动没动 / 对不对" | `blender_rt_watch` | ≤6 帧/次（最多 32 帧采样） |
| **内环搜索**（几千次迭代不花模型回合） | `blender_rt_loop` | 160 tick/s |
| 透传 addon 任意命令 / 查命令面 | `blender_rt_cmd` / `blender_rt_commands` | 25–55 ms |
| 渲染性能诊断与优化预设 | `blender_rt_perf` | analyze 十几秒 |
| 对象精简（安全合并，几何零损失） | `blender_rt_opt` | ≈1.4 ms/对象 |
| **无头进程**跑重渲染 / 批量几何 | `blender_rt_headless` | 冷起 0.8 s |
| **热无头会话**（免冷启动） | `blender_rt_worker` | 复用同一个 `blender -b` |
| **长活后台化**（作业层） | `blender_rt_job` | `op=start` 立刻回 jobId |
| **事务 / 回滚** | `blender_rt_txn` | 快照 96.7 MB / 824 ms（300 对象） |
| **配方库** | `blender_rt_preset` | save / apply / export |
| **判定 / 验收 / 导出 / 造型** | `blender_rt_plan` | 307 对象：AABB 24 ms · BVH 0.17 ms/对 |
| 通道体检 / 租约 / 一键拉起 | `blender_viewport` | 体检 70–100 ms |

---

## 2. 前置条件（Requirements，5 条，缺一不可）

1. **Blender 4.x / 5.x（GUI 模式）** —— 本通道要的是"能看见的 Blender"；后台 `-b` 只用于无头工具那条线。
2. **Blender 里的 addon：`MCP for Blender`**（**不在本包内**，需自备）：它在 Blender 内起一个 TCP 服务，默认监听 `127.0.0.1:9876`；命令面实测 34 条（19 常驻 + 15 集成门控），至少提供 `ping`、`get_scene_info`、`get_world_state_snapshot`、`get_object_info(name)`、`get_viewport_screenshot(max_size, filepath, format)`、`execute_code(code)`；另有 5 个可选集成（PolyHaven / Hyper3D / Sketchfab / Poly Pizza / Hunyuan3D），开关由 addon 自己的 scene 属性控制。
   - **也支持 harveyxiacn 增强版 `blender_mcp_addon`**（协议不同，插件侧自动适配，配置项 `addonProtocol`，默认 `auto`；见 `docs/配置参考.md` §6）。该实现没有上面那 5 条资产集成通道，相关命令会明确报错。
   - 装好后：3D 视图按 `N` → 找到「MCP for Blender」面板 → **Connect**；兼容性自检 `node runtime/_probe_tools.mjs`（逐条探测命令，输出 ok / 耗时）；协议自检 `node tests/protocol_selftest.mjs`（不需要 Blender）。
3. **Node.js ≥ 20**（跑后端与插件宿主）。
4. **DSH（DeepSeek Harness）**：本包以 DSH 插件形态提供工具（`inject: ['tools']`），并按 `dsh.bundle` 约定声明装配（`cordis.patch.yml`）。
   - WSL 里跑 DSH、Blender 在 Windows：需要 WSL 互操作开启（默认开），`spawn` 才能直接起 `blender.exe`；两者同在 Windows：把 `blenderExe` 配成 `D:\...\blender.exe` 即可，路径映射自动退化。
   - **原生 Linux（非 WSL，Blender 与宿主同机）** —— v1.0.4 起支持。同机同 OS ⇒ 跨 OS 路径改写**不参与**（`/home/…` 原样交给 Blender），`blender_viewport op=doctor` 的 `distro` 报的是你自己的发行版；Blender 走 `which blender` 兜底，装在别处就配 `DSH_BLENDER_EXE`；工作目录默认 `os.tmpdir()/dsh-blender-rt`（见 `docs/配置参考.md` §5）。
5. **macOS** —— 已支持。宿主与 Blender 同机，不涉及跨 OS 路径映射：Blender 自动探测 `/Applications` 下的 `Blender*.app`（也扫 `~/Applications` 与 `/Volumes/*/Applications`），工作目录默认 `~/.dsh-blender-rt`。只有装在非标准位置时才需要配 `DSH_BLENDER_EXE` / `blenderExe`；`blender_viewport op=doctor` 会打印探测结果。

---

## 3. 安装（Install，5 步）

```bash
# ① 构建（需要指向一个 DSH 源码 checkout，用于链接 cordis/schemastery/dsh-tools）
cd dsh-blender-plugin
DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh          # 产出 lib/

# ② 配置（不设也能靠自动探测跑；要改就复制样例）
cp dsh-blender.config.example.json dsh-blender.config.json        # 见 docs/配置参考.md

# ③ 装到 DSH（本包已声明 dsh.bundle → 走官方 bundle 装配）
#    a) 软链进 <profile>/node_modules/@dsh-external/dsh-blender-plugin
#    b) profile package.json：dependencies 加 "link:<本目录绝对路径>"，
#       dsh.profile.bundles 加 "@dsh-external/dsh-blender-plugin"
#    c) 预演：dsh --profile <profile> --dump-config → 重启 DSH

# ④ 启动 Blender + Connect addon —— 两条路：
#    agent / 无人值守：blender_viewport(op="launch")   ← 写 boot 脚本 + spawn + 轮询 9876，幂等
#    人工：启动 Blender → 按 N →「MCP for Blender」→ Connect

# ⑤ 验证 —— **唯一验收判据：doctor 回 `kind=ok`**（没到 = 没装好，按 §8 排查）
curl -sS http://127.0.0.1:9877/health      # 后端活着 + 加载版本自证（provenance）
curl -sS http://127.0.0.1:9877/doctor      # ⭐ 真跑一次 bpy 往返 + 打印生效配置
curl -sS http://127.0.0.1:9877/who         # 租约 + 通道指标 + config
```

插件加载时会自动拉起后端（`node runtime/server.mjs`，默认 `127.0.0.1:9877`）并带 **15 秒看护**：进程被杀 / 宿主重启会自动拉起；`blender_viewport op=stop` 会暂停看护。

> ⚠ 插件是 **DSH 启动时加载**的模块：**升级后要重启 DSH** 才会在当前会话生效；只改了后端才用 `blender_viewport op=restart`。

---

## 4. 第一次使用（First contact）

```text
blender_viewport(op="doctor")                                # ① 体检：通道健康 + 生效配置
blender_rt_see(max_size=560)                                 # ② 看一帧：模型眼里的视口
blender_rt_see(from="9,-9,6", look_at="0,0,1")               # ③ 换个角度：不打扰你正在看的视口
```

期望：① `kind=ok`（含 `roundTripMs` / `ping_ms` / `config`）；②③ 返回内联图片 + 毫秒数与 hash。

---

## 5. 工具参考（Tool reference，15 个工具）

### `blender_rt_see` —— 看视口

取一帧 3D 视口（约 55–160 ms）。`max_size`（最长边，默认 560）· `full=true` + `area=N` 走整窗口 / 第 N 个区域截图。
给 `from` / `look_at`（`"x,y,z"`，可配 `lens` `ortho` `ortho_scale` `view_size` `shading` `overlays` `view_mode`）走**自定义视角**：自建矩阵离屏绘制 —— 不建相机、不改 `scene.camera`、不动用户视口；出图带 `coverage_estimate` 与 `frame_looks_empty` 自诊断（瞄空时给建议的 `from` / `look_at`）。同一视角 + 同一 hash 默认不重复附图，`force=true` 强制重发。

### `blender_rt_do` —— 做（+ 顺手看）

在 Blender 的 Python 里执行一段代码（`code=`）或直接跑一个 `.py` 文件（`file=`），`see=true`（默认）顺带回一帧 —— 整步约 105 ms，可在同一轮里连续做几十步。
预置 `bpy / math / mathutils / Vector` 与持久内核 `K`（`sys.modules["dsh_rt_kernel"]`，跨调用保留状态）；异常也回传 partial `stdout` / `stderr` / `traceback`。回执里的 `ms` 是**主线程占用**，>1 s 会提示改走 `blender_rt_headless` / `blender_rt_worker`。

### `blender_rt_watch` —— 连续观察

可选先跑一段 `code=`（例如 `bpy.app.timers` 驱动 / 播放动画），然后在 `seconds`（默认 2，0.2–20）内按 `fps`（默认 4，0.5–8）采样视口帧，内联返回均匀抽取的最多 6 帧 + 逐帧 hash（hash 相同 = 画面没变）。用于判断「它到底动没动、动得对不对」。

### `blender_rt_loop` —— 内环搜索

一次调用在 Blender 主线程跑几百到几千次迭代（`bpy.app.timers` 循环，主线程安全；迭代 / 时间双上限 + 急停）—— 模型只写目标与验收，机器去搜。`op=start | status | stop | board | export | help | bench`；`spec={setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every, patience}`，`measure` 必须给 `ns["score"]`。
**什么时候用（看任务形状，不看次数）**：对齐 / 拟合 / 反推尺寸 / 参数扫描 —— 判据能写成一个数（轮廓 IoU、包围盒尺寸误差、剖面差，或你自写的 score），候选几十~几千组。只试 ≤5 组 → 用 `blender_rt_do` 自己循环更省事；**无头（`blender -b`）下 `bpy.app.timers` 不触发，内环只在 GUI 会话有效**。内环只优化你写的目标函数 —— 收敛后必须换另一条计算通路复核 + `blender_rt_see` 视觉确认（防 Goodhart）；注意跑完场景停在**最后一次迭代**的参数上，不是 best（要 best 用 `op="export"`）。
**不想手写 setup/step/measure？** 用声明式：`blender_rt_plan(op="shape_search", args={objective:"silhouette_iou", ref:"D:/ref/side.png", view:{…}, apply:"ob.location.y = p['dy_mm']/1000.0", params:{dy_mm:{min:-30,max:30,step:5}}, iterations:300, export:"D:/out/best_fit.py"})` —— 目标 + 区间进，候选表 + 可复现脚本出（内部跑同一条内环；`dry_run=true` 只校验并回 spec）。长尾说明见 `blender_rt_plan(op="catalog", args={tool:"rt_loop"})`。

### `blender_rt_cmd` —— 透传 addon 任意命令

`name`（必填）+ `params` + `timeout_ms`（默认 120000）。含 MCP 层不暴露的 `get_world_state_snapshot` / `drain_human_activity` / `get_telemetry_consent` / `set_telemetry_consent` / `get_addon_info`，以及资产类命令（PolyHaven / Sketchfab / Poly Pizza / Hyper3D / Hunyuan3D）。
参数必须匹配 addon 真实签名：`get_scene_info` 无参、`get_object_info` 用 `name`（不是 `object_name`）、不要传 MCP 才有的 `user_prompt`。把 plan op（如 `audit_mesh`）误发到这里会被**本地拦下**并指回 `blender_rt_plan`。

### `blender_rt_commands` —— 查命令面与集成状态

列出直连通道当前可用的 addon 命令（实测 34 条 = 19 常驻 + 15 集成门控）与 5 个集成的真实状态（开关是否打开、是否缺 API key）。调 `blender_rt_cmd` 之前先用它 —— 命令面随 addon 版本变化，以实时输出为准。

### `blender_rt_perf` —— 渲染性能

`op=status | analyze | apply | revert | help`。`analyze` 差分实测「每轮同步 / 每采样 GPU 成本」（占主线程十几秒）；`apply` 应用预设（`persistent_data` / OptiX 降噪 / `denoising_use_gpu` / `auto_tile off` / 采样上限）；`revert` 还原 `apply` 之前。参考数字（2318 对象场景）：每轮 CPU 侧同步 ≈4.2 s；开 `persistent_data` 后重复渲染 **14.6 s → 0.79 s**。

### `blender_rt_opt` —— 对象精简

`op=analyze | join | help`。`analyze` 列出可安全合并的分组与预计节省；`join` **默认 `dry_run=true` 只报告**，`dry_run=false` 才真合并（强烈建议同时给 `save_before=<.blend 绝对路径>` 先存回退点）。
合并规则：同集合 / 同材质 / 同父级 / 无修改器 / 无动画 / 无形态键 / 无自定义属性 / 无实例 / 非库链接；合并后**几何零损失**（面数与顶点数不变）。

### `blender_rt_headless` —— 无头进程（第一路径）

独立进程跑脚本（`blender.exe -b`）：不占 GUI 通道、不动你在看的场景；批量几何 / 校验 / 渲染 / 不需要"人在回路看视口"的活都先走这里。
参数：`script` · `script_file` · `file`（.blend；传 .py 会自动当脚本）· `outdir` · `as_job` · `wait_s` · `timeout_ms` · `engine`（`eevee` 默认 / `cycles` / `keep` / `none`）· `preload` · `shots` · `out_json` · `env` · `args` · `workdir` · `factory_startup` · `use_user_config` · `bootstrap` · `gpu` · `include_noise`。
脚本里 `print("HEADLESS " + json.dumps(obj))` 回传（**必须单行 JSON**）；结构化结果用 `out_json=`（>4 KB 会自动落 `<workdir>/results/<runId>.json`）。**>100 s 或长渲染：直接 `as_job=true`**；否则等待窗口（`DSH_HEADLESS_WAIT_MS`，默认 100 s）到点会回 `{kind:"promoted", jobId:"run-…"}` —— 超时 ≠ 失败，用 `blender_rt_job(op="wait"/"collect", id=…)` 收。

### `blender_rt_worker` —— 热无头会话

常驻 `blender -b`：`op=start | exec | status | stop | restart | list`；`code` · `timeout_ms`（默认 120000）· `gpu` · `engine` · `purge_prefix` · `name`（实例名，默认 default）。复用**同一个 Blender 会话与持久内核 `K`**，免去每次 1.1–1.5 s 冷启动 + EEVEE 着色器编译（最多约 16 s）。
**何时用（量化）**：同一脚本要跑 ≥3 次，或单次 >10 s 且要反复迭代；一次性脚本 / 要 GUI 上下文 / 要多进程并行 → 继续用 headless。串行、无窗口：依赖 GUI 的 `bpy.ops` 可能失败。

### `blender_rt_txn` —— 事务 / 回滚

`op=snapshot | restore | list | prune | mark | revert | marks | drop | help`。**文件级**：`snapshot` / `restore`（整场景回退；写 .blend 副本，`copy=True` 所以不改当前 filepath；300 对象工程实测 96.7 MB / 824 ms）；**对象级**：`mark` / `revert`（只记 transform / 材质槽 / 可见性 / 修改器开关，毫秒级、就地回滚）。
⚠️ 边界：对象级**不含拓扑 / UV / 顶点级改动** —— Boolean、合并、删面之后回不去（`revert` 会跳过并报告），那种回滚请用文件级 `snapshot` / `restore`（`restore` 会丢掉当前未保存状态）。

### `blender_rt_preset` —— 配方库

`op=save | list | get | apply | delete | export | import | help`。`data` 用点路径表达：材质节点 `{"inputs.Base Color": [1,0,0,1]}`、对象属性 `{"location": [0,0,1]}`、场景设置 `{"render.resolution_x": 640}`。`apply` 的 `targets` 用 `MAT:材质名` / `OBJ:对象名` / `SCENE`（逗号分隔）；不给 `targets` 只预览（dry_run），给了就实际写入并回报 applied / skipped / errors。`export` / `import` 走单个 JSON bundle，便于把配方分享到别的机器或会话。

### `blender_rt_job` —— 作业层

长活后台化：`op=start` 立刻返回 `jobId`（不占客户端连接、不会被工具超时掐断）；`status | collect | wait | kill | list`。`wait` 一次拿到结构化结果（默认 120 s / 次，上限 600 s）—— 别再连发 `status`（会撞宿主的重复调用检测）。
与 headless 的分工：预期 <100 s 用 headless 直接拿结果；更长或已知要跑很久 → headless 的 `as_job=true`，或本工具 `op=start`。run 与 job **同一 id 空间**（headless 的 `run-…` 也能用 `status` / `collect` / `wait` / `kill` 收）；日志落 `<outdir>/jobs/<id>/`。

### `blender_rt_plan` —— 判定 / 验收 / 导出 / 造型（28 family · 183 op）

所有 family 与 op 都在这一个工具里。**不确定用哪个就先问目录**：`op="catalog"`（插件本地直出，不占 Blender 往返）→ 单族展开 `args={family:"audit"}` → 算子细节用各 family 自己的 `<family>_help`。
常见入口：`audit_scene` / `audit_mesh`（网格体检）· `audit_gate`（出厂门：连通 + 包络）· `audit_interference` / `audit_overlap`（干涉 / 重叠，可跨 .blend）· `qc_render_views`（对照图 / 多视角 / 逐部件配色，长活 `asJob=true`）· `qc_compare`（IoU / 剖面差）· `sculpt_scan → sculpt_setup → sculpt_apply` · `fix_repair` · `uv_smart_project` / `uv_unwrap` / `uv_pack` · `print_report`（薄壁 + 悬垂，回执带 `resolution_mm`）· `sweep_analyze → sweep_build` · `material_build` / `material_apply` / `material_bake` · `deliver_export` / `deliver_verify`（OBJ/MTL + md5 清单）· `motion_joints` / `motion_measure` / `motion_export_urdf` · `generator_save` / `generator_run`（`save` 用 `code=`，`run` 的参数走嵌套 `args=`）· `gui_frame` / `gui_shading`（真 UI 上下文才做得到的事）· `render_state` / `render_wait`。
契约层（假设 / 区间 / 三态判定 `supported·refuted·unresolved` / `destructive_guard` / 证据账本 md5）判据见 [docs/假设驱动建模-cookbook.md](docs/假设驱动建模-cookbook.md)。写 op 过写租约；只读 op（`catalog` / `audit_*` / `print_*` / `qc_*` …）豁免 —— 任何租约状态下都能问目录。op 名拼错回 `did_you_mean`（带骨架）；怀疑加载的是旧版：`op="catalog", args={verify:true}`。

### `blender_viewport` —— 通道运维

`status`（健康 / 视口区域 / 计数 / 版本）· `doctor`（真跑一次 bpy 往返的三级体检：未连 / 主线程忙 / addon 线程卡死 + 修法）· `start` / `stop` / `restart`（后端进程与 15 s 看护）· `who` / `lease` / `release`（写租约）。
`launch`：一键拉起 GUI Blender 并自动 Connect addon —— 写 boot 脚本 → detached spawn → **轮询 addon 端口**（唯一可信判据）→ 顺手 doctor；幂等，支持 `wait_ms` / `file` / `exe` / `addon_module` / `addon_file` / `dry_run`。

### 通道语义（15 个工具共用）

- **只有一条 TCP 直连通道**：后端 `127.0.0.1:9877` → addon socket `127.0.0.1:9876`；插件加载时自动拉起后端并带 15 秒看护。
- **持久内核 `K`**：`execute_code` 每次是新命名空间，状态要挂在 `K` 上（`sys.modules["dsh_rt_kernel"]`）。路径助手 `K.win_path / K.wsl_path / K.blend_path / K.out_dir`（GUI 与无头通用），另有 `K.run(path, reload_modules=True)`。
- **回执两段式**（headless / job）：第 1 个 text block 是单行 JSON 信封（`status` / `resultJson` / `resultPath` / `stdoutTail` / `inputFile` / `shots` / `pathWarnings` …），第 2 个是人读摘要。
- **超时 ≠ 失败**：客户端断开 / 宿主取消（`exec.signal`）都**不会**杀掉 Blender 侧的子进程，产物照旧落盘；长活按 `runId` / `jobId` 回收。
- **写租约**：写路由自动带 `holder`，别的会话持有时返回 409（只读 op 豁免，`force=true` 可抢）；持有者进程已死时按 `plugin-pid-<pid>` 探活自动回收，不再挡到 TTL 结束。
- **渲染中别硬发**：渲染开始 / 结束由 `bpy.app.handlers` 写 / 删磁盘标记，渲染中的主线程写命令**秒回 `BUSY_RENDER`**（用 `render_wait` 等它，或改走 `rt_job`）；陈标记 15 min 自动放行（`DSH_RENDER_STALE_MS` 可调）。
- **回执出口统一消毒**：所有工具返回值都过一遍 lossless 消毒（`undefined` → 丢键、非有限数 → `null`、`-0` → `0`），避免宿主的 lossless-JSON 门拒收整条工具通道；有改动会在回执里点名。
- **轨迹**：每次顶层调用写一行 `<工作目录>/trajectory/blender_rt-<日期>.jsonl`（route / op / ms / ok / 参数摘要；超 16 MB 轮转，`DSH_TRAJ=0` 关）。

---

## 6. 实战配方（Recipes）

1. **改一步看一眼**（最常用）—— `blender_rt_do(code="bpy.data.objects['Cube'].rotation_euler.z += 0.4", see=True)`：一批做 10–30 步，再拉开距离看一次；画面没变时不会重复附图（省视觉 token）。
2. **多角度验收（不打搅用户）** —— `blender_rt_see(from="9,-9,6", look_at="0,0,1", view_size="1280x720")`：同一组参数出图稳定（hash 可当验收指纹）；要对照参考图就 `blender_rt_plan(op="qc_render_views", args={ref_path:…})`，IoU 与剖面差由 `qc_compare` 算。
3. **内环拟合（搜索跑在 Blender 侧）** —— `blender_rt_loop(op="start", spec={setup:…, step:…, measure:…, iterations:3000, budget_ms:20000, minimize:true, top_k:20})`，再 `op="board"` 看候选 / `op="stop"` 急停 / `op="export"` 导出可复用脚本。`measure` 里写 `ns["score"]`，约束用 `penalize`、步长用 `anneal`；**收敛后换一条通路复核**（例如 `blender_rt_do` 重量一遍）+ `blender_rt_see` 看一眼。
4. **渲染性能流水线** —— `blender_rt_perf(op="status") → op="analyze" → op="apply" → op="status"`（不满意就 `op="revert"`）：`apply` 会设 `persistent_data`、按本机 GPU 自动选降噪器、关 `auto_tile` 并给采样上限；对象太多再走配方 5。
5. **对象精简** —— `blender_rt_opt(op="analyze")` 先看候选分组与预计节省 → `blender_rt_opt(op="join", args={dry_run:false, save_before:"D:/.../xxx_before_join.blend"})` 真合并（几何零损失）；合并前后对拍面数 / 顶点数。
6. **无头成品渲染 / 批量出图** —— `blender_rt_headless(file=…, outdir=…, engine="cycles", timeout_ms=900000, as_job=True, preload="view,perf")`，再用 `blender_rt_job(op="wait", id="job-…", timeout_ms=600000)` 收结果；预期 >100 s 就别赌等待窗口。
7. **事务 / 回滚** —— 拓扑类改动走文件级 `blender_rt_txn(op="snapshot", label="before_bool")` → 改 → `op="restore"`；只改一处再对比走对象级 `op="mark", objects="Cube"` → `op="revert"`（拓扑改动会被跳过并报告）。
8. **从参考图到交付** —— 先 `blender_rt_plan(op="shape_plan", args={object_class:"furniture"})` 拿该类别的还原协议（要哪些视图 / 盯哪些比例 / 配哪些算子），再 `op="shape_sections"` → `op="shape_loft"` 放样成壳；验收 `op="qc_render_views", args={ref_path:…}` / `op="qc_compare"`（IoU / 剖面差）；交付 `op="uv_smart_project"` → `op="material_build"` → `op="material_bake"` → `op="deliver_export"` → `op="deliver_verify"`（OBJ/MTL + md5 清单）。

---

## 7. 配置（Configuration）

解析顺序：**环境变量 → 配置文件 → 自动探测 → 内置默认**。

| 设置 | 环境变量 | 配置键 | 默认 |
|---|---|---|---|
| 工作目录（帧 / 脚本 / 台账 / results） | `DSH_BLENDER_WORKDIR` | `workDir` | `%LOCALAPPDATA%\dsh-blender-rt`（macOS `~/.dsh-blender-rt`；WSL 下映射成 `/mnt/c/…`） |
| Blender 可执行文件 | `DSH_BLENDER_EXE` | `blenderExe` | 自动（Program Files / Steam / `/Applications` / `PATH`） |
| addon 地址 / 端口 | `DSH_BLENDER_ADDON_HOST` / `DSH_BLENDER_ADDON_PORT` | `addonHost` / `addonPort` | `127.0.0.1` / `9876` |
| addon 协议 | `DSH_BLENDER_ADDON_PROTOCOL` | `addonProtocol` | `auto`（可显式 `ahujasid` / `category-action`） |
| 后端端口 | `DSH_BLENDER_HTTP_PORT` | `httpPort` | `9877` |
| 写租约身份 / TTL | `DSH_BLENDER_HOLDER` / `DSH_BLENDER_LEASE_TTL_MS` | `holder` / `leaseTtlMs` | `plugin-pid-<pid>` / 600000 ms |
| 热无头 worker 端口 | `DSH_BLENDER_WORKER_PORT` | `workerPort` | `9879` |
| 无头等待窗口 / 自动转作业阈值 | `DSH_HEADLESS_WAIT_MS` / `DSH_HEADLESS_AUTO_JOB_MS` | — | 100000 ms / 关闭 |
| Blender 用户配置 / 脚本目录 | `DSH_BLENDER_USER_CONFIG` / `DSH_BLENDER_USER_SCRIPTS` | `blenderUserConfig` / `blenderUserScripts` | `null`（无头进程默认读不到用户偏好） |

配置文件位置：`$DSH_BLENDER_CONFIG` → `<包根>/dsh-blender.config.json` → `~/.dsh/dsh-blender.config.json`（样例见 `dsh-blender.config.example.json`）。随时查**生效**配置：`curl -sS http://127.0.0.1:9877/who`（看 `config` 段）或 `blender_viewport(op="doctor")`。完整说明：[docs/配置参考.md](docs/配置参考.md)。

> `/doctor` 里 `config.workDir.shared=false` 表示该目录 Windows 侧看不见（例如 `/tmp` 这类独立挂载）—— 无头脚本会被自动换到共享目录，并在回执里给 `WORKDIR_NOT_SHARED` 警告。

---

## 8. 常见故障（Troubleshooting）

| 症状 | 先跑这个 | 多半是 |
|---|---|---|
| 工具报"后端不可用" | `blender_viewport op=start` | 后端进程没起来 / 端口被占（`EADDRINUSE` 会点名僵死进程占端口） |
| `blender-unreachable` | `blender_viewport op=launch`（一键拉起 GUI + 自动 Connect） | Blender 没跑，或 addon 没 Connect |
| `main-thread-busy` | 等它空下来，或改无头 | Blender 主线程被渲染 / 模态操作占住 |
| `BUSY_RENDER` | `blender_rt_plan(op="render_wait")`，或改走 `rt_job` | 正在渲染（写命令被当场拦下，不再排队到超时）；陈标记 15 min 自动放行 |
| `addon-thread-stuck` | 别连发，等它结束 | 上一条长命令还在跑（`blender_rt_loop op=stop` 可急停内环） |
| `addon-thread-stuck` 且带 `detected_protocol` | 按提示改 `addonProtocol` 后 `blender_viewport op=restart` | 装的是另一种 addon，协议不匹配（扁平 vs category/action） |
| 出图报路径错误 | `blender_viewport op=doctor` 看 `config.workDir`（含 `shared`） | 工作目录两端不互通（改 `workDir`） |
| 写操作 409 `leased` | `blender_viewport op=who` | 另一会话持有写权限租约（`op=lease force=true` 可抢） |
| `blender_rt_headless` 起不来 / `blender-exe-missing` | 看 `config.blenderExe` | 没找到 blender.exe（三种配置方式见 `docs/配置参考.md`） |
| 工具返回 `value is not lossless JSON` | 重建 `lib/` 后**重启 DSH** | 加载的是旧一代 lib（插件是 DSH 启动时加载的模块） |
| `blender_viewport op=launch` 等不到端口 | 读回执里的 `statusFile`（`launch-status.json` 的 boot 结论） | addon 没被扫到（试 `addon_module` / `addon_file`）/ exe 路径不对 |
| `/health` 的 `provenance.stale=true` | `blender_viewport op=restart` | 磁盘上的 runtime 已改，进程还是旧一代 |

失败分三级判读（`/doctor` 的 `kind`）：TCP 不通 → Blender / addon 没起来；TCP 通但 bpy 调用超时 → 主线程忙；TCP 通而 `ping` 也不回 → addon 客户端线程卡死。更多在 [docs/操作教程.md](docs/操作教程.md) §5。

---

## 9. 目录结构（Repository layout）

```text
dsh-blender-plugin/
├── package.json · cordis.patch.yml · dsh-blender.config.example.json   # 包定义 / bundle 装配 / 配置样例
├── tsconfig.json · scripts/build.sh   # 构建：链接 checkout 依赖 + tsc → lib/
├── src/index.ts                       # host：15 个工具 + 后端看护 + 租约 + 回执消毒
├── runtime/                           # 自带 runtime（node + python，包内路径全相对）
│   ├── config.mjs · paths.mjs         # env → 配置文件 → 自动探测 → 默认；跨 OS 路径
│   ├── engine.mjs · server.mjs        # 直连引擎（addon socket 客户端 + 路由）与后端 HTTP 9877
│   ├── catalog.mjs · classes.mjs      # op 目录（28 family · 183 op）与 12 类建模指引
│   ├── kit.py                         # 共享内核：JSON / API 样板、单位、几何、度量、回执骨架
│   ├── view.py · runner.py · perf.py  # 自定义视角捕获 · 内环 runner v2 · 渲染性能预设 + 对象精简
│   ├── contract.py · planner.py       # 契约层（假设 / 区间 / 三态判定 / 证据账本）· 对象图规划器
│   ├── audit.py · gate.py · deliver.py · qc.py · qc_render.py   # 体检 / 干涉 / 装配门 · 交付 · 对照图与多视角
│   ├── sculpt.py · mesh_fix.py · uv_tools.py · printcheck.py · sweep.py · material.py · render_guard.py
│   ├── motion.py · vehicle.py · generator.py · shapegen.py · human.py · imgtools.py · clearance.py
│   ├── txn.py · presets.py · worker.py   # 事务快照 · 配方库 · 热无头会话（常驻 blender -b）
│   ├── launch.mjs · staging.mjs · proc_env.mjs · backend_probe.mjs · addon-protocol.mjs
│   └── offline_bootstrap.py           # 离线逃生口：后端挂了也能在 blender -b 里直接用能力
├── docs/                              # 教程 / 配置 / 判据 / 评估（随包分发）
│   ├── 操作教程.md · 配置参考.md · 部署SOP.md · AI-安装提示词.md
│   ├── 假设驱动建模-cookbook.md · 证据分级与读图纪律.md
│   ├── headless优先-路线决定.md · EEVEE-工作要点.md · Procedura-融合分析.md
│   └── examples/chair-backrest/       # 可复跑示例（约 3 s）+ 归档结果与证据图
└── tests/                             # 可复跑验证（离线 + 真机两档，见 §10）
```

---

## 10. 自检（Self-tests）

完整清单见 [tests/README.md](tests/README.md)。离线（不需要 Blender）与真机两档：

**① 离线回归（一条命令，13 步）**：`npm test` —— 判据是**退出码 0**（别用 grep 判"失败"字样，会漏掉静默失败）。
单跑入口：`npm run test:discover`（工具可发现性 51 项）· `test:lease` · `test:workdir`（14 项）· `test:provenance`（16 项）· `test:generator`（13 项）· `test:dedupe` · `test:mac`（45 项）· `test:lossless`（回执出口消毒）· `test:routes`（路由契约）。

**② DSH 升级后先跑这三条**（工具是用宿主的 `defineTool` 声明的，契约一变就是"15 个工具整体消失"）：

```bash
node tests/protocol_selftest.mjs         # addon 协议层（28 项，不需要 Blender / DSH）
node tests/dsh_api_compat_probe.mjs      # 15 个工具 + 15 个静态 timeoutMs + 0 报错
blender_viewport(op="doctor")            # 回执里的「宿主 API：dsh-tools@<版本> @ <路径>」是自证
```

**③ 真机（需要 Blender 在跑）**：`npm run test:acceptance`（端到端验收）· `npm run test:gui`（GUI 通道探针，真机 + addon）；
或直接跑 Blender 侧脚本：`blender -b --factory-startup --python tests/contract_selftest.py -- /tmp/contract_out`（契约层）、`tests/plan_selftest.py`（规划器）。
各能力族都有自己的 `*_selftest`：`mate`（配合门）· `txn`（事务 / 编辑协议）· `deliver`（导出与 md5）· `motion`（关节与运动学扫掠）· `qc`（IoU 与对齐）· `upstream_integration`（雕刻 / 修复 / UV / 制造 / 扫掠 / 材质 / 渲染 guard 一条链）· `view_diag`（自定义视角诊断）；用 `blender_rt_headless(preload=…, script_file=…)` 或 `blender -b --python <文件>` 跑。

**④ 通道三条 `curl`**（同 §3 第 ⑤ 步）：`/health`（含加载版本自证 `provenance`）· `/doctor`（`kind=ok`）· `/who`（租约 + 指标 + 生效配置）。

---

## 11. 安全、许可与致谢（Security, license, credits）

- 本包**不含** Blender addon 本体（`MCP for Blender`），也不含任何模型 / 贴图资源；addon 需自备并遵守其自身许可。
- 通道只监听 `127.0.0.1`（默认），**不要**对外网开放；`execute_code` 是有意留下的"万能通道"—— 它会在 Blender 进程里跑任意 Python，请只在可信环境使用。
- 不含人肉面板 / MJPEG 推流 / 鼠标键盘注入（这条路线已明确移除：注入输入容易把系统按键状态搞坏）。
- 写通道有租约保护：别的会话持有时写路由返回 409（只读 op 豁免），`force=true` 才抢。
- 许可：**BSD-3-Clause**（见 [LICENSE](LICENSE)）；版权人写在 `LICENSE` 里，需要不同署名就自己改。
- 作者机器的实测数据（帧延迟、矩阵偏差、租约用例等）保留在 `docs/操作教程.md` 与 `tests/README.md`，可当作你环境的对照基线，不是保证值。

### 致谢 / Credits

- **@lurenjia-l** — [dsh-blender-stylized-shading](https://github.com/lurenjia-l/dsh-blender-stylized-shading)：其 `material_pipeline.md` 的 EEVEE 实测踩坑（Shader to RGB 只含直接光、漫射拓展光源压暗、渐变组控制器绑定）已并入本插件 `docs/EEVEE-工作要点.md`；其 `stylized-shading` 技能已适配本插件直连通道（见 `~/.dsh/skills/stylized-shading/`）。
- **@yihefeikong-rgb** — addon 协议适配（`addonProtocol`，默认 `auto`）：扁平协议的官方 `MCP for Blender` 与 `harveyxiacn/blender-mcp` 的 category/action addon 都能用（PR #2）。
- 契约层与判定口径的移植评估参考 [SpatiaOS/Procedura](https://github.com/SpatiaOS/Procedura)（MIT）—— 只搬判据与算法，不搬技术栈；见 `docs/Procedura-融合分析.md`。
