# Architecture decision records

An ADR records a decision that is expensive to reverse: a boundary, a dependency, a contract, or a
release mechanism. It explains **why** the repository looks the way it does, so a later contributor can
challenge the reasoning instead of only the code.

This is the only index. Every ADR is a file here; there is no separate template file (the template is
inline below), and no decision lives only in a chat log.

## Index

| ID | Decision | Status | Date |
|---|---|---|---|
| [0001](0001-runtime-ts.md) | Rewrite the backend runtime in strict TypeScript and compile it to `lib/` | Accepted | 2026-10-30 |
| [0002](0002-test-stack.md) | Test stack: Vitest, pytest, and an in-house Blender harness | Accepted | 2026-10-30 |
| [0003](0003-addon-package-release.md) | Ship the addon as a package and a zip, released with 3.0.0 | Accepted | 2026-10-30 |
| [0004](0004-lib-build-release.md) | Stop committing `lib/`, build it in `prepack`, and verify the tarball | Accepted | 2026-10-30 |

## When to write one

Write an ADR when a change:

- moves a module boundary or changes the dependency direction between host, backend, and addon;
- changes a published contract: tool names, addon commands, HTTP routes, protocol version, package
  exports, or the shape of a receipt;
- adds, removes, or replaces a third-party dependency, test framework, or build tool;
- changes how the project is installed, upgraded, released, or rolled back;
- is the kind of decision a reviewer would otherwise ask about twice.

Do **not** write one for a bug fix, a refactor inside one module, formatting, or a dependency bump that
changes no contract. Those belong in the pull request and the changelog.

One decision per record. If a decision changes later, add a new ADR that supersedes the old one; do not
rewrite history. Set the old status to `Superseded by NNNN`.

## Process

1. Copy the template below into `docs/adr/NNNN-short-title.md`, using the next four-digit number.
2. Set `Status` to `Proposed` while it is under discussion, `Accepted` once merged.
3. Link the ADR from the pull request and from the code or document it governs where useful.
4. Add the row to the index above in the same change.

ADRs are written in English (AI-facing docs are English-only; see [AGENTS.md §3](../../AGENTS.md#3-hard-rules)).
Commands are not repeated here; [AGENTS.md §1](../../AGENTS.md#1-commands-single-source) is the single source.

## Template

```markdown
# NNNN. Title in the imperative

- **Status**: Proposed | Accepted | Superseded by NNNN
- **Date**: YYYY-MM-DD
- **Deciders**: names or roles

## Context

The forces at play: current state, constraints, numbers, and what breaks if nothing changes.

## Decision

What we will do, stated as a rule another contributor can apply without asking us.

## Consequences

What becomes easier, what becomes harder, and what we accept as a cost. Include the migration order when
the decision changes existing code.

## Alternatives considered

Each alternative with the reason it lost. "Status quo" is always an alternative.

## Links

Related ADRs, the specification section, and evidence.
```

## Links

- Specification and gates: [docs/refactor/spec.md](../refactor/spec.md)
- AI collaboration rules: [AGENTS.md](../../AGENTS.md)
