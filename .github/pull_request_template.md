# Pull request

## Summary

<!-- What changes, why, and how you verified it. Two or three sentences. -->

## Task

<!-- Task ID from the refactor plan, or "none" for an unrelated fix. One task per pull request. -->

## Scope

- **In scope**:
- **Out of scope**:

## Behavior matrix row

<!-- Name the row(s) in tests/BEHAVIOR_MATRIX.md this PR covers, or explain why no row changes. -->

## Test evidence

<!-- Paste the commands you ran and their result. The command table is AGENTS.md §1; do not invent
     variants here. Link the evidence pack under docs/refactor/evidence/<TASK>/ when the task requires one. -->

## Build and packaging evidence

<!-- Required when src/, runtime/, addon/, or packaging changes: build, typecheck, pack dry run,
     tarball install smoke. Write "not applicable" otherwise. -->

## Risk

<!-- What can break, who is affected, how it would be detected. Note any R1 gate (contract, protocol,
     addon install, security, release). -->

## Rollback

<!-- How to undo this change: revert, previous version, addon rollback, package deprecation.
     Link the relevant ADR when the change is expensive to reverse. -->

## Disclosure

<!-- AI-assisted work must be disclosed. Name the assistant, the model, and what it wrote or reviewed.
     Write "none" when no agent was involved. Commit trailer: see AGENTS.md §6. -->

- Assistant:
- Model:
- Wrote / reviewed:

## Checklist

- [ ] The change is scoped to one task and the description states the intent.
- [ ] Tests for the touched layer pass, and new behavior had a failing test first.
- [ ] Documentation is updated in the same change, with both language versions in sync.
- [ ] No secrets, build artifacts, `node_modules`, venv, or hand-edited `lib/`.
- [ ] The disclosure section is filled in.
- [ ] An ADR is added when the change is expensive to reverse.
