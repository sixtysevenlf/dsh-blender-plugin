# T15f — cleanup, docs, and rollback rehearsal

## Scope

§7.2 T15f and §10.6: no runtime .mjs / @ts-nocheck; ADR, migration notes, and evidence packs complete; v2.6.2 rollback rehearsed; B1 3-question small regression.

## Result

PASS for the cleanup, docs, evidence, and rollback criteria. B1 3-question regression was NOT executed: the WSL B1 harness exists, but it needs DSH profiles bench-m/bench-p0/bench-p1 with the fixed v2.6.1 plugin, and this host has no ~/.dsh in WSL. Handed to T16/D4 without inventing results.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T15f.1 zero runtime .mjs, zero @ts-nocheck, zero explicit any | PASS | scan.log (0 .mjs/.mts/.d.mts, 0 @ts-nocheck, 0 explicit any) · checkbuild.log | author self-check | 2026-10-30 |
| T15f.2 ADR 0001 updated with the Phase R outcome and ADR index unchanged (Accepted) | PASS | docs/adr/0001-runtime-ts.md (Outcome section) · docs/adr/README.md | author self-check | 2026-10-30 |
| T15f.3 README / AGENTS / MIGRATIONS / spec facts updated for the built-runtime layout | PASS | docs-facts.txt · README.md · AGENTS.md · MIGRATIONS.md · docs/refactor/spec.md | author self-check | 2026-10-30 |
| T15f.4 evidence packs complete for T15a-T15f and phase-r | PASS | docs/refactor/evidence/T15a..T15f · phase-r | author self-check | 2026-10-30 |
| T15f.5 v2.6.2 rollback rehearsal | PASS (Git level) / build-level limitation recorded | rollback2.log · rollback.md | author self-check | 2026-10-30 |
| T15f.6 B1 3-question small regression | NOT EXECUTED — benchmark environment incomplete (no WSL DSH profiles / v2.6.1 plugin); handed to T16/D4 | b1-regression.md | author self-check | 2026-10-30 |

## Caveats

- B1 regression is not executed and no result is claimed (see b1-regression.md).
- The pre-migration build (`bash scripts/build.sh`) requires DSH_CHECKOUT with a packages/ tree, which this host does not have; the release-level rollback instead checks out the v2.6.2 tag/tarball, which carries lib/ and needs no rebuild. The migration-commit revert restores the base tree exactly (git diff b8778c8 HEAD = 0 lines).
