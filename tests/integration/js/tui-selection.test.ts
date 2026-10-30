
import fs from 'node:fs'
import vm from 'node:vm'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import {
  selectionChipTitle, selectionMessageText, selectionTick, __tuiHostInternals,
} from '../../../src/tui-host'
import { pkgPath } from '../../helpers/pkg'
import { startFakeHost, type FakeHost } from '../../helpers/fake-http'

// v2.3 Blender native selection -> DSH draft. Two halves must agree byte for byte:
// the TUI host picker text (src/tui-host.ts) and the Web bundle (src/client/index.js).
// Also pins the CAS consume protocol: only a staged chip consumes ok:true, a failed
// attach retries, and the third failure consumes ok:false with a reason.

const FIXTURES: any[] = [
  { selection: { kind: 'objects', count: 1, names: ['Cube'], active: 'Cube', mode: 'OBJECT' } },
  { selection: { kind: 'objects', count: 2, names: ['Cube', 'Lamp'], active: 'Cube', mode: 'OBJECT' } },
  { selection: { kind: 'objects', count: 2, names: ['Cube', 'Lamp'], active: 'Cube', mode: 'OBJECT', collections: [{ name: 'COL_A', count: 2 }] } },
  { selection: { kind: 'objects', count: 412, names: Array.from({ length: 30 }, (_v, i) => 'Part_' + String(i)), active: 'Part_0' } },
  { selection: { kind: 'objects', count: 5, names: ['Cube'], truncated: true } },
  { selection: { kind: 'collection', collection: 'COL_Engine', count: 57, names: ['Piston', 'Valve', 'Bolt'] } },
  { selection: { kind: 'collection', collection: 'COL_Asset', count: 3, names: [], truncated: true } },
  { selection: { kind: 'objects', count: 1, names: ['Cube'], sub: { type: 'MESH', object: 'Cube', mode: 'EDIT', counts: { vertices: 8 }, indices: { vertices: [1, 2, 3] } } } },
  { selection: { kind: 'objects', count: 1, names: ['Cube'], sub: { type: 'MESH', object: 'Cube', mode: 'EDIT', counts: { vertices: 5000 }, indices: { vertices: Array.from({ length: 30 }, (_v, i) => i) }, truncated: true } } },
  { selection: { kind: 'objects', count: 1, names: ['Rig'], sub: { type: 'ARMATURE', object: 'Rig', mode: 'EDIT', bones: ['root', 'spine'] } } },
  { selection: { kind: 'objects', count: 1, names: ['Spline'], sub: { type: 'CURVE', object: 'Spline', points: [[0, 1], [1, 2]] } } },
  { selection: { kind: 'objects', count: 1, names: ['Lattice'], sub: { type: 'LATTICE', object: 'Lattice', points: [[0, 0]] } } },
  { selection: { kind: 'objects', count: 1, names: ['Empty'], sub: { type: 'OTHER', object: 'Empty', mode: 'EDIT', note: '自定义选择' } } },
  { selection: { kind: 'objects', count: 0, names: [] } },
  { selection: {} },
  {},
]

function loadClientSelftest(): any {
  const code = fs.readFileSync(pkgPath('src', 'client', 'index.js'), 'utf8')
  let entry: any = null
  const sandbox: any = {
    window: {
      __ModuleLoader__: {
        load(definition: any) { entry = definition },
      },
    },
    console,
  }
  vm.runInNewContext(code, sandbox)
  expect(entry && entry.id).toBe('@dsh-external/dsh-blender-plugin')
  const reactStub = new Proxy({}, { get: () => () => null })
  return entry.factory((id: string) => { if (id === 'react') return reactStub; throw new Error('unexpected require ' + id) }).__selftest
}

let clientSelftest: any

beforeAll(() => { clientSelftest = loadClientSelftest() })

describe('draft text parity between TUI host and Web bundle', () => {
  it('produces identical text for every selection shape', () => {
    expect(typeof clientSelftest.selectionMessageText).toBe('function')
    for (const fixture of FIXTURES) {
      expect(selectionMessageText(fixture)).toBe(clientSelftest.selectionMessageText(fixture))
    }
  })

  it('renders the documented one-object, multi-object and collection summaries', () => {
    expect(selectionMessageText(FIXTURES[0])).toContain('Blender 选中：Cube')
    expect(selectionMessageText(FIXTURES[0])).toContain('活动对象：Cube')
    expect(selectionMessageText(FIXTURES[3])).toContain('只列前 24 件')
    expect(selectionMessageText(FIXTURES[5])).toContain('Blender 集合：COL_Engine（57 件')
    expect(selectionMessageText(FIXTURES[13])).toBeNull()
    expect(selectionMessageText({})).toBeNull()
    expect(selectionMessageText(FIXTURES[0])).toContain('用户说"这个/这些"时指它们')
  })

  it('describes edit-mode sub selections', () => {
    expect(selectionMessageText(FIXTURES[7])).toContain('编辑模式')
    expect(selectionMessageText(FIXTURES[7])).toContain('顶点索引：1,2,3')
    expect(selectionMessageText(FIXTURES[9])).toContain('骨骼')
    expect(selectionMessageText(FIXTURES[9])).toContain('spine')
    expect(selectionMessageText(FIXTURES[11])).toContain('点')
    expect(selectionMessageText(FIXTURES[12])).toContain('自定义选择')
  })

  it('builds short chip titles per selection kind', () => {
    expect(selectionChipTitle({ kind: 'objects', count: 1, names: ['Cube'] })).toBe('Blender 选中 · Cube')
    expect(selectionChipTitle({ kind: 'objects', count: 412, names: [] })).toBe('Blender 选中 · 412 件')
    expect(selectionChipTitle({ kind: 'collection', collection: 'Engine', count: 57 })).toContain('Blender 集合 · Engine（57 件）')
    expect(selectionChipTitle({ kind: 'objects', sub: { type: 'MESH', object: 'Cube' } })).toBe('Blender 子元素 · Cube')
    const long = selectionChipTitle({ kind: 'objects', count: 1, names: ['x'.repeat(80)] })
    expect(long.length).toBeLessThanOrEqual(40)
    expect(long).toContain('…')
  })
})

describe('selection tick and consume CAS', () => {
  let host: FakeHost
  let latest: any
  let state: any

  beforeAll(async () => {
    host = await startFakeHost((request) => {
      if (request.path.startsWith('/host/selection/latest')) return { json: latest }
      if (request.path.startsWith('/host/selection/consume')) return { json: { ok: true, consumed_seq: request.body && request.body.seq } }
      return { json: { ok: true } }
    })
    latest = { ok: true, seq: 7, unconsumed: true, selection: { kind: 'objects', count: 2, names: ['Cube', 'Lamp'], active: 'Cube' } }
    state = { lastSeq: 0, fails: 0 }
    __tuiHostInternals.bridge.lastSeq = 0
  })
  afterAll(async () => { await host.close() })

  it('stages the chip, consumes ok:true and sends a toast', async () => {
    const attached: any[] = []
    const notices: any[] = []
    state = { lastSeq: 0, fails: 0 }
    latest = { ok: true, seq: 7, unconsumed: true, selection: { kind: 'objects', count: 2, names: ['Cube', 'Lamp'], active: 'Cube' } }
    await selectionTick({
      port: host.port,
      chips: { attachContext: (input: any) => { attached.push(input); return true } },
      notify: (text: string, color: string) => notices.push([text, color]),
      state,
    })
    expect(attached[0]).toMatchObject({ key: 'selection' })
    expect(attached[0].title).toBe('Blender 选中 · 2 件')
    expect(attached[0].content).toContain('Blender 选中：Cube、Lamp')
    const consume = host.requests.find((r) => r.path.startsWith('/host/selection/consume'))
    expect(consume && consume.body).toMatchObject({ seq: 7, ok: true, how: 'tui-chip' })
    expect(state.lastSeq).toBe(7)
    expect(notices[0][0]).toContain('未发送')
    expect(notices[0][1]).toBe('success')
  })

  it('does not re-consume the same seq', async () => {
    const before = host.requests.filter((r) => r.path.startsWith('/host/selection/consume')).length
    const attached: any[] = []
    await selectionTick({ port: host.port, chips: { attachContext: (input: any) => { attached.push(input); return true } }, state })
    expect(attached).toHaveLength(0)
    expect(host.requests.filter((r) => r.path.startsWith('/host/selection/consume')).length).toBe(before)
  })

  it('retries a failed attach and consumes ok:false on the third failure', async () => {
    latest = { ok: true, seq: 8, unconsumed: true, selection: { kind: 'objects', count: 1, names: ['Cube'] } }
    state = { lastSeq: 0, fails: 0 }
    const notices: any[] = []
    const chips = { attachContext: () => false }
    await selectionTick({ port: host.port, chips, notify: (t: string) => notices.push(t), state })
    await selectionTick({ port: host.port, chips, state })
    const baseline = host.requests.filter((r) => r.path.startsWith('/host/selection/consume')).length
    let consumes = host.requests.filter((r) => r.path.startsWith('/host/selection/consume'))
    expect(consumes).toHaveLength(baseline)
    await selectionTick({ port: host.port, chips, state })
    consumes = host.requests.filter((r) => r.path.startsWith('/host/selection/consume'))
    expect(consumes[consumes.length - 1].body).toMatchObject({ seq: 8, ok: false })
    expect(String(consumes[consumes.length - 1].body.reason)).toContain('没挂上')
    expect(notices[0]).toContain('没挂上')
  })

  it('consumes an empty selection as a failure instead of mounting an empty chip', async () => {
    latest = { ok: true, seq: 9, unconsumed: true, selection: { kind: 'objects', count: 0, names: [] } }
    state = { lastSeq: 0, fails: 0 }
    const attached: any[] = []
    await selectionTick({ port: host.port, chips: { attachContext: (input: any) => { attached.push(input); return true } }, state })
    expect(attached).toHaveLength(0)
    const consumes = host.requests.filter((r) => r.path.startsWith('/host/selection/consume'))
    expect(consumes[consumes.length - 1].body).toMatchObject({ seq: 9, ok: false })
    expect(String(consumes[consumes.length - 1].body.reason)).toContain('空选中')
  })

  it('never throws when the backend is unreachable', async () => {
    await expect(selectionTick({ port: 1, chips: { attachContext: () => true }, state: { lastSeq: 0, fails: 0 } })).resolves.toBeUndefined()
  })
})
