import { describe, expect, it } from 'vitest'
import {
  buildDisplayCapabilities,
  prerequisiteTable,
} from '../../../runtime/display/prerequisites.js'
import { resolveVirtualGuiFeature } from '../../../runtime/display/feature.js'
import { providerCandidates, selectDisplayProvider } from '../../../runtime/display/provider.js'
import { statusView } from '../../../runtime/core/engine-api/doctor.js'
import type { EngineApiDeps } from '../../../runtime/core/engine-api/index.js'
import { SessionRegistry } from '../../../runtime/core/sessions/registry.js'

const WIN_CHECKS = { 'win-hidden-desktop-helper': true, blender: true }

describe('provider selection order', () => {
  it('tries the Windows hidden desktop before the IDD fallback', () => {
    expect(providerCandidates('win32').map((candidate) => candidate.kind)).toEqual([
      'win-hidden-desktop',
      'win-idd',
    ])
  })

  it('keeps Linux preview-only and gives macOS a single candidate', () => {
    expect(providerCandidates('linux')).toEqual([
      { kind: 'linux-xvfb-virtualgl', preview: true, implemented: false },
    ])
    expect(providerCandidates('darwin')).toEqual([
      { kind: 'macos-provider', preview: false, implemented: false },
    ])
  })

  it('never returns an unimplemented provider, even when capabilities are ready', () => {
    const selection = selectDisplayProvider()
    expect(selection.provider).toBeNull()
    expect(selection.candidates.length).toBeGreaterThan(0)
    expect(selection.reason).toBeTruthy()
  })
})

describe('status surface', () => {
  it('exposes virtualGui capabilities even when the addon is unreachable', async () => {
    const E = {
      addon: {
        send: async () => {
          throw new Error('addon unreachable')
        },
      },
      jobs: new Map(),
      runs: new Map(),
      ledgerList: () => [],
      ledgerState: () => ({}),
      runSnapshot: () => ({}),
      jobSnapshot: () => ({}),
    } as unknown as EngineApiDeps
    const view = await statusView(E)
    const virtualGui = view.virtualGui as { enabled: boolean; reason: string }
    expect(virtualGui.enabled).toBe(false)
    expect(virtualGui.reason).toBe('feature_disabled')
    expect(view.addonError).toContain('addon unreachable')
  })
})

describe('virtual-gui feature gate', () => {
  it('defaults off before V0 and auto once V0 is released', () => {
    expect(resolveVirtualGuiFeature({ platform: 'win32', v0Released: false })).toEqual({
      value: 'off',
      source: 'default:v0',
    })
    expect(resolveVirtualGuiFeature({ platform: 'win32', v0Released: true })).toEqual({
      value: 'auto',
      source: 'default:v0',
    })
    expect(resolveVirtualGuiFeature({ platform: 'linux' }).value).toBe('off')
  })

  it('keeps Linux preview explicit and fails closed on an unknown value', () => {
    expect(
      resolveVirtualGuiFeature({ platform: 'linux', configured: 'on', source: 'file:test' }),
    ).toEqual({ value: 'on', source: 'file:test' })
    expect(
      resolveVirtualGuiFeature({ platform: 'linux', configured: 'auto', source: 'file:test' }),
    ).toEqual({ value: 'off', source: 'file:test:auto-is-windows-only' })
    expect(
      resolveVirtualGuiFeature({
        platform: 'win32',
        configured: 'definitely',
        source: 'file:test',
      }),
    ).toEqual({ value: 'off', source: 'file:test:invalid' })
  })

  it('ships a complete prerequisites table with a runnable install step', () => {
    for (const platform of ['win32', 'linux', 'darwin']) {
      const table = prerequisiteTable(platform)
      expect(table.length).toBeGreaterThan(0)
      for (const row of table) {
        expect(row.id).toBeTruthy()
        expect(row.check).toBeTruthy()
        expect(row.install).toBeTruthy()
        expect(typeof row.needsRoot).toBe('boolean')
      }
    }
    expect(prerequisiteTable('linux').map((row) => row.id)).toContain('xvfb')
    expect(prerequisiteTable('linux').map((row) => row.id)).toContain('virtualgl')
    expect(prerequisiteTable('linux').map((row) => row.id)).toContain('gpu-device')
  })

  it('stays disabled when features.virtualGui is off, even with every prerequisite installed', () => {
    const cap = buildDisplayCapabilities({
      platform: 'win32',
      configured: '',
      checks: WIN_CHECKS,
      providerImplemented: true,
    })
    expect(cap.enabled).toBe(false)
    expect(cap.ready).toBe(false)
    expect(cap.reason).toBe('feature_disabled')
    expect(cap.missing).toEqual([])
    expect(cap.fix).toContain('features.virtualGui=on')
  })

  it('fails closed while the platform provider is not implemented', () => {
    const cap = buildDisplayCapabilities({
      platform: 'win32',
      configured: 'on',
      checks: WIN_CHECKS,
      providerImplemented: false,
    })
    expect(cap.enabled).toBe(true)
    expect(cap.ready).toBe(false)
    expect(cap.reason).toBe('provider_unavailable')
    expect(cap.needs_approval).toBe(true)
    expect(cap.fix).toContain('not implemented')
  })

  it('reports every missing prerequisite with its platform fix', () => {
    const cap = buildDisplayCapabilities({
      platform: 'linux',
      configured: 'on',
      env: { PATH: '' },
      checks: { blender: false },
      providerImplemented: true,
    })
    expect(cap.preview).toBe(true)
    expect(cap.ready).toBe(false)
    expect(cap.reason).toBe('prerequisites_missing')
    expect(cap.missing).toEqual(
      expect.arrayContaining(['xvfb', 'virtualgl', 'gpu-device', 'blender']),
    )
    expect(cap.fix).toContain('install the missing prerequisites')
    expect(cap.fix).toContain('apt install xvfb')
    expect(cap.fix).not.toContain('sudo apt install virtualgl')
    expect(cap.fix).toContain('not a valid step')
  })

  it('becomes ready only when the gate, provider and every prerequisite are present', () => {
    const cap = buildDisplayCapabilities({
      platform: 'win32',
      configured: 'on',
      checks: WIN_CHECKS,
      providerImplemented: true,
    })
    expect(cap.ready).toBe(true)
    expect(cap.reason).toBeNull()
    expect(cap.missing).toEqual([])
  })
})

describe('blender_session start pre-spawn gate', () => {
  const caller = { hostId: 'h', dshSessionId: 's', agentId: 'a', role: 'main' as const }

  it('returns stage=config when the feature is off', () => {
    const cap = buildDisplayCapabilities({
      platform: 'win32',
      configured: '',
      checks: WIN_CHECKS,
      providerImplemented: true,
    })
    const result = new SessionRegistry(
      () => [],
      () => cap,
    ).start(caller)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.stage).toBe('config')
    expect(result.code).toBe('feature_disabled')
    expect(result.evidence.enabled).toBe(false)
    expect(result.fix).toContain('features.virtualGui')
  })

  it('returns prerequisites_missing without spawning when the platform is incomplete', () => {
    const cap = buildDisplayCapabilities({
      platform: 'linux',
      configured: 'on',
      env: { PATH: '' },
      providerImplemented: true,
    })
    const result = new SessionRegistry(
      () => [],
      () => cap,
    ).start(caller)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.stage).toBe('config')
    expect(result.code).toBe('prerequisites_missing')
    expect(result.evidence.missing).toEqual(expect.arrayContaining(['xvfb']))
  })

  it('never pretends a ready capability is startable without a provider', () => {
    const cap = buildDisplayCapabilities({
      platform: 'win32',
      configured: 'on',
      checks: WIN_CHECKS,
      providerImplemented: true,
    })
    const result = new SessionRegistry(
      () => [],
      () => cap,
    ).start(caller)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.stage).toBe('config')
    expect(result.code).toBe('provider_unavailable')
  })
})
