# Proposal: Agent presence v2

## Why

Agent presence exists in the Gateway session model, but a user cannot reliably see where an agent looked. A viewport capture deliberately publishes only "following the user", a camera capture publishes no projection or frame size, and a full-window capture publishes no independent pose at all. The addon also has no stable per-agent identity, no hide control, and no list of who is looking where. When more than one Blender instance is connected, session-bound calls can fail with an ambiguity error because the session's instance is not carried through the request path.

The result is that the user cannot answer the basic question: "which agent looked from where, and when?"

## What Changes

- A successful viewport, camera, or custom `see` publishes an independent observation pose with its projection matrix and pixel dimensions. A viewport capture freezes the pose at capture time, so moving the user viewport afterwards does not move the recorded frustum.
- The addon draws a projection-accurate frustum for every published pose, including viewport captures, with the same aspect ratio and lens/orthographic shape as the captured image.
- Each agent gets a stable opaque ID and a human-facing short ID. Short IDs and labels are shown by default; color-coding of agents is available but off by default.
- The default presence color is blue, configurable in Blender preferences and from the Blender sidebar. The sidebar lists each agent, its short ID, last activity, and a per-row visibility toggle.
- A full-window screenshot does not change the recorded pose; it shows a transient status-bar notice instead of drawing a frustum.
- The DSH web client exposes the presence list in its own right-sidebar tab, opened by `/blender-presence`. The DSH TUI exposes the same data through the explicit `/blender presence` command as a text table; it never auto-injects the list into the conversation.
- Session-bound `/act`, `/view`, and `/frame` requests carry the session's `instanceId`, and view-module injection is tracked per instance. Ambiguity remains only for callers that genuinely omit an instance while several are connected.

## Capabilities

### New Capabilities

- `blender/agent-presence-ui`: user-facing presence rendering, identity, visibility controls, Blender sidebar list, preferences, and the DSH right-sidebar tab/command.

### Modified Capabilities

- `blender/sessions`: presence records carry stable identity, activity timestamps, frozen viewport poses with projection, and transient notices; `see` publishes those records.
- `blender/gateway`: session-bound requests route to the session's instance, and per-instance module injection no longer leaks across instances.

## Impact

- Gateway sessions and presence publication: `runtime/core/sessions/*`, `runtime/gateway/presence-push.ts`, `runtime/core/engine-api/*`, `runtime/http/routes/*`, `runtime/py/render/view.py`.
- Host tools: `src/tools/see/*`, `src/tools/py/execute.ts`, `src/host/http.ts`, `src/client/index.js`.
- Blender addon: `presence_draw.py`, `ui/panels/index.py`, `ui/preferences.py`, `registration.py`.
- Tests: unit tests for presence identity/routing, Python tests for presence draw state, extension tests for preferences/panel, e2e presence test updated to assert the new publication shape.
- Documentation: `docs/` presence/see sections and the addon command count if a new operator is registered.
