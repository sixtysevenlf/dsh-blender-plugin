# T15c — server/http migration review notes

STATUS: **author self-check; independent non-author R1 review is still to do.**
This file is not evidence of an independent review.

## What the author checked

- Commands in commands.txt were executed with Node 22 first on PATH; exit statuses are the real ones
  (see D:/DSH/tmp/t15logs and the copies in this directory).
- Behaviour was not changed on purpose: parity compares the base b8778c8 runtime with the built
  lib/runtime on the same inputs; contract counts stay 15 tools / 34 families / 228 ops / 39 commands /
  29 routes / protocol 11.
- Migration discipline: leaf modules first, server/http next, engine/core/execution last; the old .mjs
  files were deleted in the same migration commit 0a2a395; no runtime/**/*.mjs remains.
- Limits are recorded honestly: see Caveats in acceptance.md.
