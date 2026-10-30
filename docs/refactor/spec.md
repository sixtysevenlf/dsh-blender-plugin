# DSH × Blender 3.0 specification

- **Status**: normative target. As of 2026-10-30 (T13) only this documentation layer and the contract
  tests under `tests/unit/js/contract/` exist; every gate in section 5 is **open** until its task lands
  and its evidence pack is complete.
- **Owners**: plugin maintainers. **Review**: one non-author reviewer for every R1 gate.
- **Evidence root**: `docs/refactor/evidence/<TASK>/`; the release pack is `docs/refactor/evidence/release/`.

This document states what 3.0.0 must be true. It is not a task list: the plan owns sequencing, this
specification owns the acceptance condition of each gate, and each gate names the artifact that proves it.
When the implementation and this document disagree, the gate is not met — fix one of them explicitly.

## 1. Problem

The plugin grows by accretion: `src/index.ts` holds 2,888 lines and all fifteen tools, the backend is
sixteen flat scripts totalling about 8,000 lines, the addon is a single 7,515-line file, and the build
output `lib/` is committed by hand. Contracts (15 tools, 34 families, 228 ops, 39 addon commands, 29 HTTP
routes, protocol 11) are stable but their enforcement is spread across ad-hoc self-tests, so a change can
break a boundary without a failing check. Installation asks users to pick a single Python file by hand,
and AI-facing documentation contradicts itself in two languages with no machine check.

The cost is concrete: changes are hard to review, a stale build can ship, a broken addon file has no
failing test before a user sees it, and agents cannot tell a current fact from an outdated one.

## 2. Goals

- **G1 — Boundaries are files.** One tool per file, engine and server split by responsibility, Python in
  domain packages, addon in a package, each new module under 600 lines with no new 1,000-line module.
- **G2 — One build artifact.** Backend is TypeScript compiled into `lib/runtime/`; `lib/` is built in
  `prepack`; tests import sources, end-to-end and packaging use the build.
- **G3 — Contracts are enforced by tests.** The six counts above are checked by contract tests, not by
  convention, and a declared change produces an explicit diff.
- **G4 — Installation is one artifact and one verification.** Addon zip install, then `doctor` returns
  `kind=ok` (GUI) or worker plus `see` returns a frame (headless).
- **G5 — Documentation is executable.** AI docs are English and machine-checked; human entries are
  bilingual and cross-linked; a broken link, a missing command, or a stale file name fails a test.
- **G6 — Release is reversible.** Every layer has a written rollback, and the rollback is rehearsed
  before 3.0.0 ships.

## 3. Non-goals

- No change to the fifteen tool names, their parameters, or the planning surface of 34 families / 228 ops
  in 3.0.0. A change requires its own task and ADR.
- No new model tool. New capability goes into `blender_rt_plan` ops.
- No protocol bump: 3.0.0 keeps protocol 11 and addon 1.11; backend and addon are upgraded together.
- No move to the Blender extension platform, no pip or conda packaging, no hosted service.
- No rewriting user-facing guides into English; `docs/操作教程.md`, `docs/配置参考.md`, and the other
  Chinese guides stay Chinese.
- No performance target beyond "no regression" and no claim about rendering quality.

## 4. Constraints

- **K1 — Contract freeze.** 15 tools / 34 families / 228 ops / 39 addon commands / 29 routes / protocol 11
  unless the task declares a versioned change in `MIGRATIONS.md` and `CHANGELOG.md`.
- **K2 — Supported matrix.** Node 22 (CI and tests; the backend still runs on Node 20+), Windows 11
  primary, Linux and macOS supported, Blender 4.2 LTS and 5.x (5.x rows may be an approved exemption).
- **K3 — Python isolation.** Python tests and the runtime use a project virtualenv selected by
  `PYTHON`; no global site-packages, no implicit PATH.
- **K4 — No behavior change while code moves.** A migration change proves parity before it deletes the
  old implementation; the old file is deleted in the same change that makes the new one authoritative.
- **K5 — Language split.** AI-facing docs (this file, ADRs, AGENTS, PR template, tests README) are
  English-only. README and CONTRIBUTING are bilingual pairs. User guides stay Chinese.
- **K6 — Evidence or it did not happen.** A gate is checked only with a log, report, or artifact under the
  evidence root; a screenshot-free claim is not evidence.
- **K7 — Secrets and artifacts.** No secrets, no `node_modules`, no venv, no `.tgz`, no committed
  `lib/` after 3.0.0.

## 5. Normative clauses (SHALL)

The identifier before each clause is the gate identifier from the plan (§8). A clause is binary: met or
not met. "SHALL" is mandatory; there is no partial credit.

### 5.1 Gate A — contracts and compatibility (R1)

- **A1 — Tool semantics.** Every one of the fifteen tools SHALL be exercised on success, invalid
  arguments, unreachable backend, timeout or abort, presenter, lossless receipt, and headless fallback;
  names, schemas, and description budgets SHALL differ from the v2.6.2 snapshot only by a declared
  change. Evidence: per-tool log and an empty schema diff.
- **A2 — Family and op contract.** The catalog and registry SHALL register each of 34 families and 228
  ops exactly once, with no orphan, duplicate, or implicit alias; the capability lock SHALL be
  reproducible from its documented generator. Evidence: catalog report and lock diff.
- **A3 — Addon commands.** All 39 commands SHALL be covered for request, response, error, pause, and
  read-only behavior, with names, parameters, return shape, and pause semantics unchanged.
- **A4 — Protocol 11.** Handshake, version mismatch, frame boundaries, partial frames, disconnect,
  timeout, error reply, and pause SHALL be covered; the protocol version SHALL remain 11.
- **A5 — HTTP routes.** Every one of the 29 routes SHALL cover the normal path, invalid input, error code
  and content type, lease write protection, and CORS; invalid input SHALL NOT produce a 500.
- **A6 — Surface fit honesty.** Catalog, API, help, and capability lock SHALL agree; when an operation is
  not implemented, `surface_fit` SHALL return `ok:false` with a reason and a next action, never a fake
  success.
- **A7 — Compatibility snapshots.** v2.6.2 and 3.0.0 tool and command signature snapshots SHALL pass the
  description budgets (12,000 characters of tool descriptions, 1,600 of command descriptions); any
  difference SHALL be declared in `MIGRATIONS.md`.
- **A8 — Errors, lossless, and safety.** Every receipt SHALL pass the DSH lossless check; every error
  SHALL carry `code`, `message`, and `next_action`, and SHALL NOT return a raw stack or a secret; path
  traversal, symlink, `file=` guard, and lease-bypass cases SHALL each have a rejection test.

### 5.2 Gate B — structure, migration, and build

- **B1 — Entry and file map.** `src/index.ts` SHALL be at most 150 lines and contain only imports,
  apply, and exports; the fifteen tool handlers SHALL live under `src/tools/*`; the files named in plan
  §4.2-4.5 SHALL exist; the import graph SHALL be acyclic.
- **B2 — Engine and server split.** `engine.mjs` SHALL only assemble the engine and dispatch;
  `server.mjs` SHALL only start and stop; routes, lease, and host views SHALL be separate modules. New
  modules SHALL stay at or below 600 lines, and none over 1,000 without an ADR.
- **B3 — Python domain packages.** Five domain packages under `runtime/py/` plus shims SHALL exist; old
  imports SHALL keep working; shims SHALL only re-export; no module-level Node import and no scene
  mutation at import time.
- **B4 — Addon package.** The single file SHALL no longer be the install entry; the zip SHALL contain
  `__init__.py` and `bl_info`, register 39 commands, and the launcher SHALL check addon 1.10+ and
  protocol 11. See [ADR 0003](../adr/0003-addon-package-release.md).
- **B5 — Phase R migration, build, and packaging.** `runtime/**/*.mjs` SHALL be zero; runtime TypeScript
  SHALL be strict-compiled into `lib/runtime/**/*.js` with no `@ts-nocheck` and no new explicit `any`;
  tests SHALL import TypeScript; end-to-end, backend spawn, and packaging SHALL use the built JavaScript;
  `SERVER_PATH` SHALL resolve to `lib/runtime/server.js`; the tarball SHALL contain
  `lib/runtime/**/*.js` and `runtime/**/*.py` and no `.ts`; `git status` SHALL show no `lib/`.
- **B6 — Repository and package.** `lib/` SHALL NOT be in git; `prepack` and CI SHALL build it; the
  packed tarball SHALL contain the built entry and runtime Python and SHALL NOT contain the legacy
  single-file addon; a clean install SHALL load the entry. See [ADR 0004](../adr/0004-lib-build-release.md).
- **B7 — No duplicate implementation, no dead references.** Old and new implementations SHALL NOT both be
  active; shims SHALL only re-export; removed tools such as `blender_rt_pick` SHALL have no remaining
  reference in code, tests, or AI docs.

### 5.3 Gate C — tests and behavior coverage

- **C1 — Three independent layers.** `test:unit`, `test:integration`, and `test:e2e` SHALL run
  independently; the default sets SHALL require no real Blender, GUI, network, or DSH provider.
- **C2 — Pull-request gate.** A clean clone SHALL pass dependency install, unit, integration, Python unit,
  typecheck, lint, and the package dry run with zero exit codes, without a real Blender.
- **C3 — Behavior matrix.** Every row of `tests/BEHAVIOR_MATRIX.md` SHALL be `covered` or an approved
  exemption at release; `planned` SHALL be zero; the checker SHALL exit non-zero on gaps, duplicates, or
  unknown identifiers.
- **C4 — Legacy test migration.** All 67 legacy tests SHALL have a keep/split/merge/delete/move decision in
  `tests/MIGRATION.md`; `test:legacy` SHALL be removed and no legacy test SHALL reference a deleted API.
  Until that migration lands, `test:legacy` is allowed and is marked shrink-only in
  [AGENTS.md](../../AGENTS.md#1-commands-single-source).
- **C5 — Coverage thresholds.** Changed lines ≥80/70 (statements/branches), new modules ≥85/70, critical
  contracts ≥90/75, Python and addon ≥80/70, repository total not decreasing.
- **C6 — Real Blender end-to-end.** Before release, Blender 4.2 LTS SHALL pass headless and GUI flows:
  zip install, enable, Connect, doctor, selection, bpy execution, export, uninstall; 5.x rows SHALL be
  covered or have an approved exemption.
- **C7 — Skip, flaky, retry governance.** Zero unapproved skips; no retry that hides a failure in the PR
  set; every flaky test SHALL have an issue, an owner, and an expiry date and SHALL NOT be in the
  mandatory set.
- **C8 — Test quality spot check.** Each test file SHALL have one topic; no self-test over 800 lines;
  contract tests SHALL assert against the real registry, not only a snapshot; a failure message SHALL
  name the tool, command, or route.

### 5.4 Gate D — engineering, operation, and release

- **D1 — Clean environment.** A clean machine SHALL install with Node 22, dependency install, the test
  suite, typecheck, and lint, with no global dependency or implicit PATH requirement.
- **D2 — Release package.** The tarball SHALL be produced, its file list SHALL match the recorded
  `pack-list`, the entry SHALL load, and there SHALL be no secret or oversized file; a size difference
  from v2.6.2 SHALL be explained.
- **D3 — Addon installation migration.** A real Blender SHALL install the zip, enable it, Connect, return
  `doctor` `kind=ok`; the legacy single-file uninstall path SHALL work; the plugin SHALL still work
  after an upgrade and a DSH restart.
- **D4 — B1 small regression.** The fixed 3-topic × P0/P1 regression SHALL run with the v2.6.1 plugin and
  zero failures; results, ledger, and evaluation time SHALL be archived separately from 3.0.0 scores.
- **D5 — Release and rollback.** `package.json` and `PLUGIN_VERSION` SHALL be 3.0.0, addon `bl_info`
  SHALL be 1.11, protocol SHALL be 11; the tag, GitHub release, npm package (if published),
  CHANGELOG/MIGRATIONS/README SHALL be complete; the v2.6.2 rollback SHALL be rehearsed.
- **D6 — Release documentation.** Install, upgrade, uninstall, rollback, and every breaking change SHALL
  be written down; legacy single-file users SHALL have migration steps; release notes and CHANGELOG SHALL
  agree.

### 5.5 Gate E — AI collaboration and evidence

- **E1 — AGENTS rules.** `AGENTS.md` SHALL be at most 120 lines, be the single source for build, test,
  typecheck, lint, and pack commands, contain the two-place disclosure convention (commit trailer and
  pull-request section), and SHALL NOT reference the deleted `CLAUDE.md`,
  `docs/engineering-principles.md`, or `docs/refactor/metrics.md`; a broken link, a missing command, or
  a missing section SHALL fail the docs contract test.
- **E2 — Decidable specification.** This file SHALL contain the problem, goals, non-goals, constraints,
  SHALL clauses, WHEN/THEN acceptance scenarios, a behavior-matrix link, and a rollback section; every
  gate in the plan §8 SHALL trace to a clause here.
- **E3 — AI collaboration assets.** Four ADRs (runtime TypeScript, test stack, addon package and release,
  lib build and release), the ADR index with an inline template, this specification with its Metrics
  section, and the pull-request template SHALL exist and cross-link; no separate
  engineering-principles, metrics, or CLAUDE file SHALL exist.
- **E4 — Review and size.** One pull request per task or boundary; target at most 400 changed lines,
  above 400 requires a Lead exception, above 800 must be split; R1 gates need a non-author review; the
  review SHALL cover intent and specification, boundaries and error handling, security and privacy, and
  maintainability; the PR template SHALL include the behavior-matrix row, test evidence, build logs,
  risk, rollback, and disclosure.
- **E5 — Documentation fact check.** Links, commands, and matrix rows SHALL be checkable;
  `tests/README.md` SHALL be at most 80 lines; README/zh-README and CONTRIBUTING/zh-CONTRIBUTING SHALL
  cross-link at the top; the retired Chinese README filename SHALL NOT appear anywhere; no deleted
  tool SHALL be referenced.
- **E6 — Metrics baseline.** The Metrics section below SHALL record the delivery layer (release interval,
  change failure rate, rollback count) and the collaboration layer (PR size, review time, rework rate,
  flaky rate, agent first-pass rate). Generated line counts and tokens SHALL NOT be recorded as output.

### 5.6 Gate F — release sign-off

- **F1 — All gates pass.** A1-A8, B1-B7, C1-C8, D1-D6, and E1-E6 SHALL be checked with no R1 exemption.
- **F2 — Evidence archived.** Every task evidence pack SHALL be complete; the release directory SHALL
  contain checksums, test logs, evaluation results, and rollback records.
- **F3 — Fact sources synchronized.** Specification, behavior matrix, ADR index, and AGENTS SHALL match
  the final code and test state.
- **F4 — Sign-off.** Author self-check, independent review, and Lead sign-off SHALL be recorded with dates
  and the release commit.

## 6. Acceptance scenarios (WHEN/THEN)

Each scenario is a review script, not a test name. The evidence named in section 5 is the proof.

- **S1 — Tool schema change.** WHEN a contributor changes a tool parameter or description, THEN the
  contract test shows a schema diff, and the change is either reverted or declared in `MIGRATIONS.md`
  and `CHANGELOG.md`; otherwise A1 and A7 fail.
- **S2 — Catalog change.** WHEN an op is added or renamed, THEN the capability lock is regenerated with
  the documented generator, the counts stay 34/228, and the catalog test proves one registration per op.
- **S3 — Backend migration.** WHEN a `runtime/` module is converted, THEN the parity test runs against
  the same inputs, tests import the TypeScript source, no new `.mjs` appears, and the old file is deleted
  in the same change.
- **S4 — Fresh clone.** WHEN a reviewer clones the repository on Node 22 and runs the pull-request gate,
  THEN install, typecheck, lint, unit, integration, Python unit, and package dry run all exit zero without
  Blender.
- **S5 — Release build.** WHEN the maintainer packs the release, THEN `prepack` builds `lib/`, the
  tarball contains `lib/runtime/**/*.js`, no `.ts` backend source, and the file list matches
  `pack-list.txt`; a clean install loads the entry and starts the backend.
- **S6 — Addon install.** WHEN a user installs the release zip in Blender 4.2 LTS, THEN 39 commands
  register, Connect succeeds, and `doctor` returns `kind=ok`; the legacy single file is removed first.
- **S7 — Rollback drill.** WHEN the release rollback is rehearsed, THEN the addon returns to the v2.6.2
  single file or the plugin returns to the v2.6.2 tag, and the documented steps succeed without editing
  code.
- **S8 — AI documentation change.** WHEN someone edits AGENTS, an ADR, this file, the PR template, or
  the tests README, THEN the docs contract test fails on a broken link, a missing command, an over-limit
  file, or a stale reference.
- **S9 — Human entry change.** WHEN README, zh-README, CONTRIBUTING, or zh-CONTRIBUTING is edited, THEN
  the bilingual test fails if the top cross-links are missing, if the language versions diverge in
  section count, or if the retired Chinese README filename reappears.
- **S10 — Matrix at release.** WHEN the release candidate is cut, THEN every behavior-matrix row is
  covered or exempt and `planned` is zero.
- **S11 — Pull request review.** WHEN a PR is opened, THEN it names one task, fills the template,
  includes test and build evidence, and a non-author reviews every R1 gate.
- **S12 — Metrics update.** WHEN the release is prepared, THEN the Metrics section is refreshed from git
  and review data, and no generated-line or token figure is reported as output.

## 7. Behavior matrix

[tests/BEHAVIOR_MATRIX.md](../../tests/BEHAVIOR_MATRIX.md) is the row-level truth for coverage. It is
owned by T14: the file exists now with the header, identifiers, and status vocabulary, and the rows are
populated by T14 and the release tasks. Until then, this section is not a claim of coverage; C3 is open.

## 8. Rollback

Rollback is per layer, and each layer is independent enough that a failed release does not require
rewriting code.

| Layer | Failure | Rollback |
|---|---|---|
| Documentation (this task) | Wrong or contradictory docs | Revert the documentation commit; the contract tests fail first if a link or command is broken. |
| Host `src/` | Tool regression after split | Revert the module move; the previous file is still present until its replacement lands. Pin the previous plugin version for users. |
| Backend runtime | TypeScript migration breaks a route or job | Restore the previous tag's `runtime/` sources; the package can ship the previous build with `lib/` rebuilt from that tag. |
| Addon | Zip package fails to register or Connect | Remove the zip addon, reinstall the v2.6.2 single file, restart Blender and DSH. See [ADR 0003](../adr/0003-addon-package-release.md). |
| npm package | Bad publish | Deprecate the version, move the `latest` dist-tag back, or publish a patch from the last good commit; do not unpublish. See [ADR 0004](../adr/0004-lib-build-release.md). |
| Release | Gate missed after tagging | Mark the GitHub release as withdrawn, publish a fixed patch, and record the incident in `CHANGELOG.md`. |

## 9. Metrics

Baseline recorded on 2026-10-30 from git metadata for the range v0.4.0 (2026-09-13) to v2.6.2
(2026-10-28): 24 release tags on 16 release days, 17 merge commits, 135 commits with diff statistics.

| Layer | Metric | Definition | Baseline | Direction |
|---|---|---|---|---|
| Delivery | Release interval | Days between consecutive release tags | 15 intervals; median 2 days, mean 3.0, p90 9, range 1-9 | Do not regress; set the 3.0.0 cadence at F4 |
| Delivery | Change failure rate | Share of releases that are patch releases within 7 days of the previous release (proxy: no production telemetry) | 13 of 24 tags (54%) | Lower |
| Delivery | Rollback count | Recorded rollbacks (code revert, addon reinstall, package deprecation) per release | 0 recorded; no rehearsal before T15f/T16 | Keep 0; rehearsal required by D5 |
| Collaboration | PR size | Changed lines per merge, from `git diff --shortstat M^1 M` | Median 386; p90 1,662; 17 PRs | ≤400 target per E4 |
| Collaboration | Review time | Hours from PR open to first non-author approval | Not instrumented offline; collect from the GitHub API | Establish at 3.0.0 |
| Collaboration | Rework rate | Commits pushed after the first non-author review divided by all commits in the PR | Not instrumented offline; collect from the GitHub API | Lower |
| Collaboration | Flaky rate | Runs that fail then pass on rerun divided by all runs | Not instrumented offline; requires the CI test report | 0 unapproved skips (C7) |
| Collaboration | Agent first-pass rate | Agent-authored PRs whose first CI run is green divided by all agent-authored PRs | Not instrumented offline; requires CI and the disclosure trailer | Higher |

Rules for this section:

- A metric is recorded only from a stated source (git, GitHub API, CI report). "Not instrumented" is an
  honest value; an invented number is a review failure.
- Generated lines, token counts, and model cost are **not** output metrics and SHALL NOT be reported as
  results of the refactor.
- The section is refreshed at each release; the baseline row is kept so a later reader can see the
  starting point.

## 10. Traceability

| Spec clause | Plan gate | Primary evidence |
|---|---|---|
| 5.1 A1-A2 | A1-A2 | per-tool contract log, catalog report, lock diff |
| 5.1 A3-A5 | A3-A5 | command matrix, protocol log, route matrix |
| 5.1 A6-A8 | A6-A8 | surface-fit log, compat diff, security log |
| 5.2 B1-B3 | B1-B3 | structure report, file map, import smoke |
| 5.2 B4-B7 | B4-B7 | zip manifest, build/typecheck/parity logs, pack list |
| 5.3 C1-C4 | C1-C4 | layer logs, CI log, matrix report, migration diff |
| 5.3 C5-C8 | C5-C8 | coverage report, E2E log, flaky report, review notes |
| 5.4 D1-D3 | D1-D3 | clean-env log, tarball check, install migration log |
| 5.4 D4-D6 | D4-D6 | B1 regression pack, release directory, docs diff |
| 5.5 E1-E3 | E1-E3 | agents-doc log, spec review, docs tree |
| 5.5 E4-E6 | E4-E6 | review record, docs-check log, this Metrics section |
| 5.6 F1-F4 | F1-F4 | acceptance summary, evidence index, fact diff, sign-off |

## 11. Changing this specification

A change to a SHALL clause is an architecture decision: open a pull request that (1) updates this file,
(2) adds or updates the ADR when the change is expensive to reverse, (3) updates the gate's evidence
expectation, and (4) states which gates become open again. A specification that silently drifts from the
code is a broken gate, not a documentation task.
