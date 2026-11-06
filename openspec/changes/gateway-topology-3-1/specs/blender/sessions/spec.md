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

### Requirement: Session resolution never silently starts a virtual-gui

When a session-bound capability (`session`, an executing `op`, `py`, `see`) has no ready `user-gui`, Gateway SHALL stop with a decision-required response and MUST NOT start a `virtual-gui`, spawn a headless Blender, or dispatch the call elsewhere. The stop MUST name the requested capability, state that Blender is not attached, and offer two explicit choices: launch Blender and attach, or start `virtual-gui` after the user's consent. An existing virtual-gui does not ask again.

#### Scenario: First call without a visible Blender stops
- **WHEN** the first session-bound call in a session finds no ready user-gui
- **THEN** it returns `stage=session` stating that Blender is not attached, starts no process and no provider, and lists the two explicit choices

#### Scenario: Catalog lookup works without Blender
- **WHEN** `op` is called with a catalog or dry-run only
- **THEN** it returns the catalog or validation result without requiring a session, starting Blender, or returning `stage=session`

#### Scenario: virtual-gui starts only after consent
- **WHEN** the user chooses virtual-gui in response to that stop
- **THEN** the next explicit start creates the virtual-gui session, and no call starts one on its own

#### Scenario: Refusal is not a fallback
- **WHEN** the user declines virtual-gui
- **THEN** subsequent session-bound calls keep failing with the same decision-required response and never spawn a headless Blender

### Requirement: A session has one caller-aware writer lease

Each GUI session SHALL have a single write lease whose holder is a caller identity tuple `(hostId, dshSessionId, agentId)`, not a process ID. Multiple callers MAY read, `see`, and query presence concurrently; only one caller MAY hold the write lease. Mutating operations MUST require the lease, and the holder SHALL be able to pause writes so another agent or the user can inspect without interleaving edits. A read-only operation MUST remain available while writes are paused.

#### Scenario: Second writer is denied
- **WHEN** one agent holds the write lease and another attempts a mutating operation
- **THEN** the second operation fails with a lease conflict that names the holder and its expiry

#### Scenario: Two DSH sessions share one user-gui
- **WHEN** two DSH sessions attach to the same user-gui
- **THEN** both can read and see the scene, but only the lease holder can mutate it

#### Scenario: Pause blocks writes but not reads
- **WHEN** the lease holder pauses the session
- **THEN** mutating operations are rejected and read-only inspection still works

### Requirement: Lease lifecycle and conflict handling

A lease SHALL have a bounded TTL and heartbeat. It MUST be released when the caller disconnects, the host exits, or the call finishes, and an expired lease MAY be acquired by another caller. A conflict MUST fail fast with `409` and name the holder and expiry by default; an optional `wait_ms` MAY be bounded to at most 5 seconds. `force` takeover MUST be restricted to a human or Team Lead and MUST write an audit event.

#### Scenario: Delegation does not inherit the lease
- **WHEN** a parent agent delegates work to a subagent
- **THEN** the parent releases the write lease before the subagent acquires it; the subagent does not inherit write access implicitly

#### Scenario: Stale lease is reclaimed
- **WHEN** a lease holder disconnects without releasing
- **THEN** the lease expires after its TTL and another caller can acquire it without `force`

#### Scenario: Force requires privilege
- **WHEN** an ordinary agent attempts a `force` takeover
- **THEN** it is rejected; a human or Team Lead takeover succeeds and writes an audit event

### Requirement: Agent presence is visible in the user viewport

For a user-gui session, Gateway SHALL track each attached caller's display name, color, observation pose, selected object UUIDs, activity, and lease state. The addon SHALL draw that presence as a frustum, label, and selection outlines in the user's viewport without moving the user's view or mutating the scene. The same read-only presence data SHALL be queryable through `session` without adding a model tool.

#### Scenario: User sees where an agent is looking
- **WHEN** an agent attaches to a user-gui and reports an observation pose
- **THEN** the addon draws the agent's frustum and label in the user's viewport, and the user's own view is unchanged

#### Scenario: Multiple agents are distinguishable
- **WHEN** two agents attach to the same user-gui
- **THEN** each gets a stable color and label, and their viewports and selections are drawn independently

#### Scenario: Presence is read-only
- **WHEN** presence is queried or drawn
- **THEN** no scene data, selection, or user view is modified

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

### Requirement: see preview size is a fixed enum

See SHALL accept `max_size` only from a fixed set per aspect: `1:1` = `544|768|1024|1280`; `4:3` = `640|960|1280`; `16:9` = `1024|1152|1280`. A value outside that aspect's set MUST return `invalid_params` naming the allowed values and MUST NOT be clamped. The model-facing see surface MUST NOT accept an arbitrary `view_size`; supplying it MUST return `invalid_params`.

#### Scenario: Valid values are accepted

- **WHEN** see is called with a valid `(aspect, max_size)` pair from the fixed sets
- **THEN** it returns the exact pixel dimensions for that pair and reports them in the receipt

#### Scenario: 1:1 fixed sizes

- **WHEN** see is called with `aspect=1:1`
- **THEN** `max_size` is one of `544`, `768`, `1024`, `1280`, producing `544×544`, `768×768`, `1024×1024`, or `1280×1280`

#### Scenario: 4:3 fixed sizes

- **WHEN** see is called with `aspect=4:3`
- **THEN** `max_size` is one of `640`, `960`, `1280`, producing `640×480`, `960×720`, or `1280×960`

#### Scenario: 16:9 fixed sizes

- **WHEN** see is called with `aspect=16:9`
- **THEN** `max_size` is one of `1024`, `1152`, `1280`, producing `1024×576`, `1152×648`, or `1280×720`

#### Scenario: Invalid value is rejected

- **WHEN** `max_size` is outside the selected aspect's set, or `view_size` is supplied
- **THEN** see returns `invalid_params` with the allowed values and does not clamp or capture

### Requirement: see preview options respect the model pixel bounds

Every option SHALL have at least `544×544` total pixels and at most `1300×1300` total pixels. The smallest option per aspect SHALL be `544×544`, `640×480`, and `1024×576`; the largest SHALL be `1280×1280`, `1280×960`, and `1280×720`. A `full=true` screenshot SHALL preserve its captured ratio and use a fixed 1024-pixel longest edge; `aspect` and `max_size` do not apply to it.

#### Scenario: Smallest options meet the lower bound

- **WHEN** the minimum option is selected for any aspect
- **THEN** its total pixels are at least 544×544 and the image is not below the model's small-image threshold

#### Scenario: Largest options stay under the upper bound

- **WHEN** the maximum option is selected for any aspect
- **THEN** its total pixels are at most 1300×1300

#### Scenario: Full screenshots use a fixed bound

- **WHEN** see is called with `full=true`
- **THEN** the screenshot keeps its captured ratio and its longest edge is 1024 pixels

### Requirement: see supports three aspect presets

See SHALL accept `aspect` with exactly `1:1`, `4:3`, and `16:9`. 3D viewport and custom-view captures SHALL default to `16:9`. When `aspect` is omitted, `max_size` SHALL default to 1024; when `aspect` is supplied without `max_size`, `max_size` SHALL default to that aspect's smallest option (`544`, `640`, or `1024`). The selected aspect SHALL determine the output ratio without stretching the scene.

#### Scenario: Default is 16:9 at 1024

- **WHEN** see is called without `aspect` and `max_size`
- **THEN** it returns a `1024×576` image

#### Scenario: Aspect-only call uses the minimum

- **WHEN** see is called with `aspect=1:1` or `aspect=4:3` and no `max_size`
- **THEN** it returns `544×544` or `640×480` respectively

#### Scenario: Aspect does not stretch the scene

- **WHEN** see selects any aspect preset
- **THEN** the scene is not stretched and the user's viewport and selection are unchanged

### Requirement: Sessions are isolated

Instances SHALL NOT share Python state, scene files, or leases. A failure or disconnect of one instance MUST NOT mark another instance unavailable, and a job assigned to one session MUST NOT execute in another.

#### Scenario: One instance fails
- **WHEN** one session loses its addon connection
- **THEN** other connected sessions remain ready and routable

#### Scenario: Execution does not cross instances
- **WHEN** a mutating operation is addressed to one session
- **THEN** the operation is executed only by that instance and its result references the same instanceId