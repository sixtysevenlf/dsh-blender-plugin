import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildDisplayCapabilities } from '../../../runtime/display/prerequisites.js'
import {
  clampVirtualGuiLimit,
  resolveVirtualGuiLimits,
  VIRTUAL_GUI_DEFAULT_MAX,
} from '../../../runtime/display/limits.js'
import { SessionRegistry } from '../../../runtime/core/sessions/registry.js'
import type {
  DisplayHealth,
  DisplayInstance,
  DisplayProvider,
  DisplayStartRequest,
} from '../../../runtime/display/types.js'
import type { VirtualGuiLimits } from '../../../runtime/display/limits.js'

const WIN_CHECKS = { 'win-hidden-desktop-helper': true, blender: true }
const MAIN = { hostId: 'h', dshSessionId: 's', agentId: 'a', role: 'main' as const }
const OTHER = { hostId: 'h2', dshSessionId: 's2', agentId: 'a2', role: 'main' as const }

function readyCap() {
  return buildDisplayCapabilities({
    platform: 'win32',
    configured: 'on',
    checks: WIN_CHECKS,
    providerImplemented: true,
  })
}

function fakeProvider() {
  const started: DisplayStartRequest[] = []
  const stopped: DisplayInstance[] = []
  let display: DisplayInstance | null = null
  const provider: DisplayProvider = {
    kind: 'fake',
    capabilities: readyCap,
    async start(request) {
      started.push(request)
      display = { id: 'display_1', display: 'dsh-vgui-test', pid: 4242, provider: 'fake' }
      return display
    },
    async stop(instance) {
      stopped.push(instance)
    },
    async health(): Promise<DisplayHealth> {
      return { ok: true, detail: 'fake' }
    },
  }
  return { provider, started, stopped }
}

function limits(limit: number): VirtualGuiLimits {
  return {
    limit,
    configured: limit,
    low_resource: false,
    reason: 'configured',
    total_mem_bytes: 0,
    free_mem_bytes: 0,
  }
}

function makeRegistry(opts: {
  provider?: DisplayProvider | null
  wait?: (instanceId: string, timeoutMs: number) => Promise<boolean>
  max?: number
  instances?: { instanceId: string; ready: boolean }[]
}) {
  return new SessionRegistry(() => opts.instances || [], {
    display: readyCap,
    selectProvider: () => (opts.provider === undefined ? null : opts.provider),
    waitForInstance: opts.wait || (async () => true),
    limits: () => limits(opts.max ?? 4),
  })
}

async function startWithConsent(reg: SessionRegistry, caller = MAIN) {
  return reg.start(caller)
}

afterEach(() => {
  vi.useRealTimers()
})

describe('virtual-gui lifecycle', () => {
  it('registers a ready session only after the addon handshake', async () => {
    const { provider, started } = fakeProvider()
    const gate: { release?: (value: boolean) => void } = {}
    const wait = () =>
      new Promise<boolean>((resolve) => {
        gate.release = resolve
      })
    const reg = makeRegistry({ provider, wait })
    const pending = startWithConsent(reg)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(reg.listFor(MAIN)).toEqual([])
    expect(started).toHaveLength(1)
    expect(started[0].instanceId).toMatch(/^vgui-/)
    gate.release?.(true)
    const result = await pending
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.session.status).toBe('ready')
    expect(result.session.kind).toBe('virtual-gui')
    expect(result.session.instanceId).toBe(started[0].instanceId)
    expect(result.session.display).toMatchObject({ provider: 'fake', pid: 4242 })
    expect(reg.getSession(String(result.session.id))?.provider).toBe(provider)
  })

  it('stops the provider and leaves no session when the handshake never arrives', async () => {
    const { provider, stopped } = fakeProvider()
    const reg = makeRegistry({ provider, wait: async () => false })
    const result = await startWithConsent(reg)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.code).toBe('addon_handshake_timeout')
    expect(result.stage).toBe('handshake')
    expect(stopped).toHaveLength(1)
    expect(reg.listFor(MAIN)).toEqual([])
  })

  it('passes a structured provider error through without leaving a session', async () => {
    const provider = {
      kind: 'fake',
      capabilities: readyCap,
      async start(): Promise<DisplayInstance> {
        const error = new Error('helper missing') as Error & { view: () => unknown }
        error.view = () => ({
          ok: false,
          stage: 'config',
          code: 'helper_missing',
          root_cause: 'hidden-desktop helper not found',
          fix: 'install a 3.1 build',
          evidence: { checked: ['vendor'] },
        })
        throw error
      },
      async stop(): Promise<void> {},
      async health(): Promise<DisplayHealth> {
        return { ok: true, detail: 'fake' }
      },
    } satisfies DisplayProvider
    const reg = makeRegistry({ provider })
    const result = await startWithConsent(reg)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.stage).toBe('config')
    expect(result.code).toBe('helper_missing')
    expect(result.evidence.checked).toEqual(['vendor'])
    expect(reg.listFor(MAIN)).toEqual([])
  })

  it('rejects a start above the instance limit before spawning anything', async () => {
    const { provider, started } = fakeProvider()
    const reg = makeRegistry({ provider, max: 1 })
    const first = await startWithConsent(reg, MAIN)
    expect(first.ok).toBe(true)
    const second = await startWithConsent(reg, OTHER)
    expect(second.ok).toBe(false)
    if (second.ok) return
    expect(second.stage).toBe('session')
    expect(second.code).toBe('capacity')
    expect(second.evidence.used).toBe(1)
    expect(second.evidence.limit).toBe(1)
    expect(started).toHaveLength(1)
    expect(reg.virtualGuiCount()).toBe(1)
  })

  it('stop disposes the provider and is idempotent', async () => {
    const { provider, stopped } = fakeProvider()
    const reg = makeRegistry({ provider })
    const started = await startWithConsent(reg)
    if (!started.ok) throw new Error('start failed')
    const id = String(started.session.id)
    const stoppedOk = await reg.stop(MAIN, id)
    expect(stoppedOk).toMatchObject({ ok: true, stopped: true })
    expect(stopped).toHaveLength(1)
    expect(reg.getSession(id)).toBeNull()
    const again = await reg.stop(MAIN, id)
    expect(again).toEqual({ ok: true, stopped: false })
  })

  it('keeps the creator attached so it can write and stop after the owner TTL', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { provider } = fakeProvider()
    const reg = makeRegistry({ provider })
    const started = await startWithConsent(reg)
    if (!started.ok) throw new Error('start failed')
    const id = String(started.session.id)
    vi.advanceTimersByTime(121_000)
    const write = reg.authorizeWrite(MAIN, id, ['exclusive'])
    expect(write.ok).toBe(true)
    const stopped = await reg.stop(MAIN, id)
    expect(stopped).toMatchObject({ ok: true, stopped: true })
    expect(reg.getSession(id)).toBeNull()
  })

  it('refuses an attach from another DSH session on a virtual instance', async () => {
    const { provider } = fakeProvider()
    const instances = [{ instanceId: '', ready: true }]
    const reg = makeRegistry({ provider, instances })
    const result = await startWithConsent(reg)
    if (!result.ok) throw new Error('start failed')
    instances[0].instanceId = String(result.session.instanceId)
    const other = reg.attach(OTHER)
    expect(other.ok).toBe(false)
    if (other.ok) return
    expect(other.code).toBe('session_in_use')
    const owner = reg.attach(MAIN)
    expect(owner.ok).toBe(true)
    if (!owner.ok) return
    expect(owner.session.id).toBe(result.session.id)
  })
})

describe('virtual-gui limits', () => {
  it('uses the configured value by default and clamps it to 1-16', () => {
    expect(resolveVirtualGuiLimits({ configured: 6 }).limit).toBe(6)
    expect(resolveVirtualGuiLimits({}).limit).toBe(VIRTUAL_GUI_DEFAULT_MAX)
    expect(clampVirtualGuiLimit(0, 4)).toBe(4)
    expect(clampVirtualGuiLimit(99, 4)).toBe(16)
    expect(clampVirtualGuiLimit('3', 4)).toBe(3)
  })

  it('honours the environment override', () => {
    expect(
      resolveVirtualGuiLimits({ configured: 4, env: { DSH_BLENDER_VIRTUAL_GUI_MAX: '8' } }).limit,
    ).toBe(8)
    expect(
      resolveVirtualGuiLimits({ configured: 4, env: { DSH_BLENDER_VIRTUAL_GUI_MAX: 'nope' } })
        .limit,
    ).toBe(4)
  })

  it('caps the limit at two under memory pressure', () => {
    const low = resolveVirtualGuiLimits({
      configured: 6,
      totalMemBytes: 8 * 1024 ** 3,
      freeMemBytes: 2 * 1024 ** 3,
    })
    expect(low.limit).toBe(2)
    expect(low.low_resource).toBe(true)
    expect(low.reason).toBe('low_memory')
    const normal = resolveVirtualGuiLimits({
      configured: 6,
      totalMemBytes: 64 * 1024 ** 3,
      freeMemBytes: 32 * 1024 ** 3,
    })
    expect(normal.limit).toBe(6)
    expect(normal.low_resource).toBe(false)
  })
})
