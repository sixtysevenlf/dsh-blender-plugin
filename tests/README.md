# 验证脚本（可复跑）

> 这些检查把 v0.4.0（自定义视角 / 无头 / 租约）的关键结论固定下来，换机器或升级 Blender 后**先跑它们**。

## 0. addon 协议适配（不需要 Blender / DSH，最省事）

```bash
node tests/protocol_selftest.mjs      # 或 npm test
```

用两个 mock addon（扁平协议 / category-action）把适配层跑一遍，共 28 项断言：

| 覆盖 | 断言要点 |
|---|---|
| 协议探测 | 两种 mock 各判对；死端口 → `null`；**扁平 addon 第一轮没回时按封套形状纠错**回 `ahujasid` |
| 协议解析 | `auto` 探测出正确结果；显式 `addonProtocol` 不探测、立即返回；未知协议名报错 |
| 两种协议下的调用 | `ping` / `get_scene_info` / `execute_code`（形状必须是 `{executed,result}`）/ 视口帧 |
| 错误传播 | 两种协议各自把 addon 的错误变成 reject |
| 缺能力不假装 | category-action 侧的资产集成命令明确报错；遥测类是本地桩（不占 socket） |

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

## 5. 三条自检命令（工作区口径）

```bash
curl -sS http://127.0.0.1:9877/health    # 后端在不在
curl -sS http://127.0.0.1:9877/doctor    # 通道体检（真跑 bpy 往返；不通才三级判定）
curl -sS http://127.0.0.1:9877/who       # 租约 + 通道指标
```
