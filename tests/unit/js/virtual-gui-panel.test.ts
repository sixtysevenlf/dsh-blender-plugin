import { beforeEach, describe, expect, it } from 'vitest'
import {
  handleVirtualGuiPanel,
  normalizeInstallPlans,
  resetVirtualGuiInstall,
  startVirtualGuiInstall,
  virtualGuiInstallView,
} from '../../../src/host/virtual-gui-panel.js'
import type { HostInstallPlan } from '../../../src/tui/install.js'

const OWNER = { principal: { kind: 'owner' } }
const GUEST = { principal: { kind: 'guest' } }
const PERMISSION = { allowed: false, source: 'config' as const, updatedAt: null }

function plan(over: Partial<HostInstallPlan> = {}): HostInstallPlan {
  return {
    id: 'xvfb',
    label: 'Xvfb',
    ok: false,
    needsRoot: true,
    supported: true,
    oneClick: ['pkexec', 'apt-get', 'install', '-y', 'xvfb'],
    command: 'sudo apt-get install -y xvfb',
    ...over,
  }
}

function doctorWith(plans: unknown[], capability: Record<string, unknown> = {}) {
  return async () => ({
    virtualGui: {
      enabled: true,
      ready: false,
      prerequisites: [{ id: 'xvfb', label: 'Xvfb', ok: false }],
      installPlans: plans,
      ...capability,
    },
  })
}

beforeEach(() => {
  resetVirtualGuiInstall()
})

describe('virtual-gui host install panel', () => {
  it('normalizes only executable plans and keeps manual steps', () => {
    const plans = normalizeInstallPlans([
      plan(),
      plan({ id: 'virtualgl', supported: false, oneClick: null }),
      { id: '', label: 'bad' },
      plan({
        id: 'helper',
        steps: [{ description: 'download helper', command: 'curl example' }],
      }),
    ])
    expect(plans.map((item) => item.id)).toEqual(['xvfb', 'virtualgl', 'helper'])
    expect(plans[0].supported).toBe(true)
    expect(plans[1].supported).toBe(false)
    expect(plans[2].steps).toEqual([{ description: 'download helper', command: 'curl example' }])
  })

  it('runs a one-click plan in the background and records progress', async () => {
    let resolve: ((value: { ok: boolean; detail: string }) => void) | null = null
    const running = new Promise<{ ok: boolean; detail: string }>((done) => {
      resolve = done
    })
    const seen: string[] = []
    const started = startVirtualGuiInstall(plan(), async (_plan, onLine) => {
      onLine?.('installing xvfb\nsecond line\n')
      seen.push('started')
      return running
    })
    expect(started.state).toBe('installing')
    expect(started.label).toBe('Xvfb')
    expect(seen).toEqual(['started'])
    expect(virtualGuiInstallView().lines).toEqual(['installing xvfb', 'second line'])
    resolve!({ ok: true, detail: 'done' })
    await new Promise((done) => setTimeout(done, 0))
    const done = virtualGuiInstallView()
    expect(done.state).toBe('done')
    expect(done.ok).toBe(true)
    expect(done.finishedAt).toBeTypeOf('number')
  })

  it('marks a rejected runner as failed with a bounded detail', async () => {
    startVirtualGuiInstall(plan(), async () => {
      throw new Error('installer exploded')
    })
    await new Promise((done) => setTimeout(done, 0))
    const failed = virtualGuiInstallView()
    expect(failed.state).toBe('failed')
    expect(failed.ok).toBe(false)
    expect(failed.detail).toContain('installer exploded')
  })

  it('returns permission, capability and install plans to the owner', async () => {
    const out = await handleVirtualGuiPanel({ op: 'status' }, OWNER, 39877, PERMISSION, {
      ensureBackend: async () => true,
      doctor: doctorWith([plan()]),
    })
    expect(out.ok).toBe(true)
    if (!out.ok) return
    expect(out.value.permission).toEqual(PERMISSION)
    const capability = out.value.capability as Record<string, unknown>
    expect(capability.installPlans).toBeUndefined()
    expect(capability.ready).toBe(false)
    expect((out.value.plans as unknown[]).length).toBe(1)
    expect(out.value.install).toMatchObject({ state: 'idle' })
  })

  it('rejects a non-owner peer before touching the Gateway', async () => {
    const out = await handleVirtualGuiPanel({ op: 'status' }, GUEST, 39877, PERMISSION, {
      ensureBackend: async () => true,
      doctor: doctorWith([plan()]),
    })
    expect(out.ok).toBe(false)
    if (out.ok) return
    expect(out.error.code).toBe('forbidden')
  })

  it('returns manual_install_required with the displayed command and steps', async () => {
    const manual = plan({
      id: 'virtualgl',
      label: 'VirtualGL',
      supported: false,
      oneClick: null,
      command: 'install virtualgl manually',
      steps: [{ description: 'read docs', command: 'open docs' }],
    })
    const out = await handleVirtualGuiPanel(
      { op: 'install', planId: 'virtualgl' },
      OWNER,
      39877,
      PERMISSION,
      { ensureBackend: async () => true, doctor: doctorWith([manual]) },
    )
    expect(out.ok).toBe(false)
    if (out.ok) return
    expect(out.error.code).toBe('manual_install_required')
    expect(out.error.details).toMatchObject({
      planId: 'virtualgl',
      command: 'install virtualgl manually',
      steps: [{ description: 'read docs', command: 'open docs' }],
    })
  })

  it('starts a supported plan and rejects a second concurrent install', async () => {
    let starts = 0
    const deps = {
      ensureBackend: async () => true,
      doctor: doctorWith([plan()]),
      startInstall: (next: HostInstallPlan) => {
        starts += 1
        return startVirtualGuiInstall(next, async () => new Promise(() => {}))
      },
    }
    const first = await handleVirtualGuiPanel(
      { op: 'install', planId: 'xvfb' },
      OWNER,
      39877,
      PERMISSION,
      deps,
    )
    expect(first.ok).toBe(true)
    expect(starts).toBe(1)
    const second = await handleVirtualGuiPanel(
      { op: 'install', planId: 'xvfb' },
      OWNER,
      39877,
      PERMISSION,
      deps,
    )
    expect(second.ok).toBe(false)
    if (second.ok) return
    expect(second.error.code).toBe('install_in_progress')
  })

  it('rejects unknown plans and missing backend ports', async () => {
    const unknown = await handleVirtualGuiPanel(
      { op: 'install', planId: 'nope' },
      OWNER,
      39877,
      PERMISSION,
      { ensureBackend: async () => true, doctor: doctorWith([plan()]) },
    )
    expect(unknown.ok).toBe(false)
    if (!unknown.ok) expect(unknown.error.code).toBe('plan_not_found')
    const noPort = await handleVirtualGuiPanel({ op: 'status' }, OWNER, 0, PERMISSION)
    expect(noPort.ok).toBe(false)
    if (!noPort.ok) expect(noPort.error.code).toBe('backend_unavailable')
  })
})
