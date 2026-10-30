# 0004. Stop committing lib, build it in prepack, and verify the tarball

- **Status**: Accepted
- **Date**: 2026-10-30
- **Deciders**: plugin maintainers
- **Target**: 3.0.0, tasks T15 and T16

## Context

The repository currently commits `lib/`, the compiled output of `src/`. Users clone and run without a
build, which is convenient, but the artifact and its source drift: a pull request that edits `src/`
without rebuilding leaves a stale `lib/`, and a pull request that rebuilds adds hundreds of generated
lines that nobody reviews. The backend runtime is interpreted from `runtime/`, so the "committed build"
covers only half of what actually runs.

The refactor plan (§1.3, §10) moves the backend to TypeScript compiled into `lib/runtime/` and removes
the committed `lib/` entirely. Microsoft npm's lifecycle gives us a natural build point: `prepack` runs
before `npm pack` and before publish.

## Decision

- `lib/` SHALL NOT be tracked in git at 3.0.0. It is produced by the build, which runs in `prepack`
  and in CI before packaging.
- The published tarball contains `lib/index.js`, `lib/runtime/**/*.js` (with source maps),
  `runtime/**/*.py`, the addon package, the skill, and the docs; it contains no `.ts`/`.mts` backend
  sources and no legacy single-file addon.
- The tarball is verified, not assumed:

  1. `npm pack --dry-run` file list is compared with the expected list (`pack-list.txt`).
  2. The tarball is installed into a clean directory with no network and no global packages.
  3. The plugin entry loads (`require`/`import`), the backend starts from `lib/runtime/server.js`,
     and `doctor` returns `kind=ok` against a fake or real addon.
  4. Size is compared with v2.6.2 (about 1.6 MB / 163 files); a large jump is explained in the release
     notes, not ignored.

- `SERVER_PATH`, the `files` allow-list, and `.npmignore` are part of the packaged contract; changing
  one requires re-running the tarball check.

**Rollback when a publish is bad.** npm versions are immutable, so rollback is a forward action:

1. Deprecate the bad version (`npm deprecate`) with a pointer to the fixed one.
2. Move the `latest` dist-tag back to the previous good version, or publish a patch built from the last
   good commit, whichever is faster and safer.
3. Do not unpublish an older version; it breaks lockfiles. Within npm's 72-hour window an unpublish is
   technically possible but is not part of this plan.
4. On GitHub, edit the release: mark it as withdrawn, replace the attached tarball with the rebuilt one
   only under a new version, and link the incident in `CHANGELOG.md`.
5. Users roll back by pinning the previous version; the backend has no persisted schema that would block
   a downgrade.

## Consequences

- The repository diff stays reviewable; generated code is no longer in pull requests.
- A clone is no longer directly runnable for the backend; contributors run the build first (the command
  table in [AGENTS.md §1](../../AGENTS.md#1-commands-single-source)).
- Packaging failures move earlier: `prepack` fails the release instead of shipping a stale `lib/`.
- The tarball check becomes a release gate (D2), documented with real logs.

## Alternatives considered

- **Keep committing `lib/`.** Rejected: drift and review noise are the problem this ADR removes.
- **Build during `npm install`.** Rejected: install lifecycle scripts are disabled in many environments,
  need a network fetch of the DSH types, and are a supply-chain risk.
- **Publish the TypeScript sources and transpile on load.** Rejected: slower startup, requires a
  TypeScript runtime dependency in production, and diverges from what the tests exercise.

## Links

- Runtime language decision: [ADR 0001](0001-runtime-ts.md)
- Release process: [CONTRIBUTING.md](../../CONTRIBUTING.md)
