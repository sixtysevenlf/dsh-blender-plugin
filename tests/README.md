# 验证脚本（可复跑）

> 这些检查把 v0.4.0（自定义视角 / 无头 / 租约）的关键结论固定下来，换机器或升级 Blender 后**先跑它们**。

## 宿主集成自检（v1.1.0 · 不需要 Blender 的那一条可单独跑）

```bash
node tests/client_bundle_selftest.mjs   # 客户端半边：假 window.__ModuleLoader__ 下装载 lib/client.js
node tests/host_routes_selftest.mjs     # /host/* 路由（需要后端 127.0.0.1:9877；Blender 不在则 SKIP 且 exit 0）
```

- **`client_bundle_selftest.mjs`**：这是"**用户重启 DSH 后插件会不会崩**"的安全网。断言注册 `id` ===
  `@dsh-external/dsh-blender-plugin`、`factory(require)` 返回带 `apply`(函数)/`inject`(字符串数组)、
  bundle 内无顶层 `import`/`export`/`await`、不引 Node 内置模块；另按契约断言轮询节流常量与 §2.4/§2.5 文案。
  实测 29 项全绿；对 6 类蓄意违规 fixture（id 错 / 缺 apply / 顶层 import / `require('fs')` / factory 抛 / 语法错）
  **全部 exit 1**。`lib/client.js` 缺失时打印 `SKIP` 且 exit 0。
- **`host_routes_selftest.mjs`**：对 `127.0.0.1:9877` 打契约 §4 判据 1–8（CORS 预检与 loopback 白名单、
  capture→PNG 魔数、陈旧 seq 409、pick 命中/空处/错配、`scene-items` 过滤与 `limit` 夹取）。
  Blender 不在、后端不可达、或后端未装载 `/host` → **SKIP 且 exit 0**（不算失败）。
  判据 7 需临时改场景名构造错配：默认开、`finally` 还原并核验 stdout、`DSH_HOST_TEST_NO_MUTATE=1` 可关、
  写租约被别的会话持有时**不夺租约**而是跳过（共享同一 Blender 时的有意取舍）。
- 两条都不进 `npm test` 主链（`host_routes` 依赖真后端），需要时手动跑。

## 工具卡片自检（v2.6.2 · 进 `npm test`）

```bash
node tests/tool_cards_selftest.mjs      # 15 个工具的 presentCall / presentResult（不需要 Blender，不起后端）
```

- 宿主（dsh-TUI / Web）用 `presentCall` / `presentResult` 决定**卡片怎么给人看**；模型侧回执（`render`）不变。
- 断言：15 个工具都挂了 presenter；标题「Blender <动作> · <要点>」；带代码的调用把代码放进卡片正文；
  结果卡片去掉单行 JSON 信封、摘要在前、代码在后、图片块原样透传；出错 / 没东西可改时回 `undefined`（宿主默认显示）；
  `render` 输出仍以信封开头；坏参数不抛、view 全是 lossless JSON。

## dsh-TUI 宿主半边自检（v2.6.2 · 进 `npm test`）

```bash
node tests/tui_host_selftest.mjs        # src/tui-host.ts：假宿主接缝 + 假后端 + 一个真 runtime/server.mjs（不需要 Blender / dsh-TUI）
```

- 草稿文本与 `lib/client.js` **逐字对拍**（16 组选中 payload）：两份实现，靠这里防漂移。
- 选中桥 CAS：挂上才 `consume(ok)`、挂不上第 3 次 `consume(ok:false)`、空选中直接 `consume(ok:false)`、同一 seq 不重复挂。
- 页脚字段的每种状态、`@` 提及条目、`/blender status|jobs|doctor`、接缝缺失时零注册、卸载清理。
- 真后端 `/host/state`：无 Blender 时的形状与「未连接」；再挂一个假 addon，验「AI 操作已暂停」→ `aiPaused=true` → 成功一次后清空。

## Windows 子进程藏窗自检（v2.6.2 · 进 `npm test`）

```bash
node tests/windows_hide_selftest.mjs     # 静态扫描 runtime/*.mjs 的 child_process 调用（不需要 Blender / 不需要 Windows）
```

- 规则：Windows 上会创建控制台的 child_process 调用必须带 `windowsHide: true`；GUI 启动 `runtime/launch.mjs`（`detached: true` ⇒ libuv `DETACHED_PROCESS`）允许用 `detached: true` 替代。
- 背景 issue #38 / #43：`spawn` 族修了、同族的 `execFileSync` 漏了 ⇒ 用静态扫描把**整个 `runtime/*.mjs` 面**锁住，而不是只修当前这几处。
- 实现：先遮掉字符串 / 注释 / 正则字面量（`/['"]/` 这类不会把后面代码带偏），再做括号配对；`exec(compile(...))` 这种字符串里的 Python 代码不会被误判成调用。
- 反例实测：删掉 config / engine 的 `windowsHide`、删掉 launch 的 `detached` 均 exit 1，并指到文件:行。

## 上游整合 A1–A5 自检（v0.9.6）

``python
blender_rt_headless(preload="audit,sculpt,mesh_fix,uv_tools,printcheck,sweep,material,render_guard,gate,human,vehicle,clearance,shapegen,faceeval,imgtools,pipeline", engine="none",
    factory_startup=True, timeout_ms=300000,
    script="_p=K.win_path('<包路径>/tests/upstream_integration_selftest.py');"
           "exec(compile(open(_p,encoding='utf-8').read(),_p,'exec'),globals())")
``

> ⚠ preload 必须覆盖 main() 里跑到的**全部**能力面。旧示例只写前 6 个 —— 实测会红 2 条
> （`t_pipeline` / `t_refs_single_op` 报「模块没注入：K.dsh_pipe_api」），那是示例漏项、不是插件缺陷。
> 完整清单见上面这行（`pipeline` 最容易漏）。

128 项断言（2026-10-07 实测 · 全绿；v0.9.6 起含材质、渲染 guard、D3 瘦身门、D5 门包/分岛/三态、D6 相贴判据），**实测数字写死**（不达标改代码，不改断言）：雕刻位移笔刷真的动了网格、对称是独立笔触、
体素预算守卫拦得下过小的 voxel_size、fix_repair 修完自证、空网格不再让 audit_mesh 崩（回归）、
UV 零面积面=0、薄板壁厚 5mm ±0.5、粗网格必须给 resolution_mm 警告、直管 38 顶点/48 面、过紧路径被拒、
**print（issue #14）：平底薄板底面判为贴床面（净悬垂 0 / 老口径字段仍给 40000 / 默认过门）、同批离台 50mm 的
板底面仍计 10000mm²、`print_selftest` 六形状自证（平底立方体 / 悬臂 / 平底碎块 / 蘑菇帽不许假绿 / 斜面不许当
贴床 / 单件 T 的 overhang_ratio 分母）—— 「真悬垂不许被平台面吞掉」这条有回归锁**。
材质：metal_brushed 建图（节点/连线 ≥5）→ 套用 → scan 报 procedural/needs_uv → AO 128px 烘出贴图（bytes>0）；
渲染：render_guard install/mark/clear —— 磁盘标记出现又消失，handler 数量 ≥1；
瘦身：`audit_scene(summary_only, top_k)` 判定不变（clean/totals 一致）、字符省 ≥70%、40 行 → top_k 行；`audit_mesh` 去明细留判据。
跑完断言"临时对象已清干净"（场景对象数复原）。

> 走 `exec(open(...))` 跑别的测试脚本时记得**先设 `__file__`**（例如 plan_selftest.py 用
> `os.path.dirname(__file__)` 找 runtime/，不设会指到无头临时目录上去）。

## 描述预算自检（v0.9.6 · D2，与 D1 同一个测试文件，共 51 项）

- **度量口径**：只量"真正发给模型的" `description + parameters`（execute 里的代码字符串不算 schema）
- **基线对比**（同口径实测）：v0.9.1 = 13,671 字符（最大工具 2,829）· v0.9.4(HEAD) = **17,147**（最大 4,020）· 现在 = **11,962**（最大 1,560）
- **硬门**：单工具 < 1600 字符 · 15 个合计 <= 12000 字符 —— 超了测试直接红，防止描述再膨胀回"散文大海"
- **长尾去哪**：`blender_rt_plan(op="catalog", args={tool:"rt_headless"})`（后端本地直出，7 个重工具都有条目），
  与 `op="catalog"` / `args={family:"xxx"}` 构成三级渐进披露：工具索引 → family → 算子细节

## 工具可发现性自检（v0.9.6 · D1，不需要 Blender）

```bash
node tests/discoverability_selftest.mjs      # 或 npm run test:discover（已并入 npm test）
```

51 项断言（含下节的预算门），全部在本机跑（不需要 Blender / DSH）：
- **catalog 体量**：**>= 26 family**（现为 27）且含 sculpt/fix/uv/print/sweep/vehicle/gate/material/render_guard/generator；
  默认短文本 3k–9600 字符**且每个 family <= 360 字符**（按家族摊，避免"加一族就得改常数"）；单族展开 < 1500；只有 `full:true` 才给结构化明细
- **不写死家族数**：v0.9.6 前这里写 `=== 26` / `detail.length === 26`，而磁盘早已 27 → 测试自己先假失败
- **参数名护栏**：generator 骨架必须是 `code=`（不是 `script=`）、`generator_run` 必须是嵌套 `args=`（照抄旧目录会当场炸）
- **目录自证（内容指纹）**：`catalogPayload()` 回 `provenance`（`loadedHash` / `currentHash` / `verdict` / `stale`）；
  `args={verify:true}` 起干净 node 进程 import 磁盘 engine.mjs 当裁判（`diskVerdict.matchesLoadedCatalog`）；
  目录文本带一行"目录自证：loaded … · 磁盘 … · 一致"，不一致时点名 `⚠ 目录自证 FAIL`
- **rt_plan 长尾**：`args={tool:"rt_plan"}` 里的 family/op 数由 `PLAN_CATALOG` 现算（旧版硬写"16 family / 117 op"，早就过期）
- **未知 op 最近邻**：`audti_mesh → audit_mesh`（带骨架）· `sculpt_appli → sculpt_apply` · `qc_render_view → qc_render_views` · 乱码不硬猜
- **跨通道纠错**：把 `audit_mesh` / `qc_render_catalog` 当 addon 命令发给 `rt_cmd` ⇒ 回 `CMD_HINT` 指回 `blender_rt_plan`；真 addon 命令（`get_scene_info`）不误判
- **描述预算**：`blender_rt_plan` 的 description 必须 < 2000 字符（压缩前 4,708）—— 防止再膨胀回"散文大海"
- **真 HTTP 路由**：起一个隔离后端，验证 `/plan op=catalog`（含 `args={verify:true}`）在**没有 Blender** 时也能回目录、`/cmd` 的纠错在本地完成

## workDir 共享性自检（v0.9.6 · D3，不需要 Blender）

```bash
node tests/workdir_shared_selftest.mjs       # 或 npm run test:workdir
```

14 项。判据是**挂载表**，不是"Node 能不能写"（后者永远说能）：
- `/tmp`、`/run`、`/dev/shm`（独立挂载）→ Windows 的 blender.exe **看不到** → `shared=false`
- 发行版根文件系统（`/home/…`、`/var/…`）→ `\\wsl.localhost\<distro>\…` 可见 → `shared=true`；`/mnt/<盘>/` → 驱动器盘符 → true
- `describeConfig().workDir.shared` 是自证入口（`/doctor` 能查）
- `writeHeadlessScript` 在不共享时**必须换目录**并回 `staging.substituted/note`（回执另加 `WORKDIR_NOT_SHARED` pathWarning）

## 加载版本自证（v0.9.6 · D3，不需要 Blender）

```bash
node tests/provenance_stale_selftest.mjs     # 或 npm run test:provenance
```

16 项。把 `runtime/*.mjs` 拷到临时目录、对**副本**动手（绝不改真源码）：
- 刚加载 → `verdict=current`；**只 `utimes` 改 mtime、内容逐字节不变 → 仍必须 current**（"mtime 不能冒充加载版本"）
- 真改内容 → `verdict=stale`，`staleReason` 同时给出 loaded/current 两个指纹
- `verify:true` 的干净进程裁判：只加注释 → 目录指纹不变（`matchesLoadedCatalog=true`）；改了一族名 → 进程内目录被判旧版，文本出 `⚠ 目录自证 FAIL`

## generator 参数走廊自检（v0.9.6，不需要 Blender）

```bash
node scripts/pyrun.mjs tests/generator_dispatch_selftest.py   # 或 npm run test:generator
```

13 项：`generator_save` 的参数名是 **code**（`script=` 被明确拒绝并给出正确清单）；`generator_run` 的
**嵌套 `args` 是真参数、不被 dispatch 摊平**（旧实现把 `{"name":"x","args":{"n":3}}` 摊成 `generator_run(name="x", n=3)`，
PARAMS 静默变空）；单键信封 `{"args":{…}}` 的向后兼容仍在。

## DSH 更新后先跑这三条（v0.9.2 起）

工具是用 `defineTool`（来自宿主的 `@deepseek-ai/dsh-tools`）声明的，所以 **DSH 每次升级都要先验"工具契约"**：
失败方式是 `fiber failed → 15 个工具整体消失`，而 Blender 与插件包看起来都没坏。

```bash
node tests/dsh_api_compat_probe.mjs           # ② 工具契约：15 个工具 + 静态 timeoutMs + 0 报错
node tests/dsh_api_compat_probe.mjs --version 0.1.7-alpha.2 --json /tmp/base.json   # ③ 对"待升级版本"预演
```

跑完在会话里再点一次 `blender_viewport(op="doctor")`，它现在会打印 `宿主 API：dsh-tools@<版本> @ <路径>`
—— 这条自证用来区分"插件/宿主 API 坏了"和"Blender 没起"。工具描述前缀 `[v0.9.2]` 则是"跑的是这一代 lib"的自证。

## 0. addon 协议适配（不需要 Blender / DSH，最省事）

```bash
```

（协议适配自检已随旧 addon 支持一起删除：v2.3 只支持本仓 addon 的扁平协议，真机连通性由 launch_selftest 覆盖。）

| 覆盖 | 断言要点 |
|---|---|
| 协议探测 | 两种 mock 各判对；死端口 → `null`；**扁平 addon 第一轮没回时按封套形状纠错**回 `flat` |
| 两种协议下的调用 | `ping` / `get_scene_info` / `execute_code`（形状必须是 `{executed,result}`）/ 视口帧 |
| 错误传播 | 两种协议各自把 addon 的错误变成 reject |

## 1. view.py 自检（矩阵 + 离屏 + PNG 字节）

无需 GUI，走无头进程（约 1–3 s）：

```python
blender_rt_headless(preload="view", factory_startup=True, script=
  "import json\nprint('HEADLESS ' + K.dsh_view_api['selftest']())")
```

期望（`ok:true`）：

| 字段 | 含义 | 本次实测 |
|---|---|---|
| `proj_max_delta` | 自建投影矩阵 vs `Camera.calc_matrix_camera()` | **1.19e-7** |
| `view_max_delta` | 自建视图矩阵 vs `matrix_world.inverted()` | **9.5e-7** |
| `ortho_max_delta` | 正交投影对拍 | **1.19e-7** |
| `first_px` | 清屏 (25,153,51) 读回并直写 PNG 后的首像素 | **[25,153,51,255]** |
| `png_path` | 自检 PNG（可直接看图；应为纯色 `#199933`） | 配置的工作目录下 `dsh_view_selftest.png` |

## 2. 只算矩阵（不碰 GPU，最快）

```python
blender_rt_headless(preload="view", script="K.dsh_view_api['matrices']('{"from":[7,-7,5],"look_at":[0,0,1],"width":640,"height":360}')")
```

`640x360 / 50mm / 36mm` 时 `proj_matrix` 应为 `[[2.777778,0,0,0],[0,4.938272,0,0],[0,0,-1.0002,-0.20002],[0,0,-1,0]]`。

## 3. PNG 字节反查（确认没有二次 gamma / 没有上下颠倒）

```bash
python3 tests/png_decode.py "<配置的工作目录>/dsh_view_selftest.png"   # 路径见 blender_viewport op=doctor 的 config.workDir
# → size 96 54 bd 8 ct 6 rawlen ... px [25, 153, 51, 255]
```

导出过滤器的 `px` 是**第一行第一个像素**（PNG 自顶向下）；`filter0` 为 0 时 `raw[1:5]` 就是它。

## 4. 租约（9 条路径）

```bash
B=http://127.0.0.1:9877
curl -s -X POST $B/lease  -H "content-type: application/json" -d '{"holder":"A","ttlMs":30000}'   # 1 拿到
curl -s -o /dev/null -w "%{http_code}\n" -X POST $B/act -H "content-type: application/json" -d '{"code":"print(1)"}'  # 2 → 409
curl -s -X POST $B/lease  -H "content-type: application/json" -d '{"holder":"B","renewOnly":true}'                  # 3 不抢
curl -s -X POST $B/lease  -H "content-type: application/json" -d '{"holder":"B","force":true}'                       # 4 抢占
curl -s $B/who                                                                                                          # 5 holder + 指标
curl -s -X POST $B/perf   -H "content-type: application/json" -d '{"op":"status"}'                                     # 6 只读豁免（放行）
curl -s -X POST $B/release -H "content-type: application/json" -d '{"holder":"B"}'                                    # 7 释放
```

## 4.5 契约层 / 规划器自检（v0.5.0）

```bash
blender -b --factory-startup --python tests/contract_selftest.py -- /tmp/contract_out   # 24 项断言
blender -b --factory-startup --python tests/plan_selftest.py     -- /tmp/plan_out       # 18 项断言
```

期望 `{"ok":true,"passed":24,"failed":0}` 与 `{"ok":true,"passed":18,"failed":0}`。
覆盖：未判别连接 → Boolean 被拦、不可辨识参数 → unresolved、探针达标 → supported、dry_run 不改场景、
hidden_when 两态、ParamOutOfRange、Cycle 检测、硬错误拒绝编译、与契约层联动的包络检查。

## 5. 三条自检命令（工作区口径）

```bash
curl -sS http://127.0.0.1:9877/health    # 后端在不在 + provenance（加载版本自证：verdict/stale）
curl -sS http://127.0.0.1:9877/doctor    # 通道体检（真跑 bpy 往返；不通才三级判定）+ config.workDir.shared
curl -sS http://127.0.0.1:9877/who       # 租约 + 通道指标
```

`/health` 的 `provenance.stale=true` 表示**磁盘上的 runtime 已经改了、这个进程还是旧一代** →
重启后端（加载新版 lib/runtime）后再问一次；`/doctor` 的 `config.workDir.shared=false` 表示
无头脚本会被自动换到共享目录（并给 `WORKDIR_NOT_SHARED` 警告）。两处都不看 mtime。
