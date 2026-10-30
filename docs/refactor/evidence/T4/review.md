# T4 自审（author self-check，非独立评审）

## 范围
- 按计划 §4.2/§4.3 把原 2888 行 src/index.ts（@ts-nocheck）拆为目标文件：index/constants/plugin/contracts/host/utils/plan/ui/headless/tools/plan-tool。
- src/index.ts 现在只 re-export；15 个工具 handler 在 src/tools/*.ts，plan 在 src/plan/tool.ts。
- src/tui-host.ts 未改动（计划要求 unchanged；仍保留原来的 @ts-nocheck）。
- lib/ 已用 Node 22 + tsc 5.9.3 重新构建并纳入本次提交。

## 自检清单（全部为实际输出，见 T4/build.log、T4/test.log、T4/structure-report.json）
- [x] src/index.ts entry-only：12 行（wc -l；check-structure 按换行切分为 13），远小于 150 行上限。
- [x] 目标文件 32/32 存在；导入图 32 文件 / 147 边 / 0 环（scripts/check-structure.mjs）。
- [x] 15 个工具名与 capability.lock.json 一致；能力锁 PASS（34 family / 228 op / 15 tools）。
- [x] 公开导出面不变：Config,__internals,apply,frameDedupe,inject,name,resetFrameCache（node import 实测）。
- [x] Vitest 9 文件 / 30 测试通过；pytest 5 通过；11 个 legacy 自检全部 exit 0。
- [x] lossless / frame dedupe / schema resolve / cards / skill bundle / tui host / client bundle 等兼容自检通过。
- [x] 无新增 @ts-nocheck（check-build 的 no-ts-nocheck 检查；tui-host.ts 按计划例外）。

## 行为冻结核对
- 工具名、参数 schema、描述、presenter、错误码、lease/pause 语义均从原实现原样搬移；未主动改文案/默认值/路由。
- 仅有一处代码修正：watch 的 att.why 缺失时回退到 '未知原因'（原实现依赖 att.why，拆分后类型化需要等价兜底）。
- capability_lock.mjs 与 single_source_selftest.mjs 的读取位置从 src/index.ts 改为实际真源（src/tools/*.ts、src/plan/tool.ts）；
  scripts/sync_counts.mjs 同步改为写 src/plan/tool.ts。这是结构拆分带来的测试/脚本适配，不改变对外契约。

## 残余风险
1. 无独立评审（作者自审；独立评审需 Lead/其他 reviewer 完成，见 review.md 的 R1 流程要求）。
2. 本任务未启动 Blender/GUI，也没有真实 addon 往返；行为等价性的证据是单元/契约/legacy 自检，不是端到端真机。
3. E4 的 <=400 changed lines 目标对本拆分必然超出（拆文件 + 重建 lib + 证据），需 Lead 记例外；未擅自缩小范围。
4. src/tui-host.ts 仍有 @ts-nocheck（计划要求 unchanged），所以严格类型覆盖不含该文件。
5. lib/ 的 .js.map 与拆分后的模块会增加包内文件数（最终 npm pack 321 文件 / 1.7 MB，仍远低于历史 458 MB 事故面；增加的是拆分后的 lib 模块与证据，不是 runtime 大资产）。
6. 环境用本地 node_modules junction farm + TEMP=D:/DSH/tmp；在干净 CI 上路径不同，但 npm ci 的 lock 一致性已用 scratch 验证。
