# T12 证据：CI 与工程门禁

- 分支：refactor
- T12 实现提交：b7d7b23（门禁、覆盖率、真实 Blender E2E 修复）
- 后续提交：252314f（workflow 先 build、GUI 截图、shutil.which、xvfb 检测）、c9805f5（Python 改动行覆盖率按 addon 口径统计）
- 证据提交时 HEAD：c9805f5
- 环境：Windows 11 + Git Bash；Node v22.17.0（C:/Users/villa/node22）；Python 3.11 venv（C:/Users/villa/e2e/dsh-venv，pytest 295）；Blender 4.2.10（C:/Users/villa/e2e/blender-4.2.10）；TMP/TEMP=D:/DSH/tmp/pytemp
- CI 工作流：.github/workflows/pr.yml（static-and-tests + coverage）、.github/workflows/e2e.yml（blender-headless，workflow_dispatch/label/tag）
- 本地等价命令与政策：docs/refactor/ci.md

## 结果摘要

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 安装解析 | npm ci --dry-run --legacy-peer-deps | EXIT 0 |
| 构建 | npm run build | EXIT 0 |
| 类型 | npm run typecheck（src/tests/runtime strict） | EXIT 0 |
| Lint | npm run lint（--max-warnings=0） | EXIT 0 |
| 格式 | npm run format:check | EXIT 0 |
| 结构 | npm run check:structure | PASS（index 13 行、32/32 文件、147 边、0 环） |
| 构建产物 | npm run check:build | PASS（62 runtime ts、0 mjs、0 @ts-nocheck） |
| 行为矩阵 | npm run test:matrix -- --release | 347 行（345 covered / 0 planned / 2 exempt） |
| 单元+集成+Python | npm test | Vitest 54 files / 353 passed / 2 skipped；pytest 295 passed |
| 打包 | npm pack --dry-run | 792 files、2.2 MB（未压缩 7.4 MB） |
| 真实 Blender E2E（headless） | npm run test:e2e | 29 passed / 8 deselected |
| 真实 Blender E2E（GUI） | npm run test:e2e:gui | 6 passed / 2 skipped（skip 见 exemptions #53） |
| JS 覆盖率 | npm run test:coverage | statements/lines 42.84%（4451/10388）、branches 73.35%、functions 37.06% |
| JS 改动行覆盖率 | diff-cover vs refactor-base-v2.6.2 | 8232 行 / 缺 5545 / 32%（EXEMPT #52） |
| Python addon 覆盖率 | pytest --cov=addon | 32%（4334 行 / 2950 缺） |
| Python 改动行覆盖率 | diff-cover vs refactor-base-v2.6.2 | 4334 行 / 缺 2950 / 31%（EXEMPT #52） |
| Clean clone | D:/DSH/t12-clean 从 c9805f5 clone 后跑 PR 门禁 | 见 clean-clone.log / clean-clone-ci.log |

C5 目标 80/70 未达，按 issue #52 记为 EXEMPT，并设 ratchet：后续 PR 的改动行覆盖率不得低于当前值（JS 32%、Python 30%）；缺口补测计划见 issue #52。C7 的两个 GUI 条件 skip 由 issue #53 批准。

## 文件

- logs/：每个门禁的原始 stdout/stderr 日志（npm-ci-dryrun、build、typecheck、lint、format、structure、build-check、matrix、test、pack、e2e-headless、e2e-gui、coverage、python-coverage、diff-cover-js、diff-cover-py）。
- e2e/：真实 Blender E2E 的 results JSON、stdout/stderr、GUI Connect 截图 gui-connect.png（971890 B，来自 tests/e2e/gui，4 passed/1 skipped 的运行）。
- clean-clone.log：干净克隆的完整 PR 门禁输出。
- exemptions.md：EXEMPT-C5 与 GUI skip 的 issue 记录。
- acceptance.md：§8.2 C1–C8 逐项判定。
- review.md：C8 文档/质量抽查记录与剩余风险。
- artifacts.sha256：本目录文件的哈希清单。
