# T5 验收（author self-check，非独立评审）

验收人：作者自检（delegated agent）  日期：2026-10-30  独立评审：未进行（需 Lead 安排）
判据来源：计划 §4.2（T5 静态门禁）、§8.3 C2；行为冻结要求。

| ID | 验收项 | 结果 | 证据 | 备注/评审 |
|---|---|---|---|---|
| T5-01 | tests 纳入严格 TypeScript | PASS | tsconfig.test.json；T5/test.log npm run typecheck EXIT=0（src + tests 两次 tsc） | tests/types/shims.d.ts 把 .mjs/.js 运行时边界声明为 ambient any；清理 9 个失效 @ts-expect-error |
| T5-02 | ESLint 可运行，含边界规则 | PASS | eslint.config.mjs；T5/test.log npm run lint EXIT=0（--max-warnings=0） | 负向控制：src/ui 导入 ../tools/see.js → no-restricted-imports，exit 1（预期），探针已删除 |
| T5-03 | Prettier 可运行且格式检查通过 | PASS | .prettierrc.json；.prettierignore；T5/test.log npm run format:check EXIT=0 | 范围只含本次代码/配置，不重排全仓；排除 tui-host.ts |
| T5-04 | B1 结构门禁脚本 | PASS | scripts/check-structure.mjs；T4/structure-report.json；T4/build.log | index 13 行 entry-only=true；15 handler；32/32 文件；32 文件/147 边/0 环 |
| T5-05 | prepack/build 检查 | PASS | scripts/check-build.mjs；T5/build.log check-build PASS | 临时目录编译、entry 产物齐全、no-ts-nocheck、build-client --check |
| T5-06 | 发布面 npm pack 通过 | PASS | T5/build.log 最终段：npm pack --dry-run EXIT=0；1.7 MB / 5.2 MB unpacked / 321 文件 | 无 venv 大目录；仅含既有 824 B/1.6 kB runtime/ext 小脚本 |
| T5-07 | devDependencies 与 package-lock 一致 | PASS | package.json（@eslint/js、eslint、prettier、typescript-eslint）；package-lock.json（+93 包，@deepseek-ai/* 0 版本变化）；T5/build.log npm ci --dry-run --legacy-peer-deps EXIT=0（dry-run added 314） | lock 在 scratch 用固定版本生成，未在 worktree npm install；CI 需 --legacy-peer-deps |
| T5-08 | 脚本入口 | PASS | package.json scripts：typecheck/typecheck:src/typecheck:test/lint/lint:fix/format/format:check/check:structure/check:build/prepack | prepack=check-build |

结论：T5 PASS（作者自检）。残余风险：无独立评审；E4 行数例外；prettier 未覆盖全仓/legacy；
node_modules 本地 junction farm 未提交；CI 需 --legacy-peer-deps；check-build 用临时目录而非 scripts/build.sh。
