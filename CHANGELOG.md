# CHANGELOG — @dsh-external/dsh-blender-plugin

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
