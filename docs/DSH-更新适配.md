# DSH 更新适配（v0.9.2 起）

> 触发场景：DSH 升级（`npm i -g @deepseek-ai/dsh@<版本>`）之后，本插件的 15 个工具是否还在、行为是否变了。
> 结论依据：2026-09-23 对 宿主 0.1.6-alpha.1 ↔ npm 上 0.1.7-alpha.2 的**逐包 diff + 版本矩阵实测**。

## 0. 一句话

**工具定义契约是安全的**（15 个工具在 `dsh-tools` 0.1.5-rc.2 / 0.1.6-alpha.1 / 0.1.6-alpha.2 /
0.1.7-alpha.1 / 0.1.7-alpha.2 五个版本下全部注册成功、0 报错、参数 schema 逐字节一致）。
真正要盯的是**四件与工具定义无关的事**：加载链、解析链、取消/超时、输出预算口径。

## 1. 本插件在 DSH 里的三条链（先认清结构，再看风险）

| 链 | 现状 | 断了的症状 |
|---|---|---|
| **加载链** | **bundle 式（2026-09-23 起）**：插件列在 profile 的 `dsh.profile.bundles`，patch 层由本包 `cordis.patch.yml` 提供（邻位 coc-dice / session-list-cache 同批迁入）。`dsh-super-injector` 已卸载并删除 | 15 个工具整体消失，但 Blender / 插件包都没坏；先用 `--dump-config` 看本包那一层在不在（§2） |
| **解析链** | `<插件>/node_modules/@deepseek-ai/dsh-tools` → `~/dsh-harness`（fake checkout 软链）→ `/usr/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-tools`（**全局安装**）；`cordis`/`schemastery`/`cosmokit`/`dsh-llm` 同理 | 载入期 `ERR_MODULE_NOT_FOUND`（不是降级，是整体消失）；换 npm prefix / nvm / pnpm global 就会触发 |
| **构建链** | `scripts/build.sh` 探测 `DSH_CHECKOUT`（默认 `~/dsh-harness`），用它的 `tsc` 与 `@types/node` 软链 | `build: dependency target missing` / `tsc not found` |

好处：解析链指向**全局安装本身**，所以插件的 `defineTool` 与宿主**永远同版本**（不存在"对着旧副本编译"）。
代价：没有冻结基线 —— 宿主怎么改，插件下一次载入就吃什么；所以下面的回归必须跑。

## 2. 更新后验收（三步，约 30 秒）

```bash
cd ~/DSH/dsh-blender-plugin
node tests/protocol_selftest.mjs        # ① addon 协议层：28 项，不需要 Blender / DSH
npm run test:dsh-api                    # ② 工具契约：15 工具 + 15 timeoutMs + 0 报错 + 前缀 [v0.9.2]
npm run test:dsh-api -- --version 0.1.7-alpha.2   # ③ 升级前预演：把待升级版本的 dsh-tools 拉下来先测
```

会话里再点一次 `blender_viewport(op="doctor")`：

```
诊断：…
宿主 API：dsh-tools@0.1.6-alpha.1 @ /usr/lib/node_modules/.../dsh-tools/lib/index.js   ← v0.9.2 自证
插件：v0.9.2 @ /home/sixtyseven67/DSH/dsh-blender-plugin/lib/index.js
```

`宿主 API` 这行是**升级后第一时间要看的**：它证明"插件挂上了哪一份宿主 API"。
若工具整体消失，先跑装配预演：

```bash
dsh --profile web --dump-config | grep -A3 dsh-blender-plugin
```

有本包那一层 = 加载链没断（去查解析链 / 宿主 API）；没有 = profile 的 `dsh.profile.bundles` 被改过。
（`dev_plugin_status` / `dev_injected_list` 属于已删除的 super-injector，不再可用。）

## 3. 已知风险与状态

| 编号 | 风险 | 状态 |
|---|---|---|
| R1 | 曾只能靠 super-injector 注入：第三方 hook 吃宿主内部面（`loader.internal.loadCache`/`entry._dispose`），loader 一重构就可能整体消失 | **已解决**：2026-09-23 切到官方 bundle 装配并删除 super-injector（见 §4） |
| R2 | 解析链依赖全局安装的绝对路径；无冻结基线 | **已加自证**（`HOST_API` + doctor/status）；换安装位置仍需人工确认 |
| R3 | 宿主 `exec.signal` 未接、未声明 `timeoutMs` → 上游设默认截止时"Blender 还在跑、工具已判超时" | **已修**（v0.9.2：`EXEC_CTX` 贯通 + 15 个静态 `timeoutMs`） |
| R4 | 0.1.7 把 spill 口径从 `maxInlineBytes: 50000` 改成 `maxInlineTokens: 12500`，且图片会计入省略 → 大文本可能改为落盘 spill、长会话图片可能被省略 | **观察项**：升级后按 token 重新校准本插件自己的 12KB 截断阈值 |
| R5 | 新版 `plugin-manager` 按 `dsh.bundle` 判定 bundle | **已声明**（`package.json` + `cordis.patch.yml`），可切 §4 |
| R6 | 0.1.7 把官方 HMR 行 `cordis-plugin-hmr` 换成 `@deepseek-ai/dsh-hmr`、`dsh-settings-file` → `dsh-settings` | 本插件不依赖这些 id；injector 已删除 —— 代价是不再有第三方"运行时热注入"，改配置需重启（官方 HMR 行在 0.1.7 才启用） |
| R7 | `peerDependencies` 写的是裸名 `cordis`/`schemastery`（npm 上是另一条谱系），宿主用的是 `@deepseek-ai/*` | 现在走 **link 装配**（profile node_modules → 本目录 → 全局安装）→ 无影响；**改成 registry 安装**（`pnpm add`）前必须先解决，否则会引入第二份实现 |
| R8 | 图片回传失败被 `catch` 吞掉，纯文本结果无告警 | **已修**（v0.9.2：`{ref}`/`{why}` + `⚠ 图片未回传：<原因>`） |
| R9 | 15 个工具 + 超长中文描述全量进 prompt；新版 dsh-tools 已有 `deferLoading` | **优化项**：上游若默认延迟加载，需评估"描述即文档"策略 |

## 4. 装配方式：bundle 式（2026-09-23 已切换 + 迁移记录）

本机现状：插件列在 `~/.dsh/profiles/web/package.json` 的 `dsh.profile.bundles`，patch 层由本包
`cordis.patch.yml` 提供；`dsh-super-injector` 已从 bundles / 依赖 / 状态目录 / 源码 / 分发产物里全部删除。

同批迁移的还有两个**原先只靠注入活着**的插件 —— 不迁就拆注入器会把它们一起弄丢：

| 插件 | 迁移前 | 迁移后 |
|---|---|---|
| `@dsh-external/dsh-blender-plugin` | injector 注入（`ctx.loader.create` 挂 root） | profile bundle（本包 `cordis.patch.yml`） |
| `@dsh-external/dsh-coc-dice` | 同上 | profile bundle（该包 `cordis.patch.yml`）—— 仍是**宿主级**，掷骰工具对所有会话可见 |
| `@dsh-external/dsh-session-list-cache` | 同上 | profile bundle（该包 `cordis.patch.yml`） |

**必须重启 DSH 才真正生效**（重启前旧进程里仍留着注入器 entry 与注入式 fiber；重启后只按 bundles 装配）。
注意：重启前那个活着的注入器 fiber 会**自己重建** `~/.dsh/super-injector/` 里的统计/日志文件（它的定时写），
所以"删了又出现"是正常的 —— 重启后再删一次即可：

```bash
rm -rf ~/.dsh/super-injector   # 重启后执行；此后不会被重建
```
重启后自检：

```bash
dsh --profile web --dump-config | grep -A3 "dsh-blender-plugin\|dsh-coc-dice\|dsh-session-list-cache"
# 期望：三层各一行 insert；另外 grep super-injector 应无输出
```

**回滚**：把 `~/.dsh/profiles/web/package.json` 换回 `package.json.bak-injector-removal-<时间戳>`，
重装 super-injector，重启。

⚠ 不要同时用两种装配：两个 fiber 会**重复注册同名工具**（会话里看到两份 `blender_rt_*`）。

## 5. 每次 DSH 升级的固定动作（抄这段）

1. 升级前：`npm run test:dsh-api -- --version <目标版本>` → PASS 再升。
2. 升级时：**别用裸 `npm i -g @deepseek-ai/dsh`** —— npm 的 `latest` 可能比现装版本**旧**
   （2026-09-23 快照：`latest=0.1.5-rc.2`、`alpha=0.1.7-alpha.2`，而现装 0.1.6-alpha.1）。用显式版本或 `@alpha`。
3. 升级后：§2 三步 + `dsh --profile web --dump-config | grep -c super-injector`（应为 0）
   + `blender_viewport(op="doctor")` 看 `宿主 API` 行。
4. 若 R4 生效（结果改为落盘 spill）：调 `docs/配置参考.md` 里记录的截断阈值，并复跑一次大型
   `blender_rt_plan audit_*` 看输出形态。
