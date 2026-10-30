# CI and engineering gates (T12)

This repository uses GitHub Actions for the PR gate and the release E2E gate. The
workflows are thin wrappers around the same local commands, so a green local run is
the expected first step before opening a PR.

## Workflows

| Workflow | Trigger | Runs | Blocker |
|---|---|---|---|
| `.github/workflows/pr.yml` | every pull request, push to `refactor`/`main` | typecheck, lint, format, structure/build checks, unit, integration, Python unit, behavior matrix, `npm pack --dry-run`, changed-line coverage | R1 (PR cannot merge) |
| `.github/workflows/e2e.yml` | manual, label `run-e2e`, `v*` tag | Blender 4.2.10 headless E2E + GUI E2E under Xvfb, addon zip check | R1 at release |

The PR gate never starts a real Blender process, never calls a DSH provider and never
needs a GUI. Real Blender/GUI tests live only in the E2E workflow and in
`tests/e2e/`.

## Local equivalents

```bash
# PR gate (Node 22 and Python 3.11 expected)
npm ci --legacy-peer-deps
npm run typecheck
npm run lint
npm run format:check
npm run check:structure
npm run check:build
npm run test:unit
npm run test:integration
npm run test:python
npm run test:matrix
npm pack --dry-run

# Coverage (JS + Python)
npm run test:coverage
python -m pytest --cov=runtime --cov=addon --cov-report=xml:coverage/python-coverage.xml
diff-cover coverage/lcov.info --compare-branch=origin/refactor --fail-under=80
diff-cover coverage/python-coverage.xml --compare-branch=origin/refactor --fail-under=80

# E2E (needs a Blender 4.2 LTS binary; set DSH_BLENDER_EXE)
npm run test:e2e        # headless
npm run test:e2e:gui    # GUI/Xvfb
```

## Coverage ratchet

- The CI coverage job prints the summary and uploads `coverage/` artifacts.
- `diff-cover` checks **changed lines only**: 80% line / 70% branch is the merge bar.
  A PR may not lower coverage without an ADR; the exception is a pure file move
  (Phase R) where the tooling reports the move as new lines.
- Global coverage is reported to catch regressions but is not a fixed 80% number.
- `lib/`, `tests/`, `fixtures/` and vendored code are excluded from coverage.

## Skip/flaky policy

- No unapproved `skip` in the PR set. Platform-only paths must be marked with an
  explicit reason and reported in the test log.
- A flaky test must have an issue from the `flaky-test.yml` template, an owner and
  an expiry date; it must not be in the required PR set, and retries are not allowed
  to mask failures.
- GUI E2E is the only place where a single retry is allowed for an environment
  failure; the retry must be labelled flaky in the log and tracked.

## Current limitations

- The E2E workflow downloads Blender 4.2.10 Linux from download.blender.org; runners
  without network access to that host must use a self-hosted runner or cache.
- Blender 5.x is not installed by CI; it is covered by the matrix as `EXEMPT` until a
  second Blender line is available on the runner.
