# 迁移与路径变更记录

> 若你的环境基线文档里出现"旧路径"，对照本表。**旧路径均已删除**，只在原址留指路 README。

| 时间 | 变更 | 旧 | 新 |
|---|---|---|---|
| 2026-09-13 | 插件独立成包并改名 | `plugins/dsh-blender-viewport/`（包名 `@dsh-external/dsh-blender-viewport`） | **`dsh-blender-plugin/`**（包名 **`@dsh-external/dsh-blender-plugin`**）；运行时从 `blender/viewport/` 迁到 **`dsh-blender-plugin/runtime/`** |
| 2026-09-13 | MCP / CLI 通道废弃 | `blender/cli-tools/blender-mcp`、`D:\DSH\blender\venv`、`~/blender-mcp`、`blender/viewport/ui.html`、`tools/win_input_daemon.ps1` | 全部删除；模型侧只剩直连通道的 9 个工具 |
| 2026-09-13 | 后端进程 | 由旧插件 `plugins/dsh-blender-viewport` 拉起 | 由 `dsh-blender-plugin` 拉起（`runtime/server.mjs --port 9877`），带 15 s 看护 |
| 2026-09-13 (v0.4.0) | 取帧参数：自定义视角 | 旧版"换角度 = 改用户视口 / 建相机对象"（或 CLI 的 `rotate_view_generate_image`） | `blender_rt_see {from, look_at, lens, ortho, ortho_scale, view_size, shading, overlays, view_mode}`；零场景改动（`runtime/view.py`） |
| 2026-09-13 (v0.4.0) | 长任务通道 | 在 GUI 会话里硬跑 `render.render()`（会撞 CUDA context，且占满主线程） | `blender_rt_headless {script, file, outdir, args, timeout_ms, factory_startup, bootstrap, preload}` 独立进程 |
| 2026-09-13 (v0.4.0) | 并发保护 | 无（多个会话/agent 直接互相踩） | 写通道租约：`/lease` `/release` `/who` + `blender_viewport {op:"who|lease|release"}`；只读 op 豁免 |
| 2026-09-13 (v0.4.0) | 健康检查 | `/health` 只能说"后端活着" | `/doctor` + `{op:"doctor"}`：真跑 bpy 往返，三级判定 + `fix` |

## 环境基线文档该读哪份

- 总说明：`dsh-blender-plugin/README.md`
- 机制/踩坑：`dsh-blender-plugin/docs/AI实时交互Blender-通道说明.md`
- 建模方案（内环）：`dsh-blender-plugin/docs/AI建模双层循环-方案.md`
- 工作区约定：作者工作区的 `AGENTS.md` §2（等价内容见本包 `README.md` + `docs/操作教程.md`）

## 注意（曾经踩过）

- **Blender 里 addon 的 socket 没起来时**，后端照常运行（`/health` ok），但所有 rt 工具会报错 —— 先看 `blender_viewport {op:"doctor"}` 的三级判定。
- **GUI 会话里不要连续 `render.render()`**（见 README「已知限制」）；长渲染用 `blender_rt_headless`。
- **旧文档里若写租约是"顾问式/不阻塞"或参数叫 `resolution`**：那是最初的设计稿口径。实际实现是：写路由会返回 **409 并挡下**（`force:true` 可抢），自定义视角分辨率参数叫 **`view_size`**（"宽x高"）。见 CHANGELOG v0.4.0。
