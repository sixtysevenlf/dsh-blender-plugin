# Spec Delta

## Purpose

定义 addon 从 legacy zip 转为 Blender Extension 之后的打包、许可、命名空间、安装更新与官方市场发布契约，保证用户可以按 Blender 的标准方式安装和升级，同时不牺牲协议兼容性和仓库许可边界。

## ADDED Requirements

### Requirement: One extension artifact replaces the legacy zip

The addon SHALL ship as exactly one Blender Extension zip containing `blender_manifest.toml` and the addon package at the archive root. The legacy `dsh_blender_bridge.zip` layout and its manual install path MUST be removed in the same release.

#### Scenario: Extension archive has the required root
- **WHEN** the addon artifact is built
- **THEN** the archive root contains the manifest and the package, and no nested wrapper directory

#### Scenario: Legacy artifact is gone
- **WHEN** the release is packaged
- **THEN** no legacy addon zip or legacy install instruction remains in the shipped files

### Requirement: The extension manifest is valid

The manifest SHALL declare `type = add-on`, `blender_version_min = 4.2.0`, a semver version, `license = [SPDX:GPL-3.0-or-later]`, and the `network` and `files` permissions. The package MUST NOT define `bl_info` and MUST pass the Blender 4.2 extension validator.

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

Network and files permissions SHALL be declared in the manifest. The addon MUST consult `bpy.app.online_access` before asset-network calls; when online access is disabled, local bridge traffic SHALL continue to work and network-backed capabilities MUST fail with an explicit policy error.

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