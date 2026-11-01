# Contributing to DSH × Blender

[简体中文](docs/zh/CONTRIBUTING.md)

Thanks for taking the time to help. This project gives AI agents a direct, verifiable interface to a live
Blender session. Issues and pull requests are welcome in **English or Chinese**.

Before you start, read [README.md](README.md) (or [docs/zh/README.md](docs/zh/README.md)) and [AGENTS.md](AGENTS.md).
AGENTS.md explains how the pieces fit together, what "installed correctly" means, and where the canonical
commands live.

## Ways to contribute

- **Bug reports** with a reproduction, the exact version from `package.json`, and the output of the
  `doctor` endpoint.
- **Documentation**: both READMEs, `docs/`, the architecture guide, and the bundled skill under
  `skills/blender-modeling/`. The Chinese user guides stay Chinese; AI-facing docs stay English.
- **Tests**: a failing test that reproduces a real bug is more valuable than a fix without one.
- **Code**: `src/` (TypeScript host), `runtime/` (backend), `addon/` (Blender side).

## Before you open an issue

1. Search existing issues and the troubleshooting section of the README.
2. Reproduce on the latest release, not on an old tag.
3. **Security issues go to [SECURITY.md](SECURITY.md)**, never to a public issue.

For anything larger than a bug fix — a new tool, a protocol change, a new dependency — open an issue first
so the shape can be agreed before you spend time on the implementation.

## Development setup

Requirements:

- Node.js **22.17** for development and tests; Node.js 20 is not tested.
- Blender **4.2 LTS** (tested); Blender 5.x is not yet validated. GUI mode needs the bundled addon; headless mode only needs the executable.
- Python **3.11+** in a virtualenv with `pytest`; select it with `PYTHON`
  (`PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` on Windows).
- Optional: a DSH source checkout, only if you need to rebuild `lib/`.

The install, build, typecheck, test, lint, and package commands are defined once in
[AGENTS.md §1](AGENTS.md#1-commands-single-source). Use those commands; do not copy variants from older
documents. A normal clone no longer ships `lib/`: build it before running the backend locally.

Blender side: install the addon package from the release zip, enable it, and click **Connect**. The
installation is good only when `doctor` returns `kind=ok` (GUI route), or the worker plus `see` returns
a frame (headless-only route).

## Repository layout

| Path | What it is |
|---|---|
| `src/` | TypeScript source for the plugin host side, split into tools, host, plan, and contracts |
| `lib/` | Build output — generated, never committed after 3.0.0, never hand-edited |
| `runtime/` | Backend: engine, server, catalog, worker, headless runner, Python domains |
| `addon/` | The Blender addon package — the only supported addon |
| `skills/` | The bundled `blender-modeling` skill, registered automatically |
| `tests/` | Unit, integration, and end-to-end layers; see [tests/README.md](tests/README.md) |
| `scripts/` | Developer build/check/test tooling; see [scripts/README.md](scripts/README.md) |
| `docs/` | User guides (Chinese), the architecture guide, the refactor specification, and evidence packs |

## Tests

The layers and their commands are described in [tests/README.md](tests/README.md). Run the layer that
matches your change while iterating, and the full pull-request gate before opening a pull request. Most
tests need neither Blender nor a GUI; the end-to-end layer does, and it runs on labels and release
candidates rather than on every pull request.

A test that fails is information. Do not add a skip, a retry, or a looser assertion to make it pass; fix
the cause or report it. Flaky tests need an issue, an owner, and an expiry date.

## Changing the catalog

The catalog in `runtime/` is the single source of truth for families, operations, and counts. After
changing it, regenerate the counts and the capability lock with the commands in AGENTS.md §1, then run the
contract tests. Explain in the pull request why the lock changed; never hand-edit the lock file.

## Pull requests

- One logical change, one task. Keep unrelated formatting out of the diff.
- Conventional commit subjects: `feat:`, `fix:`, `docs:`, `test:`, `perf:`, `chore:`.
- Use one of the [.github/PULL_REQUEST_TEMPLATE/](.github/PULL_REQUEST_TEMPLATE/) templates (`bug_fix`, `feature`, `docs_update`, `refactor`): scope, behavior-matrix row,
  test evidence, risk, rollback, disclosure, checklist.
- If the pull request fully resolves an issue, add `Closes #123` to the description (one keyword per issue); GitHub closes the issue when the PR is merged into the default branch (`main`). Use `Refs #123` for partial or related work. Keep the keyword in the PR description, not in a comment or code block.
- Keep [README.md](README.md) and [docs/zh/README.md](docs/zh/README.md) in sync, and the same for
  [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/zh/CONTRIBUTING.md](docs/zh/CONTRIBUTING.md). They are two versions
  of one document, not independent pages.
- Update [CHANGELOG.md](CHANGELOG.md) under the next version heading, and [MIGRATIONS.md](MIGRATIONS.md)
  for any breaking change.
- Read [docs/architecture.md](docs/architecture.md) before changing a boundary or contract.
- Disclose AI assistance in both the commit trailer and the pull request; see AGENTS.md §6.
- Do not commit `node_modules`, virtualenvs, `__pycache__`, `.tgz` artifacts, or symlinks to
  dependencies. Keep a review-sized diff: the target is at most 400 changed lines per pull request.

## Release process

The release flow is owned by the release checklist and by [docs/architecture.md](docs/architecture.md). In short: verify the release gates, bump the version in
`package.json` and `PLUGIN_VERSION` in the same change, let `prepack` build `lib/`, attach the addon zip
and the npm tarball to the release, and rehearse the rollback before tagging. The detailed checklist is
kept in the local refactor workspace, not in this repository.

### Manual CD

[.github/workflows/release.yml](.github/workflows/release.yml) is the manual release pipeline. It is
`workflow_dispatch` only: **Actions -> Manual CD -> Run workflow**. Inputs:

- `version` must match `package.json` and `PLUGIN_VERSION` in `src/constants.ts`;
- `dry_run` defaults to `true` and only verifies and packages;
- `dry_run=false` creates a **draft** GitHub Release and tag after the `release` environment approval;
- `publish_npm` defaults to `false`; when enabled, the packed tarball is published with provenance.

The workflow never pushes `main` or a tag itself. `workflow_dispatch` only appears after this file is on the default branch, so run it from `main` after the refactor merge (or from a branch that contains the workflow for the dry-run stage). Real Blender
E2E and the final GitHub Release publication remain human steps. Configure the `release` environment with
required reviewers, protect `v*` tags, and add `NPM_TOKEN` only when npm publishing is intended.

## License

The project is [BSD-3-Clause](LICENSE). By contributing you agree that your contribution is licensed under
the same terms. Be respectful and assume good faith; disagreements about design are fine, personal attacks
are not.