# Spec Delta

## Purpose

定义 3.1 首次调用与诊断失败的统一可执行契约，让 agent 能从错误本身判断停在哪一层、为什么、怎么修、降级到哪，而不是在 start、status、doctor 之间反复试探。

## ADDED Requirements

### Requirement: Every failure carries the four-question envelope

Every failed tool, route, and job response SHALL include `stage`, `root_cause`, `fix`, `fallback`, and `evidence`. A response that only says unreachable, timeout, or failed MUST NOT be emitted.

#### Scenario: First call fails without Blender
- **WHEN** a tool is called and no Blender executable is configured or detected
- **THEN** the response names `stage=config`, gives the concrete root cause, a fix command or setting, and an explicit unavailable fallback

#### Scenario: Generic error is rejected
- **WHEN** a failure has no stage or no actionable fix
- **THEN** the tool treats the response as a contract violation and surfaces the missing fields instead of a success-looking message

### Requirement: Stage identifies the failing layer

`stage` SHALL be one of the documented layers, at minimum `config`, `spawn`, `transport`, `handshake`, `addon`, `bpy`, `queue`, and `render`. A failure MUST report exactly one stage and MUST NOT collapse distinct layers into a generic unknown state.

#### Scenario: Addon is connected but bpy is busy
- **WHEN** the control connection is healthy but the Blender main thread does not answer
- **THEN** the failure reports `stage=addon` with timeout evidence, not `stage=transport` or backend unreachable

#### Scenario: Missing executable is a config failure
- **WHEN** the Blender executable path is null
- **THEN** the failure reports `stage=config` even if a later spawn would also fail

#### Scenario: Occupied Gateway port is a config failure
- **WHEN** the Gateway port is already held by another process
- **THEN** the failure reports `stage=config` with the port and the occupying process, not `stage=addon` or a Blender-unreachable message

### Requirement: Start and doctor agree

For the same instance and moment, a successful start SHALL imply that doctor reports the same instance and a usable bpy session. If doctor reports an unavailable addon or bpy, start MUST NOT report ready. The two surfaces MUST share the same readiness source.

#### Scenario: Start and doctor share readiness
- **WHEN** start returns ready for an instance
- **THEN** doctor for the same instance returns the same identity and a ready bpy state

#### Scenario: Contradictory readiness is a defect
- **WHEN** start reports ready while doctor reports the addon as unavailable
- **THEN** the contract test fails and the readiness source is fixed rather than documenting both outcomes

### Requirement: Liveness is not readiness

An HTTP or control-channel health check SHALL only prove liveness. Status and doctor MUST separately report addon readiness and bpy readiness, and MUST NOT derive readiness from a listening socket or a successful transport handshake alone.

#### Scenario: Gateway is up but Blender is absent
- **WHEN** Gateway answers its health endpoint with no addon instance connected
- **THEN** status reports no ready session and does not present the gateway liveness as a usable Blender

#### Scenario: Handshake success is not bpy success
- **WHEN** the addon handshake succeeds but the first bpy probe fails
- **THEN** status reports the addon as connected and bpy as unavailable with a bpy-stage failure

### Requirement: First-call diagnosis is bounded

On a local loopback deployment, a failed first call SHALL return a diagnostic envelope with P95 at or below 3 seconds. Every diagnosis path MUST have a hard timeout and MUST NOT leave the caller waiting on the legacy nested retry chain.

#### Scenario: Half-open addon returns in budget
- **WHEN** the addon accepts the control connection but never replies to a probe
- **THEN** the failed call returns within the documented budget with timeout evidence and an actionable fallback

#### Scenario: Timeout remains structured
- **WHEN** a diagnosis timeout expires
- **THEN** the response is a structured timeout failure with the same envelope, not a transport-level connection reset

### Requirement: Fallback is explicit and safe

`fallback` SHALL name a safe degraded path or state that the capability is unavailable. It MUST NOT silently start a visible GUI, pretend a render succeeded, or switch to an unverified transport. When no safe fallback exists, the response MUST say so.

#### Scenario: Render cannot run headless
- **WHEN** a render job cannot acquire a GPU slot or a display provider
- **THEN** the response states that rendering is unavailable and does not fabricate output files

#### Scenario: Fallback never hides an installation problem
- **WHEN** the addon extension is not installed
- **THEN** the fallback points to installation or upgrade steps and does not launch an unrelated legacy addon