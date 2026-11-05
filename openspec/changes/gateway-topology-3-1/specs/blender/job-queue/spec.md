# Spec Delta

## Purpose

定义 Gateway 内唯一的有界作业队列，统一承载最终渲染与通用无头任务；提交即返回 jobId，状态可查询、可取消、可持久化，并且不把同步执行或超时提升当成另一条路径。

## ADDED Requirements

### Requirement: Every job is submitted with an id

`run` SHALL submit a job and return a stable `jobId` immediately. The response MUST NOT wait for completion. A caller that needs completion MUST wait separately with a bounded wait or poll status; there MUST NOT be a second synchronous submission path.

#### Scenario: Submit returns immediately
- **WHEN** a render or headless job is submitted
- **THEN** the response contains a jobId and state queued or assigned without blocking on Blender

#### Scenario: No promoted wait ID
- **WHEN** a submit response is returned
- **THEN** it uses the same jobId namespace for all later operations and does not create a second run ID

### Requirement: Job kinds are render and headless

The queue SHALL support exactly two job kinds: `render` for a declarative final render, and `headless` for a one-shot Blender background task. `render` MUST NOT execute arbitrary Python, and `headless` MUST run in a fresh `blender -b` process without a GUI session or interactive see.

#### Scenario: Render is declarative
- **WHEN** a render job is submitted
- **THEN** it accepts render parameters such as file, frames, camera, engine, resolution, samples, format, and output, and executes them without a script argument

#### Scenario: Headless is a scripted one-shot task
- **WHEN** a headless job is submitted
- **THEN** it runs the supplied script or file in a fresh background Blender and returns its structured result

### Requirement: Job state machine is explicit

A job SHALL move through `queued`, `assigned`, `running`, and exactly one terminal state: `completed`, `failed`, or `canceled`. Terminal transitions MUST be immutable and every state change MUST be observable through status.

#### Scenario: State advances in order
- **WHEN** a queued job acquires a slot
- **THEN** it moves to assigned and then running before a terminal state

#### Scenario: Terminal state is stable
- **WHEN** a completed or canceled job is queried again
- **THEN** its state remains unchanged and no new execution starts

### Requirement: Capacity is bounded and not oversold

The queue SHALL hold at most 64 pending jobs. Render jobs SHALL use one GPU slot, and headless jobs SHALL use a CPU slot by default and a GPU slot only when explicitly requested. The default capacity SHALL be one GPU slot and two CPU slots, and a submit that exceeds capacity MUST fail with a `QUEUE_FULL` error rather than waiting unbounded.

#### Scenario: Full queue is rejected
- **WHEN** 64 jobs are pending and another job is submitted
- **THEN** the submit fails with a structured queue-full error including current limits

#### Scenario: GPU slot is not oversold
- **WHEN** a render job is running on the GPU slot
- **THEN** another render job waits queued and no second render shares that slot

#### Scenario: Headless uses CPU by default
- **WHEN** a headless job is submitted without a GPU request
- **THEN** it can run on a CPU slot while a render occupies the GPU slot

### Requirement: Queue state survives a Gateway restart

Queued and terminal jobs SHALL be persisted. After a Gateway restart, queued jobs SHALL remain queued, terminal jobs SHALL keep their result and artifact references, and a job that was running MUST be marked failed with a `gateway_restart` root cause rather than being silently retried.

#### Scenario: Queued job survives restart
- **WHEN** Gateway restarts with a queued job
- **THEN** the job remains queued and later executes with the same jobId

#### Scenario: Running job is not replayed
- **WHEN** Gateway restarts while a job was running
- **THEN** the job becomes failed with gateway_restart and its partial artifacts remain inspectable

### Requirement: Jobs can be controlled and collected

`run` SHALL support status, wait, collect, cancel, and list. Wait SHALL be bounded and return a timeout result without killing the job. Cancel SHALL be idempotent, terminate only the target process tree, and leave no orphan Blender process. Collect SHALL return logs and declared output artifacts.

#### Scenario: Wait times out without canceling
- **WHEN** a bounded wait expires on a running job
- **THEN** the response reports a wait timeout and the job remains running

#### Scenario: Cancel removes the process tree
- **WHEN** a running job is canceled
- **THEN** the job reaches canceled and no Blender process from that job remains

#### Scenario: Completed job can be collected
- **WHEN** a job reaches completed
- **THEN** collect returns its exit result, logs, and output artifacts by jobId

### Requirement: Job failures use the same envelope

A failed job SHALL include the same stage, root cause, fix, fallback, and evidence contract as a failed tool call. The job state and the failure envelope MUST be retrievable after the process has exited.

#### Scenario: Spawn failure is attributable
- **WHEN** the job process cannot spawn Blender
- **THEN** the failed job records stage spawn with executable and environment evidence

#### Scenario: Queue rejection is actionable
- **WHEN** a job is rejected for capacity
- **THEN** the error names the limit and a retry or priority action rather than a generic failure