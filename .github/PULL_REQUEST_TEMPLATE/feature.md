# Pull request: feature

## Summary

<!-- What the user can do after this change, and why it matters. -->

## Linked issue

<!-- Optional. Link an issue when one exists. Use "Refs #123" for partial or related work;
     replace it with "Fixes #123" when this PR fully resolves the issue.
     GitHub closes it when the PR is merged into the default branch (main). -->

Refs #

## Motivation

<!-- Problem or user request; link the issue when one exists. -->

## Task

<!-- Task or issue reference (for example an issue number), or "none" for an unrelated change. -->

## Scope

- **In scope**:
- **Out of scope**:

## Behavior matrix row

<!-- Name the row(s) in tests/BEHAVIOR_MATRIX.md this PR covers, or state why no row changes. -->

## Test evidence

<!-- Commands and result. The command table is AGENTS.md §1; do not invent variants. Link the evidence (CI run, logs, or test output) instead of pasting long logs. -->

## Build and packaging evidence

<!-- Required for src/, runtime/, extension/, or packaging changes; write "not applicable" otherwise. -->

## Risk

<!-- What can break, who is affected, how it would be detected. Note any reviewer requirement. -->

## Rollback

<!-- How to undo this change; link the architecture guide when the change is expensive to reverse. -->

## Disclosure

<!-- AI-assisted work must be disclosed: assistant, model, and what it wrote/reviewed. Write "none" if no agent. Commit trailer: AGENTS.md §6. -->

- Assistant:
- Model:
- Wrote / reviewed:

## Checklist

- [ ] Scoped to one concern; the description states the intent.
- [ ] Tests for the touched layer pass; new behavior had a failing test first.
- [ ] Documentation is updated in the same change, both languages in sync.
- [ ] No secrets, build artifacts, node_modules, venv, or hand-edited lib/.
- [ ] Boundary declarations are current: new or moved Python modules, TypeScript directories, or cross-element imports updated the import-linter contract or the ESLint matrix.
- [ ] Disclosure is filled in.
- [ ] The architecture guide is updated when the change is expensive to reverse.

<!-- Command table and rules: AGENTS.md §1 and §6. -->
