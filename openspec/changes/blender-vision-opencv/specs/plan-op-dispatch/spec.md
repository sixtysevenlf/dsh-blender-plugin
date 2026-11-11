# Spec Delta

## ADDED Requirements

### Requirement: Host-local vision ops bypass the session and addon

catalog 新增的 `vision` 家族及其八个 op 必须（SHALL）在 Gateway/host 侧完成分派：`blender_op` 不得为它们要求 GUI 会话，`planCall` 不得把它们发送到 addon 的 Python dispatch，worker/headless 入口也不得把它们当作 Blender 端 op。缺少视觉运行时，或 `vision_detect` 遇到 `features.dnn` 关闭、DNN 配置切换中、自定义 backend/device 不可用、没有可用模型时，必须返回视觉自身的结构化失败，而不是 `stage=session` 或 `unknown op`。

#### Scenario: No session required
- **WHEN** 没有已连接的 Blender，调用 `blender_op(op="vision_compare", args={ref:"a.png", candidate:"b.png"})`
- **THEN** 请求进入 host 的视觉路径并返回结果或 `stage=vision`/`config` 的失败，不返回 `stage=session`

#### Scenario: Not routed to Blender
- **WHEN** 调用 `vision_analyze` 且 Gateway 已连接 addon
- **THEN** addon 收不到该 op 的 execute_code 请求，Python dispatch 表也不需要新增 `vision_` 键

#### Scenario: DNN gate fails before dispatch
- **WHEN** `features.dnn=false` 时调用 `vision_detect`
- **THEN** 回执为 `stage=config`、`code=dnn_disabled`、`retryable=false`，addon 收不到请求，也不会被误报为 `stage=session` 或 `unknown op`

#### Scenario: DNN backend gate fails before dispatch
- **WHEN** `features.dnn=true`、`vision.dnn.backend=onnxruntime` 且自定义 venv 缺少 ONNX Runtime
- **THEN** 回执为 `stage=config`、`code=dnn_backend_unavailable`，addon 收不到请求，host 不自动安装包或改用 OpenCV 后端

#### Scenario: DNN config switching fails before dispatch
- **WHEN** DNN 配置切换登记为 pending，调用 `vision_detect`
- **THEN** 回执为 `stage=config`、`code=dnn_config_switching`、`retryable=true`，带 active/pending revision，addon 收不到请求，也不被误报为 `stage=session` 或 `unknown op`

### Requirement: Animation family is session-bound and addon-dispatched

catalog 新增的 `animation` 家族及其三个 op（`animation_report`、`animation_selftest`、`animation_help`）必须（SHALL）标记 `requires_session=true`，经 `config/routing.json` 的 GUI 路由进入 addon 的 Python dispatch；不得被 host 的 vision 短路截获，也不得登记为 `config/contracts.json` 的 host family。`animation_report`/`animation_help` 必须走 session-read：要求会话，但不要求 write grant、不获取写租约，也不得登记进 read-only 列表；`animation_selftest` 走写门。

catalog 总数必须为 36 family / 239 op（vision 8 + animation 3），6 工具、29 路由、25 注册 addon 命令、protocol 12 不变。

#### Scenario: Animation requires a session
- **WHEN** 没有已连接的 Blender，调用 `blender_op(op="animation_report", args={objects:["Cube"]})`
- **THEN** 回执为 `stage=session` 的失败，不进入 host vision 路径，也不返回空指标

#### Scenario: Report is session-read, not read-only
- **WHEN** 会话在线但没有 write grant，调用 `animation_report`
- **THEN** 请求经 session-read 门通过并返回报告，不申请 write grant；`animation_report`/`animation_help` 不出现在 `READ_ONLY_OPS` 或 `PLAN_READ_ONLY_OPS`

#### Scenario: Selftest uses the write gate
- **WHEN** 会话在线但没有 write grant，调用 `animation_selftest`
- **THEN** 回执为写门失败，不创建临时 collection

#### Scenario: Animation is routed to the addon
- **WHEN** 会话在线且调用 `animation_report`
- **THEN** 请求经 animation 的 GUI 路由进入 `dsh_animation_api`，addon 收到 execute_code；host 的 `runtime/vision/dispatch.ts` 不被调用

#### Scenario: Animation is not a host family
- **WHEN** 检查 `config/contracts.json` 与 catalog parity
- **THEN** animation 不被列为 host family，三个 op 由 Python dispatch 覆盖；缺失路由或 preload 时门禁失败

### Requirement: Host families are declared, not treated as drift

`config/contracts.json` 必须（MUST）把 `vision` 声明为 host family，并给出 host 入口与 reason；catalog ↔ Python dispatch parity 必须把该家族计为已覆盖而不是漂移。任何 host family 缺少声明、或声明的 host 入口不存在，都必须让 parity 门禁失败。

#### Scenario: Vision parity is green
- **WHEN** 运行 catalog ↔ Python dispatch parity 门禁
- **THEN** `vision` 的八个 op 全部由 host 声明覆盖，缺失列表为空

#### Scenario: Undeclared host family fails
- **WHEN** `vision` 从 `contracts.json` 移除但 catalog 仍保留
- **THEN** parity 门禁失败并报告家族名与八个缺失 op，而不是以 drift 备注通过
