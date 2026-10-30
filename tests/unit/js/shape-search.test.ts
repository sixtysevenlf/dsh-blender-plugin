import { describe, expect, it } from 'vitest'
import {
  PLAN_CATALOG, PLAN_READ_ONLY, catalogPayload, planOpNames,
} from '../../../runtime/engine.js'
import { createPlanDispatch } from '../../../runtime/core/plan-dispatch.js'

// P2 declarative search (plan op shape_search): the only plan op orchestrated
// locally in JS. Route/catalog drift and a broken spec generator both surface
// as an unusable inner loop for the model, so the generated Python is also
// compiled (string assertions alone missed an IndentationError regression).
// The HTTP wiring is covered by the integration route contract.

const VIEW = { from: [0, -6, 1.0], look_at: [0, 0, 0.5], lens: 50, ortho: true }
const APPLY = 'ob.location.y = p["dy_mm"] / 1000.0' + String.fromCharCode(10) + '    bpy.context.view_layer.update()'
const dispatch = createPlanDispatch({} as any)
interface SearchSpec { setup: string; step: string; measure: string }
interface SearchRes {
  ok: boolean
  dry_run?: boolean
  spec_echo: { strategy?: string; combos?: number; iterations?: number }
  spec: SearchSpec
  skeleton: string
  [key: string]: unknown
}
const search = (args: Record<string, unknown>): Promise<SearchRes> =>
  dispatch.planCall('shape_search', Object.assign({ dry_run: true }, args)) as unknown as Promise<SearchRes>

describe('shape_search catalog surface', () => {
  it('is listed, not read-only, and matches the catalog count', () => {
    const names = planOpNames()
    expect(names).toContain('shape_search')
    expect(names.length).toBe(catalogPayload({}).ops_count)
    const shape = PLAN_CATALOG.find((f) => f.f === 'shape')!
    expect(shape.ops).toContain('search')
    expect(PLAN_READ_ONLY.has('shape_search')).toBe(false)
    expect(catalogPayload({ family: 'shape' }).text).toContain('search')
  })
})

describe('shape_search dry-run spec generation', () => {
  it('snaps a small grid space to grid with Python-literal booleans', async () => {
    const res = await search({ objective: 'silhouette_iou', ref: 'D:/ref/side.png', view: VIEW, apply: APPLY,
      params: { dy_mm: { min: -30, max: 30, step: 5 } }, iterations: 200, budget_ms: 30000 })
    expect(res).toMatchObject({ ok: true, dry_run: true })
    expect(res.spec_echo).toMatchObject({ strategy: 'grid', combos: 13, iterations: 13 })
    const spec = res.spec || {}
    expect(typeof spec.setup).toBe('string')
    expect(typeof spec.step).toBe('string')
    expect(typeof spec.measure).toBe('string')
    expect(spec.setup.length).toBeGreaterThan(10)
    expect(spec.step).toContain('_axes')
    expect(spec.measure).toContain('silhouette_iou')
    expect(spec.setup).toContain('def apply(p):')
    expect(spec.measure).toContain('True')
    expect(/[:,\[\s]true[,}\]]/.test(spec.measure)).toBe(false)
  })

  it('chooses random for a big space and uses random.uniform', async () => {
    const res = await search({ objective: 'silhouette_iou', ref: 'D:/ref/side.png', view: VIEW, apply: APPLY,
      params: { a: { min: 0, max: 100 }, b: { min: 0, max: 100 }, c: { min: 0, max: 100 } }, iterations: 5, budget_ms: 5000 })
    expect(res.spec_echo.strategy).toBe('random')
    expect(res.spec.step).toContain('random.uniform')
  })

  it('keeps nested if/else relative indentation', async () => {
    const nested = "if p['dy_mm'] > 0:" + String.fromCharCode(10) + "    ob.location.y = p['dy_mm'] / 1000.0"
      + String.fromCharCode(10) + 'else:' + String.fromCharCode(10)
      + "    ob.location.y = -p['dy_mm'] / 1000.0" + String.fromCharCode(10) + '    bpy.context.view_layer.update()'
    const res = await search({ objective: 'silhouette_iou', ref: 'D:/ref/side.png', view: VIEW, apply: nested,
      params: { dy_mm: { min: -10, max: 10, step: 5 } }, iterations: 10, budget_ms: 5000 })
    expect(res.spec.setup).toContain('    ob.location.y = p')
    expect(res.spec.setup).toContain('        ob.location.y = -p')
  })

  it('supports aabb_err with enum axes and multi-view averaging', async () => {
    const aabb = await search({ objective: 'aabb_err', target: 'GEO-hull', target_size: [1.0, 2.0, 3.0], strategy: 'random',
      apply: APPLY, params: { n: { values: [6, 10, 16] }, k: { min: 0, max: 1, step: 0.5 } }, iterations: 60, budget_ms: 10000 })
    expect(aabb.spec.measure).toContain('aabb_err')
    expect(aabb.spec.measure).toContain('[1, 2, 3]')
    expect(aabb.spec.step).toContain('random.choice')
    expect(aabb.spec.step).toContain('[6, 10, 16]')
    const multi = await search({ objective: 'silhouette_iou', ref: 'D:/ref/side.png',
      views: [VIEW, { from: [6, 0, 1.0], look_at: [0, 0, 0.5], lens: 50 }], apply: APPLY,
      params: { dy_mm: { min: -10, max: 10, step: 10 } }, iterations: 10, budget_ms: 5000 })
    expect(multi.spec.measure).toContain('sum(_ss)')
    expect(multi.spec.measure).toContain('per_view')
  })
})

describe('shape_search validation', () => {
  const cases: Array<[string, Record<string, unknown>]> = [
    ['missing params', { objective: 'silhouette_iou', ref: 'D:/r.png', view: VIEW, apply: APPLY }],
    ['missing ref', { objective: 'silhouette_iou', view: VIEW, apply: APPLY, params: { d: { min: 0, max: 1 } } }],
    ['illegal objective', { objective: 'nope', params: { d: { min: 0, max: 1 } } }],
    ['render objective without apply', { objective: 'silhouette_iou', ref: 'D:/r.png', view: VIEW, params: { d: { min: 0, max: 1 } } }],
    ['grid combinatorics explosion', { objective: 'aabb_err', target: 'X', target_size: [1, 1, 1], apply: APPLY, strategy: 'grid',
      params: { a: { min: 0, max: 100, step: 1 }, b: { min: 0, max: 100, step: 1 }, c: { min: 0, max: 100, step: 1 }, d: { min: 0, max: 100, step: 1 } } }],
  ]
  for (const [label, args] of cases) {
    it('rejects ' + label + ' with ok:false and a copyable skeleton', async () => {
      const res = await search(args)
      expect(res.ok).toBe(false)
      expect(typeof res.skeleton).toBe('string')
      expect(res.skeleton).toContain('shape_search')
    })
  }
})
