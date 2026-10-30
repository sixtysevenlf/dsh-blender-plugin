# T12 / C8 测试质量抽检（review）

抽检对象：tests/unit、tests/integration、tests/e2e 的测试文件与 helpers；重点看单主题、体量、是否断言真实注册表、失败信息定位。

## 结论

- 通过（R2 作者自检 + 抽检），但附两条已记录偏差。
- 新测试文件普遍单一主题、<200 行；最大抽检样本 tests/integration/js/server-routes.test.ts 143 行、tests/unit/js/contract/command-contract.test.ts 130 行。
- 契约测试断言真实来源：command-contract 直接读 addon/dsh_blender_bridge/server.py、protocol.py、commands/core.py、commands/generate_premium.py 的 handler 表，并与 runtime/catalog.ts 的 COMMAND_CATALOG 对拍，不是只比快照。
- 服务路由测试真实 spawn lib/runtime/server.js + fake addon，等 /health 后发请求，失败信息带 childLog 尾部（可定位到启动/路由）。
- 工具契约/行为矩阵测试从 catalog 与 behavior matrix 读 15 tools/39 commands/34 families/228 ops/29 routes，矩阵脚本对缺口/重复/未知号非 0。
- E2E 失败信息包含 case 名、Blender 退出码、report 的 stage/error；GUI 截图保存在 e2e/gui-connect.png。

## 偏差与剩余风险

1. tests/e2e/blender/cases/upstream_integration_selftest.py 为 856 行（超过 C8 的 800 行 selftest 建议线）。它是 T11 归档的 legacy 上游整合脚本，tests/MIGRATION.md 已记录“不拆六域”的有意偏差：其 main() 不可无损拆分，拆成六个 wrapper 会重复执行同一脚本。保留 856 行是迁移决策，不是新写 selftest。
2. 抽检的是结构与断言来源，不是逐文件作者级 review；T4-T15 的非作者独立复核仍待发布前执行（R1 要求），本轮不冒充已完成。

## 样本用例

- tests/integration/js/server-routes.test.ts：freePort + 真实 server 子进程 + fake addon，覆盖 25/25 请求与错误路径。
- tests/unit/js/contract/command-contract.test.ts：对 addon 源做静态 handler 解析，断言 39 commands 完整、pause/read-only 语义。
- tests/integration/js/package-contract.test.ts：npm pack 产物、离线安装、入口语法验证。
- tests/e2e/blender/test_upstream_integration.py：真实 Blender 4.2.10 下执行归档 case，报告 JSON 落盘。
