# T15a — Phase R baseline and build path

## Scope

§7.2 T15a, §10.2 and §10.4: module inventory checked against git, tsconfig.runtime.json, build/prepack/typecheck dual compile, SERVER_PATH pointing at lib/runtime/server.js, and the parity/pack check skeletons runnable.

## Result

PASS for the T15a completion criteria; the one scope gap is that this branch has no CI workflow yet (T12 not landed), so the dual test paths were proven locally and in a clean clone, not in CI.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T15a.1 runtime inventory matches git base: 60 base .mjs -> 60 .ts, 0 missing, 2 new boundary modules, 2 hand-written .d.mts deleted, 0 .mjs/.mts/.d.mts left | PASS | inventory.txt · git ls-tree b8778c8 | author self-check | 2026-10-30 |
| T15a.2 tsconfig.runtime.json compiles runtime TS strict (NodeNext, ES2023, rootDir runtime, outDir lib/runtime, sourceMap, declaration) | PASS | tsconfig.runtime.json · build.log (62 JS + 62 maps + 62 d.ts) | author self-check | 2026-10-30 |
| T15a.3 build / prepack / typecheck dual-compile paths run | PASS | build.log · typecheck.log · checkbuild.log · package.json | author self-check | 2026-10-30 |
| T15a.4 SERVER_PATH resolves lib/runtime/server.js and keeps DSH_BLENDER_SERVER override | PASS | src/constants.ts (resolveServerPath) · install-smoke.log (SMOKE SERVER_PATH) | author self-check | 2026-10-30 |
| T15a.5 parity and pack checks runnable | PASS | parity.json · pack-list.txt · contract.log | author self-check | 2026-10-30 |

## Caveats

- No CI workflow exists on this branch: T12 is still unchecked in the plan. The two import paths are exercised locally and in a clean clone (T15e.6).
- Build order is runtime -> host -> client, not the host-first order in plan §10.2: after T7 the host side imports boundary helpers from the built lib/runtime, so the runtime must compile first. Recorded in ADR 0001.
