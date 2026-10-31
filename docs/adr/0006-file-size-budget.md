# 0006. Cap production source files at 600 lines

- **Status**: Accepted; runtime/py scope extended by [0007](0007-runtime-py-line-budget.md)
- **Date**: 2026-11-01
- **Deciders**: plugin maintainers
- **Target**: gate B9 (validated by T5c, enforced by `check:structure`)

## Context

The 3.0 refactor reduced coupling and made the layers explicit, but a few files
still carried unrelated responsibilities: the host TUI seam, the job launcher,
the Poly Haven command/client pair, and the addon core. Those files were hard to
review and diff, and review had already found defects that a smaller module
would have made visible earlier.

A line budget is a blunt instrument. It cannot prove cohesion, and a 601-line
file is not automatically worse than two 300-line files. It is useful only as a
ratchet against re-accumulation: every file that crosses the line has to make
that choice explicit in review.

## Decision

Production source files are capped at **600 physical lines**:

- `src/**/*.ts` (excluding `.d.ts`);
- `runtime/**/*.ts`, excluding `runtime/ext/` (third-party generated code);
- `addon/**/*.py`.

The only whitelist entry is `runtime/catalog.ts`. It is the data-first registry
for the plan/command catalog: 954 lines dominated by declarative tables, plus a
small set of read-only lookup helpers (fingerprint, edit-distance suggestions,
op-name rendering). Splitting the tables from those helpers would create a
second source of truth and invite drift, so the module stays whole; it is not a
logic-heavy module of the kind this budget targets. Every other exception needs
a new ADR.

`npm run check:structure` walks those trees, reports
`lineBudget: {budget, files, exempt, violations}`, and exits non-zero when a
non-exempt file exceeds the budget. CI runs the same script, so local and remote
cannot drift.

`runtime/py/**` is **out of scope for the 600-line rule**, but no longer
unbounded: [ADR 0007](0007-runtime-py-line-budget.md) gives the Blender-side
script library its own 1,000-line budget and loader-visible facade pattern.
`check:structure` now reports both budgets.

## Consequences

- T5c must split the TUI host, jobs launcher, addon core/export and Poly Haven
  client/command before B9 can pass.
- New code that pushes a file past 600 lines fails `check:structure`; the
  author either splits it or writes an ADR explaining why this one is different.
- The budget is intentionally on physical lines, not statements: it is cheap,
  deterministic, and easy for a reviewer to check.
- `runtime/py` is governed separately by [ADR 0007](0007-runtime-py-line-budget.md); this ADR still names it instead of hiding it.

## Alternatives considered

- **No budget, rely on review.** Rejected: this is how the 900-line headless
  orchestrator and 898-line dispatch were allowed to grow in the first place.
- **A 1,000-line budget.** Rejected: it would pass every current file and
  therefore enforce nothing.
- **Budget every language including `runtime/py` at 600 lines.** Rejected for
  this ADR: the Blender-side split was large and higher-risk, and 600 lines
  would have forced cosmetic splits. It is now handled at 1,000 lines by
  [ADR 0007](0007-runtime-py-line-budget.md).
- **Whitelist by path for each large file.** Rejected: it turns the budget into
  a list of exceptions and stops being a ratchet. The catalog is the one
  deliberate exception because it is data-first, not a logic module.
