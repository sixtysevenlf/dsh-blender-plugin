# T16 证据：3.0.0 发布准备

- 分支：refactor；HEAD 以提交时为准（T16 提交见 git log）。
- 版本：package.json / package-lock.json = 3.0.0；src/constants.ts PLUGIN_VERSION = 3.0.0；addon bl_info = (1,11)；protocol 11；lib 未入库。
- 环境：Node 22.17（C:/Users/villa/node22）；Python 3.11 venv；Blender 4.2.10（C:/Users/villa/e2e/blender-4.2.10）；TMP/TEMP=D:/DSH/tmp/pytemp。
- 发布状态：**未发布**。按用户 2026-10-30 的裁决，先完成 T16 其余部分，tag v3.0.0 / GitHub Release / npm publish 待 C5 覆盖率决策后执行。

## 已完成

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 版本同步 | package/lock 3.0.0、PLUGIN_VERSION 3.0.0、addon 1.11、protocol 11、lib 未跟踪 | logs/version-sync.log |
| 构建 | npm run build（runtime→lib/runtime、host、client） | logs/build.log（最终门禁）/ t16-build.log |
| 门禁 | typecheck/lint/format/structure/build-check 全 0；test 353 passed + 295 pytest；unit 251、integration 102；matrix 347 行；check:addon 通过 | logs/*.log |
| 真实 Blender E2E | headless 29 passed；GUI 6 passed / 2 approved skip；GUI 覆盖 zip 安装、启用、Connect、doctor、bpy、selection、卸载 | logs/e2e-headless.log、logs/e2e-gui.log、e2e/、logs/install-migration.log |
| 打包 | npm pack 3.0.0：3.2 MB、829 files；tarball 含 62 个 lib/runtime JS、lib/index.js、addon zip，无单文件 addon、无 .ts 源码（仅 .d.ts） | logs/pack.log、logs/pack-actual.log、logs/pack-list.txt |
| 安装 smoke | tarball 干净安装；doctor kind=ok；/health ok；/status version=3.0.0、runtimeKeys=56；engine exports 87 | logs/install-smoke.log |
| addon extension validate | N/A：legacy bl_info addon 没有 blender_manifest.toml，Blender 4.2 `--command extension validate` 报 archive has no manifest；安装/启停由真实 Blender GUI E2E 覆盖 | logs/addon-extension-validate.log |
| 回滚路径 | tag refactor-base-v2.6.2 可 clone；v2.6.2 package 2.6.2、lib tracked 7、runtime .mjs 17、runtime .ts 0；完整演练见 T15f/rollback.md | logs/rollback-check.log |
| 文档 | CHANGELOG 3.0.0、MIGRATIONS v3.0（含卸载与回滚 §7）、README/zh-README/CONTRIBUTING/AGENTS/addon README 均 zip 安装；安装类 docs 版本口径改为 3.0.0 / addon v1.11 | release-notes.md、git diff |

## 未完成 / 阻塞

- **D4 B1 小回归未完成**：P0 bw_0001 已完成 score=93.75；P0 bw_0023 于 22:12 进入 EVAL；P0 bw_0091、P1 三题尚未完成。D4 使用 v2.6.1 profile，结果不混入 3.0.0。
- **D5 发布未完成**：tag v3.0.0、GitHub Release、npm publish 均未执行；C5 覆盖率仍为 EXEMPT 未裁决。
- **C5 覆盖率**：T12 已记录失败与 issue #52；本 T16 不重复宣称通过。
- 首次全量 npm test 曾出现一次 tests/integration/js/server-routes.test.ts 端口监听超时，单文件复跑与全量复跑均通过；作为环境竞争观察项记录，未用 retry 掩盖。

## 文件

- logs/：版本同步、build、typecheck、lint、format、structure、build-check、test、unit、integration、matrix、addon-check、pack、pack-actual、pack-list、install-smoke、addon-extension-validate、rollback-check、e2e-headless、e2e-gui、install-migration。
- e2e/：真实 Blender E2E results JSON、stdout/stderr、GUI Connect 截图。
- release-notes.md：v3.0.0 发布说明草稿（与 CHANGELOG 一致）。
- acceptance.md：§8.4 Gate D 逐项判定（D1–D6）。
