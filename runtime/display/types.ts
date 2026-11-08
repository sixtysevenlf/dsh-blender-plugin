// DisplayProvider contract for virtual-gui sessions in the 3.1 topology.
//
// A provider owns one isolated display + one Blender GUI process. The Gateway
// never pretends a provider exists: selection returns "unavailable" with a
// machine-readable reason until the platform implementation is installed.

export type VirtualGuiFeature = 'off' | 'auto' | 'on'

export interface DisplayPrerequisiteSpec {
  id: string
  label: string
  check: string
  install: string
  needsRoot: boolean
  /** Optional rows name a degradation (for example software GL) instead of a start gate. */
  optional?: boolean
}

export interface DisplayPrerequisiteStatus extends DisplayPrerequisiteSpec {
  ok: boolean
  detail: string
}

export interface DisplayCapabilities {
  platform: NodeJS.Platform | string
  provider: string | null
  provider_implemented: boolean
  feature: VirtualGuiFeature
  feature_source: string
  enabled: boolean
  preview: boolean
  /** Linux/Windows only: every hardware-GL prerequisite is present (not a software fallback). */
  hardware_gl: boolean
  ready: boolean
  needs_approval: boolean
  prerequisites: DisplayPrerequisiteStatus[]
  missing: string[]
  reason: string | null
  fix: string
}

export interface DisplayStartRequest {
  sessionId: string
  /** Deterministic addon instance id the launched Blender must hand shake with. */
  instanceId?: string
  width?: number
  height?: number
  blenderArgs?: string[]
}

export interface DisplayInstance {
  id: string
  display: string
  pid: number | null
  provider: string
}

export interface DisplayHealth {
  ok: boolean
  detail: string
}

/** The only interface a platform provider may expose. No force/skip-check path. */
export interface DisplayProvider {
  kind: string
  capabilities(): DisplayCapabilities
  start(request: DisplayStartRequest): Promise<DisplayInstance>
  stop(instance: DisplayInstance): Promise<void>
  health(instance: DisplayInstance): Promise<DisplayHealth>
}
