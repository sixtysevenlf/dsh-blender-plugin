# Agent presence UI

## ADDED Requirements

### Requirement: Every successful observation is visible as a frustum

For a user-gui session, the addon SHALL draw an independent frustum for each attached agent after any successful `see` observation: viewport, camera, or custom. A viewport observation SHALL freeze the pose at capture time; moving the user viewport afterwards MUST NOT move that agent's frustum. A full-window screenshot SHALL NOT create or replace a frustum pose.

#### Scenario: Viewport observation leaves a frozen frustum
- **WHEN** an agent runs `see` with the current viewport and then the user orbits the viewport
- **THEN** the agent's frustum remains at the captured camera position and orientation

#### Scenario: Camera and custom observations are drawn
- **WHEN** an agent runs `see` with `viewpoint=camera` or with `from`/`look_at`
- **THEN** a frustum matching that observation is drawn in the user's viewport

#### Scenario: Full-window screenshot does not move the frustum
- **WHEN** an agent runs `see` with `full=true`
- **THEN** the previously recorded pose is unchanged and a transient status-bar notice reports the full-window capture

### Requirement: Frustum geometry matches the captured image

The drawn frustum SHALL use the observation's projection and pixel dimensions, so its horizontal and vertical fields of view and aspect ratio match the captured image. A 1:1 capture SHALL produce a frustum whose cross-section is square; a 16:9 capture SHALL produce a 16:9 cross-section. Orthographic captures SHALL be drawn with parallel edges rather than a perspective apex.

#### Scenario: Aspect ratio is preserved
- **WHEN** an agent captures a 1:1 viewport and a 16:9 camera view
- **THEN** the two frusta show square and 16:9 cross-sections respectively

#### Scenario: Projection-less pose still uses its aspect
- **WHEN** a pose has lens information but no projection matrix
- **THEN** the addon uses the pose's pixel dimensions for aspect and labels the frustum as approximate rather than assuming a square

### Requirement: Agents are identifiable and distinguishable

Each presence entry SHALL have a stable opaque identifier and a human-facing short identifier. The addon SHALL label each frustum with its short identifier and display name by default. The default presence color SHALL be blue. Coloring each agent differently SHALL be available but disabled by default; when disabled all frusta use the configured presence color.

#### Scenario: Default labels
- **WHEN** two agents are attached
- **THEN** their frusta are labeled with distinct short identifiers such as A1 and A2, and both use the configured blue color

#### Scenario: Stable identity across activity
- **WHEN** an agent observes, changes selection, and observes again
- **THEN** its opaque identifier and short identifier do not change

#### Scenario: Optional per-agent colors
- **WHEN** per-agent coloring is enabled
- **THEN** attached agents receive visually distinct colors from the presence palette and keep them for the session

### Requirement: Presence entries can be hidden individually

The Blender sidebar SHALL list every attached agent with its short identifier, display name, last activity, and a per-row visibility control. Toggling the control SHALL hide or show only that agent's frustum, label, and selection outline. Hiding MUST NOT delete the agent's presence data or stop it observing. Hidden state SHALL survive redraws and SHALL reset when the presence entry disappears.

#### Scenario: Hide and restore one frustum
- **WHEN** the user hides agent A1 in the sidebar and then shows it again
- **THEN** only A1's drawing disappears and returns, while A2's drawing is unaffected

#### Scenario: Hidden agent remains listed
- **WHEN** A1 is hidden
- **THEN** A1 still appears in the sidebar with its last activity

### Requirement: Presence appearance is configurable in Blender preferences

Blender preferences SHALL expose the default presence color, per-agent coloring, and short-ID labels. Changes SHALL apply to subsequent redraws without restarting Blender. Invalid colors MUST fall back to the default blue rather than raising an error.

#### Scenario: Preference changes take effect
- **WHEN** the user changes the presence color in preferences
- **THEN** visible frusta use the new color on the next redraw

#### Scenario: Invalid color is rejected safely
- **WHEN** a stored or supplied color is not a valid hex color
- **THEN** the addon uses the default blue and continues drawing

### Requirement: DSH exposes the presence list in its sidebar

The DSH web client SHALL provide a right-sidebar tab listing connected Blender sessions and their agents, including each agent's short identifier, display name, last activity, and whether its observation is current. The `/blender-presence` action SHALL open that tab. The list MUST be fetched on demand or by polling and MUST NOT be rendered inline in the TUI conversation.

#### Scenario: Command opens the panel
- **WHEN** the user runs `/blender-presence`
- **THEN** the DSH sidebar selects the Blender presence panel without sending a chat message

#### Scenario: No agents
- **WHEN** the panel is opened with no attached agents
- **THEN** it shows an empty state rather than stale entries
