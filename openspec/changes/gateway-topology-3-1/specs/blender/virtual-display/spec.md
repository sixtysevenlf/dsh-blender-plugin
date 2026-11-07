# Spec Delta

## Purpose

定义 virtual-gui 的显示提供者抽象、feature gate 与平台验收门槛：Gateway 必须能启动一个用户不可察、真实 GUI、硬件 GL 的 Blender；virtual-gui 默认 opt-in，Linux 只作 preview、不阻塞 3.1 GA；Windows 没有任何满足 GA 门槛的 provider 时，virtual-gui 不得以 headless 或软件渲染冒充。

## ADDED Requirements

### Requirement: DisplayProvider has an explicit lifecycle

Every virtual display provider SHALL implement `start(profile)`, `stop(handle)`, `health(handle)`, and `capabilities()`. `start` MUST return an identifier, a display endpoint, and a process launch description; `stop` MUST release all provider resources even when Blender has already exited.

#### Scenario: Start returns a usable handle
- **WHEN** a provider starts a virtual display
- **THEN** it returns a handle that can be passed to health, stop, and the Blender launch command

#### Scenario: Stop is idempotent
- **WHEN** stop is called twice for the same handle
- **THEN** the second call succeeds without starting or killing another process

### Requirement: Windows uses a hidden desktop without a driver

On Windows, the preferred provider SHALL launch Blender as a normal GUI process on a private WinSta0 desktop created by the bundled helper. It MUST NOT use a minimized window, a Windows virtual desktop, a display driver, or administrator rights, and it MUST fail closed if the private desktop cannot be created.

#### Scenario: Blender runs as a GUI on a hidden desktop
- **WHEN** the Windows hidden-desktop provider starts Blender
- **THEN** the process reports `bpy.app.background == False` and its window is not minimized

#### Scenario: The hidden window is not user-visible
- **WHEN** Blender is running on the private desktop
- **THEN** the default desktop taskbar and Alt-Tab do not show the Blender window

#### Scenario: Helper failure is not a visible fallback
- **WHEN** the private desktop or job object cannot be created
- **THEN** start fails with a structured provider error and never launches Blender on the user default desktop

### Requirement: IDD is a conditional fallback

A signed virtual display driver MAY be used only when the hidden-desktop provider fails the Windows GA V0 GL or viewport acceptance. Driver installation MUST require explicit user approval, MUST NOT be silent, and MUST report missing prerequisites as an actionable error. When the hidden-desktop provider passes, IDD MUST NOT be required and its absence MUST NOT block the release.

#### Scenario: IDD is offered only after a proven failure
- **WHEN** the Windows hidden-desktop provider fails the Windows GA V0 acceptance
- **THEN** the system reports the measured failure and offers the IDD provider as an explicit fallback

#### Scenario: IDD is not required after the hidden desktop passes
- **WHEN** the self-written hidden-desktop provider passes the Windows GA V0 acceptance
- **THEN** the IDD fallback is not selected and its absent driver does not block the release

#### Scenario: Missing driver is actionable
- **WHEN** the IDD provider is selected but no compatible driver is installed
- **THEN** the failure names the driver prerequisite and the user approval step instead of attempting a silent install

### Requirement: Linux uses Xvfb with a GPU GL transport

On Linux, the preferred provider SHALL use Xvfb plus VirtualGL so Blender gets a GUI display and a GPU-backed GL context. It SHALL be preview-only: it MUST NOT count as GA support or block the 3.1 GA release. A software-only provider MUST be reported as preview-only, MAY run only behind the Linux preview feature gate, and MUST NOT claim hardware GL.

#### Scenario: GPU GL is used
- **WHEN** the Linux provider starts Blender
- **THEN** the GL renderer reports the real GPU rather than llvmpipe or another software renderer

#### Scenario: Software fallback is labeled
- **WHEN** only llvmpipe is available
- **THEN** the provider reports preview-only capability, does not satisfy the hardware-GL acceptance, and MUST NOT be advertised or labeled as GA

#### Scenario: Two displays are isolated
- **WHEN** two Linux virtual-gui sessions start
- **THEN** each uses a distinct X display and neither shares the other session scene or GL context

### Requirement: virtual-gui is an explicit opt-in feature gate

The plugin SHALL expose virtual-gui behind a feature gate inside the existing bundle and MUST NOT require a second bundle. The gate SHALL default off on Linux and become auto on Windows only after Windows GA V0 passes. Capabilities and doctor SHALL report enabled state, preview, prerequisites, and missing items. When off or unready, start MUST fail before spawning with stage=config and a fix; see/op/py MUST NOT fall back to headless or software rendering.

#### Scenario: Off is explicit and spawns nothing
- **WHEN** virtual-gui is disabled or unready
- **THEN** start returns stage=config with the missing prerequisite and no Blender, Xvfb, VirtualGL, or helper process is spawned

#### Scenario: Linux preview is off by default
- **WHEN** the plugin runs on Linux with default configuration
- **THEN** virtual-gui is disabled until the user explicitly enables the Linux preview gate

#### Scenario: Linux prerequisites are actionable
- **WHEN** the Linux preview is enabled but Xvfb, VirtualGL, or /dev/dri access is missing
- **THEN** capabilities/doctor name the missing item and exact install/permission step, and start refuses without attempting an install

#### Scenario: Tool surface stays six
- **WHEN** virtual-gui is disabled
- **THEN** the six model tools remain registered and see/op/py reject without a ready GUI session; none silently becomes a headless operation

### Requirement: Windows GA V0 proves the provider before release

3.1 SHALL NOT ship Windows virtual-gui until the Windows GA V0 bake-off proves real GUI (`bpy.app.background == False`), hardware GL, GUI-path viewport capture, two concurrent instances, clean stop, and user-gui parity. The self-written hidden-desktop helper is required; signed IDD is fallback only. If both fail, Windows GA MUST be blocked. Linux preview MUST NOT block 3.1 GA.

#### Scenario: Hidden desktop passing unblocks Windows GA
- **WHEN** the self-written hidden-desktop provider passes the Windows GA V0 acceptance
- **THEN** the Windows GA release gate is satisfied and the IDD fallback is not required

#### Scenario: Both Windows providers fail blocks Windows GA
- **WHEN** both the hidden-desktop provider and the IDD fallback fail the Windows GA V0 acceptance
- **THEN** the 3.1 release is blocked for Windows and the report names the failing measurements

#### Scenario: Linux preview does not block GA
- **WHEN** the Linux provider is preview-only or fails its preview acceptance
- **THEN** the 3.1 Windows GA release is not blocked, and Linux virtual-gui remains disabled or labeled preview

#### Scenario: Headless is not a substitute
- **WHEN** a display provider is unavailable
- **THEN** virtual-gui reports unavailable and does not silently run the request as a headless job

### Requirement: Provider prerequisites are visible

Provider capabilities SHALL report required host prerequisites, installation state, and whether user approval is needed. The session tool MUST surface these facts before a failed start rather than after an opaque timeout.

#### Scenario: Capabilities are queried first
- **WHEN** an agent checks virtual-gui availability
- **THEN** the response lists each provider, its prerequisites, and its current readiness

#### Scenario: Unready provider is not selected
- **WHEN** the selected provider reports missing prerequisites
- **THEN** start fails immediately with the prerequisite and fix, not after a display timeout

### Requirement: Prerequisite bootstrap is explicit and verified

Prerequisite installation SHALL be an explicit user action, never part of plugin or extension installation. Capabilities SHALL report each prerequisite's detection result, exact install command, elevation requirement, and post-install re-check. A one-click path MUST run the displayed command with system authentication (`pkexec` or equivalent) and re-query capabilities afterwards. Without a supported one-click path, the response MUST give the exact command and MUST NOT claim installation.

#### Scenario: One-click install is authenticated and verified
- **WHEN** the user triggers the one-click prerequisite install on a supported distribution
- **THEN** the elevated command is the one that was displayed, and the provider is reported ready only after the post-install capabilities re-check passes

#### Scenario: Missing prerequisites are never silently installed
- **WHEN** a plugin or extension is installed on a host without Xvfb or VirtualGL
- **THEN** no package manager runs, and the failure names the missing packages with their install command

#### Scenario: Unsupported distribution degrades to a command
- **WHEN** the host has no supported one-click path (no polkit, AUR-only package, or unknown package manager)
- **THEN** the response returns the exact command, reports the provider as unavailable, and does not attempt an unattended install

### Requirement: Missing prerequisites are a code-level hard gate

A provider whose prerequisites are absent SHALL refuse to start inside the provider's own start path, before any process is spawned. The refusal MUST NOT depend on the caller's intent, and no force flag, environment override, or agent decision MAY bypass it. Capabilities SHALL report the provider as unavailable and name the missing prerequisites.

#### Scenario: Explicit start is still refused
- **WHEN** a caller explicitly starts virtual-gui on a Linux host without Xvfb or VirtualGL
- **THEN** the provider start path returns a prerequisite failure and spawns no Xvfb, VirtualGL, or Blender process

#### Scenario: No bypass exists
- **WHEN** the configuration or call arguments are inspected for an override
- **THEN** no force/unsafe/skip-check option can make an unready provider start

#### Scenario: Re-check after installation
- **WHEN** the missing packages are installed while the process is running
- **THEN** the next capabilities query reports the provider as available without requiring a restart