# Refactor evidence index

One directory per task, created when the task is accepted. Each contains at least:

- `acceptance.md` — ID / result / evidence / reviewer / date, with WHEN/THEN criteria.
- `env.txt` — machine, toolchain, env vars, version output.
- `commands.txt` — exact commands needed to reproduce the evidence.
- `test.log` / `build.log` / `typecheck.log` — raw command output, unedited.
- `review.md` — self-review findings and deferred independent reviews.

| Task | Status | Directory |
|---|---|---|
| T0 standard environment + baseline + surface_fit decision | self-checked | docs/refactor/evidence/T0/ |
| T1 layered test skeleton | self-checked | docs/refactor/evidence/T1/ |
| T2 first contract-test batch | self-checked | docs/refactor/evidence/T2/ |
| T3 fake addon channel | self-checked | docs/refactor/evidence/T3/ |

Logs are raw command output; do not translate or rewrite them.
