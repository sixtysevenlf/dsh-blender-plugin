# T1 review

- Reviewer: author (self-check).
- The new default is `npm test` = Vitest + pytest, not the old 28-step chain. The old
  chain is preserved verbatim as `npm run test:legacy` so the migration can compare
  behavior until T11 removes it.
- Fixtures currently duplicate tests/capability.lock.json and
  tests/plan_readonly.baseline.json. This is temporary by design; T11 moves the
  originals and deletes the duplicate.
- Smoke tests are intentionally trivial; their job is to prove the harness and
  isolation (no Blender, no backend, no DSH provider), not coverage.
