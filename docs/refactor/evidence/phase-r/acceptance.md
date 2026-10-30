# phase-r — §8.2 B5 gate

## Scope

§8.2 B5 and §10.8: run the build, typecheck, contract, parity, pack, clean-install smoke, and the B1 small regression; then verify the Phase R facts (zero .mjs, strict compile into lib/runtime, no @ts-nocheck / no new explicit any, contract snapshot unchanged, TS imports in unit/integration, built JS for E2E/spawn/pack/install, tarball contents, SERVER_PATH, source maps, no lib in git, v2.6.2 rollback); evidence lives here.

## Result

PASS for every item except B5.12 (B1 3-question regression, NOT EXECUTED because the WSL B1 harness has no DSH profiles / fixed v2.6.1 plugin on this host; handed to T16/D4) and R1 (non-author review pending). No pass is claimed for those two.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| B5.1 runtime/**/*.mjs = 0; every runtime TS module strict-compiled to lib/runtime/**/*.js | PASS | scan.log · build.log (62 JS + 62 maps + 62 d.ts) | author self-check | 2026-10-30 |
| B5.2 no @ts-nocheck; no new explicit any; JSON/subprocess/path/Python boundaries parsed | PASS | scan.log (0 @ts-nocheck, 0 explicit any) · lint.log (no-explicit-any) | author self-check | 2026-10-30 |
| B5.3 contract snapshot unchanged: 15 tools / 34 families / 228 ops / 39 commands / 29 routes / protocol 11 | PASS | parity.json (PASS) · contract.log · matrix.log | author self-check | 2026-10-30 |
| B5.4 unit/integration import TS; E2E/backend spawn/pack/install use built lib/runtime JS | PASS | test.log · e2e.log · install-smoke.log · clean2-results.txt | author self-check | 2026-10-30 |
| B5.5 tarball contains lib/runtime/**/*.js + runtime/**/*.py and no .ts | PASS | pack-list.txt · pack-counts.json (62 lib JS, 85 Python, 0 non-.d.ts TS) | author self-check | 2026-10-30 |
| B5.6 SERVER_PATH resolves to lib/runtime/server.js (DSH_BLENDER_SERVER override kept) | PASS | install-smoke.log (SMOKE SERVER_PATH) · src/constants.ts | author self-check | 2026-10-30 |
| B5.7 source maps readable | PASS | build.log · lib/runtime/engine.js.map (inline sources) | author self-check | 2026-10-30 |
| B5.8 git status shows no lib/ | PASS | scan.log (lib tracked in git: 0) · .gitignore · git rm --cached lib | author self-check | 2026-10-30 |
| B5.9 v2.6.2 rollback rehearsal | PASS (Git level); build-level limitation recorded | rollback.md · rollback2.log | author self-check | 2026-10-30 |
| B5.10 clean install smoke: doctor kind=ok and the packaged backend serves | PASS | install-smoke.log (clean clone) · install-smoke-final.log (final tree) | author self-check | 2026-10-30 |
| B5.11 clean environment build+test+pack+install all green | PASS | clean2-results.txt (ci/build/checkbuild/typecheck/test/packdry/installsmoke = 0) | author self-check | 2026-10-30 |
| B5.12 B1 3-question small regression | NOT EXECUTED — benchmark environment incomplete; handed to T16/D4 | b1-regression.md | author self-check | 2026-10-30 |
| R1 non-author review | PENDING | review.md | — | — |

## Caveats

- B1 regression not executed; see b1-regression.md. It is release-environment work (T16/D4), not T15 code work.
- pack-list.txt is from the clean clone of code commit 46f2d83 (683 entries). Packing the final evidence commit adds only docs/refactor/evidence (780 entries; see pack-counts-final.json); code entries and all B5 checks are identical, and the final-tree install smoke (install-smoke-final.log) still returns doctor kind=ok and a serving backend.
- No CI workflow exists on this branch (T12 unchecked); the dual import/spawn paths were proven locally and in a clean clone.
- Only Blender 4.2 headless E2E ran; GUI rendering and Blender 5.x were unavailable.
- frame/render latency was not measured (no GUI Blender/addon); perf.log measures engine import, backend start, /health, /status, and an addon doctor round-trip, and says so.
