// virtual-gui lifecycle (3.1 tasks 4.4 / 4.9).
//
// A start is only successful once the addon handshake for the exact instance the
// Gateway spawned is visible. Every failure path stops the provider again, so an
// aborted start never leaves a Blender process or a half-registered session.
import { randomUUID } from 'node:crypto'
import { AVAILABILITY_STAGES, availabilityFailure } from '../availability.js'
import type { AvailabilityFailure, AvailabilityStage } from '../availability.js'
import { isRecord } from '../json.js'
import { startVirtualGuiGate } from './display-gate.js'
import { callerKey, fail, ownerMatches, ownerOf, sessionView } from './types.js'
import { touchAgent } from './presence.js'
import type { CallerIdentity, InstanceRef, SessionRecord, SessionResult } from './types.js'
import type { VirtualGuiLimits } from '../../display/limits.js'
import type { DisplayCapabilities, DisplayInstance, DisplayProvider } from '../../display/types.js'

export const VIRTUAL_GUI_HANDSHAKE_TIMEOUT_MS = 30_000
const HANDSHAKE_POLL_MS = 100

/** Everything the lifecycle needs from the registry it belongs to. */
export interface VirtualGuiHost {
  display(): DisplayCapabilities
  findForCaller(c: CallerIdentity): SessionRecord | null
  getSession(sessionId: string): SessionRecord | null
  virtualGuiCount(): number
  limits(): VirtualGuiLimits
  addSession(session: SessionRecord): void
  removeSession(sessionId: string): void
  releaseSession(session: SessionRecord): void
  waitForInstance(instanceId: string, timeoutMs: number): Promise<boolean>
}

export type ProviderSelector = (capabilities: DisplayCapabilities) => DisplayProvider | null

export interface VirtualGuiStartOptions {
  kind?: string
}

function stageOf(value: unknown): AvailabilityStage {
  const text = String(value || '')
  return (AVAILABILITY_STAGES as readonly string[]).includes(text)
    ? (text as AvailabilityStage)
    : 'spawn'
}

function providerFailure(error: unknown): AvailabilityFailure {
  if (error instanceof Error && 'view' in error && typeof error.view === 'function') {
    const view = (error as unknown as { view: () => unknown }).view()
    if (isRecord(view)) {
      return availabilityFailure({
        stage: stageOf(view.stage),
        code: String(view.code || 'provider_start_failed'),
        rootCause: String(view.root_cause || error.message || 'the display provider failed'),
        fix: String(view.fix || 'inspect the provider error and retry'),
        fallback: {
          kind: 'unavailable',
          summary: 'virtual-gui did not start; no Blender process was left behind',
        },
        evidence: isRecord(view.evidence) ? view.evidence : {},
      })
    }
  }
  return availabilityFailure({
    stage: 'spawn',
    code: 'provider_start_failed',
    rootCause: error instanceof Error ? error.message : String(error),
    fix: 'inspect the Gateway log and the display prerequisites, then retry',
    fallback: {
      kind: 'unavailable',
      summary: 'virtual-gui did not start; no Blender process was left behind',
    },
    evidence: {},
  })
}

function capacityFailure(used: number, limits: VirtualGuiLimits): AvailabilityFailure {
  return availabilityFailure({
    stage: 'session',
    code: 'capacity',
    rootCause: 'virtual-gui instance limit reached (' + used + '/' + String(limits.limit) + ')',
    fix:
      'stop an existing virtual-gui session, or raise features.virtualGuiMax ' +
      '(env DSH_BLENDER_VIRTUAL_GUI_MAX) within 1-16',
    fallback: {
      kind: 'degraded',
      summary: 'no extra Blender process was started; reuse or stop an existing session',
    },
    evidence: {
      used,
      limit: limits.limit,
      configured: limits.configured,
      low_resource: limits.low_resource,
      reason: limits.reason,
    },
  })
}

function handshakeFailure(
  instanceId: string,
  display: DisplayInstance,
  timeoutMs: number,
): AvailabilityFailure {
  return availabilityFailure({
    stage: 'handshake',
    code: 'addon_handshake_timeout',
    rootCause:
      'the spawned Blender did not connect to the Gateway within ' +
      String(timeoutMs) +
      'ms (instance ' +
      instanceId +
      ')',
    fix:
      'check that DSH Blender Bridge 2.0.0 is installed and enabled in the Blender ' +
      'the provider launched, then retry',
    fallback: {
      kind: 'unavailable',
      summary: 'the provider was stopped; no half instance was left behind',
    },
    evidence: {
      instanceId,
      display: display.display,
      pid: display.pid,
      timeoutMs,
    },
  })
}

export class VirtualGuiLifecycle {
  private readonly host: VirtualGuiHost
  private readonly selectProvider: ProviderSelector
  /** Slots reserved between the capacity check and the session becoming ready. */
  private readonly pending = new Set<string>()

  constructor(host: VirtualGuiHost, selectProvider: ProviderSelector = () => null) {
    this.host = host
    this.selectProvider = selectProvider
  }

  async start(
    caller: CallerIdentity,
    opts: VirtualGuiStartOptions = {},
  ): Promise<SessionResult<{ session: Record<string, unknown> }>> {
    const capability = this.host.display()
    const gated = startVirtualGuiGate({
      caller,
      kind: opts.kind,
      capability,
      existing: this.host.findForCaller(caller),
    })
    if (!gated.ok) return gated
    const limits = this.host.limits()
    // Count in-flight starts too: two concurrent starts used to both pass this
    // check and exceed the configured limit.
    const used = this.host.virtualGuiCount() + this.pending.size
    if (used >= limits.limit) return capacityFailure(used, limits)
    const provider = this.selectProvider(capability)
    if (!provider)
      return fail(
        'provider_unavailable',
        'config',
        'virtual-gui is ready but this build selected no startable provider',
        capability.fix || 'enable a display provider or use user-gui / headless render',
        { provider: capability.provider, missing: capability.missing },
      )
    const instanceId = 'vgui-' + randomUUID().replace(/-/g, '').slice(0, 24)
    this.pending.add(instanceId)
    let display: DisplayInstance
    try {
      display = await provider.start({ sessionId: instanceId, instanceId })
    } catch (error) {
      this.pending.delete(instanceId)
      return providerFailure(error)
    }
    const ready = await this.host.waitForInstance(instanceId, VIRTUAL_GUI_HANDSHAKE_TIMEOUT_MS)
    if (!ready) {
      this.pending.delete(instanceId)
      await this.dispose(display, provider)
      return handshakeFailure(instanceId, display, VIRTUAL_GUI_HANDSHAKE_TIMEOUT_MS)
    }
    const now = Date.now()
    const session: SessionRecord = {
      id: 'virtual-gui:' + instanceId,
      kind: 'virtual-gui',
      instanceId,
      status: 'ready',
      owner: ownerOf(caller),
      agents: new Map(),
      claimants: new Set(),
      grants: new Map(),
      paused: false,
      pausedAt: null,
      activePy: null,
      humanEdits: new Map(),
      humanModifiedAt: null,
      orphanedAt: null,
      notice: null,
      display,
      provider,
      createdAt: now,
      lastActivityAt: now,
    }
    // The creator is an attached agent of the session it started: without this
    // entry an owner-heartbeat expiry made the creator unable to write or stop
    // the virtual-gui it owns (the session then stayed alive as an orphan).
    touchAgent(session, caller)
    this.host.addSession(session)
    this.pending.delete(instanceId)
    return { ok: true, session: sessionView(session) }
  }

  async stop(
    caller: CallerIdentity,
    sessionId?: string,
  ): Promise<SessionResult<{ stopped: boolean }>> {
    const session = sessionId
      ? this.host.getSession(String(sessionId))
      : this.host.findForCaller(caller)
    if (!session) return { ok: true, stopped: false }
    if (session.kind !== 'virtual-gui') {
      return fail(
        'invalid_params',
        'session',
        'stop does not control a user-started Blender',
        'close Blender yourself; stop only manages virtual-gui',
        { sessionId: session.id },
      )
    }
    const attached = session.agents.has(callerKey(caller))
    if (!ownerMatches(session.owner, caller) && !attached) {
      return fail(
        'session_in_use',
        'session',
        'only an attached agent of the owning DSH session can stop this virtual-gui session',
        'ask the owner to stop it',
        { sessionId: session.id },
      )
    }
    const stopped = await this.disposeSession(session)
    if (stopped) return stopped
    this.host.removeSession(session.id)
    return { ok: true, stopped: true }
  }

  /**
   * Stop a virtual-gui session and report provider cleanup failure instead of
   * claiming success while an orphan Blender process may still be running.
   */
  async disposeSession(session: SessionRecord): Promise<AvailabilityFailure | null> {
    const error =
      session.provider && session.display
        ? await this.dispose(session.display, session.provider)
        : null
    if (error) return providerFailure(error)
    session.display = null
    session.provider = null
    session.status = 'stopped'
    return null
  }

  private async dispose(
    display: DisplayInstance,
    provider: DisplayProvider,
  ): Promise<unknown | null> {
    try {
      await provider.stop(display)
      return null
    } catch (error) {
      // The caller decides whether a failed stop is fatal. Start's cleanup path
      // still reports the original error; stop returns display_stop_failed.
      return error
    }
  }
}

/** Poll the Gateway instance list so a caller can inject a cheaper waiter in tests. */
export async function waitForAddonInstance(
  instances: () => InstanceRef[],
  instanceId: string,
  timeoutMs: number,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    if (instances().some((x) => x.instanceId === instanceId && x.ready)) return true
    if (Date.now() >= deadline) return false
    await new Promise((resolve) => setTimeout(resolve, HANDSHAKE_POLL_MS))
  }
}
