# Pull request: bug fix

## Summary

<!-- What is broken, how it reproduces, and what the fix changes. -->

## Linked issue

<!-- If this PR fully resolves an issue, add one closing keyword per issue, for example:
     Closes #123
     GitHub closes the issue when this PR is merged into the default branch (main).
     Use "Refs #123" for partial work. Keep the keyword in the PR description, not in a comment or code block. -->

Closes #

## Reproduction

<!-- Preconditions, steps, expected vs actual, and the failing test or log. -->

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

<!-- Required for src/, runtime/, addon/, or packaging changes; write "not applicable" otherwise. -->

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
- [ ] Disclosure is filled in.
- [ ] The architecture guide is updated when the change is expensive to reverse.

<!-- Command table and rules: AGENTS.md §1 and §6. -->
