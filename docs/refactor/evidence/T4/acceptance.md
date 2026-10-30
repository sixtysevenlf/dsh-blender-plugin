# T4 验收（author self-check，非独立评审）

验收人：作者自检（delegated agent）  日期：2026-10-30  独立评审：未进行（需 Lead 安排）
判据来源：计划 §4.2/§4.3、§8.0；行为冻结要求。

| ID | 验收项 | 结果 | 证据 | 备注/评审 |
|---|---|---|---|---|
| T4-01 | src/index.ts entry-only 且 <=150 行 | PASS | src/index.ts（12 行 / 807 B）；T4/structure-report.json（13 行，entry-only=true） | wc 与换行计数差异为末尾换行；均远低于 150 |
| T4-02 | §4.2/§4.3 目标文件齐全 | PASS | T4/structure-report.json target files 32/32；T4/build.log | 含 host/contracts/utils/ui/headless/plan/tools 全部目标 |
| T4-03 | 15 个工具 handler 全部在 src/tools/*（plan 在 src/plan/tool.ts） | PASS | T4/build.log check-structure 15 个 handler；T4/test.log capability_lock PASS 34/228/15 | 工具顺序与原实现一致 |
| T4-04 | 工具名/schema/描述/presenter/错误码/lease 语义不变 | PASS | T4/test.log（tool_cards_selftest、lossless_guard、Vitest tool-schema 4 项）；T4/build.log 导出面 | 行为冻结靠契约测试与人工搬移核对；无独立评审 |
| T4-05 | 公开导出兼容 | PASS | T4/build.log node import：Config,__internals,apply,frameDedupe,inject,name,resetFrameCache | 与拆分前一致 |
| T4-06 | 单元 + 集成 + Python 测试 | PASS | T4/test.log：Vitest 9 文件 / 30 测试；pytest 5 passed | 均 EXIT=0 |
| T4-07 | legacy 回归自检 | PASS | T4/test.log：11 个 selftest 全 EXIT=0（capability_lock、single_source、lossless_guard、frame_dedupe、schema_resolve、skill_bundle、tool_cards、tui_host、client_bundle、backend_probe、selection_store） | 其中 capability_lock/single_source/sync_counts 的读取路径适配为拆分后真源 |
| T4-08 | 严格 TS 构建 + lib 重建 | PASS | T4/build.log：tsc -p tsconfig.json --noEmit EXIT=0；tsc emit EXIT=0；build-client EXIT=0；T5/build.log check-build PASS | 未运行 scripts/build.sh（会 relink node_modules），用等价命令 |
| T4-09 | 无新增 @ts-nocheck（tui-host 例外） | PASS | T5/build.log check-build：no-ts-nocheck — clean | src/tui-host.ts 按计划保持 unchanged，仍有原 @ts-nocheck |

结论：T4 PASS（作者自检）。残余风险：无独立评审；无 Blender/GUI 真机验证；E4 行数例外需 Lead 批准；
node_modules 为本地 junction farm 的环境脚手架；包内文件数因拆分模块增加（最终 321 文件 / 1.7 MB）。
