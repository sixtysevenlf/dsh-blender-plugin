# Spec Delta

## Purpose

定义 addon 从 legacy zip 转为 Blender Extension 之后的打包、许可、命名空间、安装更新、零遥测与官方市场发布契约，保证用户可以按 Blender 的标准方式安装和升级，同时不牺牲协议兼容性、仓库许可边界与用户隐私。

## ADDED Requirements

### Requirement: One extension artifact replaces the legacy zip

The addon SHALL ship as exactly one Blender Extension zip using a single top-level `dsh_blender_bridge/` folder: `blender_manifest.toml`, `__init__.py`, and all modules are inside that folder, with no wrapper directory and no loose files at the archive root. The legacy `dsh_blender_bridge.zip` layout and its manual install path MUST be removed in the same release.

#### Scenario: Extension archive uses one top-level folder
- **WHEN** the addon artifact is built
- **THEN** the archive contains exactly one top-level `dsh_blender_bridge/` folder holding the manifest and package, with no root-level loose files

#### Scenario: Legacy artifact is gone
- **WHEN** the release is packaged
- **THEN** no legacy extension zip or legacy install instruction remains in the shipped files

### Requirement: The extension manifest is valid

The manifest SHALL declare quoted TOML values for `schema_version = "1.0.0"`, `id = "dsh_blender_bridge"`, `version = "2.0.0"`, `name`, `tagline`, `maintainer`, `type = "add-on"`, `blender_version_min = "4.2.0"`, and `license = ["SPDX:GPL-3.0-or-later"]`. The `[permissions]` `network` and `files` values MUST be non-empty reason strings. The package MUST NOT define `bl_info` and MUST pass the Blender 4.2 extension validator.

#### Scenario: Validator passes on the built artifact
- **WHEN** the built extension zip is validated with the Blender 4.2 CLI
- **THEN** the validator reports no missing or invalid manifest field

#### Scenario: bl_info is absent
- **WHEN** the addon package is inspected
- **THEN** it contains no bl_info registration and the extension manifest is the only install metadata

### Requirement: License boundaries are explicit

The addon SHALL be licensed GPL-3.0-or-later, the host plugin SHALL remain BSD-3-Clause, and upstream MIT notices SHALL be preserved. No file may be described as GPL if it remains under another license, and the marketplace metadata MUST reflect the addon license.

#### Scenario: Addon metadata matches the license
- **WHEN** the extension manifest and repository license files are checked
- **THEN** the addon declares GPL-3.0-or-later and the plugin root does not claim GPL

#### Scenario: Upstream notice survives
- **WHEN** the extension is packaged
- **THEN** the upstream MIT attribution is present in the artifact and documentation

### Requirement: The extension namespace is discovered dynamically

The host launch and inspection code SHALL discover the addon under either `dsh_blender_bridge` or `bl_ext.<repository>.dsh_blender_bridge`. It MUST NOT hardcode the legacy module name, read bl_info for version, or assume a zip install.

#### Scenario: Blender-launched extension is found
- **WHEN** Blender starts with the extension installed from the official repository
- **THEN** the launch bootstrap finds the bl_ext module and reads its declared addon version

#### Scenario: Missing extension is actionable
- **WHEN** no supported addon module is installed
- **THEN** launch fails with install and enable steps and does not import a legacy shim

### Requirement: Permissions and online access are honored

Network and files permissions SHALL be declared in the manifest with explicit reason strings. The addon MUST consult `bpy.app.online_access` before asset-network calls; when online access is disabled, local bridge traffic SHALL continue to work and network-backed capabilities MUST fail with an explicit policy error.

#### Scenario: Offline mode keeps the local bridge
- **WHEN** online access is disabled
- **THEN** the local Gateway bridge remains usable and network asset calls fail with a policy error

#### Scenario: Network permission is not hidden
- **WHEN** the user installs the extension
- **THEN** the manifest exposes network and files permissions before installation

### Requirement: Official marketplace publishing is gated

The first extension listing SHALL be submitted manually and pass human review. Later versions MAY upload through the official extension API using a repository secret; the workflow MUST fail or skip explicitly when the token is absent and MUST NOT bypass or fake review.

#### Scenario: Token is missing
- **WHEN** an automated release is configured to publish but no extension token is available
- **THEN** the publish step is skipped or fails explicitly and does not report a published version

#### Scenario: Upload is versioned
- **WHEN** a release uploads a new extension version
- **THEN** the request carries the version file and release notes, and the recorded result identifies the submitted version

### Requirement: Protocol compatibility is enforced at the extension boundary

The 3.1 addon SHALL report addon version 2.0.0, extension manifest version 2.0.0, and protocol 12. Gateway SHALL reject a 3.0.x addon and a protocol mismatch, and MUST NOT enable a dual-stack compatibility mode.

#### Scenario: New addon is accepted
- **WHEN** the 3.1 extension handshakes with protocol 12
- **THEN** Gateway records addon version 2.0.0 and routes the instance normally

#### Scenario: Old addon is rejected
- **WHEN** a 3.0.x addon sends protocol 11
- **THEN** it receives an upgrade-required error and no request is routed

### Requirement: The addon collects no telemetry or usage data

The addon SHALL NOT collect, buffer, persist, or transmit telemetry, analytics, prompts, code, screenshots, manual-edit events, or usage records. It MUST NOT expose a telemetry consent preference or telemetry commands, and MUST NOT install consent-gated edit-capture handlers. Local activity detection MAY remain in memory for scope grant and pause coordination, but it MUST NOT be persisted or sent off the machine.

#### Scenario: Telemetry surface is absent
- **WHEN** the addon preferences and command registry are inspected
- **THEN** there is no `telemetry_consent` preference, no telemetry/consent command, and no edit-capture handler registered

#### Scenario: Local activity is not telemetry
- **WHEN** `get_activity_state` reports human/agent/idle/both for scope grant or pause coordination
- **THEN** the response is computed from local in-memory state, and no event history is persisted or transmitted

#### Scenario: Removed command is rejected
- **WHEN** an older host sends `get_telemetry_consent`, `set_telemetry_consent`, or `drain_human_activity`
- **THEN** the addon returns an unknown-command/upgrade-required error and does not fall back to a compatibility shim

### Requirement: The addon ships no promotional community links

The addon UI SHALL NOT include Discord/community promotion, "Stay updated" calls to action, or telemetry terms links. Credential and documentation links required to configure supported asset providers MAY remain.

#### Scenario: N-panel has no community promotion
- **WHEN** the addon N-panel is drawn
- **THEN** it contains no Discord link or "Stay updated with the latest developments" call to action

#### Scenario: Preferences have no telemetry terms entry
- **WHEN** the addon preferences are drawn
- **THEN** there is no Telemetry & Privacy section and no "View Terms and Conditions" link

### Requirement: Command counts reflect telemetry removal

The addon SHALL register 25 commands and expose 26 wire commands (the 25 registered plus `ping`): 21 registered read-only commands inspect can execute plus 4 internal transport commands. The 9 legacy write commands, 3 telemetry commands, and `get_polyhaven_asset_preview` MUST be removed from the command surface. Catalog, behavior matrix, contract tests, and documentation SHALL use those counts and MUST NOT list `get_telemetry_consent`, `set_telemetry_consent`, or `drain_human_activity`.

#### Scenario: Counts match the removal
- **WHEN** the addon command registry and contract tests run
- **THEN** registered commands are 25, wire commands are 26, and the removed write, telemetry, and preview commands appear in no catalog

#### Scenario: Removed commands are absent
- **WHEN** the behavior matrix and catalog are inspected
- **THEN** no row or entry lists a telemetry/consent command or `drain_human_activity`