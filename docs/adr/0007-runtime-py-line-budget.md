# 0007. Cap runtime/py implementation files at 1000 lines

- **Status**: Accepted
- **Date**: 2026-11-01
- **Deciders**: plugin maintainers
- **Target**: gate B10 (validated by T5d, enforced by `check:structure`)

> **Update (F2.3, 2026-11-01)**: the flat `runtime/<name>.py` shims referenced
> throughout this ADR are gone; the facade keeps the original module path under
> `runtime/py/<domain>/` and the loader remains path-based. `generator.py` /
> `uv_tools.py` now live in `geometry`. Nothing in the decision below changed.

## Context

ADR 0006 capped `src/**/*.ts`, `runtime/**/*.ts` and `addon/**/*.py` at 600
lines but deliberately left `runtime/py/**` out of scope. At that point the
Blender-side Python library was 41,746 lines and 13 modules were over 1,000
lines, led by `assembly.py` (4,291), `qc_render.py` (3,443), `audit.py`
(3,199), `motion.py` (2,720), `blockout.py` (2,634) and `imgtools.py`
(2,553).

Those modules had the same review problems as the TypeScript monoliths: a
single edit could touch unrelated features, source guards had to read a whole
file, and a generated file was indistinguishable from a hand-written one.

The loader contract makes a naive package conversion unsafe: `runtime/core/kernel.ts`
resolves a Python module by file path (`runtime/py/<domain>/<name>.py`) and
executes the file text; `offline_bootstrap.py` does the same. Turning
`audit.py` into a package would break preload, the flat `runtime/<name>.py`
shims and `K.dsh_<name>_api`.

## Decision

Large `runtime/py` modules keep the **original public file as a facade** and
move the implementation into a sibling `<name>_impl/` package:

- the facade keeps the original file name and path, so the loader, shims and
  `K.dsh_<name>_api` contract are unchanged;
- the facade locates the plugin root, imports `runtime.py.<domain>.<name>_impl`
  and copies the package `__all__` into its globals;
- the implementation package merges all submodule namespaces, including
  single-underscore helpers, so the facade preserves the original module
  surface;
- implementation files are capped at **1,000 physical lines**, not 600. The
  Blender-side code contains wide single-purpose functions (for example
  `qc_render_views`) whose 600-line split would be cosmetic and would separate
  one operation from its own state machine.

The cap covers `runtime/py/**/*.py`, excluding `__pycache__` and third-party
generated code. `npm run check:structure` reports it as
`pyLineBudget: {budget, files, violations}` and exits non-zero on a violation.
CI runs the same script.

Modules split under this decision: `assembly`, `qc_render`, `audit`,
`motion`, `blockout`, `imgtools`, `surface`, `contract`, `vehicle`,
`visualreport`, `qc`, `gate` and `deliver`. The facade pattern was necessary
because the preload contract is path-based, not because package imports are
preferred.

## Consequences

- The largest `runtime/py` implementation file is now under 1,000 lines and
  `check:structure` enforces the budget for future changes.
- The loader, flat shims, preload names and `K.dsh_*_api` entry points stay
  byte-compatible; no TypeScript loader change was needed.
- Each split adds a facade import and one extra `sys.path`/import hop at module
  boot. This is a one-time cost per process and is covered by the real-Blender
  E2E modules.
- Facades are intentionally small and boring: they must not accumulate logic.
  New behavior belongs in a submodule, and a facade over 100 lines is a review
  smell.
- `runtime/py` is no longer a silent exception to the line budget; ADR 0006's
  exclusion paragraph is superseded for this tree.

## Alternatives considered

- **Convert each module to a real Python package and update the loader.** Rejected:
  it changes a published loading contract (path-based preload, flat shims,
  `K.dsh_*_api`) for a structural cleanup, with a much larger blast radius.
- **Keep the 600-line budget for `runtime/py`.** Rejected: it would force
  cosmetic splits inside single operations and does not match the code's shape.
- **Leave `runtime/py` out of scope until after release.** Rejected by the
  project decision that structural debt is part of this refactor; deferring it
  merely moves the same risk past the release.
- **Mechanical line-based split without behavior tests.** Rejected: every split
  here is verified with `py_compile`, the Python unit suite and the real-Blender
  native E2E module for that domain.

## Links

- [ADR 0006](0006-file-size-budget.md): 600-line budget for TS/addon.
- [ADR 0001](0001-runtime-ts.md): TypeScript backend and loader boundary.
- Evidence: local refactor workspace `evidence/T5d/` (not committed).
