# 让 AI 助手把插件装对（可直接复制的提示词）

**为什么需要这一页**：直接把仓库地址丢给 AI，常见翻车姿势是 —— 跳过 §2 前置条件直接构建；自己编 `blenderExe` / 端口 / 配置项；把"命令跑完了"当成"装好了"；不跑 `doctor` 就宣布成功。
下面这段提示词把「**先读 → 再装 → 按判据验收**」写成硬要求，复制粘贴即可。

> **当前版本口径（2026-10-28）**：插件 **v2.6.2** · **15 个模型工具** · plan catalog **34 family / 228 op** ·
> Blender 侧 addon **v1.10**（protocol **11**）· 后端 `127.0.0.1:9877` / addon `127.0.0.1:9876`。
> **验收判据**：GUI 路线 = `doctor` 回 `kind=ok`；纯无头路线（不装 addon）= `rt_worker(start)` + `see` 出图自证
> （doctor 的 bpy 往返不可达属预期）。**GUI 不再必需**：交互式无头（持久 worker + see/watch/reset）可独立建模。

---

## ① 通用提示词（任何 AI 助手）

~~~text
请帮我安装这个 DSH 插件：https://github.com/sixtysevenlf/dsh-blender-plugin

动手前的要求（必须遵守）：
1. 先完整阅读仓库里的 README.zh-CN.md：§2 前置条件（5 条）、§3 安装（5 步）、§8 常见故障，以及 AGENTS.md。
2. 逐条核对 5 条前置条件；缺哪条先告诉我，不要跳过，也不要替我决定。
   当前版本：插件 v2.6.2 / 15 个模型工具 / plan catalog 34 family 228 op / addon v1.10（protocol 11）。
   注意：仓库已提交 lib/，只有要改 src/ 时才必须构建。
3. 严格按 §3 的 5 步执行；配置项只从 docs/配置参考.md 里取，不要自己编路径 / 端口 / 配置名。
4. 验收判据（按路线二选一，另一条不适用时说明原因）：
   · GUI 路线：curl -sS http://127.0.0.1:9877/doctor → kind=ok，且 blender_rt_see(max_size=560) 回一帧图；
   · 纯无头路线（不装 addon）：blender_rt_worker(op="start") + op="see" 回一帧图
     （doctor 的 bpy 往返不可达属预期，不是没装好）；
   两条路线都要：blender_rt_plan(op="catalog") 能列出 34 family / 228 op。
   没到对应判据不许说"安装成功"；失败就按 §8 的表逐条排查。
5. 每一步都把原始回执贴给我，不要只给结论。
6. 装完并验收通过后，必须主动告诉我当前版本能力（照 §③.5，用你自己的话复述，不要漏）：
   交互式无头（rt_worker see/watch/reset/preload/warmup、rt_do/rt_see 可 session/auto、
   plan session="auto" 同步无头路由、worker 空闲 TTL）、参考图量具 img_digest/probe/measure/calib、
   装配 asm_scan/manifest/merge/repair/export/gate、白模 blockout base/batch/export/turntable/refsheet、
   同机位 QC visualreport_report、无头轮廓拟合 shapesolve_solve、缺陷评估 eval_run、
   强制门 gate_preflight/checkpoint/deliver + deliver_export(require_gate)。
   并明确说明「landmark/人脸（无 cv2/MediaPipe）、OCR、DXF、Open3D/manifold3d/glTF-Validator 未安装，不在支持范围」。
~~~

## ② DSH 用户（仓库已在本地工作区）

~~~text
读 dsh-blender-plugin/README.zh-CN.md 的 §2 / §3 / §8 与 AGENTS.md，
按 §3 的五步把这个插件装进我的 DSH profile（构建（可选）→ 配置 → 装进 profile → 起 Blender → 验证）。
装完跑 blender_viewport(op="doctor")，kind=ok 才算成功；不是 kind=ok 就按 §8 排查，并把原始回执贴给我。
（纯无头路线不装 addon 时，doctor 的 bpy 往返不可达属预期：改用 blender_rt_worker(op="start") + op="see" 出图自证。）
验收通过后，请主动告诉我当前版本（v2.6.2）的新能力：
· 交互式无头：rt_worker 持久 worker（see/watch/reset/preload/warmup，空闲 TTL 默认 15 min）、
  rt_do/rt_see 在 GUI 不可达时自动走 session/auto、plan session="auto" 同步无头路由；
· 曲面算法（v2.6.2 新增）：surface_loft/sweep/patch → tessellate → validate → export（M0–M2/M4；M3 fit、M5 blend/offset 未实现）；
· 参考图量具 / 装配 / 白模与转台/refsheet / visualreport / shapesolve / eval_run / 强制门（详见 catalog）；
· Blender 原生选中 → N 面板「发送选中到 DSH」仍可用（v2.3 起；addon 需 v1.10+）；
· addon v1.10 起 N 面板有「暂停 AI 操作」（恢复只能人在 Blender 点）；
· 不覆盖：landmark/人脸、OCR、DXF、Open3D/manifold3d/glTF-Validator（未安装）。
并说明升级插件后必须重启 DSH、Blender 侧 addon 要 v1.10+（旧 MCP for Blender 自 v2.3 起不再支持）。
~~~

## ③ 装完让 AI 自检（GUI / 无头两套）

~~~text
blender_viewport(op="doctor")             # kind=ok ？（真跑一次 bpy 往返）
blender_rt_plan(op="catalog")             # 34 family / 228 op 能列出来？
skill(name="blender-modeling")            # 随包 skill 能加载？（v2.2.0 起自动注册，无需另装）
blender_rt_cmd(name="get_addon_info")     # addon_version=[1,10] + protocol 11？
blender_rt_cmd(name="get_activity_state") # 选中/activity 命令在不在？
blender_rt_cmd(name="get_collections")    # 集合模式的数据源在不在？

# GUI 不可用时（交互式无头；不需要打开 Blender 窗口）
blender_rt_worker(op="start")             # 持久 blender -b（冷启动 ~2s）
blender_rt_worker(op="see", from="4,-4,3", look_at="0,0,1")  # 出图 ~50–100ms
blender_rt_do(code="print(K.out_dir)", session="auto")       # 同步 exec，不占 GUI
~~~

## ③.5 装完必须告知主人（v2.6.2 的"顺手通知"要求）

> 这一节是给**安装插件的 AI** 看的硬要求：验收通过后，用你自己的话（中文优先）把下面几点告诉用户，不要省略。
> 用户装插件通常只关心"能干什么"，不会去翻 CHANGELOG —— 所以由你转达。

1. **版本口径**：插件 v2.6.2、15 个模型工具、plan catalog 34 family / 228 op、addon v1.10（protocol 11）；
   装完/升级后必须**重启 DSH**（插件是启动时装载的模块）。
2. **交互式无头（v2.6 重点）**：`blender_rt_worker` 是常驻 `blender -b` 热会话（`start/exec/see/watch/reset/stop`，
   支持 `preload`/`warmup`，空闲 TTL 默认 15 min，回执带 `idleMs`）；`blender_rt_do`/`blender_rt_see` 在 GUI 不可达
   或显式 `session` 时自动走无头；`blender_rt_plan(..., session="auto")` 按 family 自动起 worker 并同步返回。
   **GUI 不再是必需条件**，但 GUI 可用时仍是最顺手的看图方式。
3. **曲面算法（v2.6.2 新增）**：`surface_loft / surface_sweep / surface_patch → surface_tessellate →`
   `surface_validate → surface_export`；纯 bpy+numpy，单位 mm、同 spec 可复现；verdict 三态（`fail` 不静默成 pass，
   `degraded` 不等于通过）。**M3 fit、M5 blend/offset 未实现**（明确 `ok:false`，需 OCCT）。
4. **新能力面（P0–P2）**：参考图量具 `img_digest/probe/measure/calib`；装配 `asm_scan/manifest/merge/repair/export/gate`；
   白模 `blockout_base/batch/export/turntable/refsheet`；同机位 QC `visualreport_report`（verdict-first）；
   无头轮廓拟合 `shapesolve_solve`；缺陷评估 `eval_run`；强制门 `gate_preflight/checkpoint/deliver` +
   `deliver_export(require_gate=true)`；`blender_rt_watch` 也支持无头。
5. **选中入口仍在 Blender 侧**（v2.3 起）：原生选中 → `N` →「DSH Blender Bridge」→ **Selection → DSH**；
   多选 / 集合两种模式；编辑模式点/线/面、姿态骨骼、曲线/晶格点也会随选中一起发送；
   activity 四态可查（`get_activity_state`）；该入口**只支持本仓 addon v1.10+**。
6. **暂停 AI 操作**（addon v1.10）：N 面板顶部按钮，暂停后写操作被拒绝、只读仍可用；恢复只能由人在 Blender 里点。
7. **不覆盖 / 未安装依赖（请如实告诉用户）**：landmark/人脸（无 cv2/MediaPipe）、OCR、DXF、
   Open3D / manifold3d / glTF-Validator 均未安装，相关能力不在本插件支持范围；catalog 的"不覆盖声明"也写明
   生物/有机造型、电影级布光/体积/合成、角色动画绑定不覆盖。
8. **addon 兼容性（重要）**：v2.3 起只支持本仓 [`addon/dsh_blender_bridge.py`](../addon/dsh_blender_bridge.py)（v1.10+）。
   若装的是旧 `MCP for Blender` / `blender_mcp`，先停用再装本仓 addon；否则「Selection → DSH」与模型工具都可能连不上。
   —— 请把这条如实告诉用户，并给出迁移步骤（[MIGRATIONS.md](../MIGRATIONS.md)）。

## ④ 什么算失败（拿到这些回执就别宣布成功）

| 回执 | 含义 | 处置 |
|---|---|---|
| `blender-unreachable` | Blender 没跑，或 addon 没 Connect | `blender_viewport(op="launch")`；或人工在 Blender 里按 `N` → Connect；纯无头可改用 `blender_rt_worker` |
| `main-thread-busy` | 主线程被渲染 / 模态操作占住 | 等它空下来，或改走无头（`blender_rt_headless` / `blender_rt_worker`） |
| 后端不可用 | 后端进程没起来 | `blender_viewport(op="start")` |
| 工具数 / op 数与文档不符（如仍是旧目录） | 插件/lib 与宿主版本不一致，或升级后没重启 | 重新安装 v2.6.2 → **重启 DSH** → `blender_rt_plan(op="catalog")` 复核 34/228 |
| `value is not lossless JSON` | 宿主与插件的回执契约不匹配（旧版或未重载） | 升级到 v2.6.2 后**重启 DSH**，再跑一遍验收 |
| 写操作 409 `leased` | 别的会话持有写租约 | `blender_viewport(op="who")`，别强抢 |

## ⑤ 给仓库维护者：让 AI"先读 README"的四个抓手

1. **`AGENTS.md`（本仓库根目录）** —— DSH / Claude Code / Cursor / Codex 等助手在仓库内工作时**自动加载**，是最硬的一道。
2. **README 首屏的一段话** —— 用 AI 的人通常是把仓库地址直接丢给助手，助手第一件事就是读 README 首屏。
3. **失败回执指向文档** —— 让"没读 README 的人"在最容易卡住的地方（通道连不上）被明确告知去读哪一节。
4. **提示词模板（本页）** —— 由用户粘贴，任何助手都吃这一套，不依赖助手是否支持约定文件。
