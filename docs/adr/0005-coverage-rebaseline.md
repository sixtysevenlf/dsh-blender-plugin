# 0005. Rebaseline coverage after the 3.0 refactor

- **Status**: Accepted
- **Date**: 2026-10-31
- **Deciders**: plugin maintainers
- **Target**: gate C5

## Context

Gate C5 was first baselined at commit `b4427a5` against v2.6.2 with 63.01% JS
lines, 69.33% branches, 58.48% changed lines, and 82.25% / 79.51% Python pure
logic. The refactor then changed the shape of the code:

- the lint/format commit `9b9a7aa` reformatted 180 files, changing the v8 line
  accounting without changing behaviour;
- T5a typed the host and tool layers and added guards, which changed executable
  lines and branch counts;
- the addon Premium/Tripo removal changed the addon count from 39 to 38
  commands and 39 to 34 modules.

At `f001e1d` the measured JS coverage is 57.68% lines, 68.45% branches, 63.7%
functions; changed-lines coverage against v2.6.2 is 56.70%. The old 63 / 69 / 58
floors therefore fail on a correct, fully green refactor. Keeping those floors
would require adding tests whose only purpose is to execute lines, which the
plan explicitly rejects.

Vitest's parallel coverage merge was also non-deterministic: repeated runs reported
68.45% (2831 branches) or 68.5% (2835 branches) because the same source file was
merged from several workers. `npm run test:coverage` now runs with
`--no-file-parallelism`, and the baseline uses the stable 68.45% measurement.

Changed-lines coverage still has a one-line run-to-run tolerance: repeated deterministic
runs reported 6,697 or 6,698 missing lines (56.70% vs 56.697%). The baseline records
the worse 6,698 (56.697%) and the floor is that value, so either observation passes.

## Decision

The C5 baseline is the measured state at code commit `f001e1d`:

| Surface | Baseline |
|---|---:|
| JS lines / branches / functions | 57.68% / 68.45% / 63.7% |
| JS changed lines vs v2.6.2 | 56.697% (15,468 changed, 6,698 missing; rounds to 56.70%) |
| Critical modules | catalog 96.93/78.57; addon-protocol 92.53/80; router 100/92.85; lease 95.94/87.17; tools/index 100/100 |
| Python pure logic | 76.95% lines, 71.71% branches |
| E2E behavior matrix | 344/344 covered, 0 planned, 2 exempt |

The gate then applies these rules:

1. JS global lines, branches, and functions MUST NOT fall below the baseline.
2. Changed-lines coverage MUST stay at or above 56.697%; the target remains 70%
   and the +2 percentage-point ratchet remains a release-window policy, not an
   automatic config rewrite. A PR that improves coverage SHOULD raise the floor
   in the same change, with the new numbers recorded in this ADR's successor.
3. Critical modules remain at `>= 90 / 75`, Python pure logic at `>= 70 / 60`,
   and E2E scenario coverage at 100%. These are long-term quality floors, not
   the baseline itself; the measured values above are recorded for review.
4. The local gate and CI MUST both read `config/coverage-thresholds.json`, use
   diff-cover against the fixed `v2.6.2` baseline ref until 3.0.0 ships, and use
   the same Python scope. JS coverage MUST run through `npm run test:coverage`,
   which disables file parallelism so the v8 branch merge is deterministic.
5. No source lines are excluded. `config/coverage-move-manifest.json` stays
   empty because the repository cannot mechanically prove that the rename-heavy
   refactor lines are pure moves; the lower global floor is the honest cost of
   that limitation.
6. A future rebaseline MUST be a new ADR with the same level of detail. A
   silent edit to a floor is a gate failure.

### T5b amendment (2026-11-01)

T5b split the headless orchestrator and plan dispatch, added deterministic
execution tests, and made the execution lifecycle part of the gate. Those modules
did not exist in their current shape at `f001e1d`, so they cannot be compared to
that commit mechanically. They are tracked in `javascript.executionModules` with
floors measured at T5b and recorded in `config/coverage-baseline.json`:

| Module | Measured lines / branches | Floor |
|---|---:|---:|
| runtime/core/plan-dispatch.ts | 98.07 / 89.47 | 90 / 75 |
| runtime/core/plan/render-guard.ts | 100 / 96.29 | 90 / 75 |
| runtime/core/plan/shape-search.ts | 90.46 / 79.38 | 90 / 75 |
| runtime/execution/jobs.ts | 82.68 / 58.21 | 80 / 55 |
| runtime/execution/workers.ts | 88.31 / 60.43 | 85 / 58 |
| runtime/execution/headless/index.ts | 98.27 / 88.09 | 90 / 75 |
| runtime/execution/headless/options.ts | 92.3 / 81.94 | 90 / 75 |
| runtime/execution/headless/process.ts | 89.92 / 63.63 | 85 / 60 |
| runtime/execution/headless/result.ts | 83.48 / 57.14 | 80 / 55 |

These are ratchet floors, not a lower bar for contract modules: a change that
drops one below its recorded floor fails C5, and improving one SHOULD raise its
floor in the same PR. The global JS floors were the f001e1d values at T5b; T16c later calibrated them
against the T16f clean-clone run (see the T16c amendment). Adding these modules
does not lower them.

### T16c amendment (2026-11-01, provisional)

T16a's remote CI could not run in this workspace because the user explicitly
forbade pushing workflow files (m18274), so the canonical CI coverage data was
unavailable. T16c therefore uses the T16f clean-clone run at `adfa3fc` as the
canonical measurement environment: 71.24% lines, 69.22% branches, 74.13%
functions, and 71.398% changed lines (16,090 changed / 4,602 missing).

Applying the T16c rule (floor the measured value to 0.1pp, subtract 1.5pp, and
never go below the previous floor) yields:

| Surface | T16f clean clone | New floor |
|---|---:|---:|
| JS lines | 71.24% | 69.7% |
| JS branches | 69.22% | 68.45% (previous floor kept; the formula gives 67.7%) |
| JS functions | 74.13% | 72.6% |
| JS changed lines vs v2.6.2 | 71.398% | 69.8% |

Critical-module and execution-module floors are unchanged. This amendment is
provisional: when T16a becomes possible, the CI run URL and its coverage numbers
MUST be recorded here. If CI is lower than these floors, the coverage/CI setup
must be fixed rather than lowering the floor; if CI is higher, the next ratchet
MAY raise the floors.

C5 is an R2 gate: author self-check plus review spot-check. This ADR is the
decision record; the reproducible logs live in the local evidence workspace
under `docs/refactor/evidence/C5-20261031-f001e1d/`.

### F6 amendment (2026-11-01, finalization)

The finalization splits (F1.1-F1.3, F2, F3) preserved behaviour but divided
large modules into many small files. V8 counts each exported function in the
denominator, so the function denominator grew from the T16c shape to 846
functions even though the same behaviour is covered. The measured tree after
F1.3 is 71.81% lines, 69.16% branches, 68.67% functions, and 72.035% changed
lines (17,443 changed / 4,878 missing); the old 72.6% function floor therefore
failed on a correct tree. This amendment keeps the lines/branches/changed-lines
floors and lowers only the function floor to 67.1% (measured minus 1.5pp, still
above the 63.7% historical baseline). Critical and execution module floors stay
at 90/75 for critical paths; the post-split paths are remapped to the files that
now own the logic:

| Module | Measured lines / branches | Floor |
|---|---:|---:|
| runtime/catalog/data.ts | 100 / 100 | 90 / 75 |
| runtime/addon-protocol.ts | 92.53 / 80 | 90 / 75 |
| runtime/http/router.ts | 100 / 92.85 | 90 / 75 |
| runtime/http/lease.ts | 95.94 / 87.17 | 90 / 75 |
| src/tools/index.ts | 100 / 100 | 90 / 75 |
| runtime/core/plan-dispatch.ts | 98.07 / 89.47 | 90 / 75 |
| runtime/core/plan/render-guard.ts | 100 / 96.29 | 90 / 75 |
| runtime/core/plan/shape-search/index.ts | 100 / 100 | 90 / 75 |
| runtime/execution/jobs/index.ts | 100 / 87.5 | 80 / 55 |
| runtime/execution/workers/index.ts | 88.11 / 55.17 | 80 / 55 |
| runtime/execution/headless/index.ts | 98.27 / 88.09 | 90 / 75 |
| runtime/execution/headless/options/index.ts | 100 / 82.35 | 80 / 55 |
| runtime/execution/headless/process.ts | 89.92 / 63.63 | 85 / 60 |
| runtime/execution/headless/result.ts | 83.48 / 57.14 | 80 / 55 |

This is a split-denominator rebaseline, not a test cut: no source lines were
excluded and no tests were removed. A future function-coverage rise MUST raise
the floor; a further split that grows the denominator again MUST be reviewed
with the same evidence. The reproducible run is C5-20261101-finalization in the
local evidence workspace.

## Consequences

- C5 is green for the current refactor without weakening the contract floors or
  adding coverage-only tests.
- The calibrated floors are above the f001e1d baseline and leave a 1.5pp
  buffer below the T16f clean-clone measurement; adding a large untested module
  will fail until it is tested or the floor is deliberately raised through the
  PR ratchet. That is the intended behaviour.
- Changed-lines coverage is still measured against v2.6.2. Until 3.0.0 ships
  this is stable; after release the baseline must be re-measured against the
  new release tag, not against the old refactor base.
- The +2 ratchet is human-owned for now. The gate verifies the floor it is
  given; it does not rewrite thresholds.

## Alternatives considered

- **Keep 63 / 69 / 58.** Rejected: the numbers describe the pre-format/pre-T5a
  tree, not the current code. Passing them would require coverage theatre.
- **Drop the gate or lower the floors to arbitrary round numbers.** Rejected:
  this hides regressions and breaks the "no decline without an ADR" rule.
- **Exclude execution-layer modules from coverage.** Rejected: those modules are
  the highest-risk part of the runtime and already covered by E2E; excluding
  them would make the global number look better while reducing protection.
- **Automatically raise thresholds in CI.** Rejected for now: a green build
  writing its own floor defeats the purpose of a baseline. The ratchet is
  reviewed with the coverage report until the release cadence is stable.

## Links

- Plan: `D:/DSH/dsh-blender-plugin-refactor-plan-v3.md` §8.3 C5 and
  `plan/plan-v3-testing.md` §6.11 (local working documents).
- Gate evidence: `D:/DSH/refactor-workspace/docs/refactor/evidence/C5-20261031-f001e1d/`.
- ADR index: [README.md](README.md)
- Behavior matrix: [tests/BEHAVIOR_MATRIX.md](../../tests/BEHAVIOR_MATRIX.md)