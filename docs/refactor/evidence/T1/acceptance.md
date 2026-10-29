# T1 — Layered test skeleton

## Scope
§7.1 B (first half): Vitest/pytest directories, one test entry point
(`scripts/test.mjs`), `test:legacy` preservation, and immutable fixtures.

## Result
PASS. New tests live in `tests/unit/{js,python}` and `tests/integration/{js,python}`;
`npm test` runs Vitest then pytest; `npm run test:legacy` keeps the old 28-step chain
runnable until T11. Fixtures copy the frozen capability lock and read-only baseline.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T1.1 Vitest + pytest directories exist with smoke tests that do not start Blender/DSH | PASS | tests/unit, tests/integration, T1/test.log | author self-check | 2026-10-30 |
| T1.2 `npm test` is one cross-platform entry point with js/python/unit/integration/contract modes | PASS | scripts/test.mjs + T1/test.log | author self-check | 2026-10-30 |
| T1.3 `npm run test:legacy` preserves the full 28-step chain | PASS | package.json + T0/legacy.log | author self-check | 2026-10-30 |
| T1.4 Immutable fixtures exist under tests/fixtures (capability lock + read-only baseline) | PASS | tests/fixtures/ | author self-check | 2026-10-30 |
| T1.5 The same suite passes on Windows Node 22.17 and WSL Node 22.17 | PASS | T1/test-windows.log + T1/test.log | author self-check | 2026-10-30 |

## Notes
- Python test packages now have `__init__.py` and pytest uses
  `--import-mode=importlib`, so duplicate `test_smoke.py` basenames are legal.
- `-p no:cacheprovider` avoids a read-only .pytest_cache warning in the Windows sandbox.
- requirements-dev.txt pins pytest 8.x for a reproducible local/CI install.
