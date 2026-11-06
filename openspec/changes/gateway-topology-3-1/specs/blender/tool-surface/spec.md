# Spec Delta

## Purpose

定义 3.1 的模型工具面为六个稳定的产品能力：session、op、py、see、run、inspect。六个名字是对 3.0.x 旧工具的等价迁移与重命名，不是新的建模或渲染能力；工具名表达用户任务，而不是内部执行方式；内部 worker、队列、显示提供者不暴露为额外工具。早期叙事中的 `render` 工具在本 change 定名为 `run`，因为它同时承载 render 与 headless 两类作业。`py` 是 `do` 的改名：在 GUI 会话内执行 agent 写的脚本与短 Python，是执行通道，不是建模或渲染引擎；没有会话时拒绝，不启动 headless。

## ADDED Requirements

### Requirement: The model surface has exactly six tools

The model-visible tool surface SHALL contain exactly `session`, `op`, `py`, `see`, `run`, and `inspect`. Legacy tool names and internal execution concepts MUST NOT be registered as additional model tools.

#### Scenario: Catalog exposes six tools
- **WHEN** the tool catalog is queried
- **THEN** it lists the six names and no legacy viewport, worker, plan, do, headless, job, perf, commands, or cmd tool

#### Scenario: Legacy name is rejected
- **WHEN** a caller invokes a removed tool name
- **THEN** the call fails with a migration hint that maps the old name to one of the six tools

### Requirement: Each tool has one product boundary

`session` SHALL manage user-gui and virtual-gui sessions, their caller-aware leases, and read-only presence. `op` SHALL execute catalog plan operations. `py` SHALL execute Python in a GUI session. `see` SHALL capture a GUI viewport. `run` SHALL submit and control job-queue jobs. `inspect` SHALL provide read-only inspection, with any write-capable command requiring an explicit execute mode and returning a write receipt. Presence MUST NOT add a seventh tool.

#### Scenario: py does not run headless
- **WHEN** py is called without a GUI session
- **THEN** it returns a session-boundary error and does not start a background Blender

#### Scenario: run does not execute arbitrary GUI Python
- **WHEN** a caller sends an arbitrary script through run as a render job
- **THEN** validation rejects the script and points to headless job or py with the correct boundary

#### Scenario: inspect is read-only by default
- **WHEN** inspect is called without an explicit execute mode
- **THEN** it performs only read queries and cannot mutate the scene or addon state

### Requirement: run is the only job submission surface

`run` SHALL be the only model tool that submits render or headless jobs. It SHALL expose submit, status, wait, collect, cancel, list, and queue metrics, and it MUST NOT provide a synchronous execution mode or a separate job tool.

#### Scenario: Submit and control use one tool
- **WHEN** an agent submits a render and later checks or cancels it
- **THEN** all operations use run with the returned jobId

#### Scenario: No second job tool
- **WHEN** the surface is inspected
- **THEN** there is no headless, job, or render tool beside run

### Requirement: Internal execution is not model-visible

The worker, virtual display provider, queue scheduler, and job persistence SHALL be internal. A worker MUST NOT appear as a session kind or a tool, and a headless job MUST NOT appear as a session. Diagnostics MAY expose internal health, but MUST NOT add callable model tools.

#### Scenario: Worker is not a session
- **WHEN** the session tool lists kinds
- **THEN** only user-gui and virtual-gui are returned

#### Scenario: Internal diagnostics do not add tools
- **WHEN** inspect returns worker, provider, or queue health
- **THEN** the data is diagnostic output and no new model tool is registered

### Requirement: The six-tool count is contractual

The tool count SHALL be six in the tool catalog, behavior matrix, documentation, and package metadata. A contract test MUST fail if a seventh tool is registered or if a legacy name reappears without an explicit version change.

#### Scenario: Count drift is caught
- **WHEN** a new model tool is registered
- **THEN** the contract test fails until the count and every compatibility document are updated

#### Scenario: Six tools remain consistent
- **WHEN** the release gate runs
- **THEN** all tool-count sources report six and the behavior matrix has one row group per tool