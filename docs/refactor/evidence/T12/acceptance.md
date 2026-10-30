# T12 验收：Gate C（测试与行为覆盖）

基线：c9805f5（T12 实现 b7d7b23 + 252314f + c9805f5）。环境与命令见 README.md；日志见 logs/、clean-clone.log、e2e/。

| Gate | 判定 | 证据 | 说明 |
| --- | --- | --- | --- |
| C1 三层独立运行 | PASS（R2） | logs/unit.log（37 files / 251 passed / 2 platform skip）、logs/integration.log（17 files / 102 passed）、logs/test.log（pytest 295 passed）、logs/e2e-headless.log、logs/e2e-gui.log | unit/integration 默认集合不含真 Blender/GUI/网络/DSH provider；E2E 单独入口。 |
| C2 PR 门禁 | PASS（R1） | clean-clone.log | 在 D:/DSH/t12-clean 从 c9805f5 干净 clone；npm ci --legacy-peer-deps、build、typecheck、lint、format、check:structure、check:build、test:unit、test:integration、test:python、test:matrix、pack 全 0 退出，未跑真 Blender。 |
| C3 行为覆盖矩阵 | PASS（R1） | logs/matrix.log | 347 行（345 covered / 0 planned / 2 exempt），15 tools / 39 commands / 34 families / 228 ops / 29 routes；脚本 --release 模式通过。 |
| C4 旧测试迁移 | PASS（R2） | tests/MIGRATION.md（67 数据行，无 pending）、logs/test.log（npm test 不再含 test:legacy）、git diff of package.json | T11 逐文件处置 67 个旧文件；test:legacy 与 22 个别名已移除；归档 E2E 由 legacy_runner 执行。 |
| C5 覆盖率 | FAIL / EXEMPT-C5 不适用 | logs/coverage.log、logs/python-coverage.log、logs/diff-cover-js.log、logs/diff-cover-py.log、exemptions.md | JS 42.84%，JS diff 32%；Python addon 32%，Python diff 31%；目标 80/70。issue #52 已建，但 §8.0 只允许平台/版本例外，因此不勾选、不宣布通过。 |
| C6 真 Blender E2E | PASS（R1） | logs/e2e-headless.log（29 passed / 8 deselected）、logs/e2e-gui.log（6 passed / 2 skip）、e2e/*.results.json、e2e/gui-connect.png | Blender 4.2.10 headless + GUI 覆盖 zip 安装、启用、Connect、doctor、selection、bpy、导出、卸载；GUI 截图 971890 B。 |
| C7 skip/flaky/retry 治理 | PASS（R2） | logs/unit.log、logs/e2e-gui.log、exemptions.md、issue #53 | 2 个 macOS 条件 skip；2 个 GUI skip 由 issue #53 批准；8 个 deselected 是 marker 选择；无 retry 掩盖。 |
| C8 测试质量抽检 | PASS（R2，附偏差） | review.md | 新测试单主题、契约测试断言真实注册表、失败信息可定位；唯一 856 行 >800 的 tests/e2e/blender/cases/upstream_integration_selftest.py 是 T11 归档 legacy 且 tests/MIGRATION.md 记录不拆六域的有意偏差。 |

## 结论

- C1、C2、C3、C4、C6、C7、C8 可勾选。
- C5 未通过，不得勾选；3.0.0 发布被 §8.0 阻断。需要三选一：补测到 80/70、修订计划明确接受 C5 例外、或缩小 C5 口径（例如把 Phase R 迁移文件排除出“改动行”）。当前不擅自改验收标准。
