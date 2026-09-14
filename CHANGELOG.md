# CHANGELOG — @dsh-external/dsh-blender-plugin

## v0.8.0（2026-09-14）—— 默认渲染引擎改为 EEVEE + 光追（Cycles 可选）

- 新增 engine 参数（默认 eevee）：设 BLENDER_EEVEE + use_raytracing=True + ray_tracing_method=SCREEN + 阴影质量 + taa_render_samples；回执新增 engine / engineMode 与引擎行。
- cycles 保留原 OptiX 设备前导；keep 不动设置（向后兼容 gpu:"false"）。
- 为什么换：EEVEE 走图形后端，不依赖 compute_device_type 这类偏好 → 无头不会像 Cycles 那样静默回落 CPU（实测 15.4×）。实测（360 对象 / 512×512 / 64 采样）：EEVEE+RT 预热帧 1.35 s vs Cycles GPU 3.13 s = 2.3×。
- 代价：首帧着色器编译（冷 ~16 s，缓存热 ~2.4 s）→ 迭代请用 blender_rt_worker 热会话。
- rt_perf 引擎感知：EEVEE 下按 EEVEE 预设并显式列出被跳过的 Cycles 专属项；status 回传 engine / engine_mode / eevee 详情。
- QC 记录引擎：比对结果新增 engine_at_qc（如 BLENDER_EEVEE+RT）——判据只在同一引擎内可比。
- 文档瘦身：删除 docs/作者自用版-README.md、docs/AI实时交互Blender-通道说明.md、docs/AI建模双层循环-方案.md（有效内容并入 操作教程.md / 配置参考.md），自用版与仓库版文档集一致。


> 其他会话/agent 请先读这里，再看 `MIGRATIONS.md`（路径变更）与 `README.md`（用法）。

## v0.7.0（2026-09-14）—— 事务/回滚 + 内置 QC（含算法优化）+ 异常可观测性 + 路径桥接

> 来源：plush-build 会话（从零重建毛绒玩偶）的使用反馈；逐条核实见 `反馈分析-毛绒线-plush-build.md`，算法细节见 `QC算法优化-说明.md`。**工具数 12 → 13**（新增 `blender_rt_txn`）。

**① 异常可观测性（反馈里的第 1 痛点）**
- `rt_do` / `/act` 用 `DSH_ACT_OK`/`DSH_ACT_ERR` 包裹：**异常时也回传 partial stdout / stderr / traceback**。实测 `print(DIAG-1) … raise` → `stdout="DIAG-1 …"` + traceback 尾行（此前只剩一句 error）。
- 修掉误导性诊断：`diagnosis` 只在**传输/超时类**错误上附加，执行类错误给 `errorKind:"execution"`（此前抛错会被误报成 `main-thread-busy`）。

**② 路径桥接（GUI 通道也能用）**：内核新增 `K.win_path / K.wsl_path / K.blend_path / K.out_dir / K.workdir`（`/mnt/d/x ↔ D:\x`、WSL 内部 `↔ \\wsl.localhost\...`、`//rel → 相对 .blend`）。

**③ 跑文件一等公民**：`blender_rt_do(file=...)` 直接执行工作区脚本；内核新增 `K.run(path, reload_modules=True)`。

**④ 事务 / 回滚**：新增 `runtime/txn.py` + 工具 **`blender_rt_txn`** —— 文件级 `snapshot/restore`（`copy=True` 不动当前 filepath；300 对象工程实测 **96.7MB / 824ms**）、对象级 `mark/revert`（实测 移动到 (5,0,0) → revert → **回到 (0,0,0)**）。对象级**不含拓扑/UV/顶点改动**（工具描述里写明）。

**⑤ 内置 QC + 算法优化**（`runtime/qc.py`，以 plush-build 的 539 行脚本为原型）
- 四项优化：**自适应掩膜**（alpha 智能判定 / 边框估背景 + Otsu，不再手调阈值）、**质心对齐 + 尺度平移搜索**、**可行动指标**（Dice / 缺面积 / 多面积 / 边界距离 / 剖面差）、**防刷分**（`iou` 与 `iou_fixed` 双报 + 尺度漂移告警 + 内环强制固定对齐 + 并集显式计入越界像素）。
- 验证：合成自检 自比 **1.0000**、平移 12px 仍 **1.0000**；真图扰动 直接比 **0.534** → 搜索后 **0.866**；五视图 **0.776–0.890**（对照他们的 0.688–0.790；未完全复现其数字的原因见说明文档）。
- 入口：`blender_rt_plan(op="qc_compare" / "qc_compare_basic" / "qc_self_check" / "qc_robustness_check" / "qc_help")`；产物 = 叠加图 + 三联对照图。

**⑥ 内环 measure 预置**：`K.dsh_measure = {aabb_err, silhouette_iou, profile_err}`。
**⑦ 主线程占用提示**：`rt_do` 的 ms > 1 s 时提示改走 `blender_rt_headless` / `blender_rt_worker`。
**⑧ 附带修复**：模块注入改为**内容指纹**（此前只查 `hasattr` → 改了模块文件在同一会话里不生效）。

## v0.6.0（2026-09-14）—— 按外部使用反馈加固：GPU 语义 / 热无头会话 / 长任务流式 / 结构化失败 / 产物过滤

> 来源：另一位 AI 用本插件跑 22 轮建模（45 次流水线 + ~40 次诊断）后的反馈；逐条核实与实测见 `反馈分析-其他AI使用体验.md`。
> 工具数 11 → 12（新增 `blender_rt_worker`）。

**① GPU 语义（★★★，最贵的一条）**
- 无头进程默认读不到用户偏好 → Cycles **静默回落 CPU**。本机实测（1821 对象工程，480×270/32spp）：
  CPU 2.78 s vs GPU 首帧 1.43 s / 预热 **0.18 s = 15.4×**（外部反馈报 5×，实际更悬殊）。
- 新增 `gpu` 参数（`auto` 默认 / `true` 强制 / `false` 关闭）：`auto` 会按 OPTIX→CUDA→HIP→ONEAPI→METAL 顺序配好
  `prefs.compute_device_type` + 勾选 GPU 设备 + `scene.cycles.device="GPU"`，并**回传 `gpu` 字段**（before/after/configured/fell_back_to_cpu）——"静默"这个属性被彻底干掉；
  `gpu:"true"` 时若确实没有 GPU 后端 → `ok=false`（不静默）。
- 新增 `use_user_config` 参数：透传 `BLENDER_USER_CONFIG` / `BLENDER_USER_SCRIPTS`（配合 `factory_startup:false` 才有意义）。

**② 热无头会话 `runtime/worker.py` + 工具 `blender_rt_worker`（★★★）**
- 常驻 `blender -b` 进程：**阻塞 accept 跑在主线程**（无头下 `bpy.app.timers` 实测 0 次触发，主线程执行 bpy 才安全），
  复用**持久内核 K**（同一套 `sys.modules["dsh_rt_kernel"]` 语义），op：`start / exec / status / stop / restart`。
- 起步即带 GPU 前导；串行语义（一次一个请求，长代码会占住 worker —— 这是"热"的代价也是安全的来源）；与 GUI 通道互不干扰。
- 实测：`start` → pid/port/GPU 就绪；`exec ×2` → `K.n` 1→2（**跨调用状态保留**）；`status` 给出 uptime/calls/objects/kernel 模块；`stop` 干净退出。

**③ 长任务流式（★★）**
- 根因实测：客户端 `fetch` 的响应头超时 **300 s**（独立实验：延迟 330 s 才发响应头的服务 → `fetch failed after 300.9s，cause=UND_ERR_HEADERS_TIMEOUT`）。
- `/headless`、`/worker` 改为 **ndjson 流式**：响应头立刻返回 + 每 15 s 心跳 + 最后一行才是结果 → 任意时长（心跳 < 300 s）不再被客户端超时掐断。
- 另一个实测结论：客户端断开**不等于任务失败** —— 服务端子进程继续跑完，产物仍落在 outdir（文档与工具描述都写明了）。

**④ 结构化失败（★★）**
- 全量 stdout/stderr **落盘**并返回路径（`logs.stdout` / `logs.stderr`，默认写在 outdir）；
- 抽 `lastException`（stderr 最后一条异常行）与 `traceback` 段；`reason` 给出被杀/GPU 不可用/退出码原因；
- 转义坑友好提示：脚本里出现**字面量 \n**（应写 \\n）导致 `SyntaxError: unexpected character after line continuation character` 时直接点破。

**⑤ 产物清单过滤（★）**
- `outdir` 产物默认过滤 `__pycache__ / *.pyc / *.blend1|2 / tmp*`，并回传 `noiseFiltered` 计数；`include_noise:true` 可关闭过滤。

**⑥ 文档（★）**
- 工具描述写清 `blender_rt_cmd`（30 条固定命令，适合查状态/资产集成）与 `blender_rt_do`/headless/worker（任意 Python，适合重几何生成）的各自适用面；
- 新增「GPU 语义」「长任务」「热无头会话」三节，并把"客户端超时≠失败"写进工具描述与教程。

## v0.5.0（2026-09-14）—— 契约层（S1/S2）+ 规划器（S3）

> 来源：issue #3（@wujinz 的「白盒化 + 规划器」建议）的落地。方法论与判据见 `docs/假设驱动建模-cookbook.md`，
> 可复现实验见 `docs/examples/chair-backrest/`，自检见 `tests/contract_selftest.py` 与 `tests/plan_selftest.py`。
> 工具数 10 → 11（新增 `blender_rt_plan`）。

**① 假设驱动建模 cookbook（S0，零代码）**
- `docs/假设驱动建模-cookbook.md`：假设/区间/禁止项/推翻条件的模板、两种区间搜索跑法（GUI 内环 / 无头阻塞）、
  **可辨识性**判据、三态判定（supported / refuted / unresolved）、探针设计与 provenance 表、7 条实测踩坑。
- `docs/examples/chair-backrest/run.py`：纯无头可复现实验（约 3 s）。实测结论：外部证据下 attach 与 insert 残差**完全相同（184 / 184）**
  → 必须报 unresolved；`dy` 可辨识（极差 790）、`dz`/`tenon_len` 不可辨识（极差 2 / 0）；拆解探针（深度规）读数 0.000 m vs 0.065 m（真值 0.060 m）
  → attach refuted、insert supported。结果与证据图归档在 `docs/examples/chair-backrest/`（含 results.md/json 与 5 张证据图）。

**② 契约层 `runtime/contract.py`（S1+S2，API 挂 `K.dsh_contract_api`）**
- S1：`register_component` / `register_connection`（候选/参数区间/禁止项/置信度/所需证据）/ `register_envelope`、
  `check_envelope`（越界清单）、`check_interference`（AABB 按 x 扫描剪枝 + BVH 精查，含耗时分解）、`check_interface`（两组件间隙/侵入）、
  `destructive_guard`（**未判别连接上的 boolean_union / weld / merge / apply_transform 直接拦下**，给 `Unsupported Destructive Merge`）、
  `evidence`（出图 + md5 + 尺寸记账）、`ledger` / `status` / `report`（Markdown 报告：假设/判定/证据/复现命令）。
- S2：`verify`（三态判定：外部误差 + 可辨识性 + 探针读数）、`flip`（假设翻转并记历史）、`advance`（proposed → testing → supported/refuted）。
- 实测（无头，`tests/contract_selftest.py`）：**24/24 通过**（含"未判别连接 → Boolean 被拦"、"tenon_len 不可辨识 → unresolved"、"探针达标 → supported"）。

**③ 规划器 `runtime/planner.py`（S3，API 挂 `K.dsh_plan_api`）**
- 对象图：Component（box / cylinder / sphere / mesh_copy）+ Connection（候选/状态/禁止项/偏移）+ Feature（array / grid / mirror）。
- 诊断码（IDE 式）：`MissingComponent` / `MissingObject` / `UnresolvedConnection` / `UnsupportedDestructiveMerge` /
  `Cycle` / `ParamOutOfRange` / `UnknownKind` / `PlanInvalid`（硬错误直接拒绝编译）。
- `order`（拓扑：组件 → 连接 → 特征）、`build`（`dry_run` 先出计划；编译产物前缀 `PLAN_`；`hidden_when` 按连接候选决定存在性；
  按 `plan.envelope` 自动在契约层建包络并检查）、`graph`（mermaid / dot）。
- 实测（无头，`tests/plan_selftest.py`）：**18/18 通过**（含 dry_run 不改场景、特征展开、连接偏移、`hidden_when` 两态、
  `ParamOutOfRange`、`Cycle` 检测、硬错误拦编译、与契约层联动的包络检查）。

**④ 工具与路由**
- 新工具 **`blender_rt_plan`**（11 个工具）：契约 op 直接调用；规划器 op 用 `plan_` 前缀；`report` 支持 `path` 落盘。
- 后端新增 `POST /plan`（写路由受租约门禁；只读 op 白名单：status/help/ledger/check_*/plan_status/plan_validate/plan_order/plan_graph），
  `stats.plan` 计数。

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
