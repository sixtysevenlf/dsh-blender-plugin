# CHANGELOG — @dsh-external/dsh-blender-plugin

## v2.6.2（2026-10-28）—— 曲面算法（surface_*）+ 文档/计数一致性

> 模型工具仍 **15 个**；plan catalog 扩到 **34 family / 228 op**（+1 family / +11 op）；addon 仍 **v1.10 / protocol 11**。
> 本版**不包含**实验中的曲面工作台（workbench 面板/路由/模块已放弃，未进入发布）。

### 新增：曲面算法包（M0–M2 / M4）

- **`runtime/surface.py`**（纯 bpy + numpy，不依赖 OCCT/geomdl/trimesh）新增 11 个 op：
  `surface_loft / sweep / patch / tessellate / validate / export / blend / offset / fit / selftest / help`。
  - M0：SurfaceSpec 校验与 `spec_hash`（同 spec + tessellation ⇒ 同哈希、同顶点）；
  - M1：loft（插值型 B-spline 过截面 + Catmull-Rom 环向）、sweep（平行传输标架 + twist + 逐点 scale）、
    patch（张量积 clamped B-spline）、tessellate（spec→网格 / 对象→烘焙）；
  - M2：G0/G1/G2 缝连续性、离散曲率、BVH 自交、射线近似厚度、长宽比/翻转法线/水密，
    `verdict = pass | fail | degraded`，**fail 不静默成 pass**，degraded 不当作通过；
  - export：对象 → GLB；blend/offset 明确 `ok:false`（M5 需 OCCT），fit 保留接口（M3 未实现）。
- **接线**：`runtime/paths.mjs` SURFACE_PATH、engine `SURFACE_READY` + `surface_` 前缀路由、
  catalog 新 family、plan headless 映射（preload `surface,audit`）、只读白名单补 `surface_help`。
- **真机自检** `tests/surface_selftest.py`：三基准（瓶身/翼子板/头盔）+ 自交/薄壁/连续性负例 +
  确定性 + tessellate 双通路 + 7 类坏参数；headless `preload="surface,audit"` 全绿（23 项证据）。

### 文档

- 新增 [docs/曲面算法.md](docs/曲面算法.md)（op 表/SurfaceSpec/verdict/限制/配合链路）；
- README 中/英文各加「参数化曲面」特性与文档入口；
- 操作教程新增 §3.5.6b 参数化曲面；
- skill：SKILL.md §0.7 工具表 + §0.7.1 曲面算法回路（含硬规则）、§9 family 速查；
  新增 `skills/blender-modeling/references/曲面算法.md`，install.sh 同步拉取；skill README 更新 references 清单。

### 修复

- **`scripts/sync_counts.mjs` 三处静默漏刷**：AGENTS 正则只认「N 个 family / N 个 op」（2.6.1 改成不带「个」后永远匹配不到却报“已一致”）；
  只刷工作区 AGENTS、不刷仓内 AGENTS；单次 replace 只改第一处。改为双写法 + 双 AGENTS + `/g`，
  并新增 `docs/操作教程.md` 与 `skills/blender-modeling/SKILL.md` 两个目标（本次实测已把两处 33/217 刷成 34/228）。
- 修正 skill / 操作教程里残留的 `33 family / 217 op` 旧口径。

### 新增（v2.6.1 之后合入）
- **headless：`opts.hardExit`（默认开启）**——无头脚本在 `done` 标记之后显式冲刷 stdout/stderr，
  随即 `os._exit(0)` 跳过 Blender 自身退出拆解。现场：Blender 5.2.3（Steam）在 .blend 加载材质带
  Value→Material Output.Thickness 连线时，glTF 导出**成功、产物完整、done 已打出**之后，
  在退出期命中导出器 AV（0xC0000005），结果被 exitCode≠0 顶成"失败"。
  依赖正常 teardown 的调用方（`bpy.app.handlers` 落盘、延迟自动保存）传 `hardExit=false` 回旧行为。
  作业路径与同步路径同一份实现（`runtime/hard_exit.mjs`）；
  新增 `tests/headless_hard_exit_selftest.mjs`（顺序契约 / 开关语义 / 接线断言，已入 `npm test` 主链，
  `npm run test:hardexit`）。
### 修复
- **headless：`file=` 缺失/空串/参数名误写不再静默成功**（旧症状：`file=""` 或 `blend=<路径>` →
  `ok=true / exitCode=0 / inputFile=null / 零警告`，脚本拿到出厂空场景）：
  引擎侧 `file=` 显式校验（空串→`file-empty`、非字符串→`file-type`，直连 `/headless` 的兜底）；
  工具端（`blender_rt_headless` / `blender_rt_job` op=start|run）拦截参数名误写（`blend=`/`path=` 等六名）
  与空串——工具端在 body 组装前剥 falsy，引擎守卫对工具路径永不触发，故 ALIAS 拦截必须落在工具端。
  行为变化提示：`file=false`（布尔）旧行为被静默当「未传」，现按 `file-type` 明确报错——类型纪律收紧，
  依赖旧语义者请改为省略该参数。
- **headless：「成功但没开档」不再无声**——回执 `inputFile` 的 null 分支换成 `{given, opened, reason}`
  语义对象（形态冻结：truthy 的 `fileFingerprint` 返回值一字未动）；未开档时 `warnings` 追加
  `no_input_file`（含 `.py` 语义切换场景）；台账（headless/job 的 running 与终态）补记
  `file`/`inputFile`，事后取证可区分「没给档」与「给了没开」。
- **headless：同毫秒撞名**——无头脚本名（`staging.mjs`，旧 `Date.now().toString(36)` 同毫秒互相覆写）
  与 stdout/stderr log 名（`engine.mjs`，同毫秒追加写同一文件互脏）都加唯一后缀（脚本名 = 毫秒+进程内
  序号+随机；log 名 = 毫秒+runId）。
- 新增 `runtime/file_guard.mjs`（engine / 工具端 / 单测共用同一份判定）与
  `tests/headless_file_guard_selftest.mjs`（41 项，离线，已入 `npm test` 主链，`npm run test:fileguard`）。

## v2.6.1（2026-10-23）—— 性能优化（选中轮询 / 审计 / 批处理 update）+ 平台与测试修复

> v2.6.0 之后合入的改动打包为本版（含此前 PR 中标注为 v2.7 的 dsh-TUI 宿主半边）。
> 模型工具仍 **15 个**；plan catalog 仍 **33 family / 217 op**；addon 仍 **v1.10 / protocol 11**。

### 性能（真实成品文件实测）
- **选中轮询 1200 ms → 100 ms**（Web 客户端 + dsh-TUI 宿主半边）：端到端 p50 ~993 ms → ~156 ms
  （本机回环，8 次选中变化；CAS consume 语义不变）。
- **`audit._tris` 大网格快路径**：面数 ≥256 时批量 `foreach_get("loop_total")`；
  137k 面 59.3 ms → 15.5 ms（3.8×），逐值等价。
- **批处理 update 第 1/2 步**：新增 `K.dsh_kit.BatchUpdates`；`motion._restore_transforms` 改为
  「先写全部字段 → 一次 update → 统一核对」，真实成品 300 对象还原 32× / 91×
  （行星发动机 / 太空电梯），motion selftest 25/25。
- 实测否定了「msgbus 订阅选中」：Blender 5.2 上 `Object.select` 不是 RNA 属性；
  选中变化继续用 `depsgraph_update_post`（0.3–0.7 ms）。

### 功能（v2.6.0 之后合入）
- **#35 工具卡片**：15 个工具的 `presentCall` / `presentResult`，宿主不再显示整段 JSON。
- **#36 dsh-TUI 宿主半边**：选中上下文 chip、页脚状态字段、`@` 提及、`/blender status|doctor|jobs`；
  按接缝能力检测启用，新增只读 `GET /host/state`。

### 修复
- **#38 / #39 / #43 Windows 终端弹窗**：三处 blender `spawn` + 两处 `execFileSync` 补 `windowsHide: true`，
  并加 `tests/windows_hide_selftest.mjs` 静态自检（进 `npm test`）。
- **#40 / #41 capability lock**：发布 tarball（无 `src/`）不再 ENOENT，降级工具维度并显式打印 SKIP。
- **#42 capability lock families**：逐族取数改用族名字符串 + `full:true` detail，恢复族名 / 逐族 op 维度；
  用 `--update` 重新落锁。

## v2.6.0（2026-10-21）—— 33 family / 217 op · GUI 优先 · 无头备用 · 文档与 skill 重写

> v2.5.0 之后的四轮工作（P0 / P1 / P2 + 文档与 skill 重写）并入本版一次发布。
> 模型工具仍为 **15 个**；plan catalog 从 28 family / 186 op 扩到 **33 family / 217 op**。
> 新增能力全部走 plan catalog，**不新增模型工具**；GUI 仍是最优先；无头能力补齐（含 rt_see），作为无 GUI / 批处理 / 长活的备用。
> **2026-10-21 口径更正**：不是 headless-first；默认先开 GUI，headless 仅备用。

### 测试与文档修正（issue #32 / #33）

- **#32** `tests/linux_selftest.mjs`：裸 `import(path.join(...))` 在 Windows 上抛 `ERR_UNSUPPORTED_ESM_URL_SCHEME`，
  `npm test` 链式中断、其后 13 个用例静默跳过 → 改 `pathToFileURL(...).href`。
- **#33** `tests/workdir_shared_selftest.mjs`：C5 用 `path.sep` 切 POSIX 风格路径，Windows 上恒红
  → 改 `path.posix.basename(...)`。
- **文档口径**：改正「`/host/view/*`、`/host/pick*`、`/host/ready` 路由为兼容保留」的错误表述 ——
  这些路由在 v2.3 已随面板删除，现存宿主路由只有 `/host/selection`（+`/latest`、`/consume`）、
  `/host/scene-items`、`/host/jobs`（+`/log`）。

### 文档与 skill 重写（2026-10-21）

- **全量文本校正**：README 中英、操作教程、配置参考、安装/部署/迁移/AGENTS/CLAUDE 全部对齐 v2.6.0（15 工具 · 33 family / 217 op · addon v1.10 / protocol 11）；删除 28/186、v2.5/v0.9.x 旧口径与“必须 GUI”旧承诺。
- **skill 架构优先重写**：SKILL.md 以“插件就是建模流程”为主线（catalog → plan op → worker session → 回执/verdict → 门/交付），新增架构铁律、标准状态机、工具选择矩阵、P0–P2 配方、反模式、多 Agent 协议；通用 Blender 知识降为附录 A。
- **references 校正**：验收清单补 asm/visualreport/eval/强制门；参考图 SOP 改为 img_digest/measure/calib + shapesolve + visualreport；multiview 与英文 overview 标明与当前插件的关系。
- **工具描述**：rt_do/rt_see/rt_watch/rt_worker/rt_headless/rt_plan 明确 GUI 首选、session 无头、worker see/watch/TTL、plan session=auto；package description 同步 v2.6.0。
- 文档 grep：用户文档无 28/186/v2.5/v0.9.x 旧口径；skill >50KB 且 skill_bundle_selftest 61/61；npm test 全绿。

### P2 第三轮（2026-10-20）

- **装配交付导出** `asm_export`：GLB/FBX/OBJ + manifest.json + append-only merge_ledger.jsonl；`merge=true` 先 `asm_merge`（gate+snapshot，不过门不导出）；67 evidence 真机全绿。
- **低风险修复策略** `asm_repair(policy=safe|manual|aggressive)`：safe 只做 translate/snap、硬禁布尔；manual 只规划不执行；回归 88 evidence 全绿。
- **缺陷注入/基线评估** `eval_run`（evalharness.py）：assembly D1/D3/D4/D5/D7/D8 + reference 12 例 + calib core；缺模块 skipped/degraded 不假通过；assembly capture=1.0、reference 12/12、core degraded 0.875。
- **Seedance 参考包** `blockout_refsheet`（4 正交/透视 PNG + 2x2 拼图 + refsheet.json + 可选转台）+ `blockout_turntable`（Blender 内置 FFMPEG MP4/H264）；22 通过/0 失败；256x256/12 帧 MP4 9192B。
- **无头 watch** `rt_watch(session=...)` + worker `_watch`：逐帧 Workbench 离屏 + hash + 最多 6 帧图；真机 3 帧动画 distinct=3/58ms。
- **无头 plan 路由** 扩展 eval/blockout/asm 等 family；真机 `asm_export` 161ms、`blockout_refsheet` 331ms、`turntable` 334ms。
- catalog 33 family / 217 op；`npm test` 全绿；capability lock / 只读白名单 / 计数同步。

### P1 第二轮（2026-10-20）

- **装配修复**：`asm_repair` + `fix_clash`（执行器，dry_run 默认）+ `fix_interference`（只读规划器）；2mm 互穿 dry 不动场景→执行后 clash=0/float=0/有 snapshot；contained/布尔 subtract/浮块 snap 计划；88 条真机断言。
- **强制门/flow op**：`gate_preflight / gate_checkpoint / gate_deliver`；`deliver_export(require_gate=true)` 真机验证（pass 才写盘，bad gate blocked；deliver+verify 18 项）。
- **无头 plan 路由**：`blender_rt_plan(..., session="auto")` 按 family 自动起持久 worker + preload + dispatch，不依赖 GUI；首次冷启动 ~2s，之后同步复用。
- **会话池**：worker idle TTL（默认 15 min，`DSH_WORKER_TTL_MS`）+ `idleMs` 回执。
- **同机位 QC**：`visualreport_report`（verdict-first，qc→imgtools→numpy 三级降级）；32/32；同图 pass，平移/缺角 fail，缺陷 crop + mm 定位，72–96ms。
- **无头轮廓拟合**：`shapesolve_solve`（正交解析投影+numpy 光栅化，不渲染）；三视反演 40/20/30→误差 0.98%/1.55%/0.35%、IoU [1,1,1]，solve 793ms。
- catalog 32 family / 211 op；`npm test` 全绿；capability lock / 只读白名单 / 计数已同步。

### P0 第一轮（2026-10-20）

- **交互式 headless（重点）**：`rt_worker` 支持 `preload` 启动前导 + `see`（临时相机 + Workbench 离屏）+ `reset` + `warmup`；实测 start 2.24s（含 1.1s 首帧初始化），之后 exec 0–2ms、see 往返 53–85ms（320×180），**不是 job/异步回合**。
- `rt_do` 在 GUI 不可达/显式 `session` 时自动降级到持久无头 worker（exec + see，图片直接回模型）；`rt_see` 增加 `session/auto`；`rt_loop` 增加 `target="session"` 的 `dsh_loop_sync` 阻塞内环（20 次 56ms，不依赖 timers）。
- `rt_headless` 支持 `attach_images`（true/"sheet"）；`rt_plan` 增加单行 receipt.v2 信封（verdict/top_defects/next）+ `attach_images`（≤2 张），plan 主通道不再只有 12KB 文本。
- `imgtools.py` v2：新增 `img_digest`（原生分辨率量测/轮廓/Hough/孔/对称/可读性/原生裁剪/标注 + 可确认数字表）、`img_probe`、`img_measure`、`img_calib`；真机 61/61 断言。
- 新增 `runtime/assembly.py`：`asm_scan`（全对全分类：clash/hidden_ok/contact/gap/float/contained/unresolved + 完全包含补测）、`asm_manifest`（md5/接口帧/allow 校验）、`asm_merge`（id 序 append + 法线 + snapshot + 自动 gate，dry-run 默认）、`asm_gate`（verdict-first，export_allowed 仅 pass）；模块自检 + 测试脚本全绿。
- 新增 `runtime/blockout.py`：`blockout_base/batch/export`（声明式 parts、mm/anchor、Workbench 3 视图、GLB）；真机 33/33，20 件 geom 9ms、3 视 512 约 1.1s（首帧后 25ms/张）、GLB 117KB。
- **可交互 headless 真机建模测试**：参考图→img_digest（2m×0.8m 盒子：width 2000mm/height 800mm/px_per_mm 0.16）→ 12 件白模（GLB 70KB）→ 装配两件 2mm 互穿 scan 检出 depth=2mm/volume≈2.0e6mm³，移开 10mm 后 clash=0。
- 目录 30 family / 201 op；`npm test` 全绿；`capability.lock` / `plan_readonly.baseline` / provenance 计数已同步。

### 主线：七条实测反馈 —— 台账对账 / 5.2 合成器 / 多灯卫生 / 进度通道 / 第五态 / 不覆盖声明 / 写作用域两维

**来源**：一次 4 小时、3 个子代理、479 对象的真实项目（120 m 哥斯拉 + 被毁城市 + 原子吐息）后，
Lead 与 3 个子代理共同复盘出的 7 条改进；按顺序落地，每条都有独立自检。

1. **台账以文件为准（`runtime/recover.mjs`）**：后端重启/被杀后，磁盘台账里只剩 `status=running`、pid 已死，
   但**子进程其实已经跑完、产物/日志都在**。旧行为一律判 `stale`，模型只能自己去 `Get-Process` + `ls` 破案
   （实测两次，各赔一轮往返）。现在 `reconcileLedger` 先做磁盘对账（`summary.json` → `resultPath` → stdout 终态
   `DSH_STAGE done` / `HEADLESS` / `Traceback`），对得上就返回 `status=done|failed` + `recovered:true` +
   `recoveredFrom/recoveredEvidence`；对不上才 `stale`，并附 `evidence` / `evidenceChecked`。
   run 与 job 的台账 running 记录也补了 `stdoutLog`/`stderrLog`/`dir`，后端重启后不用扫目录猜。
2. **Blender 5.2 合成器迁移（skill §6.5.6）**：补 5 行踩坑表 + 一段最小可跑片段 ——
   `scene.node_tree` 已删（改 `compositing_node_group` + `NodeGroupOutput`；`CompositorNodeComposite` 不存在）、
   Glare 的 Type/Quality 变 MENU 插座、`CompositorNodeMixRGB`/`CompositorNodeMath` 被删（改用 `ShaderNodeMix`/`ShaderNodeMath`）、
   `mat.cycles.displacement_method` → `mat.displacement_method`、`surface_render_method` 只有 DITHERED/BLENDED。
3. **多灯卫生**：识别 `Shadow buffer full (N / M)` 并产出结构化 warning（`code=eevee_shadow_pool_full`，含次数/用量/上限/可粘贴修法）；
   新增常驻 `K.light_hygiene(apply=True, max_shadow=4)`（只保留能量最高的 N 盏点/聚光灯投影 + 池设 2048）。
   实测：36 盏灯 → 控制台每帧刷错且 `shadow_pool_size='4096'` 静默无效（枚举上限 2048）。
4. **进度通道**：`dsh_progress(**fields)` / `DSH_PROGRESS {json}` 与 stage 分开（stage=阶段跃迁，progress=阶段内高频进度，留最近 20 条）；
   运行期每 5 s 心跳（`heartbeat`：elapsedMs/logBytes/lines/stage/progressAgeMs，作业还会 append `heartbeat.jsonl`）；
   `op=status/collect` 与 headless 回执都带 `progress/progressAt/heartbeat/progressHistory`。
5. **`audit_mesh` 第五态 `pass_multi_shell`**：多壳件不再一律 `degraded`。判据改为「每个壳自己闭合+绕向一致+体积符号可确证」，
   AABB 相交（碎块簇/火球鳞瓣/布景多壳件）不再等于“判不出来”；合法空腔仍标 `nested` 豁免。
   对象 `state` 仍是 `pass`（新增 `multi_shell`/`state_detail`），totals 增 `normals_multi_shell`。
   实测：两个分离壳/嵌套空腔 → `pass_multi_shell` + `clean=true`；真互穿仍按自交判 fail。
6. **catalog 不覆盖声明**：`catalog` 默认页与 `args={handoff:true}` 都明确写「生物/有机造型、电影级布光/体积/合成、角色动画绑定」不覆盖，
   省掉“在这里找不到工具”的探索成本（handoff 仍守 3500 字符上限）。
7. **写作用域两维（集合 + 对象前缀）**：`blender_viewport op="lease" scope="collection:COL_Lights,prefix:LGT-"`；
   `/who`、`/lease`、409 回执（`holderScope`）都可见；裸 token 会按形态推断（`COL_*`→集合，带 `-`/`*`→前缀）。
   配套常驻 `K.route_collections(rules=None)`：按前缀把灯/相机/各资产归位到 COL_Lights / COL_Camera / COL_VFX / …
   —— 子代理只有单一集合写权限时，按前缀命名、装配前一行归位。

#### 自检（新增 4 个用例）

- `tests/ledger_recover_selftest.mjs`（18 断言，纯逻辑）
- `tests/ledger_recover_e2e_selftest.mjs`（10 断言，独立后端 + 真 headless：对账三态 + progress + heartbeat）
- `tests/scene_hygiene_selftest.mjs`（8 断言）
- `tests/lease_scope_selftest.mjs`（6 断言，独立后端 HTTP 契约）

#### 兼容性

- 回执只**增字段**（`recovered` / `progress` / `heartbeat` / `warnings` / `scope`），不改旧字段语义；
- `stale` 语义保留：没找到终态证据时仍然判 `stale`（不谎报完成）；
- 宿主侧 `lib/index.js` 已同步（recovered/progress 文案、lease scope 参数），**升级后需重启 DSH**；只改后端可 `blender_viewport op=restart`。

## v2.5.0（2026-10-20）—— N 面板重排（活动置顶 / 环境与模型 API 子面板）+ 暂停 AI 操作（addon v1.10）

**来源**：用户要求把活动状态移到上面、资产库与 AI Model Generation 合并成「环境与角色模型API调用」父面板
（环境生成 / 模型生成两个子面板，默认全折叠），AI 操作时显示警告，并加「暂停 AI 操作」按钮；顺带删掉一些图标。

- **活动状态置顶**：N 面板第一块就是活动状态；`agent` / `both` 时用 `alert` 红色警告「AI 正在操作 Blender」。
- **暂停 AI 操作**：新增场景属性 `dshbb_ai_paused` + 按钮 `dshbb.toggle_ai_pause`；
  暂停后 socket 只放行只读命令（`ping` / `get_*` / `drain_human_activity` / `describe_node_type` / `bpy_api_lookup`），
  `execute_code`、资产下载/生成/导入等写操作返回 `AI_PAUSED`；`get_activity_state` 带 `paused` 字段。
  恢复只能由人在 Blender 面板点「继续 AI 操作」（模型无法自己解除）。
- **新面板**：`环境与角色模型API调用`（父，默认折叠）→ `环境生成`（Poly Haven / Sketchfab / Poly Pizza）+
  `模型生成`（Hyper3D / Hunyuan3D / Tripo，含 Premium 状态）；父面板与两个子面板都 `DEFAULT_CLOSED`。
- **删图标**：新面板里的集成标题、环境生成 URL、模型生成的按钮等不再带装饰性图标；活动状态只在警告时带图标。
- 状态变化由 0.5 s timer 触发 N 面板重绘（不靠鼠标移动才刷新）。

## v2.4.0（2026-10-19）—— 子元素选中 + N 面板列表可伸缩（addon v1.9）

### 新功能：编辑模式 / 姿态模式的子元素进入选中记录

**来源**：用户问「Add 菜单里这些类型除了网格，能记录哪些？」→「把编辑模式子元素 / 骨骼也纳入选中记录」。

- addon `build_selection_payload` 新增 `selection.sub`：
  - `MESH`：`counts`（顶点/边/面）+ `indices`（三类索引数组，各上限 2000）+ `select_mode`（当前点/边/面模式）；
  - `ARMATURE`：编辑模式 `edit_bones` / 姿态模式 `pose.bones` 的选中骨骼名（上限 500）；
  - `CURVE` / `LATTICE`：控制点 `(spline_index, point_index)` 或点索引（上限 2000）；
  - 其他编辑模式（文本/蜡笔）只带 `type:"OTHER"` + `note`，不伪造元素语义；
  - `truncated` 如实标记截断。
- 后端 `runtime/selectionstore.mjs` 增加 `sanitizeSubSelection`：白名单类型、过滤非整数/负数、再次夹上限；
  未知类型归 `OTHER`，缺对象名直接丢弃。
- Web 客户端 `selectionMessageText` 生成子元素草稿文本（网格给计数 + 索引样例，骨架给骨骼名，
  曲线/晶格给控制点），完整数组可用 `blender_rt_cmd get_selection` 查。
- N 面板：编辑/姿态模式显示「子元素：编辑模式（网格）· Cube」，展开列表会多出「顶点 12 / 边 3 / 面 7 / Bone.001」行。
- 已知边界：子元素变化**不进入 activity 指纹**（activity 仍按对象级选区判定）；大网格不在每个 tick 扫描。

### 同版本 UI：N 面板列表可折叠 + 可滚动 + 高度可调

**来源**：用户反馈「这个列表是可伸缩的吗，能折叠或者隐藏的尽量隐藏」→「高度可以调整不要固定」。

- 对象列表 / 集合成员列表改为 **Blender UIList**：自带滚动条，内容多时不再一长列铺开。
- **高度可调**：展开后出现「高度（行）」滑杆（`dshbb_list_rows`，3–30，默认 8）+「− 矮一点 / + 高一点」按钮；
  `template_list(..., maxrows=30)`。
- **可折叠**：两个列表默认收起，点 `对象 / 子元素列表（N）▸` / `成员列表（N）▸` 展开；属性
  `dshbb_show_selection_list` / `dshbb_show_collection_list`（默认 `False`）。
- **绘制稳定性**：列表数据改由 timer / 属性 update 回调同步，不再在 `draw()` 里写 ID 数据；
  面板绘制异常写入 `dshbb_last_panel_error`（诊断用，正常为空）。
- 后端地址 / 覆盖项收进折叠的「后端 / 高级」（`dshbb_show_advanced`，默认隐藏）。

## v2.3.0（2026-10-19）—— Blender 原生选中 → DSH · 统一 human/agent activity 判定（addon v1.8）

**来源**：用户裁决 ——「bl 插件端加一个"操作中"的判定，把当前 DSH 的选中功能移到 Blender 插件端」。
确认口径：**B. Blender N 面板「发送选中到 DSH」** + **C. human / agent 两种 activity 统一成一个状态**。
**结论**：选物体不再依赖在 DSH 面板里点低清截图 —— 在 Blender 原生选区里选，按 N 面板按钮即进 DSH 当前会话草稿
（**不自动发送**）；Blender 侧同时给出 `idle / human / agent / both` 四态，面板实时显示、模型可查。
**同版本删除**（用户明确要求）：DSH 侧视口面板（Web 侧栏 + TUI 侧栏）、点击进消息、模型请求点选 `blender_rt_pick`
—— 模型工具 **16 → 15**；后端 `/host/view/*`、`/host/pick*`、`/host/ready` 路由保留但无本仓 UI 消费者。
**两种模式**：N 面板 `dshbb_selection_mode` 可切「**多选**」（发当前选中对象）/「**集合**」（整组发送，大装配体用）。

### 新 · addon v1.8（协议号仍 11，向后兼容）

- **`ActivityTracker`**（主线程，本地判定、**与 telemetry 同意无关、不上传**）：
  - human 信号 = `bpy.context.window.modal_operators` 非空（拖拽/变换/菜单等，持续有效）+ 最近 1.5 s 人类操作符
    （含 view3d 导航，agent 命令期间抑制）+ 撤销/重做 + 原生选区/活动对象/模式变化（1.5 s 窗口）；
  - agent 信号 = agent 命令执行中（`agent_depth>0`）或最近一次命令结束 ≤1.5 s；
  - agent 命令结束时作废选区指纹 ⇒ 不会把工具自己的改动误报成"人操作"。
- **5 条常驻命令**（进 `capabilities`，常驻 19 → **24**，总命令 34 → **39**）：
  `get_activity_state`（四态快照；**查询自身不计入 agent**）· `get_selection`（原生选中：名字/类型/活动对象/模式/场景）·
  `send_selection_to_dsh`（把 §S1.3 payload POST 给后端；按钮与命令同一实现）·
  `get_collections`（场景集合 + 对象数 + 选中数）· `send_collection_to_dsh`（整组发送；大装配体免逐个选）。
- **N 面板新 section「Selection → DSH」**：活动状态行（人/agent/同时/空闲）+ 最近人类动作 + **模式切换（多选 / 集合）** +
  多选模式下的已选摘要与「发送选中到 DSH」/ 集合模式下的集合下拉（`dshbb_collection_target`）与「发送集合到 DSH」 +
  最近一次发送结果 + 后端地址（可用场景属性 `dshbb_backend_url` / 环境变量 `DSH_BLENDER_BACKEND_URL` 覆盖）。
- **大装配体收敛**：对象模式草稿文本最多列 24 个名字（超出给"共 N 件"）；集合模式只给"集合名 + 件数 + 前几个成员"——
  不再把 200 个对象名刷进输入框。
- **Blender 侧两种模式完善（用户要求）**：多选模式在 N 面板里给出**可滚动的对象列表**（名字 + 类型，最多 30 行）；
  集合模式给出**成员列表 + 件数 + 子集合 + 当前选中数**；切到集合模式时 `update` 回调自动推断最相关集合，
  另有 ⟳ 按钮（`dshbb.use_selection_collection`）随时重新推断。
- 真机实测：模态探针 `human_active=true` + `modal_operators=[DSH_TMP_OT_modal_probe]`；模态结束后回到 `idle`；
  面板按钮 → 后端 `seq` 增长 → 浏览器草稿收到文本（`last_consume {ok:true,how:"actions"}`）；
  集合命令 `get_collections` / `send_collection_to_dsh` 真机可用；旧 v1.7 addon 的其它命令不受影响。

### 新 · 后端 `/host/selection`（纯内存，零 Blender 往返）

- `runtime/selectionstore.mjs`：payload 消毒（名字 ≤200 / 对象 ≤50 / 字符串 ≤200 字符）· `seq` / `consumed_seq` 只增不减 ·
  `consume` 幂等 + 非法 seq 400 · `last_consume` 记录客户端插入结果（回答"按钮点了为什么草稿没字"）。
- 3 条路由：`POST /host/selection`（addon 推送，**不校验写租约**）· `GET /host/selection/latest`（只有未消费才带 payload）·
  `POST /host/selection/consume`（可带 `{ok,how,reason}` 诊断）。
- `tests/selection_store_selftest.mjs`（接入 `npm test`）：**53 断言**覆盖消毒 / seq / consume / 诊断 / 集合 payload。

### 新 · Web 客户端半边：无头桥 `conversation.input.dock`

- 注册 id `blender-selection-bridge`（session 作用域、渲染 null、**面板收起也收**），每 **1200 ms** 轮询 latest。
- 插入当前会话草稿：优先 `inputActions.insertText`（可追加、保留用户内容）；备用 `SessionInput.setDraft` 仅当草稿为空；
  **不自动发送**。插入成功才 `consume {ok:true,how}`；失败不消费、重试 3 次后消费并回传原因。
- **不按页面可见性跳过**：用户可能点完 Blender 按钮立刻切回 DSH；轮询零 Blender 往返。
- `tests/client_bundle_selftest.mjs` 重写为 v2.3 精简版：**46 断言**（bundle 形状 / 无面板残留 / @ codec / 选中文本（对象/集合/大名单收敛）/ 三种插入路径 / dock 注册）。
- 删除 `src/tui-panel.ts`、`tests/tui_panel_selftest.mjs`、`docs/TUI-适配.md`；capability lock 16 → 15 工具。

### 文档 / 契约

- 新契约唯一真源：[`docs/宿主集成-接口契约-v2.3增补.md`](docs/宿主集成-接口契约-v2.3增补.md)（§S0 背景 · §S1 addon 命令与 payload ·
  §S2 三条路由 · §S3 客户端插入 · §S4 判据 S1–S8 · §S5 已知边界）。
- README（EN / zh-CN）：新增 §4.3「Blender 原生选中 → DSH」+ 四态表 + 前置（addon v1.8 + 重启 DSH）+ TUI 不支持说明；
  命令面计数 34→37（常驻 19→22）；版本 v2.3.0。
- 操作教程新增 §11（步骤 / 排错 / 验收）；`AGENTS.md`、`llms.txt`、`addon/README.md`、第三方安装清单、配置参考同步。
- 版本三处同步 **2.3.0**（`package.json` / `src/index.ts` 的 `PLUGIN_VERSION` / 重建后的 `lib/`）；**addon 1.8**。

### 验收（实测，2026-10-19）

- `npm test` **exit 0**（新增 selection_store + client_bundle 入列）；`client_bundle_selftest` 全过；schema 预算仍 ≤12,000。
- 真机：addon v1.8 重载后 `get_addon_info` 回 `[1,8]` 且 capabilities 含三条新命令；`get_activity_state` 空闲时 `idle`、
  命令后 `agent`、模拟人类操作/模态时 `human`/`both`（`last_human_kind=selection` / `modal:…` 实测）。
- 端到端：Blender 选中 Cube+Camera → 面板按钮 → 后端 `seq` → **当前会话草稿收到文本**；
  `/host/selection/latest.last_consume` 回 `{ok:true, how:"actions"}`（真实 Web 客户端插入成功）。
- 打包：**1.3 MB / 142 文件**（v2.2 为 1.5 MB/161；删 TUI 面板 + 视口/点选路由 + 旧 addon 适配/文档/测试；新增 selectionstore/契约/测试；`skills/blender-modeling/` 8 文件仍在包内）。
- **清理（v2.3 精简）**：删除旧 `MCP for Blender` / harveyxiacn category-action addon 支持、双名回退与 `addonProtocol` 配置；
  删除 `/host/view/*`、`/host/pick*`、`/host/ready` 路由与 `runtime/{viewstore,pickrequest,host_presence}.mjs`；
  删除 `blender_rt_pick` 工具、Web/TUI 视口面板、`tests/{tui_panel,protocol,pick_presence,host_cors,acceptance_v093}_selftest.mjs`；
  删除历史契约文档（v1.2/v2.1/v2.2）、上游整合/改名方案/评估文档、`tmp/` 与 `.bak` 备份。

### 未证实 / 已知边界

- **必须重启 DSH**（客户端半边是启动时装载的模块）；开发期 HMR 可能热加载，正式升级以重启为准。
- **DSH 视口面板已删除**：老用户的侧栏 tab 会消失（代码不再注册）；`/host/view/*` 等路由仍在但无 UI。
- **装完必须通知用户**：`docs/AI-安装提示词.md` §③.5 已要求安装型 AI 主动介绍原生选中 / 两种模式 / activity / 删除项。
- **addon 兼容矩阵**：上游 `MCP for Blender`（`blender_mcp`）仍可用模型工具，但**没有** v2.3 的选中/集合/activity；
  两 addon 同占 9876，不可同时启用。已写入 README §2 / `addon/README.md` / AI 安装提示词。
- **TUI 暂不支持草稿插入**（无 composer dock；与 `sendToChat` 未授权的既有边界一致）。
- 桥只写**当前打开的会话**；多标签页极端情况下同一选中可能各插一次（服务端 `last_consume` 保留最后一条）。
- 宿主面板在 Blender 主线程之外无法逐帧刷新（面板显示随 Blender 重绘更新）。

---

## v2.2.0（2026-10-19）—— skill 并入插件仓：随包发布 + 加载时自动注册

**来源**：用户提问 ——「把这个 blender-modeling skill 合并到 blender 插件仓库吧，好多人不看 github 主页不下载这个 skill」。
**结论**：skill 不再要求用户去独立仓库下载 / 软链；插件装好、DSH 重启后，会话技能目录里自动出现 `blender-modeling`。

> **本版还包含（上游 main 合入，PR #23–#26）**：dsh-TUI 适配 —— `ctx.tuiPanels` 侧栏「Blender 视口」面板 +
> 点选（Web / headless 自动 no-op；无面板轮询时快速失败），详见 README §4.2 与 `docs/TUI-适配.md`、
> `docs/宿主集成-接口契约-v2.2增补.md`。本次 skill 提交 rebase 到该 TUI 工作之上发布。

### 新 · 随包 skill（真源迁到本仓）

- 内容并入 [`skills/blender-modeling/`](skills/blender-modeling/)：`SKILL.md` + 4 个 `references/` + MIT `LICENSE` + `NOTICE.md` + 随包 `README.md`
  （独立仓库 `dsh-skill-blender-modeling` 保留为非 DSH 宿主的镜像，内容与本目录同步）。
- host 侧 `apply()` 在宿主**有 `skills` 服务时**注册 bundled provider：`ctx.inject(['skills'], cb)` 延迟挂载，
  服务缺席则跳过；**任何注册失败只告警，不影响 16 个工具**（技能是附加能力）。
- provider 口径：`source: "bundled"`、`rank = 600`（与 `@deepseek-ai/dsh-skill` 的 `BUNDLED_SKILL_RANK` 同值）；
  candidate / definition 按 DSH 技能注册表契约构造，`resourceBase` 指向包内技能目录 ⇒ 引用 `references/*.md` 的相对路径可直接读。
- **优先级**：DSH 规则 rank 小者优先 ⇒ 用户自己装在 `~/.dsh/skills`（user 级 rank 400）的副本覆盖包内副本（600），
  老用户不会被强制更新；卸载插件即自动消失（不需要清理用户技能目录）。
- 不新增运行时依赖：frontmatter 用约 20 行手写解析（只取 `name` / `description`），不引 `yaml`，不 import 宿主的 `@deepseek-ai/dsh-skill`。
- 打包面：`package.json.files` 增加 `skills`；tgz **1.5 MB / 161 文件**（v2.1.0 为 1.3 MB / 144 文件）。

### 新 · 回归门

- 新增 `tests/skill_bundle_selftest.mjs`（接入 `npm test`）：
  资产树 / frontmatter / 无 `.bak` / `files` 打包面 / lib 中 provider 形状 / frontmatter 解析边界 /
  fake ctx 三条注册路径（`inject` / 直挂 / 无 skills 服务）/ **真 `SkillRegistry` 集成**（找到宿主才跑：`list`/`get` 全链路 + 用户级 rank 400 覆盖 bundled 600）。
  本机 **61/61 PASS**；没有宿主包的环境会明确 skip 真 registry 集成段（57 断言，其中含 4 条带 `AbortController.signal` 的 thenable 断言）。
- **复核发现的 P0（同日修复）**：provider 的 `list` / `get` 原先**同步**返回数组 / 对象；宿主 `waitWithAbort()` 在调用方带 `signal`
  时会直接对返回值 `.then(...)`（模型侧每条 lookup 都带 signal）⇒ `TypeError: promise.then is not a function`，provider 被**静默 skip**，
  skill 实际加载不了（无 signal 的测试恰好绕过）。改为 `async` 后在 selftest 里补带 `AbortController.signal` 的断言（先复现失败、后验证修复：
  带 signal 的 `snapshot` 命中、`get()` 返回 103,925 字符正文）。
- 实现细节：v2.2.0 代码块放在 `apply()` **之后**（函数声明提升，行为不变）—— 若插在 `registerSchemaDiagnostic` 之后，
  会干扰 `discoverability_selftest` 对 `blender_viewport` 的 schema 字符片段切分（其口径依赖相对位置），
  把预算误报成 12,317。调整后 schema 预算保持 **11,995 / 12,000**，**没有放宽这道门**。

### 文档

- README（EN / zh-CN）：「Companion skill」改为「Bundled skill（加载时自动注册）」，§1 增随包说明，§3 安装补「重启 DSH 后技能目录自动出现」；
  Releases tgz 信息更新到 2.2.0（1.5 MB / 161 文件）。
- `AGENTS.md`：打包健康值更新；新增「随包 skill 真源 = `skills/blender-modeling/`」约定。
- 版本三处同步 **2.2.0**（`package.json` / `src/index.ts` 的 `PLUGIN_VERSION` / 重建后的 `lib/`）。

### 验收（实测，2026-10-19）

- `npm test` **exit 0**（含新增 skill_bundle_selftest）。
- 真宿主 `SkillRegistry` 集成：`ctx.skills.list()` 命中 `blender-modeling`（`source=bundled`）· `get()` 加载 103,925 字符正文 ·
  `resourceBase` 指向包内目录 · 模拟 user 级副本（rank 400）成功覆盖 bundled（600）。
- **带 `AbortController.signal`** 的真注册表 `snapshot` / `get` 实测通过（修复前：snapshot 为空、get 抛 `TypeError`）。
- `npm pack --dry-run`：**1.5 MB / 161 文件**，`skills/blender-modeling/**` 8 个文件全部在包内。

### 未证实（需重启 DSH 后由用户确认）

- 真实 DSH 会话里技能目录是否出现在**首条消息**的 available-skills 列表（本会话由用户级软链提供同一技能，无法在不重启的情况下验证 bundled 路径）。
- 用户同时保留 user 级副本时，DSH 日志会有一条 "higher-priority skill already exists" 提示（预期行为，不是故障）。

---

## v2.1.0（2026-10-16）—— 面板缩放/平移 · 一次多选零件 · headless 作业区

**来源**：用户提问 —— ① 多子代理并行建模用的是 headless，"这怎么看"；② "能一次多选零件吗"；③ "可以在标签页 blender 视口里进行放大缩小移动视角吗" → 裁决"都做"。
**唯一真源**：`docs/宿主集成-接口契约-v2.1增补.md`（§8.1 缩放/平移 · §8.2 多选 · §8.3 作业区 · §8.4 判据 18–26）。

### 新 · 面板缩放/平移（§8.1，纯客户端）

- `{zoom∈[1,8], tx, ty}`：**滚轮**以光标为锚点缩放、**按住拖拽**平移、**双击或「⟲ N×」**复位
- **实现要点**：transform 加在**包裹 img 的容器**上，而点击处理器读的是 img 自己的 `getBoundingClientRect()` ——
  **祖先 transform 会计入该 rect** ⇒ 纯 scale+translate 下 `(clientX-rect.left)/rect.width` **仍精确等于图片内比例**，
  **无需手写逆变换**（比契约 §8.1 的原公式更省且更不易错）
- 选物标记与 img 同处一个变换容器 ⇒ 放大后**标记不飘**；拖拽过 3px 即抑制紧随的 click ⇒ "平移一下"不会顺手选走物体
- 捕获 `AUTO_CAPTURE_MAX_SIZE` **1000 → 1600**：这是缩放**有意义**的前提（否则只是把低清图拉大，点小零件照样点不准）

### 新 · 一次多选零件（§8.2）

- 后端：`request{multi:true}` → `answer` 命中**按名去重累积且仍 pending**（回 `{ok:true,state:"pending",added,objects}`）；
  落空 `{ok:false,state:"pending",error:"empty"}` **不结束**；`answer{done:true}` 才 `answered`（objects 冻结）；
  新增 `POST /host/pick/remove`；`GET /host/pick/result` 两种模式都回 `objects`；上限 `PICK_MULTI_MAX=200`
- 面板：「多选」开关（连续点累积、再点一次取消该件、空点**不清空**）、已选清单（每件「✕」移除）、
  「**加入消息**」一次插入全部名字；模型发起的多选请求下出现「**完成(N)**」—— **唯一**能结束多选请求的路径
- 工具：`blender_rt_pick` 新增 `multi` 入参，多选时返回**对象名数组**并逐行列出
- **单选语义逐字不变**（§7.2）：空点仍 `answered` + `object:null`，回归已验

### 新 · headless 作业区（§8.3）

- **为什么需要**：`blender_rt_headless` / `blender_rt_job` 跑的是**独立 `blender -b` 进程**（自己的场景、不碰 GUI）
  ⇒ 视口与面板**永远没有画面**（架构必然，非缺陷）。作业注册表在**后端进程**，故加两条**只读** `/host` 路由即可
- `GET /host/jobs?limit=` → `{ok,jobs,running,total}`（running 在前，其余按耗时倒序）；`GET /host/jobs/log?id=&tail=`
  → `{ok,id,stdout,stderr,job}`（未知 id → `ok:false,error:"unknown_job"`）
- 数据源**复用已有** `engine.job.list/status/collect`（不重造）；`trimJob()` 裁大字段、`resultJson` >8 KB 截断并置 `resultTruncated`
- 面板「作业」区：状态（● 运行中 / ✓ 完成 / ■ 已终止）、阶段、已跑时长、**静默时长**（判"卡住还是在跑"）、行数；
  点一条展开日志尾部（限高 160px 可滚动）；**只在有 running 时 2 s 轮询，全空闲 10 s**

### 验收（实测，2026-10-16）

- `npm test` **exit 0**；`capability_lock` **PASS**（28 family / 186 op / **16 工具**）；客户端 bundle 自检全绿
- **描述预算门守住未放宽**：加 `multi` 入参后一度 **12 023 / 12 006 > 12 000** ⇒ **改文案压回**（最终 ≤ 12 000），
  而非放宽这道门（v1.2 立的规矩）
- 后端状态机单测 **19/19**：累积 / 按名去重 / `remove` / `done` 冻结 / 上限 200 / superseded 保留已选 / **单选回归 3 条**
- 真机全链路：`request(multi)` → `answer`(空点，仍 pending) → `remove` → `done` → `answered`；单选空点回执与 v1.2 **逐字一致**
- `/host/jobs` **零 Blender 往返**：20 次轮询 `metrics.calls` 增量 **0**；clamp `0/-5→1`、`99999→100`、非法→默认 20
- 版本三处同步 **2.1.0**（`package.json` / `src/index.ts` / 重建后的 `lib/`）

### 未证实（重启 DSH 后由用户确认）

- 面板内缩放/拖拽的**真实手感**、`getBoundingClientRect` 在右侧栏里的**实际值**、作业区在真面板里的**排版**
- 客户端半边与新增工具面**必须重启 DSH** 才装载

---

## v2.0.0（2026-10-16）—— 改名：去上游品牌痕迹（addon `MCP for Blender` → `DSH Blender Bridge`）

**来源**：用户裁决 —— ① 插件依赖的 Blender 侧 addon 一并改名（文件名 / 显示名 / 标识前缀）；② 协议判别 id 去上游项目名（`ahujasid` → `flat`）；③ 历史重写 + force-push。
**唯一真源**：`docs/改名方案-去上游痕迹.md`（逐字命名映射）。

### 改名面

- **Blender 侧 addon**：`blender_mcp.py` → **`dsh_blender_bridge.py`**；`bl_info["name"]` `MCP for Blender` → **`DSH Blender Bridge`**；标识前缀 `blendermcp_` → **`dshbb_`**（34 个去重标识，含注册类 `bpy.types.dshbb_server` 与 5 个集成开关）。**版权声明与 MIT 许可证文本原样保留**（只改显示名与标识）。
- **插件侧协议 id**：`'ahujasid'` → **`'flat'`**；`addonProtocol` 合法值 `auto｜flat｜category-action`，**`ahujasid` 保留为已弃用别名**（归一化为 `flat`，旧配置不炸）。
- **协议显示标签中性化**：`ahujasid / MCP for Blender（…）` → `直连 addon · 扁平协议（无分帧）`；`harveyxiacn blender_mcp_addon（…）` → `直连 addon · category-action（换行分帧）`。
- **兼容（强制）**：插件对 addon 标识做**双名探测**（先 `dshbb_*`，取不到回退 `blendermcp_*`；`bpy.types` 同理）⇒ addon 改名前后都能连。

### 文档与迁移

- 新增 **v2.0.0 迁移说明**（见 [`MIGRATIONS.md`](MIGRATIONS.md)）：改名要点 · 需在 Blender 偏好设置里**重新启用 `DSH Blender Bridge`**（旧 `blender_mcp` 记录是死引用）· 旧配置 `ahujasid` 仍可用 · 以后升级上游 addon 会打回原形但**插件双名回退仍能工作**。
- 安装指引 / 配置参考 / README ×2 / `llms.txt` / `AGENTS.md` / `SECURITY.md` 全部改用新名；`docs/操作教程.md` §1.1 与 `docs/部署SOP.md` §3 的安装步骤已更新且仍可执行。
- **保留（用户未选择去署名，MIT 合规）**：`docs/上游整合评估-mcp-for-blender与blend-ai.md`（连文件名）与 `docs/宿主集成层评估-Codex插件与DSH移植.md` 的上游出处 / 对比叙述、本 CHANGELOG 的来源条目、`runtime/viewstore.mjs` 的 MIT 出处注释。

### 验收（实测，2026-10-16）

**addon 侧**（三份：Windows 活文件 / 工作区 v1.6 / 插件 tmp v1.7）

- 三份语法检查通过；Windows 落地件与 `tmp/dsh_blender_bridge.py` **`cmp` 逐字节一致**（md5 `d3197a75…` / v1.6 `6f0d36ef…`）
- **品牌残留清零**：`blendermcp` / `MCP for Blender` / `BlenderMCP` = **0**；剩余命中 **v1.7 = 11 行、v1.6 = 8 行，100% 是允许位**（9/6 条环境变量兼容回退表 + 署名行）
- **标识守恒**：v1.7 **34 → 34**、v1.6 **25 → 25**（逐个只差前缀）；变更行数守恒（`modified` == 含关键串行 − 允许位保留行：267 / 190，`deleted = 0`）⇒ 无关行一行未动
- **署名逐行全等**：v1.7 7 行 / v1.6 5 行（文件头版权、`bl_info["author"]`、`terms_url` 等）
- **环境变量回退功能实测**（非读码推断）：9 / 6 条全部通过「未设置 → None · 仅新名 → 新值 · 仅旧名 → 回退旧值 · 新旧并存 → 新名优先」

**插件侧**

- `npm test` **exit 0**；`protocol_selftest` **28 → 39 项**全绿（新增别名归一化 / 缓存共用 / 双名回退 / 新名优先断言）；`capability_lock` **PASS**（28 family / 186 op / 16 工具）
- 真机（Blender 在跑、addon 磁盘已是新名而**运行会话仍是旧标识**）：`/doctor` → `kind=ok`、`protocol=flat`、`protocol_label` 为中性串；`/host/ready` → `{"ok":true,"ready":true,"protocol":11,"backend":"2.0.0"}`
- **双名回退即生产路径**：`dshbb_use_*` 全缺、`blendermcp_use_*` 全在 ⇒ 回退分支被真实执行；`/commands` 的 gates 已用 `dshbb_use_*` 作键而**行为逐字不变**（19 available / 15 disabled）
- **名字启发式放宽是必需的**：运行中的模块名已是 `dsh_blender_bridge`，旧的 `"mcp" in name` 过滤会**漏掉它**（真机反证：`dsh_blender_bridge` 在旧口径 `false`、新口径 `true`）

**历史重写**

- main `1875b79…cc79b72` force-push（82 提交全部重写）；**16 个 tag**（v0.4.0–v1.0.5）重写到对应新提交并强推（annotated 保留 tagger 与 message）
- **核验**：`git rev-list --all` 中含品牌串的提交**只剩 1 个 = HEAD**，且全部落在 §4 允许位（祖先 81 提交：`MCP for Blender` 0 / `blendermcp` 0；16 个 tag：0）
- HEAD 内容 **tree `baa3d447…` 逐字节未变**（允许位完好）
- ⚠️ **未清**：GitHub 自管的 `refs/pull/*`（旧 PR #2/#5/#9/#13）仍指向重写前提交 —— push 删不掉，需在 GitHub 上删 PR

---

## v1.2.0（2026-10-16）—— 新：右侧栏视口面板（**通道可用才出现**）· 模型请用户点选（`blender_rt_pick`）

**来源**：用户裁决 —— ① 面板不该常驻（不建模时别占地方），改成"启用了才出现"；② 模型要能主动问"是哪一个，点一下"。

### 新 · 面板搬到右侧栏，并且只在 Blender 通道可用时出现

- **座位**：从 `conversation.view` 改为**右侧栏标签页**（`sidebar.right.pane.tab`，keyed/session 槽位）
- **门控**：新增**便宜的就绪端点** `GET /host/ready`（客户端每 **2 s** 轮询它）。`ready:true` 才注册 tab 类型与主体
  —— 两阶段都包在 `ctx.effect` 里 ⇒ **注销即消失**；转 `false` 就 dispose 并关掉已开的标签页
- **自动开页**：`false→true` 边沿自动 `openTab`，**按会话记账**（同会话反复翻转只开一次；用户手动关掉后不反复弹）
- **降级**：宿主没有 `sidebarRight`（旧版本）→ 退回 `conversation.view` 并在面板里说明

### 新 · 模型请用户点选（第 16 个工具 `blender_rt_pick`）

- 后端五条路由：`POST/GET /host/pick/request`、`POST /host/pick/answer`、`POST /host/pick/cancel`、`GET /host/pick/result`
- 全局**至多一个** pending；新请求把旧的置 `cancelled`/`superseded`；`expired` **惰性判定**；结束态留最近 8 条
- **`answer` 与 `/host/view/pick` 共用同一份实现**（抽出 `hostPickCore()`，避免两份拷贝日后漂移）
- **错配不结束请求**：`scene_mismatch` / `unknown_seq` → 请求**仍 pending**（用户刷新后重试）；
  命中或点空处 → `answered`（**空处 = 用户明确表示"没有"**）
- 工具**阻塞**等待：400 ms 轮询，命中即返回对象名（`bpy.data.objects[...]` 可直接用）；
  **响应宿主取消**（abort → `cancel(reason:"aborted")` 后快速退出）；`isConcurrencySafe:false`（全局单请求）

### 修 · 两处真 bug

1. **`/host/ready` 第一版 36.7 ms**（比 `/health` 慢 14 倍）—— 根因**不在路由逻辑**，而在 `engine.mjs` 的
   **轨迹包装器**：它没进 `TRAJ_SKIP`，于是**每次轮询都 `fs.appendFileSync` 到 drvfs 挂载**（本机一次同步写 ~35 ms）。
   加进 `TRAJ_SKIP` 后 **36.7 ms → 1.19 ms**。⚠️ 对"面板每 2 s 轮询"的端点这是致命的（又慢又刷爆台账）；
   以后再加高频只读端点要一并注意。
2. **`tests/discoverability_selftest.mjs` 重复计数**：它按**数组**取工具名，而 `blender_viewport` 在"降级占位"
   分支里被**再声明一次**（占位工具复用既有名，不是新能力）⇒ 被算两遍（多计 ~175 字符），把 12000 的描述预算门
   顶到上限。`tests/capability_lock.mjs:26-29` 早就为**同一原因**按集合去重，该测试漏了 —— 现按同一口径去重。
   **合计门保持 12000 未放宽**（16 个工具实测 **11 949**）；逐工具 <1600 的硬门自始至终未动。

### 能力面

- 模型侧工具 **15 → 16**（`blender_rt_pick`）；`tests/capability.lock.json` 用 `--update` **显式记录**
  （28 family / 186 op 未变）
- `runtime/classes.mjs` 的 `MODEL_FACING_TOOLS` 同步补上 —— 它被 `guidance_selftest` 与能力锁**逐名对拍**，
  且该清单进 `blender_rt_plan(op="catalog", args={handoff:true})` 的**交接块**：漏了会让子代理**不知道有这个工具**；
  那处硬编码的"模型侧 15 个工具"改为按数组长度生成（防同类漂移）

### 验收

- `npm test` **exit 0**
- `/host` 判据 1–8 + 11–15 独立自检 **99 通过 / 0 失败 / 0 跳过**；客户端 bundle 自检 **40 / 0**
- 客户端探针 **218 / 0**（门控 ①–⑦ + 点选 UI 全绿）
- 判据 11：`/host/ready` 中位数 **1 ms** vs `/doctor` 198–410 ms（**162–400×**），且 `metrics.calls` **增量 0**（不碰 Blender）
- 描述预算：16 工具 **11 949 / 12 000**

### 未证实项（照实登记，见契约 §7.8 与操作教程 §10.8）

① 右侧栏**真实渲染形态**（`kind` 是否真变 tab、`guide` 是否真渲染成引导项）；② `useTabInfo()` 真实返回
`{sidebar,panel,tab}`；③ `dispose()` 是否真移除窗口、`openTab` 未注册时是否真 throw；④ "面板不可见时不轮询"
是否真省资源；⑤ **已知能力边界（非缺陷）**：降级座位（`conversation.view`）拿不到 `tab.visible`，故降级路径下
§7.4 的轮询会一直跑；⑥ **必须重启 DSH 才生效** —— "面板只在通道可用时出现"的最终验证只能由人重启后做。

---

## v1.1.0（2026-10-16）—— 新：宿主集成（视口面板 · 点击入上下文 · `@` 提及）；修：文件级 `restore` 的 filepath 语义

### 新 · 宿主集成：把 Blender 视口搬进 DSH 对话界面

**来源**：上游 `ahujasid/mcp-for-blender` 的 **Codex 插件**（`integrations/codex/`，plugin 2.1.1）—— 它把最新一帧视口内联在对话旁，
**点击画面里的物体即把该对象挂进下一条消息**，并提供 `@` 提及与缩略图选择器。本版把这套**体验**用 DSH 原生设施实现
（**不是**照搬 MCP Apps 协议 —— DSH 不是 MCP 宿主，要的是同一体验）。评估与接口契约见
`docs/宿主集成层评估-Codex插件与DSH移植.md` 与 `docs/宿主集成-接口契约.md`。

**全部走独立 `/host/*` 路由，不进 `/plan` op 目录、不加模型工具 ⇒ 能力面零变更**

| 能力 | 实现 |
|---|---|
| 视口状态机 | `runtime/viewstore.mjs`（新）：`seq`/`source`/`captured_at`/`origin(file,scene)`/`stale`/`pickable`/`blender_quiet`；场景变更计数；在飞计数；`AUTO_CAPTURE_QUIET_S=0.8`；最近 50 帧相机矩阵 |
| 五条宿主路由 | `GET /host/view/latest` · `GET /host/view.png` · `POST /host/view/capture` · `POST /host/view/pick` · `GET /host/scene-items`（`runtime/server.mjs`） |
| CORS | 仅对 **loopback** Origin 回显 `Access-Control-Allow-Origin`；`OPTIONS` 预检 204 |
| 反投影 pick | `runtime/view.py::host_pick` —— 复用已有 `make_view_matrix`/`make_proj_matrix` 反投影 → `scene.ray_cast` → 灯光/相机/空物体按屏幕距离兜底；**先校验 `.blend`/场景错配并拒绝**（`file_mismatch`/`scene_mismatch`），绝不把点击投到当前场景 |
| 客户端半边 | `src/client/index.js`（新，手写 bundle，零打包器/零新增依赖）+ `scripts/build-client.mjs`；`package.json` 加 `dsh.client` 与 `exports["./client"]` |
| 视口面板 | 注册到 `conversation.view` 槽位；轮询 1500ms（陈旧 600ms）；`seq` 变化才换图 |
| 点击入上下文 | 点击 → 归一化坐标 → `/host/view/pick` → 命中加标记（再点取消）；「加入消息」把整帧作为**草稿附件** + 一句对象说明**写进草稿**（`ctx.conversation.createDrafts` + `inputActions.addAttachments/insertText`）—— **不自动发送** |
| 自动重拍（上游 A8） | `stale && blender_quiet` → `POST /host/view/capture {auto:true}`；**1200ms 节流**、同一 `seq` 不重复、**连续 3 次无进展停手**并在状态行说明、`visibilityState==="hidden"` 停手、pick 在飞不并发 |
| `@` 提及 | `ctx.inputTriggers.registerSource({trigger:'@'})`，数据源 `/host/scene-items`，按 对象/材质/集合 用 `section` 分组；候选失败返回 `[]` 不抛 |

**两条设计纪律（与上游同源）**

- **面板轮询不碰 Blender**：轮询只读 store。实测 60 次轮询 `metrics.calls` 增量 **0**（面板开着不占 Blender 主线程）。
- **pick 不得把自己刚抓的帧标成 stale**：`execute_code` 属"会改场景"命令集，而 pick 会经 `ensureView()` 发 `execute_code` ——
  故对**插件自身的 python 模块加载**与**三条只读 host 原语**用逐调用 `{noMark:true}` 排除（**不用**全局抑制计数器：
  那会在别的会话的写命令恰好落在窗口内时吞掉它的标记，**欠标 stale 是危险方向**）。

**验收**

- `/host` 契约判据 1–8：独立自检 `tests/host_routes_selftest.mjs` **68 通过 / 0 失败**；Lead 抽查 CORS 预检/PNG 魔数/陈旧 409 复现一致。
- 客户端半边：`tests/client_bundle_selftest.mjs` **29 通过 / 0 失败**（假 `window.__ModuleLoader__` 装载 `lib/client.js`）；6 类蓄意违规 fixture 全部 exit 1。
- `npm test` **exit 0**；`tests/capability.lock.json` **未变**（28 family / 186 op / 15 工具）。
- 现场复原：验收造的探针对象全部删除（2256 对象 / 10 材质 / scene `Scene` / 未保存文件）。

**边界与未证实项**（照实登记，**勿当已证实能力**）

1. `conversation.view` 的**实际渲染形态**未在真 GUI 观察：按类型是**对话区的 View 页签**（非常驻侧栏）。要常驻侧栏需换 `main` 或 `sidebar.right`。
2. `label` thunk → `ViewTab.label`、`InputTriggerCandidate.section` 是否真渲染成分组标题、`codec.serialize` 是否原样进模型文本 —— 均**未实测**。
3. HTTP 级 `file_mismatch` 未实测（`bpy.data.filepath` 只读，造真实错配需保存/另开 `.blend`，会动用户**未保存**的 2256 对象场景 ⇒ 拒绝做；
   已在**函数级**用控制组验证，`scene_mismatch` 是 HTTP 级实测）。
4. 灯光/相机/空物体的屏幕距离兜底**已实现但未触发**（当前场景全为 MESH）。
5. `document.visibilityState` 在 DSH GUI 里的真实行为未实测（失效方向偏保守）。
6. **必须重启 DSH 才生效**（客户端半边是启动时装载的模块）；面板在真 GUI 里的最终验证待重启后做。

---

### 修 · 文件级 `restore` 的 filepath 语义（`blender_rt_txn`，TXN_VERSION 3 → 4）

**来源**：外部使用报告（"事务 / 回滚"一节）。原话是"原文件没被动（安全），但之后一保存就会存进快照目录，语义意外"。

### 现象与成因

`restore` 用 `bpy.ops.wm.open_mainfile(<out_dir>/snapshots/<label>.blend)` 把**整个会话**搬到快照文件上，
`bpy.data.filepath` 随之变成快照路径 —— 原文件确实一直没动，但**之后任何保存（Ctrl+S / `save_mainfile`）
都会写进 `snapshots/`**。用户"以为在改自己的工程"，实际写到了插件的临时目录。

### 修法（`runtime/txn.py`）

- **`restore(label, into="auto", backup=True)`**：
  - `into="auto"`（默认）：载入快照后**把会话放回快照的出处文件**（优先快照记录里的 `filepath`，
    其次会话当前文件；两者都落在快照目录里时不写）。即"恢复进原文件" —— 保存落回自己的工程。
  - `into="snapshot"`：**非破坏**模式，只载入、一个字节都不写原文件；回执给 `next_save_goes_to` 明确"现在保存会落哪"。
  - `into="original"`：要求必须能回写，兜不到目标就 `ok:false`（**不静默降级**）。
  - `backup=True`（默认）：载入前先存一份 `_pre_restore_<label>_<ts>` 快照（restore 会丢当前未保存状态，
    这个 undo 点必须留）；会话脏且要覆盖原文件时，再另存一份**原文件的磁盘副本** `_pre_restore_file_*`
    （内存态 ≠ 磁盘态，覆盖前两份都要有底）。两份都进快照清单，能 `list` 到、能 `restore` 回来。
- `snapshot` 的记录新增 `filepath` / `unsaved` —— 之后开过别的文件，`restore` 仍认得出"原文件在哪"。
  （**目标顺序有讲究**：先看快照的出处。用户可能已经开了**别的**工程再 restore，那时写"当前文件"
  等于毁掉另一个工程。）
- 回执补 `filepath_before` / `filepath` / `wrote_to_original` / `target` / `target_source` /
  `next_save_goes_to` / `backup` / `on_disk_backup` / `warning`。
- `TXN_VERSION` 3 → 4；`t_help` 的 `file_level.restore` 口径同步。

### 测试

- 新增 **`tests/txn_restore_selftest.py`**（headless，自造合成场景，`preload="txn"`）—— **23 项断言**：
  默认 restore 后会话回到原文件、`into="snapshot"` 时原文件 md5 一个字节没变、会话已漂到快照目录时
  靠快照记录的 `filepath` 仍兜回原文件、未保存场景不乱写只告警、pre_restore 备份真落盘且能 `list` 到。
  **先跑红（老代码 14 过 9 失败）再修**，修复后 23/23。
- 回归：`tests/txn_selftest.py`（含 deliver 交叉断言）**55/55 全绿**（版本号断言同步到 4）。

### 工具面

`blender_rt_txn` 新增参数 `into` / `backup` 并转发到 `/txn`；描述里的旧口径
"restore 会丢掉当前未保存状态" 改为"默认放回原文件 + 载入前自动备份"。
`src/index.ts` 已改，**`lib/` 需重建**（工作树里另有未构建的在飞改动）。

## 未发布（v1.0.6 待定）—— S0 草稿评估：参考图还原的第一步改成「先出制作表格，不量尺寸」

**动机（用户口径）**：建模的**评估**阶段一上来就量精确尺寸、统计 mesh 数量，等于在**还没有参考图**的时候
把编出来的数字当成规格；而这一步本该只是"在脑子里打草稿"——认清是什么 / 大概多大 / 拆成几块 / 谁是主体谁是配件 /
哪块硬表面哪块曲面 / 哪里镂空·凸起·凹陷，后续返回图片时再慢慢完善。

### 新增 `shape_draft` / `shape_draft_check`（shape 族，只读）

- **`shape_draft(object_class=?)`**：返回**制作表格的字段契约 + 可填骨架**，**不返回任何测量值**。四张表：
  T1 识别与量级（是什么 / 归哪类 / 量级档 A–F / 对称 / 轴向）、T2 制作单元（每件：role 主体·配件·重复件 /
  parent / form_class / features / build_path / evidence / to_confirm）、T3 形态（faceted·smooth·organic·
  rotational·plate·repeats·sweep）、T4 空间特征（镂空·凸起·凹陷·缝·薄壁·格栅）。
  给了 `object_class` 就附带该类的**关注项名字**（要盯哪些比例 / 走哪条 `section_path`），**不含数字**。
- **三条硬规则（机检）**：R1 不量精确尺寸（尺寸只能是量级档或 `0.3×` 这类相对比例，出现 mm/cm 即拒收）；
  R2 不数 mesh（列出本阶段禁止调用的 `audit_scene`/`audit_mesh`/`print_report`/`shape_sections`… 清单）；
  R3 不定死（每字段带 `confidence` + `source`，判不出写 `unknown`，每件必带 `to_confirm`）。
- **`shape_draft_check(record=…)`**：把填好的表格机械校验（字段齐不齐 / 枚举对不对 / 主体是否为 0 或 >1 /
  **有没有偷偷写 mm 级绝对值**），返回 `errors` / `warnings` / `stats`。`ok=true` 只表示"表格合格且没把数据定死"，
  **不代表尺寸被验证过** —— 数字一律等 S2 标定 / S5 验收。
- **迭代路径**随回执给出：S0 草稿（无图）→ S1 `img_scan`（像素 ≠ 毫米）→ S2 `img_rectify` 标定（第一次有数字）
  → S3 站表/截面 → S4 出图对照 → S5 验收（**这一步才**数面数、量间隙）。

### 指引同步（`catalog` / `classes` / 技能文档）

- `catalog` 的 shape 族：`when` 改成「**先 S0 草稿** → `shape_plan` → 放样→特征线→IoU」，`not` 加
  「**S0 别写 mm、别调 `audit_*` 数面数**」；ops 加 `draft` / `draft_check`（默认页长度门仍通过）。
- `classes.mjs` 的 `recon` 类：`entry` 改为 `shape_draft`（第一步），chain 前置 `shape_draft → shape_draft_check`，
  验收门分 S0 / S3 两段；`renderHandoff()` 硬规则 1 同步。
- 技能侧：`references/参考图通用还原SOP.md` 由「八步」改为「**九步**」（新增 ⓪ 草稿评估 + §1.1 四张表 +
  §5 草稿→数字的迭代纪律）；`SKILL.md` 新增 §0.5.0「冻结规格之前先打草稿」，并改 §0.7 决策树与四条硬规则第 1 条。
- 计数：`28 family / 184 → 186 op`（`src/index.ts` 描述与工作区 `AGENTS.md` 经 `scripts/sync_counts.mjs` 刷新；
  `capability.lock.json` 与 `tests/plan_readonly.baseline.json` 同步更新）。
- `SHAPE_VERSION` 1 → 2；`shape_selftest` 增补 S0 断言（模板四表齐全 + 合法 record 过 + 写 `450mm` 必须被拒）。
- 自检：`npm test` 全绿（能力锁 / 单一事实源 / 可发现性 / 指引完整性 / 路由契约等）。

## v1.0.5（2026-10-07）—— issue #14：悬垂默认不再把「贴床底面」算成需要支撑（+ 两个各向异性缩放的旧账 + `npm test` 在 Windows 上跑不到一半）

### 修 1（P1 · issue #14）：`print_overhang` / `print_report` 的贴床面口径

- **症状**：FDM 零件绝大多数是平底，而旧口径把「法线朝下的面」一律算悬垂 ⇒ **平底零件默认 `overhang_ok=false`**。
  报告人的 PLA 支架 `overhang_area_mm2=5351.57`，其中 **97%（5198.49mm²）是 z=0 的贴床平底**；
  1 单位立方体 `overhang_ratio=0.1667`、ok=false。
- **根因**：判据只看法线（`c >= cos_lim`），从没问「这个面是不是贴在打印平台上」；而回执 note 却让用户
  「底面贴床的平面请用 `min_area_mm2` 排除」—— 与实现矛盾（那是**面积预算**，抬高预算会把真悬垂一并放行）。
- **改法**：新增 `exclude_bed=True`（默认）、`bed_tol_mm=0.2`、`bed_mode=flat|min`、`bed_horiz_deg=1.0`、`bed_band_mm=1.0`。
  贴床面 = 水平朝下（≤`bed_horiz_deg`）**且**标高落在平台面 ±`bed_tol_mm` 内；平台面 `flat`（默认）取
  「面积最大的水平朝下层」的标高、`min` 取所选集合最低点。回执：`overhang_area_mm2` = **净悬垂**、
  `bed_area_mm2` 单列贴床面、`overhang_area_incl_bed_mm2` 保留老口径、`bed_level_mm`/`lowest_mm` 可复盘；
  `exclude_bed=False` 整体回退 v1.0.4 老口径（净悬垂/占比/ok 逐值等价）。
- **顺手堵掉一个假绿**：`flat` 若无条件信任「面积最大的水平朝下面」，**蘑菇件**（细柱 + 宽帽）会把帽底
  当平台面 ⇒ 实测 200×200 帽底 **40000mm² 真实悬垂被整块吞掉、`ok=true`**。加 `bed_band_mm` 防线：候选层
  离最低点超过 1mm 就判定「它不像平台面」→ 退回 `min` 口径并写 `bed_warning`。修后同一几何：
  `bed_mode=min-fallback`、帽底 40000mm² 照算、`ok=false`。
- **不确定不许读成绿**：`print_report` 顶层新增 `warnings[]`（平台面判据的自证警告，`rows[].bed_warning` 同步），
  `gate.py` 的 print preset 把 `len(warnings) > 0` 接进 `degrade_if` ⇒ 这类回执在门里是 **degraded**。
- **已知边界（写进 `print_help` 的 limits）**：`bed_band_mm` 以内的差值判不出来 —— 「距台 ≤1mm 的宽面」与
  「底缘碎面」是同一形状，`flat` 一律算贴床（FDM 能桥接）；另 `flat/min` 都几何自推、**位置无关**，
  整件悬空时最低点就是它自己，要按绝对平台判请先落台或用 `exclude_bed=False`。
- **字段契约变更**：`PRINT_VERSION` 1 → 2；`overhang_area_mm2` 语义翻转（默认不再含贴床面）。
- **自检**：`print_selftest` 从 1 个形状扩到 **8 个**（平底立方体 / 悬臂 / 平底碎块 / 蘑菇帽 / 斜面不许当贴床 /
  单件 T 的比值 / 非均匀缩放的面积真值与法线），期望值全是解析解而非实现回抄；并接进真机 A1–A5 套件。

### 修 2（旧账 · v1.0.4 就有）：各向异性缩放下法线与面积都算错

- **法线**：`M.to_3x3() @ n` 应为**逆转置** `((M⁻¹)ᵀ @ n)`。scale(2,1,1) 下真法线离正下方 16°（必须支撑）被算成
  49° ⇒ **整块面丢出悬垂集**（实测修前该斜面悬垂 0.00mm²、修后 0.34mm² 计入）。
- **面积**：`p.area × (_scale_avg × mmu)²` 在 scale(2,1,1) 下偏 **-11.1%**（1.777e6 vs 真值 2e6 mm²）；
  改为按世界坐标三角扇累加（旋转/等比缩放下与原值等价，只有各向异性缩放会变）。

### 修 3（issue #10 的跟进项）：`npm test` 在 Windows 上跑不到一半

- `import(path.join(...))` 得到的 Windows 盘符路径（`C:\…`）会被 ESM 当成 scheme `c:` ⇒
  `ERR_UNSUPPORTED_ESM_URL_SCHEME`（**任何平台都可复现**，不只 Windows）。**11 处**统一改成
  `pathToFileURL(...).href`：`capability_lock` / `single_source` / `guidance` / `shape_search` / `backend_probe` /
  `route_contract` / `mac_selftest` / `sync_counts` / `dsh_api_compat_probe`（两处内联脚本）/
  `schema_resolve_selftest`（父进程传 URL + 子进程自转 URL）。
- `tests/schema_resolve_selftest.mjs` 新增 **E 组**：Windows 风格裸路径不得再撞 scheme 错 —— 这条**任何平台都能跑**，
  所以 Windows 的失效模式在 Linux CI 上就有回归保护。
- `tests/protocol_selftest.mjs` 的「死端口」原为 `flatPort + 4242`：OS 动态端口在 49152–65535，> 61293 时越界 ⇒
  `Socket.connect()` 抛 `ERR_SOCKET_BAD_PORT`、那句断言根本跑不到（约 **26%** 概率间歇红）。改为 bind(:0) → close
  取真死端口，并把原来那条**恒真**断言换成「死端口 ≠ 任一 mock 端口」。
- `tests/README.md` 的 preload 示例漏了 `pipeline`（实测红 2 条：`t_pipeline` / `t_refs_single_op` —— 是示例漏项、
  不是插件缺陷），已补全；断言数订正为实测值。

### 验收（全部在最终改动之后实测）

| 门 | 结果 |
|---|---|
| `npm test`（16 步） | **EXIT=0** |
| `print_selftest`（真机 Blender 5.2.2） | **ok=true** · 8 形状 |
| `gate_selftest` | **ok=true**（新增真机用例：`warnings` 非空 ⇒ 该门 degraded） |
| A1–A5 集成自检（真机无头） | **128 / 0 失败** |
| gate 复审回归（真机无头） | **52 / 0 失败** |
| issue #14 复现（默认 Cube） | 修前 `4000000 / 0.1667 / ok=false` → 修后 **`0 / 0 / ok=true`**；`exclude_bed=False` 仍复现老数字 |
| 变异测试（故意改坏实现） | 忽略 `bed_horiz_deg` → 红；`overhang_ratio` 分母写错 → 红；法线退回 `M@n` → 红；面积退回 `_scale_avg` → 红；拆掉 `bed_band_mm` → 红 |

## v1.0.4（2026-10-05）—— 原生 Linux 成为一等平台：修 `wslToWin()` 把"非 Win/mac 的宿主"一律当成 WSL（取帧恒报 `frame http 502`）

### 修（issue #11，报告人 @mlfc666，Manjaro 原生 Linux）
- **症状**：原生 Linux（Blender 与宿主同机）上 `blender_rt_see` 恒报 `Error: frame http 502`，`/frame.png` 回
  `ENOENT … /home/<u>/.dsh-blender-rt/dsh_live_viewport.png`；而帧 PNG **确实写出来了**，只是落在一个
  **字面量反斜杠目录** `/home/<u>/\wsl.localhost/Ubuntu/home/<u>/.dsh-blender-rt/` 里。旁证：`/doctor` 在 Manjaro 上把 `distro` 报成 `Ubuntu`。
- **根因**：`runtime/config.mjs` 的 `wslToWin()` 只分了 `IS_MAC` / `IS_WIN`，**其余一律当成「Linux 宿主 ⇒ WSL +
  Windows 上的 Blender」**，把宿主绝对路径映射成 `\\wsl.localhost\<distro>\…`。同机的 Blender 会把这个串当
  **相对路径**，于是"Node 侧写、Blender 侧读"的文件交接全线错位（帧 / 无头脚本 / 台账 / results / trajectory /
  launch 的 statusFile / staging 都走它）。macOS 当年踩过同一个坑并修了（`blenderSidePath` 的注释就是原话），原生 Linux 漏了。
- **修法**：新增 `IS_WSL` 判据（环境变量优先 → 退回 `/proc/version`；systemd 服务里 dsh **不继承** `WSL_DISTRO_NAME`，
  见 #6，所以不能只看环境变量），`wslToWin()` 在非 WSL 上**恒等返回**；判据剥成纯函数 `detectWsl(platform, env, procVersion)`
  以便在**任何平台**上对拍。顺带两处同源问题：`/doctor` 的 `distro` 在原生 Linux 上读 `/etc/os-release` 报真实发行版；
  `PATH_GUARD`（"Windows Blender 把 `/home/x` 当当前盘根下的相对路径"的体检）在原生 Linux 关闭 —— 否则每条正常脚本
  都回一条 `render_filepath_posix` 假警告，还把人往"改成 Windows 形式"引。
- **实测对照**（在 WSL 里用**私有挂载命名空间**把 `/proc/version` 绑成 Manjaro 内核串 + 清掉 `WSL_DISTRO_NAME/WSL_INTEROP`，
  于是真走到"原生 Linux"分支；同一台机器上跑修前 / 修后两份代码对拍）：

  | 探针 | 修前 | 修后 |
  |---|---|---|
  | `wslToWin('/home/u/.dsh-blender-rt')` | `\\wsl.localhost\Ubuntu\home\u\.dsh-blender-rt` | `/home/u/.dsh-blender-rt` |
  | `CFG.workDirWin` | `\\wsl.localhost\Ubuntu\tmp\dsh-blender-rt` | `/tmp/dsh-blender-rt`（与 `workDirWsl` 同构） |
  | `PATHS.livePngWin` | `\\wsl.localhost\Ubuntu\tmp\dsh-blender-rt/dsh_live_viewport.png` | `/tmp/dsh-blender-rt/dsh_live_viewport.png` |
  | `describeConfig().distro` | `Ubuntu` | `Ubuntu 24.04.4 LTS`（读 os-release） |
  | `PATH_GUARD` | 非空（假警告） | `''` |

- **回归门**：新增 `tests/linux_selftest.mjs`（`npm run test:linux`，25 断言；已挂进 `npm test`）—— `detectWsl()` 判据决策表
  （任何平台可跑，含"假内核串不能误判成 WSL"/"读不到 /proc/version 不硬猜"）+ 本平台分支自洽 + 源码回归保护（UNC 分支仍在）
  + 原生 Linux 端到端恒等。同一份自检打在**修前**代码上是 **11 红 / 16**，修后 **25 全绿**。
- **WSL / Windows / macOS 三条既有路径逐字节不变**：`wslToWin('/home/x/f.png') === '\\wsl.localhost\Ubuntu\home\x\f.png'`、
  `/mnt/d/… → D:\…`；真机上 `lease_stale_selftest`（含拉起 Windows 版 `blender.exe`、拿回 `resultJson.ok=true` 的 D2）20/0、
  `mac_selftest` 7/0，`npm test` 与修前**逐条一致**、无新增失败。（唯一红的 `shape_search_selftest` 是本机 **Windows 侧
  DSH 主进程占用了它默认的 9895 端口**，与本次改动无关：换端口单独跑 24/24 全过。）

### 已知未修（与 issue #11 报告人的附注一致，留给后续）
- `wslPathShared()` 在原生 Linux 上仍按 WSL 判据（`/tmp`、独立挂载一律判"不共享"）⇒ `/doctor` 的 `workDir.shared=false`、
  无头脚本走"替换 / 降级"分支并带 `WORKDIR_NOT_SHARED` 提示。功能不挡事（同机 Blender 读得到），但语义要重新表述；
  `workdir_shared_selftest` 的 A1/A2/A3 把 WSL 语义写成了硬断言，得跟着一起改。
- 另注：`runtime/paths.mjs` 的 python 侧用的是另一套"非 WSL 原生 POSIX"判据（`!IS_WIN && !fs.existsSync('/mnt/c')`）。
  原生 Linux 上两者结论一致（本版没动它）；只在"WSL 但没有 /mnt/c"这种边角上两套判据会分叉。

## v1.0.3（2026-10-05）—— 内环从"存在"到"真能用"：修 `ns` 未绑定（内环 6 个版本跑不通一次迭代）+ 新 op `shape_search`（声明式搜索）+ 描述改任务型触发词

### 修（P0 · 这才是"没人用 rt_loop"的真正原因）：`step`/`measure` 里 `ns[...]` 一跑就 NameError
- **症状**：`blender_rt_loop(op="start", …)` 回 `start_ok: true`，但**第一个 tick 就 error 收工** ——
  `stop_reason="error"` / `i=0` / `best=null` / `board_n=0`，`error` 尾部是
  `File "<dsh_loop.step>", line 1, in <module>\nNameError: name 'ns' is not defined. Did you mean: 'np'?`
- **根因**：`runner.py` 的 `exec(step_code, ns)` 用 `ns` 当 globals，但**从没把 `ns` 这个名字绑进命名空间**
  （`git log -S 'ns["ns"]'` 在 v0.4.0→v1.0.2 全史无命中 ⇒ 自发布起就如此）。而 README / cookbook / 工具描述 /
  全部食谱**一律**写 `ns["params"]` / `ns["score"]` —— 谁照文档写谁炸，于是"内环存在但没人用得上"。
- **修法**：注入 `ns["ns"] = ns` 与 `ns["st"] = st`（后者也是文档 preloaded 列表里承诺过的名字）。
- **实测对照**（真 Blender 无头，手动驱动 tick；无头下 timers 不触发）：
  | | 修复前 `run-muu3oj9hlcjh` | 修复后 `run-muu3ppkr6508` |
  |---|---|---|
  | `stop_reason` | `error` | `iterations` |
  | `i` | 0 | 5 |
  | `best` | `null` | `{score: 0.0687…}` |
  | `board_n` | 0 | 3 |
  | `ns_bound` | **false** | **true** |
- **回归门**：`dsh_loop_selftest` 从"只读 status/help、不真起循环"升级为**真起一个最小循环并手动驱动 4 个 tick**，
  硬判 `ns_bound / iters_done / best_present / no_error`（旧自检的结构性盲区正是这条回归活了 6 个版本的原因）。

### 改 1：`blender_rt_loop` 描述从「次数门槛」改成「任务形状」
- 触发词：对齐 / 拟合 / 反推尺寸 / 参数扫描 —— 判据能写成一个数（轮廓 IoU、包围盒尺寸误差、剖面差，或自写 score）。
- 明写硬前提：**只在 GUI 会话有效**；`op=board` 看候选；`op=export` 落可复现脚本；收敛后换通路复核；跑完场景停在**最后一次迭代**而非 best。
- 长尾（spec 字段 / `K.dsh_measure` 三签名 / 可照抄骨架 / 4 条纪律）搬进 catalog 详情：`blender_rt_plan(op="catalog", args={tool:"rt_loop"})`（1,834 字符，按需取）。
- 描述预算门仍绿：合计 **11,672 ≤ 12,000**，rt_loop **1,261 < 1,600**。
- 旧文案里的 `≥20 次才用` 是"自我否决"：模型读完就跳过 —— 换成任务型触发词后，§0 路由表 + 描述 + catalog 三级都能命中。

### 新 op：`blender_rt_plan(op="shape_search", args={…})` —— 声明式搜索
- **你只给目标与区间，机器去搜**：`objective`（`silhouette_iou` / `profile_err` / `aabb_err`）+ `params`
  （`{dy_mm:{min:-30,max:30,step:5}}` 或 `{values:[…]}`）+ `apply`（每轮把 `ns["params"]` 落到场景的代码；
  建议在 setup 里定义 `apply(p)` —— 与 `op=export` 的复现口径一致）。
- 内部**复用 runner**：`start → 轮询 → board → export` 全程托管 ⇒ **只在 GUI 会话有效**；
  `dry_run:true` 只校验并回 spec（**不需要 Blender 连着**）。
- 采样 `strategy:"auto"`：组合数 ≤ iterations 走**穷举网格**（迭代数自动收敛到组合数），否则**随机搜索**；
  可显式 `grid` / `random`；显式网格 >20000 组合直接拒绝并给骨架。
- 收尾：内环结束时场景停在最后一次迭代的参数上，本 op 默认再执行一次 `setup + apply(best)` 把场景落到 best（`restore:"none"` 可关）。
- 只读性：会通过 `apply` 改场景 ⇒ 归**写 op**（不在 `/plan` 只读白名单，受写租约约束）。
- 回执：`spec_echo` + `best` + `board` + `stop_reason` + `applied` + `exported` + `next_steps`；**任何校验失败都给 `skeleton`**。
- 代码块缩进归一化（`normBody`）：`apply`/`setup` 支持"首行顶格 + 续行缩进"写法与嵌套块（`if/else`），
  不会生成 `IndentationError` —— 由测试里"用 python3 真编译三段代码"这条断言守住。

### 配套 skill（`dsh-skill-blender-modeling`，另一仓库，同步发布）
- §0 路由表补 `rt_loop` 行；§0.7 增**第四条硬规则**（可标量化 + 要试几十~几千组 ⇒ 不许手工拍参数）；
  新增 **Recipe 24**（可复制 spec + 现成目标函数表 + 5 条纪律 + 3 种不该用的场景）+ 声明式 `shape_search` 的用法与分工；
  front-matter 描述补"参数搜索内环"、配方计数 23 → 24。

### 测试
- 新增 `tests/shape_search_selftest.mjs`（**24 项**）：目录面 / spec 生成（grid · random · aabb_err · 多视平均 · pyLit 布尔）/
  **python3 真编译三段代码（含嵌套块）** / 校验面（缺参 · 非法目标 · 组合爆炸都必须给骨架）/ 真路由（隔离后端 POST `/plan`）。
- 能力锁更新为 **28 family / 184 op**（+1 op，原因即本节）；`npm test` **16 步全绿**。
## v1.0.2（2026-09-30）—— 门禁合规（去 eval）+ issue #10 修复 + S10 自愈一并发布

> 触发：本插件投稿 [awesome-ai-plugins](https://github.com/hashgraph-online/awesome-ai-plugins)（PR #539）时，上游"必需的
> source scan"给 **73/100**（阈值 ≥80 且无 critical/high），唯一 high 是 `runtime/gate.py` 的动态求值。
> 本版把三件事一起发出去：**去 eval** · **issue #10 修复** · **S10 现场补丁回灌**，外加门禁缺分项的仓库卫生。

### 1. `runtime/gate.py`：动态求值 → 逐节点白名单 AST 求值器（门禁 high 项）
- 原实现把编译后的 AST 交给内置动态求值入口执行。语义上它早已被 `_check_ast` 限死（不许下划线属性、调用只许
  `ALLOWED_FUNCS`），但"名字+左括号"这个形态本身会被扫描器判 high（`DANGEROUS_DYNAMIC_EXECUTION`）。
- 现在新增 `_eval_node()` **逐节点按白名单求值**（BoolOp / UnaryOp / Compare / BinOp / Name / Constant /
  Attribute(非下划线) / Subscript / Tuple / List / Call），不再调用任何动态执行入口；`_check_ast` 仍是唯一入口。
- 两处**故意更严**：名字不在 env → 直接报错；调用不支持 `*args` / `**kwargs`（`_check_ast` 本就不放行）。
- 回归：新增 `tests/gate_eval_selftest.py`（**32 项**：源码面 3 · 语义等价 14 · 短路/操作数语义 4 · 拒绝面 11），
  已接入 `npm test`。它不需要 Blender（给 `bpy` 与共享内核 `dsh_rt_kernel` 打桩，只测纯求值器）。

### 2. issue #10 修复：schemastery 解析改 ESM 优先 + 顶层不再 throw
明细见下一节 `v1.0.1+S11`（含新增 `tests/schema_resolve_selftest.mjs`，14 项）。

### 3. S10 现场补丁**回灌进 `src/`**
原先只打在 `lib/index.js`，一 `npm run build` 就丢；本次补齐 src 侧，明细见 `v1.0.1+S10`。

### 4. 仓库卫生（门禁缺分项）
- 新增 `SECURITY.md`（私密上报口径 + 本插件的执行面/凭据面声明）
- 提交 `package-lock.json`（修 `DEPENDENCY_LOCKFILE_MISSING`）
- 新增 `.github/dependabot.yml`（npm 周更）
- `package.json` 的 `files` 增加排除：`!runtime/*.bak-*`、`!runtime/*.v1-*` —— 现场备份不再进包

### 验证
- `npm test` 全链绿（含新增 `tests/schema_resolve_selftest.mjs` 与 `tests/gate_eval_selftest.py`）
- `python3 tests/gate_eval_selftest.py` → **32 项 ALL PASS**
- 全仓文本扫描（含注释）：被判 high 的那条**动态求值调用形态** **0 处**；用 exec 加载源码再执行的既有写法
  原样保留 —— 上游同一轮扫描没有判它们（只报了 gate.py 那一处）

## v1.0.1+S11（2026-09-30）—— 修 issue #10：schemastery 解析改 ESM 优先 + 顶层不再 throw（「15 个工具整体消失」）

> 外部报告：<https://github.com/sixtysevenlf/dsh-blender-plugin/issues/10>（@qaz040619，证据链含包内容逐文件对拍、
> `dsh.profile.bundles` 二分、`--dump-config-schema` 与 boot 路径分叉）。**确认成立，1.0.1 未修**：`src/index.ts`
> 里确实还是 `createRequire()` 双名解析 + 模块顶层 `throw`。

### 现象
profile 里同时装 `dshmarket` 或 `dsh-context`（两者都以带 scope 名 `@deepseek-ai/schemastery` 走 ESM loader）时，
插件 entry 报 `failed to import`、**15 个工具整体消失**、后端也不起；宿主**只回一句** `failed to import`，真异常被吞。
顺序无关、包本身逐字节一致、`--dump-config-schema` 里该 entry 反而是 `schema`（导入成功）—— 故障现象与真因不在同一层。

### 根因
解析只用 `createRequire()`，且**在模块顶层 `throw`**：该 require 在多插件共存时的宿主模块解析拦截下失败 ⇒
模块**载入期**抛错 ⇒ entry 的 `fiber` 始终 `undefined` ⇒ 整体消失。本仓 `docs/DSH-更新适配.md` 早已点过这个失效模式。

### 改法（`src/index.ts`，3 处）
1. **顺序**：ESM `await import()` 优先（`@deepseek-ai/schemastery` → `schemastery`），失败再退回 `createRequire`；
   形态判定要求真拿得到 `.object()`（拿不到就判为不可用，不赌）。
2. **失败语义**：**顶层不再 `throw`** ⇒ 降级加载：`Config` 为 `undefined`，`apply()` 只注册一个
   `blender_viewport` **诊断占位**（直接回报试过的名字、每条通道的真实错误与修法）。插件不再"整体消失"，
   宿主那句 `failed to import` 之外终于有可读的现场。`apply()` 同时防御性读配置（不再假设 schema 一定补了默认值）。
3. **可诊断**：`__internals.schemaResolve`（`via` / `error` / `tried`），并在 `blender_viewport op=status` 与
   `op=doctor` 各加一行（"走了哪条通道 / 为什么失败"）。

### 顺带修：S10 补丁**回灌进 src**（否则一 build 就丢）
S10 现场补丁原先只打在 `lib/index.js`（`src/index.ts` 里没有）：`killBackendByCmd` 的 `signal` 参数、argv token
精确匹配（防自伤）、看护"先清尸再重生"（连续 2 次探活失败 → SIGTERM → 1.2s → SIGKILL）。一旦 `npm run build`
这些逻辑就会被抹掉。本次已补齐 src 侧，**src 与 lib 重新同源**，S10 的 `runtime/server.mjs` 部分本就在树里。

### 回归自检（新增，不需要 Blender）：`tests/schema_resolve_selftest.mjs`
已接入 `npm test`，另有 `npm run test:schema`。四组 fixture 各自真 `import()` 一份 `lib/` 拷贝，只改它上方
`node_modules` 里"有哪些名字"：
| 组 | fixture 提供的名字 | 断言 |
|---|---|---|
| A | 只有 `@deepseek-ai/schemastery` | import 成功、走 `esm:` 通道、`Config` 正常构建 |
| B | 只有 `schemastery` | import 成功、`Config` 正常构建 |
| C | 两个都没有 | **不抛**；`Config === undefined`；注册的是诊断占位且回报原因/修法 |
| D | 本包真身（真 node_modules） | 可 import、`__internals.schemaResolve.via` 有值 |

实测 **14 项全过**；C 组正是旧版"整体消失"的那条路径。

### 附带
- **能力锁**：`tools_count` 从"数组长度"改为**集合去重**（降级分支会复用同一个 tool 名；本测试口径本来就是
  "只看集合（工具名）"）。工具面仍是 **15 个**，不是 16。
- issue #10 里第 3 条（`tests/protocol_selftest.mjs` mock 端口随机到 >65535 触发 `ERR_SOCKET_BAD_PORT`）**未能复现**：
  1.0.0 与 1.0.1 的 `tests/protocol_selftest.mjs:90` 都是 `srv.listen(0, '127.0.0.1', …)`（OS 分配端口），
  全包唯一随机端口在 `tests/launch_selftest.mjs:23`（`21000 + Math.random()*20000`，区间 21000–40999）。已在 issue 里请对方补该文件的行号/stack。
- **版本**：本节修复随 **v1.0.2** 一起发布（S10 现场补丁 + 本节修复 + 门禁卫生文件）。`package.json`
  的 `files` 已加 `!runtime/*.bak-*` / `!runtime/*.v1-*`，现场备份不再进包。

## v1.0.1+S10（2026-09-29）—— 僵死后端自愈（现场补丁，未随包发布）

> 现场：9877 后端**事件循环被长时间同步操作占死** ⇒ `/health` 不应答（`ss` 见 `Recv-Q` 只涨、
> 无人 accept）、**SIGTERM 处理函数永远排不上队**（信号排进队列但主线程不返回）、看护每 15 s
> 重生一个实例却撞 `EADDRINUSE` 直接 `exit(1)` ⇒ **无限空转、永不自愈，只能人工 `kill -9`**。
> 本次把它做成自愈，改 2 个文件、6 处；实测红绿对照见下。

### 修 1：`runtime/server.mjs` —— EADDRINUSE 时"先判定死活，死了就夺回端口"（P0）
- 原来只在 `server.on('error')` 里打印结论然后 `process.exit(1)`；现在：探活占用者（1200 ms），
  **能应答就不夺**（避免把"只是忙"的后端踢掉），**真死则 `SIGTERM` → 600 ms → `SIGKILL` 清场后重试 `listen`**（最多 2 次）。
- 为什么必须升级到 SIGKILL：实测事件循环被占死的进程**收不到也处理不了 SIGTERM**。
- 附带修掉一个既存 bug：该错误分支里用了 `fs` / `path` 却**从未 import**，
  于是那句"可执行结论"其实一直被 `catch (e2)` 吞掉、**从没写进 `backend-listen-error.txt`**；现已补 import。

### 修 2：`lib/index.js` —— 看护"先清尸再重生" + SIGKILL 升级
- 看护原先探活失败就直接 `startBackend()`，**从不调用已有的 `killBackendByCmd()`**（它只被 `op=stop/restart` 用到）
  ⇒ 端口被僵死进程占着时每次重生都注定 EADDRINUSE。
- 现在：连续 **2 次**（≈30 s，`probe` 超时仅 1500 ms，故不用单次判定）探活失败 →
  `killBackendByCmd(port,'SIGTERM')` → 1.2 s → `killBackendByCmd(port,'SIGKILL')` → 再重生。
- `killBackendByCmd(port, signal='SIGTERM')` 增加信号参数，默认值保持既有调用点行为不变。

### 修 3：两处"杀进程"都改成 **argv token 精确匹配**（自伤修复）
- 原判据是**整条 cmdline 子串匹配**（`cmd.includes('server.mjs') && cmd.includes('--port 9877')`）。
  实测自伤：调用方 `bash -c "... node runtime/server.mjs --port 9899 ..."` 的命令行同样含这两个串
  ⇒ 新版抢端口时把**调用方自己的 shell 一起 SIGKILL 掉**（本补丁首版即踩，被 A/B 测试当场抓出）。
- 现在要求：**某个独立 argv 以 `/server.mjs` 结尾**（basename 精确）且其后紧跟 `--port <port>`，
  并排除 `bash/sh/zsh/dash/fish/bwrap` 一类 shell。`lib/index.js` 与 `runtime/server.mjs` 两侧同步收紧。
- 回归证据：同样带该字面量的 bash 调用**不再被杀**（修复前会被杀）。

### 修 4：`runtime/server.mjs` —— 信号处理强制退出兜底
- `SIGTERM/SIGINT` 处理改为 `try{engine.stop()}catch{}` + `try{server.close()}catch{}` +
  `setTimeout(()=>process.exit(0),400)`：任一环节抛错/挂住都不会再让"占着端口的僵尸"活下来。
- 实测：发 SIGTERM 后 **510 ms** 内退出。

### 红绿对照（本机实测，隔离端口 9898/9899，替身 = 占端口 + 忽略 SIGTERM + 永不应答）
| | 旧版 | 新版 |
|---|---|---|
| 启动结果 | `exit 1`，日志 `已被占用（EADDRINUSE）…` | `僵死后端占着 9899：SIGTERM=[37258] SIGKILL=[37258] → 重试 listen` + `[blender-rt] http://…:9899/` |
| `/health` | 无应答 ❌ | `{"ok":true,…}` ✅ |
| 替身进程 | 仍活着（需人工 kill） | 已被 SIGKILL 清掉 ✅ |

### 未随包发布 / 升级注意
- 这是**现场补丁**：插件包升级会覆盖 `lib/index.js` 与 `runtime/server.mjs`。原件与可重打 diff 在
  `tmp/patch-backup-20260929/`（`index.js` / `server.mjs` 原件 + `S10-selfheal.diff`，`patch -p0 < S10-selfheal.diff` 重打）。
- **生效范围**：`runtime/server.mjs` 的部分**重启后端即生效**（`blender_viewport op=restart`）；
  `lib/index.js` 的看护部分需**宿主/插件重新加载**后才生效（本次会话内仍是旧看护，但仅靠 server.mjs 的夺端口路径也已完成自愈）。

## v1.0.1（2026-09-28）—— 修 blender_rt_perf / blender_rt_opt 每个 op 都崩（perf API 从未注册）+ 描述按平台改写

### 修：perf / opt 两个工具全 op 报 AttributeError（P0）
- **症状**：`blender_rt_perf`（status/analyze/apply/revert/help）与 `blender_rt_opt`（analyze/opt_analyze/opt_join）
  每一个 op 都回 `AttributeError: module 'dsh_rt_kernel' has no attribute 'dsh_perf_api'`（内核里只有
  `dsh_perf_api_fp`，那是 engine 写的内容指纹，容易被误读成"注册过"）。
- **根因**：`runtime/perf.py` 尾部只有一段 v0.9.1 时代的 shim —— 它把**已存在**的 `K.dsh_perf_api`
  包成 `_DshApi`，而全包没有任何地方真正创建过这个属性；同时 `engine.mjs` 的
  `MODULE_ATTR.PERF_READY='dsh_perf_api'` 这个 ready 判据永远为假 ⇒ 每次调用都重新注入模块。
- **修法**：按 `kit.py` 的统一约定注册（与 qc / clearance / pipeline 同形）：
  `_KIT.register("perf", PERF_VERSION, {status, analyze, apply, revert, help, opt_analyze, opt_join}, extra={selftest})`。
- **契约对齐**：`engine.mjs` 的 `perfCall` 由 per-op 直调 `K.dsh_perf_api[op](dict)`
  （参数会被当成第一个位置参数误绑，如 `samples={'samples':64}`）改为
  `K.dsh_perf_api["dispatch"](op, json)` —— 与 audit / txn / preset / plan 同契约，参数按 kwargs 摊平。
- **自检收紧**：`dsh_perf_selftest` 把 `api_registered_by_module` 从「只如实标注」升为**硬判据**；
  同类"模块没注册 API"的回归以后会被测试直接拦下。
- 验证：隔离无头实测 status / help / opt_analyze / apply 全通（status 回 `engine=BLENDER_EEVEE`）；`npm test` 全绿。

### 改：`blender_rt_headless` 的路径提示改按平台分支
- 原文案写死 `cwd 是 \\wsl.localhost\…\DSH，WSL 绝对路径必须带开头 /`，在 Windows 宿主上指向错误空间。
- 现按新增的模块级 `IS_WIN` 分支：Windows 提示"给 Windows 路径"，WSL 保留原提示。
- 描述预算门仍绿：rt_headless 1556（< 1600），15 个工具合计 11944（<= 12000）。

### 含
- 本版以 v1.0.0 之后的 `13a10bd`（ext 安装提示文案修复）为基线。


## v1.0.0（2026-09-28）—— 首个 1.0 发布：macOS 全 GUI 路径打通 + 对外能力冻结

> **1.0 = 能力冻结线**：15 个模型侧工具 + `blender_rt_plan`（28 个 family / 183 个 op）是对外稳定面；
> 包版本（`package.json`）、工具描述前缀（`[v1.0.0] …`）、npm 包名 `@dsh-external/dsh-blender-plugin` 三者一致。
> 相对 v0.9.6 的**功能增量只有一项**：macOS 支持（见下）；其余为既有能力收口 + README（中英）重写为「只写 1.0 现状」，
> 不再按版本号罗列历史 —— 历史留在本文件里。
>
> **仓库同一提交的另两个动作**：
> ① 删除本地与远端分支 `backup/v0.9.6-local-0926-2005`（独有 commit `0f99949`，删除后不再有 ref 指向它；
>   本地回看用 `git show 0f99949`，远端若已被 GC 则取不回）—— 此后只保留并推送 `main`；
> ② 版本号 0.9.6 → 1.0.0（`package.json` / `src/index.ts` / `lib/index.js` 的 `PLUGIN_VERSION`），并重建 `lib/`。

macOS 上宿主与 Blender 同机同 OS，跨 OS 路径改写退化为恒等。
原先这些改写会把 `/Users/...` 拼成 `\\wsl.localhost\Ubuntu\Users\...` —— Blender 静默打不开。
Windows / WSL 行为不变（有回归断言兜底）。

### 新增

- `runtime/config.mjs`：`IS_MAC`；`winToWsl` / `wslToWin` 在 mac 上早退返回原值；
  `detectBlenderExe()` 增扫 `.app` bundle（`/Applications`、`~/Applications`、`/Volumes/*/Applications`，
  支持任意版本名 `Blender*.app`）；`resolveWorkDir()` 改用 `~/.dsh-blender-rt`
  （**不落系统临时目录** —— 那里会被清理，帧与台账会丢）；`winLocalAppData()` 不再尝试调 `cmd.exe`。
- `runtime/engine.mjs`：`blenderJoin()` 收编原先散落的 `path.win32.join`（mac 上会产出 `\Users\...`）；
  `withWslEnv()` 同 OS 时直接返回（不再往子进程塞 `WSLENV`）；注入 Blender 的 python 路径助手
  （`_dsh_slashes` / `_dsh_win_path` / `_dsh_wsl_path`）带 `__DSH_NATIVE_POSIX__` 渲染开关，mac 上恒等；
  `PATH_GUARD`（WSL 专属的 `render.filepath` 体检）在 mac 上关闭，避免把正常的 `/Users/...` 误报成路径问题。
- `src/index.ts`：`killBackendByCmd()` 在没有 `/proc` 时回退 `ps -ax -o pid=,command=`
  （macOS 无 `/proc`，原先静默返回 `[]`，热重载后残留后端停不掉）。
- `tests/mac_selftest.mjs`（`npm run test:mac`）：45 条断言，纯 Node + 真跑一次 `python3`
  执行注入的路径助手；含**回归保护**（断言 WSL/Windows 分支仍在，防止 mac 支持把原逻辑吃掉）。

### 修复

- `runtime/config.mjs`：`PATHS.livePngWin` / `PATHS.viewPngWin` 硬编码 `path.win32.join` →
  在 mac 上产出 `\Users\...`，取视口截图会静默失败（该路径会被内联进 bpy 脚本、也直接交给 addon）。
- `runtime/qc.py`：「已是绝对路径」的判据补上 `/` 开头（原来只认 `X:` 与 `\\`）。

### 实机验证（macOS arm64 + Blender 5.2.2 LTS + DSH Blender Bridge 1.7）

路径层（`npm run test:mac`，45 断言，纯 Node + 真跑 `python3`）：

- 无头运行 `status: finished` / 退出码 0 / workdir·runId·env 契约正确；
- 真实渲染产出合法 160x120 RGBA PNG；
- `GPUOffScreen` 自定义视角自检 `gpu_init: ok`、`first_px == [25,153,51,255]`、投影误差 ≈1e-7
  （与 Windows 参考值一致）。

GUI 通道（`npm run test:gui`，需真机 + addon）—— **11/11 通过**：

- `blender_viewport op=doctor` / `op=status`：通道健康，读出真实 3D 视口 region；
- `blender_rt_see`：离屏取帧 148ms，合法 RGBA PNG，写入 `~/.dsh-blender-rt`；
- `blender_rt_see {from, look_at}`：自定义视角 178ms，覆盖 26.8%，**用户视口未被改动**；
- `blender_rt_do`：改写场景 + 读回验证通过；持久内核 `K` 跨调用保留；
- `blender_rt_cmd` / `blender_rt_commands` / `blender_rt_perf` / `blender_rt_opt` 均正常；
- `blender_viewport op=launch` 冷启动：2.5s 从零到一个已 Connect 的 GUI Blender（原先需人手点面板）。

`npm test` 既有回归（协议 / 租约）不受影响。


## v0.9.6（2026-09-26）—— 上游整合 A1–A5（雕刻/修复/UV/制造/扫掠）+ addon 升 v1.7 + D1 工具可发现性

来源：`docs/上游整合评估-mcp-for-blender与blend-ai.md`（对 ahujasid/mcp-for-blender 与 HoldMyBeer-gg/blend-ai 的实测盘点）。
一句话：**把上游两家缺的那半补上**——上游都做不到"程序化雕刻"（blend-ai README 自述不能模拟笔触），
本插件用 numpy 位移笔刷 + 拓扑准备把它做成可复算的几何操作；顺手补掉旧 runtime 里 uv/thin-wall/sweep 三块空白。

新增 5 个 runtime 模块（21 个 op，全部走 `blender_rt_plan`，不新增工具）：

| 模块 | op | 说明 |
|---|---|---|
| `sculpt.py` | scan / setup / apply / filter / mask / remesh / selftest / help | **numpy 位移笔刷**（draw·inflate·pinch·flatten·smooth·crease）+ 体素/multires/subdiv/dyntopo + 遮罩 + GUI 滤镜 |
| `mesh_fix.py` | repair / decimate / selftest / help | audit 只诊断，这里负责治：合并重复点 / 消零面积面 / 删孤立点 / 重算法线 / 可选封洞 |
| `uv_tools.py` | stats / smart_project / unwrap / project / pack / selftest / help | UV 四件套；枚举从算子 RNA 读，不写死 |
| `printcheck.py` | walls / overhang / report / selftest / help | BVH 射线测壁厚 + 法线锥测悬垂（不依赖 3D Print Toolbox 插件） |
| `sweep.py` | analyze / build / selftest / help | 平行移动标架扫掠（拐弯不扭转）+ 弯折半径先算后建 |

### 实测数字（本机 Blender 5.2.2 LTS）

- **雕刻**：体素重构 1986 → 12148 顶点 **0.084 s**；单笔 1986 顶点、257 顶点受影响 **6 ms**；
  `sculpt_selftest` 在临时球体上跑 apply+remesh+mask+scan，实测 affected=65 / max_delta=0.223 / remesh 3120 顶点。
- **修复**：`fix_selftest` 合成缺陷网格（7 boundary / 1 degenerate / 1 loose）→ 修复后 degenerate=0、loose=0，真非流形=0。
- **制造检查**：200×200×5 mm 薄板（面 4 mm）@min_mm=10 → min=median=5 mm、thin=5000、**无警告**（正例）；
  体素球（面 47 mm）@min_mm=5 → min=1686 mm、thin=0，并明确警告"min_mm 低于测量分辨率，结论不可信"（不给假结论）。
- **扫掠**：直管 3 站 12 边形 = 38 顶点 / 48 面；直角路径 min_radius=0.424 vs 需要 0.05 → 放行；
  过紧路径 min_radius=0.014 < 0.2 → **拒绝**并指出是第几个点。

### 关键结论（写进工具描述与 skill，避免后人重复踩）

- **`bpy.ops.sculpt.brush_stroke` 在 Blender 5.2 上 Python 走不通**：上下文已经解决
  （`temp_override` 后 poll()=True），但 `stroke` 集合在 RNA 侧拒绝 dict（6 种格式全失败），
  `OperatorStrokeElement` 也无法实例化。所以雕刻走"位移笔刷"这条路，别再花时间构造 stroke。
- 对称镜像必须**各自独立成笔触**（开发期实测抓到：把镜像点串进同一条折线会让 1 个点插值成 39 个 dab，把表面拖花）。
- 壁厚测量有**分辨率下限 = 本地面尺寸**：体素重构出的 60 mm 面测不了 5 mm 壁厚 —— 回执必须给 `resolution_mm`。

### 顺带修掉的既有缺陷

- `audit.py`：空网格（0 边）会 `me.edges[0]` IndexError 崩掉体检 → 现在报 `empty` 并把
  `empty_objects` 计入 `clean=false`（"建了对象没填面"不再伪装成健康）。

### 接线与文档

- `engine.mjs`：5 条新前缀路由（sculpt_/fix_/uv_/print_/sweep_）+ 模块注入（needFile 兜底）；`server.mjs`：
  11 个只读 op 进白名单（scan/help/stats/walls/overhang/report/analyze…），写 op 照旧过租约。
- `lib/index.js`：`blender_rt_plan` 工具描述补齐 5 个能力面的参数与硬规则（**下次 DSH 启动生效**）。
- skill `blender-modeling`：新增 Recipe 11–14（雕刻 / 修复+UV / 制造检查 / 扫掠），并改掉决策树里
  "笔触式雕刻不适合文本驱动"的旧说法（现在给的是 API 层实测结论）。

### addon 升级：v1.6（protocol 5）→ v1.7（protocol 11）

- **落地**：替换 @@C:\Users\<用户>\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\blender_mcp.py@@
  （模块名保持 @@blender_mcp@@ 不变 ⇒ Blender 偏好设置里不会出现重复条目），源文件取自 PyPI sdist
  @@mcp-for-blender-2.1.0@@ 的 @@src/blender_mcp/bundled/addon.py@@（sha 校验一致）。
- **回退点**（两份，md5 已对拍 @@50dfd58290e5eb34c2a2fd9fae3f6f23@@）：
  同目录 @@blender_mcp.py.v16.bak@@ 与 @@dsh-blender-plugin/tmp/backup/blender_mcp_v16.py@@。
- **重启**：@@Stop-Process blender -Force@@ → @@blender_viewport(op="launch")@@（5.7 s 起监听 9876、doctor 随即健康）。
  ⚠ 升级后必须**完全重启 Blender**（脚本 reload 会留下旧 socket 线程），与上游说明一致。
- **新增能力（实测）**：@@get_addon_info@@ 回 @@addon_version":[1,7],"protocol_version":11@@、capabilities 9 → 11 条；
  @@rt_commands@@ 可用命令 15 → **19** 条（新增 @@describe_node_type@@ / @@bpy_api_lookup@@ / @@export_scene@@ / @@get_tripo_status@@，
  已补进 @@COMMAND_CATALOG@@）。三条新命令都现场跑通：
  @@describe_node_type("ShaderNodeTexNoise")@@ 回 3307 字符的属性表；@@bpy_api_lookup("bpy.ops.mesh.primitive_uv_sphere_add")@@
  回算子签名；@@export_scene(filepath=..., format="glb")@@ 导出 1936 B（Cube/Light/Camera）。
- **协议层**：扁平协议未变（@@{type, params} → {status, result}@@），插件 @@addonProtocol=auto@@ 无需改动；
  @@addon-protocol.mjs@@ 的协议标签已去掉写死的 "v1.6"（改成"实测 v1.6/protocol5 与 v1.7/protocol11 都通"），
  @@protocol_selftest@@ 28/28 仍绿。
- **升级后回归**：五个模块 selftest 全绿、@@rt_do@@ 通、@@audit_mesh(Cube)@@ clean、@@qc_render_views@@ 出图正常、@@rt_see@@ 161 ms。

### D1 · 工具可发现性（被实测数据逼出来的）

- **证据**（插件自带 trajectory 日志，10 天 8,116 次调用）：plan 通道只占 **1.6%**（剔掉自检），
  `sculpt_* / fix_* / uv_* / print_* / sweep_*` 五族**真实使用 0 次**；111 条失败里 **68% 是超时**，
  真正的"选错通道/参数"只有 3 条 ⇒ 瓶颈是"**不知道有**"，不是"报错"。
- **`op="catalog"`**：16 family / 117 op 的"什么时候用 / 什么时候别用 / 最小 args 骨架 / 要点"，
  **插件本地直出**（不占 Blender 往返，任何租约下都能问）；`args={family:"sculpt"}` 单族展开（约 600 字符）、
  `args={full:true}` 才给结构化明细。默认文本 5.1k 字符（≈1.3k tokens，按需取，不是常驻）。
- **描述压缩**：`rt_plan` 的 description **4,708 → 1,496 字符**（全部 15 个工具的 schema 24,028 → 20,816 ≈ **每轮省 ~800 tokens**），
  长尾语义搬进 catalog（渐进披露：描述=索引 → catalog=判据 → `<family>_help`=算子细节）。
- **错误即纠正**：未知 op 回 `did_you_mean`（最近邻 + 骨架，实测 `audti_mesh→audit_mesh`、`sculpt_appli→sculpt_apply`）；
  把 plan op 当 addon 命令发给 `rt_cmd` 回 `CMD_HINT` 并在**本地**拦下（实测真实发生过 `qc_render_catalog` 走错通道）。
- **轨迹加字段** `family` / `args_keys` / `hint_shown`：用来做"家族覆盖率"与"意图→工具"周报（不改参数值，只记键名）。
- **AGENTS.md（全局 + 工作区）加"意图→调用"表 + 三条协议**：① 开工先 `doctor`；② 不确定先 `op="catalog"`；
  ③ 看到 `did_you_mean`/纠正提示就直接照做。表里明确"**>1 min 的重活走 `rt_job`**"（对着 68% 超时那条）。
- **回归**：`tests/discoverability_selftest.mjs` **26 项全绿**（catalog 体量/单族/最近邻/跨通道纠错/描述预算/真 HTTP 路由），
  已并入 `npm test`；`npm run test:discover` 可单跑。

### D2 · 描述预算（15 个工具的 schema 从 17,147 → 11,213 字符）

- **同口径实测**（只量真正发给模型的 `description + parameters`）：v0.9.1 13,671（最大工具 2,829）→
  v0.9.4 17,147（最大 **4,020**，rt_headless）→ 现在 **11,213**（最大 1,437）≈ **每轮省 ~1,480 tokens**。
- **做法**：重工具的描述只留"索引"（用途 + 3 条硬规则 + 指路），参数各留一句；把超时语义/路径语义/shots/
  preload/env/回执字段/常见坑等长尾搬进 `blender_rt_plan(op="catalog", args={tool:"rt_headless"})`
  （后端本地直出；7 个重工具：rt_headless / rt_job / rt_worker / rt_see / rt_loop / viewport / rt_plan）。
  三级渐进披露：工具索引 → family → 算子细节，全部按需取、不常驻。
- **防膨胀门**：`tests/discoverability_selftest.mjs` 新增 4 条预算断言（单工具 < 1600 字符、合计 <= 12000、
  rt_headless < 1600、重工具都有细节条目），**35 项全绿**，已并入 `npm test`。
- ⚠ 生效时机：描述在 `lib/index.js` 里，**下次 DSH 启动**才对模型可见（catalog/纠错/细节是后端侧，重启后端即生效）。

### A6/A7 · 材质节点图 + 渲染状态跟踪（对着真实用量做的两块）

**A6 材质（material_*，6 个 op）** —— 实测动机：工作区 7,182 个脚本里 `nodes.new()/ShaderNode` 命中
**2,732 次 / 204 个文件**，每个 build 脚本都各自手写一遍 TexCoord→Mapping→Noise→ColorRamp→Principled；
且 OBJ/MTL 交付会丢程序化节点。
- `material_build`：10 个 preset（metal_brushed / metal_paint / rust / plastic / glass / fabric / wood /
  concrete / emission / hologram / unlit），一次调用建图并连线，参数可覆盖（base_color / scale / bump / ior…）
- `material_scan`（只读）：节点数/类型分布/贴图依赖/procedural/needs_uv/被谁用
- `material_apply`：按对象或集合套用（replace / append 两种语义）
- `material_bake`：**把程序化材质烘成 PNG**（Cycles：DIFFUSE/ROUGHNESS/NORMAL/AO/EMIT/COMBINED），
  结束自动还原引擎 —— 这就是"程序化材质 → 可交付纹理"的桥，接 deliver_export 即可带材质交付
- 实测：`material_selftest` 绿（build 7 节点/7 连线 → apply → scan 认 procedural+needs_uv → AO 128px 落盘 473 B）；
  真机 E2E：rust preset @256px DIFFUSE 烘出 **58,466 B** PNG

**A7 渲染状态跟踪（render_state / render_wait / render_reset / render_guard_install / render_guard_selftest）**
—— 实测动机：111 条失败里 **76 条是超时**，渲染占满主线程是主因之一；工作区 `render.render` 362 次 / 334 个文件。
- **设计**：不"问 Blender"（渲染时问了也排队），改由 `bpy.app.handlers`（render_init / render_complete /
  render_cancel / load_post）在渲染开始时**写磁盘标记**、结束时删；后端（Node）读文件即可**秒判** busy
- **行为**：渲染中主线程写命令**秒回 `BUSY_RENDER`** 并给出"用 render_wait 或改走 rt_job"的下一步
  （ping 不在拦截范围：它走 addon 服务器线程，渲染中照样可用 → doctor 仍能判 main-thread-busy）
- 实测：真机排一次 720×480/256 samples 的 EEVEE 渲染 → busy 在 **1.29 s** 被观测到、**2.30 s** 清除；
  **渲染中发 rt_do ⇒ 71 ms 被拒**（此前会排队到超时）；`render_wait` 真等待路径等了 **9,392 ms** 到渲染结束
- 兜底：陈标记 15 min 自动判 stale 放行（`DSH_RENDER_STALE_MS` 可调）；load_post 清标记；`render_reset` 手动清

**接线**：目录 16 → **18 family / 117 → 128 op**；只读白名单 +6（material_scan/help、render_state/wait…）；
无头整合自检 **23 → 34 断言**（新增材质链路与 guard 的 install/mark/clear）。
⚠ 生效时机：catalog 与 guard 在后端（重启即生效）；工具描述在 `lib/index.js`（下次 DSH 启动）。

### D30 · 运行时层加固（现场报告 7 项，按序一次改完）

现场结论是"方法论层撑得住、运行时层拖后腿"。逐项：

| # | 现场问题 | 本批改动 | 验证 |
|---|---|---|---|
| ① | `/mnt/d` 变只读 ⇒ 后端写不了 status/ledger 起不来；报错只说"看后端日志"；9877 被僵死进程占着(EADDRINUSE) | `config.mjs` 新增 **workDir 探写 + 回退**（原候选 → 包内 `tmp` → OS tmpdir，**回退必留痕** `CFG.workDirFallback{requested,why,used,errs}`）；`server.mjs` 加 `EADDRINUSE` 明确文案（明说"僵死后端占端口"+ 处置）并落盘 `backend-listen-error.txt` | 模拟不可写：`DSH_BLENDER_WORKDIR=/etc/hostname/nope` ⇒ 自动回退到 `dsh-blender-plugin/tmp`、`writable=true`、`why=ENOTDIR…` ✅ |
| ② | 后端一挂 headless/job/do 全废；没有官方离线通道（用户手搓 15 行恢复能力） | 新增 **`runtime/offline_bootstrap.py`**：建 `dsh_rt_kernel` → 注入 kit → 按名加载模块，`kapi(name)` 取 API；支持 `blender -b --python … -- vehicle audit` 与脚本内 `ob.boot([...])`（契约与在线路径一致） | 真机：`blender -b --factory-startup --python runtime/offline_bootstrap.py -- vehicle audit` ⇒ **`OFFLINE_BOOTSTRAP ok · 已加载：audit, vehicle`** ✅ |
| ③ | `K` 在 import 进来的 `lib/*.py` 里不可见（推荐用法与实现冲突） | `kit.py` 新增 **`kapi(name)`** 与 **`install_kernel(ns=None)`**；文档给官方一行式 `import sys; K = sys.modules["dsh_rt_kernel"]` | 离线路径已实测 `kapi("generator")` → `Api` ✅ |
| ④ | preload 模块名靠猜 | `kapi` 报错列已加载模块；engine 的 `preload 找不到模块` 报错**列全部可用模块名**并给 `preload="vehicle,audit"` 示例 + 指向离线逃生口 | 语法/单测通过 ✅ |
| ⑤ | 返回类型不统一（`dsh_audit_api` 回 dict，skill 示例写 JSON 字符串） | skill 新增 **§0.71 调用约定**：`api(op,args)`→dict；`api["dispatch"](op,json_str)`→str（要自己 loads）；给 `kapi()`/`install_kernel()`/`preload` 命名规则/离线逃生口 | skill 已写入 ✅ |
| ⑥ | `audit_gate` 装配语义没写清（222 件车必然过不了连通门） | catalog 的 audit 族 `not` 明确写：**连通门是「单体船」口径（默认 0.3 mm）**；多零件请调 `micro_gap_mm`(1–2 mm) 或走 `gate_plan(preset="assembly")`；skill §0.71 同口径 | discoverability 51/51 ✅ |
| ⑦ | Blender 5.2 合成器漂移（`scene.node_tree` 已移除；Glare `mix`、Mix `blend_type` 改名）从插件助手深处爆 AttributeError | `kit.comp_tree(scene)` **版本容错取合成器树**（5.2 `compositing_node_group` → 旧 `node_tree`），拿不到时明确写清版本口径与改名清单 | 真机 5.2 上注册可用（`material.py`/`qc_render.py` 本身用的是材质 `node_tree`，未受影响） |

**回归**：`npm test` **exit=0**（13 步）· discoverability 51/51 · guidance 通过 · `py_compile` 全过 · 离线逃生口真机通过。

**仍未做**（下一批）：`remap`/mixed 节点的完整 5.2 属性兜底（需要真机逐版本矩阵）；`workDir` 回退信息目前只在 `describeConfig()`/`CFG` 里，`/status`+`/health` 的字段暴露还没接。

### D29 · 修 `blender_viewport(op="launch")`：S3-c 搬移漏了 4 个依赖（现场报告）

**现场**：`op=launch` 与 `dry_run` 都直接 `tcpProbe is not defined`，用户只能自己写 boot 脚本 + PowerShell spawn 才点亮 GUI。

**根因（我的锅）**：S3-c 把 `launchBlender` 从 engine.mjs 搬到 `launch.mjs` 时，**只搬了函数体，没搬它的依赖**。逐个暴露出来的是：

| 依赖 | 定义处 | 处理 |
|---|---|---|
| `tcpProbe` | engine.mjs | 复用 `runtime/backend_probe.mjs` 的 `probeTcp`（同语义，单一实现） |
| `BLENDER_EXE` / `USER_CONFIG_WIN` / `USER_SCRIPTS_WIN` | paths.mjs | 补导入 |
| `writeHeadlessScript` | engine.mjs | **抽出**到 `runtime/staging.mjs`；engine 具名导入 + 重导出（导出面不变） |
| `baseChildEnv` / `withWslEnv` / `readJsonSafe` / `workdirStagingWarning` | engine.mjs | **抽出**到 `runtime/proc_env.mjs`；engine 导入（`readJsonSafe` 仍是 engine 本地，不进导出面） |

**防复发**：新增 `tmp/dep_scan.py`（机械扫描：把 launch.mjs 用到的、定义在 engine/config/paths/staging/proc_env/backend_probe 里的顶层名，减去它的 import 与本地声明 ⇒ 列出缺依赖）。
这次就是靠它一次列全，而不是一个个试出来。

**验证（真机 END-TO-END）**：

```
dry_run   → LAUNCH ok · 给出 blender.exe 路径 + boot 脚本路径（不再抛错）
真实 launch → LAUNCH ok · 已 spawn pid=2607071 · addon 端口 9876 已监听（5689ms）
doctor    → ok
```
（顺带把用户当时已退出的 Blender 拉回来了。）

回归：`npm test` **exit=0**（13 步）· Blender 侧 **263 断言**全绿。

**教训**：S3-c 这类"搬函数"必须**搬依赖**，并且搬完立刻用机械扫描（而不是调用一次看报错）验证。已在 CHANGELOG 记下这个纪律。

### D28 · 修一批重构遗留（都藏在"看起来通过"的测试里）

修 `op=start` 时顺手把 `npm test` 用**退出码**判了一遍（之前我用 `grep 失败|✗` 判，漏掉了三类静默失败），挖出 4 个真问题：

| # | 问题 | 根因 | 修法 |
|---|---|---|---|
| 1 | `/plan op=catalog args={verify:true}` 抛 `ReferenceError: ENGINE_SOURCE_PATH is not defined` | **S3-a** 把 `probeDiskCatalog` 搬进 `catalog.mjs` 时，`ENGINE_SOURCE_PATH` / `pathToFileURL` / `execFileSync` 三个导入都没跟过去 | catalog.mjs 补齐三处（`ENGINE_SOURCE_PATH` 由本模块位置解析，与 engine.mjs 同目录） |
| 2 | 纯 python 导入 `generator.py` 直接 `NameError: _KIT` | **S1** 统一内核时，`_KIT` 只定义在 **POSTLUDE 字符串里**（注入生成脚本用的），模块顶层却在用它 —— Blender 侧靠 preload 命名空间串味掩盖了 | 模块顶层显式解析 + 缺失即明确报错；`tests/generator_dispatch_selftest.py` 在伪内核里先注入 kit（纯 python 路径） |
| 3 | `catalog` 默认页 10,410 字符（守卫上限 9,600） | **S6** 给 13 族加的硬警告 + 提示行把一页索引撑长了 | 默认页只显示可操作那句警告、骨架封顶 160 字符（完整内容仍在 `args={family:…}` 与 handoff 块里）；现 **9,442 字符** ✓ |
| 4 | `mac_selftest` 的 `/mnt/` 守卫、`provenance_stale_selftest` 的 C 系列失败 | **S3-b** 把 PATH_HELPERS 搬到 `paths.mjs`、**S3-a** 把目录搬到 `catalog.mjs`，而这两条守卫还在改/读 `engine.mjs` | 守卫改为读 engine + paths；provenance 改的是 **catalog 副本**，族数 27 → 28 |

另外修了路由契约测试的**分类口径**：`参数不匹配 + did_you_mean` 恰恰证明**路由是通的**（只是空参不对），此前被误判成"路由失败" ⇒ 现在先排除参数类错误，真机 **70/70** 恢复。

**过程教训（记下来）**：我此前几次说"npm test 全绿"是**不准确**的 —— 判据是 `grep 失败|✗`，而 `mac_selftest` 打印的是 `FAILED`、`provenance` 是静默非零退出、`pyrun` 直接抛 traceback，三类都躲过了 grep。**现在一律用退出码判**：

```
npm test > /tmp/nt.log 2>&1; echo "exit=$?"     # exit=0 才算绿
```

**验证**：`npm test` **exit=0**（13 步全过）· 路由契约 4/4（静态 28/28 · 真机 70/70）· Blender 侧 263 断言全绿 · `pyrun` generator 13/0 · provenance 16/0 · mac 7/0。

### D27 · 修 bug：`blender_viewport op=start` 信陈旧句柄，端口没监听也回"已在跑"

**现象（现场两次）**：
- ① `op=start` 回「后端已启动（already-spawned）」→ 紧接着 `doctor` 失败、`curl /doctor` 4 s 超时：**9877 根本没有监听**；
- ② 另一次真拉起来了，却因为等待窗口太短回「启动失败：spawned pid=…」。

**根因**：`startBackend()` 只看子进程句柄 `childAlive()`（exitCode/signalCode/pid），**从不探端口**；等待用「睡 20×300 ms 后探一次」。

**修法**：

1. 新增 `runtime/backend_probe.mjs`（+ `backend_probe.d.mts`）：`probeTcp` / `probeHttp` /
   `decideStart({portUp, childAlive})`（纯函数三态） / `waitHttp(url, timeout, interval)`；
2. `startBackend()` 改**探端口优先**：
   - 端口能服务 ⇒ `already-running`（别的会话拉起的也算在跑，绝不重复拉）；
   - 端口不通但句柄还在 ⇒ **`stale-handle`**：SIGTERM 清掉陈旧句柄后重拉（这就是修掉的那个 bug）；
   - 否则 `spawn`；
3. `op=start` / `op=restart` 的等待改成 `waitHttp(…, 15000, 300)` —— **轮询到真能服务**（不再睡固定窗口），失败信息带修法提示（`op=restart` 清残留）；
4. 看护与命令路径的 `startBackend` 调用改为 `await`（函数变 async）。

**验证**：

- 单测 `tests/backend_probe_selftest.mjs`（已挂 `npm test`）**10/10**：服务在/不在时 TCP+HTTP 探活的真假、决策表三态、`waitHttp` 的"迟到成功"与"纯超时如实回 false"、真后端探活；
- **等价 E2E（真环境）**：停后端 → 造「句柄活、端口死」的陈旧态 → 决策 **`stale-handle`**（旧实现在这里回 `already-spawned`）→ 清陈旧 → 重拉 → 轮询到能服务 ✅；
- `lib/index.js` 已同步镜像并通过 `node --check`；`npm test` 全绿；`doctor` = ok。

**生效条件与诚实边界**：

- 插件是 **DSH 启动时加载**的模块 ⇒ 本修复**要重启 DSH 才生效**（后端侧无需重启）；
- 本机**没有 tsc**（本包不装 typescript，DSH 宿主里也没有）⇒ `lib/index.js` 是**手工镜像**的，与 `src/index.ts` 语义一致；
  下次在能构建的机器上跑 `bash scripts/build.sh` 应产生等价结果（届时以构建产物为准）。

### D26 · S6-b：子代理交接块 —— 解决"主模型会用工具、子代理不会"

**现场反馈**："启用子代理后，虽然主模型看了 catalog 会用工具了，但子代理没看 catalog。"

**为什么会这样**：子代理是**自包含上下文** —— 看不到主模型读过的目录；而它们又倾向直接写 Python（`rt_do` / `rt_headless`）绕过 plan 通道。
后果就是那个"肥皂车"的成因之一：子代理**自造几何**、自写判据，插件的 `vehicle_*` / `shape_*` 全白给。

**交付**：

1. **一条命令拿到可粘贴的交接块**（真机验证）：
   ```
   blender_rt_plan(op="catalog", args={handoff:true})
   ```
   ≈**2,545 字符**（能塞进子代理提示词）：15 个工具名 + 12 类建模（第一步 / 禁止自造）+ 三条硬规则 + 通道纪律；
2. **目录首页**新增指向它的提示行（主模型查目录时就能看到）；
3. **规矩写进被注入的两处**（子代理也会读到）：工作区 `AGENTS.md`「派子代理时（必做 · 实测教训）」+ skill `SKILL.md` §0.5 开头；
4. **机械守卫**（`tests/guidance_selftest.mjs` 6 → **11 项**）：交接块存在 · **≤3500 字符**（保持可粘贴）· 覆盖全部 12 类 · 含"禁止自造/三条硬规则"·
   `MODEL_FACING_TOOLS` 与 `capability.lock.json` 的 15 个工具**逐名一致**（防改名后交接块变错）。

**验证**：指引自检 11/11 · 能力锁 PASS（28/183/15）· 路由契约 28/28 + 70/70 · `npm test` 全绿 · 真机 `args={handoff:true}` 返回交接块 ✓

**边界（诚实）**：交接块仍需**主模型主动粘**（插件的机制能保证"命令存在且内容不会腐烂"，不能强制某个模型去用它）；
把规矩写进 AGENTS.md 是为了让**每一个**新上下文（含子代理）都看到这条要求。真正治本仍是机械门（下一步：`vehicle_shell` gate preset + 比例门凭证化）。

### D25 · 验收不再随规模退化（7 类面数门槛默认改为"不限，全跑"）

**要求**：把"验收会随规模退化"取消 —— 任何面数都要跑完整分析、给真结论，而不是"超阈值就跳过 / 降级成 bbox"。

**改动**：7 个门槛常量默认值改为 `0 = 不设上限`，并在每处判断加 `cap > 0` 前置（**显式**设 env / 参数时才启用上限，那时行为与老版本一致）：

| 常量 | 旧默认 | 新默认 | 位置 |
|---|---|---|---|
| `SHELL_MAX_FACES` | 300,000 | **0（不限）** | `audit.py:181`（逐壳朝向）|
| `CONN_MAX_TRIS` | 4,000,000 | **0** | `audit.py:792`（连通/浮块）|
| `BVH_MAX_TRIS` | 500,000 | **0** | `audit.py:1799`（网格级复核 / 干涉 BVH）|
| `PAIRS_BBOX_CAP` | 200,000 | **0** | `audit.py:2205`（交叠对统计）|
| `MATE_MAX_TRIS` | 2,000,000 | **0** | `contract.py:59`（配合门）|
| `INSIDE_MAX_TRIS` | 200,000 | **0** | `contract.py:60`（射线内测试）|
| `VOL_MAX_TRIS` | 60,000 | **0** | `contract.py:62`（体积估计）|

**规模实测（1.57M 三角面：cube + subsurf 8 双层）**：

```
connectivity   analyzed=true  gate=pass   28.1 s   ← 旧行为：>50 万面跳过网格复核 → attached=null → 门降级
audit_mesh     verdict=pass               9.9 s    ← 旧行为：>30 万面逐壳朝向 unknown → state=degraded
interference   ok=true analyzed=true      运行      ← 旧行为：门里这条会把"0"当上限直接报错（见下）
```

**双侧验证（默认 vs 显式）**：默认 `analyzed=true / gate=pass`；`DSH_CONN_MAX_TRIS=1000` → `analyzed=false / gate=degraded` + 明说原因；去掉 env → 恢复 `pass`。

**修的过程（如实记）**：第一轮只改了 13 处默认值与判断，跑回归时 `gate_review` **5 条失败**暴露还有 3 处比较没加前置（`audit.py:2693`、`audit.py:2824`、`contract.py:1529`）—— 它们把 `0` 当成"上限 0"，于是 12 面的小方块也被判"超上限 → 不分析"。补齐后 **263 断言全绿**。
（另记一笔：我第一次贴的"全绿"是**误报** —— 我在检查前把输出截断了，没匹配到 fail 计数；之后改成对全文本判定。）

**文档同步**：`docs/操作教程.md` · `docs/证据分级与读图纪律.md` · `README.zh-CN.md` · skill `SKILL.md`（2 处）共 **5 处**旧口径（"面数超 50 万跳过复核 → 门降级"）改为"**默认不限、全跑**；显式设上限才降级"。

**代价（诚实）**：全跑是有成本的 —— 1.57M 面下 `connectivity` 28 s、`audit_mesh` 10 s；再大体量会更慢（BVH 构建随面数线性、逐壳判朝向是 Python 循环）。要更快有三条路：
① 分件分析（`objects:["A","B"]` 缩 scope）② `fix_decimate` 降到阈值内再验 ③ 后续做分块 / 向量化优化（尚未做）。

### D24 · S6：建模类型指引 —— 给模型指明"每类建模用哪些工具、禁止自造什么"

**动机（实测）**：另一个建模 AI 手里有 `vehicle_*` 配方、也知道 Recipe 17/18/19 与 Recipe 22，**仍然自己写了参数化放样** ⇒
渲染出来是一张连续光滑面：没棱线、没板缝、6 处结构硬伤（尖喙/悬空平板/鼓包/悬空尾板/孤立鳍片/无棱无缝）。
根因**不是缺文档**（SKILL.md 里车壳配方 1030–1136 行、Recipe 22 在 1141 行、`crease` 出现 9 次），而是三样都缺：
**入口**（第一步该敲哪条命令）、**禁止自造**（写了也没人拦）、**可数值判的验收门**（"丑"没有任何门会红）。

**本批交付**：

1. **`runtime/classes.mjs`** —— 12 类建模指引，每类四件事：第一步 / 算子链 / 禁止自造 / 验收门：
   `recon · faceted · smooth · organic · repeats · boolean · human · sweep · rotational · plate · assembly · deliver`
2. **模型侧查法**（真机验证）：`op="catalog", args={classes:true}` 拿全部 12 类 · `args={class:"sweep"}` 拿单类；目录首页加了指路提示行；
3. **catalog 13 族补硬警告** —— 例：`vehicle.not` 现在明确写「**别自己写参数化放样** —— 自造只能出一张连续光滑面（没棱没缝的肥皂）；车壳一律 `vehicle_sections → vehicle_loft → vehicle_panels`」；
   同批：`shape`（别手算站表）· `sweep`（别手接路径段）· `generator`（别写一次性循环）· `uv`（别手动摆 UV）· `material`（别手搭节点树）·
   `deliver`（别手写导出）· `gate`（别自写判据）· `motion`（别手摆姿态）· `human`（别自造比例）· `img`（别用眼睛估尺寸）· `audit`/`qc`（别目测代替体检/验收）；
4. **机械守卫 `tests/guidance_selftest.mjs`**（已挂进 `npm test`，6 项）：
   ① 12 类字段齐全且 `forbid` 必须是硬话；② 指引里点到的 op / 工具名**必须真实存在**（防改名后变成错指路）；
   ③ 13 个家族的 `not` 必须带硬警告（防被删）。

**配套 skill**（`dsh-skill-blender-modeling`）：`SKILL.md` 新增 **§0.7 建模类型 → 工具指路**（12 类表 + 三条硬规则 + 查询命令），
`shape_plan` 在 SKILL.md 的提及 **0 → 4 次**（此前只加载 skill 的模型学不到这个单入口）。

**验证**：能力锁 PASS（28 family / 183 op / 15 工具）· 指引自检 6/6 · 路由契约 28/28 + 70/70 · `npm test` 全绿 · Blender 侧 **263 断言**全绿 · 真机目录查询通过。

**边界（诚实）**：指引仍是"文档 + 可查询"，**没有强制力** —— 真正能拦住"自造放样"的是机械门（下一步：把板缝/特征线计数、连通分量、悬空件做成 `gate_run` 的 `vehicle_shell` preset）。

### D22 · 路由契约测试（拆 createEngine 的前置条件，已抓到一个真 bug）

现有 263 条断言偏「领域正确性」，不覆盖「每个 op 都能被路由到」——这几轮实际撞过两次（目录漏列 `vehicle_loft`、`perf` 模块没有 dispatch）。
新增 `tests/route_contract_selftest.mjs`（`npm run test:routes`），两层：

1. **静态**：读 `PLAN_CATALOG`，断言 28 个 family 的前缀在 `engine.mjs` 里都有路由（认 `p: 'x_'` / `indexOf('x_') === 0` / `startsWith('x_')` 三种写法；空前缀家族走 plan/契约兜底）；
2. **真机**：把 `/plan` 只读白名单（70 个 op）**逐个空参调用**，只判「路由是否接上」——`ok:false` / 参数校验报错都算正常（要证的是「接上了」，不是「空参也能跑」）。

**首次运行就抓到一个真 bug**：`render_lock_status`（白名单里的只读 op）被直切成 `lock_status` 发给 `qc_render`，模块不认识 ⇒ `unknown qc_render op`。
对照实测：`render_status` 反而通（`qc_render` 的锁查询 op 就叫 `status`）⇒ 别名为 `status` 并归一，问题消失。

现在：**静态 28/28 · 真机 70/70 可路由**（正常回执 47 · 空参被拒 22 · degraded 1）。
后端/Blender 不在时**跳过**（exit 0），不阻塞离线 `npm test`。


### D23 · kit 按需执行：每次调用省 7.45ms（实测）

`kit.py`（10.7KB）原本嵌在 `KERNEL_BOOTSTRAP` 里**每次 execute_code 都 exec 一遍**。在 Blender 里直接量：

```
kit 全量 exec      7.451 ms/次
条件执行（已注入） 0.0006 ms/次     => 省 7.45 ms/次（往返 163ms 的 ~5%）
```

改法（**零风险**）：源码照传（本地 socket 传输可忽略），但包进条件：

```python
if getattr(K, "dsh_kit", None) is None:
    <kit.py 全量，统一缩进 4 空格>
```

于是持久内核（GUI 侧 plan/act 全部路径）只在**第一次**真跑，`K` 被清空（Blender 重启）后自动重建；无头/作业路径（`blender.exe -b`，K 每次都是新的）行为不变 —— 不需要任何状态标志或额外往返。

实测：`rt_do` 往返 163ms → **97ms**（含此次会话的其它变化，保守口径以「in-Blender exec 7.45ms」为准）· Blender 侧 263 断言全绿 · 路由契约 4/4。

### D21 · S5-b：@引用进单发 op（不再局限于 pipe_run）

此前 `@工件` 只在 `pipe_run` 内解析 —— 单发 `blender_rt_plan(op="vehicle_loft", args={"stations":"@sec.stations"})` 会把字符串当参数用。本批让它**在单步调用里也能用**。

**实现路径（走过一次弯路，如实记录）**：最初想在 **engine 路由层**统一包一层解析（改 `_json.loads` 外层），结果所有 plan 调用都报 `JSONDecodeError`。对照测试（stash 掉改动后一切正常）确认是路由改动导致 —— **果断放弃**，改为**模块级解析**（风险面小得多）：

- `kit.resolve_refs(value, skip_keys=("out", "steps"))`：递归解析 `"@名字[.路径]"`，引用不存在抛 KeyError（含现有工件名）；
  `skip_keys` 是**必需**的 —— `pipe_run` 的 `out="@h"` 是**输出名**、`steps` 由 pipe_run 自己按序解析（否则「第 3 步引用第 2 步产出」会被提前解析而报错）；
- `kit.dispatch_table` 在摊平后解析 ⇒ 覆盖所有用 `_KIT.register` 注册的模块；
- `vehicle.py` / `gate.py` / `imgtools.py` 的手写 dispatch 各插一段（这三个是链上最常用的消费方）。

实测（真机）：先 `pipe_run` 存下 `@sec`，再**单发** `vehicle_loft(stations="@sec.stations")` ⇒ 直接建出壳（4289×597×708mm / 21 站）；引用不存在时回可读报错：`引用解析失败: 引用了不存在的工件 @nope（现有：sec, n2）`。

**又踩一次的坑**：改 `kit.py` 后忘了重启后端 ⇒ 测试仍跑旧内核（`KIT_SRC` 是引擎启动时读的），表现为「改了没生效」。

回归：Blender 侧 **263 断言**（整合 124 + audit 78 + gate 52 + 模块自检 9）全绿 · 能力锁 PASS · npm test 全绿。

### D20 · S1-b 收尾：给 10 个「无自检」模块补自检（重构盲区清零）

重构时发现：`contract / deliver / montage / perf / planner / presets / qc / runner / txn / worker` 这 10 个模块
**没有 selftest** —— 改坏了没人发现（前几批迁移它们时就是盲区）。本批补上 9 个（worker 是热无头会话的入口脚本、
不是 K-API 模块，单独说明）。

每个自检都是**自包含**的：造临时对象/数据 → 验核心机制 → 清理。

| 模块 | 验的是什么（evidence 摘录） |
|---|---|
| contract | 复位 → 登记真组件 → status 计数 ≥1 → 复位 |
| deliver | 建临时立方体 → 导出 OBJ → 文件真的落盘 → verify 对 md5 |
| montage | 造两张 8×8 PNG → 拼成 2 列 → 产物落盘 |
| perf | ops 齐备 + status/apply/revert 可跑（**该模块由 engine 直调，不注册 K API** —— 如实标注） |
| planner | status / diag / graph 都给结构化回执 |
| presets | 存 → 取 → 套到真对象 → **值真生效**（location 1,2,3）→ 删 |
| qc | **IoU 用已知答案验**（50/50 掩码交集 25、并集 75 ⇒ 1/3）+ resize + profile |
| runner | status 空闲可读 + help 结构化（不真起循环） |
| txn | mark → 真改变换 → revert → **变换真还原** + 列表可读 |

**新套件** `tests/modules_selftest.py`（9 项，独立回归）：`HEADLESS {pass:9, fail:0}`。

**kit 扩展**：新增 `_KIT.wrap_dispatch(base, extra)` —— 给已有 dispatch 挂附加 op，不去改各模块形态不一的手写 op 表
（这条也顺带暴露了一个真实不一致：`perf.py` 根本不注册自己的 API，尾部只有一段兼容 shim）。

**踩坑记录（都是实测）**：① `py_compile` 过的插入仍可能语法错（我把 selftest 键插到字典**外**、又叠加成 `},,`）；
② helper 函数被追加到函数**末尾** ⇒ `UnboundLocalError`（Python 把名字当局部，调用在前赋值在后）；
③ `kit.py` 改动要**重启后端**才生效（`KIT_SRC` 是引擎启动时读的，指纹只影响模块重注）；
④ `presets.apply` 的 `targets` 必须传**列表**（传字符串会被逐字符拆开）。

回归：Blender 侧 **261 断言**（整合 122 + audit 78 + gate 52 + 模块自检 9）全绿 · 能力锁 PASS（28 family / 183 op / 15 工具）· npm test 全绿。

### D19 · S5：流水线层（工件注册表 + @引用）—— 让 183 个 op 真正配合

痛点（重构前的实测）：op 之间只能人肉搬运。`vehicle_sections` 回一份 **5,656 字符**的 JSON，
`vehicle_loft` 要求把 `stations` 数组再粘回去；每一步交接都过模型上下文 ⇒ 贵且易抄错。
skill 里那 18 条多步链全是散文，模型得自己翻译成 N 次工具调用。

**新家族 pip（7 op）**：`pipe_run` / `pipe_put` / `pipe_get` / `pipe_list` / `pipe_clear` / `pipe_selftest` / `pipe_help`

- **工件注册表**：任何一步产出可命名存放（存持久内核 `K.dsh_artifacts`，活一个 Blender 会话）；
- **@引用**：后续步骤 args 里写 `"@名字"` 或 `"@名字.a.b"`（点路径/列表下标），运行时解析成真值；
- **逐步回执**：每步给 `ok / ms / keys / stored`，失败默认即停（`stop_on_fail=false` 可跑完看全貌）；
- 引用不存在的工件 ⇒ **当场报错并列出已有的工件名**（不静默失败）。

**真链实测**（合成参考图，全程 @引用）：`vehicle.sections` 144ms → `vehicle.loft`（`stations="@sec.stations"`）97ms
→ `vehicle.panels`（`object_name="@shell.object"`）353ms → `clearance.check`（`a/b` 用 `@panels.parts.0/1`）61ms；
**一次调用 657ms 跑完，4 个工件自动传递**。自检 9 项全 true（含点路径、嵌套解析、失败即停）。

接进现有面：engine 路由 `pipe_` + 目录族（含 when/not/最小骨架）+ 只读白名单 +3（`pipe_list/get/help`）；
S4 的机器直接用上了 —— 一条命令刷新能力锁与计数（**28 family / 183 op / 15 工具**）。

回归：Blender 侧 **252 断言**（整合 122 + audit 78 + gate 52）全绿 · 能力锁 PASS · 单一事实源 5/5 · npm test 全绿。

边界：@引用只在 `pipe_run` 内解析（单发 plan op 不自动解引用 —— 那要动引擎写路径，留待后续）；
工件活在当前 Blender 会话（重启即清空）；`pipe_run` 是同步串行，长任务仍走 `rt_job`/headless。

### D18 · P4.2：截面通路写进类别协议（选路不再靠读 CHANGELOG）

`shape_plan` 的每类协议现在直接带 **`section_path`**（该类用哪条截面通路），并随协议返回 **5 条决策规则**：

1. 轴对称（瓶/罐/轮毂/花瓶/喷口）=> `shape_revolve`，**不要**放样
2. 左右对称 + 前视有轮廓 => `section_shape`
3. 有弯度 / 左右不对称 / 要精确控制截面（翼型、机匣、异形件）=> `section_outline`
4. 只有一条侧视轮廓、截面随长度基本不变（板件/型材）=> 超椭圆 `section_pts` + 特征线
5. 多体量 => `shape_regions` / `shape_revolve(parts=[...])`，装配后 `clear_check` 验间隙

类别侧重点：**aircraft 明确给 section_outline（翼型，必选）**（镜像对称那条路表达不了弯度）、
**rotational 明确给 shape_revolve（不是放样）**、furniture 指向 `section_outline` + `sweep_build`、hull 提示水线上下分别给站表。

`shape_selftest` 增加两条硬检查：**每类都必须有 `section_path`**（防以后加类别漏配）+ 决策规则 >=4 条。
目录不变（27 family / 176 op）；无头整合自检 112 -> **116 断言**。

### D17 · P4.1：通用还原扩展（翼型/非对称截面 · 旋转体多体量 · G2 圆角）+ 账目校正

**A. workdir 测试前提修正（账目校正）**：`tests/workdir_shared_selftest.mjs` 原先把「`/tmp` 不与 Windows 共享 ⇒ 必须被替换」写死，
但本机 `/tmp` 就在 WSL 根文件系统上（可经 `\\wsl.localhost\Ubuntu\tmp\…` 访问）⇒ 插件判 `shared=true` 是**正确**的，测试却失败 4 项。
改法（环境无关）：A1 改判「**判定自洽**」（`shared=true ⇒ why=distro-root-fs`；`false ⇒ why 点名独立挂载）；
C 段按 `requestedShared` **分支验替换契约**；探针目录**动态选到真正的非共享挂载**（本机 `/dev/shm`）⇒ 替换分支照样真跑到。
实测 **10/4 → 14/0**。

**B. 验收文档补「现状口径」**（`docs/插件与技能优化验收.md` §-1）：本机口径 = Blender 侧 237 断言全绿（107+78+52）+ Node 侧全绿（workdir 14/14）；
并说明「npm test 197 通过」是**另一套挂载配置**下的数字；U1/U2（新能力是否到模型手上）已由真机复核解决。

**C. 能力扩展三件**：

- **自定义/非对称截面 `section_outline`**：`vehicle_loft`/`shape_loft` 接受显式闭合轮廓 `[[y_mm, z_mm], …]`，
  按站点归一（y 按半宽、z 按站高）⇒ **翼型（有弯度）、侧挂不对称件**都能表达；`section_source: custom_outline`。
  实测：NACA 风格 40 点轮廓 → 4264×1304×551mm，`ymin −408.2 / ymax +895.9`（**确实左右不对称**）。
- **旋转体多体量装配 `shape_revolve(parts=[…])`**：一次给多个 `{profile, at:[x,y,z]_mm, name}` ⇒ 轮毂+轮胎、瓶身+瓶盖、喷口阵列。
  实测 2 件各自落点（`hub@[0,0,0]`、`tire@[200,0,0]mm` → `location.x=0.2`）。
- **圆角 G2 控制**：`crease_lines` 加 `profile`（0.5 正圆 / >0.5 偏方更接近 G2 平顺 / <0.5 偏凹），回执回显 `bevel.profile`。实测 profile 0.75。

目录不变（27 family / 176 op）；无头整合自检 107 → **112 断言**，全绿；`npm test` 全绿（含修好的 workdir 14/14）。

### D16 · P4：把还原方法通用化（`shape_*` 家族 + SOP）

车辆那套"包络 → 站表 → 截面 → 特征线 → IoU"**本身与类别无关**（几何机制不依赖"车"的假设），
本轮把它抽成独立的方法层：新模块 `runtime/shapegen.py`（`shape_*` 9 个 op），**几何全部委托** `img_*`/`vehicle_*`，
自己只负责"**按对象类别给协议**"。

- **`shape_plan(object_class=…)`**：10 个类别协议 —— vehicle / humanoid / creature / headwear / furniture / hull /
  aircraft / rotational / weapon / generic，每类给「要哪些视图 · 盯哪些比例 · 配哪些算子 · 验收门 · 已知坑」。
- **`shape_sections` / `shape_loft` / `shape_fit` / `shape_regions` / `shape_panels`**：通用量具/放样/拟合/分件（委托实现，回执带 `layer`）。
- **`shape_revolve`**：**轴对称**通路（瓶/罐/轮毂/花瓶）—— 这类物体**不该用放样**，要绕轴旋转单条母线。实测 60×60×100mm / 146 顶点。
- **SOP**：`dsh-skill-blender-modeling/references/参考图通用还原SOP.md` —— 八步流程、十类协议表、三个决定性细节、验收纪律。

**实测**：`shape_selftest` ok（10 类协议无缺项 + 旋转体通路）；目录 **26 → 27 family / 167 → 176 op**；
无头整合自检 100 → **107 断言**，全绿；`npm test` 全绿。

### D15 · P3.6：圆角特征线（真半径，G1/G2 过渡）

特征线从"硬折线"扩展到"**带半径的圆角**"：`crease_lines` 的条目加 `radius_mm` + `segments`，
走 **Bevel 权重属性 + Bevel 修改器**（`limit_method=WEIGHT`、`affect=EDGES`、`profile=0.5` 正圆）——
**不是**用折痕权重去近似半径，而是生成真正的圆弧过渡几何。

```python
crease_lines=[{"frac":0.55,"radius_mm":6.0,"segments":3},   # 腰线：R6 圆角（3 段圆弧）
               {"x_mm":1500,"radius_mm":4.0,"segments":2}]   # 横向分缝：R4
```

**实测**：硬折线版 762 顶点 → R6 圆角版 **1244 顶点**（真加了圆角几何）；回执 `bevel={radius_mm:6.0, segments:3, edges:30}`；
修改器全部落地（对象栈不残留）；同带宽度 868.45（硬）→ 883.64（R6）—— 圆角把尖角换成圆角后外轮廓略外扩，符合预期。

实现细节：① 折痕标记函数参数化为 `attr_name`（`crease_edge` / `bevel_weight_edge`）+ `default_w`，两条路共用同一套环索引逻辑；
② Bevel 修改器插在 Subsurf **之前**（先圆角后细分 = 硬表面标准栈）；③ 没开 subsurf 时也要单独应用 bevel，否则圆角只挂在栈上、几何没变。

目录不变（26 family / 167 op）；无头整合自检 96 → **100 断言**，全绿。

### D14 · P3.5：特征线层（面感参数化）

`vehicle_loft` 新增 `crease_lines`（SubD 折痕 = 硬折线）与 `inset_lines`（几何折面/凹槽），两者都按**高度或位置 + 区间**指定：

```python
crease_lines=[{"frac":0.55,"weight":0.8},                    # 按各环高度比例（腰线，推荐）
               {"z_mm":560,"x_mm":[400,3900],"weight":0.85}, # 绝对高度的纵向线
               {"x_mm":1500,"weight":1.0}]                   # 横向分缝（自动插站）
inset_lines=[{"z_mm":470,"band_mm":60,"inset_mm":12,"x_mm":[500,3800]}]   # 门线凹槽/折面
```

**实测**：肩线折痕 10 条环边 · 横向分缝 20 条边（整圈）· 凹陷 A/B 同带宽度 **868.9 → 850.2mm（−18.7mm）**。

**两个必须记下的坑**：① **不能按 z 带匹配边** —— 环上顶点是离散的，目标高度上常常根本没有边（实测 `frac=0.55` 标到 **0 条**）；
改用**环索引**标记（纵向线 = 各环最近点的连线）后稳定命中；② 横向特征线要求那个 x **有站位**，否则同样 0 条 —— 
现在 `vehicle_loft` 会在横向线处**自动插站**（实测 `stations_inserted: 1`，边数 0 → 24）。

诚实口径：`z_mm` 只在几何确实存在该高度处中标（数量偏少是正常的）；要一条连续的腰线请用 `frac`。
目录不变（26 family / 167 op）；无头整合自检 92 → **95 断言**。

### D13 · P3.4：更强的形状族（折线闭合剖面）+ 照片透视校正

- **① 折线族 `profile_pts`**：`vehicle_spec` 接受显式控制点 `[[x,z_top,z_bottom]…]`（三点 = **闭合剖面**，上下轮廓都来自参考图）⇒
  `_profile_points` 用它直接建多边形，能表达 cargen 族**表达不了**的形状（方箱 / 皮卡 / 装甲 / 科幻）。
  拟合器加 `family="polyline"` + `control_points` **K 扫描**（K 越少 spec 越简单）：
  实测同一轮廓 —— cargen 族 0.637 → 0.667（240 次）；折线族 K=6 **0.754**、K=10 **0.781**、K=16 **0.821**（各 200 次）。
  即**天花板从 0.67 抬到 0.82**，并给出 K↔IoU 取舍曲线（挑"够用就好"的控制点数）。
- **② `img_rectify`（照片透视校正）**：四角点 → 矩形的**单应校正**（4 点 DLT 解 8 参数 + 双线性采样），
  **纯 numpy，不依赖 OpenCV**；给 `known_w_mm` 时直接回 `mm_per_px`，校正后的图再喂 `vehicle_sections`/`img_scan` 才准。

**踩坑记录（两个都是"改了会让结果静默归零"的）**：① 把 `_profile_points` 拆成 cargen/折线两条路时，
`L/H/ride` 三行留在了被切走的头部 ⇒ `_profile_cargen` 抛 NameError ⇒ **cargen 拟合 IoU 归零**；
② `_load` 返回 `(arr, path)` 而不是裸数组 ⇒ `img_rectify` 报 tuple 无 shape。都已修并有断言兜。

目录 26 family / **166 → 167 op**；无头整合自检 89 → **92 断言**，全绿；`npm test` 全绿。

### D12 · P3.3：外壳还原三件套（截面轮廓 / 多区域 / IoU 闭环）

- **③ 前视截面轮廓**：`vehicle_sections` 从**前视图逐行宽度**提取归一化形状 `section_shape[{z_frac, hw_frac}]`（20 点）；
  `vehicle_loft(section_shape=…)` 用它造环（`section_source: front_view_shape`）—— 截面性格不再靠超椭圆公式猜。
  **顺带修掉一个失真**：只有侧+前视图时 `half_w_mm` 缺失会让车身窄成 460mm ⇒ 加「前视最大宽 × 平面收放假设」回填
  （`half_width_source: front_view_taper_assumed`），实测宽度恢复 **1850.0mm**。
- **② `vehicle_regions`**：把参考图按 x 区间切成多块，**每块一个独立体量**（卡车驾驶室+货箱、装甲车体+炮塔、科幻主体+吊舱）。
  实测 3 区域 → `DBG_R_nose/cabin/tail` 三件，各自有尺寸。
- **① `vehicle_fit`：站表 → IoU 拟合闭环**（纯 2D 梯形积分，**不需要渲染**，比走 `rt_loop` 快一个量级）。
  目标轮廓 = 量出来的站表；候选 = 15 参数剖面公式；目标函数 = 两区域 IoU；搜索 = **解析反推初值 + 多起点 + 坐标下降**。
  实测：默认初值 IoU 0.456 → **解析反推初值 0.637** → 精修 **0.667**（迭代 240 次）。
  踩坑记录：解析初值把 `height_mm` 改成量到的值后，默认轮径 340mm 触发「轮径 > 0.65×车高」校验 ⇒ 候选全废弃、**IoU 归零**；
  加**参数自洽化守卫**（轮径/轴距/轮距随测量值等比收缩）后恢复正常。

**诚实边界（写进文档）**：`vehicle_fit` 拟合的是 **cargen 那 15 参数形状族**——
如果参考轮廓不在这个族里（例如方箱式），IoU 会停在 ~0.67 上不去；**要精确复刻就用站表直放样路径**（构造上就等于轮廓）。
两者分工：`vehicle_fit` 给「可复用、可改参数的 spec」，`vehicle_loft(stations=…)` 给「贴着参考图的壳」。

目录 26 family / **164 → 166 op**；无头整合自检 83 → **89 断言**，全绿；`npm test` 全绿。

### D11 · P3.2：外壳还原（三视图 → 站表 → 参数化放样 → 缝 → IoU）

承接 D10：`vehicle_base` 的 15 参数只能表达"单体积车"，复杂/跑车外形表达不了 ⇒ 升级为**站表驱动**。

- **`img_scan` 补 `col_top/col_bottom/col_fill`**（逐列上下边界）——站表的原料（此前只有逐行宽度）。
- **`vehicle_sections`**：侧视图（必需）+ 俯视/前视图（可选）→ 站表 + 截面参数 + 包络数字：
  侧视逐列给 z_top(x)/z_bottom(x)；俯视给 half_w(x)；前视给腰线高度 / 侧倾 tumblehome / 下裙内收。
  轮轴 x 用**轮廓底部低洼段聚类**自动定位（轮子与车身在填充轮廓里连成一片，不能用"贴地几像素"判 —— 实测只识别出 1 段）；
  阈值自适应（z_min + 0.35×(中位底边 − z_min)）。实测：合成跑车侧视（1px=10mm）⇒ 车长 **4300** 精确、
  轴距 **2687.5**（真值 2600，误差 3.4%）、前轴 860（真值 900）。
- **`vehicle_loft`**：站表 → 逐站超椭圆环 → 放样；截面性格由 `n_top/n_bot/tumblehome/beltline_frac/
  shoulder_inset/sill_tuck` 控制；`flare_mm` 做轮眉外扩（默认落在**底边被抬起**的站位 = 轮拱）；
  `crease_shoulder` 给腰线环边加 SubD 折痕（面感来源）。**折痕走 Blender 4+ 的 `crease_edge` attribute**
  （`e.crease_weight` 已不存在 —— 实测第一次 crease_edges=0）。实测：41 站 → 4295×611×723mm、3242 顶点、**134 条折痕**。
- **`vehicle_panels`**：在给定 x 处**减去薄板**（缝宽 = 真几何）并可 `separate(type=LOOSE)` 成多件。
  实测：两刀 ⇒ 3 件、缝宽量到 **3.9999mm**、零互穿。

**已知边界**（写进 Recipe 18）：没俯视图 ⇒ `half_w_mm` 缺失、车身偏窄（实测 611 vs 应 1850mm）；
轮径无法从填充轮廓反推（需 `wheel_r_px` 或已知规格）；照片参考必须先透视校正；
最后 20% 面感靠折痕位置与曲面张力微调。

目录 26 family / **161 → 164 op**；无头整合自检 78 → **83 断言**，全绿；真机四件套（sections/loft/panels/clear_check）跑通。
技能补 **Recipe 18**（并写明：这套"包络→站表→截面→特征线"对**凡有明确外轮廓的物体**通用；不适用布料/流体与内部结构定义的件）。

### D10 · P3：车辆外壳（参考图 → 剖面数字 → 放样）+ 成对间隙门

问题（用户原话）：车辆建模完全不像参考图、还各种穿模。诊断：**车壳必须由剖面参数放样，不能靠雕曲面**。

参考实现（GitHub）：**fpagerie/cargen**（Blender 插件，从参数生成车壳：长宽高 / 轴距 / 前轴到车头 / 轮+胎半径 /
轮宽 / 轮陷入地板比例 / 格栅高 / 引擎盖角·长 / 风挡角 / 车顶长·角 / 后窗角·高 / 后备箱角；米 + 度，角度从水平量）；
**vehicle-design 技能**的 Proportion-First 协议（人眼对车比例极敏感，表面细节救不了错比例）。

**新家族 vehicle（5 op）**：

- `vehicle_package`：比例门 —— WBR（轮径/车高）、轴距/车长、高/长、轮距/车宽、前后悬、离地间隙，逐项给区间与判定。
  WBR 区间按**真车标定**（911(992) 1300/680 ⇒ 52% · Corolla 1435/632 ⇒ 44% · Range Rover 1870/781 ⇒ 42%）；
  vehicle-design 技能的 35-45% 是另一套口径（相对车身而非总高），代码里写明了基准避免拿错。
- `vehicle_base`：纵向剖面 → 逐站超椭圆截面 → 放样成壳；**站位在轮缘处加密**（46 站 = 28 均匀 + 18 加密）；
  **布尔扣精确轮眉**（半径 r + arch_clear 的圆柱）；四轮（轴沿 Y）+ 车轴/车头/车尾标记。
  实测：4300×1850×1140mm、1101 顶点、4 轮在 x=900/3500、z=289、r=340。
- `vehicle_spec` / `vehicle_selftest` / `vehicle_help`。

**新家族 clearance（3 op）**：`clear_check` 规则表驱动的成对间隙/互穿判定（`min_mm` 管间隙、`allow_overlap` 管板件互插）。
实测：10mm 间隙过 · 2mm 挂 · 互穿挂 · 允许互插过；**轮与壳零互穿（间隙 10.51mm）**。

**过程中抓到的三个真问题（都是先量后修）**：① 轮子转轴转错（绕 Y ⇒ 轴朝车头，轮子横着长进壳里，78 对面相交）；
② 轮眉解析式在轮缘处断崖 + 站位过粗 ⇒ 弦切进轮子（58 → 20 对面相交）⇒ 改布尔扣；
③ `vehicle_spec` 返回 dict 而 API 层期望 JSON 字符串（`KeyError/TypeError`）⇒ 已修，并在 6 个模块的 API 层加了防御。

目录 24 → **26 family / 153 → 161 op**；无头整合自检 71 → **78 断言**，全绿。

### D9 · 整合方案 P2：零素材人形（human 家族）

素材路线（MPFB2）在 Blender 5.2 上走不通（源码树只有 fake_bl_info；重启后加载器仍不扫目录型 legacy 插件，
报「The package does not name an extension」），且用户不想下载 GB 级素材包 ⇒ 改走**零素材**参数化路线。

**新家族 human（5 个 op）**：

- `human_base`：只填数字（身高 mm / 头身比 / 肩宽 / 姿势）→ 骨架线 + Skin + Subsurf → 素体；
  回 `heads_measured`（**几何量出来**，不是拿 spec 复述）/ `head_mm` / `shoulder_over_head` / 19 个关节空物体。
  实测（1750mm / 7.5 头身）：高度 **1750.00mm 误差 0**、头身比 **7.546**、肩宽 350mm、肩/头 **1.51**、58,181 顶点。
- `human_measure`：优先读建模时写入的对象属性（head_mm/heads），退回关节标记几何。
- `human_spec`：参考图轮廓 → 像素比（height/head_w/shoulder/waist + 每行前景宽度 profile）。
- `human_head`：9 点 landmark → 参数化头型（颅椭球 + 下颌楔）+ 五官定位标记；
  实测 head_mm=233 → 三围 171.7×206.0×238.4mm，landmark_fracs 眼线 0.50 / 鼻底 0.725 / 嘴线 0.825。
- `human_selftest` / `human_help`。

**踩到并修掉的三个真坑（都是实测）**：

1. **Skin 修改器必须标 `use_root`** —— 不标则生成**零宽**几何（实测三组测试尺寸全是 [0,0,0.6]）；
2. **Skin 的末端大球不成立** —— 头中心会算到网格顶之上（头高量出 26mm 而非 233mm）⇒ 头改成**精确椭球**同网格合并；
3. 收敛方向写反过（应乘 实测/目标），且量测前没更新 depsgraph（新建空物体读到 0）。

**接进现有流程**：gate 的 OP_MAP 加了 4 个人形 op，`ensureGate` 链路带上 human ⇒ 门里可直接判「人形比例」；
实测 `gate_run` 一个三门的包 → **verdict=pass**（body 高度误差 0 · props 头身比 7.546/肩头比 1.51 · head 头高 233mm）。
技能补 **Recipe 15（人形素体）/ Recipe 16（人脸头型 + 遮住策略）**。
目录 23 → **24 family / 147 → 153 op**；无头整合自检 63 → **71 断言**，全绿。

### D8 · 整合方案 P1-③：资产门 + 第二自交通路（已落地）

- gltf_validate（Khronos glTF-Validator 2.0.0-dev.3.10）：绕过 npm 故障（直取 registry tarball → runtime/ext/node_modules）。
  真机 GLB 实测 ok=true / errors=0 / warnings=0；回执给 messages[].code，规则码可直接进 gate spec 的 pass_if。
- ext_mesh_check（open3d 0.20.0）：独立 venv（绕 PEP 668）+ apt 补 libusb-1.0-0，装在插件自己的 runtime/ext/ 下。
  A/B 实测：完好件 watertight=true / edge_manifold=true / 12 面；删一面后 watertight=false / 10 面
  —— 与插件内 audit 的结论一致，这就是换一条独立通路的意义。
- 两个 op 都在本地 spawn（不占 Blender 往返），候选命令按平台排序（后端实测跑在 WSL）；回执带 used / tried，
  失败时给确切安装命令。
- 实测坑：给 open3d 导出用 PLY/STL，不要用 OBJ —— Blender 5.2 的 OBJ 被判成 non-triangle primitive 读成 0 面。
- 目录 22 → 23 family / 145 → 147 op（ext 家族），只读白名单加 gltf_validate。

### D7 · 整合方案 P0：门标定套件 + 参考图量具（并把门的 4 个真 bug 挖出来）

对着"flash 上限 = 外部通路覆盖面"这条结论落的两件工具：

**① 门标定套件 `calib_run`（8 类缺陷 + 健康基线对照）** —— 回答"你的门到底抓得住什么"：
注入 8 类已知缺陷（删面 / 反转法线 / 孤立点 / 自交 / 平移脱开 / 浮块 / 薄壁 / 同名分叉资源），
每类判 caught，并跑一次**健康基线**（4 条门必须全过，否则标定作废 —— 假阳性对照）。
**最终：baseline_pass=true、capture_rate=1.0（8/8）、missed=[]**。
但它第一次跑出来的是 **7/8**，并且顺着 `thin_wall` 这条漏网查出了 **4 个真 bug**：

| # | bug | 症状 | 修法 |
|---|---|---|---|
| 1 | `print_walls` 射线起点偏移 `0.25×面尺寸` | 40×30×**0.3mm** 板采样为 0，报 30mm、thin=0（薄板根本测不到） | 起点只避开本面片（`1e-3×面尺寸`） |
| 2 | 同一表面片的判定用"距离阈值" | 体素重构球出现 26% 假薄壁（此前用面尺寸阈值压着，一改就复现） | 改用**命中面法线**区分同片（同向）/对壁（反向），8 次重试 |
| 3 | `_scale_avg(ob)` 用于各向异性缩放 | 底板 z 缩 0.1（3mm→0.3mm）被算成 2.1mm ⇒ 薄壁漏判 | 命中点与面心都用 `matrix_world` 变换后量**世界距离** |
| 4 | gate 的 `pass_if` 优先级 | 子 op `ok=false`（如 print_report 的"某件悬垂不过"）压过显式判据，判据为真也判 fail | **写了 `pass_if` 就以它为准**，只有 degraded 例外（不许把降级读成通过） |

— 顺手：`print_report` 增加门友好顶层字段（`walls_ok / overhang_ok / thin_samples_total / worst_min_mm /
**worst_p05_mm**`）；门判据改用**稳健分位 p05**（实测体素球有 0.05% 近零离群样本，判 min 会假报薄壁）。

**② 参考图量具 `img_scan / img_crop / img_annotate / img_diff`** —— 把"看不清"变成"不用看"：
- `img_scan`：轮廓 bbox / 长宽比 / 填充率 / 主轴角 / 主要直线角度 / ROI 前景率 / 指定点取色（Otsu 自分割，
  并给 `mask_warning`：前景 >95% 就明说"没分出背景，数字不可信"）
- `img_crop`：ROI 裁剪 + 最近邻上采样落盘（**中央凹式看图**：一次一块，尽量占满视觉输入）
- `img_annotate`：线/框/十字/网格画回图（图上看颜色、数字看 legend）
- `img_diff`：两图轮廓差分 → **IoU + a_only/b_only + 各轴剖面差 + 红蓝叠加图**（对"哪里多/少"远比"像不像"敏感）
- 自检（合成已知几何）：bbox 精确 `[20,30,80,60]`、长宽比 1.3333、放大 2× → 160×120、对齐 IoU=1.0、不对齐 0.6 ⇒ **全绿**
  （自检过程中修掉 Otsu 的并列平台 bug：双峰图上 `argmax` 落到 0 ⇒ 阈值 0 ⇒ 掩膜全空）

**接线**：目录 19 → **21 family / 132 → 141 op**；只读白名单 +3；两者都能用 plan op 直接调
（`img_selftest` / `calib_run` / `calib_selftest` 真机验证通过）。

### D6 · 相贴误报收口 + 灯组可发现性

- **修 · "正好相贴"被误报成浮块**（D5 里标"待决策"的那条）：根因是 `_components()` 会按 precision
  把**坐标重合的顶点焊接** —— 两块正好相贴的壳因此并成 **1 个连通分量**，而分类逻辑接着问"这个分量跟别的分量相接吗"，
  没有别的分量 ⇒ 判成可见浮块。**判据修正在语义层**：整个范围只有 1 个连通分量时，`"与其它分量不相接"` 这条判据
  无从成立 ⇒ `attached=True`、`evidence="single-body"`，并在 `confirm_note` 里指路
  `audit_mesh(island_split=true)` 查内部壳贴合。
- **三种相邻关系回归**（各 1 单位立方体两块，同一 mesh）：
  | 关系 | 修前 | 修后 |
  |---|---|---|
  | 互插 0.1（工艺要求） | pass（micro 容忍 2） | **pass**（不变） |
  | **正好相贴 gap=0.00 mm** | **fail（误报 1 个可见浮块）** | **pass**（可见浮块 0） |
  | 相距 5 单位（真孤立块） | fail | **fail**（不变） |
- **② 灯组可发现性**：`qc_render_views` 的 `lights={key,fill,rim}` / `lights=false` /
  `lights_mode="add"|"only"` 一直支持，但 qc 家族目录里没提（现场为此自己补了一盏背光）。已补进家族条目。
- 回归：无头整合自检 **61 → 63 断言**（新增"相贴 ⇒ pass 且可见浮块=0""孤立块仍 fail"）；`npm test` 全绿。

### D5 · 现场反馈 ③①②（规格驱动门包 / 自交分岛 / 逐对象三态）

来自同一次 95 对象装配反馈的三条真缺口，一次做完（无头整合自检 51 → **61 断言**，全绿）。

**③ 规格驱动门包（`gate_plan / gate_run / gate_selftest / gate_help`）** —— 现场为这三个门自写了
interference / redcheck / selfint_diag ≈600 行，现在由插件执行与取证：
- spec 三形态：.json / .py 模块（`SPEC` 或 `NAME/UNITS/TOLERANCE_MM/GATES`）/ 内联 dict，另带 3 个 preset
  （assembly / print / delivery）
- **三态显式**：每门 pass/fail/degraded/error；整包 `verdict` = 任一 fail → fail，否则任一 degraded → degraded，
  否则 pass；`ok` 只在 pass 时为 true —— 现场"降级长得太像绿"这条从字段层面堵死
- `pass_if` 是**受限表达式**（ast 白名单：比较/布尔/算术/属性 + min·max·len·abs·round·all·any·sum），
  **回执只回 pass_if 用到的字段**（自带瘦身）；引用了不存在的字段 ⇒ 该门判 error 并列出可用字段
- 实测：`gate_plan(preset="assembly")` 955 字符列出 3 门与各自 reads；`gate_selftest` 绿；
  真机跑包 738 字符；子 op 报错时**端上它的原话**（原先会误报成 "pass_if 求值失败"）
- 插曲：真机验证顺带发现你当前场景是 **103 个对象 / 0 个 mesh**（audit_scene 如实拒绝："场景里 0 个 mesh"）

**① `audit_mesh` 自交分岛（`island_split` / `min_island_verts`）** —— 现场手工做过的归因实验内置：
- 边连接并查集 → 连通岛；自交对分**同岛 / 跨岛**；`min_island_verts=N` 滤掉"任一侧面属于 <N 顶点小岛"的相交
- 新字段：`islands{count, small_islands}` / `self_intersections_same_island` / `_cross_island` /
  `_filtered_small_island` / `_kept`，进 `summary_only` 白名单
- 自检：宿主(细分立方体)+铆钉(小立方体) ⇒ 认 2 岛、跨岛>0 同岛=0；`min_island_verts` 一开 ⇒ 全滤掉、kept=0；
  一条边把两块壳连起来 ⇒ 认 1 岛且记同岛自交（**真互穿 vs 良性压入**从此可分开读数）

**② 连通性逐对象 + verdict（`per_object=true`）** —— 3 M 面整体只能 bbox-only 的那个坑有正解了：
- `audit_connectivity(per_object=true)` 逐件跑，每件通常在上限内 ⇒ 拿回 mesh(BVH) 级结论，
  回 `objects[{object,state,verdict,tris,mesh_mode,real_floaters,reason}]` + `"failed_objects"/"degraded_objects"`
- `_gate_from` 与 `audit_gate` 都补 `verdict`；degraded 一定带 `warn: "DEGRADED ≠ 通过…"`
- 实测三种相邻关系（各 1 单位立方体两块）：**互插 0.1 ⇒ pass**（micro 容忍 2）；**正好相贴 gap=0.00 mm ⇒ fail**
  （报成 1 个可见浮块）；**相距 1.2 单位 ⇒ fail 真浮块 2**。
  ⚠ **待决策**：第二条是**误报**——间隙恰好 0 时三角级复核没把它算作"贴而未重合"。默认单位下 1 unit = 1 m，
  0.3 mm 口径＝0.0003 unit，所以第三条不是 bug（200 mm 间隙本就该挂）；只有"正好相贴"这一种要修。

**顺手修**：`PATH_GUARD` 的 hint 里 `C:\home` 转义漏了一层 ⇒ 每次无头运行都刷
`SyntaxWarning: invalid escape sequence '\h'`（已修，重启后端后验证消失）。

### D4 · 现场反馈第一批（MK1 大件实测 · 已修 + 已核对）

来自一次 95 对象 / 3.05 M 面 的真实装配建模，逐条对到代码后的结论：

- **✅ 修 · script_file 的上下文（反馈 #4）**：script_file 是**内联**执行的（拷进 `D:\DSH\blender\tmp\dsh_headless_*.py`），
  以前 `__file__` 指向那个临时包装文件、脚本目录也不在 sys.path ⇒ 多文件工程第一行 `import spec` 就
  `ModuleNotFoundError`。现在注入 `DSH_SCRIPT_FILE` + 把 `__file__` 指回原脚本 + 脚本目录插入 `sys.path[0]`。
  复现验证：`tmp/sfprobe/{spec.py,main.py}` 不传 workdir 直接跑通（之前必挂）。
- **✅ 修 · WSL 路径"二次前缀"**：`"home/…/DSH/测试/x.py"`（漏了开头 `/`）会被当相对路径接到 cwd 后面 →
  `…\DSH\home\sixtyseven67\DSH\测试\x.py`。现在 `slipFixPath()` 判"补 `/` 后确实存在"即纠正，并写后端 stderr；
  覆盖 script_file / file / outdir / out_json / workdir 五处入口。复现验证：同一条调用传漏 `/` 的路径直接跑通。
- **✅ 记 · 路径语义进说明第一行**：schema 描述首段加警示；19 个参数的长尾（含超时/路径/回执语义）在
  `blender_rt_plan(op="catalog", args={tool:"rt_headless"})`。**注意**：第一版加长了描述被**预算门拦下**
  （rt_headless 1,690 > 1,600 字符），压缩文案后 38/38 通过 —— 门按设计工作。
- **🔎 反馈 #5"自定义灯组"其实早已支持**：`qc_render_views(lights={key,fill,rim}, lights_mode="add"|"only")`
  —— 现场"出背面全黑"是因为没用它（属**可发现性**问题，不是缺功能；待办：qc 家族条目补提示）。
- **⏳ 待决策**：反馈 #1（`audit_mesh` 自交**按连通岛分栏** + 小岛过滤）、#2（连通/gate 支持**逐对象或分块**
  且 `degraded` 不能长得像绿）、#3（**spec 驱动的一键门包**：现场自写 interference / redcheck / selfint_diag 约 600 行）
  —— 三条都是真缺口，等排期（成本从低到高：#2 < #1 < #3，价值从高到低：#3 > #1 > #2）。

### D3 · 验证者成本：清单外置 + 回执瘦身（B 档，低风险可逆）

**归因（先量后改）**：起一个"干净验证子代理"的固定成本 ≈ 工具 schema 2.8k + **SKILL.md 全文 ≈16–22k** +
AGENTS 3.7k ≈ **23–29k tokens**，而且**每轮重付**；真正的门调用（10 天日志里 audit_gate 21 次、audit_mesh 17 次…）
反而便宜。⇒ 要省的是**加载外围**，不是判据。

- **① 清单外置**：`dsh-skill-blender-modeling/references/验收清单.md`（**2,249 字符 ≈ 0.8–1.2k tokens**）：
  验证者任务模板（固定输入 + 固定 4 行输出）+ 数值门判据表（audit / print / deliver / uv 各一条命令与阈值）+
  **反 Goodhart"换一条计算通路"的判据**（构建用 `Object.dimensions` ⇒ 验证必须用 `matrix_world @ v.co`…）+
  跑偏自查。**单次验证固定开销 ≈23–29k → ≈7.3–7.7k**（省 ≈16–21k/次 × 轮数）。
- **② 回执瘦身**（默认**全关**，显式传参才生效，完全可逆）：`audit_scene(summary_only, top_k)`、
  `audit_mesh(summary_only)`、`audit_duplicates(top_k)`。实测 40 对象场景（1/4 带缺陷）：
  **9,176 → 1,148 字符（−87%）**、`top_k=2` → **773（−92%）**，而 `clean` 与 `totals` **判定完全一致**
  （验证强度不变）；`audit_mesh` 瘦身去掉 `self_intersection_pairs` 等明细、判据字段全留。
- **③ 顺手修一个静默失效**：`audit_duplicates(limit=30)` 收了参数从来没用过 —— 现在真正生效，
  截断时回 `materials_total` / `meshes_total` 与 `slim`（"跑过了"不再伪装成"查全了"）。
- **④ 回归**：无头整合自检 **34 → 42 断言**（判定不变、top_k 生效、瘦身 ≥70% 字符、mesh 明细被去掉、duplicates 有 slim）。
- **⑤ 指路**：SKILL.md 首屏 + 两份 AGENTS.md 各加一条"验证子代理只读清单"；`op="catalog"` 的 audit 家族条目
  补上 `summary_only / top_k` 提示（`audit_mesh` 回执 >6k 字符时会自动给 `hint`）。

### 许可

接口形状与判据参考上游，**代码为本插件自有实现**（blend-ai 是 AGPL-3.0，不复制其代码；mcp-for-blender 是 MIT）。

## v0.9.5（2026-09-26）—— 实测驱动的三项性能修复 + 多实例热会话 + 健康分布指标

来源：本机实测（方案与全部原始数字见 `DSH/blender/实测驱动的通道+Skill优化方案-2026-09-26.md`）。
一句话：**把力气从"看起来更聪明的新机制"挪到实测出来的三个大头**——诊断探针、重复帧、小作业固定开销。

### P0-1 · `rt_see` 诊断惰性化（自定义视角路径 −39~40%）

- **实测**（403 物体 / 128k tris）：`capture()` 里三项诊断（coverage / scene_bbox / objects_in_frame）
  每次都要 63 ms（560px）、137 ms（1120px）、488 ms（2240px）；而真正的 `draw_view3d` + 显存读回只有 ~22 ms。
- **改法**：`_spec()` 增 `diagnostics`（默认 **false**）；正常帧只回图/hash/ms/矩阵。
  **近空帧自动补跑**（PNG < `EMPTY_PNG_BYTES`=8000 字节）⇒ `frame_looks_empty` 告警照旧；
  显式 `diagnostics=true` 可强制每次都跑。顺手修掉渲染兜底分支里**连着调两次** `_scene_bbox()` 的浪费。
- **实测收益**：560×315 从 **146.3 → 88.6 ms**；1120×630 从 **301.3 → 179.4 ms**；强制诊断路径不变（161 ms，语义与 v0.9.1 一致）。
- **回归**：`tests/view_diag_selftest.py` 新增 5 项断言（不变量 = "跑了诊断 ⟺ 显式要求 or 字节数 < 阈值"），
  21/21 全绿；`npm run test:diag` 把"怎么跑"固化成命令（需要后端 9877；会按 `DSH_LEASE_HOLDER`/ `GET /who` 找 holder，**不 force 抢租约**）。`VIEW_VERSION` 2 → 3。

### P0-2 · 帧去重：同一画面不重复附图（省的是模型侧 token）

- **实测**：同一画面连发 5 次 hash 全等（`038c7439`），单帧 116,870 B —— 重复附图只烧视觉 token。
- **改法**：hash 早已算好（`fetchFrame`/`fetchView` 的 PNG md5 前 8 位），新增 `frameDedupe(key,hash,force)`：
  同键同 hash ⇒ 回执写明"与上一张完全相同（第 N 次）⇒ 未重复附图；要重发传 force:true"；
  键按通道隔离（`viewport:<size>` / `area:<n>` / `view:<视角签名>` / `do-see:<size>`）；**空 hash 永不判重**（宁多发一张，不静默丢图）。
  `blender_rt_see` 与 `blender_rt_do` 各增 `force` 参数。
- **回归**：`tests/frame_dedupe_selftest.mjs` 15/15（已并入 `npm test`）。
- ⚠ **生效范围**：这一段在 host 侧（`lib/index.js`），**重启 DSH 后生效**（同 v0.9.4 的说明）。

### P0-3 · `rt_loop` 平台期早停（默认关）

- **实测**：空转 176 tps（5.7 ms/tick = timer 地板，没有"无效空转"可省）；measure 重（~72 ms/次）时 12.4 tps（measure 占 ~90%）；
  同一 measure + `measure_every=20` ⇒ 96.8 tps ⇒ **频率旋钮本来就存在**，所以本版**不做**"动态调频率"。
- **改法**：spec 增 `patience`（0 = 关，默认关）：连续 patience 次 measure 没刷新 best ⇒ `stop_reason="plateau"`，
  status/回执带 `patience` / `last_improve_i`。只在"已经有 best"时判，避免 measure 一直不给 score 的循环被误杀。
- **实测**：patience=0 跑满 200 迭代（与旧行为逐字节同）；patience=10 + 30 次后不再变好 ⇒ **i=41 停**（`last_improve_i=31`）；
  持续变好的 loop 不早停。`RUNNER_VERSION` 2 → 3。

### P1-1 · 多实例热会话（小作业固定开销 2.7 s → ~0）

- **实测**：空脚本冷启动 **1000–1257 ms**（开 2.5 MB 工程 1287 ms / 11 MB 1386 ms）；小渲染作业（512²/16spp/2 帧）
  总 3098 ms 里**首帧着色器编译就占 1640 ms** ⇒ 固定开销 ≈2.7 s = 85%；而 2 并发只慢 ~10%（GPU 有余量）。
- **改法**：`blender_rt_worker` 从"单例"变**按 name 的多实例**（`op=start name=w1` / `exec name=w1` / `status` / `stop` / `restart` / **新增 list**），
  端口按 name 顺延（配置端口起）；缺省名 `default` ⇒ 老调用逐字节不变；`/worker` 路由与 `/health` 的 `rtt.worker` 同步。
- **实测收益**：同一句脚本，**热会话 40–60 ms** vs **冷启动 1257 ms**（≈20–30×）；批量小活不再每次 spawn。
- ⚠ **权衡写进描述**：复用 = 主动放弃"无头进程隔离"（同实例共享场景与 K）⇒ 脏了用 `restart`，要干净场景仍用 `headless`。

### P2-1 · `/health` 增分布指标（**只观测，不自动重启**）

- 新增 `rtt`（frame/act/view/headless/plan/worker/job 各留最近 20 次样本的 `last/p50/p95/n`）与
  `backend{pid,rssMB,uptimeMs}`。**不包含 blender.exe 的 RSS**（Windows 侧，取一次要起 tasklist，百毫秒级，不塞热路径）。
- 为什么不做"预测性重启"：误杀一次 20 分钟渲染的代价远大于省下的重启时间；自动拉起仍只在"端口/PID 明确死亡"这一确定态发生。

### P2-2 · `rt_job op=list` 偶发 abort（登记，未复现）

- 现象：本次会话中 `op=list` 连续两次 `This operation was aborted`，同一时刻 `/health` 2.7 ms 正常、无残留进程。
- 复现尝试：**重启后端后同一调用 116 ms 正常返回**（60 条台账 + 内存句柄都在）。⇒ 判为**瞬时态**，未找到可复现缺陷；
  已给 `/job` 路由加 `rtt.job` 埋点（P2-1），下次再现时先看分布与 `inflight`，再谈修。

### 验证

- `npm test`：protocol / lossless / lease / **frame-dedupe 15/15** 全绿。
- `npm run test:acceptance`：**62 通过 / 0 失败**（44 s）。
- `npm run test:diag`：**21/21**（含 P0-1 的 5 项新断言）。
- 真机数字：见上文各条（同一脚本改前/改后对拍）。
- ⚠ **生效范围**：`runtime/*.py`（view / runner）改完即生效（注入按 size+mtime 指纹自动重注）；
  `runtime/server.mjs` + `engine.mjs` 需**重启后端**（`blender_viewport op=restart`）；
  `lib/index.js`（工具层：force / patience 文档 / worker name / list）需**重启 DSH**。

## v0.9.4（2026-09-25）—— 返回通道修复（P0）+ 一键启动器（P1）+ skill v2

来源：另一会话《M1A1 分件建模》体感清单（500 对象 / 7 agent / 全程 headless / 主战场是自写 `tools/bl.sh`）。
回执：`docs/feedback/改进落地记录-2026-09-25.md`。

### P0 · `value is not lossless JSON` 根因（headless/job 全线不可用）

- **现象**：`blender_rt_headless` **每次**失败（连最简脚本都挂），`blender_rt_job(op=start)` 能提交但
  `status/collect/wait` 全部失败 —— "任务能交出去但收不回来"。反馈当时的猜测是"harness 拒绝非有限数"。
- **真因**：v0.9.3 `receiptEnvelope()` 里有 `promoted: ctx.mode === 'promoted' ? true : undefined`。
  宿主 `dsh-util-values` 的 lossless-JSON 门（`walkJsonValue`）**拒收值为 undefined 的自有可枚举键**，
  而 `JSON.stringify` 会把这个键**丢掉** → 回执在日志/单测里看起来完全正常，宿主却直接判
  `tool "…" returned invalid output: value is not lossless JSON`。
  该函数是 headless(sync) / headless(job 完成) / rt_job status·collect·wait 的**共同出口** → 正好对上现象。
- **同类第二处**（回归测试抓到的）：`gpu: { fell_back_to_cpu: r.gpu.fell_back_to_cpu }`（后端没给该字段即 undefined）。
- **修法**：① envelope 改「有才给键」；② `!!r.gpu.fell_back_to_cpu`；
  ③ **中央消毒器 `losslessSanitize()` 收口在 `vTool` 出口**（所有工具唯一出口）：
  undefined→丢键（数组元素→null）· NaN/±Infinity→null · -0→0 · bigint→number/string · Date→ISO ·
  Map/Set→数组 · 类实例→自有可枚举键 · 二进制→`{bytes:N}` · 循环→`[Circular]`；
  **改动不静默**：逐处记进 `fixes` 并追加到回执文本（`⚠️ 回执已消毒 N 处…`）+ `console.warn`。
- **回归门**：`tests/lossless_guard.mjs`（`npm run test:lossless`）—— 用**宿主的真校验器**
  （`dsh-util-values` 的 `snapshotJsonValue`，自动探测安装位置）断言"本插件任何回执都过得了门"；
  含控制组（§3 证明校验器确实严）、消毒器、四条回执构造器；`--live` 追加真机三项
  （真跑 headless + `rt_job(op="status")`）。**离线 40 / 真机 47 断言全绿**。
- ⚠ **生效范围**：插件在 DSH 启动时加载 → **正在运行的那个会话仍跑旧模块**（实测依旧报同一条错），
  **重启 DSH 后生效**；新会话 / `dsh` CLI 立即生效。

### P1 · 启动器：agent 自己点亮 GUI（反馈建议的 `blender_rt_launch(autoconnect=true)`）

- 落地为 **`blender_viewport(op="launch")`**（不新增第 16 个工具面：`doctor` / `blender-unreachable` 的修法直接指向它）。
- 语义：写 boot 脚本（GUI 里 `addon_utils.enable` 名字带 mcp 的模块；不行就 `importlib` 直接载入
  `$BLENDER_USER_SCRIPTS/addons/*mcp*.py` + `register()`）→ detached `spawn blender.exe` →
  **轮询 9876**（唯一可信判据）→ 端口开了顺手 doctor。
- **幂等**（已在监听回 `already:true`，不堆第二个 Blender）· `dry_run` · `exe` / `addon_module` / `addon_file` 可覆盖 ·
  boot 结论落盘 `launch-status.json`（GUI 进程 stdout 拿不到）· 回执给 `bootScript` 可手工复跑。
- **自检抓到的真 bug**：`spawn` 不存在的 exe **不抛**而是异步 emit `'error'` → 不挂 handler 会把
  **整个后端进程**打挂（自检第一版就这么挂的）；已修 + 路径存在性预检。
- **回归**：`tests/launch_selftest.mjs`（24 断言）—— 用**假 blender** + 独立后端/端口/工作目录，
  **不碰用户正在开的 Blender**；覆盖 spawn → 端口判据 → 幂等 → dryRun → 失败可读。

### P1+ · **死会话的写租约自动回收**（本机实测踩到，非反馈条目）

- 现场：一个 DSH 会话崩掉/被关掉之后，它的写租约仍留在后端内存里**继续生效到 TTL（默认 1 h）**，
  把别的会话的写通道整段挡死 —— 而那个会话早已不存在（本机实测：holder=@@plugin-pid-816309@@
  已无该进程，仍剩 57 min，headless 直接被 409 挡回）。
- 修法：holder 约定是 @@plugin-pid-<pid>@@（插件前端生成、与后端同机）→ 后端在**写请求到达时**与
  @@op=lease@@ 时按 @@process.kill(pid, 0)@@ 探活；**判死即回收**并记 @@stats.staleLeasesReclaimed@@ +
  @@lastLeaseNote@@（不静默）。@@/who@@ / @@/health@@ 的 @@lease.holderAlive@@ 给三态：
  @@true@@ / @@false@@ / **@@null@@ = 判不了（非约定命名 → 保守地继续挡，绝不误抢）**。
- 回归：@@tests/lease_stale_selftest.mjs@@（16 断言）—— 独立后端；覆盖"活持有者照样挡 / 死持有者自动回收 /
  无法判活时保守挡 / 真实写路由不再 409"。
- 活体验证（真后端）：埋入死租约 → @@holderAlive:false@@ → 本会话一次 headless **不带 force 直接放行** →
  @@staleReclaimed:1@@ + 可读 note。

### 文档 / skill

- `docs/feedback/改进落地记录-2026-09-25.md`（回执：根因、修法、验收、没做的）。
- `docs/操作教程.md`：`blender-unreachable` 一行改为"一键 `op=launch`"优先 + 新增第 8 节启动器。
- **skill `blender-modeling` v2**（`~/.dsh/skills/blender-modeling/SKILL.md`，v1 已备份）：
  新增 §0.5 多 Agent 分工范式、§0.6 参考图驱动的形体还原、坑四（headless 慎用 bpy.ops）、
  坑五（法线是一切穿模判据的前提）、坑六（预览装置默认看得清板缝）；配方 2 拆 2a 分面 / 2b 光滑；
  新增 3b（bmesh）、6b（参数化精确节距）、9（装配审计门）、10（双证据验收）；§6 清理清单升级为数值门清单。
  ⚠ 改 skill 必须保留 YAML frontmatter，否则技能会从目录里消失（已踩）。
- `npm test` 现在 = `protocol_selftest` + `lossless_guard`；新增 `test:lossless` / `test:lossless:live`。

### 未做（下一轮建议）

1. **法线体检变成代码门**：`audit_mesh` 增 `normal_consistency` / `signed_volume`，
   `audit_interference` / `audit_overlap` 判据前自动跑并把"前提：法线朝外（已验/未验）"写进回执。
2. `qc_render_views` 增 `mode="inspection"`（中性灰 + 单侧硬阴影 + 双机位），把坑六从纪律变成默认装置。
3. 读图量测原语（上一轮 D7）仍未实现。

## v0.9.3（2026-09-24）—— 外部反馈《插件改进交接-2026-09-24》落地：超时不丢结果 + 结构化回执 + 可观测 + 路径空间

来源：`docs/feedback/插件改进交接-2026-09-24.md`（7 名协作者 / 约 120 次工具调用 / 全部在 headless 完成）。
本版把 §2 的两条 P0、§3 的 D2/D3（一次重构）、D4/D5/D6、§4 的 F1/F2/F3/F4/F5/F6 与 §5 的路线决定全部落地，
并逐条补上 §8 的验收测试（`tests/acceptance_v093.mjs`，62 条断言 / 真机 Blender 5.2.2）。

### 路线决定（§5）：**headless 批处理 = 第一路径**，直连 GUI = 交互式增益
- 证据：7/7 协作者全程 headless、9876 addon 从未 Connect、`rt_do/rt_see/rt_watch/rt_cmd` 0 调用；
  而 `audit_*/qc_*/deliver_*` 本来就能在 headless 直调（`preload=` 通道），只是描述里没写。
- 落地：见 `docs/headless优先-路线决定.md`（含 17 行 op→preload 对照表 + 5 段 copy-paste + 任务模板）；
  工具描述改写（`blender_rt_headless` 明写「第一路径」、`preload` 明写 audit/qc/deliver 可直调）。
- **不删任何工具**：GUI 工具仍是「改一步看一眼」的增益，只是不再是默认入口。

### P0-1 · D1/F3：超时不再吞结果（promoted 语义）
- **客户端等待窗口**（`DSH_HEADLESS_WAIT_MS`，默认 100 s）：到点或宿主取消 → 回
  `{kind:"promoted", jobId:"run-…"}`，服务端子进程照跑，`blender_rt_job(op="collect"|"wait", id=…)` 收结果。
- **runId 由客户端生成**并随请求下发 → 窗口到点时**立刻**有可用句柄（旧版只能去猜 /status 里的最近一条）。
- **关键修复**：`withTimeout` 过去只覆盖「等响应头」——流式路由（`/headless` 立刻发头 + 每 15 s 心跳）
  之后 `await r.text()` 是无限期的，于是等待窗口形同虚设、外层 deadline 一到整段丢结果。
  现在计时器活到 body 读完（同一个 AbortController）。
- 新增 `as_job` / `wait_s`（F3）：`as_job=true` 直接后台化，`wait_s=N` 最多再等 N 秒，没完就回 jobId；
  另有 `DSH_HEADLESS_AUTO_JOB_MS`（默认 0=关）把「预计预算 ≥ 阈值」的调用直接后台化。

### P0-2 · D5：长任务可观测（PYTHONUNBUFFERED + stage 心跳）
- 三处 spawn（headless / job / worker）统一走 `baseChildEnv()` → 注入 **`PYTHONUNBUFFERED=1`**；
  实测：job 的 `stdout.log` 在**运行期**就有增量（旧版全程 0 字节，退出才落盘）。
- 新增 stage 心跳契约：脚本里 `dsh_stage("building")` / `K.progress("building", i=3)` →
  打印 `DSH_STAGE {单行 JSON}`；引擎自动打 `boot / engine-prelude / preload-done / script-start / script-end / done`。
- `op=status` 新增 `stage / stageName / stageAt / stageAgoMs / lastOutputAt / idleMs / lines / logBytes / pidAlive`。

### D2/D3 · 回执结构化（一次重构）
- 工具返回**两个 text block**：block 0 = 单行 JSON 信封（可直接 `JSON.parse`），block 1 = 人读摘要。
  信封字段：`kind/status/ok/runId/jobId/resultJson/resultTruncated/resultPath/resultPathWsl/resultBytes/`
  `resultParseError/stdoutTail/stderrTail/logs/artifacts/shots/inputFile/pathWarnings/stage/...`。
  （宿主只透传 ContentBlock[]，没有 json block；而每个 text block 在 provider 侧是独立 part → 第一块纯 JSON 即可解析。）
- **末行非 JSON 不再顶掉真实输出**：解析失败降级为 `resultParseError`，`stdoutTail`/日志保留原文（旧版回 `{_parse_error, raw}`）。
- 大结果：>4 KB 自动落盘并给 `resultPath` + `resultPathWsl`（`resultTruncated=true`）；`out_json=` 显式指定亦可。

### D6 · 作业层接口补齐
- `op=start` 与 headless 同形参：`script_file` / `args` / `env` / `factory_startup` / `bootstrap` / `workdir` / `include_noise` / `out_json`。
- 新增 **`op=wait`**（阻塞到完成或超时，默认 120 s/次、上限 600 s，服务端走流式心跳）——
  不必再连发 `op=status`（会撞外层 harness 的重复调用检测）。
- **run 与 job 同一 id 空间**：`status/collect/wait/kill` 都收 `run-…`；`collect` 对 run 也能 tail 日志。
- 状态对账：后端重启后磁盘台账里 `running` + pid 不存在 → 收敛为 **`stale`** 并给 `staleReason`（不再谎报 running）；
  `op=kill` 对未知 id **不抛错**（回「已结束/不存在」+ `alreadyFinished`）。
- 租约门禁：`wait` / `kill` 不再受**写租约**阻塞（它们是读/终止自己的作业，不碰 live 场景）。

### D4/F4/F6 · 路径空间与指纹
- `outdir` / `out_json` / `file` 收 WSL 路径（`/home/…` → `\\wsl.localhost\<distro>\home\…`），回执给 Windows + WSL 两种真实路径。
- **`winToWsl()` 补 UNC 分支**（`\\wsl.localhost\…` → `/…`）：旧版原样返回 → workDir 用 WSL 路径时台账/results/trajectory 全落在一个 Node 打不开的串上（实测）。
- 路径体检（`pathWarnings`）：脚本把 POSIX 绝对路径交给 Windows API（`scene.render.filepath` / 日志里 `Saved: 'C:home…'`）会被抓出来并给 `K.win_path()` 修法。
  *口径说明*：D4.3 原文建议「显式报错」；这里落成**可审计的告警 + hint**（不中断脚本）—— 因为脚本中途硬失败会让「已经渲好的图」一起丢掉。
- `inputFile`（F6）：`file=` 回执带 `{path,size,mtimeMs,mtime,md5}`（>512 MB 只哈希前 64 MB 并标 `md5Partial`）。

### F1/F2 · 多视角渲染一体化 + 取景自诊断
- `blender_rt_headless(shots=[{name, from, look_at, lens, res, samples, ortho, ortho_scale, margin}], outdir=…)`：
  走 `qc_render.py` 的 `qc_render_views`（自动三点光 / 渲染锁 / 逐张 md5 / 设备回读），回执 `res.shots =
  [{name, path, ms, bytes, md5, coverage_estimate, warning?}]`；主体占画面 <5% → `warning.code="subject_too_small"`。
- `shots` 也支持对象形态 `{views:[…], res, samples, tag, warmup, margin}`；与 `script` 可同给（先跑脚本再出图）；`as_job=true` 同样有效。

### 顺手修掉的静默失败（验收过程中实测发现）
- **`blender_viewport(op=status)` 在 addon 未连接时也能用**：旧版第一句 `addon.send` 就 throw → 本地信息
  （版本自证 / 台账 / workDir 可写性）全都看不到，而这恰恰是最需要它们的时候。
- **workDir 可写性自证**：`status.ledger` 增 `writable / workDir{wsl,win} / lastError / hint`，`doctor` 直接打印「工作目录：…（⚠️ 不可写 …）」+ 修法；
  `op=status` 顶层增 `workDir / workDirWritable / workDirWarning`。
- `jobStart` 的引擎前导与 headless 对齐（`engine="none"` 不再被塞进 EEVEE 前导）。

### 验收
- 新增 `tests/acceptance_v093.mjs`（`npm run test:acceptance`）：**62 条断言全绿**（真机 Blender 5.2.2 / EEVEE），
  逐条对应 §8：D1/D2/D3/D4/D5/D6/D6-stale/F1/F6/F5-doc。
- 回归：`npm test`（协议自检 28 项）全绿。
## v0.9.2（2026-09-23）—— DSH 更新适配：取消/超时契约 + 宿主 API 自证 + 图片静默失败 + bundle 声明

来源：对「DSH 更新后本插件会不会坏」做的一次逐包 diff + 实测核查（宿主 0.1.6-alpha.1 ↔ npm 最新
0.1.7-alpha.2）。结论是**工具定义契约零漂移**（15 个工具在 0.1.5-rc.2 / 0.1.6-a1 / 0.1.6-a2 /
0.1.7-a1 / 0.1.7-a2 五个 dsh-tools 版本下全部注册成功、参数 schema 逐字节一致），
本版修的是**契约之外的四个面**：

### R3 宿主取消/截止信号贯通（曾经会"假死"）
- 新增 `EXEC_CTX`（AsyncLocalStorage）+ `execSignal()`：`vTool()` 统一把宿主的 `exec` 放进
  异步上下文，`withTimeout()` 用 `AbortSignal.any` 合并 `exec.signal` —— **15 个工具体一行没改**。
- 取消时的措辞固定为 `ABORT_HINT`（只说"等待被取消"，不谎称任务失败；提示长任务按 `runId` 回收）。
  实测：已 abort 的信号下 `blender_viewport(op=status)` **20 ms** 返回可读错误（旧版会一直等到内部超时）。
- **每个工具补静态 `timeoutMs`**（常量 `T`）：一律取"内部最长超时 + 余量"，保证内部超时先触发。
  背景：宿主 `@deepseek-ai/dsh-tool-call-timeout-policy` **只在工具自己声明 timeoutMs 时才设截止**，
  不声明 = 上游一旦给默认值，就会出现"Blender 侧还在跑、工具已被判超时"的静默错位。

### R2 宿主 API 自证（解析链是活的，不是副本）
- 本机解析链：插件 `node_modules/@deepseek-ai/dsh-tools` → `~/dsh-harness`（fake checkout 软链）→
  `/usr/lib/node_modules` 的**全局安装**。好处是与宿主同版本（无陈旧副本），代价是全局安装位置一变
  就在载入期 `ERR_MODULE_NOT_FOUND`（15 个工具全消失，看不出原因）。
- 新增 `HOST_API` 自证：`blender_viewport op=doctor` 打印 `宿主 API：dsh-tools@<版本> @ <路径>`；
  `op=status` 的 JSON 增加 `hostApi` / `toolVersion` 字段。

### R8 图片回传不再静默（`toAttachment`）
- 旧行为：附件服务不可用/超限一律 `catch → undefined`，模型只看到纯文本、没有任何告警。
- 新行为：返回 `{ref}` 或 `{why}`；`rt_see`（视口/自定义视角）、`rt_do`、`rt_watch` 在未回传时把
  `⚠ 图片未回传：<原因>` 写进工具文本。

### R5/R1 按 `dsh.bundle` 约定声明 bundle，并**实际切到 bundle 式装配**（2026-09-23）
- `package.json` 加 `dsh.bundle.patch = ./cordis.patch.yml`；新增 `cordis.patch.yml`（insert 自己的行）。
  `@deepseek-ai/dsh-plugin-manager` 用该字段判定"能否作为 profile layer 管理/装配"，没有它只能靠
  super-injector 运行时注入（注入器一坏，插件整体消失）。
- 本机已完成切换：插件列进 profile 的 `dsh.profile.bundles`，**`dsh-super-injector` 被卸载并删除**
  （源码 / 状态目录 / junction / 分发产物 / .gitmodules 条目全清）。同批把另外两个只靠注入活着的插件
  （`dsh-coc-dice`、`dsh-session-list-cache`）也迁到 bundle 式 —— 否则拆掉注入器会把它们一起弄丢。
- 装配变更记录、迁移表、回滚与新自检命令见 `docs/DSH-更新适配.md §4`。

### 可复跑回归
- 新增 `tests/dsh_api_compat_probe.mjs`（`npm run test:dsh-api`）：用宿主或**指定版本**的 dsh-tools
  跑一遍注册，断言 15 工具 / 15 timeoutMs / 0 报错，可输出基线 JSON 供跨版本 diff；`--smoke` 还会
  用已 abort 的信号真调一次 `blender_viewport`，验证取消链路。
- `tests/README.md` 顶部加「DSH 更新后先跑这三条」。

## v0.9.1（2026-09-18）—— 修 rifle-build《93 反馈》：静默失败（P0）×4 + API 一致性 + 文件级算子 + GUI 原语 + 渲染队列

来源：rifle-build（QBZ47-5.8 影视级建模）全员 14 条实测反馈（lead + 5 名子代理，逐条带现象/复现/证据路径）。
**逐条回代码核实 + 实证复现**：A1/A2/A3/B3 四条**已复现**；D2（自定义视角回空帧）**未能复现** —— 那是瞄空
（from/look_at 与几何不在同一处：我用正确坐标时画面正中就是模型），但这恰恰说明它不该"静默给一张空图"，
本版给它加了自诊断（见 D2）。

### P0-A1 未知名视角不再静默替换（qc_render v3）
- `views` 非空且**一个都没解析成功** → `ok:false` + `unknown_views` + `available_views`（22 名目录）+ `hint`；
  **一张都不渲**（旧行为：静默兜底渲 4 视角且 `ok:true` → "文件名对、角度错"的图混进证据链）。
- `views` 省略/空 → 与 v0.8.11 一致的默认 **3 视角**（iso/front/right）。要 Procedura 的 4 视角集就显式传那四个名字
  —— 补丁版不静默改默认产物集。
- 返回体新增 `requested_count / rendered_count / unknown_count`（成员踩过"12 个名字只渲 8 张不报错"）。
- 补**拼写类**别名：`side_left/left_side/left_view → left`、`side_right/right_side/right_view → right`、`front_view → front`、
  `back_view → back`、`top_view/topdown/top_down → top`、`bottom_view → bottom`、`iso_left → iso_l`、
  `iso_right/isoright/iso_back_right → iso_br`；**语义模糊的一律不猜**（`muzzle_end`/`breech_end`/`*_end` 仍进 `unknown_views`）。
  实测：`["front","totally_bogus","side_left"]` → 3/2/1，实际渲 `front`+`left`。

### P0-A2 无头 QC 默认 `view_transform="Standard"`（qc_render v3）
- 缺省 = `"Standard"`（去掉 AgX/Filmic 洗淡，阈值判读才可靠）；传 `"scene"` = 沿用场景（旧行为，beauty 图用）；传具体名 = 用那个。
- 返回体与**每行 jsonl** 都带实际用的 `view_transform`；出图后**逐项还原**场景（实测：缺省返回 Standard 而场景仍是 AgX）。
- ⚠ 行为变更：依赖"跟随场景色彩变换"的老调用请显式传 `view_transform="scene"`。

### P0-A3 结构化结果不再只能从 stdout 切片
- `blender_rt_headless` 新增 `out_json=<path>`；**>4KB 的结果自动落到工作目录 `results/<runId>.json`**（返回体 `resultPath`/`resultBytes`）。
- 工具文本里 `result` 显示上限 2 KB → **6 KB**；已有结构化结果时 stdout 块只留头尾（不再重复放大）。
- plan 通道结果上限 **4 KB → 12 KB**（量测表/探针输出不再被砍）。

### P0/P1-A4 假失败分档 + 按 runId 回收
- 返回体新增 `status`（`finished / script_error / blender_error / timeout / gpu_required_missing / blender_exe_missing`）+ `failure_hint`。
  **实测坑**：脚本 `raise ValueError("boom")` 时 Blender **退出码仍是 0**，旧判据只看 exit 会漏判 → 现在按"回执缺失 + stderr 异常标记"判 `script_error`。
- `runId` + `how_to_recover` 进返回体：客户端断连**不代表失败**，用 `blender_rt_job(op="status"/"collect", id=runId)` 回收。

### P1-B1/B2 API 一致性
- `K.dsh_*_api` 从普通 dict 换成**可调用 dict 子类**：`api("op", {…})` / `api({…})` → **已解析的 dict**；`api.call(...)` 同义；
  `api["dispatch"](op, json_str)` **仍返回 str**（引擎契约不变）。覆盖 audit / contract / txn / deliver / generator / motion / planner / presets / perf / runner + qc_render + view。
- 顺带记一个坑：`dict` 是静态类型，`instance.__class__ = 子类` 会 `TypeError`（实测）→ 实现改成"换成新实例"。

### P1-B3 headless 收脚本文件
- 新增 `script_file=<.py>`（Windows `D:\…` 与 WSL `/home/…` 都收）；`file=` 传 `.py` 时**自动识别为脚本**并标 `auto_from_file:true`
  （旧版直接 `File format is not supported in file "…py"` —— 实测复现）。

### P1-B4 参数/环境契约
- 新增 `env={…}` 入参；插件自动注入 `DSH_RUN_ID / DSH_OUTDIR / DSH_ARGS / DSH_SESSION / DSH_PLUGIN_VERSION`，
  脚本里可读 `K.args / K.run_id / K.session / K.outdir_env / K.env`。
- **实测坑**：从 WSL 侧 spawn Windows 的 blender.exe 时 **env 不自动跨界**（Node 传了 6 个 DSH_*，子进程 `os.environ` 一个都看不到）
  → 自动声明 `WSLENV` **并**在脚本里再注入一次（双保险）。实测 `K.args == ["delta","echo"]` 且脚本内能看到 `DSH_*`。

### P2-C1 文件级干涉/重叠算子（audit v3：AUDIT_VERSION 2→3）
- `audit_overlap(file_a, obj_a, file_b, obj_b)`：BVH 三角面对 + **真交线段长度** + 交叠 bbox（场景单位与 mm 都给）；
  两端都可以是"当前会话对象"或"另一个 .blend 文件"，不必先 `register_component`。
- `audit_interference(...)`：**交集体积估计（mm³）+ 95% 置信区间 + 三态**（supported / refuted / unresolved）；
  口径写清：蒙特卡洛射线奇偶校验、只在交叠区域采样、**不是精确布尔**；开放网格/非流形 → `unresolved`（不给看着很准的假数）。
  实测（1×1×1 立方体 vs 沿 X 平移 0.5，解析交集体 5×10⁸ mm³）：`audit_overlap` pair_count=8 / 交叠 bbox 与解析盒精确一致 / 真交线段 500 mm / 18 ms；
  `audit_interference` **496,620,000 mm³ vs 解析 500,000,000 → 相对误差 0.676%**（95% CI 0.623%）→ `supported`；不相交 → `refuted`（上界 1 mm³）；开放网格 → `unresolved` 且 `volume=null`。
  自检 `tests/audit_file_selftest.py` → **50/50**。R2 自检还抓出并修掉三个真 bug：射线推进 eps 必须 > float32 几何分辨率（否则 0.92% 系统性偏差 → 体积少 10%）、
  `_ray_limit` 漏除方向分量（斜射线提前 1.73× 截断）、双文件加载时两侧 cleanup 互相删对方对象。
  ⚠ 误差口径修正：我 brief 里那句"n=200k、占比 10% 时约 ±0.4%"偏乐观，正确值 **±1.31%**（公式已写进返回体 `error_caliber`）。

### P2-C2 audit 类 op 支持 `file=`
- `audit_connectivity / audit_gate / audit_measure` 都接受 `file=<.blend>`：临时加载 → 同一分析链路 → **无论成败都清理**；
  返回体带 `source: "file:<path>"` 与 `cleanup` 明细（实测 file= 结果与会话侧 objects/tris/bbox 逐项相等，对象名保持文件原名）。

### P3-D1 GUI 原语（view v2）
- `gui_frame(object=…)` / `gui_shading(mode=…)` / `gui_open(path=…)` / `gui_help()` —— 由插件侧在**真 UI 上下文**执行
  （`rt_do` 里 `bpy.context.screen` 是 None）。实测 `gui_frame(object="Cube")` → `framed:"Cube", mode:"selected"`。

### P3-D2 自定义视角自诊断（view v2）
- 返回体带 `coverage_estimate`（底色取**众数**，`background=true` 也不会误判）与 `objects_in_frame`（把物体 bbox 投到画面里数）+ `scene_bbox`；
  数不到几何时给 `warning{code:"frame_looks_empty", scene_bbox, suggest:{from,look_at,lens}}`。
  实测：瞄空帧 → 覆盖 0.00% + 告警 + suggest；照 suggest 再出一次 → **覆盖 68.11%**（不必自己按 Home）。
- 顺带修：`x-dsh-view` 头过去只带 5 个字段且截 500 字符；**非 ASCII（中文告警）直接 502 "Invalid character in header content"** → 头值改 URL 编码。

### P2-E1 渲染队列/锁（qc_render v3）—— 替代成员自建的 `render_lock.py`
- 跨进程文件锁 `<工作目录>/locks/render.lock`（TTL + 持有者 + 等待毫秒）：`blender_rt_plan(op="render_lock", args={action:"acquire"|"release"|"status"})`；
  `qc_render_views` 渲染前后**自动** acquire/release（`lock=false` 可关），`waited_ms` / holder / stale 写进返回体与每行 jsonl（**计时口径可审计**）。
- holder 缺省 `DSH_SESSION#<pid>`（同一会话里并发多进程也彼此唯一）；断锁**只认 `acquired_at` 时间戳 + TTL**，不看进程存活。
- 实测：第二个持有者 `wait_s=1` → 拒绝并回报 `waited_ms=1001` + 当前 holder；错误 holder release 被拒；**过期锁被打破**（伪造 2 小时前的锁 + TTL 60s → `stale_broken:true`）；
  线上真锁现场：一个被掐掉的进程留下锁 → 600s TTL 到期后被下一个 acquire 打破。

### P3-E2 默认产物路径带会话
- 会话名 `DSH_SESSION`（缺省 `plugin-pid-<pid>`）：evidence 默认路径 → `dsh_evidence_<session>.png`（实测）；`status` 里给 `session` / `resultsDir`。

### 顺带修掉的工具层真 bug（本轮实测发现）
- **plan 工具的 `args` 会被静默丢弃**：harness 的 `type:"json"` 有时把对象给成**字符串**，旧代码 `typeof x === 'object'` 判空 →
  **任何 plan op 的参数被无声忽略却返回 `ok:true`**（本轮 `gui_frame` / `gui_shading` 踩到；给 `bogus_key` 也不报"参数不匹配"）。
  现在 `jsonPayload()` 容错解析（对象/JSON 字符串都收），非法 JSON 明确报错。
- `args` 传数组被 `String()` 变成 `"[delta]"` → 数组直传。
- `perf`/`opt` 无参 op 传了 `{}` → python 侧多收一个位置参数（实测 `_dsh_perf_status_cycles() takes 0 positional arguments but 1 was given`）→ 空参回退 `undefined`。

### 自检与回归
- 新增 `tests/engine_probe.sh`（**16 项**后端 HTTP 探针）→ **16/16**；新增 `tests/view_diag_selftest.py`（**16 项**）→ **16/16**；
  `qc_render` 自检 → **29/29**（v3）；`tests/audit_file_selftest.py` → 见下；既有 9 个自检全部复跑。

---


## v0.9.0（2026-09-17）—— Procedura 融合：装配级判据 + 数值化改动度量 + 判定随几何失效

来源：对 [SpatiaOS/Procedura](https://github.com/SpatiaOS/Procedura)（MIT，Agentic 3D Modeling with Procedural Control）逐文件精读后的移植评估
（分析表：`docs/Procedura-融合分析.md`）。**只搬判据与算法，不搬技术栈**（不引 OpenSCAD / Isaac / 它的 TS 运行时）。
全量回归：9 个自检各自独立进程全绿（下面逐条给实测数字）。

- **★ 先修一个真 bug：`audit_*` 从来没接通**。`docs/` 里一直文档化的 `blender_rt_plan(op="audit_mesh" / "audit_scene" / "audit_duplicates")`
  实际会掉进契约层报 `unknown contract op`（实测 `audit_help` 返回 `"unknown contract op"`）—— audit.py 只在 preload 通道可达。
  本版把 `audit_*`、`montage` 正式接进引擎路由（`engine.mjs` + `server.mjs` 只读名单），并补上 `motion_* / deliver_* / generator_*` 三条新路由。

- **装配级连通门 `audit_connectivity` / `audit_gate`**：连通分量 + 浮块归因 + 微隙分级。
  判据：可见浮块 = 最长 bbox 边 ≥ 全模型最长边 **1%**；相接口径 `micro_gap_mm`（默认 0.3）。
  **与上游两处有意分歧**（都写进返回体，不藏）：
  ① 不再用「最大体积分量=主体、其余都是浮块」—— 两个等大零件时那条规则会随便挑一个当浮体（自检抓到过，A/C 体积都是 1.0）；
     改成**每个分量都问「你有没有跟别的分量相接」**（`attached` 三分类：floaters / tolerated / unconfirmed）。
  ② 上游靠 bbox 间隙判 micro（单网格够用；**多对象装配里 bbox 普遍重叠 → 间隙恒为 0 → 一律放行**），本版补**网格级复核**：
     BVH `find_nearest_range` + **面上采样**（≤400 点，取顶点会漏 —— 大平面间的 0.2mm 缝顶点可能相距几米）。
     自检用例 F（bbox 与 A 重叠、面到面 1.32 mm）被判浮块；用例 C（0.2 mm 缝）判真 micro。
  大网格（>50 万三角面）跳过网格复核 → `attached=None` → 门**降级 ok=null**，未确认不得读作已通过。
  口径与分布随返回回传（`gate_caliber` / `gap_histogram` / `floater_gap_range_mm`）。
  **真机实测（COCKPIT_DELIVERY，14 对象 / 97,438 面）**：0.3 mm 口径 → 178 个可见浮块（与最近邻相距 0.33–1.14 mm）+ 970 个确认相接；
  2.0 mm 口径 → 63 个浮块。说明该模型是「贴着不接触」的装配件，不是单一实体 —— 口径该给多少取决于你要什么。

- **网格漂移度量 `audit_drift`**：对称 Chamfer 距离 = **形状漂移**（不是倒角！）。上游口径是「各自归一化 → 点到最近采样点」，
  本版换成 **BVHTree 点到曲面**：20k 采样 **21,397 ms → 46 ms（460×）**，且同网格读 **0.000000**（上游口径的采样噪音底是 0.017）。
  ⚠ 语义：归一化会消除平移与整体缩放 → 它回答「形状改了多远」，不回答「挪了多远」；位移另由 `bbox_delta.moved_mm` 报（自检把这两条都钉住）。
  band：≤0.01 基本未变 / 0.01–0.05 轻微（≤最长边 2.5%）/ >0.05 明显；grid 兜底口径系统性偏大，返回里标 `metric`。

- **测量包 `audit_measure` / 浮块贴合 `audit_snap_floaters`**：世界 bbox + 逐轴间隙/重叠（mm 与 %）+ 邻居（契约图 + 空间最近 K）；
  snap 默认只报告，且**只对「整件属于该浮块」的对象平移**（焊接体一块不撕 —— 上游 v3 snap 的翻车点），
  bbox 读成 0 时自动改走**网格级最近点对**。

- **证据随改动失效 + 判定漂移自动降级（契约层）**：新增 `fingerprint`（对象/面数/点数/世界 bbox + 12 位 digest）；
  证据与判定都绑指纹，`ledger` 逐条对拍标 `stale`；`verify` 记 bbox 快照，之后几何漂移 > 10% 对角线（或尺寸 > 20%）
  → `supported` 在 status/ledger 读取时**自动降级 unresolved**（带动画数据的对象跳过）。契约自检 24 → **33 项**。

- **渲染 harness 三件（qc_render v2）**：`mode="parts_color"`（12 色逐部件配色 + `parts_color_meta.txt` 图例 + 出图后逐项还原，
  共享 mesh 会先复制隔离，否则串色）、jsonl 每行**实测 `device`**（防 Cycles 静默回落 CPU ≈7×；拿不到就 `null` + 点名，不猜）、
  `qc_render_catalog`（22 视角 / 4 组 / 别名归一：`iso-FR-top`/`isometric`/`isoright` → `iso`；**未知名不再静默当 iso 渲**）。
  顺带修两个既有问题：`op="qc_render_help"` 过去一直落到 unknown（engine 会剥 3 个字符前缀，已补 `render_*` 别名）；
  `_set_engine` 在取景失败早退时会把引擎改掉不还原。自检 14 条 + 边界 15 条全绿。

- **配合门 + 装配特征库 + 干涉升级（契约层 v3）**：`mate_check` 在**实际网格**上量接触面积占比 / 单边间隙中位值 / 侵入深度
  （接触距离 = 1.5% 合并对角线；采样 ≤4000；双向面积占比取 max；fit 四档 clearance 0.25 / location 0.15 / press −0.05 / snap 0.20）；
  `fit_help` 读 `runtime/assembly_features.json`（9 特征 + 四档 + 导向倒角 + ISO 273 + 共享标称纪律）；
  `interference_report` 逐对报深度/体积占比/严重度，把已注册连接的两端标 `declared`（计划内接触默认豁免，超 `depth_eps` 默认 0.2 mm 仍报）；
  `check_interface` 做**加法升级**（老 bbox 字段逐字不变 + 新增 `mesh` 块）。
  实测：正常配合单边中位间隙 **0.1498 mm**（设计 0.1500）→ supported；挪远 5 mm → refuted 且点名「完全没搭上」；
  深穿模 depth_max **3.000 mm** > 0.2 → refuted；`samples=2` → unresolved；过盈档实测 0.0499（设计 0.05）→ supported。
  自检 33 → **57 项**。

- **待定编辑事务 + 交付导出（txn v3 + 新模块 deliver.py）**：`edit_begin / edit_check / edit_accept(verified) / edit_revert(why) / edit_status`
  ——同一时间一个 pending；接受必须「有复核 + 一句 verified（空串拒）」；撤销免预算但有上限 `max(2, 累计步数)` 且防抖（同 label 连续 revert 拒）；
  `deliver_export / deliver_verify`：单位盒归一化 + 多组 OBJ/MTL + manifest（md5/字节/单位换算/归一化三件套），
  改一字节或加一个面必须 FAIL。txn 自检 **55 项**、deliver 自检 **53 项**。

- **轨迹 JSONL**：每次顶层调用写 `<工作目录>/trajectory/blender_rt-<日期>.jsonl`（route/op/ms/ok/参数摘要/摘要 hash），
  超 16 MB 轮转；`DSH_TRAJ=0` 关、`DSH_TRAJ_FULL=1` 记更多参数；`engine.status()` 里给路径。

- **铰接/机构 `motion_*`（新模块 motion.py）**：关节轴/锚点**实测**（旋转对称 + 接触带两条独立证据，冲突时报 unresolved 而不是二选一）、
  **扫掠验证**（替代 Isaac：多相位驱动 + BVH 干涉，零干涉且确实位移 → supported；撞上就 refuted 并点名，实测点名 `MS_Blocker`，最深 0.019 m）、
  URDF/USDA 导出（单树合成、退化关节降级 fixed 并报 warning、结构自检：XML 可解析 / 名字唯一 / 端点存在 / 无环 / mesh 文件在）。
  扫掠后**所有对象 matrix_world 逐项对拍还原**（max_delta 0.0）。自检 25/25（另一条证据通路：铰链沿 Y + 无销的接触带定轴 + prismatic 滑块）。
  **过程中修掉三个真坑**：① `%.9f` 会把 mm 级质量 8.6e-11 静默写成 `0`（改 `%.9g` 并加断言）；
  ② `meters_per_unit` 默认 0.001 在米制场景会给出荒谬量级 → 新增 `meters_per_unit="scene"`（读 scale_length）+ 单位可疑警告；
  ③ 同一对象被两个关节组认领会在 URDF/USDA 里重复计入几何与质量 → 现在 `ok:false` 并列出 conflicts。
  边界（`motion_help` 里写明）：是运动学+BVH 扫掠，不是物理验证；零干涉需至少一个旁观者对象，否则 unresolved；
  轴推断只对可辨识几何有效（薄平板 → unresolved）；USDA 是最小可用 ASCII，不是完整 UsdPhysics schema；本机没有 URDF/USD 消费者可做端到端加载验证，只有结构自检。

- **生成器 `generator_*`（新模块 generator.py）—— #18 代码化建模通道的 Blender 原生轻量版**。
  本机两侧都没装 OpenSCAD（真接它要 Manifold 后端：同一 `hull()` CGAL 1774 s vs Manifold 1.77 s），所以把那四件事用 Blender 原生方式拿到：
  **程序即形状**（注册一段 python 生成器）+ **编译门**（在全新无头进程里从零复现）+ **模块级缓存**（源码 hash + 参数 hash 都没变就不重跑）
  + **源码一变回执过期**。自检实测：3 个方块 supported（805 ms）→ 再跑命中缓存（ms=0）→ expect 不符 refuted → 没给 expect unresolved →
  改源码后 diff.changed=true/verdict=refuted → 坏脚本 refuted。

**顺带修掉的两个基础设施问题（都来自本版实测）**：
- **preload 多模块互相覆写 helper**：engine 把多个模块源码拼进**同一 globals**，而每个 runtime 模块都定义 `_j/_kernel/_store/_now` →
  实测 `preload="txn,deliver"` 直接把 txn 打崩（`KeyError: 'marks'`）。现在每个模块 exec 到**自己的命名空间**
  （私有名不外泄，公开名与常用 import 照旧拷回 globals → 向后兼容），两个路径（headless / 作业层）都改。
- **两条通道语义坑**（已写进各自的 scope/help）：① headless 的 `ok:true / exit=0` **不等于**脚本成功 —— Blender 的 `--python`
  抛异常时退出码仍是 0（生成器自检抓到），判定必须看回执/异常标记；② 源码注入通道里**没有 `__file__`**（读同目录数据文件要用 `K.runtime_dir` 兜底，
  见 `contract._features_path()`）。

---


## v0.8.9（2026-09-16）—— 修「>15 s 的调用静默失败」等 6 条（外部会话反馈，批次 1）

来源：另一会话使用插件的复盘（通道卡点 5 条）。逐条回代码核对后，**1 条是真 bug、4 条是小缺口**，本版全部修掉。

- **★ 真 bug：超过 15 s 的 headless / worker / txn / preset 调用"静默失败"**。根因：这四个路由走
  `streamJson(res, producer, hbMs=15000)` —— **每 15 s 先写一行心跳** `{"heartbeat":true,...}`，最后一行才是结果；
  而客户端 `backendPost` 是 `await r.text()` + **整段 `JSON.parse`** → 多行就抛 `Extra data: line 2 column 1` →
  降级成 `{ok:false, raw:...}` → 工具层只打印 `HEADLESS 失败 · exit=undefined · undefinedms`（没有 result、没有日志、没有 hint），
  **而服务端子进程其实已 exitCode=0 跑完**。
  修法：解析失败时按行从后往前取**最后一条非心跳 JSON**，并把心跳条数记进 `__heartbeats`；工具层在 >15 s 时显式提示"本次走了流式回执 N 条心跳"。
  实测：20 s 脚本 修前 `HEADLESS 失败 · exit=undefined · undefinedms`（21477 ms）→ 修后 `HEADLESS ok · exit=0 · 21406 ms` + `result: {"slept":20}` + 日志路径；
  `blender_rt_worker exec`（sleep 20）修后 `WORKER exec ok · 20000ms` + `result: {"worker_slept":20}`。
- **作业层支持 preload**（原来只有 headless 有）：`blender_rt_job(op=start, preload="qc,qc_render")` 把 runtime 模块源码拼进作业脚本。
  实测：作业内 `K.dsh_qc_render_api.version = 1`、API 面 `["dispatch","help","render_views","selftest","version"]`（job-mu45mi7xip4，done/exit=0/8019 ms）。
- **headless 的 `args` 支持引号**：旧实现 `split(/\s+/)` 把 `--flag="a b"` 切成两个 token；现在是引号成对时不当分隔符。
  实测：`args='--alpha "a b" --x=1 plain'` → `["--alpha","a b","--x=1","plain"]`。
- **读图零尺寸守卫**（qc.py，QC_VERSION=4）：Blender 对不支持的格式（如 GIF）**静默**返回 0×0，不抛异常 → 现在直接报
  `读图失败或格式不支持：<path>（Blender 能解 PNG/JPEG/WebP/BMP/TGA/TIFF/EXR；GIF 会被静默读成 0×0）→ 先用外部工具转成 PNG`。
  实测：加载 2×2 GIF → 修前 `size=[0,0], has_data=false`（静默）→ 修后上面这条明确报错。
- **错误提示补两条 Blender 语义坑**：`StructRNA of type ... has been removed`（→ 换文件前抓的 Object/Collection 引用全部作废，open 后重新取）
  与 `context is incorrect`（→ 重设 active/select，或 `temp_override`，或拆成两次调用）。两条均实测触发并回显提示。
- **长活边界写进工具输出**：headless 单次 >60 s 时提示"下次直接 `as_job:true` / 走 blender_rt_job"。

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

> ⚠️ **本节的 `ahujasid` 是改名前的历史称谓**：v2.0.0 起协议判别 id 改称 **`flat`**，`ahujasid` 保留为**已弃用别名**（旧配置仍被接受，`resolveProtocol` 归一化为 `flat`）。本节文字按改名前的原文保留。

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
