# Contributing to DSH × Blender

[简体中文](zh-CONTRIBUTING.md)

Thanks for taking the time to help. This project gives AI agents a direct, verifiable interface to a live
Blender session. Issues and pull requests are welcome in **English or Chinese**.

Before you start, read [README.md](README.md) (or [zh-README.md](zh-README.md)) and [AGENTS.md](AGENTS.md).
AGENTS.md explains how the pieces fit together, what "installed correctly" means, and where the canonical
commands live.

## Ways to contribute

- **Bug reports** with a reproduction, the exact version from `package.json`, and the output of the
  `doctor` endpoint.
- **Documentation**: both READMEs, `docs/`, the ADRs, and the bundled skill under
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

- Node.js **22** for development and tests; Node.js 20 or newer runs the backend.
- Blender **4.2 LTS or 5.x** (GUI mode needs the bundled addon; headless mode only needs the executable).
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
| `docs/` | User guides (Chinese), ADRs, the refactor specification, and evidence packs |

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
- Use [.github/pull_request_template.md](.github/pull_request_template.md): scope, behavior-matrix row,
  test evidence, risk, rollback, disclosure, checklist.
- Keep [README.md](README.md) and [zh-README.md](zh-README.md) in sync, and the same for
  [CONTRIBUTING.md](CONTRIBUTING.md) and [zh-CONTRIBUTING.md](zh-CONTRIBUTING.md). They are two versions
  of one document, not independent pages.
- Update [CHANGELOG.md](CHANGELOG.md) under the next version heading, and [MIGRATIONS.md](MIGRATIONS.md)
  for any breaking change.
- Add an ADR under [docs/adr/](docs/adr/README.md) when a decision is expensive to reverse.
- Disclose AI assistance in both the commit trailer and the pull request; see AGENTS.md §6.
- Do not commit `node_modules`, virtualenvs, `__pycache__`, `.tgz` artifacts, or symlinks to
  dependencies. Keep a review-sized diff: the target is at most 400 changed lines per pull request.

## Release process

The release flow is owned by the plan's T16 and by [ADR 0003](docs/adr/0003-addon-package-release.md) and
[ADR 0004](docs/adr/0004-lib-build-release.md). In short: verify the gates in
[docs/refactor/spec.md](docs/refactor/spec.md), bump the version in `package.json` and `PLUGIN_VERSION`
in the same change, let `prepack` build `lib/`, attach the addon zip and the npm tarball to the release,
and rehearse the rollback before tagging. The detailed checklist lives in the release evidence directory.

## License

The project is [BSD-3-Clause](LICENSE). By contributing you agree that your contribution is licensed under
the same terms. Be respectful and assume good faith; disagreements about design are fine, personal attacks
are not.
