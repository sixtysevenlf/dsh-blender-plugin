# Tasks

## 1. Home、配置与 DSH 设置

- [ ] 1.1 先写失败测试，再新增 `runtime/home.ts`：`DSH_BLENDER_HOME` → `$DSH_HOME/dsh-blender` → `~/.dsh/dsh-blender` 解析、`ensureHomeLayout()` 幂等创建、`home_unwritable` 失败路径；用临时目录测试解析来源与只读目录错误，跑 `npm run test:unit`。
- [ ] 1.2 先写失败测试，再改 `runtime/config.ts` 为 home 优先的同步配置层：默认→包内→home→`DSH_BLENDER_CONFIG`→`DSH_BLENDER_PLUGIN_CONFIG` 合并，记录每项来源；`DSH_BLENDER_WORKDIR` 作为弃用别名；测试覆盖优先级、非法值、旧别名 warning。
- [ ] 1.3 把 queue/jobs/sessions/logs/cache 的默认根切到 home：改 `runtime/core/queue/store.ts`、`runtime/core/queue/spawn-executor.ts`、`runtime/execution/**` 的 `CFG.workDir*` 使用；旧 `dsh-blender-rt` 仅在目标缺失时复制 `queue.json`/状态 JSON，不删除旧目录；测试迁移与不覆盖已有数据。
- [ ] 1.4 在 `src/plugin.ts` 的 `Config` 增加 `cache.render.{maxEntries,maxBytes,ttlDays}` 与 `vision.{python,detector,model,autoInstall}`，带默认值和范围；`apply()` 把合法值序列化到 `DSH_BLENDER_PLUGIN_CONFIG` 并传给 Gateway；测试 schema 默认值/非法值/不含密钥，`doctor` 报告来源。
- [ ] 1.5 更新 `docs/配置参考.md`（home、配置优先级、新字段、旧 workDir 迁移）、`docs/architecture.md`（home/config 层）、`docs/zh` 对应页；按文档命令逐条验证，并跑 `npm run check:doc-paths`。

## 2. Vision worker 与自动安装

- [ ] 2.1 先写失败测试，再新增 `runtime/vision/protocol.ts`、`manager.ts`：JSON-lines 分帧/8 MiB 上限/30 秒超时/空闲回收/崩溃隔离/最多 3 次每 5 分钟重启；用 fake worker 测试正常、超时、崩溃、协议错乱，跑 `npm run test:unit`。
- [ ] 2.2 先写失败测试，再新增 `runtime/vision/python-detect.ts` 与 `installer.ts`：3.13→3.12→3.11 发现顺序、`vision.python` 覆盖、venv 临时目录+原子 rename、`opencv-python-headless==5.0.0.93`、lock/retry/state.json；用 fake python 脚本覆盖成功、无解释器、pip 失败、断点恢复。
- [ ] 2.3 增加 python-build-standalone 托管解释器兜底：固定 release/URL/sha256、系统 `tar` 解压、校验 `import venv`、失败返回 `python_missing`；测试校验和错误不执行解压后的程序。
- [ ] 2.4 新增 `runtime/vision/worker/` Python 包（`main.py`、`protocol.py`、`selftest.py`）与 `requirements.lock`：握手、`status`、`selftest`、优雅退出；`python -m pytest` 跑 worker 单元测试，并把该目录加入 pyright/ruff 扫描。
- [ ] 2.5 插件激活后后台触发安装，`vision_status` 可读 `not_installed/resolving_python/creating_venv/installing/verifying/ready/failed`；安装不阻塞其他工具；测试并发锁、失败日志路径、`autoInstall=false` 不自动下载。
- [ ] 2.6 更新 `docs/操作教程.md`（首次安装、离线/代理、许可、`vision_status` 排障）与 `docs/配置参考.md` 的 `vision.*`；按文档在临时 home 复现一次安装/失败，跑 `npm run check:python-static`。

## 3. Vision op、catalog 与 host 分派

- [ ] 3.1 先写失败契约测试，再在 `runtime/catalog/data.ts` 增加 `vision` 家族 7 op（`vision_status/analyze/compare/detect/track/selftest/help`，`requires_session:false`、`write_scope:"none"`）；同步 `config/contracts.json` host family 与 `src/tools/op/read-only.ts`；测试 catalog 35/235、7 op 全在 host 覆盖、缺声明时 parity 失败。
- [ ] 3.2 先写失败测试，再在 `runtime/core/plan-dispatch.ts` 增加 vision host 短路到 `runtime/vision/dispatch.ts`，确保无 Blender 时不返回 `stage=session`、addon 不收到 `execute_code`；跑 `tests/unit/js/contract/op-contract.test.ts` 与新增 dispatch 测试。
- [ ] 3.3 实现 `vision_analyze` / `vision_compare` / `vision_help`：颜色/清晰度/曝光/前景、ECC/ORB 配准、IoU/差异统计、有界返回；用合成图 golden 测试固定 bbox/数值容差，断言回执不含像素数组。
- [ ] 3.4 实现 `vision_detect`：YOLOv5 v7.0 n/s/m/l/x 模型表（文件名/字节数/sha256/COCO 类名）、`ensure_model` 下载与原子落盘、letterbox/解码/NMS；测试默认 `yolov5n`、非法模型拒绝、`autoInstall=false` 缺模型失败、离线预置哈希通过。
- [ ] 3.5 实现 `vision_track` / `vision_selftest`：Farneback/LK 有界摘要、合成自检；测试空帧/单帧/超 120 帧、方法非法、自检失败上报。
- [ ] 3.6 更新 catalog/behavior matrix/计数锁：跑 `node scripts/sync_counts.mjs`、更新 `tests/fixtures/capability.lock.json`、`tests/BEHAVIOR_MATRIX.md`；跑 `npm run test:matrix` 与 `npm run test:contract`。
- [ ] 3.7 更新 `docs/操作教程.md`（7 个 op 的参数/单位/限制）、`docs/配置参考.md`（检测器/模型）、`docs/architecture.md`（host family/worker 边界）与 `docs/zh`；按文档示例调用 op 并核对返回字段。

## 4. Render passes

- [ ] 4.1 先写失败测试，再在 `src/tools/run/execute.ts` 的 `RENDER_KEYS` 增加 `passes`：允许集合、非空、去重、未知值拒绝，未传时保持旧行为；测试提交前拒绝、参数原样传到 Gateway。
- [ ] 4.2 先写失败测试，再改 `runtime/core/queue/spawn-executor.ts`：有 `passes` 时写固定 `render_passes.py` + `render-config.json`，Blender 以 `-b --factory-startup <file> --python ...` 启动；测试生成的命令/文件不含调用方脚本、无 passes 时命令不变。
- [ ] 4.3 实现 pass 渲染脚本的 beauty/depth/normal/alpha：view layer pass、compositor File Output、depth 16-bit PNG + near/far、normal 相机空间编码、alpha；Blender 4.2/4.5/5.2 E2E 跑 1–2 帧，校验文件存在、dtype/值域/manifest。
- [ ] 4.4 实现 object_index：稳定 `pass_index` 分配、IndexOB 16-bit PNG、index→name 映射；E2E 校验背景 0、名字映射与实际对象一致、回执不含索引数组。
- [ ] 4.5 扩展 `blender_run` submit/status/collect 回执：只含 manifest 路径/pass 元数据/大小/摘要，不带 raw/base64 像素；测试 collect 与失败帧 `degraded`。
- [ ] 4.6 更新 `docs/操作教程.md`（passes 参数、manifest 字段、坐标系/depth 解码）、`docs/architecture.md`（queue pass 流程）、`CHANGELOG.md` 与 `docs/zh`；按文档示例跑一次真实 render 并核对 manifest。

## 5. Render pass 缓存

- [ ] 5.1 先写失败测试，再新增 `runtime/core/queue/render-cache.ts`：key 含 scene path/size/mtime/frame/camera/resolution/samples/engine/passSet/manifestVersion，canonical JSON + sha256；测试同参数同 key、mtime/size 变化/未保存场景 miss。
- [ ] 5.2 先写失败测试，再接入 queue 提交路径：命中创建 `completed/cache_hit=true` job、artifacts 指向缓存、不占 GPU slot、不 spawn Blender；测试命中不调用 executor、状态与 artifacts 正确。
- [ ] 5.3 实现成功后的 staging→原子提升：manifest 完整才提升，单条 >2 GiB 跳过并 `cache_skipped=oversize`，用户 `outdir` 得到副本；测试提升、超限、用户目录复制、失败/取消不写索引。
- [ ] 5.4 实现索引/清理/损坏处理：临时文件+rename、启动时 TTL+LRU、每次写入后淘汰、缺失/损坏 entry 删除并回退 miss；测试 8 条上限、TTL 过期、maxEntries 改 4 后重启淘汰、损坏缓存重渲染。
- [ ] 5.5 更新 `docs/配置参考.md`（`cache.render.*` 默认/范围/生效时机）、`docs/architecture.md`（缓存布局/失效）、`docs/zh`；测试把配置改成非法值，`doctor` 报字段/范围且不崩溃。

## 6. 集成验收与发布准备

- [ ] 6.1 跑全量门禁：`npm run typecheck`、`test:unit`、`test:integration`、`test:contract`、`test:python`、`check:structure`、`check:finalization`、`check:addon`、`check:build`、`lint`、`format:check`；确认 6 工具/29 路由/25 注册命令/protocol 12 未变，catalog 35/235。
- [ ] 6.2 干净构建后跑 `npm pack --dry-run` 与 `npm run smoke:install`：确认包内不含 `vision/venv`、模型、缓存、home 数据；在 Windows 与 Linux CI 各跑一次安装/视觉自检（真实 OpenCV 可夜间任务）。
- [ ] 6.3 真实 Blender E2E：4.2/4.5/5.2 各跑 passes（depth/normal/object_index/alpha）与缓存二次提交；记录 Blender 版本/命令/产物摘要。
- [ ] 6.4 安全与边界复核：图片路径/像素上限、无 URL/脚本/任意模型、worker 无监听端口、模型下载仅固定 URL+sha256、venv 不写系统 Python；把检查结果写入 change 的验证记录。
- [ ] 6.5 更新 `CHANGELOG.md`、`tests/BEHAVIOR_MATRIX.md`、`docs/architecture.md` 的最终计数与能力表，`docs/zh` 同步；跑 `npm run check:doc-paths` 与 `npm run test:matrix`。

## Workflow follow-up

- 评审通过后提交 PR（refactor/feature 模板 + AI 披露），合并到 main。
- 触发 next minor 发布流程，确认 GitHub Release 与 npm 包一致。
- 发布后归档本 change，并把 Python/YOLO 锁定值、安装失败样本与 E2E 记录保留在仓库内验证记录中。
