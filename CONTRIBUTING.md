# Contributing to DSH × Blender

Thanks for taking the time to help. This project gives AI agents a direct, verifiable interface to a live
Blender session. Issues and pull requests are welcome in **English or Chinese**.

Before you start, read [`README.md`](README.md) (or [`README.zh-CN.md`](README.zh-CN.md)) and
[`AGENTS.md`](AGENTS.md). `AGENTS.md` is the short version of how the pieces fit together and what
"installed correctly" means.

## Ways to contribute

- **Bug reports** with a reproduction, the exact version (`package.json` → `version`), and the output of
  `curl -sS http://127.0.0.1:9877/doctor`.
- **Documentation**: both READMEs, `docs/`, and the bundled skill under `skills/blender-modeling/`.
- **Tests**: a failing self-test that reproduces a real bug is more valuable than a fix without one.
- **Code**: `src/` (TypeScript), `runtime/` (backend), `addon/` (Blender side).

## Before you open an issue

1. Search existing issues and the troubleshooting section of the README.
2. Reproduce on the latest release, not on an old tag.
3. **Security issues go to [`SECURITY.md`](SECURITY.md)**, never to a public issue.

For anything larger than a bug fix — a new tool, a protocol change, a new dependency — open an issue
first so we can agree on the shape before you spend time on the implementation.

## Development setup

Requirements:

- Node.js **20+**
- Blender **4.x or 5.x** (GUI mode needs the bundled addon; headless mode only needs the executable)
- Optional: a DSH source checkout, only if you need to rebuild `lib/`

```bash
git clone https://github.com/sixtysevenlf/dsh-blender-plugin.git
cd dsh-blender-plugin
npm install
npm run typecheck
```

The repository ships a prebuilt `lib/`, so users never need to build. **If you change `src/`, rebuild and
commit the regenerated `lib/`:**

```bash
export DSH_CHECKOUT=/path/to/dsh-source-checkout
npm run build
```

Blender side: install `addon/dsh_blender_bridge.zip`, enable it, and click **Connect**. After changing the
add-on sources under `addon/dsh_blender_bridge/`, rebuild the zip with `node scripts/build-addon.mjs`.
The installation is considered good only when `curl -sS http://127.0.0.1:9877/doctor` returns `kind=ok`.

## Repository layout

| Path | What it is |
|---|---|
| `src/` | TypeScript source for the plugin host side |
| `lib/` | Committed build output of `src/` — **do not hand-edit** |
| `runtime/` | Backend: server, engine, catalog, worker, headless runner |
| `addon/` | The Blender addon package + `dsh_blender_bridge.zip` (**v1.11 / protocol 11**) — the only supported addon |
| `skills/` | The bundled `blender-modeling` skill, registered automatically |
| `tests/` | Node and Python self-tests |
| `docs/` | Operation guide, configuration reference, contracts, SOPs |

## Tests

Run the full suite before opening a pull request:

```bash
npm test
```

Useful subsets while iterating: `npm run test:lease`, `npm run test:scope`, `npm run test:recover`,
`npm run test:hygiene`, `npm run test:client` — see `package.json` for the whole list. Python self-tests run
through the same wrapper: `node scripts/pyrun.mjs tests/<name>.py`.

Most self-tests need neither Blender nor a GUI. The `test:gui` probe does need a running Blender and is not
part of `npm test`.

## Adding or renaming an operation

The catalog in `runtime/` is the **single source of truth** for families, operations and counts. After
changing it:

```bash
node scripts/sync_counts.mjs          # refresh counts in src/, docs, README, package.json
node tests/capability_lock.mjs --update   # regenerate tests/capability.lock.json
npm test
```

Explain in the pull request why the lock file changed. Never hand-edit `tests/capability.lock.json`.

## Pull requests

- One logical change per pull request; keep unrelated formatting out of it.
- Conventional commit subjects: `feat:`, `fix:`, `docs:`, `test:`, `perf:`, `chore:`.
- Keep [`README.md`](README.md) and [`README.zh-CN.md`](README.zh-CN.md) in sync — they are two versions of
  the same document, not independent pages.
- Update [`CHANGELOG.md`](CHANGELOG.md) under the next version heading.
- Do not commit `node_modules`, virtualenvs, `__pycache__`, `.tgz` artifacts, or symlinks to dependencies.
- There is no CI yet; please run `npm test` locally and paste a summary in the pull request
  (CI on GitHub Actions is planned).

## Release process

1. Bump the version in **three places**: `package.json`, `PLUGIN_VERSION` in `src/index.ts`, and the rebuilt
   `lib/`. Add the `CHANGELOG.md` section and update the version banner in both READMEs and `AGENTS.md`.
2. `npm test` must be green before tagging.
3. `npm pack` and check the package size (about 1.6 MB / 163 files; a much larger tarball means
   `runtime/ext/venv` slipped past `files` / `.npmignore`).
4. Tag `v<version>`, push `main` and the tag, then create the GitHub release and attach the resulting
   `dsh-external-dsh-blender-plugin-<version>.tgz`.

## License

The project is [BSD-3-Clause](LICENSE). By contributing you agree that your contribution is licensed under
the same terms. Be respectful and assume good faith; disagreements about design are fine, personal attacks
are not.

## 中文摘要

- 欢迎提交 issue 和 PR，中英文都可以；安全漏洞请走私密通道（见 `SECURITY.md`），不要开公开 issue。
- 改 `src/` 之后必须 `npm run build`（需要 `DSH_CHECKOUT`）并一并提交重新生成的 `lib/`；`lib/` 不要手改。
- 提交前跑 `npm test`；改过 catalog 的话先 `node scripts/sync_counts.mjs` 和
  `node tests/capability_lock.mjs --update`，并在 PR 里说明锁文件为什么变。
- 两个 README 是同一份文档的中英版本，改动要同步；新版本要更新 `CHANGELOG.md` 和 `package.json` 的版本号。
- 目前还没有 CI，请在本地跑测试并在 PR 里贴结果。
