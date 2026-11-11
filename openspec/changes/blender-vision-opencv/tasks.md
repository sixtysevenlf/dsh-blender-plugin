# Tasks

## 1. Home、配置与 DSH 设置

- [ ] 1.1 先写失败测试，再新增 `runtime/home.ts`：`DSH_BLENDER_HOME` → `$DSH_HOME/dsh-blender` → `~/.dsh/dsh-blender` 解析、`ensureHomeLayout()` 幂等创建、`home_unwritable` 失败路径；用临时目录测试解析来源与只读目录错误，跑 `npm run test:unit`。
- [ ] 1.2 先写失败测试，再改 `runtime/config.ts` 为 home 优先的同步配置层：默认→包内→home→`DSH_BLENDER_CONFIG`→`DSH_BLENDER_PLUGIN_CONFIG` 合并，记录每项来源；`DSH_BLENDER_WORKDIR` 作为弃用别名；测试覆盖优先级、非法值、旧别名 warning。
- [ ] 1.3 把 queue/jobs/sessions/logs/cache 的默认根切到 home：改 `runtime/core/queue/store.ts`、`runtime/core/queue/spawn-executor.ts`、`runtime/execution/**` 的 `CFG.workDir*` 使用；旧 `dsh-blender-rt` 仅在目标缺失时复制 `queue.json`/状态 JSON，不删除旧目录；测试迁移与不覆盖已有数据。
- [ ] 1.4 在 `src/plugin.ts` 的 `Config` 增加 `features.dnn`（默认 `false`）、`cache.render.{maxEntries,maxBytes,ttlDays}`、`vision.python`（默认空）与 `vision.model`（默认 `yolov5n`），带默认值/范围/格式；`apply()` 把合法值序列化到 `DSH_BLENDER_PLUGIN_CONFIG` 并传给 Gateway；测试 schema 默认值/非法值/不含密钥，`doctor` 报告 `features.dnn` 来源；不新增 `vision.detector`/`vision.autoInstall`。
- [ ] 1.5 更新 `docs/配置参考.md`（home、配置优先级、`features.dnn`、`vision.python/model`、`cache.render.*`、旧 workDir 迁移）、`docs/architecture.md`（home/config 层）、`docs/zh` 对应页；按文档命令逐条验证，并跑 `npm run check:doc-paths`。

## 2. Vision worker 与自动安装

- [ ] 2.1 先写失败测试，再新增 `runtime/vision/protocol.ts`、`manager.ts`：JSON-lines 分帧/8 MiB 上限/30 秒超时/空闲回收/崩溃隔离/最多 3 次每 5 分钟重启；用 fake worker 测试正常、超时、崩溃、协议错乱，跑 `npm run test:unit`。
- [ ] 2.2 先写失败测试，再新增 `runtime/vision/python-detect.ts` 与 `installer.ts`：3.13→3.12→3.11 发现顺序、`vision.python` 覆盖、venv 临时目录+原子 rename、`opencv-python-headless==5.0.0.93`、lock/retry/state.json；用 fake python 脚本覆盖成功、无解释器、pip 失败、断点恢复。
- [ ] 2.3 增加 python-build-standalone 托管解释器兜底：固定 release/URL/sha256、系统 `tar` 解压、校验 `import venv`、失败返回 `python_missing`；测试校验和错误不执行解压后的程序。
- [ ] 2.4 新增 `runtime/vision/worker/` Python 包与 `requirements.lock`：`main.py`、`protocol.py`、`selftest.py` 实现握手、`status`、`selftest`、优雅退出；`python -m pytest` 跑 worker 单元测试，并把该目录加入 pyright/ruff 扫描。
- [ ] 2.5 插件激活后后台触发安装，`vision_status` 可读 `not_installed/resolving_python/creating_venv/installing/verifying/ready/failed`；安装不阻塞其他工具，DNN 开关不影响 Python/OpenCV 安装；测试并发锁、失败日志路径、关闭 `features.dnn` 时安装仍可完成。
- [ ] 2.6 更新 `docs/操作教程.md`（首次安装、离线/代理、许可、`vision_status` 排障）与 `docs/配置参考.md` 的 `vision.python`；按文档在临时 home 复现一次安装/失败，跑 `npm run check:python-static`。

## 3. 可选 DNN：feature flag、models.yml 与预设下载

- [ ] 3.1 先写失败测试，再在 `runtime/config.ts` 增加 `features.dnn`：读取 `DSH_BLENDER_DNN=off/on` 与 `DSH_BLENDER_PLUGIN_CONFIG`，默认 `false`，暴露 `CFG.features.dnn` 与 source；在 `runtime/vision/dispatch.ts` 入口做门，关闭时返回 `stage=config`、`code=dnn_disabled`、`retryable=false`，不启动 worker；`doctor`/`vision_status` 报告生效值与来源。测试默认 false、env 覆盖、关闭时 worker 未启动。
- [ ] 3.2 新增 `runtime/vision/models.ts` 与 worker `models.py`：用 `cv.FileStorage` 读 `vision/models/models.yml`，宿主递归扫描 `models/**/*.onnx`；校验必填字段/类型/`postprocessing: yolov5`/labels 行数/模型 id 正则/相对路径与 realpath；实现 `dnn_no_model`、`vision_model_unconfigured`、`vision_model_missing`、`vision_model_unsupported`。测试用户自定义 ONNX、缺条目、无 labels、路径逃逸、重复 key/YAML 解析失败。
- [ ] 3.3 新增 `src/host/dnn-pins.ts`（yolov5n/s/m/l/x 的 URL/字节数/sha256）与 `src/host/dnn-download.ts`：`/doctor` 的 `dnn` 块、owner-only `downloadDnnModels` RPC、单并发、`.part`→sha256→原子 rename、写 `coco80.txt`、向 `models.yml` 追加条目不覆盖已有 key；用 stub 下载源测试校验失败清理、重复下载幂等、已有 key 不被覆盖。
- [ ] 3.4 改 `src/client/index.js` 配置卡：`features.dnn` 开关；启用且 models 目录无 `.onnx` 时弹复选框列表（`yolov5n` 默认勾选，n/s/m/l/x 可多选，显示大小/来源/GPL-3.0）；开关保存后立即检查 models 目录并弹窗，不等 Gateway 重启；确认调用下载 RPC，点“暂不下载”只关弹窗并保留警告与下载入口，开关仍为开；不新增客户端 bundle/HTTP 路由；测试或冒烟覆盖默认勾选与拒绝后开关状态。
- [ ] 3.5 更新 `docs/操作教程.md`（启用 DNN、models.yml 字段/示例、放自定义 ONNX、下载弹窗、拒绝后的状态）、`docs/配置参考.md`（`features.dnn`、`vision.model`、模型目录）、`docs/architecture.md`（host/worker 分工）、`CHANGELOG.md` 与 `docs/zh`；按文档用一个小 ONNX 验证用户扩展路径。

## 4. Vision op、catalog 与 host 分派

- [ ] 4.1 先写失败契约测试，再在 `runtime/catalog/data.ts` 增加 `vision` 家族 7 op（`vision_status/analyze/compare/detect/track/selftest/help`，`requires_session:false`、`write_scope:"none"`）；同步 `config/contracts.json` host family 与 `src/tools/op/read-only.ts`；测试 catalog 35/235、7 op 全在 host 覆盖、缺声明时 parity 失败。
- [ ] 4.2 先写失败测试，再在 `runtime/core/plan-dispatch.ts` 增加 vision host 短路到 `runtime/vision/dispatch.ts`，确保无 Blender 时不返回 `stage=session`、addon 不收到 `execute_code`，`features.dnn` 关闭时返回 `dnn_disabled` 而不是 dispatch 错误；跑 `tests/unit/js/contract/op-contract.test.ts` 与新增 dispatch 测试。
- [ ] 4.3 实现 `vision_analyze` / `vision_compare` / `vision_help`：颜色/清晰度/曝光/前景、ECC/ORB 配准、IoU/差异统计、有界返回；用合成图 golden 测试固定 bbox/数值容差，断言回执不含像素数组；`vision_help` 写清 models.yml 字段与模型 id 用法。
- [ ] 4.4 实现 `vision_detect`：host 侧只做 feature gate 与“models 目录是否为空”的早失败，模型 id 校验和 models.yml 语义由 worker 负责；参数只接受模型 id；worker 按条目 width/height/mean/scale/rgb/labels 预处理，`readNetFromONNX` + letterbox/解码/NMS（YOLOv5 输出按 `(1,N,85)` 解码，不硬编码 25200），返回 class/可选 label/confidence/xyxy；错误覆盖 `dnn_disabled`、`dnn_no_model`、`vision_model_unconfigured`、`vision_model_missing`、`vision_model_unsupported`。测试默认 `yolov5n` fixture、用户 ONNX、无 labels、非法 id/路径/URL、未配置、形状不匹配、worker 无网络。
- [ ] 4.5 实现 `vision_track` / `vision_selftest`：Farneback/LK 有界摘要、合成自检；DNN 检查在 `features.dnn=false` 或无可用模型时标为 skipped，不算失败；测试空帧/单帧/超 120 帧、方法非法、自检失败上报。
- [ ] 4.6 更新 catalog/behavior matrix/计数锁：跑 `node scripts/sync_counts.mjs`、更新 `tests/fixtures/capability.lock.json`、`tests/BEHAVIOR_MATRIX.md`；跑 `npm run test:matrix` 与 `npm run test:contract`。
- [ ] 4.7 更新 `docs/操作教程.md`（7 个 op 的参数/单位/限制、`features.dnn` 与模型 id 的调用示例）、`docs/architecture.md`（host family/worker 边界）与 `docs/zh`；按文档示例调用 op 并核对返回字段。

## 5. Render passes

- [ ] 5.1 先写失败测试，再在 `src/tools/run/execute.ts` 的 `RENDER_KEYS` 增加 `passes`：允许集合、非空、去重、未知值拒绝，未传时保持旧行为；测试提交前拒绝、参数原样传到 Gateway。
- [ ] 5.2 先写失败测试，再改 `runtime/core/queue/spawn-executor.ts`：有 `passes` 时写固定 `render_passes.py` + `render-config.json`，Blender 以 `-b --factory-startup <file> --python ...` 启动；测试生成的命令/文件不含调用方脚本、无 passes 时命令不变。
- [ ] 5.3 实现 pass 渲染脚本的 beauty/depth/normal/alpha：view layer pass、compositor File Output、depth 16-bit PNG + near/far、normal 相机空间编码、alpha；Blender 4.2/4.5/5.2 E2E 跑 1–2 帧，校验文件存在、dtype/值域/manifest。
- [ ] 5.4 实现 object_index：稳定 `pass_index` 分配、IndexOB 16-bit PNG、index→name 映射；E2E 校验背景 0、名字映射与实际对象一致、回执不含索引数组。
- [ ] 5.5 扩展 `blender_run` submit/status/collect 回执：只含 manifest 路径/pass 元数据/大小/摘要，不带 raw/base64 像素；测试 collect 与失败帧 `degraded`。
- [ ] 5.6 更新 `docs/操作教程.md`（passes 参数、manifest 字段、坐标系/depth 解码）、`docs/architecture.md`（queue pass 流程）、`CHANGELOG.md` 与 `docs/zh`；按文档示例跑一次真实 render 并核对 manifest。

## 6. Render pass 缓存

- [ ] 6.1 先写失败测试，再新增 `runtime/core/queue/render-cache.ts`：key 含 scene path/size/mtime/frame/camera/resolution/samples/engine/passSet/manifestVersion，canonical JSON + sha256；测试同参数同 key、mtime/size 变化/未保存场景 miss。
- [ ] 6.2 先写失败测试，再接入 queue 提交路径：命中创建 `completed/cache_hit=true` job、artifacts 指向缓存、不占 GPU slot、不 spawn Blender；测试命中不调用 executor、状态与 artifacts 正确。
- [ ] 6.3 实现成功后的 staging→原子提升：manifest 完整才提升，单条 >2 GiB 跳过并 `cache_skipped=oversize`，用户 `outdir` 得到副本；测试提升、超限、用户目录复制、失败/取消不写索引。
- [ ] 6.4 实现索引/清理/损坏处理：临时文件+rename、启动时 TTL+LRU、每次写入后淘汰、缺失/损坏 entry 删除并回退 miss；测试 8 条上限、TTL 过期、maxEntries 改 4 后重启淘汰、损坏缓存重渲染。
- [ ] 6.5 更新 `docs/配置参考.md`（`cache.render.*` 默认/范围/生效时机）、`docs/architecture.md`（缓存布局/失效）、`docs/zh`；测试把配置改成非法值，`doctor` 报字段/范围且不崩溃。

## 7. 集成验收与发布准备

- [ ] 7.1 跑全量门禁：`npm run typecheck`、`test:unit`、`test:integration`、`test:contract`、`test:python`、`check:structure`、`check:finalization`、`check:addon`、`check:build`、`lint`、`format:check`；确认 6 工具/29 路由/25 注册命令/protocol 12 未变，catalog 35/235。
- [ ] 7.2 干净构建后跑 `npm pack --dry-run` 与 `npm run smoke:install`：确认包内不含 `vision/venv`、模型、缓存、home 数据；在 Windows 与 Linux CI 各跑一次安装/视觉自检（真实 OpenCV 可夜间任务）。
- [ ] 7.3 真实 Blender E2E：4.2/4.5/5.2 各跑 passes（depth/normal/object_index/alpha）与缓存二次提交；记录 Blender 版本/命令/产物摘要。
- [ ] 7.4 安全与边界复核：图片路径/像素上限、无 URL/脚本/任意模型路径、worker 无监听端口且不发起网络请求、模型 id 与 models.yml 路径逃逸被拒、模型下载仅由人类确认且只用固定 URL+sha256、venv 不写系统 Python；把检查结果写入 change 的验证记录。
- [ ] 7.5 更新 `CHANGELOG.md`、`tests/BEHAVIOR_MATRIX.md`、`docs/architecture.md` 的最终计数与能力表，`docs/zh` 同步；跑 `npm run check:doc-paths` 与 `npm run test:matrix`。

## Workflow follow-up

- 评审通过后提交 PR（refactor/feature 模板 + AI 披露），合并到 main。
- 触发 next minor 发布流程，确认 GitHub Release 与 npm 包一致。
- 发布后归档本 change，并把 Python 与 YOLOv5 预设的锁定值、models.yml 示例、安装失败样本与 E2E 记录保留在仓库内验证记录中。
