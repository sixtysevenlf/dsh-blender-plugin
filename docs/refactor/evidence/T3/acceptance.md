# T3 — Fake addon channel tests

## Scope
§7.1 B (last item): a fake Blender addon that can handshake, pause, succeed, fail,
time out, and disconnect without a real Blender or a real port.

## Result
PASS. 7 integration tests drive the real AddonClient against a fake flat-protocol
addon bound to an ephemeral port (never 9876).

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T3.1 fake addon binds an ephemeral port and is detectable as flat protocol | PASS | fake-addon.test.ts | author self-check | 2026-10-30 |
| T3.2 success handshake returns get_addon_info + ping payloads | PASS | fake-addon.test.ts | author self-check | 2026-10-30 |
| T3.3 error and pause are surfaced as rejections with the addon message | PASS | fake-addon.test.ts | author self-check | 2026-10-30 |
| T3.4 timeout rejects with `addon timeout: <type>` and does not hang the suite | PASS | fake-addon.test.ts | author self-check | 2026-10-30 |
| T3.5 socket close mid-request rejects with `socket closed` | PASS | fake-addon.test.ts | author self-check | 2026-10-30 |
| T3.6 the fake addon port is released after close | PASS | fake-addon.test.ts | author self-check | 2026-10-30 |

## Notes
- The fake addon is tests/helpers/fake-addon.mjs and speaks the flat JSON shape:
  request `{type, params}`, response `{status:"success", result}` or
  `{status:"error", message}`.
- AddonClient is imported from runtime/engine.mjs; no backend process is started.
