# T14 review

- Reviewer: author self-check (AI agent, 2026-10-30). Non-author review is still
  required for the R1 release gate and is recorded as PENDING, not approved.
- Scope reviewed: tests/BEHAVIOR_MATRIX.md (347 rows), scripts/check-behavior-matrix.mjs,
  ten new test files/helpers, the Blender 4.2 headless E2E script, and the package
  test:matrix entry.
- No product behavior, protocol, route, command, or op contract was changed. The only
  non-test change is the package.json `test:matrix` script (release-gate wiring).
  No plan checkbox was edited.

## Coverage confidence

- 15 model tools: each has a success-path execution test against a fake backend plus
  the shared presenter contract; parameter errors, 409 lease, exec abort, session
  routing, and unreachable backend are tested. This is unit/integration level; the
  E2E column points at the C6 real-GUI/real-addon gate.
- 39 addon commands: catalog presence, addon handler backing, gate placement, pause
  safety, handshake protocol 11, and error-envelope propagation are tested. Not every
  command is executed against a real Blender; that remains C6.
- 34 families / 228 op entries: every entry is checked against PLAN_CATALOG, its
  family, dispatch marker, read-only baseline, distance-zero suggestion, and
  cross-channel routing. Write entries are additionally tied to the lease/read-only
  integration test. Per-op live Blender execution remains C6.
- 29 HTTP routes: the startup route list and a meaningful subset (health, status,
  who, host state, 410 removals, lease 409, read-only /plan bypass, selection lifecycle,
  graceful shutdown, port conflict) are exercised against a real spawned server.
  Routes that need a live addon/Blender are contract-checked, not all live-exercised.
- Protocol: 8 rows cover handshake, mismatch advisory, frame boundary, partial frame,
  disconnect, timeout, error envelope, and pause. The partial-frame test drives
  AddonClient._onData directly; the rest use fake TCP/HTTP helpers.
- Lifecycle/config/install/security: covered by 13 lifecycle/config tests, 10 security
  tests, 3 package/tarball tests, and 7 backend-route tests. No network or real Blender
  is used in unit/integration.
- Blender 4.2: real headless E2E passed (`PASS Blender 4.2 headless E2E`). GUI and
  rendering were not exercised in this worktree.
- Blender 5.x: not installed; exempt with issue/owner/reason/review date.
- Previous DSH: not installed and obtaining it needs network; exempt with
  issue/owner/reason/review date.

## Risks / limitations

- The matrix is a contract/unit gate, not proof that every command, op, and route
  succeeds on a real Blender install. The e2e column names C6 for that gate; T14 did
  not claim a C6 pass for all rows.
- ENV-BLENDER-42 now has a real headless test; ENV-BLENDER-5X and ENV-DSH-PREVIOUS
  remain the only exemptions.
- T8/T9 are not landed in this worktree: runtime is still .mjs and the addon is still
  one dsh_blender_bridge.py file. The checker reads the current source shape; when
  T8/T9 land, it will force the matrix and references to be updated rather than
  silently passing a stale copy.
- The lifecycle test prints harmless MSYS `ps` fatal-error lines on stderr during
  child cleanup. Exit code is 0 and the assertions pass; this is an environment
  artifact, not a test failure.
- `scripts/test.mjs` could not be used directly because this worktree's node_modules
  symlink triggers EPERM in Vitest's config loader. The suite was run with
  `--configLoader runner`; the same test files and assertions run.

## Approval

- Author self-check: PASS for T14 functional scope.
- Non-author review: PENDING. A human reviewer should verify the C6 interpretation
  above and the two version/platform exemptions before release.
