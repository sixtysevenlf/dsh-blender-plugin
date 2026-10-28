# CLAUDE.md

见 [AGENTS.md](AGENTS.md) —— 本仓库的 AI 助手约定。

最要紧的几条：

1. **安装 / 配置 / 排查前先读** `README.zh-CN.md` 的 §2 前置条件、§3 安装（构建可选）、§8 常见故障（英文见 `README.md` 的 §9）。
   「装好了」的判据：**GUI 路线 = `doctor` 回 `kind=ok`**（`curl -sS http://127.0.0.1:9877/doctor` 与 `blender_viewport(op="doctor")`）；
   **纯无头路线**（不装 addon）用 `blender_rt_worker(start)` + `see` 出图自证（doctor 的 bpy 往返不可达属预期）。
2. **当前版本 v2.6.2**：**15 个模型工具** · plan catalog **34 family / 228 op** · addon **v1.10**（protocol **11**）。
   升级插件后必须**重启 DSH**；旧 `MCP for Blender` 自 v2.3 起不再支持（迁移见 [MIGRATIONS.md](MIGRATIONS.md)）。
3. **GUI 优先（无头备用）**：默认先开 GUI Blender + Connect。无头是备用：`blender_rt_worker`（`start/exec/see/watch/reset`、`preload`/`warmup`、空闲 TTL），
   `blender_rt_do/rt_see` 支持 `session/auto`，`blender_rt_plan(session="auto")` 同步无头路由；只有长活/批处理/无 GUI 才走
   `blender_rt_headless` / `blender_rt_job`；无头也能 rt_see。
4. **随包 skill 真源**是 `skills/blender-modeling/`（当前 v2.6，GUI 优先（无头备用））；改 skill 直接改这里，
   独立仓库 `dsh-skill-blender-modeling` 仅作非 DSH 宿主的镜像。
5. **不覆盖 / 未安装依赖**：landmark/人脸（无 cv2/MediaPipe）、OCR、DXF、Open3D/manifold3d/glTF-Validator
   均未安装 —— 不要写成已支持。
6. 工具名 / op 名不确定时先 `blender_rt_plan(op="catalog")`（34/228，本地直出）；写错会回 `did_you_mean`，照骨架改。
