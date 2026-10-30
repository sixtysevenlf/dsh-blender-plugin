# T5 自审（author self-check，非独立评审）

## 范围
- 严格 TypeScript 覆盖 tests：新增 tsconfig.test.json（extends tsconfig，module ESNext / moduleResolution Bundler / include tests）；
  新增 tests/types/shims.d.ts 把 .mjs / .js 运行时边界声明为 ambient any；清理 tests 中 9 个失效 @ts-expect-error；
  修正 fake-context 的 isConcurrencySafe 类型与 tool-schema.test.ts 的 guard 收窄。
- ESLint flat config：js recommended + typescript-eslint recommended；仅对 src/tests 的 TS 和 scripts 的 mjs 生效；
  关闭 no-explicit-any（存量代码大量 any），保留 no-unused-vars（^_ 忽略）与 correctness 规则；
  加 no-restricted-imports 边界：index 不得 import 实现层；src/ui 只依赖 utils/contracts；constants/contracts/exec-context 不得依赖 tools/plugin/plan/headless。
- Prettier：.prettierrc.json（semi false / singleQuote / printWidth 140 / trailingComma none / arrowParens always / endOfLine auto），
  .prettierignore 排除 lib、runtime、addon、skills、docs、tests/fixtures 与 src/tui-host.ts（计划要求不改）。
- scripts/check-structure.mjs（B1 结构门禁）与 scripts/check-build.mjs（prepack 构建门禁）。
- package.json 增加 devDependencies 与 scripts；package-lock.json 重新生成（+93 packages，@deepseek-ai/* 版本 0 变化）。

## 自检清单（实际输出见 T5/test.log、T5/build.log、T4/structure-report.json）
- [x] npm run typecheck：src 严格 + tests 严格，均 exit 0。
- [x] npm run lint：exit 0（--max-warnings=0）；边界规则用负向探针证明会报错（src/ui 导入 ../tools/see.js → no-restricted-imports，exit 1 预期失败，随后删除探针）。
- [x] npm run format:check：exit 0；范围限于 src/新脚本/配置，不做全仓重排。
- [x] node scripts/test.mjs js：9 文件 / 30 测试通过。
- [x] npm run check:build：PASS（临时目录编译、entry 产物齐全、no-ts-nocheck、build-client --check）。
- [x] npm pack --dry-run：exit 0，1.7 MB / 321 文件（无 venv 大目录；仅含 824 B / 1.6 kB 的 runtime/ext 小脚本，符合仓库既有打包面）。
- [x] lock：scratch 目录 npm ci --dry-run --legacy-peer-deps exit 0（dry-run 新增 314 包）；未改 @deepseek-ai 版本。
- [x] check-structure：PASS（index 13 行 entry-only、15 handler、32/32 文件、0 环）。

## 设计取舍与残余风险
1. 未运行 npm install 改 worktree node_modules（环境冻结）；lock 用 scratch + 已提交版本的精确 devDeps 生成，再用 npm ci dry-run 校验。
   生成命令带 --legacy-peer-deps（仓库 e2e/依赖 peer 冲突的既有现实），CI 也需同样参数，已在 commands.txt 记录。
2. eslint 只启用 correctness/boundary 基线，不引入会制造数千存量错误的风格规则；后续可分批收紧。
3. prettier 刻意不覆盖 tests/**、runtime/**、src/tui-host.ts 与整个历史代码，避免无关 diff；format:check 只查本次范围。
4. check-build 在 os.tmpdir() 编译，不写 lib/，避免 prepack 改变工作树；真实发布前仍以 npm pack 内容为准。
5. 无独立评审；E4 行数例外、node_modules 环境脚手架、无 Blender 真机验证同 T4 review.md。
6. 未提交 node_modules、未提交 scratch lockgen/ci-check 目录；仅提交配置、脚本、src/lib、tests、package.json/lock 与证据。
