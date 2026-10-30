# T15c — server/http migration

## Scope

§7.2 T15c and §4.5: the server/http modules moved to .ts; 29 routes, lease, read-only, and lifecycle contracts unchanged; an E2E smoke passes against the built runtime.

## Result

PASS: CONTRACT_ROUTES still has 29 entries, route/lease/lifecycle contract tests and the HTTP integration tests are green, and the packaged backend serves /health and prints the 29 routes.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T15c.1 server/http modules migrated to .ts (server.ts, http/*, http/routes/*) | PASS | runtime/http/**/*.ts · build.log | author self-check | 2026-10-30 |
| T15c.2 29 routes contract unchanged | PASS | route-contract.log · parity.json (routes 29) · contract.log | author self-check | 2026-10-30 |
| T15c.3 lease, read-only, and lifecycle contracts unchanged | PASS | test.log (http-lease, lease-stale, lease-scope, lifecycle-config) | author self-check | 2026-10-30 |
| T15c.4 E2E smoke: built backend serves /health and starts with 29 routes | PASS | e2e.log · install-smoke.log (SMOKE health · 29 route line) | author self-check | 2026-10-30 |

## Caveats

- The E2E smoke is Blender 4.2 headless only. A GUI session and a live addon were not available on this host, so GUI rendering and frame latency are not part of this evidence (see phase-r/perf.log).
