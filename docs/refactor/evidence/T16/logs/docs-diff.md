# Docs diff — v3.0.0 release docs

## Changed files
 AGENTS.md                                          |  2 +-
 CHANGELOG.md                                       | 48 ++++++++++++++++++++++
 MIGRATIONS.md                                      | 28 ++++++++++---
 addon/README.md                                    |  2 +-
 ...243\205\346\217\220\347\244\272\350\257\215.md" | 26 ++++++------
 ...233\264\346\226\260\351\200\202\351\205\215.md" | 16 ++++----
 ...21\347\272\246-v2.3\345\242\236\350\241\245.md" | 10 ++---
 ...223\215\344\275\234\346\225\231\347\250\213.md" | 12 +++---
 ...256\211\350\243\205\346\270\205\345\215\225.md" | 16 ++++----
 "docs/\351\203\250\347\275\262SOP.md"              |  8 ++--
 ...205\215\347\275\256\345\217\202\350\200\203.md" |  4 +-
 11 files changed, 118 insertions(+), 54 deletions(-)

## Version claims
AGENTS.md:7:This repository is the **DSH × Blender plugin** (v3.0.0): a DeepSeek Harness plugin that drives a live
AGENTS.md:9:**34 families / 228 ops**; Blender addon **v1.11 / protocol 11** (package + zip install); backend `127.0.0.1:9877`, addon `127.0.0.1:9876`.
AGENTS.md:60:- **Only the bundled addon is supported** (`addon/dsh_blender_bridge.zip`, v1.11 / protocol 11).
CHANGELOG.md:3:## v3.0.0（2026-10-30）—— 工程健康化重构（breaking：addon 改 zip 包）
CHANGELOG.md:10:- **addon 安装方式改为 zip 包**：发布物是 `addon/dsh_blender_bridge.zip`（包内 `__init__.py`、`bl_info` v1.11、protocol 11）；
CHANGELOG.md:49:- T16 发布动作（tag v3.0.0 / GitHub Release / npm publish）在覆盖率决策后执行；本条目是发布候选说明。
MIGRATIONS.md:3:> **当前版本（2026-10-30）**：插件 **v3.0.0** · **15 个模型工具** · plan catalog **34 family / 228 op** ·
MIGRATIONS.md:4:> addon **v1.11**（protocol **11**）· 后端 `127.0.0.1:9877` / addon `127.0.0.1:9876`。
MIGRATIONS.md:7:## 0. 最短路径（任何旧版 → v3.0.0）
MIGRATIONS.md:9:1. 更新 DSH 插件包到 **v3.0.0** → **重启 DSH**；旧「Blender 视口」tab / 点击进消息在 v2.3 已删除，这是预期。
MIGRATIONS.md:10:2. Blender 里停用并移除旧 addon（含旧的单文件 `dsh_blender_bridge.py`）→ 安装本仓 `addon/dsh_blender_bridge.zip`（**v1.11**）→ 启用 → N → Connect。
MIGRATIONS.md:18:## 1. Blender 侧 addon：必须换成 `DSH Blender Bridge` v1.11（zip 包）
MIGRATIONS.md:24:2. `Install...` → 选本仓 [`addon/dsh_blender_bridge.zip`](addon/dsh_blender_bridge.zip) → 勾选启用 **DSH Blender Bridge**（v1.11 / protocol 11）。v3.0 起不再支持单文件安装。
MIGRATIONS.md:44:## 4. 仓库使用方式（v3.0.0 / T15 起）
MIGRATIONS.md:58:## 6. addon v1.10 / v1.11 / v2.6 追加（无需迁移动作）
MIGRATIONS.md:60:- **addon v1.11**：拆成包并改为 zip 安装；命令名、参数、返回结构与 pause 语义不变（protocol 11）。
MIGRATIONS.md:74:## 7. 卸载与回滚（v3.0.0）
MIGRATIONS.md:76:### 7.1 卸载 v3.0.0
MIGRATIONS.md:80:3. 旧单文件 `dsh_blender_bridge.py`（v2.6.1 及以前）在升级时就必须停用并移除；v3.0.0 不会再加载它。
MIGRATIONS.md:84:1. 安装 v2.6.2 的插件包（tag `refactor-base-v2.6.2` / 对应 tarball），不要用 v3.0.0 的目录覆盖；
MIGRATIONS.md:85:2. Blender 里停用 v1.11 zip addon，重新安装 v2.6.2 自带的单文件 `dsh_blender_bridge.py`（或 v2.6.2 的 addon 产物），启用后 N → Connect；
MIGRATIONS.md:87:4. v2.6.2 tag 自带 `lib/` 与旧 `runtime/*.mjs`，不需要构建；v3.0.0 的 `lib/runtime` 与 `.ts` 不要混用。
MIGRATIONS.md:88:5. 回滚演练记录见 `docs/refactor/evidence/T15f/rollback.md` 与 v3.0.0 发布证据 `docs/refactor/evidence/T16/`。
addon/README.md:17:| 版本 / 协议 | addon **v1.11 / protocol 11** |
addon/README.md:55:> 本 addon（目录或 zip 形式的 `dsh_blender_bridge` / `dshbb_*`），并校验 addon v1.11+ / protocol 11。
docs/AI-安装提示词.md:6:> **当前版本口径（2026-10-28）**：插件 **v3.0.0** · **15 个模型工具** · plan catalog **34 family / 228 op** ·
docs/AI-安装提示词.md:7:> Blender 侧 addon **v1.11**（protocol **11**）· 后端 `127.0.0.1:9877` / addon `127.0.0.1:9876`。
docs/AI-安装提示词.md:21:   当前版本：插件 v3.0.0 / 15 个模型工具 / plan catalog 34 family 228 op / addon v1.11（protocol 11）。
docs/AI-安装提示词.md:47:验收通过后，请主动告诉我当前版本（v3.0.0）的新能力：
docs/AI-安装提示词.md:52:· Blender 原生选中 → N 面板「发送选中到 DSH」仍可用（v2.3 起；addon 需 v1.11+）；
docs/AI-安装提示词.md:53:· addon v1.11 起 N 面板有「暂停 AI 操作」（恢复只能人在 Blender 点）；
docs/AI-安装提示词.md:55:并说明升级插件后必须重启 DSH、Blender 侧 addon 要 v1.11+（旧 MCP for Blender 自 v2.3 起不再支持）。
docs/AI-安装提示词.md:74:## ③.5 装完必须告知主人（v3.0.0 的"顺手通知"要求）
docs/AI-安装提示词.md:79:1. **版本口径**：插件 v3.0.0、15 个模型工具、plan catalog 34 family / 228 op、addon v1.11（protocol 11）；
docs/AI-安装提示词.md:94:   activity 四态可查（`get_activity_state`）；该入口**只支持本仓 addon v1.11+**。
docs/AI-安装提示词.md:95:6. **暂停 AI 操作**（addon v1.11）：N 面板顶部按钮，暂停后写操作被拒绝、只读仍可用；恢复只能由人在 Blender 里点。
docs/AI-安装提示词.md:99:8. **addon 兼容性（重要）**：v3.0 起只支持本仓 [`addon/dsh_blender_bridge.zip`](../addon/dsh_blender_bridge.zip)（包形式，v1.11+ / protocol 11）。
docs/AI-安装提示词.md:110:| 工具数 / op 数与文档不符（如仍是旧目录） | 插件/lib 与宿主版本不一致，或升级后没重启 | 重新安装 v3.0.0 → **重启 DSH** → `blender_rt_plan(op="catalog")` 复核 34/228 |
docs/AI-安装提示词.md:111:| `value is not lossless JSON` | 宿主与插件的回执契约不匹配（旧版或未重载） | 升级到 v3.0.0 后**重启 DSH**，再跑一遍验收 |
docs/给第三方的安装清单.md:8:> **当前版本口径（2026-10-28）**：插件 **v3.0.0** · **15 个模型工具** · plan catalog **34 family / 228 op** ·

## Zip install / breaking change
MIGRATIONS.md:5:> **旧 `MCP for Blender` 自 v2.3 起不再支持**；v3.0 是工程重构 + addon 安装方式 breaking，按下文 §0/§1 迁。
MIGRATIONS.md:10:2. Blender 里停用并移除旧 addon（含旧的单文件 `dsh_blender_bridge.py`）→ 安装本仓 `addon/dsh_blender_bridge.zip`（**v1.11**）→ 启用 → N → Connect。
MIGRATIONS.md:18:## 1. Blender 侧 addon：必须换成 `DSH Blender Bridge` v1.11（zip 包）
MIGRATIONS.md:24:2. `Install...` → 选本仓 [`addon/dsh_blender_bridge.zip`](addon/dsh_blender_bridge.zip) → 勾选启用 **DSH Blender Bridge**（v1.11 / protocol 11）。v3.0 起不再支持单文件安装。
MIGRATIONS.md:60:- **addon v1.11**：拆成包并改为 zip 安装；命令名、参数、返回结构与 pause 语义不变（protocol 11）。
MIGRATIONS.md:80:3. 旧单文件 `dsh_blender_bridge.py`（v2.6.1 及以前）在升级时就必须停用并移除；v3.0.0 不会再加载它。
MIGRATIONS.md:85:2. Blender 里停用 v1.11 zip addon，重新安装 v2.6.2 自带的单文件 `dsh_blender_bridge.py`（或 v2.6.2 的 addon 产物），启用后 N → Connect；
CHANGELOG.md:3:## v3.0.0（2026-10-30）—— 工程健康化重构（breaking：addon 改 zip 包）
CHANGELOG.md:10:- **addon 安装方式改为 zip 包**：发布物是 `addon/dsh_blender_bridge.zip`（包内 `__init__.py`、`bl_info` v1.11、protocol 11）；
CHANGELOG.md:11:  旧的单文件 `addon/dsh_blender_bridge.py` 不再随包发布，也不再是安装入口。升级时必须先在 Blender Preferences 里停用并移除旧单文件，再安装 zip、启用、N 面板 Connect。
CHANGELOG.md:23:- addon 单文件拆为 `addon/dsh_blender_bridge/`（39 个模块，最大 665 行）+ 提交 `addon/dsh_blender_bridge.zip`；launcher 校验包目录/zip、protocol 11、addon version >= 1.10/1.11。
CHANGELOG.md:35:- 真实 Blender 4.2.10 E2E：headless 29 passed、GUI 6 passed（2 个 GUI 条件 skip 由 issue #53 批准）；覆盖 zip 安装、启用、Connect、doctor、selection、bpy、导出、卸载。
CHANGELOG.md:44:- README / zh-README / CONTRIBUTING / zh-CONTRIBUTING / AGENTS / addon README 全部改为 zip 安装；英文 AI 文档保留英文，中文用户文档保留中文并互链。
addon/README.md:5:> **v3.0 起 addon 以包 + zip 安装，单文件 `dsh_blender_bridge.py` 不再是安装入口，且已从本仓删除。**
addon/README.md:6:> 旧单文件安装过的用户请先在 Blender 里停用并移除它，再安装 `dsh_blender_bridge.zip`（见
addon/README.md:14:| 安装包 / 模块名 | `dsh_blender_bridge.zip`（包根 `dsh_blender_bridge/`） / `dsh_blender_bridge` |
addon/README.md:34:## 构建 zip
addon/README.md:36:仓库已提交 `addon/dsh_blender_bridge.zip`；改源码后重新构建：
addon/README.md:39:node scripts/build-addon.mjs          # 写 addon/dsh_blender_bridge.zip
addon/README.md:40:node scripts/build-addon.mjs --check  # 只校验现有 zip 清单与版本
addon/README.md:43:zip 内只有一个包根 `dsh_blender_bridge/`，不含 `__pycache__` / `.pyc`，并使用固定时间戳保证可复现。
addon/README.md:48:   - 如果列表里有旧单文件 addon（模块 `dsh_blender_bridge` 的 `.py` 版本）、旧 `MCP for Blender` /
addon/README.md:50:   - `Install...` → 选本目录的 `dsh_blender_bridge.zip`（不是目录、不是单个 `.py`）。
addon/README.md:55:> 本 addon（目录或 zip 形式的 `dsh_blender_bridge` / `dshbb_*`），并校验 addon v1.11+ / protocol 11。
