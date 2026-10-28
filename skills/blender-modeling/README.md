# blender-modeling（随插件捆绑）

> **这份 skill 已经并进插件仓库**（`@dsh-external/dsh-blender-plugin` 的 `skills/blender-modeling/`），
> 从 **v2.2.0** 起由插件在 DSH 启动时**自动注册为 bundled skill** —— 装插件就自带，
> **不需要**再单独 clone / 下载 / 软链。

## 用户怎么用

- 安装插件并**重启 DSH** 后，会话里的技能目录（`skill` 工具的 available skills 列表）会直接出现 `blender-modeling`；
  模型遇到「建一个 / 捏一个 3D 物体」「按参考图还原外形」「多 agent 分工造一台机器」「装配体检」这类任务时会自动加载。
- 手动确认：在会话里让模型 `skill` 加载 `blender-modeling`，或看技能目录里是否有这一条。
- **优先级**：如果你此前已经把独立仓库装进了 `~/.dsh/skills/blender-modeling`（user 级），它会**优先于**本包内的 bundled 副本
  （DSH 的 rank 规则：user 400 < bundled 600）；两份内容一样，不影响使用。想强制使用包内版本，删掉用户级目录/软链即可。

## 不装插件、只想把 skill 给别的宿主（Claude Code / Codex / Cursor…）

包安装目录就是一份标准 `skills/<name>/SKILL.md` 目录，直接软链或拷贝即可：

```bash
# 找到本包安装位置（示例：DSH profile 的 node_modules）
PLUGIN="$(dirname "$(node -e "console.log(require.resolve('@dsh-external/dsh-blender-plugin/package.json'))")")"
mkdir -p ~/.dsh/skills
ln -s "$PLUGIN/skills/blender-modeling" ~/.dsh/skills/blender-modeling   # 或者 cp -r
```

> ⚠ `SKILL.md` **必须保留 YAML frontmatter**（`name` / `description` / `license`）——加载器靠它建技能目录。

## 内容（v2.6 · 架构优先）

- `SKILL.md` —— **插件架构优先的建模/装配作业指导书**：先 catalog → plan op → worker session → 回执/verdict → 门/交付；含标准状态机、工具选择矩阵、参考图量测、装配修复/门/导出、白模与 Seedance 参考包、同机位 QC、缺陷评估、无头 watch、多 Agent 协议与反模式。通用 Blender 配方降为附录 A（深水参考，不是首选路径）。
- `references/modeling-overview.md` —— 上游英文原文收录（深水参考；不替代插件架构）。
- `references/multiview-calibration.md` —— 三视图联立标定脚本（可 `--selftest`；与 img_calib/shapesolve 配套）。
- `references/参考图通用还原SOP.md` —— 参考图还原九步（优先 img_digest/probe/measure/calib；无标定不写 mm）。
- `references/验收清单.md` —— 独立验证子代理专用（优先 audit/asm_gate/gate_run/visualreport/eval；约 4.2k 字符）。
- `references/曲面算法.md` —— surface_* 曲面包速查：loft/sweep/patch → tessellate → validate → export，verdict 三态与 M3/M5 限制。

> **架构优先**：有 plugin op 就不许手写等价几何/验收；catalog 里没有对应能力才写 bpy，并且仍必须回插件门验收。

## 许可与来源

- 本目录内容：**MIT**（见 [LICENSE](LICENSE)），Copyright © 2026 sixtyseven67。
- 派生自两个 MIT 项目（决策树 / 配方 / 坑表 / 六步总纲等），工具调用已改写为本插件 `blender_rt_*` 直连链：
  [RobLe3/cc-blender-skill](https://github.com/RobLe3/cc-blender-skill) ·
  [arjun988/blender-skills](https://github.com/arjun988/blender-skills)。
- 完整归属与本地改动见 [NOTICE.md](NOTICE.md)。
- 独立分发镜像（历史仓库，内容与本目录同步）：<https://github.com/sixtysevenlf/dsh-skill-blender-modeling>。

---

**同步口径**：v2.6（2026-10-21）已按插件架构重写 SKILL.md 并校正 references；此后**以本仓库为真源**维护，独立仓库仅作镜像/历史。
