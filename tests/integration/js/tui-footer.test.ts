
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { footerField } from '../../../src/tui-host'
import { startBackend, type Backend } from '../../helpers/backend'
import { startFakeAddon } from '../../helpers/fake-addon.mjs'
import { startFakeHost, type FakeHost } from '../../helpers/fake-http'

// dsh-TUI footer field (contract v2.3 addendum section S6). The field is a pure
// function of GET /host/state, so the whole status matrix is testable without
// Blender. The live half drives a real backend plus a fake addon: an addon
// refusal with the pause text must show the paused state, and a success clears it.

const HOLDER = 'footer-it'
const PORT = 9877

describe('footer field matrix', () => {
  it('distinguishes backend down from Blender down', () => {
    const down = footerField(null, HOLDER, PORT)
    expect(down.text).toBe('Blender ○')
    expect(down.tone).toBe('dim')
    expect(down.detail).toContain(String(PORT))
    expect(down.detail).toContain('blender_viewport')

    const notConnected = footerField({ ok: true, channel: { lastOkAgeMs: null }, lease: {}, jobs: { running: 0 }, render: { busy: false } }, HOLDER, PORT)
    expect(notConnected.text).toBe('Blender ○ 未连接')
    expect(notConnected.tone).toBe('dim')
  })

  it('shows the connected state and running jobs', () => {
    const ok = footerField({ ok: true, channel: { lastOkAgeMs: 1200 }, lease: {}, jobs: { running: 0 }, render: { busy: false } }, HOLDER, PORT)
    expect(ok.text).toBe('Blender ●')
    expect(ok.tone).toBe('success')
    expect(ok.detail).toContain('GUI 通道已连上')

    const jobs = footerField({ ok: true, channel: { lastOkAgeMs: 1200 }, lease: {}, jobs: { running: 2 }, render: { busy: false } }, HOLDER, PORT)
    expect(jobs.text).toBe('Blender ● · 作业 2')
    expect(jobs.detail).toContain('2 个 headless 作业')
  })

  it('gives the paused, rendering and busy states their own label and tone', () => {
    const paused = footerField({ ok: true, channel: { aiPaused: true, lastOkAgeMs: 10 }, lease: {}, jobs: { running: 0 }, render: { busy: false } }, HOLDER, PORT)
    expect(paused.text).toBe('Blender ⏸ AI 暂停')
    expect(paused.tone).toBe('warning')

    const rendering = footerField({ ok: true, channel: { lastOkAgeMs: 10 }, lease: {}, jobs: { running: 1 }, render: { busy: true, ageMs: 12000 } }, HOLDER, PORT)
    expect(rendering.text).toBe('Blender ● 渲染中 · 作业 1')
    expect(rendering.detail).toContain('12 s')

    const busy = footerField({ ok: true, channel: { lastOkAgeMs: 10, diagnosis: 'main-thread-busy' }, lease: {}, jobs: { running: 0 }, render: { busy: false } }, HOLDER, PORT)
    expect(busy.text).toBe('Blender ● 忙')
    expect(busy.tone).toBe('warning')
  })

  it('reports a lease held by another session', () => {
    const other = footerField({ ok: true, channel: { lastOkAgeMs: 10 }, lease: { active: true, holder: 'someone-else' }, jobs: { running: 0 }, render: { busy: false } }, HOLDER, PORT)
    expect(other.text).toBe('Blender ● 他人租约')
    expect(other.tone).toBe('warning')
    expect(other.detail).toContain('someone-else')

    const mine = footerField({ ok: true, channel: { lastOkAgeMs: 10 }, lease: { active: true, holder: HOLDER }, jobs: { running: 0 }, render: { busy: false } }, HOLDER, PORT)
    expect(mine.detail).toContain('本会话持有写租约')
  })

  it('keeps the footer text within the 24-cell budget', () => {
    const states: any[] = [
      null,
      { ok: true, channel: { lastOkAgeMs: null }, lease: {}, jobs: { running: 0 }, render: {} },
      { ok: true, channel: { aiPaused: true }, lease: {}, jobs: { running: 12 }, render: {} },
      { ok: true, channel: { lastOkAgeMs: 10 }, lease: { active: true, holder: 'x' }, jobs: { running: 3 }, render: { busy: true, ageMs: 1000 } },
    ]
    for (const state of states) expect(footerField(state, HOLDER, PORT).text.length).toBeLessThanOrEqual(24)
  })
})

describe('footer against a real backend and a fake addon', () => {
  let backend: Backend
  let addon: Awaited<ReturnType<typeof startFakeAddon>>
  let paused = true

  beforeAll(async () => {
    addon = await startFakeAddon({
      behavior: (type: string) => {
        if (type === 'get_addon_info') return { status: 'success', result: { name: 'fake', version: '1.8.0', protocol: 11, paused: paused } }
        if (paused) return { status: 'error', message: 'AI 操作已暂停（fake addon）' }
        return { status: 'success', result: { type } }
      },
    })
    backend = await startBackend({ addonPort: addon.port, env: { DSH_BLENDER_HOLDER: HOLDER } })
    await backend.post('/lease', { holder: HOLDER, ttlMs: 60000 })
  })
  afterAll(async () => {
    await backend.stop()
    await addon.close()
  })

  const state = async () => (await backend.get('/host/state')).json

  it('flips to paused after the addon refuses', async () => {
    const refused = await backend.post('/cmd', { name: 'execute_code', params: { code: 'print(1)' }, timeoutMs: 5000, holder: HOLDER })
    expect(refused.status).toBe(200)
    expect(refused.json.ok).toBe(false)
    expect(String(refused.json.error)).toContain('已暂停')
    const pausedState = await state()
    expect(pausedState.channel.aiPaused).toBe(true)
    expect(footerField(pausedState, HOLDER, backend.port).text).toBe('Blender ⏸ AI 暂停')
  })

  it('clears the pause after one successful command', async () => {
    paused = false
    const ok = await backend.post('/cmd', { name: 'execute_code', params: { code: 'print(1)' }, timeoutMs: 5000, holder: HOLDER })
    expect(ok.status).toBe(200)
    expect(ok.json.ok).toBe(true)
    const cleared = await state()
    expect(cleared.channel.aiPaused).toBe(false)
    expect(cleared.channel.lastOkAgeMs).not.toBeNull()
    expect(footerField(cleared, HOLDER, backend.port).text).toBe('Blender ●')
  })

  it('mirrors the same state into the TUI footer source shape', async () => {
    const source: FakeHost = await startFakeHost(() => ({ json: { ok: true } }))
    try {
      const st = await state()
      expect(st.backend.pid).toBeGreaterThan(0)
      expect(footerField(st, HOLDER, source.port).text).toBe('Blender ●')
    } finally {
      await source.close()
    }
  })
})
