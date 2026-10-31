# 实验：批处理 update 第 1/2 步（未合并）

> 分支 `experiments/batch-update-step12`，基于 `main@22012d1`。只做提案的第 1、2 步：
> **① runtime 内加批量 update 工具；② 按扫描结果合并可安全合并的 update。**
> 第 3 步（addon `batch` 协议）不在本分支。
>
> **归档说明（T11b.6）：** `tests/motion_selftest.py` 已迁移到
> `tests/e2e/blender/native/motion.py`；本目录仅保留实验记录，其中的命令不再适用于当前树。

## 改了什么

### 1. `runtime/kit.py`：新增 `BatchUpdates`

`K.dsh_kit.BatchUpdates`（也导出到 `K.dsh_kit` namespace）：

```python
batch = _KIT.BatchUpdates()
batch.flush()                     # 起始基线（可选）
for ob in objs: ...               # 只写数据，不 update
batch.flush()                     # 需要读 evaluated 数据前再刷
print(batch.stages, batch.updates)
```

也能当上下文管理器用（退出时 flush）。纪律写在 docstring 里：
任何 `evaluated_get() / to_mesh() / BVH / bake / render` 之前必须 flush；
合并 update 会减少 `depsgraph_update_post` 触发次数（活动追踪等依赖该事件的逻辑要复核）。

### 2. `runtime/motion.py`：`_restore_transforms` 批量化

旧实现：每个"动过"的对象写回字段后立刻 `view_layer.update()`，再核对矩阵；不齐再写矩阵、再 update。
新实现：**先写完所有对象字段 → 一次 update → 统一核对**；只有兜底"直接写矩阵"的对象才额外 update。
返回结构（`mismatches / pre_max / post_max / wrote`）与兜底路径保持不变。

兼容性：`kit` 里没有 `BatchUpdates` 时（旧内核 / 测试内联执行）用本地兜底实现，模块不硬依赖新 kit。

## A/B 实测（真实成品文件，300 个 mesh 对象，还原被扰动的 transform）

| 场景 | 旧实现（逐对象 update） | 新实现（一次 update） | 提速 |
|---|---|---|---|
| 行星发动机 | 410.2 ms | **12.7 ms** | **32×** |
| 太空电梯 | 2,261.2 ms | **24.9 ms** | **91×** |

```text
行星发动机: wrote=300 mismatches=0 pre_max=1.2002 post_max=0.0 verify(max_delta=0, bad=0, exact=true)
太空电梯:   wrote=300 mismatches=0 pre_max=1.2000 post_max=0.0 verify(max_delta=0, bad=0, exact=true)
```

复跑：`blender_rt_headless(engine="none", script_file="experiments/batch-update-step12/ab_restore.py")`
（脚本自己加载 `runtime/offline_bootstrap.py` 和真实 `.blend`，结果写 `D:/DSH/blender/tmp/restore_ab.json`）

## 正确性

- `tests/motion_selftest.py`：**25 通过 / 0 失败**，其中 3 条专门验"扫掠后逐对象 matrix_world 还原"
  （用 `run_motion_selftest.py` 在离线内核里跑）。
- A/B 每轮还原后逐对象矩阵对拍：`max_delta = 0`、`bad = 0`、`exact = true`。
- `K.dsh_kit.selftest()`：`ok=true`，且 `BatchUpdates` 已挂上。
- `npm test`：见提交记录（Node 主链）。

## 其余 44 处 `view_layer.update()` 为什么不动

静态扫描 45 处、6 处在循环内，逐个看过：

| 位置 | 为什么保留 |
|---|---|
| `assembly.py:asm_repair` | 每轮末尾 update 是下一轮 `_repair_rescan`（读 evaluated）的前提；合并一轮内的多次写入仍可做，但当前一轮已只有 1 次 update |
| `motion.py:_measure_one` | 每相位 update 后立刻 `_group_tris(co, dg)` 读 evaluated 几何，必须 flush |
| `motion.py:_restore_transforms` | 本分支已合并（字段写入 1 次 + 兜底额外） |
| `qc_render.py:qc_render_views` | 每视角切换需要重算/重绘 |
| `qc_render.py:_selftest_projection` | 测试辅助，不影响产品路径 |
| `blockout.py:blockout_base` 3 处 | 分别在镜像前（需矩阵）、镜像后 bbox、anchor 平移后 bbox —— 三处之后都有 evaluated/bbox 读 |
| 其余约 39 处 | 各函数一次，非相邻；没有"连续两次 update 中间无读"的可合并对 |

结论：**这轮能安全合并的只有 `_restore_transforms`**。再往下就需要第 3 步（协议层 batch）或逐 op 重构读取时机。

## 跑法

```bash
# motion 自检（离线内核，不依赖已安装插件）
blender_rt_headless(engine="none", script_file="experiments/batch-update-step12/run_motion_selftest.py")

# A/B
blender_rt_headless(engine="none", script_file="experiments/batch-update-step12/ab_restore.py")

# kit 自检
blender_rt_headless(engine="none", script_file="experiments/batch-update-step12/check_kit.py")
```
