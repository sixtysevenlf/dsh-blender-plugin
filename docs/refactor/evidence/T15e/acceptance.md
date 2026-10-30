# T15e — tests, packaging, and install switch

## Scope

§7.2 T15e and §10.2/§10.4: unit/integration import the TypeScript sources; E2E, backend spawn, pack, and install use lib/runtime JS; source maps are emitted; a clean clone builds, tests, packs, and installs green; the tarball contains lib/runtime JS and runtime Python and no .ts; both CI paths are covered.

## Result

PASS for the local and clean-clone criteria. CI wiring is the one gap: this branch has no .github/workflows (T12 not landed), so the two paths were executed in the worktree and in a fresh clone instead.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T15e.1 unit/integration tests import runtime TypeScript sources | PASS | imports.txt · test.log (Vitest resolves runtime/*.js specifiers to .ts) | author self-check | 2026-10-30 |
| T15e.2 E2E, backend spawn, pack, and install use built lib/runtime JS | PASS | built-paths.txt · e2e.log · install-smoke.log · clean2-installsmoke.log · install-smoke-final.log | author self-check | 2026-10-30 |
| T15e.3 source maps emitted and readable (inline sources) | PASS | sourcemap-sample.txt · build.log (62 .js.map) | author self-check | 2026-10-30 |
| T15e.4 clean environment build+test+pack+install all green | PASS | clean2-results.txt · clean2 logs (ci/build/checkbuild/typecheck/test/packdry/installsmoke all 0) | author self-check | 2026-10-30 |
| T15e.5 tarball contains lib/runtime/**/*.js + runtime Python, no .ts | PASS | pack-list.txt (683 entries, 62 lib/runtime JS, 85 Python, 0 non-.d.ts TS) | author self-check | 2026-10-30 |
| T15e.6 both test/import paths covered in CI | PARTIAL — local + clean-clone proof only; no CI workflow on this branch until T12/T16 | clean2 logs · .github (no workflows dir) | author self-check | 2026-10-30 |

## Caveats

- No CI workflow exists on this branch: T12 is unchecked in the plan. The functional intent (unit/integration import TS, E2E/spawn/pack/install use built JS) is proven locally and in the clean clone; wiring it into CI is handed to T12/T16.
- The clean-clone tarball of the code commit has 683 entries. Packing the final evidence commit gives 780 entries; the difference is documentation under docs/refactor/evidence only (see phase-r/pack-counts-final.json). Code entries and the B5 checks are identical.
