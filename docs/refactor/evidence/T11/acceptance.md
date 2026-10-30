# T11 acceptance evidence

Task: plan 7.2 T11 (legacy test migration) and plan 6.3-6.7; gate C4.
Verdict: **PASS for the in-scope, locally runnable criteria; PARTIAL/NOT RUN where the plan
requires the release machine.** Every PASS row below points at a real command log with exit 0.
Author self-check; independent reviewer R1 is still to do (see review.md).

| ID | Criterion | Result | Evidence | Verifier | Date |
| --- | --- | --- | --- | --- | --- |
| T11-1 | tests/MIGRATION.md lists all 67 legacy files with a conclusion, no pending row | PASS | tests/MIGRATION.md (67 rows) | host-analyst (author) | 2026-10-30 |
| T11-2 | npm run test:legacy and its 22 aliases removed from package.json | PASS | package.json; migration-diff.md | host-analyst (author) | 2026-10-30 |
| T11-3 | Legacy tests/*.mjs and tests/*.py removed after targets existed | PASS | migration-diff.md deleted list; git diff --cached | host-analyst (author) | 2026-10-30 |
| T11-4 | Unit layer runs independently (Vitest, no Blender/backend) | PASS (37 files / 252 passed / 1 skipped) | unit.log | host-analyst (author) | 2026-10-30 |
| T11-5 | Integration layer runs independently (Vitest, fake addon/backend) | PASS (17 files / 102 passed) | integration.log | host-analyst (author) | 2026-10-30 |
| T11-6 | Python unit+integration default collection runs, blender/gui excluded | PASS (292 passed) | pytest.log | host-analyst (author) | 2026-10-30 |
| T11-7 | typecheck | PASS (exit 0) | typecheck.log | host-analyst (author) | 2026-10-30 |
| T11-8 | lint (eslint --max-warnings=0) | PASS (exit 0) | lint.log | host-analyst (author) | 2026-10-30 |
| T11-9 | build / package precheck | PASS (exit 0, check-build PASS) | build.log | host-analyst (author) | 2026-10-30 |
| T11-10 | E2E wrappers collect under -m blender (25 wrappers + GUI probe) | PASS | e2e.log (collect-only) | host-analyst (author) | 2026-10-30 |
| T11-11 | Two real-Blender E2E cases executed on Blender 4.2.10 | PASS (geo, plan) | e2e.log | host-analyst (author) | 2026-10-30 |
| T11-12 | Behavior matrix unchanged and still consistent | PASS (347 rows: 345 covered, 2 exempt) | commands.txt; check-behavior-matrix run | host-analyst (author) | 2026-10-30 |
| C4 | Legacy test migration gate as defined in plan 8.3 | PASS | T11-1..T11-3, migration-diff.md, review.md | host-analyst (author) | 2026-10-30 |
| C1 | Three layers run independently; default set excludes real Blender/GUI/network/DSH provider | PASS, with the E2E release-gated exception documented in tests/README.md | unit.log, integration.log, pytest.log | host-analyst (author) | 2026-10-30 |
| C2 | Clean-clone PR gate (npm ci --legacy-peer-deps, unit, integration, python unit, typecheck, lint, npm pack --dry-run) | PARTIAL — every step except npm ci from a clean clone and npm pack --dry-run was run on this worktree (logs above). Not re-run from a fresh clone in this sandbox. | unit.log, integration.log, pytest.log, typecheck.log, lint.log, build.log | host-analyst (author) | 2026-10-30 |
| C6 | Real-Blender E2E before release | NOT RUN here by design (release gate). 2 of 25 cases were executed (geo, plan); the remaining 23 are wrappers over archived cases and must run on the release machine. | e2e.log; tests/e2e/blender/cases/ | host-analyst (author) | 2026-10-30 |
| C7 | skip/flaky governance | PASS with declarations: 1 platform skip (macOS-only branch on Windows), GUI probe skips unless DSH_BLENDER_GUI_E2E=1, no retry added; one historical backend-routes parallel flake did not reproduce in final runs. | unit.log, integration.log, review.md | host-analyst (author) | 2026-10-30 |

## Notes for the independent reviewer

- Only 2 of 25 real-Blender cases were executed in this environment (Blender 4.2.10 is installed and
  geo + plan indeed ran green; see e2e.log). Do not read T11-10 as proof of E2E assertion coverage.
- The plan's six-way split of upstream_integration_selftest.py was deliberately not done (rationale in
  review.md and MIGRATION.md); confirm this is acceptable or request the split before merge.
- AGENTS.md and three scripts/*.mjs comments/paths were changed because the new contract test enforces
  that AGENTS.md only names existing scripts/files and the structure check reads the moved capability
  fixture. Reviewers should confirm those doc-only edits are in scope for T11.
