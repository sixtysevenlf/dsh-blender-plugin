# CHANGELOG — @dsh-external/dsh-blender-plugin

## v0.8.8（2026-09-15）—— 内置多视角渲染 harness（P2-1，外部反馈 #4 最后一项）

外部反馈要的形态：入参 {file, views[], res, samples, thr, outdir} → 自动按 bbox 精确取景 + 固定三点光
+ 逐张 jsonl 计时 + 不超过 budget 的判定（"这是任何建模任务都要重写一遍的东西"）。本轮把它收敛成通用能力。

- **新模块 `runtime/qc_render.py`**（挂 `K.dsh_qc_render_api`），入口 `blender_rt_plan(op="qc_render_views", args={...})`；
  也支持 `args.asJob=true` 直接转作业层（无头进程天然隔离场景）。qc.py 侧同时注册 `render_views` 转接（只注入 qc.py 也能用）。
- **自动取景**：合并所有可见 mesh 的 AABB（`targets` 可限定）→ 逐角解算相机距离 + `margin` 留白；具名视角
  front/back/left/right/top/bottom/iso/iso_l/iso_back/front_high/right_high，也支持 `az=35,el=20` 与显式 `from/look_at`、正交。
- **固定三点光**：SUN 灯 key 3.2 / fill 1.0 / rim 2.4（角度固定、相对相机方位角：+35°/45°、-50°/8°、+165°/30°），
  **只在本进程内临时建**，出图后删除并把 scene 渲染设置全部还原（GUI 实测：对象数 95、相机 1、灯 4、无残留、视口帧 hash 不变）。
- **逐张渲染 + jsonl**：每张 PNG 记录 `{view, ms, bytes, hash, path, res, engine, samples, camera, frame{bbox,margin_px,coverage,pred_bbox_px,proj_err_px}}`，
  写 `<outdir>/render_views.jsonl`（一行一张，预算中途停下也保留已完成的行）。
- **预算判定**：`budget_s`（别名 `thr`）累计渲染毫秒超限立即停 → `within_budget=false` + `stopped_early` + `skipped_views`，已出的图保留。
- **可选参考比对**：`ref_path`/ref_box（可按视角给字典）逐张调 qc.py 的 `compare`（默认固定对齐），IoU/Dice/边界距离/剖面差写进 jsonl。
- **引擎语义**：默认 `engine="keep"` 跟随进程（无头/作业由引擎前导保证 EEVEE+光追）；`samples` 同时写 EEVEE taa_render_samples 与 Cycles samples；`view_transform` 可选。
- 顺带：路径常量注入 `K.runtime_dir`（qc.py 据此现场加载同目录模块）；`bpy.types.Camera.calc_matrix_camera` 在 Blender 5.2 已移除，
  改用 `bpy_extras.object_utils.world_to_camera_view` 作投影真值对拍。

**实测（`D:/DSH/blender/out/preserve_scene_before_orca.blend` / 350 个可见 mesh / 512×512 / 64 采样 / EEVEE+RT）**：

| 视角 | 渲染 ms | bytes | 出图 alpha bbox | 画面 margin（px） |
|---|---|---|---|---|
| iso（-45°,25°） | 2124（首张含着色器编译） | 154604 | [83,96,428,458] | L83 T96 R83 B53 |
| front（-90°,0°） | 798 | 162515 | [36,36,475,475] | 36 / 36 / 36 / 36 |
| right（0°,0°） | 565 | 192160 | [36,36,475,475] | 36 / 36 / 36 / 36 |

总计 3 张 **3487 ms**（in-process 3571 ms）· jsonl 3 行 · `within_budget=true`（预算 120 s）；
预算 1.0 s 的对照跑：只出 1 张即停、`within_budget=false`、`skipped_views=["front","right"]`、已出的图保留；
取景对拍 `proj_err_px` 0.000009–0.000081 像素（自算矩阵 vs world_to_camera_view）；11 个具名视角 selftest 全部落在画幅内；
手写流程同机位对照：alpha 通道逐像素完全一致（几何/机位一致），RGB 差 ≤5/255 且只在 0.046% 像素上（EEVEE+RT 随机采样噪声；
同装置连渲两张自身就有 1/255 差异作对照）。

## v0.8.7（2026-09-15）—— 外部反馈 #4 的 P0-2 / P1-1 / P1-2 / P2-2 / P2-3 / P3

- **P0-2 长任务可再接句柄**：`blender_rt_headless(as_job=true)`（或 `auto_job_ms`）自动转作业层 —— 返回 `mode=job` + jobId + 日志路径，客户端超时不再丢结果。
- **P1-1 场景世代号**：`/act` 回执带 `sceneEpoch`（objects/meshes/materials/nameHash），用于发现"别人的重建把我的装配清掉了"；`/act` 路由已透传。
- **P1-2 路径常量进脚本命名空间**：`DSH_OUT` / `DSH_WIN(path)` / `DSH_WSL(path)`（独立模块 + `exec(open())` 写法也能用）。
- **P2-2 rt_loop 建模 measure 模板**：cookbook 补三种 measure 的入参/返回表 + spec 模板 + 配合纪律。
- **P2-3 错误增强**：库占用（`libraries.remove` / `copy=True`）、无相机（先设 `scene.camera`）、UNC 形态归一化或 `K.stage` 兜底。
- **P3 Blender 语义坑两条**：`libraries.write` 会写出无场景文件；`transform_apply` 不缩放 Bevel 宽度。

## v0.8.6（2026-09-15）—— 修 blender_rt_perf 的 dsh_perf_status NameError（P0，外部反馈 #4）

根因：v0.8.3 给 perf 加引擎感知时，把 dsh_perf_status 改名为 _dsh_perf_status_cycles 并把包装函数**追加到文件末尾**，而注册字典（第 323 行）在包装函数（第 338 行）之前引用了它 → 模块导入即 NameError → 整个 perf 模块注入失败（'PERF status 失败 · name dsh_perf_status is not defined'）。
修法：注册字典改为延迟求值 lambda，调用时才解析名字。验证：status 返回 engine/engine_mode/eevee 详情；apply 返回 preset=eevee-rt（rt false → true）。


## v0.8.5（2026-09-14）—— 作业层 + 模块热重载 + QC 掩膜区间（已实现并验证；工具与文档待补）

- **作业层（引擎+路由已实现，验证通过）**：job start/status/collect/kill/list。实测：start 0.08 s 返回 job id；立刻 status=running；15 s 后 collect → done + result={"slept":12} + 产物 40 项 + 日志路径；kill → killed；list 正常。日志与产物落 outdir/jobs/<id>/。
- **模块热重载（已实现，验证通过）**：内核 K.reload_modules(prefixes, root)；rt_worker exec 支持 purge_prefix（经 /worker 路由透传）。实测：改文件后不 purge 读到旧值 1，purge 后 2，purged=["pe_mod_test"]。
- **QC 掩膜区间（已实现，验证通过）**：qc_mask_sweep 逐组 (sat,v) 固定对齐算 IoU → iou_min/median/max + spread + 推荐口径。实测海报对 0.5914 / 0.6319 / 0.9324，spread=0.341。

**待补（下轮第一件事）**：① blender_rt_job 工具（第 15 个工具，暴露 op=start/status/collect/kill/list）；② 文档四项（UNC 写入改实测可写、// 语义、WSL env 不跨 exe、适用面与 silhouette-fit 配方）。

## v0.8.4（2026-09-14）—— worker 异常可见性 + 路径归一化 + K.stage

来源：第三份外部反馈（设计驱动线）。已完成两条 ★★★：

- **worker 异常回传（对齐 /act）**：/worker 路由把 error/traceback 提到信封层；blender_rt_worker 工具失败时读 r.result.{error,traceback,stderr,stdout}。实测 raise RuntimeError → 信封 error + traceback 齐全（此前只回 unknown）。
- **路径形态归一化**：//wsl.localhost/... 此前被 Blender 当成「相对 .blend」（解析成 <blend目录>\wsl.localhost\...）→ 现在 K.win_path() 归一化为反斜杠 UNC。
- **新增 K.stage(path)**：复制到本地路径作兜底，返回 {ok,src,staged,bytes,was_unc}；qc_load 读失败自动 stage 重试。实测 UNC 中文图 → D:\DSH\blender\tmp\stage\qc_front.png（437KB）→ images.load 640×640。

> 运维提示：engine.mjs 在后端启动时加载（Python 模块才按内容指纹热重注入）—— 改 runtime JS 必须 blender_viewport(op="restart")。

**待做（本版未含）**：blender_rt_job 作业层（★★★）· 模块热重载 K.reload_modules（★★）· QC 掩膜区间 mask_sweep（★★）· 文档四处修正（★）· 适用面与 silhouette-fit 配方（★）。

## v0.8.3（2026-09-14）—— QC 口径诚实化 + auto 掩膜守卫 + 绝对口径判据

来源：行星发动机"两个版本"复查（旧 09-12 / 新 09-14 影视级重建）实测。新构建比值偏差只有 0.03%–0.29%，但总高口径差 11%（9,900 vs 11,000 m）＋成品渲染被雾/AgX 洗淡（饱和 0.2222→0.1181、雾感 0.28→0.74）→ "感觉没那么还原"。据此修三处：

- **`iou` 改为固定对齐（诚实值）**：搜索对齐另放 `iou_search`；`scale_drift` 告警阈值 12%→**5%**；新增 `iou_search_gain >0.05` 告警（实测一个纯 11% 尺度差曾被搜索吸收 **0.13 IoU**）。
- **auto 掩膜守卫**：参考图前景占比 >80% 或 <2% 时自动回落到阈值口径，并在回执写明 `satv-fallback` + 原因（实测满构图海报 auto 前景 95.9% → 不可信）。
- **`ref_box` 改为可选**（省略=整图；此前必填导致第一次调用直接失败）。
- **新增绝对口径判据**（cookbook 层 3）：凡绝对尺寸项必须单列「绝对尺寸对照表」并标注采用哪套口径——**比值/IoU 结构上看不见绝对口径差**。

同步：`tests/qc_selftest.py` 断言更新；操作教程/配置参考口径说明更新。

## v0.8.2（2026-09-14）—— 配方库（blender_rt_preset）+ 部署 SOP + 整合社区优点

- **新增第 14 个工具 blender_rt_preset（配方库）**：save/list/get/apply/delete/export/import；点路径 data；targets 支持 MAT:/OBJ:/SCENE；不给 targets 只预览；export/import 走单个 bundle 便于分享。实测：场景 render.resolution_x 720 → 640 真实生效。灵感来自 lurenjia-l/dsh-blender-stylized-shading 的「配方化」优点（通用化为与领域无关的机制）。
- **新增 docs/部署SOP.md**：目标与完成标准 → 环境探测 → 安装 → 连接 addon → 自检（doctor/qc_self_check/engine）→ 汇报 → 排障速查（格式借鉴其 AI_DEPLOY.md）。
- **cookbook 改为三层技能结构**（领域方法论 / 平台原语 / 交付与验收 SOP，含 frontmatter 触发条件）；配置参考新增引擎索引章节。
- README 双语新增「致谢 / Credits」，点名感谢 @lurenjia-l 的 EEVEE 实测踩坑与技能分层法。
- 两树一致：14 工具、presets.py、部署SOP、cookbook、配置参考、EEVEE-工作要点。


## v0.8.1（2026-09-14）—— 修复 v0.7.0 起 /act 路由回归（Issue #4，感谢 @yihefeikong-rgb）

engine.act 在 v0.7.0 改成返回结构化对象（stdout/stderr/error/traceback/mainThreadMs），但 server.mjs 的 /act 路由仍读旧的 out.result，导致：rt_do 恒无 stdout、rt_do(file=...) 静默空跑、异常被当成成功。影响 v0.7.0 与 v0.8.0 的仓库版/分享包。修复：/act 路由转发结构化字段并把 file 透传给 engine.act。本机实测：print(123) 得到 stdout=123、抛异常得到 ok=false + error + traceback、file= 的脚本真正执行。


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
