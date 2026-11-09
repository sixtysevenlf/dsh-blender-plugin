# Blender sessions (presence delta)

## MODIFIED Requirements

### Requirement: see preserves the user viewport and publishes presence

When `see` uses `from`/`look_at` or `viewpoint=camera`, it SHALL capture offscreen and MUST NOT move the user viewport, camera, selection, or current frame. A successful custom capture SHALL publish the caller pose with its projection matrix and pixel dimensions; a successful camera capture SHALL publish the evaluated view matrix, projection matrix, pixel dimensions, and camera name; a successful viewport capture SHALL publish the frozen view matrix, projection matrix, and pixel dimensions observed at capture time and MUST NOT leave the caller merely following the user view. A full-window screenshot SHALL leave any existing pose unchanged and SHALL publish a transient status notice instead. Presence publication MUST NOT require a write scope grant.

#### Scenario: Custom sequence leaves the user viewport unchanged
- **WHEN** see is called with mode=sequence and from/look_at
- **THEN** every sample uses the same offscreen pose and the user viewport, camera, selection, and current frame are unchanged

#### Scenario: Presence records the custom pose
- **WHEN** a custom-view see succeeds
- **THEN** the caller presence pose includes the projection and pixel dimensions and the user can see the agent frustum

#### Scenario: Viewport observation pose
- **WHEN** see is called without from/look_at and the user later changes the viewport
- **THEN** the caller presence pose remains the captured view matrix and projection, and the user can see a stationary agent frustum

#### Scenario: Camera preview leaves the scene unchanged
- **WHEN** see is called with viewpoint=camera on a keyframed camera
- **THEN** it captures from the evaluated current-frame pose and the current frame, camera, selection, and user viewport are unchanged

#### Scenario: Presence records the camera view
- **WHEN** a camera-view see succeeds
- **THEN** the caller presence row is updated with the view matrix, projection, dimensions, and camera name

#### Scenario: Full-window screenshot leaves the pose alone
- **WHEN** see is called with full=true
- **THEN** no pose is created or replaced and the user receives a transient status notice about the capture

## ADDED Requirements

### Requirement: Presence entries expose stable identity and activity

Each presence entry SHALL expose a stable opaque identifier derived from the caller identity, a short human-facing identifier unique among attached agents, a display name, a color, the current observation pose, the selected object identifiers, and a timestamp plus a short label for the most recent activity. Identity and short identifier MUST remain stable while the caller stays attached and MUST NOT change when the agent observes, selects, or pauses.

#### Scenario: Identity survives repeated activity
- **WHEN** an attached agent performs several observations and selections
- **THEN** its opaque identifier and short identifier remain unchanged and its last activity timestamp advances

#### Scenario: Short identifiers are unique per session
- **WHEN** two or more agents are attached to one session
- **THEN** each has a distinct short identifier assigned in attach order

### Requirement: Presence publication can show a transient notice

A user-gui session SHALL accept a transient presence notice independent of observation poses. The addon SHALL show the notice in Blender's status bar for a bounded lifetime and SHALL NOT draw it as a frustum or persist it after it expires.

#### Scenario: Notice appears and expires
- **WHEN** a full-window capture publishes a notice
- **THEN** the status bar shows the notice briefly and later redraws do not show it again

### Requirement: Attached owner agents keep write access after lease expiry

The session registry SHALL treat an attached caller as a legitimate writer when the owning DSH session's lease has expired. It MUST reclaim ownership for that caller on the next write instead of returning `session_in_use`; a caller that is not attached MUST still be rejected.

#### Scenario: Attached caller reclaims writes after TTL
- **WHEN** the owner lease has expired but the caller is still in the session's agent set
- **THEN** its write acquires ownership and proceeds without `session_in_use`

#### Scenario: Unattached caller is still rejected
- **WHEN** the owner lease has expired and the caller is not in the session's agent set
- **THEN** the write fails with `session_in_use`
