# Spec Delta

## Purpose

定义 3.1 的会话边界：会话只表示可见 Blender GUI 的交互实例，分为用户自启并附着的 user-gui 与 Gateway 托管的 virtual-gui；无头作业不属于会话。

## MODIFIED Requirements

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

#### Scenario: A detached agent cannot write
- **WHEN** an agent detaches and then calls a mutating operation on the same session
- **THEN** it receives `session_in_use` telling it to attach first, and the grants it held are released immediately rather than at their TTL

#### Scenario: A session without an owner stays readable
- **WHEN** the owner's heartbeat expires or the user-gui instance disconnects
- **THEN** the agents that were attached can still read the session and its presence, while a caller that never attached receives `session_in_use`

### Requirement: Session mutations require an exclusive grant

Mutating `session` operations, including txn `restore`, `mark`, and `revert`, SHALL require an `exclusive` grant and SHALL be rejected during human pause. Read-only session queries, `see`, and presence SHALL remain available.

#### Scenario: txn restore without a grant is rejected
- **WHEN** an agent calls txn restore without an exclusive grant
- **THEN** the call fails with scope_conflict and executes nothing

#### Scenario: txn write ignores a narrower declared scope
- **WHEN** an agent holding only a collection grant calls txn restore and declares that same scope
- **THEN** the call is gated as exclusive and fails with scope_conflict

### Requirement: Human edits take priority

In the Blender UI, the human is not a DSH caller and MUST NOT be modeled as an agent or scope holder. The human SHALL always be able to edit the scene. The addon SHALL provide pause/resume; pause blocks all agent writes but not reads, `see`, or presence. When the human modifies data covered by an agent's grant, the next agent write SHALL fail with `human_modified` and require a re-read. 3.1 MUST NOT merge concurrent edits or provide per-actor undo.

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

#### Scenario: Only the blocked agent clears the conflict
- **WHEN** an agent that never wrote calls `mark_read` for a scope whose edit blocked another agent
- **THEN** nothing is cleared, and only the agent whose write failed with human_modified can clear it

### Requirement: virtual-gui is Gateway-managed and user-invisible

A `virtual-gui` session SHALL be started and stopped by Gateway through a DisplayProvider. It SHALL be owned by the DSH session that requested it, and only that session's agents MAY attach. The Blender process MUST run as a real GUI process with `bpy.app.background == False` and MUST NOT create a user-visible top-level window. The session MUST have a bounded instance limit and MUST be cleaned up when stopped or when its owner disconnects. The capacity check MUST count starts already in flight.

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
- **WHEN** the configured virtual-gui limit is reached, or a start is still in flight
- **THEN** a new request fails with a structured capacity error and does not start an extra process

#### Scenario: An ownerless virtual-gui is stopped
- **WHEN** a virtual-gui owner's lease lapses and the reclaim grace passes
- **THEN** Gateway stops the display provider and removes the session instead of leaving an orphan that holds a GPU slot

### Requirement: see preview options respect the model pixel bounds

For `viewpoint=viewport` and `viewpoint=custom`, every option SHALL have at least `544×544` total pixels and at most `1300×1300` total pixels. The smallest option per aspect SHALL be `544×544`, `640×480`, and `1024×576`; the largest SHALL be `1280×1280`, `1280×960`, and `1280×720`. A `full=true` screenshot SHALL preserve its captured ratio and use a fixed 1024-pixel longest edge; `aspect` and `max_size` do not apply to it.

#### Scenario: Smallest options meet the lower bound

- **WHEN** the minimum option is selected for any aspect
- **THEN** its total pixels are at least 544×544 and the image is not below the model's small-image threshold

#### Scenario: Largest options stay under the upper bound

- **WHEN** the maximum option is selected for any aspect
- **THEN** its total pixels are at most 1300×1300

#### Scenario: Full screenshots use a fixed bound

- **WHEN** see is called with `full=true` and no `area`
- **THEN** the whole Blender window is captured (`screen.screenshot`) with a 1024-pixel longest edge, and the receipt names the method
- **AND** with an explicit `area` the named editor is captured instead, `invalid_area` is returned for an out-of-range index, and a missing `area` never falls back to `areas[0]` (usually PROPERTIES) but to the 3D viewport
