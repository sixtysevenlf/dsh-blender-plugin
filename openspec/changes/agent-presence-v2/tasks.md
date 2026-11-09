# Tasks

## 1. Presence data model and publication

- [x] 1.1 Add stable `id`, `shortId`, `lastActivityAt`, `lastActivity` to presence rows, preserving them across observations and selections; verify with `npm run test:unit` presence tests.
- [x] 1.2 Add a transient `notice` field to session records and include it in presence pushes without clearing poses; verify with unit tests for publish payloads.
- [x] 1.3 Extend the session API and registry for `presence_notice`; verify with route/registry tests.
- [x] 1.4 Add the blue-first palette and default color behavior; verify existing and new color tests pass.

## 2. Capture metadata and see publication

- [x] 2.1 Return projection matrix, width, height, ortho flag, and lens from the view route header for viewport, camera, and custom captures; verify with Python view tests.
- [x] 2.2 Publish a frozen viewport pose with projection/dimensions after a successful viewport capture; publish projection/dimensions for camera and custom; verify with see unit tests.
- [x] 2.3 Publish a notice instead of a pose for full-window captures; verify with a see unit test.
- [x] 2.4 Thread the session instanceId through view/sequence requests; verify with host fetch tests.

## 3. Blender addon presence UI

- [x] 3.1 Replace the lens-only pyramid with projection-based frustum corners and aspect-correct fallback; verify with Python unit tests for 1:1, 16:9, and orthographic poses.
- [x] 3.2 Render labels with short IDs and apply the preference-controlled color policy (default blue, optional per-agent color); verify with Python unit tests for color resolution and label text.
- [x] 3.3 Add per-agent hide state and a sidebar list showing short ID, display name, last activity, and a hide toggle; verify via extension unit tests and a Blender GUI check.
- [x] 3.4 Add presence preferences (color, color-by-agent, show IDs) and a status-bar notice consumer; verify via extension unit tests.
- [x] 3.5 Register the new panel/operator and keep the registered command count consistent; verify with `npm run test:contract` and the addon registration test.

## 4. DSH right-sidebar tab

- [x] 4.1 Register the `blender-presence` right-sidebar tab type (with a guide entry), keyed `sidebar.right.pane.tab` body, and polling of `/host/state` presence; verify with a client build and a manual right-sidebar check. — 2026-11-09: user confirmed the right-sidebar panel opens and lists 2 agents.
- [ ] 4.2 Register the `/blender-presence` action to open the right-sidebar tab without submitting a message; verify with the client self-test and a manual command check.
- [x] 4.4 Add `/blender presence` to the dsh-TUI host half: read `/host/state`, render short ID, display name, observation kind, last activity, and session instance as a text table, and show a zero-agent empty state; verify with `tests/integration/js/tui-commands.test.ts`. — 2026-11-10: 13 command tests, full unit/contract/integration, typecheck, lint, and format gates pass; plugin installed into the `dsh-tui` profile.
- [x] 4.3 Handle empty, error, and multiple-session states in the panel; verify with a client unit or self-test assertion.

## 5. Session-instance routing

- [x] 5.1 Add `instanceId` to host requests for `/act`, `/view`, and `/frame`; verify with host unit tests asserting the body/query.
- [x] 5.2 Accept and forward `instanceId` in engine act/view/frame and HTTP routes; verify with route tests.
- [x] 5.3 Track kernel module injection per instance and pass the instance to `ensureView`; verify with kernel unit tests using two instance keys.
- [x] 5.4 Verify an ambiguous request still fails closed when no instance is available; verify with transport tests.
- [x] 5.5 Host session selection is explicit: `blender_py` / `blender_see` accept `session_id`; more than one ready session without it fails `session_ambiguous` instead of silently using the first; verify with contract tests for ambiguity and explicit instance forwarding.
- [x] 5.6 Keep attached owner agents writable after the owner lease expires: `authorizeWrite` reclaims ownership for an attached caller instead of returning `session_in_use`; verify with the "attached caller reclaim writes after owner TTL" unit test and an E2E `blender_py` call after the lease TTL. — 2026-11-09: verified on Blender 4.2.23 and 4.5.15 after >120s, then camera see refreshed presence.

## 6. Integration and documentation

- [x] 6.1 Update see-presence docs and addon UI docs in both languages; verify with the docs path gate.
- [x] 6.2 Run `npm run typecheck`, `npm run test:unit`, `npm run test:contract`, `npm run test:extension-unit`, addon Python tests, `npm run lint`, and `npm run format:check`; all must pass.
- [x] 6.3 Rebuild the extension and run a Blender GUI smoke check on 4.2, 4.5, and 5.2 for a blue frozen frustum, short-ID label, hide toggle, and full-window notice.
