# phase-r review notes

STATUS: **author self-check; independent non-author R1 review is still to do.**
This file is not evidence of an independent review.

## What was checked

- Every B5 command was run for real; exit codes are in results.txt and the per-step logs. Nothing was marked
  PASS from a dry run or a plan statement.
- Parity compares the base b8778c8 runtime (.mjs) with the built lib/runtime on the same inputs; contract
  counts stay 15 tools / 34 families / 228 ops / 39 commands / 29 routes / protocol 11.
- The tarball was inspected entry by entry (pack-list.txt): lib/runtime JS and runtime Python are present,
  backend .ts/.mts are absent.
- The rollback drill ran in a throwaway clone: v2.6.2 tag checkout and the migration-commit revert both
  boot the old backend. The pre-migration rebuild limitation is stated in rollback.md, not hidden.
- B1 regression is explicitly NOT EXECUTED; no result is claimed.

## Open items

- R1 non-author review of the migration and the B5 evidence.
- B1 3-question regression (T16/D4).
- CI workflow wiring when T12 lands.
