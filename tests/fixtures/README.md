# Test fixtures

Immutable inputs shared by more than one test. Do not read state from a live
Blender/backend here; tests that need those belong in `tests/e2e/`.

- `capability.lock.json`: frozen 34 family / 228 op / 15 tool surface (copied from
  `tests/capability.lock.json` during the T1 skeleton; the legacy file is removed in T11).
- `plan_readonly.baseline.json`: frozen read-only op whitelist (same migration path).
