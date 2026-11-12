# 验证记录 — blender-vision-opencv

日期：2026-11-12（Asia/Shanghai）
环境：Windows 11 / Node v24.14.0 / Python 3.11.1（.venv，pytest 8.4.2）/ ruff 0.17.3 / 无 Blender 可执行文件 / 无外网（github.com 与 PyPI 均不可达）
沙箱：workspace-write；Node 子进程管道捕获被拒绝（spawn EPERM），因此所有依赖 `spawnSync`/`execFileSync` 包装脚本的 npm script 在本机不可用，改用直接命令逐项验证。

## 1. 门禁结果（8.1）

通过（exit 0）：

| 门禁 | 结果 |
| --- | --- |
| `tsc -p tsconfig.json / runtime / tests / scripts --noEmit` | 4 个工程均 0 错误 |
| `eslint . --max-warnings=0` | 0 error / 0 warning |
| `prettier --check`（package.json format:check 的 glob） | All matched files use Prettier code style |
| `pytest tests/unit/python tests/integration/python` | 545 collected / 535 passed / 10 skipped / 0 failed |
| `check:any` | 280 files，0 explicit any |
| `check:structure` | PASS，349/349 文件在行预算内，84 文件 / 300 边 / 0 环 |
| `check:routes`（gen-routes 生成后） | OK，42 routes，sha256 `a6e6b6882a73` |
| `check:gateway-schema` | OK，protocol 12，sha256 `7ba8b05b75d3` |
| `check:availability` | OK，9 stages，sha256 `4ea00f868ead` |
| `check:no-shims` | PASS（2 facades / 29 registered exports；3 sanctioned barrels） |
| `check:runtime-layout` | PASS（42 modules in 6 domains） |
| `check:doc-paths --enforce` | PASS（19 legacy docs；25 extended docs，0 orphan，7 baseline refs） |
| `check:plan-ids --enforce` | PASS（1389 tokens in 92 files；residual 0） |
| `check:exceptions` | OK（20 entries） |
| `check:ports` / `check:tsconfig-layout` | PASS |
| `check:behavior-matrix --release` | PASS（335 rows：333 covered / 0 planned / 2 exempt；6 tools / 25 commands / 36 families / 239 ops / 29 routes） |
| `check-python-imports`（import-linter） | 3 contracts kept，0 broken |
| `check:extension` | `ok: true`，77 entries，addon 2.0.0 / protocol 12 |
| `openspec validate blender-vision-opencv --json` | valid |

环境阻塞（非代码失败）：

| 门禁 | 原因 |
| --- | --- |
| `check:function-budget` | `spawnSync .venv\Scripts\python.exe EPERM`（Python 函数扫描无法启动子进程） |
| `check:build` | build-runtime / resolve-tsc / check-structure / build-client-check 均因 `spawnSync node EPERM` 失败；已用直接 `tsc` 重建并人工确认 `lib/index.js`、`lib/client.js`、`lib/runtime/gateway.js` 存在 |
| `check:git-hygiene` | `git` 子进程 EPERM |
| `test:parity` | `spawnSync git ls-tree b8778c8` EPERM（同一命令在交互 shell 中可正常返回） |
| `check:python-static` | `spawnSync` EPERM；vision-core 用 `CHECK_PYTHON_TRACKED_FILES` 注入 383 个 Python 文件后跑出 `scope clean` |
| `npm run *` 包装脚本 | npm 自身或包装脚本需要管道子进程，统一 EPERM |
| `npm pack --dry-run`（带 prepack） | prepack 的 `npm run build` 子进程失败 + npm cache 目录在沙箱外；改用 `--ignore-scripts --cache .npm-cache` 完成内容核对（见 §4） |
| 真实 Blender E2E（5.3/5.4/5.6、7.5、8.3） | 本机没有 blender.exe，无法执行 4.2/4.5/5.2 渲染与 animation 真机用例 |
| 真实托管 venv / pip / PBS / 模型下载 | 无外网；installer 与下载路径由注入 runner / fake download 覆盖，真实 pip、48MB PBS 包与 5 个 ONNX 未下载 |

JS 全量（`vitest run tests/unit/js tests/integration/js`）：1478 tests，1332 passed / 42 skipped / 104 failed，36 个失败文件。逐个核对失败原因：全部是 `spawn EPERM` 或依赖其结果的断言（`spawnSync ... EPERM`、`status === null`、`ensure-build failed`、子进程输出为空），没有业务逻辑失败。典型文件：`tests/integration/js/*`（启动 Gateway 子进程）、`check-git-hygiene`、`http-lease`、`provenance`、`workdir`、`contract/{behavior-matrix,description-budget,doc-paths-gates,exceptions-registry,function-budget-registry,lifecycle-config,plan-ids-gates,ports-gate,python-static-scope,routes-generated,single-source,tsconfig-layout}`。

已通过的重点新增测试（直接 vitest，非 mock 环境）：

- `tests/unit/js/vision-{protocol,manager,runtime-config,models,python-detect,installer}.test.ts`：88 passed
- `tests/unit/js/vision-dispatch.test.ts`、`tests/integration/js/vision-dnn-hot-apply.test.ts`：34 passed
- `tests/unit/js/vision-pass.test.ts` + `vision-png.test.ts`：30 passed
- `tests/unit/js/{home,config-layers,plugin-settings,queue-home,ledger-home}.test.ts`：35+ passed
- `tests/unit/js/render-{cache,pass-executor,pass-receipt}.test.ts`、`run-passes.test.ts`、`render-cache-queue.test.ts`：64 passed
- `tests/unit/js/doctor-cache-render.test.ts`：3 passed（非法 cache.render 值报字段/范围且不崩）
- `tests/unit/js/animation-session-read.test.ts`、`tests/unit/python/test_animation_*.py`：session-read 三态门与 animation 纯逻辑/指标通过

## 2. 8.4 安全与边界复核

- 图片/pass manifest/reference 路径只读：`runtime/vision/dispatch.ts`、`runtime/vision/pass.ts` 只接受已存在路径；拒绝 URL/通配/目录逃逸；`vision-pass.test.ts` 覆盖缺字段、越界与 64MP/帧数/对象数上限。
- 像素与帧数上限：单帧 8 MiB、解码 64MP、pass manifest 1–240、objects ≤64、track ≤120 帧/20 轨迹、detect ≤100 条；超限返回结构化错误（`vision_image_too_large`、`vision_pass_manifest_invalid`、`animation_sampling_invalid`）。
- 无 URL/脚本/任意模型路径：models.yml 只接受模型目录内相对路径；绝对路径、`..`、symlink 逃逸与 URL 均被拒（`runtime/vision/models.ts` 与其单测）。
- worker 无监听端口、不发起网络请求：worker 走 stdin/stdout JSON-lines（`runtime/vision/protocol.ts`、`worker/protocol.py`）；probe 10s 超时、不联网/不安装/不加载 ONNX。
- 模型 id 与 models.yml 路径逃逸被拒：`MODEL_ID_PATTERN`、realpath 校验与 `vision_model_unsupported` 路径有单测。
- 模型下载仅由人类确认、只用固定 URL+sha256：`src/host/dnn-download.ts` 的 `.part`→sha256/字节校验→原子 rename、单并发、同名 key 不覆盖；未验证 pin 在碰文件系统前返回 `dnn_pin_unavailable`（`dnn-pins.test.ts`、`dnn-download.test.ts`）。
- 默认不 import/安装 onnxruntime：`inference/select.py` 懒加载；opencv+cpu 默认不 import ORT（`test_vision_worker_inference.py`）。
- 自定义 venv 只读且插件不 pip/不改驱动：`installer.ts` 的 custom 分支只校验解释器/版本/import cv2,numpy(,onnxruntime)，不创建 venv、不跑 pip、失败不回退（`vision-installer.test.ts`）。
- venv 不写系统 Python：托管 venv 固定在 `<home>/vision/venv`，临时目录 + 原子 rename（`installer.ts`）。
- animation session-read 不取写租约：`src/tools/op/session-read.ts` + `execute.ts` 三态门；`runtime/http/routes/plan.ts` 对 `PLAN_SESSION_READ_OPS` 使用 `leaseGate({readOnly:true})`；`animation_report`/`animation_help` 不在 `READ_ONLY_OPS`/`PLAN_READ_ONLY_OPS`。
- `frame_current`/`frame_subframe` 恢复、selftest 临时 collection 清理：`runtime/py/animation/animation_impl/report.py`、`selftest.py` 的 finally 分支；`test_animation_pure.py` 覆盖。
- 无 control/mechanism/DEF 角色断言：animation 只读 evaluated depsgraph，不读 Action.fcurves，不依赖 control 骨命名。
- `runtime.json` 仅宿主单写、原子替换、无密钥：`src/host/dnn-config.ts` 写临时文件 + rename；`runtime/vision/runtime-config.ts` 的 Gateway 只读；`plugin-settings.test.ts` 验证序列化不含密钥。
- pending 不杀在途 worker：`runtime/vision/manager.ts` 两阶段切换与 `vision-manager.test.ts`（16 passed）覆盖 idle/busy/切换失败。
- probe 不写 runtime/模型、不联网/安装/加载 ONNX：`runtime/vision/worker/probe.py` + `dnn-probe.test.ts`。
- Gateway 不可达不声称已生效：`src/host/dnn-config.ts` 返回 `gateway_unreachable` + `savedForNextStart`；`dnn-config.test.ts` 覆盖。
- `/doctor` 同时提供 `vision.dnn` 与顶层 `dnn` 别名；配置卡优先读非空块，真实 Gateway 下不再出现空 status。

## 3. 8.2 包内容

`npm pack --dry-run --ignore-scripts --cache .npm-cache`：1442 files，package 3.0 MB，unpacked 10.0 MB。
核对结果：不含 `vision/venv`、`vision/models`、`*.onnx`、`cache/`、home 数据、onnxruntime wheel/二进制或 GPU runtime；`runtime/vision/worker/inference/onnxruntime.py` 是懒加载适配器源码，不是运行时依赖。`runtime/vision/assets/GPU-DIY.md`、`requirements.lock`、worker 源码按设计随包分发。prepack 因沙箱无法执行，真实发布前需在可联网环境重跑带 prepack 的 `npm pack --dry-run` 与 `npm run smoke:install`。

## 3.5 真实 Blender E2E（5.3 / 5.4 / 5.6 / 7.5 / 8.3）

环境：Windows 11，Blender **4.2.23 LTS**（`D:\Program Files\Blender Foundation\Blender 4.2\blender.exe`）、**4.5.16 LTS**（同目录 4.5）、**5.2.4 LTS**（`D:\Program File\Steam\steamapps\common\Blender\blender.exe`）。

**render passes（新增 `npm run test:e2e:passes`）**：`tests/e2e/render_passes.mjs` 用仓库自己的 `lib/` 起 JobQueue（SpawnJobExecutor + JsonJobStore + createRenderCache），先 headless 建场景（128×128、CamMain+SunMain+Ground+CubeMain(带 1–2 帧位移关键帧)+SphereMain、帧范围 1–2），再提交 `kind=render, passes=[beauty,depth,normal,alpha,object_index], samples=8, engine=CYCLES`，命令形如 `blender -b --factory-startup <scene> --python render_passes.py -- render-config.json`；产出 `out/passes/frame_0001..0002/{manifest.json, 5 个 PNG}`。

三版全部通过（同样 26–30 项断言）：manifest 版本/分辨率/`degraded=[]`/`blenderVersion`、每个 pass 的条目与 `bytes` 与磁盘一致、`depth.near/far`、`normal.coordinateSpace=camera`、`object_index.dtype=uint16` 且 mapping 非空；另有逐像素校验（`tests/e2e/blender/render_passes_verify.py`，用同一 Blender 以 Non-Color 读回）：depth 落在 clip 内（min 5.715 / p50 7.515 / max 17.753 m）、normal 是相机空间单位向量（median |n|=1.000）、alpha ∈[0,1]、beauty 非全黑、object_index 解码 `round(pixel*65535)` 只出现 `{0,1,2,3}` 且 1/2/3 与 `mapping` 的 CubeMain/Ground/SphereMain 一一对应、背景像素 2510。三版数值逐位一致。

**缓存**：同一提交第二次返回 `result.cache.hit=true`（`entries: 2`，两个 frame key），状态 `completed`，不 spawn Blender。

**object_index 的引擎前置条件（三版一致，实测）**：`object_index` 只在 Cycles 下可用。4.2.23 / 4.5.16 / 5.2.4 三支的 EEVEE 与 Workbench 提交同一请求都返回 `pass_unsupported`，`root_cause` 形如 `the requested pass output is not available in Blender 5.2.4 LTS (object_index needs the Cycles engine; … current engine is BLENDER_EEVEE)`，且 `out-eevee/` 下 **0 个文件**——不会静默落一张全 0 或全 1 的索引图。机制：4.x 的 Render Layers 固定 31 个 socket，`IndexOB` 名字恒在列表里但 `outputs.get()` 只在 Cycles 下解析出 socket；5.x 按引擎重建输出，Cycles 下叫 `Object Index`，EEVEE 下连名字都不出现。E2E 把这条作为反向断言固化了（`DSH_E2E_ENGINE` / `DSH_E2E_PASSES` 可切引擎与 pass 集做矩阵）。其余四个 pass（beauty/depth/normal/alpha）在 EEVEE 与 Cycles 下三版都可用。

**vision_pass**：把同两帧的 manifest 交给 `runVisionPass`，`ok=true`、`frame_count=2`、`passes=[depth,normal,object_index,alpha]`，depth 解出 5.71–17.75 m，对象表按 index 映射还原出 `CubeMain/Ground/SphereMain`，回执 4.4 KB 无像素数组。

**animation（`pytest -m blender -k animation tests/e2e/blender/test_native_e2e.py`）**：4.2.23 / 4.5.16 / 5.2.4 各 16 项断言全通过（`NATIVE_REPORT ok:true passed:1 failed:0`），覆盖 evaluated depsgraph 采样 25 帧闭区间、root motion 位移/路径/速度、reference 精确帧匹配（3 matched / 0 missing / 22 unused）、显式骨链（root 90°/child 0°）、有符号接触与 −0.02 m 穿透、root_bone 世界旋转、无 bone_chains 时 warning、每帧表面重叠、`effective_fps=24/1.001`、缺对象结构化错误、`frame_current` 恢复、selftest 建后清理且 `frame_restored=true`。

E2E 期间发现并修掉的真实缺陷（详见 CHANGELOG）：5.x 的 File Output API 差异（`media_type`、`file_output_items`、`ShaderNode*`、`compositing_node_group`）、IndexOB 未归一化导致 object_index 全为 1、`render-passes-script.ts` 超单文件行预算（拆成 setup/capture 两个模板模块）、`animation_report` 无 armature 时 `UnboundLocalError`、`joint_metrics` 重复乘父骨 rest 导致子骨恒定 90°。

session-read 与 selftest 写门属于宿主侧（`src/tools/op/session-read.ts`、`runtime/http/lease.ts`），不在 Blender 进程内，仍由 `tests/unit/js/animation-session-read.test.ts` 与 integration 覆盖；真机 E2E 覆盖的是读型 op 在真实 depsgraph 上的行为与场景/帧恢复。

## 4. 未完成 / 剩余风险

- 3.3 的 YOLOv5 v7.0 五个预设 ONNX 的 `bytes`/`sha256` 已在 2026-11-12 从 GitHub release 实测采集并写入 `src/host/dnn-pins.ts`；未验证前仍返回 `dnn_pin_unavailable` 的分支保留（供未来换版本时使用）。
- 真实托管 venv/pip/PBS 与真实 ONNX 推理未端到端跑；相关路径由注入 runner、fake download、fake provider 覆盖。

## 5. 独立评审修复轮（2026-11-12 晚）

对 §1–§3 的实现做了一遍独立评审（6 路并行 reviewer + 主 agent 逐条复现），结论 9 CRITICAL / 25 WARNING / 10 SUGGESTION，全部修复后复验如下。

**行为修复（有测试或真机证据）**

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| `vision_*` 经 HTTP `/plan` 仍走写门，必须绑定 GUI 会话（与 host-local 契约冲突） | `runtime/catalog/data.ts` 新增 `PLAN_HOST_LOCAL_OPS`（8 个 vision op），`runtime/http/lease.ts` 的 `isReadOnly('/plan')` 取并集，`src/tools/op/plan.ts` 对 host-local 不写 `write_scope` | `tests/unit/js/vision-plan-lease.test.ts`（真实 `createPlanRoutes`+`leaseGate`+空 `SessionRegistry`+caller 头） |
| 托管安装所有子进程共享 15 s 超时 | 每步预算（download 900 s / venv 300 s / extract 300 s / pip 1800 s / probe 60 s）经 `createVisionInstaller({timeouts})` 注入 | `tests/unit/js/vision-installer.test.ts` |
| `vision.model` 热应用不生效且回执谎报 revision | 请求帧携带 `config:{revision, model}`，worker 合并后按请求 revision 回执；`starting` 期间的 apply 排队到 hello 之后 | `tests/unit/js/vision-dnn-hot-apply.test.ts` |
| 安装/切换与 detect 门禁交叉污染 | 只有 `dnn_config_switching`/`dnn_config_switch_failed` 拦截 detect；`dnn_config_invalid` 只影响 apply/status | 同上 |
| 空串无法把配置层清空 | `null` 清空、`""` 回落低层 | `tests/unit/js/vision-runtime-config.test.ts` |
| `pass.ts` 把 depth raw 0 当无效、8 MiB 行上限可绕过、预算/解码无上限 | `noGeometryValue`/`clampedAt` 契约、逐行长度校验、4e8 像素与 60 s 预算、结构化 `vision_image_too_large` | `tests/unit/js/vision-pass.test.ts`、`vision-protocol.test.ts` |
| 降级帧进入渲染缓存、缓存命中不落 outdir、promote 超 maxBytes | 降级帧一律不缓存；命中复制到用户 outdir；先按字节预算再 promote | `tests/unit/js/render-cache*.test.ts` |
| 动画 `frame_step>1` 把速度放大 step 倍 | `dt = frame_step / effective_fps` 贯穿 metrics/report | `tests/unit/python/test_animation_*`、真机 E2E 断言 |
| 非 mesh 穿插对象被当成“无重叠” | 聚合 `skipped` + verdict degraded，不给出 pass | 真机 E2E 断言 |
| 模型 YAML `rgb: true`/FileStorage/probe 在 OpenCV 4.x 崩溃/溢出 | `_read_bool` 接受布尔拼写、`getNode` 兼容、int32 运算、错误映射到 `vision_*` 码 | `tests/unit/python/test_vision_worker_*`（已装 OpenCV，不再 skip） |

**复验门禁（2026-11-12 22:40）**

`tsc -p tsconfig.json` / `runtime` / `tests` / `scripts --noEmit` 全部 0 错误；`eslint . --max-warnings=0` 0；`prettier --check` 通过；`vitest run tests/unit tests/integration` 172 文件 / 1613 passed / 2 skipped；`pytest tests/unit/python tests/integration/python` **614 passed / 0 skipped**（本轮装了 `opencv-python-headless==4.10.0.84`，原 11 个 cv2 skip 变成真跑）；`check:structure` 352/352 文件（84 文件 / 301 边 / 0 环）、py 178/178；`check:function-budget` 4474 函数，>120 为 0，80–120 段 123 条全部登记；`check:routes` 42；`check:gateway-schema` protocol 12；`check:behavior-matrix --release` 335 行（6 tools / 25 commands / 36 families / 239 ops / 29 routes）；`check-plan-ids` residual 0；`check-doc-paths --enforce` 通过；`ruff check`、`check-python-imports`、`openspec validate` 通过。

**真机 E2E（本轮重跑）**

`tests/e2e/render_passes.mjs` 在 Blender 4.2.23 / 4.5.16 / 5.2.4 上各 **79 项断言全过**（此前 4.2 有 2 项失败：见下）。`test_native_e2e.py -m blender -k animation` 三版各 23 项断言全过。

本轮 E2E 抓到的 3 个问题都在验证脚本/测试场景自身，产品代码经复核正确：

1. `render_passes_verify.py::load_pass` 没处理 Blender 的 bottom-up 行序，`verify_normal` 取“底部 15%”实际取到画面顶部（原始字节底部为 `[0.004, 0.915, 0.404]`，经 Blender 读顶部为 `[0.576, 0.765, -0.294]`）。改为读入后 `np.flipud` 一次。
2. `depth:more_than_8bit_steps` 用“不同 raw 值 > 256”证明 16 bit，但该计数取决于场景覆盖的裁剪范围（本例近 0.1 / 远 1000、表面 5.7–17.8 m，只有 172 个台阶）。改为断言 `raw.max() > 255`（8 bit 文件不可能超过 255），bit depth 16 仍由 IHDR 单独断言。
3. 动画“约束驱动最终姿态”用例的 `LIMIT_ROTATION` 默认 `owner_space='WORLD'`，在骨骼绕自身轴扭转时不会把 90° 夹到 45°；改为 `owner_space='LOCAL'`（这是绑定师语义），指标随即报出 45°。

**仍未覆盖**

- `npm pack` 的真实 prepack（沙箱 EPERM）与 `smoke:install` 未跑；发布前需在可联网环境重跑。
- 真实 `pip` 下载/安装全链路（本轮只验证了预算、原子换 venv 与失败回滚的注入测试）、真实 PBS 与真实 ONNX 推理仍未端到端跑。
- Windows 虚拟桌面 helper 的真实 GUI 路径未在本轮重跑。
