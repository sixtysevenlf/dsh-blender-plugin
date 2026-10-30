
import { describe, expect, it } from 'vitest'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import {
  MAX_SELECTION_COLLECTIONS, MAX_SELECTION_NAMES, MAX_SELECTION_OBJECTS, MAX_STRING_CHARS,
  MAX_SUB_BONES, MAX_SUB_INDICES, STALE_MS,
  createSelectionStore, sanitizeSelectionPayload,
} from '../../../runtime/selectionstore.js'
import { pkgPath } from '../../helpers/pkg'

interface SubView {
  type?: string
  object?: string
  mode?: string
  select_mode?: Record<string, boolean>
  counts?: Record<string, number>
  indices: Record<string, number[]>
  bones?: string[]
  truncated?: boolean
}
interface SelectionView {
  kind?: string
  count?: number
  names: string[]
  active?: string | null
  mode?: string
  objects: Array<Record<string, unknown>>
  collections: Array<Record<string, unknown>>
  collection?: string
  sub?: SubView | null
  truncated?: boolean
}
interface SanitizedView {
  v?: number
  source?: string
  sent_at?: number
  scene?: Record<string, unknown>
  activity?: Record<string, unknown> | null
  selection: SelectionView
  [key: string]: unknown
}
const sanitize = (payload: unknown): SanitizedView =>
  sanitizeSelectionPayload(payload) as unknown as SanitizedView

// The selection store is the only server-side truth for "Blender native selection
// -> DSH draft". It is pure memory: the addon POSTs, the client polls. Entry is
// sanitised so a 4000-object selection cannot blow up the panel or the receipt.

const clock = (start = 1000) => {
  let now = start
  return { now: () => now, advance: (ms: number) => { now += ms } }
}

describe('selection payload sanitising', () => {
  it('keeps the documented shape and drops unknown fields', () => {
    const out = sanitize({
      source: 'blender-addon', sent_at: '12.9', extra: 1,
      selection: {
        count: '2', names: ['Cube', 'Lamp'], active: 'Cube', mode: 'OBJECT', extra: true,
        objects: [{ name: 'Cube', type: 'MESH', dimensions_mm: [100.04, 20.06, 0.01], extra: 1 }],
        collections: [{ name: 'COL_Asset', count: '3' }],
        scene: 'nope',
      },
      scene: { name: 'scene', file: '/tmp/a.blend' },
      activity: { state: 'idle', human_active: true, agent_active: 0 },
      junk: { a: 1 },
    })
    expect(Object.keys(out).sort()).toEqual(['activity', 'scene', 'selection', 'sent_at', 'source', 'v'])
    expect(out.v).toBe(1)
    expect(out.source).toBe('blender-addon')
    expect(out.sent_at).toBe(12)
    expect(out.selection.kind).toBe('objects')
    expect(out.selection.names).toEqual(['Cube', 'Lamp'])
    expect(out.selection.active).toBe('Cube')
    expect(out.selection.mode).toBe('OBJECT')
    expect(out.selection.objects).toEqual([{ name: 'Cube', type: 'MESH', dimensions_mm: [100, 20.1, 0] }])
    expect(out.selection.collections).toEqual([{ name: 'COL_Asset', count: 3 }])
    expect(out.scene).toEqual({ name: 'scene', file: '/tmp/a.blend' })
    expect(out.activity).toEqual({ state: 'idle', human_active: true, agent_active: false })
    expect(out.selection).not.toHaveProperty('extra')
  })

  it('caps strings, names, object details and collection summaries', () => {
    const long = 'x'.repeat(MAX_STRING_CHARS + 40)
    const out = sanitize({
      selection: {
        count: 5000,
        names: Array.from({ length: MAX_SELECTION_NAMES + 10 }, (_v, i) => 'n' + String(i)),
        objects: Array.from({ length: MAX_SELECTION_OBJECTS + 5 }, (_v, i) => ({ name: 'o' + String(i), type: 'MESH' })),
        collections: Array.from({ length: MAX_SELECTION_COLLECTIONS + 5 }, (_v, i) => ({ name: 'c' + String(i), count: i })),
        sub: { type: 'MESH', object: long, indices: { vertices: [1, 2, '3', -4, 1.5] } },
      },
    })
    expect(out.selection.names).toHaveLength(MAX_SELECTION_NAMES)
    expect(out.selection.names.every((n: string) => n.length <= MAX_STRING_CHARS)).toBe(true)
    expect(out.selection.objects).toHaveLength(MAX_SELECTION_OBJECTS)
    expect(out.selection.collections).toHaveLength(MAX_SELECTION_COLLECTIONS)
    expect(out.selection.sub!.object).toHaveLength(MAX_STRING_CHARS)
    expect(out.selection.truncated).toBe(true)
    expect(out.selection.sub!.indices.vertices).toEqual([1, 2, 3])
  })

  it('normalises a missing payload to an empty objects selection', () => {
    const out = sanitize(null)
    expect(out.selection.kind).toBe('objects')
    expect(out.selection.count).toBe(0)
    expect(out.selection.names).toEqual([])
    expect(out.selection.active).toBeNull()
    expect(out.scene).toEqual({ name: null, file: null })
    expect(out.activity).toBeNull()
  })

  it('marks truncated when the declared count exceeds the names we received', () => {
    const out = sanitize({ selection: { count: 9, names: [] } })
    expect(out.selection.truncated).toBe(true)
  })

  it('rounds dimensions to 0.1mm and drops non-finite triples', () => {
    const out = sanitize({
      selection: { objects: [
        { name: 'A', type: 'MESH', dimensions_mm: [1.26, 2.34, 3] },
        { name: 'B', type: 'MESH', dimensions_mm: [1, Number.NaN, 3] },
        { name: 'C', type: 'MESH', dimensions_mm: [1, 2] },
      ] },
    })
    expect(out.selection.objects[0].dimensions_mm).toEqual([1.3, 2.3, 3])
    expect(out.selection.objects[1]).not.toHaveProperty('dimensions_mm')
    expect(out.selection.objects[2]).not.toHaveProperty('dimensions_mm')
  })

  it('keeps collection kind and its summary', () => {
    const out = sanitize({
      selection: { kind: 'collection', collection: 'COL_Engine', count: 57, names: ['Piston', 'Valve', 'Bolt'] },
    })
    expect(out.selection.kind).toBe('collection')
    expect(out.selection.collection).toBe('COL_Engine')
    expect(out.selection.count).toBe(57)
    expect(out.selection.names).toEqual(['Piston', 'Valve', 'Bolt'])
  })

  it('sanitises edit-mode sub selections per type', () => {
    const mesh = sanitize({
      selection: { sub: {
        type: 'MESH', object: 'Cube', mode: 'EDIT', select_mode: { vertex: true, edge: false, face: true },
        counts: { vertices: 8, edges: '12', faces: -1, junk: 3 },
        indices: { vertices: [1, 2, 3], faces: [4, 5] },
      } },
    }).selection.sub!
    expect(mesh).toMatchObject({
      type: 'MESH', object: 'Cube', mode: 'EDIT',
      select_mode: { vertex: true, edge: false, face: true },
      counts: { vertices: 8, edges: 12 },
      truncated: false,
    })
    expect(mesh.indices).toEqual({ vertices: [1, 2, 3], faces: [4, 5] })

    const armature = sanitize({
      selection: { sub: { type: 'ARMATURE', object: 'Rig', bones: ['root', 'spine'] } },
    }).selection.sub!
    expect(armature.type).toBe('ARMATURE')
    expect(armature.bones).toEqual(['root', 'spine'])

    const unknown = sanitize({ selection: { sub: { type: 'LIGHT', object: 'Sun' } } }).selection.sub!
    expect(unknown.type).toBe('OTHER')

    expect(sanitize({ selection: { sub: { type: 'MESH' } } }).selection.sub!).toBeNull()
    expect(sanitize({ selection: { sub: [] } }).selection.sub!).toBeNull()
  })

  it('caps long indices and bones and flags truncation', () => {
    const sub = sanitize({
      selection: { sub: {
        type: 'MESH', object: 'Cube',
        indices: { vertices: Array.from({ length: MAX_SUB_INDICES + 3 }, (_v, i) => i) },
        bones: Array.from({ length: MAX_SUB_BONES + 2 }, (_v, i) => 'b' + String(i)),
      } },
    }).selection.sub!
    expect(sub.indices.vertices).toHaveLength(MAX_SUB_INDICES)
    expect(sub.bones).toHaveLength(MAX_SUB_BONES)
    expect(sub.truncated).toBe(true)
  })
})

describe('selection store sequence and consume cursor', () => {
  it('starts empty and exposes the documented snapshot keys', () => {
    const store = createSelectionStore(clock().now)
    const snap = store.snapshot()
    expect(Object.keys(snap).sort()).toEqual(
      ['activity', 'consumed_seq', 'last_consume', 'ok', 'received_at', 'scene', 'selection', 'seq', 'stale_ms', 'unconsumed'].sort(),
    )
    expect(snap.seq).toBe(0)
    expect(snap.unconsumed).toBe(false)
    expect(snap.received_at).toBeNull()
    expect(snap.stale_ms).toBeNull()
    expect(snap.selection).toBeNull()
  })

  it('increments seq on put and keeps the unconsumed payload readable', () => {
    const c = clock()
    const store = createSelectionStore(c.now)
    c.advance(5)
    expect(store.put({ source: 'blender-addon', selection: { names: ['Cube'], count: 1, active: 'Cube' } }))
      .toEqual({ ok: true, seq: 1, received_at: 1005 })
    const snap = store.snapshot()
    expect(snap.seq).toBe(1)
    expect(snap.unconsumed).toBe(true)
    expect((snap as unknown as { selection: SelectionView }).selection.names).toEqual(['Cube'])
    expect(snap.stale_ms).toBe(0)
    c.advance(STALE_MS + 1)
    expect(store.snapshot().stale_ms).toBe(STALE_MS + 1)
    expect((store.put({ selection: { names: ['Lamp'] } }) as unknown as { seq: number }).seq).toBe(2)
    expect((store.snapshot() as unknown as { selection: SelectionView }).selection.names).toEqual(['Lamp'])
  })

  it('rejects non-object payloads without moving the cursor', () => {
    const store = createSelectionStore(clock().now)
    expect(store.put(null)).toEqual({ ok: false, error: 'invalid_payload' })
    expect(store.put('x')).toEqual({ ok: false, error: 'invalid_payload' })
    expect(store.put([1])).toEqual({ ok: false, error: 'invalid_payload' })
    expect(store.snapshot().seq).toBe(0)
  })

  it('consumes a seq once, idempotently, and records the client result', () => {
    const store = createSelectionStore(clock().now)
    store.put({ selection: { names: ['Cube'] } })
    expect(store.consume(1, { ok: true, how: 'actions' })).toEqual({ ok: true, consumed_seq: 1 })
    expect(store.consume(1, { ok: true, how: 'actions' })).toEqual({ ok: true, consumed_seq: 1 })
    const snap = store.snapshot()
    expect(snap.unconsumed).toBe(false)
    expect(snap.selection).toBeNull()
    expect(snap.last_consume).toMatchObject({ seq: 1, ok: true, how: 'actions' })
  })

  it('ignores invalid consume values, including future seqs and non-integers', () => {
    const store = createSelectionStore(clock().now)
    store.put({ selection: { names: ['Cube'] } })
    for (const bad of [0, -3, 99, 1.5, null, undefined, 'x', Number.NaN]) {
      expect(store.consume(bad)).toEqual({ ok: false, consumed_seq: 0, error: 'invalid_seq' })
    }
    expect(store.consume('1')).toEqual({ ok: true, consumed_seq: 1 })
    expect(store.consume(2).ok).toBe(false)
  })

  it('accepts a numeric string seq and keeps the consumed cursor monotonic', () => {
    const store = createSelectionStore(clock().now)
    store.put({ selection: { names: ['A'] } })
    store.put({ selection: { names: ['B'] } })
    expect(store.consume('2').consumed_seq).toBe(2)
    expect(store.consume(1)).toEqual({ ok: true, consumed_seq: 2 })
    expect(store.consume(2, { ok: false, reason: '没挂上' }).consumed_seq).toBe(2)
    expect(store.snapshot().last_consume).toMatchObject({ seq: 2, ok: false, reason: '没挂上' })
  })

  it('reports the module through the lib-facing path', async () => {
    const mod = await import(pathToFileURL(path.join(pkgPath('lib', 'runtime', 'selectionstore.js'))).href)
    expect(typeof mod.createSelectionStore).toBe('function')
    expect(typeof mod.sanitizeSubSelection).toBe('function')
  })
})
