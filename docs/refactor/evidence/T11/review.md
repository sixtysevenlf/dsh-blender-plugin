# T11 review notes

STATUS: **author self-check; independent reviewer (not the author) R1 is still to do.**
This file is not evidence of an independent review.

## What was checked

- Every legacy file was inventoried before deletion: 23 unit JS, 10 integration JS, 3 unit Python,
  25 E2E Blender and 6 moved/kept files = 67 rows in tests/MIGRATION.md.
- Each target test was written/ported before the legacy file was removed (plan section 6.7).
- The legacy `test:legacy` chain and its 22 per-selftest aliases are gone from package.json.
- Legacy scripts no longer import removed runtime APIs: the remaining `runtime/*.py` files are
  pure re-export shims, pinned by tests/unit/python/test_import_smoke.py; the old JS selftests that
  reached into `lib/index.js __internals` were replaced by contract tests over public exports.
- E2E cases keep their original assertions under tests/e2e/blender/cases/ and run through
  tests/e2e/blender/legacy_runner.py (offline_bootstrap reproduces preload=...). Two cases (geo,
  plan with preload=view) were really executed on Blender 4.2.10 in this run; the rest are
  release-gated. No case was marked pass without a real exit-0 run.

## Deliberate deviations (not open work)

1. Plan 6.5 asked to split upstream_integration_selftest.py into six domain files. Its main() is not
   separable without rewriting 856 lines, so one wrapper keeps the full assertion set. Splitting
   would execute the same script six times and add no coverage. Documented in MIGRATION.md.
2. E2E cases were archived unchanged (with their sys.exit/global-state idioms) behind a wrapper
   rather than rewritten into pure pytest functions. The wrapper isolates each in one Blender
   process, so the old global-state/exit hazards do not leak between cases. Rewriting 9k lines was
   out of scope for T11 and would risk changing assertions.
3. AGENTS.md and three scripts/*.mjs comments/paths were touched minimally: the contract test
   tests/unit/js/contract/agents-doc.test.ts requires AGENTS.md to name only existing scripts/files,
   and check-structure/pyrun/sync_counts referenced the moved capability fixture and retired
   selftest paths. Without these the new contract suite is red for stale documentation.

## Known limitations

- 23 of 25 real-Blender E2E wrappers were not executed on this machine (release gate); only geo and
  plan were. The green E2E collection proves imports/markers/wrapper wiring, not the assertions.
- Upstream E2E case archives p0_real_modeling_selftest.py still writes to fixed D:/DSH/blender/tmp
  paths (legacy behavior); it is unchanged and release-gated.
- The earlier one-off backend-routes parallel flake did not reproduce in the final three runs; no
  retry was added.
