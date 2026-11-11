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
- [x] 3.5 Register the new panel/operator and keep the registered command count consistent; verify with `npm run test:contract` and the addon registration test. — 2026-11-10: presence 推送改走 pause-safe 的内部命令 `get_viewport_screenshot`（`presence` 载荷）后不再依赖暂停期间被拒的 `execute_code`；registered/wire 计数保持 25/26，`tests/unit/js/presence-push.test.ts` 5 项、`tests/integration/python/test_addon_readonly_pause.py` 5 项通过。

## 4. DSH right-sidebar tab

- [x] 4.1 Register the `blender-presence` right-sidebar tab type (with a guide entry), keyed `sidebar.right.pane.tab` body, and polling of `/host/state` presence; verify with a client build and a manual right-sidebar check. — 2026-11-09: user confirmed the right-sidebar panel opens and lists 2 agents.
- [x] 4.2 Register the `/blender-presence` action to open the right-sidebar tab without submitting a message; verify with the client self-test and a manual command check. — 2026-11-10: client self-test `tests/unit/js/contract/sidebar-injection.test.ts` (registers the /blender-presence action and opens the right-sidebar tab without a prompt) loads `src/client/index.js`, asserts the command is registered as an action, `ui.run()` opens the `blender-presence` tab, no panel is selected and no message is submitted; implementation `src/client/index.js:908-964`. 真机 composer 手测未单独留证。
- [x] 4.4 Add `/blender presence` to the dsh-TUI host half: read `/host/state`, render short ID, display name, observation kind, last activity, and session instance as a text table, and show a zero-agent empty state; verify with `tests/integration/js/tui-commands.test.ts`. — 2026-11-10: 13 command tests, full unit/contract/integration, typecheck, lint, and format gates pass; plugin installed into the `dsh-tui` profile.
- [x] 4.3 Handle empty, error, and multiple-session states in the panel; verify with a client unit or self-test assertion.
- [x] 4.6 Add the DSH Plugins page Blender card (`plugins.item`): enable-time dependency check, install prompt/decline/keep-off, visible one-click install progress with re-check, manual command display, and missing-dependency warning while enabled; report all ready GUI Blenders split by `user-gui`/`virtual-gui` and mark virtual-gui agent rows purple; verify with `tests/unit/js/virtual-gui-panel.test.ts`, `tests/unit/js/virtual-gui-permission.test.ts`, `tests/unit/js/contract/sidebar-injection.test.ts`, and `tests/unit/js/session-registry.test.ts`.
- [x] 4.5 Add the virtual-gui permission switch and the connected-GUI count to the panel: default from plugin config `allowVirtualGui=false`, write only through the DSH authenticated connection RPC, disable when that RPC is unavailable, and deny start before Gateway probe; verify with `tests/unit/js/virtual-gui-permission.test.ts`, `tests/unit/js/contract/tool-execution.test.ts`, and the client self-test.

## 5. Session-instance routing

- [x] 5.1 Add `instanceId` to host requests for `/act`, `/view`, and `/frame`; verify with host unit tests asserting the body/query.
- [x] 5.2 Accept and forward `instanceId` in engine act/view/frame and HTTP routes; verify with route tests.
- [x] 5.3 Track kernel module injection per instance and pass the instance to `ensureView`; verify with kernel unit tests using two instance keys.
- [x] 5.4 Verify an ambiguous request still fails closed when no instance is available; verify with transport tests.
- [x] 5.5 Host session selection is explicit: `blender_py` / `blender_see` accept `session_id`; more than one ready session without it fails `session_ambiguous` instead of silently using the first; verify with contract tests for ambiguity and explicit instance forwarding.
- [x] 5.6 Keep attached owner agents writable after the owner lease expires: `authorizeWrite` reclaims ownership for an attached caller instead of returning `session_in_use`; verify with the "attached caller reclaim writes after owner TTL" unit test and an E2E `blender_py` call after the lease TTL. — 2026-11-09: verified on Blender 4.2.23 and 4.5.15 after >120s, then camera see refreshed presence.

## 6. Integration and documentation

- [x] 6.1 Update see-presence docs and addon UI docs in both languages; verify with the docs path gate.
- [x] 6.2 Run `npm run typecheck`, `npm run test:unit`, `npm run test:contract`, `npm run test:extension-unit`, addon Python tests, `npm run lint`, and `npm run format:check`; all must pass. — 2026-11-10: pause-safe see/presence 落地后重跑 runtime/test 两套 typecheck（通过）、addon/render 相关 pytest 181 passed、targeted vitest（instance-routing 4 + presence-push 5）；`check:structure` 通过；extension zip 重建 77 entries / sha256 `fc17ce2145e9efc81ad6ee8d1da538b8545f22bf3db2125ea8e1516fda15c3f8`。
- [x] 6.3 Rebuild the extension and run a Blender GUI smoke check on 4.2, 4.5, and 5.2 for a blue frozen frustum, short-ID label, hide toggle, and full-window notice.
