# blender/sessions Specification

## Purpose

定义 3.1 的会话边界：会话只表示可见 Blender GUI 的交互实例，分为用户自启并附着的 user-gui 与 Gateway 托管的 virtual-gui；无头作业不属于会话。

## Requirements

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

Session-bound calls (`op` executing, `py`, `see`) SHALL NOT run without a ready GUI session (`user-gui`, or a `virtual-gui` owned by the same DSH session). Gateway SHALL return a decision-required response and MUST NOT start a `virtual-gui`, spawn a headless Blender, or dispatch elsewhere. The response SHALL name the capability, state Blender is not attached, and offer two choices: launch Blender and attach, or start `virtual-gui` after user consent. An existing virtual-gui does not ask again.

#### Scenario: First call without a visible Blender stops
- **WHEN** the first session-bound call in a session finds no ready GUI session
- **THEN** it returns `stage=session` stating that Blender is not attached, starts no process and no provider, and lists the two explicit choices

#### Scenario: Catalog lookup works without Blender
- **WHEN** `op` is called with a catalog or dry-run only
- **THEN** it returns the catalog or validation result without requiring a session, starting Blender, or returning `stage=session`

#### Scenario: virtual-gui start requires a consent token
- **WHEN** an agent calls `session start kind=virtual-gui` without a consent token
- **THEN** it returns `consent_required`; the DSH host obtains the user confirmation, issues a one-time short-TTL `consent_token`, and only then does start create the session

#### Scenario: Refusal is not a fallback
- **WHEN** the user declines virtual-gui
- **THEN** subsequent session-bound calls keep failing with the same decision-required response and never spawn a headless Blender

### Requirement: Session management has pre-session exceptions

`session` SHALL allow `list`, `status`, `doctor`, `start`, and `stop` without a ready GUI session. The first three are read-only; `start`/`stop` manage virtual-gui, and the first virtual-gui start still requires a consent token.

#### Scenario: Pre-session query works without Blender
- **WHEN** an agent calls `session list`, `status`, or `doctor` before any Blender is attached
- **THEN** the call returns its read-only result instead of a session decision request

#### Scenario: Virtual-gui start still needs consent
- **WHEN** an agent calls `session start kind=virtual-gui` without a consent token
- **THEN** the call returns `consent_required` and creates no session

### Requirement: A user-gui session is owned by one DSH session

Each `user-gui` SHALL be owned by exactly one DSH session `(hostId, dshSessionId)`. The owner's agents MAY attach and read concurrently. Another DSH session MUST NOT attach or operate until ownership is released; it SHALL receive `session_in_use` naming the owner and expiry. Ownership SHALL be released on detach, DSH session end, host exit, or TTL expiry; another DSH session MAY then claim it.

#### Scenario: Owner agents attach to one session
- **WHEN** a DSH session owns a user-gui and its main agent and a subagent both attach
- **THEN** both can read, see, and query presence without creating a second session

#### Scenario: Second DSH session is rejected
- **WHEN** another DSH session attempts to attach to the same user-gui
- **THEN** it receives `session_in_use` naming the owner and expiry and executes nothing

#### Scenario: Released session can be claimed
- **WHEN** the owner detaches or its DSH session ends
- **THEN** ownership is released and another DSH session can claim the same user-gui

### Requirement: Ownership has a heartbeat and a main agent

Ownership SHALL heartbeat every 30 seconds and expire after 120 seconds without a heartbeat. The main agent is the `agentId` recorded at claim; only a host-injected `role=main` may grant or revoke scopes.

#### Scenario: Stale ownership is reclaimed
- **WHEN** the owner crashes and stops heartbeating for more than 120 seconds
- **THEN** ownership expires and another DSH session may claim the user-gui

### Requirement: Writes are coordinated by intra-session scope grants

Within the owning DSH session, a mutating operation SHALL declare a scope: `exclusive` for global mutations, or `collection:<name>`, `prefix:<name>`, or `object:<session_uid>`. Non-overlapping grants MAY run concurrently; overlapping grants MUST fail fast with 409 `scope_conflict` naming the holder, scope, and expiry. Only the main agent MAY grant or revoke scopes; a subagent MUST NOT inherit them. Read, `see`, and presence MUST NOT require a grant.

#### Scenario: Non-overlapping writes run concurrently
- **WHEN** two agents hold grants for disjoint collections or objects
- **THEN** both mutations execute without a session-wide writer lock

#### Scenario: Object scope falls back without a session UID
- **WHEN** an object scope has no session UID
- **THEN** `instanceId:scene:name:type` is used as the stable identifier and the receipt includes the readable object name

#### Scenario: Overlapping writes conflict
- **WHEN** an agent requests a scope that overlaps a live grant
- **THEN** it receives scope_conflict naming the holder, scope, and expiry and executes nothing

#### Scenario: Subagent does not inherit
- **WHEN** the main agent delegates work to a subagent
- **THEN** the subagent must obtain its own scope grant and does not inherit the parent's grants

#### Scenario: Reads do not require a grant
- **WHEN** an agent reads, captures see, or queries presence while another agent holds a grant
- **THEN** the read succeeds without acquiring a grant

### Requirement: py execution is exclusive

`py` SHALL always acquire an `exclusive` grant and MUST NOT accept a narrower scope. A second `py` call SHALL fail with `py_busy` naming the holder and retry delay, unless it requests a bounded `wait_ms` of at most 5 seconds.

#### Scenario: Second py is rejected as busy
- **WHEN** a `py` call is running and another agent calls `py` during a human pause
- **THEN** the new call fails with `py_busy` or a human-pause error, and the running call is not interrupted

### Requirement: py grant lifetime, pause, and headless separation

An active `py` grant MUST NOT expire by the normal grant TTL; it is released when the call finishes, fails, times out, or the connection drops. Human pause SHALL reject new `py` calls but MUST NOT interrupt a running execution. A headless job MAY accept a script through `run`, but it is a separate job surface and MUST NOT be represented as a session or an interactive `py` call.

#### Scenario: Running py is not timed out by grant TTL
- **WHEN** a `py` call runs longer than 30 seconds
- **THEN** its exclusive grant remains active until the call finishes or the connection drops, and a timeout receipt says `py_still_running`

#### Scenario: Human pause does not interrupt running py
- **WHEN** human pause is enabled while a `py` call is running
- **THEN** new `py` calls are rejected and the running execution continues

#### Scenario: Headless run is not a py session
- **WHEN** a headless job accepts a script through `run`
- **THEN** it is reported as a job, not a session or an interactive `py` call

### Requirement: Session mutations require an exclusive grant

Mutating `session` operations, including txn `restore`, `mark`, and `revert`, SHALL require an `exclusive` grant and SHALL be rejected during human pause. Read-only session queries, `see`, and presence SHALL remain available.

#### Scenario: txn restore without a grant is rejected
- **WHEN** an agent calls txn restore without an exclusive grant
- **THEN** the call fails with scope_conflict and executes nothing

### Requirement: Human edits take priority

In the Blender UI, the human is not a DSH caller and MUST NOT be modeled as an agent or scope holder. The human SHALL always be able to edit the scene. The addon SHALL provide pause/resume; pause blocks all agent writes but not reads, `see`, or presence. When the human modifies data covered by an agent's grant, the next agent write SHALL fail with `human_modified` and require a re-read; 3.1 MUST NOT merge concurrent edits or provide per-actor undo.

#### Scenario: Human can always edit
- **WHEN** agents hold scopes over objects that the human edits in the Blender UI
- **THEN** the human's edit is applied, and no agent grant blocks or reverts it

#### Scenario: Pause blocks agent writes only
- **WHEN** the human pauses agent writes from the addon panel
- **THEN** agent mutations are rejected while reads, see, and presence continue

#### Scenario: Agent cannot clear a human pause
- **WHEN** an agent calls session resume while a human pause is active
- **THEN** the call is rejected and the pause remains in effect

#### Scenario: Human-modified scope requires re-read
- **WHEN** an agent writes to an object that the human changed after the grant was acquired
- **THEN** the write fails with human_modified and the agent must re-read before retrying

### Requirement: Scope grants have bounded lifetime and conflict handling

A scope grant SHALL have a bounded TTL and heartbeat. It MUST be released when its holder disconnects, the DSH session ends, or the call finishes, and an expired grant MAY be reclaimed. Default conflicts MUST fail fast with 409 and name the holder, scope, and expiry; an optional `wait_ms` MAY be bounded to at most 5 seconds. Only the owning session's main agent MAY revoke another caller's grant.

#### Scenario: Stale grant is reclaimed
- **WHEN** a grant holder disconnects without releasing
- **THEN** the grant expires after its TTL and another caller can acquire it without force

#### Scenario: Main agent revokes a subagent grant
- **WHEN** the main agent revokes a subagent's grant
- **THEN** the subagent's next mutation is rejected until it acquires a new grant

#### Scenario: Bounded wait
- **WHEN** a conflicting write requests wait_ms
- **THEN** it waits at most the requested bounded time and then returns scope_conflict

### Requirement: Agent presence is visible in the user viewport

For a user-gui session, Gateway SHALL track each attached caller's display name, color, observation pose, selected object `session_uid`s, activity, and held scope grants; it SHALL also track human pause state. The addon SHALL draw that presence as a frustum, label, and selection outlines in the user's viewport without moving the user's view or mutating the scene. The same read-only presence data SHALL be queryable through `session` without adding a model tool.

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

A `virtual-gui` session SHALL be started and stopped by Gateway through a DisplayProvider. It SHALL be owned by the DSH session that requested it, and only that session's agents MAY attach. The Blender process MUST run as a real GUI process with `bpy.app.background == False` and MUST NOT create a user-visible top-level window. The session MUST have a bounded instance limit and MUST be cleaned up when stopped or when its owner disconnects.

#### Scenario: Gateway starts an invisible GUI
- **WHEN** a virtual-gui session is requested and a provider is available
- **THEN** Gateway starts Blender with a GUI process on a provider display and reports session readiness only after the addon handshake succeeds

#### Scenario: Stop removes the instance
- **WHEN** a virtual-gui session is stopped
- **THEN** its Blender process and display provider resources are released and no orphan process remains

#### Scenario: Virtual-gui is owner-scoped
- **WHEN** another DSH session attempts to attach to a virtual-gui
- **THEN** it is rejected with session_in_use and the owner's agents remain attached

#### Scenario: Limit is enforced
- **WHEN** the configured virtual-gui limit is reached
- **THEN** a new request fails with a structured capacity error and does not start an extra process

### Requirement: see belongs to GUI sessions

The see capability SHALL capture from a `user-gui` or `virtual-gui` session only, using one of its supported viewpoints. A headless job MUST NOT provide an interactive see surface, and a failed GUI capture MUST NOT fall back to a headless Workbench render.

#### Scenario: see captures a session viewport
- **WHEN** see is called on a ready GUI session
- **THEN** it returns the current viewport image from that session and includes its instanceId

#### Scenario: see on a headless job is rejected
- **WHEN** see is called for a headless job
- **THEN** it returns a structured boundary error and does not start a background Blender for a frame

### Requirement: see supports viewport, camera, and custom viewpoints

`see` SHALL accept `viewpoint` values `viewport` (default), `camera`, and `custom`. `viewpoint=custom` SHALL require `from` and `look_at`; `viewpoint=viewport` SHALL reject camera and custom-only fields; `viewpoint=camera` SHALL accept `camera=true` for the active scene camera or a camera object name, and SHALL reject `from`, `look_at`, `lens`, `ortho`, `ortho_scale`, `full`, `area`, and `aspect`.

#### Scenario: Camera view uses the active scene camera
- **WHEN** see is called with viewpoint=camera and no camera name
- **THEN** it captures from the active scene camera and reports the camera name and actual dimensions

#### Scenario: Named camera view
- **WHEN** see is called with viewpoint=camera and camera="Camera.001"
- **THEN** it captures from that camera object and rejects a non-camera object

#### Scenario: Invalid viewpoint combinations are rejected
- **WHEN** viewpoint=camera is combined with from/look_at, or viewpoint=custom lacks from/look_at
- **THEN** it returns invalid_params naming the offending field and captures nothing

#### Scenario: Custom frame rejects full and area
- **WHEN** see is called with viewpoint=custom and full or area
- **THEN** it returns invalid_params naming the incompatible parameter and captures nothing

### Requirement: camera view is current-frame and read-only

`viewpoint=camera` SHALL use the evaluated camera at the current scene frame, including object transforms, constraints, drivers, and animated camera data. It MUST NOT change `scene.frame_current`, insert or modify keyframes, change `scene.camera`, or mutate the scene, selection, or user viewport. `mode=sequence` with `viewpoint=camera` MUST return invalid_params and point to `run(kind=render)` for animation preview.

#### Scenario: Keyframed camera is evaluated at the current frame
- **WHEN** see is called with viewpoint=camera on a camera with transform or lens keyframes
- **THEN** it captures the evaluated current-frame pose and the keyframes and current frame remain unchanged

#### Scenario: Camera sequence is rejected
- **WHEN** see is called with viewpoint=camera and mode=sequence
- **THEN** it returns invalid_params and points to run(kind=render) for camera animation preview

### Requirement: camera view uses render framing

A camera-view frame SHALL use the evaluated render resolution ratio as its output aspect and report actual width and height. A panoramic camera SHALL return `unsupported`; depth of field and motion blur SHALL be marked preview-only; the fixed aspect presets and `full`/`area` do not apply. `max_size` SHALL default to 1024 and accept only 544, 768, 1024, or 1280.

#### Scenario: Camera view keeps the render aspect
- **WHEN** the render resolution is 1920x1080 and max_size is 1024
- **THEN** the camera view reports 1024x576 and does not use a viewport/custom fixed preset

#### Scenario: Panoramic camera is unsupported
- **WHEN** viewpoint=camera selects a panoramic camera
- **THEN** it returns unsupported and points to run(kind=render)

#### Scenario: Depth of field is preview-only
- **WHEN** viewpoint=camera selects a camera with depth of field enabled
- **THEN** the receipt marks preview_only and dof_ignored while the capture remains current-frame only

### Requirement: see exposes frame and sequence capture modes

`see` SHALL expose `mode=frame` (default) and `mode=sequence`. Frame SHALL support `viewpoint=viewport|camera|custom`; sequence SHALL require an explicit `viewpoint=viewport` or `viewpoint=custom` and MUST NOT infer the viewpoint from the presence of `from`/`look_at`. Both modes SHALL use only a ready GUI session and MUST NOT fall back to a headless worker or Workbench capture. `mode=sequence` MUST NOT execute code or mutate the scene, current frame, selection, or viewport.

#### Scenario: Default call is a single frame
- **WHEN** see is called without mode
- **THEN** it captures one viewport frame using the fixed preview presets and reports its actual width and height

#### Scenario: Sequence rejects unsupported capture options
- **WHEN** mode=sequence includes code, full, area, diagnostics, or force
- **THEN** it returns invalid_params naming the incompatible parameter and does not mutate the scene or viewport

#### Scenario: Sequence does not fall back
- **WHEN** no ready GUI session is available or every sample fails
- **THEN** it returns a session or transport error and does not start a headless worker or Workbench capture

### Requirement: sequence viewpoint is explicit

`mode=sequence` SHALL require an explicit `viewpoint=viewport` or `viewpoint=custom`. It MUST NOT infer the viewpoint from `from`/`look_at`, MUST NOT accept `viewpoint=camera`, and MUST reject `full`/`area` with `custom`.

#### Scenario: Custom sequence rejects full and area
- **WHEN** see is called with mode=sequence, viewpoint=custom, and full or area
- **THEN** it returns invalid_params and captures nothing

### Requirement: sequence capture is bounded and observational

`mode=sequence` SHALL sample an explicit `viewpoint=viewport` or `viewpoint=custom` over a wall-clock window of 0.2–20 seconds at 0.5–8 fps, with `round(seconds*fps)` between 1 and 32, and MUST return `invalid_params` without clamping outside those bounds. It MUST NOT accept `viewpoint=camera` and SHALL return a timeline of timestamps and hashes with at most six attached distinct frames.

#### Scenario: Sequence is bounded and observational
- **WHEN** see is called with mode=sequence, seconds=2, and fps=4
- **THEN** it samples eight frames or fewer over roughly two seconds, returns a timeline with timestamps and hashes, attaches at most six distinct frames, and reports static=true when every hash matches

#### Scenario: Out-of-range sequence is rejected
- **WHEN** seconds, fps, or round(seconds*fps) is outside the allowed range
- **THEN** it returns invalid_params with the allowed range and does not clamp or capture

### Requirement: see preserves the user viewport and publishes presence

When `see` uses `from`/`look_at` or `viewpoint=camera`, it SHALL capture offscreen and MUST NOT move the user viewport, camera, selection, or current frame. A successful custom capture SHALL publish the caller pose; a successful camera capture SHALL publish the evaluated view matrix and camera name; a viewport capture SHALL mark the caller as following the user view. Presence publication MUST NOT require a write scope grant.

#### Scenario: Custom sequence leaves the user viewport unchanged
- **WHEN** see is called with mode=sequence and from/look_at
- **THEN** every sample uses the same offscreen pose and the user viewport, camera, selection, and current frame are unchanged

#### Scenario: Presence records the custom pose
- **WHEN** a custom-view see succeeds
- **THEN** the caller presence view pose is updated and the user can see the agent frustum

#### Scenario: Viewport observation pose
- **WHEN** see is called without from/look_at
- **THEN** presence marks the caller as following the user view and does not draw an independent frustum

#### Scenario: Camera preview leaves the scene unchanged
- **WHEN** see is called with viewpoint=camera on a keyframed camera
- **THEN** it captures from the evaluated current-frame pose and the current frame, camera, selection, and user viewport are unchanged

#### Scenario: Presence records the camera view
- **WHEN** a camera-view see succeeds
- **THEN** the caller presence view matrix and camera name are updated and the user can see the agent frustum

### Requirement: see preview size is a fixed enum

For `viewpoint=viewport` and `viewpoint=custom`, See SHALL accept `max_size` only from a fixed set per aspect: `1:1` = `544|768|1024|1280`; `4:3` = `640|960|1280`; `16:9` = `1024|1152|1280`. A value outside that aspect's set MUST return `invalid_params` naming the allowed values and MUST NOT be clamped. The model-facing see surface MUST NOT accept an arbitrary `view_size`; supplying it MUST return `invalid_params`.

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

For `viewpoint=viewport` and `viewpoint=custom`, every option SHALL have at least `544×544` total pixels and at most `1300×1300` total pixels. The smallest option per aspect SHALL be `544×544`, `640×480`, and `1024×576`; the largest SHALL be `1280×1280`, `1280×960`, and `1280×720`. A `full=true` screenshot SHALL preserve its captured ratio and use a fixed 1024-pixel longest edge; `aspect` and `max_size` do not apply to it.

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

Instances SHALL NOT share Python state, scene files, session ownership, or scope grants. A failure or disconnect of one instance MUST NOT mark another instance unavailable, and a job assigned to one session MUST NOT execute in another.

#### Scenario: One instance fails
- **WHEN** one session loses its addon connection
- **THEN** other connected sessions remain ready and routable

#### Scenario: Execution does not cross instances
- **WHEN** a mutating operation is addressed to one session
- **THEN** the operation is executed only by that instance and its result references the same instanceId