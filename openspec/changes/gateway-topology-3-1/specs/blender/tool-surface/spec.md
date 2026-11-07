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

`session` SHALL manage user-gui and virtual-gui sessions, their session ownership, intra-session scope grants, and read-only presence. `op` SHALL execute catalog plan operations. `py` SHALL execute Python in a GUI session. `see` SHALL capture a GUI session viewpoint (viewport, camera, or custom). `run` SHALL submit and control job-queue jobs. `inspect` SHALL provide read-only inspection and MUST NOT execute write commands. Presence MUST NOT add a seventh tool.

#### Scenario: py does not run headless
- **WHEN** py is called without a GUI session
- **THEN** it returns a session-boundary error and does not start a background Blender

#### Scenario: run does not execute arbitrary GUI Python
- **WHEN** a caller sends an arbitrary script through run as a render job
- **THEN** validation rejects the script and points to headless job or py with the correct boundary

### Requirement: inspect is read-only and lists every command

`inspect` SHALL be read-only. `help` SHALL list only the read-only commands it can execute, with name, description, access, gate, enabled, pause-safe, and args. `inspect` MUST execute only those commands; an internal transport command MUST NOT be listed or executable; a known write command name MUST fail with `wrong_tool`; an unknown name MUST fail with `unknown_command`. There MUST be no execute mode or write receipt.

#### Scenario: Help lists the inspectable read-only commands
- **WHEN** inspect is called with command=help
- **THEN** it returns the read-only command list and does not list write or internal transport commands

#### Scenario: Read command executes
- **WHEN** inspect is called with a read-only command
- **THEN** it returns that command's result without mutating scene or addon state

#### Scenario: Write command is rejected
- **WHEN** inspect is called with a write command such as export_scene or set_texture
- **THEN** it returns wrong_tool naming py, op, or another owning tool and executes nothing

#### Scenario: Unknown command
- **WHEN** inspect is called with an unregistered command
- **THEN** it returns unknown_command with did_you_mean suggestions

### Requirement: The command surface is read-only plus internal transports

The addon wire surface SHALL contain 25 registered commands (21 inspect-executable read-only commands plus 4 internal transport commands) and 26 wire commands (25 registered plus ping). Internal transports: `execute_code`, `get_viewport_screenshot`, `send_selection_to_dsh`, `send_collection_to_dsh`. The 9 legacy write commands, 3 telemetry commands, and `get_polyhaven_asset_preview` MUST NOT be registered or exposed. Internal transports MUST NOT appear in inspect help or model-facing results.

#### Scenario: Counts match the reduced command surface
- **WHEN** the addon registry, catalog, and behavior matrix are inspected
- **THEN** they report 25 registered and 26 wire commands: 21 registered read-only, 4 internal, plus ping on the wire

#### Scenario: Write commands are absent
- **WHEN** the command surface is enumerated
- **THEN** no legacy write command, telemetry command, or asset-preview command is registered or listed by inspect

#### Scenario: Internal transport is not model-visible
- **WHEN** inspect help or a catalog is queried
- **THEN** `execute_code`, `get_viewport_screenshot`, and the two send commands are not listed as callable model commands

### Requirement: Session mutations obey scope and human pause

Mutating `session` operations, including txn `restore`, `mark`, and `revert`, SHALL require an `exclusive` scope grant. A human pause SHALL reject new mutating session operations without interrupting an operation that already started.

#### Scenario: txn restore requires a grant
- **WHEN** an agent calls txn restore without an exclusive grant
- **THEN** the call fails with scope_conflict and does not modify the scene

#### Scenario: Human pause blocks session mutation
- **WHEN** the human pauses agent writes and an agent calls a mutating session operation
- **THEN** the call is rejected with human pause evidence while read and presence calls continue

### Requirement: run is the only job submission surface

`run` SHALL be the only model tool that submits render or headless jobs. It SHALL expose submit, status, wait, collect, cancel, list, and `perf` (queue metrics), and it MUST NOT provide a synchronous execution mode or a separate job tool.

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