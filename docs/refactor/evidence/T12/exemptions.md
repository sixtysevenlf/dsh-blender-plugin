# T12 例外与 skip 记录

## EXEMPT-C5：改动行覆盖率未达 80/70

- 状态：已建 issue，未满足 §8.0 对 EXEMPT 的适用范围（§8.0 写明 EXEMPT 仅允许平台/版本条件），因此 C5 不勾选；发布受阻。
- Issue：[#52](https://github.com/sixtysevenlf/dsh-blender-plugin/issues/52)
- Owner：T12（发布前由 Lead 复审）
- 到期日：2027-01-31；复审日：2026-11-15
- 实测（c9805f5，refactor-base-v2.6.2 为基）：
  - JS statements/lines 42.84%（4451/10388），branches 73.35%，functions 37.06%。
  - JS diff-cover：8232 changed lines / 5545 missing / 32%。
  - Python addon：4334 行 / 2950 missing / 32%；diff-cover Python：4334 行 / 2950 missing / 31%。
- 缺口主因（不是“测试没跑”）：Phase R 把约 4,476 行 runtime .mjs 语义等价迁到 .ts，diff-cover 按新增文件计行；大量行由真实 Blender E2E 覆盖，而 vitest coverage 只统计 JS 测试进程。最大缺口为 runtime/execution/headless.ts、src/headless/receipts.ts、runtime/execution/jobs.ts、src/tools/*（viewport/see/job）。
- Ratchet：后续 PR 的 diff-cover 不得低于 JS 32%、Python 30%；见 .github/workflows/pr.yml 的 --fail-under。补测计划与优先文件清单在 issue #52。
- 不勾选 C5 的影响：§8.0 要求 A1-A8、B1-B7、C1-C8、D1-D6、E1-E6 全部勾选才允许发布；在覆盖率达标或计划被明确修订前，3.0.0 不发布。

## C7 平台条件 skip（已记录，非未批准）

- Vitest unit：2 个 `it.skipIf(!IS_MAC)`（tests/unit/js/platform-paths.test.ts:100、119），仅在 macOS 运行；Windows/Linux 上跳过属平台条件，不是 flaky 掩盖。
- Blender GUI E2E：2 个 approved skip（test_qc.py、test_gui_channel.py），需完整 GUI+backend 栈（DSH_BLENDER_GUI_E2E=1）；由 issue [#53](https://github.com/sixtysevenlf/dsh-blender-plugin/issues/53) 批准，owner T10，到期 2027-01-31。
- E2E 的 8 个 deselected 是按 marker 选择（release 集合不选这些 GUI/慢用例），不是 skip。
- 本轮未使用 retry 掩盖失败；pytest 默认集合没有重试插件。