# 0010. Measure add-on pure logic separately from bpy and UI code

- **Status**: Accepted
- **Date**: 2026-11-01
- **Deciders**: plugin maintainers (F3 addon convergence)
- **Target**: gate C5, add-on layer

> Numbering note: the integration branch already carries ADR 0008
> (public facade and function budget) and 0009 (runtime module registry), so
> this decision took the next free number, 0010.

## Context

ADR 0005 defined the Python coverage surface as eight add-on modules at a
`>= 70 / 60` line/branch floor, measured at `f001e1d` as 76.95% / 71.71%.
F3 then converged the add-on:

- the command table became one pure-data module,
  `addon/dsh_blender_bridge/command_registry.py`, replacing the table fragments
  in `protocol.py` and `server.py` (38 registered / 39 wire / protocol 11);
- the selection payload limits moved to
  `addon/dsh_blender_bridge/selection/caps.py`;
- registration, UI drawing and command implementations were split into
  bpy-heavy modules that a fake-bpy unit test cannot meaningfully execute
  (registration side effects, `bpy.ops`, network calls, real edit-mode context).

A coverage number that mixes pure logic with drawing/registration code rewards
tests that only touch definitions, not behavior. The plan's F3.4 rule is
explicit: fake-bpy unit tests cover registration, command registration,
parameter validation and boundary helpers; `bpy`/UI code is omitted explicitly
and covered by real-Blender E2E (C6); no coverage-only tests.

## Decision

1. The C5 Python scope is the pure-logic surface:

   | Module | Why it is in scope |
   |---|---|
   | `command_registry.py` | single command table: names, params, handlers, gates, pause policy |
   | `protocol.py` | protocol version, pause rules, snapshot caps |
   | `config.py` | environment/legacy-var resolution, backend URL |
   | `recorders/activity.py` | activity state machine |
   | `commands/selection.py` | selection payload assembly and posting |
   | `commands/activity.py` | activity command envelopes and consent gate |
   | `integrations/hyper3d_client.py` | request-signing constants |
   | `integrations/sketchfab_client.py` | endpoint URL constants |
   | `assets/io.py` | safe file names, texture matching, mesh import helpers |
   | `selection/caps.py` | payload caps and truncation |

2. `floor` rises to `>= 80% lines / >= 70% branches` for that scope.
   The measured value at the F3 commit is 82.50% / 74.36%
   (580/703 lines, 116/156 branches), recorded in
   `config/coverage-baseline.json`.

3. Explicitly omitted from the C5 Python scope, covered by C6 real-Blender E2E
   instead: `registration.py` and `ui/**` (bpy registration/drawing
   boilerplate), `selection/payload.py` (reads real edit/pose-mode context),
   and every `commands/**` package other than `selection.py`/`activity.py`
   (bpy operators, downloads, network IO).

4. Unit tests for the new pure-logic modules live in
   `tests/unit/python/test_addon_command_registry.py`,
   `test_addon_registration.py`, `test_addon_import_helpers.py`,
   `test_addon_assets_io_helpers.py`, `test_addon_activity_commands.py` and
   `test_addon_selection_send.py`. They run against `tests/helpers/bpy_stub.py`
   and never start Blender.

5. A future change that moves code into or out of this scope MUST update
   `config/coverage-thresholds.json` and the measured numbers here in the same
   change; a silent floor edit remains a gate failure per ADR 0005.

## Consequences

- The coverage gate now protects the add-on's actual decision logic (command
  table, pause policy, payload caps, selection posting envelopes) instead of
  rewarding bpy boilerplate.
- The floor is 10pp above the ADR 0005 Python floor for lines; the extra cost is
  six focused unit files, not coverage theatre.
- `selection/payload.py` and the bpy command packages can lose line coverage
  without failing C5. That is accepted because C6 runs the real Blender round
  trip and the E2E layer is the authority for those paths; a future fake-bpy
  harness that can model edit-mode context should move them back into scope.
- ADR 0005's Python numbers remain the historical record for `f001e1d`; this
  ADR is the successor for the add-on surface, as rule 6 of ADR 0005 requires.

## Alternatives considered

- **Keep the eight-module 70/60 scope.** Rejected: the new single command table
  and caps module would escape the gate entirely, and the split lowered the
  measured scope below the plan's 80/70 target.
- **Cover everything under `addon/dsh_blender_bridge`.** Rejected: UI drawing
  and registration are bpy side effects; the tests that touch them would execute
  lines without asserting behaviour, which the plan forbids.
- **Exclude `assets/io.py` because it imports bpy.** Rejected: its file-name,
  texture-matching and download planning helpers are deterministic and testable
  with a requests double; only the import/cleanup functions need bpy and stay at
  their measured level.

## Links

- [ADR 0005](0005-coverage-rebaseline.md) — coverage baseline and successor rule.
- [ADR 0003](0003-addon-package-release.md) — add-on package/zip contract.
- `config/coverage-thresholds.json` and `config/coverage-baseline.json`.
- Evidence: `D:/DSH/refactor-workspace/docs/refactor/evidence/finalization-20261101/F3/`.
