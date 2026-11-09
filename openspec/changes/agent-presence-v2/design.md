# Design: Agent presence v2

## Context

See `proposal.md` for motivation. The current data path is: `see` captures an image through the view route, then separately publishes a `ViewPose` to the session registry; the registry pushes presence rows to the addon, which draws them in a POST_VIEW handler. The pose currently carries no projection or pixel size for camera/viewport, and viewport poses are intentionally marked as "following the user". The addon therefore either skips drawing or falls back to an approximate lens-based pyramid.

The session record already carries the addon `instanceId` that the user-gui attached to, but host tools do not include it in view/execution/frame requests. Gateway correctly rejects an unqualified request when more than one addon is ready.

## Goals / Non-Goals

**Goals**
- Turn every successful observation into a durable, drawable pose without moving the user's view or camera.
- Make the frustum shape derive from the same projection used for capture.
- Give users identity, visibility, and color controls that survive normal redraws.
- Expose the same presence data in the DSH sidebar without adding a model-facing tool.
- Remove avoidable ambiguity from session-bound routing.

**Non-Goals**
- No new scene objects, markers, or baked geometry for presence.
- No persistent presence history; each agent keeps only its latest observation.
- No per-agent color persistence across sessions.
- No change to the existing capture image format or the see receipt shape beyond additive metadata.

## Decisions

### 1. Publish an explicit projection and frozen pose

All three observation kinds publish a pose containing the view matrix, projection matrix, pixel width/height, and the source viewpoint. A viewport capture publishes the matrices captured at that moment and stops using the "follow the user" marker. Rationale: the addon cannot reconstruct the exact aspect or lens/shift/orthographic shape from a view matrix alone. Keeping the pose frozen is the only way to answer "where did the agent look" after the user moves. Alternative rejected: re-read the live viewport each redraw; it would make the frustum follow the user and lose the historical position.

### 2. Derive frustum corners by unprojecting NDC

The addon computes the four NDC corners of the captured image, transforms them through the inverse projection into camera space, then scales the direction to a fixed depth. Orthographic poses draw two parallel rectangles. The projectionless fallback uses lens and pixel aspect, and is explicitly approximate. Rationale: this reproduces lens, sensor fit, shift, and aspect without duplicating Blender's projection math. Alternative rejected: keep the fixed 36 mm/2.0-depth pyramid; it is visibly wrong for 1:1 and shifted captures.

### 3. Opaque identity plus a session-local short ID

The session row gets `id = first 12 hex chars of sha1(callerKey)` and `shortId = A1..A8` assigned in attach order. Labels and the DSH list show the short ID (and display name); the DSH panel may also show a short prefix of the opaque ID for disambiguation. Rationale: hashes are stable across reconnects and safe to persist in logs; short IDs are readable. Alternative rejected: use the display name as identity; names are user-supplied and can collide.

### 4. Color policy belongs to the addon, identity to the Gateway

The Gateway continues to publish a per-agent color from a blue-first palette, but the addon applies the user's preference: default blue for all agents unless "color by agent" is enabled. The preference is read at draw time so changes apply on the next redraw. Rationale: color is presentation, and the user explicitly asked for it in Blender preferences and the sidebar. Alternative rejected: remove per-agent colors from the Gateway; that would break clients that already rely on them and lose information for the DSH panel.

### 5. Hide locally, never mutate Gateway state

Visibility is an addon-local set keyed by opaque ID. The sidebar and preferences only alter that set; the Gateway still sees the agent as attached and active. The set is cleared when the entry disappears or presence is cleared. Rationale: hiding must not stop an agent from observing, and no new write-scope/grant semantics are needed. Alternative rejected: route a "hidden" flag through the Gateway; it would make a local display preference part of the protocol and require an extra round trip per click.

### 6. Full-window captures notify instead of drawing

`full=true` is a screenshot of the whole Blender window, not an observation through a camera. It publishes a transient notice and leaves the last frustum untouched. The addon shows it via `bpy.context.workspace.status_text_set` with a timer that clears it. Rationale: Blender has no public operator-independent transient INFO API; status text is the native, non-blocking surface. Alternative rejected: add a 3D marker for full captures; it would imply a viewpoint that does not exist.

### 7. Carry instanceId explicitly through every session-bound layer

The host reads `instanceId` from the session row and adds it to `/act`, `/view`, and `/frame` requests. Engine methods accept an optional instance and pass it to `addon.send`; routes forward it. Kernel injection tracks `(instanceId, module)` pairs, and `ensureView` takes the instance. The same rule applies one layer up: `blender_py` and `blender_see` take an optional `session_id`, and a call without one fails `session_ambiguous` when several ready sessions exist instead of silently selecting the first row. Rationale: transport already supports an explicit instance and already fails closed on ambiguity; the missing link is the host/runtime plumbing. Alternative rejected: infer the instance from the caller's session inside Gateway; Gateway has no DSH session identity on these routes and inference would violate the "never choose silently" rule.

### 8. DSH panel is read-only and polling-based

The web client registers a `sidebar.panellist` entry, a main panel keyed `blender-presence`, and a `/blender-presence` action that selects the panel. The panel polls `/host/state` (extended with a `presence` field) while visible. Rationale: no new HTTP route or contract count, and the existing client already has a backend base URL. Alternative rejected: push over a websocket; the client has no general push channel for plugin panels and polling is adequate for a human-readable list.

## Risks / Trade-offs

- [Projection matrices are column-major/flat in transport and nested 4x4 in some paths] -> Normalize with one helper and add a Python test that round-trips a known 1:1 and 16:9 projection.
- [Frozen viewport frustum becomes stale when the scene/camera changes] -> It represents the observation, not the current scene; labels and "last activity" make the age explicit.
- [More than eight agents] -> Keep the existing bounded list; short IDs wrap or are omitted beyond the cap with a count in the panel.
- [Status-bar notice can be overwritten by another addon] -> Set and clear it from a timer; accept that another status writer can win, as it is a transient hint.
- [Preference color is shared across sessions] -> It is a user preference, not session state; the selected color persists in Blender preferences as intended.
- [Host tests do not exercise a real multi-instance Gateway] -> Add route-level tests that assert the instance is forwarded, and an e2e check with two fake instances if the harness allows it.

## Migration Plan

1. Additive fields only: old sessions without `id`/`shortId` still render using a derived fallback; old poses without projection use the lens fallback.
2. Host and runtime are released together; a new addon paired with an old host simply receives richer poses when available and keeps the fallback otherwise.
3. No storage migration. Presence is in-memory session state.
4. Rollback is a normal revert; no scene data is written.

## Open Questions

None blocking implementation. A future change may decide whether full-window captures deserve a non-frustum scene marker; this change deliberately leaves the pose untouched.
