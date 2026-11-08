# NOTICE — 来源与许可

本技能（`blender-modeling`）是 MIT 许可项目的派生作品，工具调用已改写为本机 `blender_rt_*` 直连工具链。

## 上游项目

1. **RobLe3/cc-blender-skill** — https://github.com/RobLe3/cc-blender-skill （MIT）
   使用：`plugin/skills/blender-modeling/SKILL.md` 的决策树、8 个配方、三条关键坑、修改器栈序与症状表；`references/overview.md`（本目录 `references/modeling-overview.md`，原文收录）。

2. **arjun988/blender-skills** — https://github.com/arjun988/blender-skills （MIT）
   使用：`.claude/skills/blender-modeler/SKILL.md` 的六步总纲、集合结构、修改器栈哲学与规则、清理清单、MUST DO / MUST NOT DO。

## 本地改动

- `mcp__blender__execute_blender_code` / `get_scene_info` / `get_object_info` → `blender_rt_do` / `blender_rt_headless` / `blender_rt_cmd` / `blender_rt_see` / `blender_rt_job` / `blender_rt_txn`。
- 新增「通道语义」一节（MCP 的「每次重新 import」假设在本机只对 headless 成立）。
- 正文改写为中文，结构精简合并。

MIT 许可要求保留版权与许可声明；完整许可文本见对应上游仓库的 LICENSE。本派生作品同样以 MIT 提供。
