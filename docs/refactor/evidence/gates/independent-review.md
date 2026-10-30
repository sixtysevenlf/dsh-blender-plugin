# Independent acceptance review — dsh-blender-plugin v3.0.0 refactor

Reviewer: independent non-author reviewer (DSH agent session; did not write the refactor, tests, evidence or plan).
Date: 2026-10-30, review window 22:19–22:30 +08:00.
Plan authority: `D:/DSH/dsh-blender-plugin-refactor-plan-v3.md` §8 (8.0–8.7).
Method: read the plan and every gate artifact under `docs/refactor/evidence/`; independently reran
`test:contract` (23 files / 173 passed), `check:structure` (PASS), `check:build` (PASS),
`check-behavior-matrix.mjs --release` (347 rows / 345 covered / 0 planned / 2 exempt),
`check-parity.mjs` (default base: PASS), targeted pytest (17 + 138 passed), and inspected the real
Blender E2E result JSONs. Read-only; wrote only this file. No product code, plan checkbox, tag, release
or push was touched.

## Commit and environment

- Reviewed tree: branch `refactor`, HEAD `bc76135` ("test(gates): add v2.6.2 compatibility and
  catalog-lock artifacts"), parent `7806a5b` ("fix: keep parity and count sync valid at 3.0.0"), whose
  parent is `13cc256` (T16 release evidence). At review start HEAD was `13cc256` plus one uncommitted
  `scripts/check-parity.mjs` change; HEAD advanced twice while the review ran and the A1/A2/A7 artifacts
  appeared mid-review and were then committed in `bc76135` (see "Moving target" below).
- Env: Windows 11, Node v22.17.0 (`C:/Users/villa/node22`), npm 10.9.2, Python 3.11
  (`C:/Users/villa/e2e/dsh-venv/Scripts/python.exe`), Blender 4.2.10 (`C:/Users/villa/e2e/blender-4.2.10`).
- Tags: last release tag is `v2.6.2` (= commit `8b1b470`). There is **no** `v3.0.0` tag.

## Status rubric

- PASS: every clause of the gate has evidence I could inspect or reproduce.
- PARTIAL: substantive evidence exists and most clauses verify, but a required artifact/sub-case is
  missing or a written claim is overstated (still not acceptable as PASS under §8.0).
- FAIL: a required clause is unmet/contradicted, or the evidence does not support the claim.
- PENDING / BLOCKED as used by the plan.

## A. Contracts and behavior

| Gate | Status | Evidence checked | Remaining gaps |
|---|---|---|---|
| A1 15 tools × 7 aspects | FAIL | `gates/logs/a1-tool-execution.log` (20 tests, one file), `gates/tool-matrix.json`, `tests/unit/js/contract/tool-execution.test.ts`, `tool-presenter.test.ts`, `lossless.test.ts` | No per-tool matrix for param error / backend unreachable / timeout-abort / session-headless; only viewport is tested unreachable, only `blender_rt_do` for headless fallback, one generic missing-param test, one abort test; `tool-matrix.json` marks "covered" by copying BEHAVIOR_MATRIX statuses, and every tool's integration cell points at the same generic presenter test. `gates/tool-schema.diff` now exists (empty) — that artifact is satisfied, the coverage is not. |
| A2 family/op contract | PASS | `gates/catalog-report.json` (34 families / 228 ops / fingerprint 1d401b938b69 / `lockMatchesCommitted`), `gates/catalog-lock.diff` (empty), `tests/fixtures/capability.lock.json`, `contract/catalog.test.ts`, `single-source.test.ts` (incl. new sync_counts --dry test) | Lock file carries counts, not a content hash ("哈希" wording unmet); lock diff/artifact was created during this review and is uncommitted. |
| A3 39 addon commands | PASS | `T10/command-matrix.md` + `command-matrix.json` (39 rows), `T10/test.log` (138 matrix tests; I reran: 138 passed), `gates/logs/a3-command-contract.log` (9 tests), `gates/command-matrix.json` (39 covered) | Tests monkeypatch recorders over the real dispatch/registry; real bpy handler bodies are exercised only by E2E. Author's T10 A3-4 review row is still PENDING in the pack; this review supplies it. |
| A4 protocol 11 | PASS | `gates/logs/a4-protocol-11.log` (16 tests: handshake, partial frame, timeout, socket close, pause, mismatch, error), `gates/protocol-info.json` (protocol 11, bl_info 1.11), `protocol-contract.test.ts`, `tests/integration/js/fake-addon.test.ts` | File is named `a4-protocol-11.log`, not `protocol-11.log`; content matches the gate. |
| A5 29 routes | PARTIAL | `gates/logs/a5-routes.log` (16 tests), `gates/route-matrix.json` (29/29), `server-routes.test.ts` (CORS exact, 404/410, lease), `backend-routes.test.ts` (live server, 29 advertised) | No per-route normal/invalid/error no-500 record; tests sample a subset (health/status/who/host/lease/jobs/selection). Route-matrix statuses are derived from BEHAVIOR_MATRIX, not from per-route executions. |
| A6 surface_fit honesty | PARTIAL | `gates/logs/a6-surface-fit.log` (3 unit tests), `gates/logs/a6-surface-fit-python.log` (3 pytest; I reran surface-fit 3 passed), `runtime/py/geometry/surface.py:1748` (`next_action` string) | No test *executes* `surface_fit` and asserts `ok:false` + `reason` + `next_action`; both tests are source/AST/string assertions. |
| A7 v2.6.2↔3.0.0 compat | PARTIAL | `gates/compat.diff` (only DECLARED pluginVersion 2.6.2→3.0.0, addonVersion [1,10]→[1,11]; RESULT PASS), `gates/tool-schema.diff` (empty), `gates/compat-base.json` / `compat-v300.json` (15 tools, 39 commands, protocol 11 both sides; tool description total 6188 chars both), `gates/make-compat.mjs`, base = worktree of tag `v2.6.2` (8b1b470) | Snapshot compares tool name/parameters/output/timeout/concurrency/description and command names/descriptions only. It does **not** snapshot the 39 commands' parameters/return structure/pause semantics, nor assert the 12,000/1,600 description budgets (only equal lengths are derivable). Generated at 22:26 during this review and committed in `bc76135`. |
| A8 errors/lossless/security | FAIL | `gates/logs/a8-security-lossless.log` (14 tests), `security.test.ts` (file= guard, zip traversal, no shell, static lease), `lossless.test.ts` (4) | Error receipts do not carry `next_action` (repo search: only `runtime/py/geometry/surface.py:1748` and `docs/refactor/spec.md:95`); no test asserts raw stack/secret suppression; the symlink case is explicitly *not* rejected ("passes file= paths through without claiming symlink containment"). |

## B. Structure, packaging, Phase R

| Gate | Status | Evidence checked | Remaining gaps |
|---|---|---|---|
| B1 entry/file map | PASS | `T4/structure-report.json`, `gates/logs/structure.log`; my rerun: index 13 lines, 15 handlers, 32/32 files, 147 edges, 0 cycles | — |
| B2 engine/server split | PASS | `gates/b2-largest-modules.txt` (max 531 lines), `runtime/engine.ts` 23, `runtime/server.ts` 37, `runtime/core/engine.ts` 225; no module >600, none >1000 | No ADR needed; release notes say "server 35" vs actual 37 (cosmetic). |
| B3 Python domain packages | PASS | `runtime/py/{core,geometry,assembly,render,export}` (45 .py), 38 flat shims, `tests/unit/python/test_runtime_layout.py` (AST shim checks), `runtime-layout.test.ts`; my rerun: 17 pytest passed | T8 pack lacks `test.log`/`build.log` (pack completeness — see F2), but the gate's import/shim checks pass. |
| B4 addon package + zip | PARTIAL | `addon/dsh_blender_bridge.zip` (41 entries; `__init__.py`, bl_info (1,11), 39 commands), `build-addon.mjs --check` sha256 `e8f6b73…`, T10 packaging tests (residue false), T16 GUI E2E install/enable/remove | `T9/test.log` contains a red launcher selftest (`LAUNCH_SELFTEST_RC=1`, 22 pass / 12 fail, Windows fake-blender spawn) and T9 acceptance itself says full T9 is not claimed; no dedicated register/unregister log artifact. |
| B5 Phase R migration | PENDING | `phase-r/acceptance.md`, `gates/logs/build-check.log`, `parity.log`, `structure.log`, `phase-r/install-smoke.log`, `pack-list.txt`; my reruns: build-check PASS (62 ts / 62 js / 0 .mjs / 0 @ts-nocheck / lib untracked), default parity exit 0 | B5.12 (B1 3-question regression) NOT EXECUTED (= D4); R1 non-author review was pending (this document covers only what I verified); rollback rehearsal is git/spawn-level only. |
| B6 release package | PASS | `phase-r/pack-list.txt`, `T16/logs/pack-list.txt`, `T16/clean-clone.log` (`npm pack`, install smoke, `SMOKE SERVER_PATH ... lib/runtime/server.js`, `doctor {"ok":true,"kind":"ok"}`) | Pack list is one commit stale (D2). |
| B7 no duplicates/dead code | PARTIAL | `gates/b7-retired-api.txt` (retired `blender_rt_pick` only in historical CHANGELOG/llms/comments and guards), 0 runtime .mjs, single-file addon absent, 38 re-export-only shims | Gate names `duplicate-report.json`; that artifact does not exist (only a retired-API grep). A stale `sync_counts.mjs` import was found and fixed in `7806a5b`. |

## C. Tests and coverage

| Gate | Status | Evidence checked | Remaining gaps |
|---|---|---|---|
| C1 three independent layers | PASS | `T11`/`T12` `logs/unit.log` (37/251), `integration.log` (17/102), `test.log` (pytest 295), separate E2E entry; T12 acceptance C1 | — |
| C2 PR gate in clean clone | PASS | `T12/clean-clone.log` (c9805f5), `T16/clean-clone.log` (3.0.0 clone; npm ci, build, typecheck, lint, format, structure, build-check, unit, integration, python, matrix, pack, smoke; `CLEAN-CLONE-OK`) | Log label does not show `--legacy-peer-deps`; contract suite not part of the clone run (not required by C2). |
| C3 behavior matrix | PASS | `gates/matrix-report.json` (347 rows; 15/39/228/29), `T14/matrix-report.json`; my rerun `--release`: 345 covered / 0 planned / 2 exempt | Committed T14 report's `sources` field is stale (`runtime/catalog.mjs`); regenerated report is uncommitted. |
| C4 legacy test migration | PASS | `tests/MIGRATION.md` (67 rows, no pending, `test:legacy` + 22 aliases removed), T11 migration-diff | — |
| C5 coverage | FAIL | `T12/acceptance.md`, `T12/exemptions.md`, `logs/coverage.log`, `python-coverage.log`, `diff-cover-js.log`, `diff-cover-py.log`: JS 42.84% / diff 32%, Python addon 32% / diff 31% vs 80/70 | Issue #52 is not a §8.0 EXEMPT (only platform/version conditions qualify); release is blocked. Author already refuses to tick. |
| C6 real Blender E2E | PARTIAL | `T16/e2e/blender-headless.results.json` (install_zip, enable_addon, get_addon_info, execute_bpy_command, selection_send, pause_gate, unknown_command, uninstall; 29 passed), `blender-gui.results.json` (install_enable, connect_operator, socket_roundtrip, get_addon_info, ping, execute_bpy_command, get_selection, disconnect, stop_server, disable_no_residue, remove_addon; 6 passed / 2 approved skip), `e2e/gui-connect.png` | No `doctor` case and no `export` case anywhere in the E2E results (`grep -ril doctor|export` over the E2E evidence is empty). `T16/logs/install-migration.log:19` claims the probe "checks doctor/bpy/selection" — the doctor part is not in the results. Strict §8.0 reading: missing sub-case = not pass. |
| C7 skip/flaky/retry | PASS | `T12/exemptions.md` (2 macOS platform skips; 2 GUI skips approved by issue #53; no retry plugin), `logs/unit.log`, `logs/e2e-gui.log` | `tests/e2e/gui/gui_harness.py:58` also honours `T10_SKIP_GUI=1`; documented only in code, not in exemptions.md. |
| C8 test quality | PASS | `T12/review.md` sample (single-topic tests, real registry/source assertions, locatable failures; 856-line archived upstream selftest documented as deliberate deviation); I independently sampled `server-routes`, `command-contract`, `tool-presenter`, `lossless`, `security` | Author's own note that per-file author-level review is not done; my sample is a spot-check, not a file-by-file review. |

## D. Release prerequisites

| Gate | Status | Evidence checked | Remaining gaps |
|---|---|---|---|
| D1 clean environment | PASS | `T16/clean-clone.log` (clean clone of `74c61f5`, every gate command, `CLEAN-CLONE-OK`), env recorded in `gates/acceptance.md` and `T12/README.md` | No `T16/env.txt`; same-machine CI replay on a CI runner not possible (no runner), only local clone. |
| D2 release package | PARTIAL | `T16/logs/pack.log` (3.2 MB / 829 files), `T16/logs/pack-list.txt` (828), `pack-actual`, v2.6.2 comparison 2.2 MB / 782; entry/install smoke | No `size.txt`; the final tree (with evidence docs) packs 4.2 MB / 845 files, so pack-list and size numbers are from a pre-evidence commit; only prose explains the delta. |
| D3 addon install/migration | PARTIAL | `T16/logs/install-migration.log`, GUI E2E install/enable/connect/selection/bpy/remove, `MIGRATIONS.md` upgrade/uninstall/rollback paths | No `doctor kind=ok` from the Blender E2E (only backend install-smoke `doctor`); no evidence of "upgrade + restart DSH then still works"; old single-file removal path is documented, not executed. |
| D4 B1 3×P0/P1 regression | PENDING | `T15f/b1-regression.md`, `phase-r/b1-regression.md` (NOT EXECUTED); live WSL check: PID 948 `b1_runner.py --arms P0,P1 --tasks bw_0001,bw_0023,bw_0091`, ledger 20 lines, log: P0 bw_0001 DONE 93.75 (20:55), P0 bw_0023 EVAL since 22:12, 5 of 6 runs still to go | Only one score so far; cannot be evidence yet. |
| D5 release and rollback | BLOCKED | no `v3.0.0` tag, no GitHub Release, no npm publish (user decision); `T16/release-notes.md` says deferred; `CHANGELOG.md` v3.0.0; `T15f/rollback.md` | Release actions not executed; rollback rehearsal is git/build-level with a recorded limitation; npm deprecate/rollback path not executed. |
| D6 release docs | PASS | `T16/logs/docs-diff.md` (11 files), `CHANGELOG.md`, `MIGRATIONS.md` (upgrade/uninstall/rollback), `T16/release-notes.md` — consistent on the breaking addon-zip change | — |

## E. Docs, review, metrics

| Gate | Status | Evidence checked | Remaining gaps |
|---|---|---|---|
| E1 AGENTS rules | PASS | `gates/logs/e1-e5-docs.log` (agents-doc 22 tests), `AGENTS.md` 120 lines (test enforces ≤120) | Gate names `agents-doc.log`; content lives in the combined e1-e5 log. |
| E2 decidable spec | PASS | `T13/spec-review.md`, `docs/refactor/spec.md` §5 clauses and §10 traceability for A1–F4; E2 is R2 | Spec was reviewed at T13 when most gates were still open; state rows are historical. |
| E3 AI assets | PASS | `docs/adr/README.md` + 4 ADRs, `T13/docs-tree.txt`, `.github/pull_request_template.md` (scope/matrix/evidence/risk/rollback/disclosure/checklist), spec Metrics | — |
| E4 review and size | FAIL | `T13/spec-review.md` E4 row admits the doc PR exceeds the 400-line target **and** the 800-line split threshold and needs a Lead exception; merge commits are 1.8k–42k changed lines; the gate-evidence commit `bc76135` itself adds 2,837 lines; no PR objects and no `github.com/*/pull` links; no non-author R1 review records (until this document) | No Lead exception for any oversize change; commit-per-task is not a PR; T13's own requirement unmet. |
| E5 docs fact check | PASS | `gates/logs/e1-e5-docs.log` (docs-bilingual 14, broken links, command names, line limits), `tests/README.md` 54 lines (≤80) | Gate names `docs-check.log`; content lives in the combined log. |
| E6 metrics baseline | FAIL | `docs/refactor/spec.md` §9: git-derived delivery baseline; review time, rework, flaky rate, agent first-pass are "not instrumented" | Four of five collaboration metrics have no numeric baseline; a plan that SHALL record them is not met. |

## F. Final gates

| Gate | Status | Evidence checked | Remaining gaps |
|---|---|---|---|
| F1 all gates checked | FAIL | `gates/acceptance.md` summary table exists | Gates are not all checked: C5, A1, A8, E4, E6, F2, F3 fail; B5/D4 pending; D5 blocked; C6/D3/A5/A6/A7/B4/B7/D2 partial. F1 cannot be PASS. |
| F2 evidence archive | FAIL | `docs/refactor/evidence/README.md` (stale: lists only T0–T8, T13, T14), `gates/` (artifacts committed in `7806a5b`/`bc76135`) | No `evidence-index.md`, no `sign-off.md`, no checksums for gate artifacts; `gates/` has no `env.txt`/`commands.txt`/`artifacts.sha256`/`review.md`; T8/T13/T14 lack `test.log`/`build.log` while their acceptance cites them; T16 lacks `env.txt`/`commands.txt`/`review.md`; T12 lacks top-level env/commands/test/build. |
| F3 fact sources synced | FAIL | `docs/refactor/spec.md:245` still says the matrix is a stub and "C3 is open"; evidence README stale; plan §7.2 checkboxes untouched; `AGENTS.md` current | Spec/evidence index contradict the final state; plan checkboxes deliberately untouched (correct per instruction) but then `acceptance.md` cannot claim sync. |
| F4 sign-off | PENDING | This file (independent review); `T16/acceptance.md`, `gates/acceptance.md` (author/Lead pre-audit) | No `sign-off.md`; no author/independent/Lead signature block; Lead has not signed. |

## Claims I could not verify

1. `doctor` returns `kind=ok` in a real Blender install via the GUI Connect path — no doctor case exists in the E2E results; only the backend install-smoke reports `kind=ok`.
2. Export from a real Blender scene through the plugin — no export case in any E2E result.
3. Persistence after DSH upgrade/restart — no restart step is recorded in any log.
4. Per-tool timeout/backend-unreachable/session behavior for the other 12 tools — no tests exist.
5. Author-level review of every test file (C8 is a spot-check by design).
6. The release itself (tag/Release/npm/rollback) — intentionally not executed.
7. Blender 5.x compatibility — declared EXEMPT-ENV-BLENDER-5X; no runtime available.
8. The parent repository's Notion mirror/fact sync — out of scope of this repository review.

## Moving target (material process finding)

- The tree changed during the review: HEAD `13cc256` → `7806a5b` at 22:24 → `bc76135` at 22:31.
  At 22:24–22:27 the author created the very artifacts A1/A2/A7 had been missing (`tool-schema.diff`,
  `compat.diff`, `compat-base.json`, `compat-v300.json`, `catalog-lock.diff`, lint/typecheck/format/
  full-test logs, `acceptance.md`), then committed them in `bc76135` (13 files, 2,837 insertions) after I
  had inspected them but before this review was finalised. `gates/logs/` was already committed in `7806a5b`.
  The committed artifacts match what I inspected (empty `tool-schema.diff`, `compat.diff` with only the
  two declared version changes, empty `catalog-lock.diff`), so the A2/A7 judgments below reflect them.
- I inspected and reran the generators; `make-compat.mjs` really does load the `v2.6.2` worktree
  (`8b1b470`) and the current `lib/index.js`, so the empty `tool-schema.diff` is a genuine comparison,
  and `compat.diff` shows only the two declared version changes. That improves A7 from "no evidence" to
  PARTIAL, but it is evidence produced after the release commit and during the acceptance audit — which is
  itself an F2/E4 problem, not a substitute for evidence present when the release was declared.
- `gates/acceptance.md` is a *Lead pre-audit*, not the independent review; it marks C6 PASS, but the
  doctor/export gaps above show that PASS is overstated. `T16/acceptance.md` repeats the doctor claim.

## Verdict

- Release verdict: **BLOCKED**. C5 alone blocks release under §8.0 (and issue #52 is not a valid EXEMPT);
  D5 is intentionally blocked, D4 is still running, and A1/A8/E4/E6/F2/F3 are open.
- **F1: FAIL.** The consolidated table exists, but the gates it summarizes do not pass: C5 fail,
  A1/A8/E4/E6/F2/F3 fail, B5/D4 pending, D5 blocked, and several gates are only partial. There is no
  reading of §8.0 under which F1 can be checked today.
- **F4: CANNOT BE SIGNED.** There is no `sign-off.md`, no author+independent+Lead signature, and the
  independent review (this file) finds multiple failing gates. A Lead signature on F4 would be signing a
  gate that F1 shows cannot pass. F4 must stay PENDING; it can only become PASS after C5 is resolved
  (or the plan is formally amended), D4 finishes, A1/A8 evidence is completed, and B5/D4/D5 close.
