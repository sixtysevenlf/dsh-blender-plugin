# Behavior matrix

Row-level truth for behavior coverage. The specification requires every row to be `covered` or an
approved exemption before 3.0.0 (gate C3); `planned` rows are allowed during the refactor and must reach
zero at release.

**Owner: T14.** This file is created by T13 as a valid link target and a fixed vocabulary. T14 populates
the rows and adds the checker; until then the table below is intentionally empty and this file is not
evidence of coverage.

## Identifiers

Rows are identified by the object they cover, so a failure names the object:

- `tool:<name>` — one of the fifteen model tools.
- `op:<family>.<op>` — one of the 228 planning operations.
- `command:<name>` — one of the 39 addon commands.
- `route:<METHOD> <path>` — one of the 29 HTTP routes.
- `protocol:<aspect>` — handshake, frames, pause, errors.
- `lifecycle:<aspect>` — install, upgrade, uninstall, rollback.
- `config:<key>` — a configuration key.
- `security:<aspect>` — guards and refusals.

## Status vocabulary

| Status | Meaning |
|---|---|
| `covered` | A test exercises the row and its evidence is linked. |
| `exempt` | Platform or version condition approved as `EXEMPT-<ID>` with owner, issue, and expiry. |
| `planned` | Not yet covered; blocks release while non-zero. |

## Rows

| ID | Surface | Status | Test / evidence |
|---|---|---|---|
| _none yet_ | _populated by T14_ | _planned_ | _none_ |
