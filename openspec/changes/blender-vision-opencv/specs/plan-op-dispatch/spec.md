# Spec Delta

## ADDED Requirements

### Requirement: Host-local vision ops bypass the session and addon

catalog 新增的 `vision` 家族及其七个 op 必须（SHALL）在 Gateway/host 侧完成分派：`blender_op` 不得为它们要求 GUI 会话，`planCall` 不得把它们发送到 addon 的 Python dispatch，worker/headless 入口也不得把它们当作 Blender 端 op。缺少视觉运行时必须返回视觉自身的结构化失败，而不是 `stage=session` 或 `unknown op`。

#### Scenario: No session required
- **WHEN** 没有已连接的 Blender，调用 `blender_op(op="vision_compare", args={ref:"a.png", candidate:"b.png"})`
- **THEN** 请求进入 host 的视觉路径并返回结果或 `stage=vision`/`config` 的失败，不返回 `stage=session`

#### Scenario: Not routed to Blender
- **WHEN** 调用 `vision_analyze` 且 Gateway 已连接 addon
- **THEN** addon 收不到该 op 的 execute_code 请求，Python dispatch 表也不需要新增 `vision_` 键

### Requirement: Host families are declared, not treated as drift

`config/contracts.json` 必须（MUST）把 `vision` 声明为 host family，并给出 host 入口与 reason；catalog ↔ Python dispatch parity 必须把该家族计为已覆盖而不是漂移。任何 host family 缺少声明、或声明的 host 入口不存在，都必须让 parity 门禁失败。

#### Scenario: Vision parity is green
- **WHEN** 运行 catalog ↔ Python dispatch parity 门禁
- **THEN** `vision` 的七个 op 全部由 host 声明覆盖，缺失列表为空

#### Scenario: Undeclared host family fails
- **WHEN** `vision` 从 `contracts.json` 移除但 catalog 仍保留
- **THEN** parity 门禁失败并报告家族名与七个缺失 op，而不是以 drift 备注通过
