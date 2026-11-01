# 0008. Public facades and the function budget

- **Status**: Accepted
- **Date**: 2026-11-01
- **Deciders**: plugin maintainers
- **Target**: finalization F2.0/F1.0; enforced by `check:function-budget` and `check-runtime-layout`

## Context

The 3.0 split left 13 `runtime/py/<domain>/<name>.py` facades. Each one
repeated a root-finding block (`_dsh_root`) and a path marker. Ten facades built
the marker with a literal Windows backslash, which makes the facade fail on
POSIX before it can import its implementation. The same pattern also made the
facade surface drift: a change to the loader contract had to be copied 13
times.

Separately, the 3.0 code still contained functions far above a reviewable size:
`qc_render_views` was 941 lines, `createShapeSearch` 561, `createWorkersApi`
462. A file-level cap (ADR 0006/0007) prevents whole-file growth but does not
prevent a single function from becoming the next monolith. The refactor needs a
function-level ratchet that is strict enough to force splits and honest enough
to record deliberate debt.

## Decision

### Public facades

- Every `runtime/py/<domain>/<name>.py` facade uses the single helper
  `runtime/py/_facade.py`.
- The helper builds the implementation marker with `os.path.join`, finds the
  plugin root, puts it on `sys.path`, and imports
  `runtime.py.<domain>.<name>_impl`.
- The facade stays under 40 lines, keeps its module docstring and re-exports the
  implementation's `__all__`. It does not define `_dsh_root` / `_dsh_impl`.
- The public path, preload name and `K.dsh_<name>_api` entry point remain the
  contract; the facade is not a compatibility shim, it is the stable entry.

### Function budget

- Target: every production function <=80 lines.
- 80-120 lines is allowed only as a reported target warning; >120 fails.
- `scripts/check-function-budget.mjs` scans TypeScript and Python, and
  `config/function-budget-allowlist.json` is the debt ledger. Every entry needs
  `reason`, `owner` and `expires`; expired entries fail. New violations not in
  the ledger fail.
- The baseline (F1.0) records 106 >120 functions and one over-budget file. F1/F2
  and F3 remove entries as they split; the allowlist is not a permanent
  exception list.
- `runtime/catalog.ts` remains the one file-level data-table exception from
  ADR 0006; it is represented in the ledger, not silently skipped.

## Consequences

- A facade bug (wrong root, wrong platform path) is fixed once in `_facade.py`.
- New Python modules under `runtime/py` must use the shared helper and stay
  small; the layout test can assert both mechanically.
- Splitting a function now has a measurable target and a script; reviewers do
  not have to argue about whether 300 lines is "too big".
- The allowlist is intentionally noisy during F1/F2/F3; final handoff requires
  the debt to be gone or each remaining entry to have a fresh owner/expiry and
  an ADR-level justification.
- This ADR does not authorize deleting flat shims; that is F2.3 and is tracked
  by ADR 0009 / the finalization plan. (F2.3 completed 2026-11-01: the 38 flat
  shims were deleted and `generator` / `uv_tools` moved into `geometry`.)

## Alternatives considered

- **Keep per-facade `_dsh_root` copies.** Rejected: already caused a POSIX
  defect and forces 13 edits per loader change.
- **Turn each module into a real package and change the loader.** Rejected:
  breaks the published path-based preload contract for a structural cleanup.
- **Only a file-level cap.** Rejected: a 600-line file can still contain a
  600-line function; the function is the unit reviewers reason about.
- **Hard-fail on every >80 line function immediately.** Rejected: it would
  block the refactor on 71 existing functions and encourage cosmetic splits;
  target/hard plus a ledger is the ratchet.
