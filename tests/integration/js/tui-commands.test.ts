
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { blenderCommand, registerTuiHost, tuiHostSummary, __tuiHostInternals } from '../../../src/tui-host'
import type { TuiHostOptions } from '../../../src/tui-host'
import { startFakeHost, type FakeHost } from '../../helpers/fake-http'

// /blender command and the dsh-TUI wiring. The host seams are optional: a host
// that has none must get a clean no-op (no crash, no partial registration), and a
// host that has them gets the footer field, @ mentions, command tree and the
// selection bridge. Everything is driven by a scripted backend.

const OPTS: TuiHostOptions = { port: 0, holder: 'plugin-pid-1', autoStart: false, tui: true } as TuiHostOptions
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const STATE = {
  ok: true,
  channel: { lastOkAgeMs: 1200, inflight: 0, lastCmd: 'get_addon_info', aiPaused: false },
  lease: { active: true, holder: 'plugin-pid-1' },
  jobs: { running: 1 },
  render: { busy: false, ageMs: null },
  backend: { pid: 42, uptimeMs: 1000 },
}

let host: FakeHost

beforeAll(async () => {
  host = await startFakeHost((request) => {
    const path = request.path
    if (path.startsWith('/host/state')) return { json: STATE }
    if (path.startsWith('/host/jobs?') || path === '/host/jobs') {
      return { json: { ok: true, running: 1, total: 2, jobs: [
        { id: 'job-1', status: 'running', ms: 42000, script: 'D:/work/bake.py' },
        { id: 'job-0', status: 'done', ms: 3000 },
      ] } }
    }
    if (path.startsWith('/doctor')) return { json: { ok: true, addon: { kind: 'ok', summary: 'GUI 通道正常' } } }
    if (path.startsWith('/host/selection/latest')) return { json: { ok: true, seq: 0, unconsumed: false } }
    if (path.startsWith('/host/selection/consume')) return { json: { ok: true } }
    if (path.startsWith('/host/scene-items')) {
      return { json: { ok: true, objects: [{ name: 'Cube', type: 'MESH' }], materials: [{ name: 'Mat' }], collections: [{ name: 'COL_Asset' }] } }
    }
    return { json: { ok: true } }
  })
})
afterAll(async () => { await host.close() })

describe('/blender command', () => {
  it('explains status without a Blender round-trip', async () => {
    const out = await blenderCommand('status', Object.assign({}, OPTS, { port: host.port }))
    expect(out.kind).toBe('success')
    expect(out.text).toContain('通道')
    expect(out.text).toContain('租约')
    expect(out.text).toContain('作业：1 个在跑')
    expect(out.text).toContain('plugin-pid-1（本会话）')
  })

  it('lists headless jobs', async () => {
    const out = await blenderCommand('jobs', Object.assign({}, OPTS, { port: host.port }))
    expect(out.kind).toBe('success')
    expect(out.text).toContain('job-1 · running · 42 s · bake.py')
  })

  it('explains doctor', async () => {
    const out = await blenderCommand('doctor', Object.assign({}, OPTS, { port: host.port }))
    expect(out.kind).toBe('success')
    expect(out.text).toContain('诊断：ok')
    expect(out.text).toContain('GUI 通道正常')
  })

  it('rejects an unknown subcommand with the available list', async () => {
    const out = await blenderCommand('nope', Object.assign({}, OPTS, { port: host.port }))
    expect(out.kind).toBe('error')
    expect(out.text).toContain('未知子命令')
    expect(out.text).toContain('status')
  })

  it('still answers when the backend is down', async () => {
    const out = await blenderCommand('status', Object.assign({}, OPTS, { port: 1 }))
    expect(out.kind).toBe('success')
    expect(out.text).toContain('后端未运行')
    const doctor = await blenderCommand('doctor', Object.assign({}, OPTS, { port: 1 }))
    expect(doctor.kind).toBe('error')
    const jobs = await blenderCommand('jobs', Object.assign({}, OPTS, { port: 1 }))
    expect(jobs.kind).toBe('error')
  })
})

function makeCtx(services: any) {
  const calls: Array<{ kind: string, value: any }> = []
  const disposers: Array<() => void> = []
  const ctx = {
    inject(deps: string[], fn: (c: any) => void) {
      const c: any = {}
      for (const dep of deps) c[dep] = services[dep]
      c.effect = (effect: () => any) => ctx.effect(effect)
      if (deps.every((dep) => services[dep] !== undefined)) fn(c)
      return () => {}
    },
    effect(fn: () => any) {
      const dispose = fn()
      if (typeof dispose === 'function') disposers.push(dispose)
      return dispose
    },
  }
  return { ctx, calls, dispose() { while (disposers.length) { try { disposers.pop()!() } catch { /* cleanup */ } } } }
}

describe('registerTuiHost wiring', () => {
  it('registers nothing when the host has no TUI seams', () => {
    const f = makeCtx({})
    registerTuiHost(f.ctx, Object.assign({}, OPTS, { port: host.port }))
    expect(tuiHostSummary()).toContain('未启用')
    f.dispose()
  })

  it('wires the footer, mentions, command tree and selection bridge', async () => {
    const fields: any[] = []
    const mentions: any[] = []
    const commands: any[] = []
    const trees: any[] = []
    const chips: any[] = []
    const toasts: any[] = []
    const services = {
      tuiStatus: { setField: (...args: any[]) => { fields.push(args); return () => {} } },
      tuiMentions: { register: (spec: any) => { mentions.push(spec); return () => {} } },
      commands: { register: (spec: any) => { commands.push(spec); return () => {} } },
      tuiCommandTrees: { register: (spec: any) => { trees.push(spec); return () => {} } },
      tuiContextChips: { attachContext: (spec: any) => { chips.push(spec); return true } },
      tuiToast: { show: (text: string, options: any) => { toasts.push([text, options]) } },
    }
    const f = makeCtx(services)
    try {
      registerTuiHost(f.ctx, Object.assign({}, OPTS, { port: host.port }))
      expect(commands[0]).toMatchObject({ name: 'blender' })
      expect(commands[0].input.hint).toContain('status')
      expect(commands[0].description).toContain('Blender 通道')
      expect(trees[0].root).toBe('blender')
      expect(trees[0].children(['blender']).map((c: any) => c.name)).toEqual(['status', 'doctor', 'jobs'])
      expect(trees[0].children(['blender', 'status'])).toEqual([])
      expect(mentions[0]).toMatchObject({ id: 'blender', title: 'Blender' })

      // Coverage instrumentation makes the first status poll slower than the
      // old fixed 120 ms sleep; poll until the footer is wired (bounded).
      const deadline = Date.now() + 5000
      while (fields.length === 0 && Date.now() < deadline) await sleep(50)
      expect(fields.length).toBeGreaterThan(0)
      expect(fields[0][0]).toBe('blender')
      expect(fields[0][1]).toContain('Blender')
      expect(fields[0][2]).toMatchObject({ slot: 'right-head', tone: 'success', maxCells: 24, minColumns: 60 })
      expect(tuiHostSummary()).toContain('页脚 ✅')
      expect(tuiHostSummary()).toContain('/blender ✅')

      const items = await mentions[0].complete('', new AbortController().signal)
      expect(items.length).toBeGreaterThanOrEqual(3)
      expect(items.some((item: any) => item.insertText === '<blender-object>Cube</blender-object>')).toBe(true)

      const listed = await blenderCommand('status', Object.assign({}, OPTS, { port: host.port }))
      expect(listed.kind).toBe('success')
      expect(chips.every((chip) => chip && typeof chip === 'object')).toBe(true)
      expect(toasts.every((toast) => typeof toast[0] === 'string')).toBe(true)
    } finally {
      f.dispose()
    }
  })

  it('skips a seam that throws instead of crashing the host', () => {
    const f = makeCtx({
      tuiMentions: { register: () => { throw new Error('host refuses') } },
      commands: { register: () => () => {} },
      tuiCommandTrees: { register: () => { throw new Error('duplicate') } },
    })
    expect(() => registerTuiHost(f.ctx, Object.assign({}, OPTS, { port: host.port }))).not.toThrow()
    f.dispose()
  })

  it('does not register the footer when tuiStatus lacks the setField seam', () => {
    const f = makeCtx({ tuiStatus: { set: () => {} } })
    registerTuiHost(f.ctx, Object.assign({}, OPTS, { port: host.port }))
    expect(tuiHostSummary()).not.toContain('页脚 ✅')
    f.dispose()
  })

  it('exposes the internals used by doctor output', () => {
    expect(__tuiHostInternals.SUBCOMMANDS.map((s: any) => s.name)).toEqual(['status', 'doctor', 'jobs'])
    expect(typeof __tuiHostInternals.wired).toBe('object')
    expect(typeof __tuiHostInternals.bridge).toBe('object')
  })
})
