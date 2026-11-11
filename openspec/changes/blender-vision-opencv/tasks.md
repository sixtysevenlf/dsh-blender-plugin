# Tasks

## 1. Home、配置与 DSH 设置

- [ ] 1.1 先写失败测试，再新增 `runtime/home.ts`：`DSH_BLENDER_HOME` → `$DSH_HOME/dsh-blender` → `~/.dsh/dsh-blender` 解析、`ensureHomeLayout()` 幂等创建、`home_unwritable` 失败路径；用临时目录测试解析来源与只读目录错误，跑 `npm run test:unit`。
- [ ] 1.2 先写失败测试，再改 `runtime/config.ts` 为 home 优先的同步配置层：默认→包内→home→`DSH_BLENDER_CONFIG`→`DSH_BLENDER_PLUGIN_CONFIG` 合并，记录每项来源；`DSH_BLENDER_WORKDIR` 作为弃用别名；测试覆盖优先级、非法值、旧别名 warning。
- [ ] 1.3 把 queue/jobs/sessions/logs/cache 的默认根切到 home：改 `runtime/core/queue/store.ts`、`runtime/core/queue/spawn-executor.ts`、`runtime/execution/**` 的 `CFG.workDir*` 使用；旧 `dsh-blender-rt` 仅在目标缺失时复制 `queue.json`/状态 JSON，不删除旧目录；测试迁移与不覆盖已有数据。
- [ ] 1.4 在 `src/plugin.ts` 的 `Config` 增加 `features.dnn`（默认 `false`）、`cache.render.{maxEntries,maxBytes,ttlDays}`、`vision.python`（默认空）、`vision.venv`（默认空）、`vision.model`（默认 `yolov5n`）、`vision.dnn.backend`（默认 `opencv`）与 `vision.dnn.device`（默认 `cpu`），带默认值/范围/枚举；拒绝 `backend=opencv` 与非 `cpu` device 的组合，`backend=onnxruntime` 记录“需要自备 venv/ORT”但不自动安装；`apply()` 首次激活把合法值序列化到 `DSH_BLENDER_PLUGIN_CONFIG` 并传给 Gateway，后续 DNN 字段保存必须写 `runtime.json` 并调用 `dnn.apply`，首次激活不得覆盖已有快照；测试 schema 默认值/非法值/组合校验/不含密钥/首次不覆盖快照，`doctor` 报告 `features.dnn` 来源与实验性标记；不新增 `vision.detector`/`vision.autoInstall`。
- [ ] 1.5 更新 `docs/配置参考.md`（home、配置优先级含 `runtime.json` 与 env 锁定、`features.dnn` 实验性、`vision.python/venv/model`、`vision.dnn.backend/device`、`cache.render.*`、旧 workDir 迁移）、`docs/architecture.md`（home/config 层与热应用快照）、`docs/zh` 对应页；按文档命令逐条验证，并跑 `npm run check:doc-paths`。

## 2. Vision worker 与自动安装

- [ ] 2.1 先写失败测试，再新增 `runtime/vision/protocol.ts`、`manager.ts`：JSON-lines 分帧/8 MiB 上限/30 秒超时/空闲回收/崩溃隔离/最多 3 次每 5 分钟重启；用 fake worker 测试正常、超时、崩溃、协议错乱，跑 `npm run test:unit`。
- [ ] 2.2 先写失败测试，再新增 `runtime/vision/python-detect.ts` 与 `installer.ts`：3.13→3.12→3.11 发现顺序、`vision.python` 覆盖、`vision.venv` 非空时优先并跳过托管创建/pip（校验解释器、版本、`import cv2/numpy`，按 backend 校验 `onnxruntime`）、venv 临时目录+原子 rename、`opencv-python-headless==5.0.0.93`、lock/retry/state.json；用 fake python 脚本覆盖成功、无解释器、pip 失败、断点恢复、custom venv 无效不回退。
- [ ] 2.3 增加 python-build-standalone 托管解释器兜底：固定 release/URL/sha256、系统 `tar` 解压、校验 `import venv`、失败返回 `python_missing`；测试校验和错误不执行解压后的程序。
- [ ] 2.4 新增 `runtime/vision/worker/` Python 包与 `requirements.lock`：`main.py`、`protocol.py`、`selftest.py` 实现握手、`status`、`selftest`、优雅退出；`python -m pytest` 跑 worker 单元测试，并把该目录加入 pyright/ruff 扫描。
- [ ] 2.5 插件激活后后台触发安装，`vision_status` 可读 `not_installed/resolving_python/creating_venv/installing/verifying/ready/failed` 与 `env.source=managed|custom`；安装不阻塞其他工具，DNN 开关不影响 Python/OpenCV 安装；测试并发锁、失败日志路径、关闭 `features.dnn` 时安装仍可完成、自定义 venv 直接 ready 不写 home venv。
- [ ] 2.6 更新 `docs/操作教程.md`（首次安装、离线/代理、许可、`vision_status` 排障、自定义 venv 入口）与 `docs/配置参考.md` 的 `vision.python/venv`；按文档在临时 home 复现一次安装/失败，跑 `npm run check:python-static`。

## 3. 可选 DNN：feature flag、models.yml、预设下载与自备 backend

- [ ] 3.1 先写失败测试，再在 `runtime/config.ts` 增加 `features.dnn`：读取 `DSH_BLENDER_DNN=off/on` 与 `DSH_BLENDER_PLUGIN_CONFIG`，默认 `false`，暴露 `CFG.features.dnn` 与 source；在 `runtime/vision/dispatch.ts` 入口做门，关闭时返回 `stage=config`、`code=dnn_disabled`、`retryable=false`，不启动 worker；`doctor`/`vision_status` 报告生效值/来源与 `experimental=true`，默认 `backend=opencv`、`device=cpu`。测试默认 false、env 覆盖、关闭时 worker 未启动、默认不 import onnxruntime。
- [ ] 3.2 新增 `runtime/vision/models.ts` 与 worker `models.py`：用 `cv.FileStorage` 读 `vision/models/models.yml`，宿主递归扫描 `models/**/*.onnx`；校验必填字段/类型/`postprocessing: yolov5`/labels 行数/模型 id 正则/相对路径与 realpath；实现 `dnn_no_model`、`vision_model_unconfigured`、`vision_model_missing`、`vision_model_unsupported`。测试用户自定义 ONNX、缺条目、无 labels、路径逃逸、重复 key/YAML 解析失败。
- [ ] 3.3 新增 `src/host/dnn-pins.ts`（yolov5n/s/m/l/x 的 URL/字节数/sha256）与 `src/host/dnn-download.ts`：`/doctor` 的 `dnn` 块、owner-only `dnn` 端点的 `download` 操作、单并发、`.part`→sha256→原子 rename、写 `coco80.txt`、向 `models.yml` 追加条目不覆盖已有 key；用 stub 下载源测试校验失败清理、重复下载幂等、已有 key 不被覆盖。
- [ ] 3.4 改 `src/client/index.js` 配置卡：`features.dnn` 开关旁加“实验性”badge 与影响说明（YOLOv5/GPL-3.0、默认 CPU、结果可能不稳定、GPU 需自备环境）；增加指向 `https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md` 的链接和“自定义推理环境（DIY）”分组（`vision.venv`/`backend`/`device`）；启用且 models 目录无 `.onnx` 时弹复选框列表（`yolov5n` 默认勾选，n/s/m/l/x 可多选，显示大小/来源/GPL-3.0）；开关保存后立即检查 models 目录并弹窗，不等 Gateway 重启；确认调用下载 RPC，点“暂不下载”只关弹窗并保留警告与下载入口，开关仍为开；增加“保存并应用”“检测可用性”“取消等待”“回滚上一版”，显示 idle/pending/switching/failed、active/pending revision 与 waitingFor；pending 时不得显示已生效，env 锁定时禁用开关；保存并应用后自动跑 probe；下载完成后提示模型可用、无需重启；不新增客户端 bundle/HTTP 路由；测试或冒烟覆盖实验性文案、默认勾选、拒绝后开关状态、busy pending、probe 失败与取消等待。
- [ ] 3.5 更新 `docs/操作教程.md`（启用 DNN、models.yml 字段/示例、放自定义 ONNX、下载弹窗、拒绝后的状态、`runtime.json` 热应用与 probe、忙时 pending、默认 CPU/GPU DIY 边界）、`docs/配置参考.md`（`features.dnn`、`vision.model`、`vision.venv`、`vision.dnn.backend/device`、模型目录）、`docs/architecture.md`（host/worker/inference backend 分工与热应用状态机）、`CHANGELOG.md` 与 `docs/zh`；按文档用一个小 ONNX 验证用户扩展路径，并验证保存后无需重启。
- [ ] 3.6 先写失败测试，再新增 `runtime/vision/worker/inference/{opencv,onnxruntime}.py` 与宿主侧 backend/device 选择：默认 `opencv+cpu` 不 import ORT；`onnxruntime` 校验 custom venv/ORT/provider，实现 `vision_custom_env_invalid`、`dnn_backend_unavailable`、`dnn_device_unavailable`、`auto` 回退 reason 与显式 `gpu` 失败；`vision_status`/`vision_detect`/`vision_selftest` 回执带 backend/device/provider/availableProviders/docs；用 fake provider 覆盖成功、缺包、无 GPU、auto 回退与不回退。
- [ ] 3.7 新增中英双语 `docs/vision-custom-backend.md`（默认 CPU、Windows DirectML/NVIDIA、Linux NVIDIA/Intel、AMD MIGraphX 边界、自备 venv/安装/设置/验证/排障、插件不维护 GPU runtime）与 `runtime/vision/assets/GPU-DIY.md` 快捷版；在 home layout 缺失时复制到 `vision/models/GPU-DIY.md`、不覆盖用户文件；配置卡/`vision_status.dnn.docs` 使用固定 GitHub URL；测试文件创建/不覆盖/中英文小节存在。

- [ ] 3.8 先写失败测试，再新增 `runtime/vision/runtime-config.ts`：读取 `$DSH_HOME/dsh-blender/vision/runtime.json`（原子写、不含密钥）、校验 revision/updatedAt、实现 `runtime.json > DSH_BLENDER_PLUGIN_CONFIG > 文件 > 包内` 的 DNN 字段优先级与 `DSH_BLENDER_DNN` 锁定；快照损坏/非法返回 `dnn_config_invalid`，陈旧返回 `dnn_config_stale`；Gateway 在 `/doctor` 与每次 `vision_*` 边界 reconcile，原子写 `runtime-state.json`（active/pending/phase/waitingFor/lastSwitchError/previous/lastStartError）；测试无快照回退、env 锁定、损坏快照、revision 单调。
- [ ] 3.9 先写失败测试，再改 `runtime/vision/manager.ts`：维护 active/pending revision、在途请求集合与 phase；idle 立即切换，busy 登记 pending，pending 期间新 worker-backed 请求返回 `dnn_config_switching`，在途请求用旧 revision 完成并带 `configRevision`；归零后优雅关闭旧 worker（2 秒后杀进程树）再提交；仅 `features.dnn` 或 `vision.model`/models.yml 变化不重启 worker；关闭失败返回 `dnn_config_switch_failed` 并保持旧 active；feature off 不杀共享 worker；测试 idle/busy/关闭失败/模型-only/feature toggle/在途请求不中断。
- [ ] 3.10 先写失败测试，再新增 `src/host/dnn-config.ts`：owner-only `/dsh-blender` 的 `dnn` 端点，操作 `apply`/`status`/`cancel-pending`/`rollback`/`download`；apply 写 runtime.json 后调用 `/doctor` 强制 reconcile，返回 applied/revision/phase/waitingFor；Gateway 不可达返回 `gateway_unreachable` 与 `savedForNextStart`；env 锁定返回 `dnn_env_locked`；支持覆盖 pending 与回滚上一版；不新增 HTTP 路由（保持 29）；用 stub Gateway 测试 apply/pending/rollback/不可达。
- [ ] 3.11 先写失败测试，再新增 `src/host/dnn-probe.ts` 与 `runtime/vision/worker/probe.py`：候选配置经 JSON stdin/stdout，10 秒超时，不经 shell；检查解释器 3.11–3.13、cv2/numpy、backend import、ORT providers 与 device、worker 模块/协议、models 目录/.onnx/models.yml/vision.model/labels/路径逃逸；返回 checks/summary/elapsedMs；不写快照、不启动常驻 worker、不加载 ONNX、不联网/安装；测试缺 ORT、缺模型、超时、连续 probe 无副作用。
- [ ] 3.12 先写失败集成测试，覆盖配置卡保存→runtime.json→`/doctor` reconcile→下一次 detect 使用新配置、busy detect 时 pending/取消/自动提交、Gateway 不可达、自定义 venv 启动失败后的显式回滚、probe 后 runtime/worker/模型目录不变；在临时 home 用 fake worker/venv 跑一遍，确认全程无需重启 Gateway。

## 4. Vision op、pass 分析与 catalog

- [ ] 4.1 先写失败契约测试，再在 `runtime/catalog/data.ts` 增加 `vision` 家族 8 op（`vision_status/analyze/compare/detect/track/pass/selftest/help`，`requires_session:false`、`write_scope:"none"`）；同步 `config/contracts.json` host family 与 `src/tools/op/read-only.ts`；测试 catalog 36/239、8 op 全在 host 覆盖、缺声明时 parity 失败。
- [ ] 4.2 先写失败测试，再在 `runtime/core/plan-dispatch.ts` 增加 vision host 短路到 `runtime/vision/dispatch.ts`，确保无 Blender 时不返回 `stage=session`、addon 不收到 `execute_code`，`features.dnn` 关闭时返回 `dnn_disabled`、DNN 配置切换中返回 `dnn_config_switching`，都不是 dispatch 错误；跑 `tests/unit/js/contract/op-contract.test.ts` 与新增 dispatch 测试。
- [ ] 4.3 实现 `vision_analyze` / `vision_compare` / `vision_help`：颜色/清晰度/曝光/前景、ECC/ORB 配准、IoU/差异统计、有界返回；用合成图 golden 测试固定 bbox/数值容差，断言回执不含像素数组；`vision_help` 写清 models.yml 字段与模型 id 用法。
- [ ] 4.4 实现 `vision_detect`：host 侧只做 feature gate、DNN 配置切换门、custom env/backend/device 校验与“models 目录是否为空”的早失败，模型 id 校验和 models.yml 语义由 worker 负责；参数只接受模型 id；worker 按条目 width/height/mean/scale/rgb/labels 预处理，默认 `opencv+cpu` 用 `readNetFromONNX`，`onnxruntime` 用 `InferenceSession`，共用 letterbox/解码/NMS（YOLOv5 输出按 `(1,N,85)` 解码，不硬编码 25200），返回 class/可选 label/confidence/xyxy 与 backend/device/provider/configRevision；错误覆盖 `dnn_disabled`、`dnn_config_switching`、`dnn_config_switch_failed`、`vision_custom_env_invalid`、`dnn_backend_unavailable`、`dnn_device_unavailable`、`dnn_no_model`、`vision_model_unconfigured`、`vision_model_missing`、`vision_model_unsupported`。测试默认 `yolov5n` fixture、用户 ONNX、无 labels、非法 id/路径/URL、未配置、形状不匹配、worker 无网络、auto 回退/显式 GPU 失败。
- [ ] 4.5 实现 `vision_track` / `vision_selftest`：Farneback/LK 有界摘要、序列稳定性（闪烁/抖动/跳变）、可选参考序列 RMSE/max、合成自检；DNN 检查在 `features.dnn=false` 或无可用模型时标为 skipped，不算失败；配置切换 pending 时返回 `dnn_config_switching`，不启动新 worker；启用时按实际 backend/device/provider 自检，backend/device 不可用显式上报；测试空帧/单帧/超 120 帧、方法非法、参考序列长度不一致、自检失败/默认 CPU/自定义 backend。
- [ ] 4.6 更新 catalog/behavior matrix/计数锁：跑 `node scripts/sync_counts.mjs`、更新 `tests/fixtures/capability.lock.json`、`tests/BEHAVIOR_MATRIX.md`；跑 `npm run test:matrix` 与 `npm run test:contract`。
- [ ] 4.7 更新 `docs/操作教程.md`（8 个 op 的参数/单位/限制、`features.dnn` 与模型 id 的调用示例）、`docs/architecture.md`（host family/worker 边界）与 `docs/zh`；按文档示例调用 op 并核对返回字段。
- [ ] 4.8 先写失败测试，再实现 `vision_pass`：读 1–240 个 manifest，按 manifest 解码 depth 为米、normal 为相机空间向量、object_index 为对象名；返回逐帧/聚合统计、对象可见覆盖/bbox/深度顺序；实现 `vision_pass_manifest_invalid`、`vision_pass_unsupported`、64 对象/240 manifest 上限与截断 warning；golden 测试断言不返回原始数组、bbox 重叠不冒充精确遮挡。
- [ ] 4.9 更新 `docs/操作教程.md`（`vision_pass` 参数、manifest 字段、depth 解码、对象覆盖口径）、`docs/architecture.md`（pass 分析流）与 `docs/zh`；用真实 pass manifest 跑一次并核对数值。

## 5. Render passes

- [ ] 5.1 先写失败测试，再在 `src/tools/run/execute.ts` 的 `RENDER_KEYS` 增加 `passes`：允许集合、非空、去重、未知值拒绝，未传时保持旧行为；测试提交前拒绝、参数原样传到 Gateway。
- [ ] 5.2 先写失败测试，再改 `runtime/core/queue/spawn-executor.ts`：有 `passes` 时写固定 `render_passes.py` + `render-config.json`，Blender 以 `-b --factory-startup <file> --python ...` 启动；测试生成的命令/文件不含调用方脚本、无 passes 时命令不变。
- [ ] 5.3 实现 pass 渲染脚本的 beauty/depth/normal/alpha：view layer pass、compositor File Output、depth 16-bit PNG + near/far、normal 相机空间编码、alpha；Blender 4.2/4.5/5.2 E2E 跑 1–2 帧，校验文件存在、dtype/值域/manifest。
- [ ] 5.4 实现 object_index：稳定 `pass_index` 分配、IndexOB 16-bit PNG、index→name 映射；E2E 校验背景 0、名字映射与实际对象一致、回执不含索引数组。
- [ ] 5.5 扩展 `blender_run` submit/status/collect 回执：只含 manifest 路径/pass 元数据/大小/摘要，不带 raw/base64 像素；测试 collect 与失败帧 `degraded`。
- [ ] 5.6 更新 `docs/操作教程.md`（passes 参数、manifest 字段、坐标系/depth 解码、manifest 交给 `vision_pass`）、`docs/architecture.md`（queue pass 流程）、`CHANGELOG.md` 与 `docs/zh`；按文档示例跑一次真实 render 并核对 manifest。

## 6. Render pass 缓存

- [ ] 6.1 先写失败测试，再新增 `runtime/core/queue/render-cache.ts`：key 含 scene path/size/mtime/frame/camera/resolution/samples/engine/passSet/manifestVersion，canonical JSON + sha256；测试同参数同 key、mtime/size 变化/未保存场景 miss。
- [ ] 6.2 先写失败测试，再接入 queue 提交路径：命中创建 `completed/cache_hit=true` job、artifacts 指向缓存、不占 GPU slot、不 spawn Blender；测试命中不调用 executor、状态与 artifacts 正确。
- [ ] 6.3 实现成功后的 staging→原子提升：manifest 完整才提升，单条 >2 GiB 跳过并 `cache_skipped=oversize`，用户 `outdir` 得到副本；测试提升、超限、用户目录复制、失败/取消不写索引。
- [ ] 6.4 实现索引/清理/损坏处理：临时文件+rename、启动时 TTL+LRU、每次写入后淘汰、缺失/损坏 entry 删除并回退 miss；测试 8 条上限、TTL 过期、maxEntries 改 4 后重启淘汰、损坏缓存重渲染。
- [ ] 6.5 更新 `docs/配置参考.md`（`cache.render.*` 默认/范围/生效时机）、`docs/architecture.md`（缓存布局/失效）、`docs/zh`；测试把配置改成非法值，`doctor` 报字段/范围且不崩溃。

## 7. 3D 动作分析（animation）

- [ ] 7.1 先写失败测试，再新增 `runtime/py/animation/` Python 领域（`report_impl.py`、`common.py`、`selftest_impl.py`、`help_impl.py`），登记 `runtime/py/modules.json` 的 domains/modules；在 `config/routing.json` 增加 `animation` GUI 路由（`dsh_animation_api`、`ensureAnimation`、`preload animation`）并跑 `scripts/gen-routes.mjs`；在 `pyproject.toml` 的 import-linter layers 加入 `animation`；catalog 加 `animation` 家族 3 op（`requires_session:true`、`write_scope:"exclusive"`）。新增 `src/tools/op/session-read.ts` 与 `runtime/catalog/data.ts` 的 `PLAN_SESSION_READ_OPS`，改 `src/tools/op/execute.ts`、`runtime/http/routes/plan.ts`、`runtime/http/lease.ts`、`runtime/http/session-gate.ts`：`animation_report`/`animation_help` 走 session-read（要求会话、不取写租约），`animation_selftest` 走写门，`READ_ONLY_OPS` 仍只含 8 个 vision op，`PLAN_READ_ONLY_OPS` 保持 98；测试 route 生成、无 write grant 的 report 可读、无 write grant 的 selftest 被拒、host 不截获、import-linter exhaustive 通过、catalog 家族 3 op、read-only 名单不含 animation。
- [ ] 7.2 先写失败测试，再实现 `animation_report` 的采样、root motion、关节指标：场景帧范围/显式 start/end/step、闭区间、默认最小整数步长 ≤240、显式 step 超 240 返回 `animation_sampling_invalid`、`effective_fps=fps/fps_base`、请求/实际范围与最后采样帧；按帧 `frame_set` 后从 evaluated depsgraph 取世界矩阵（不读 `Action.fcurves`）、`root_bone`、局部关节公式、`bone_filter`/`bone_collections`/inventory、方向位移/rotation_path/样本不足 null/旋转超 180° warning；测试合成平移物体、旋转骨架、父骨转/子骨不转、IK、23.976 fps、全部 `use_deform` 相同的 warning、bones/bone_chains/root_bone/bone_collections 缺 armature、非法 `bone_filter`/`contact_point`、frame_start>frame_end、frame_step<=0、contact_epsilon<=0、ground_z 非有限值，断言单位和边界。
- [ ] 7.3 实现接触/脚滑、采样穿插与可选参考轨迹：`contact_bones` + `contact_point`（head/tail/center）+ `ground_z`/`contact_epsilon` 的有符号接触/穿透、`contact_samples` 与区间滑动；evaluated mesh 世界空间 BVH 表面重叠（每采样帧 20 万三角形预算、最多 64 对象对、skipped/degraded）；`reference` 严格校验 version/fps（容差 1e-6）/唯一整数帧/有限坐标/精确帧匹配，返回 RMSE/max/matched/missing/unused；测试空接触、穿透、超预算 degraded、非法参考、fps 不匹配、重复帧/NaN、对象不存在、平移 armature、intersection_objects 少于 2 个、未知骨名。
- [ ] 7.4 实现 `animation_selftest` / `animation_help` 与非持久保证：selftest 在临时 collection 建关键帧物体/骨架并在 finally 清理；report 保存/恢复 `frame_current` 与 `frame_subframe`，不改关键帧/pose/不保存 .blend；help 只写通用字段与选择方法，不列 Rigify 示例；测试场景对象数不变、正常/异常都恢复帧与 subframe、help 字段完整、无 control/mechanism/DEF 角色断言。
- [ ] 7.5 更新 `docs/操作教程.md`（animation 参数、单位、`effective_fps`、session-read、`bone_filter`/inventory、预算与近似口径，不写 Rigify 示例）、`docs/architecture.md`（animation 领域、GUI 路由、session-read 门）、`CHANGELOG.md`、behavior matrix、catalog lock 与 `docs/zh`；跑 `npm run test:matrix`、`npm run test:contract`；真实 Blender 4.2/4.5/5.2 E2E 跑报告，覆盖异常帧恢复、无 write grant 读取与 selftest 写门，最终计数 36 family / 239 op。

## 8. 集成验收与发布准备

- [ ] 8.1 跑全量门禁：`npm run typecheck`、`test:unit`、`test:integration`、`test:contract`、`test:python`、`check:python-static`、`check:python-imports`、`check:structure`、`check:finalization`、`check:addon`、`check:build`、`lint`、`format:check`；确认 6 工具/29 路由/25 注册命令/protocol 12 未变，catalog 36/239。
- [ ] 8.2 干净构建后跑 `npm pack --dry-run` 与 `npm run smoke:install`：确认包内不含 `vision/venv`、模型、缓存、home 数据、`onnxruntime` 或任何 GPU runtime；在 Windows 与 Linux CI 各跑一次安装/视觉自检（真实 OpenCV 可夜间任务）。
- [ ] 8.3 真实 Blender E2E：4.2/4.5/5.2 各跑 passes（depth/normal/object_index/alpha）与缓存二次提交；记录 Blender 版本/命令/产物摘要；用同一批 manifest 跑 `vision_pass`。
- [ ] 8.4 安全与边界复核：图片/pass manifest/reference 路径只读、像素与帧数上限、无 URL/脚本/任意模型路径、worker 无监听端口且不发起网络请求、模型 id 与 models.yml 路径逃逸被拒、模型下载仅由人类确认且只用固定 URL+sha256、默认不 import/安装 onnxruntime、自定义 venv 只读且插件不 pip/不改驱动、venv 不写系统 Python、animation session-read 报告不取写租约且 report/help 不进 read-only 名单、`frame_current`/`frame_subframe` 恢复、selftest 清理、无 control/mechanism/DEF 角色断言；另核对 runtime.json 仅宿主单写、原子替换、无密钥，pending 不杀在途 worker，probe 不写 runtime/模型、不联网/安装/加载 ONNX，Gateway 不可达不声称已生效；把检查结果写入 change 的验证记录。
- [ ] 8.5 更新 `CHANGELOG.md`、`tests/BEHAVIOR_MATRIX.md`、`docs/architecture.md` 的最终计数与能力表、`docs/zh` 同步；核对 animation 文档的 session-read、`effective_fps`、`bone_filter`/inventory、世界空间 BVH 与“不列 Rigify 示例”，以及 `docs/vision-custom-backend.md` 中英双语、`GPU-DIY.md` 模型目录副本、配置卡 GitHub 链接、`runtime.json`/`runtime-state.json` 与热应用/probe 文档；跑 `npm run check:doc-paths` 与 `npm run test:matrix`。

## Workflow follow-up

- 评审通过后提交 PR（refactor/feature 模板 + AI 披露），合并到 main。
- 触发 next minor 发布流程，确认 GitHub Release 与 npm 包一致。
- 发布后归档本 change，并把 Python 与 YOLOv5 预设的锁定值、models.yml 示例、安装失败样本、animation 指标 golden 与 E2E 记录保留在仓库内验证记录中。
