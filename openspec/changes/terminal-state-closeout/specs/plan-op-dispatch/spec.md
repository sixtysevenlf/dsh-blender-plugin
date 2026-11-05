# Spec Delta

## Purpose

plan catalog 是 plan op 的对外契约。本 capability 让该契约可被机械验证：目录声明的每个 op 都能通过其声明入口解析到真实的 Python 分派键，链接层不得把不可分派的 op 当作"已知漂移"放过，worker/无头入口与主入口保持同一 API 与 preload。

## ADDED Requirements

### Requirement: Catalog op 必须在声明入口可解析

系统必须（SHALL）把 catalog 中声明的每个 plan op 通过其声明路由解析到真实存在的 Python API 分派键。修复后，qc 家族的 `compare_auto` 与 `align_iou` 必须分别命中 `dsh_qc_api` 的 `compare_auto` / `align_iou` 键，而不是返回 `unknown qc op`。

#### Scenario: qc 漂移 op 可执行

- **WHEN** 调用 `blender_rt_plan(op="qc_compare_auto", args={ref:"a.png", render:"b.png"})` 或 `blender_rt_plan(op="qc_align_iou", args={ref:"a.png", render:"b.png"})`
- **THEN** 请求经 qc 路由进入 `dsh_qc_api["dispatch"]`，分派键命中真实的 `compare_auto` / `align_iou` 实现并返回结果（成功结果或明确的参数错误），回执不含 `unknown qc op`

#### Scenario: 全家族 parity 无缺口

- **WHEN** 运行 catalog ↔ Python dispatch parity 门禁
- **THEN** 每个非 host-side 家族的每个 catalog op 都映射到一个字面分派键，缺失列表为空

### Requirement: 链接层不得白名单漂移 op

`config/contracts.json` 必须（MUST）只允许 host-side op 通过显式 `hostOps` 声明作为例外；任何缺少 Python 分派键的 catalog op 都必须让 parity 门禁失败，而不是以 drift 备注（`catalogExtras`）通过。

#### Scenario: 缺少分派键时门禁失败

- **WHEN** 某个 catalog op 在声明表中没有对应分派键，且未声明为 host-side
- **THEN** parity 测试失败并报告 family、op 与缺失键，该变更不得合入

#### Scenario: host-side op 显式登记

- **WHEN** 一个 op 由 host 侧在分派前处理（例如 `shape_search`）
- **THEN** 它必须在 `hostOps` 中带 reason 声明，且不得出现在 Python 分派表中；声明与分派表不一致时门禁失败

### Requirement: worker/无头入口与主入口一致

worker/headless 入口对 plan op 必须（SHALL）使用与主入口相同的 Python API 与 preload；不支持的 op 必须返回带 op 名的结构化错误，不得误路由到另一 API 模块或静默给出错值。

#### Scenario: qc_render 走专属 API

- **WHEN** 带 session 调用 `blender_rt_plan(op="qc_render_views", ...)`
- **THEN** worker 路由使用 `dsh_qc_render_api` 且 preload 含 `qc_render`，返回结果不是 `unknown op`

#### Scenario: 无映射时明确失败

- **WHEN** worker 路径请求一个没有 headless 映射的 plan op
- **THEN** 回执为 `ok=false`，错误文本包含该 op 名，并提示改走 GUI 通道或先查 catalog；不执行任何几何写入
