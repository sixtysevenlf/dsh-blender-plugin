# 文档归档

> 本目录保存**已被取代**的历史文档快照，只作追溯证据，**不是当前口径**。
> 其中的过时计数（如 27 family / 176 op）、已删除路径（如 `runtime/audit.py`、`runtime/gate.py`）、
> 旧版本号与哈希均按原样保留，**不要据此判断现状**。

## 归档清单

| 文件 | 时期 / 性质 | 替代者（当前口径） |
|---|---|---|
| [插件与技能优化验收.md](插件与技能优化验收.md) | v0.9.6 独立验收快照（2026-09-26）；计数与路径均已过时 | [AGENTS.md](../../AGENTS.md) §1–§7、[docs/architecture.md](../architecture.md)、`blender_rt_plan(op="catalog")` |
| [headless优先-路线决定.md](headless优先-路线决定.md) | v0.9.3 历史路线建议（headless 优先），已被 **GUI 优先**取代 | [docs/操作教程.md](../操作教程.md) §7、[README.md](../../README.md) |
| [v2.6.0-更新说明.md](v2.6.0-更新说明.md) | v2.6.0 发布说明（2026-10-21），已被后续版本取代 | [CHANGELOG.md](../../CHANGELOG.md)、GitHub Releases |

## 归档策略

- 只归档**被新文档或新版本明确取代**、且仍有追溯价值的文档；归档前在原位置加上"历史 / 已过时"横幅。
- 归档文件是**冻结快照**：不再更新，正文、计数与链接保持原样（内部链接可能失效）；不得把归档内容当作现行指令。
- 当前文档不得把本目录中的文件当作现行口径引用；确需引用历史时，写明"历史归档"并给出替代者。
- 新增归档必须在同一变更的 CHANGELOG 或 openspec change 中登记，并更新上表。
- 当前事实来源：[README.md](../../README.md)、[AGENTS.md](../../AGENTS.md)、[docs/architecture.md](../architecture.md)、
  [CHANGELOG.md](../../CHANGELOG.md) 与 `blender_rt_plan(op="catalog")`。
