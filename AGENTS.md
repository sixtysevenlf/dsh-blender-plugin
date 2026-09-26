# AGENTS.md —— 给 AI 助手的仓库说明

> 这个文件会被 DSH / Claude Code / Cursor / Codex 等助手**自动加载**。动手前先读完（约 1 分钟）。
> 人类读者请直接看 [README.zh-CN.md](README.zh-CN.md)（[English](README.md)）。

## 0. 硬规则：先读 README，再动手

凡是**安装 / 部署 / 配置 / 排查**类任务，先完整读 `README.zh-CN.md`（或 `README.md`）的这三节：

| 读哪节 | 为什么 |
|---|---|
| **§2 前置条件（5 条）** | 缺一条都装不起来。注意：Blender 侧的 addon `MCP for Blender` **不在本包内**，必须用户自备并在 Blender 里 Connect |
| **§3 安装（5 步）** | 构建 → 配置 → 装进 profile → 起 Blender → 验证 |
| **§7 常见故障** | 报错先查表，别自己发明修法 |

**不要凭直觉编**：路径、端口（addon `9876` / 后端 `9877`）、配置项名，一律从 `docs/配置参考.md` 取。
工具名 / op 名不确定时先 `blender_rt_plan(op="catalog")`（本地直出，不占往返）；写错会回 `did_you_mean`，照它给的骨架改。

## 1. 「装好了」的判据（不是「命令跑完了」）

三条全绿才算装好；缺一条就是没装好，按 §7 排查：

1. `curl -sS http://127.0.0.1:9877/doctor` → `kind=ok`
2. `blender_viewport(op="doctor")` → `kind=ok`
3. `blender_rt_see(max_size=560)` → 回一帧图（而不是一段报错文本）

**没到 `kind=ok` 不要对用户说「安装成功」。** 典型失败与含义：

| 回执 | 含义 | 处置 |
|---|---|---|
| `blender-unreachable` | Blender 没跑，或 addon 没 Connect | `blender_viewport(op="launch")`；或人工在 Blender 里按 `N` → Connect |
| `main-thread-busy` | 主线程被渲染 / 模态操作占住 | 等它空下来，或改走无头 |
| 后端不可用 | 后端进程没起来 | `blender_viewport(op="start")` |
| `value is not lossless JSON` | 版本旧（< v0.9.4） | 升级后**重启 DSH** |
| 写操作 409 `leased` | 别的会话持有写租约 | `blender_viewport(op="who")`，别强抢 |

## 2. 装完 / 改完之后的规矩

- **插件升级后必须重启 DSH**（插件是 DSH 启动时加载的模块）；只改了后端才用 `blender_viewport(op="restart")`。
- 长任务（> 1 min、长渲染、批量几何）走 `blender_rt_job(op="start"/"wait")` 或 `blender_rt_headless(as_job=true)`；
  **别用同步 headless 硬等** —— 客户端超时 ≠ 任务失败，按 `runId` 回收结果。
- 拓扑类改动要回滚，用 `blender_rt_txn` 的文件级 `snapshot/restore`；对象级 `mark/revert` **不含**拓扑 / UV 改动。

## 3. 改这个仓库本身

- 构建：`DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh`（`src/` → `lib/`）。
- 离线自检：`npm test`；真机验收：`npm run test:acceptance`（需要 Blender 在跑）。
- 改 `runtime/*.py` 前先看 `tests/README.md`；每个能力族都有对应的 `*_selftest`。
- 文档事实源在包内：`README.zh-CN.md` · `docs/操作教程.md` · `docs/配置参考.md` · `CHANGELOG.md`。

## 4. 给用户的一段提示词

[docs/AI-安装提示词.md](docs/AI-安装提示词.md) 是可直接粘贴给任意 AI 助手的安装提示词（含验收判据与失败对照表）。
