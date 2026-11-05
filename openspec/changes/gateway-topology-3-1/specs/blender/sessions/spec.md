# Spec Delta

## Purpose

定义 3.1 的会话边界：会话只表示可见 Blender GUI 的交互实例，分为用户自启并附着的 user-gui 与 Gateway 托管的 virtual-gui；无头作业不属于会话。

## ADDED Requirements

### Requirement: Sessions are GUI-only

The session capability SHALL expose exactly two kinds: `user-gui` and `virtual-gui`. A headless Blender task MUST NOT be represented as a session, and the session tool MUST reject a headless kind instead of silently mapping it.

#### Scenario: Headless task is not a session
- **WHEN** an agent submits a headless render or script task
- **THEN** the task is handled by the job queue and does not appear in the session list

#### Scenario: Unknown session kind is rejected
- **WHEN** the session tool receives kind `headless`
- **THEN** it returns a structured validation error naming the two supported GUI kinds

### Requirement: user-gui attaches to an existing instance

A `user-gui` session SHALL attach to a Blender process that the user already launched. The agent MUST NOT spawn a second visible GUI to compete with the user. Attaching MUST identify the target instance explicitly when more than one is connected.

#### Scenario: Agent attaches to the user session
- **WHEN** one user-launched instance is connected
- **THEN** attaching selects that instance and reports its instanceId and addon version

#### Scenario: Multiple user instances require a target
- **WHEN** two user instances are connected and no instanceId is supplied
- **THEN** attach fails with the ambiguity error and lists both instanceIds

### Requirement: A session has one writer lease

Each GUI session SHALL have a single write lease. Mutating operations MUST require the lease, and a lease holder SHALL be able to pause writes so another agent or the user can inspect without interleaving edits. A read-only operation MUST remain available while writes are paused.

#### Scenario: Second writer is denied
- **WHEN** one agent holds the write lease and another attempts a mutating operation
- **THEN** the second operation fails with a lease conflict that names the holder

#### Scenario: Pause blocks writes but not reads
- **WHEN** the lease holder pauses the session
- **THEN** mutating operations are rejected and read-only inspection still works

### Requirement: virtual-gui is Gateway-managed and user-invisible

A `virtual-gui` session SHALL be started and stopped by Gateway through a DisplayProvider. The Blender process MUST run as a real GUI process with `bpy.app.background == False` and MUST NOT create a user-visible top-level window. The session MUST have a bounded instance limit and MUST be cleaned up when stopped or when its owner disconnects.

#### Scenario: Gateway starts an invisible GUI
- **WHEN** a virtual-gui session is requested and a provider is available
- **THEN** Gateway starts Blender with a GUI process on a provider display and reports session readiness only after the addon handshake succeeds

#### Scenario: Stop removes the instance
- **WHEN** a virtual-gui session is stopped
- **THEN** its Blender process and display provider resources are released and no orphan process remains

#### Scenario: Limit is enforced
- **WHEN** the configured virtual-gui limit is reached
- **THEN** a new request fails with a structured capacity error and does not start an extra process

### Requirement: see belongs to GUI sessions

The see capability SHALL capture from a `user-gui` or `virtual-gui` session only. A headless job MUST NOT provide an interactive see surface, and a failed GUI capture MUST NOT fall back to a headless Workbench render.

#### Scenario: see captures a session viewport
- **WHEN** see is called on a ready GUI session
- **THEN** it returns the current viewport image from that session and includes its instanceId

#### Scenario: see on a headless job is rejected
- **WHEN** see is called for a headless job
- **THEN** it returns a structured boundary error and does not start a background Blender for a frame

### Requirement: Sessions are isolated

Instances SHALL NOT share Python state, scene files, or leases. A failure or disconnect of one instance MUST NOT mark another instance unavailable, and a job assigned to one session MUST NOT execute in another.

#### Scenario: One instance fails
- **WHEN** one session loses its addon connection
- **THEN** other connected sessions remain ready and routable

#### Scenario: Execution does not cross instances
- **WHEN** a mutating operation is addressed to one session
- **THEN** the operation is executed only by that instance and its result references the same instanceId