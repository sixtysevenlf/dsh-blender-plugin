# T16 验收：Gate D（工程、运行与发布）

基线：T16 提交（见 git log）；T12 的 C 门禁证据在 docs/refactor/evidence/T12/。发布动作按用户 2026-10-30 裁决暂缓。

| Gate | 判定 | 证据 | 说明 |
| --- | --- | --- | --- |
| D1 干净环境 | PASS（R1） | logs/install-smoke.log、clean-clone.log | Node 22.17；npm ci --legacy-peer-deps、build、typecheck、lint、unit、integration、python、matrix、pack 全 0；tarball 干净安装 doctor kind=ok、/health ok、/status 3.0.0。 |
| D2 发布包 | PASS（R1） | logs/pack.log、logs/pack-actual.log、logs/pack-list.txt | npm pack 3.0.0：3.2 MB / 829 files；含 lib/runtime 62 个 JS、lib/index.js、addon zip；无单文件 addon、无 .ts 源码（仅 .d.ts）；体积较 v2.6.2（约 2.2 MB / 778）增加 1 MB，来自 62 个 runtime JS + sourcemap/d.ts + 文档证据，无 secrets/大资源异常。 |
| D3 addon 安装迁移 | PASS（R1） | logs/install-migration.log、e2e/blender-gui.results.json、e2e/gui-connect.png、MIGRATIONS.md §1/§7 | 真 Blender 4.2.10 GUI：安装并启用 zip、Connect、socket roundtrip、get_addon_info、ping、bpy、selection、disconnect、停用无残留、移除全部 True；旧单文件停用/移除路径写在 MIGRATIONS.md §1/§7，并复用同一 Preferences addon_remove 路径。 |
| D4 B1 小回归 | PENDING | 日志 /opt/bench/runs/b1/t16-d4.log；ledger /opt/bench/runs/b1/ledger.jsonl | 固定 v2.6.1 profile；P0 bw_0001 score=93.75、P0 bw_0023 22:12 进入 EVAL、P0 bw_0091 与 P1 三题未完成；不得在完成前勾选。 |
| D5 发布与回滚 | BLOCKED | logs/version-sync.log、logs/rollback-check.log、release-notes.md | 版本/PLUGIN_VERSION 3.0.0、addon 1.11、protocol 11、lib 未入库已完成；tag v3.0.0 / GitHub Release / npm publish 未执行（C5 决策 + 用户要求暂缓）；v2.6.2 回滚路径已验证可 clone（tag 789ce2c，lib 7、mjs 17、ts 0），完整演练见 T15f/rollback.md。 |
| D6 发布文档 | PASS（R1） | release-notes.md、CHANGELOG.md、MIGRATIONS.md §0/§1/§7、git diff docs-diff | 安装、升级、卸载、回滚、breaking change 全部写清；旧单文件用户有迁移步骤；release notes 与 CHANGELOG 一致；安装类 docs 版本口径改为 3.0.0 / addon v1.11。 |

## 结论

- D1（提交后 clean clone）、D2、D3、D6 可勾选；D4 未完成，D5 被 C5 决策与用户“发布待定”阻断。
- T16 未完成；3.0.0 未发布。
