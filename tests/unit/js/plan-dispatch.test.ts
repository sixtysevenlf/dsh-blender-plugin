import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPlanDispatch, type PlanDispatchDeps } from '../../../runtime/core/plan-dispatch.js'

const { spawnSyncMock } = vi.hoisted(() => ({ spawnSyncMock: vi.fn() }))

vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>()
  return { ...actual, spawnSync: spawnSyncMock }
})

const VIEW = { from: [0, -6, 1], look_at: [0, 0, 0.5], lens: 50 }
const APPLY = 'ob.location.y = p["dy"]'
const loop = (payload: unknown) => ({ result: 'LOOP ' + JSON.stringify(payload) })
const rec = (value: unknown): Record<string, unknown> => value as Record<string, unknown>

function makeDeps(over: Partial<PlanDispatchDeps> = {}) {
  const addonSend = vi.fn(async (..._args: unknown[]) => loop({ ok: true }))
  const deps: PlanDispatchDeps = {
    RENDER_FLAG_WSL: 'D:/flags/render.flag',
    RENDER_STALE_MS: 120000,
    addon: { send: addonSend } as unknown as PlanDispatchDeps['addon'],
    clearRenderFlag: vi.fn(() => true),
    ensurePerf: vi.fn(async () => undefined),
    ensureQc: vi.fn(async () => undefined),
    ensureRenderGuard: vi.fn(async () => undefined),
    ensureRunner: vi.fn(async () => undefined),
    ensureView: vi.fn(async () => undefined),
    loopBoard: vi.fn(async () => ({ best: null, board: [] })),
    loopExport: vi.fn(async () => ({ ok: true })),
    loopStart: vi.fn(async () => ({ ok: true })),
    loopStatus: vi.fn(async () => ({ running: false, i: 0, best: null })),
    loopStop: vi.fn(async () => ({ ok: true })),
    planCallInner: vi.fn(async () => ({ ok: true })),
    readRenderFlag: vi.fn(() => ({ present: false, stale: false, ageMs: null, info: null })),
    ...over,
  }
  return { deps, addonSend, dispatch: createPlanDispatch(deps) }
}

const BASE_SEARCH: Record<string, unknown> = {
  objective: 'silhouette_iou',
  ref: 'D:/ref/side.png',
  view: VIEW,
  apply: APPLY,
  params: { dy: { min: 0, max: 10, step: 5 } },
}

async function dry(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const { dispatch } = makeDeps()
  const merged = Object.assign({ dry_run: true }, BASE_SEARCH, payload)
  return rec(await dispatch.planCall('shape_search', merged))
}

beforeEach(() => {
  vi.clearAllMocks()
  spawnSyncMock.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('planCall local routing', () => {
  it('serves catalog locally without a Blender round-trip', async () => {
    const { deps, addonSend, dispatch } = makeDeps()
    const out = rec(await dispatch.planCall('catalog', { family: 'shape' }))
    expect(out).toMatchObject({ ok: true, op: 'catalog', family: 'shape' })
    expect(String(out.text)).toContain('search')
    expect(deps.planCallInner).not.toHaveBeenCalled()
    expect(addonSend).not.toHaveBeenCalled()
  })

  it('serves render_state from the local flag without touching addon', async () => {
    const { deps, addonSend, dispatch } = makeDeps({
      readRenderFlag: vi.fn(() => ({ present: true, stale: false, ageMs: 42, info: { pid: 7 } })),
    })
    const out = rec(await dispatch.planCall('render_state'))
    expect(out).toMatchObject({
      ok: true,
      busy: true,
      stale: false,
      ageMs: 42,
      flag: { pid: 7 },
      staleMs: 120000,
    })
    expect(String(out.flagPath)).toContain('render.flag')
    expect(deps.planCallInner).not.toHaveBeenCalled()
    expect(addonSend).not.toHaveBeenCalled()
    expect(deps.ensureRenderGuard).not.toHaveBeenCalled()
  })

  it('defaults a missing op to status and delegates it', async () => {
    const { deps, dispatch } = makeDeps()
    vi.mocked(deps.planCallInner).mockResolvedValue({ ok: true, op: 'status' })
    const out = await dispatch.planCall(undefined)
    expect(out).toEqual({ ok: true, op: 'status' })
    expect(deps.planCallInner).toHaveBeenCalledWith('status', undefined)
  })
})

describe('ext check lanes', () => {
  it('maps gltf-validator RAW output into a verdict envelope', async () => {
    spawnSyncMock.mockReturnValue({
      status: 0,
      stdout:
        'RAW ' +
        JSON.stringify({
          issues: {
            numErrors: 0,
            numWarnings: 2,
            numInfos: 1,
            numHints: 0,
            messages: [{ code: 'ACCESSOR', severity: 1, pointer: '/meshes/0', message: 'x' }],
          },
          validatorVersion: '2.0.0',
          info: { drawCallCount: 4, totalVertexCount: 12, totalTriangleCount: 6, maxUVs: 2 },
        }),
      stderr: '',
    })
    const { deps, dispatch } = makeDeps()
    const out = rec(await dispatch.planCall('gltf_validate', { path: 'D:/models/a.glb' }))
    expect(out).toMatchObject({
      ok: true,
      tool: 'gltf-validator',
      errors: 0,
      warnings: 2,
      infos: 1,
      hints: 0,
      validatorVersion: '2.0.0',
    })
    expect(out.messages).toEqual([
      { code: 'ACCESSOR', severity: 1, pointer: '/meshes/0', message: 'x' },
    ])
    expect(String(out.used)).toContain('node')
    expect(rec(out.info)).toMatchObject({ drawCallCount: 4 })
    expect(spawnSyncMock).toHaveBeenCalledTimes(1)
    expect(deps.planCallInner).not.toHaveBeenCalled()
  })

  it('returns an install envelope when no glTF runner answers', async () => {
    spawnSyncMock.mockReturnValue({ status: 1, stdout: '', stderr: 'not found' })
    const { dispatch } = makeDeps()
    const out = rec(await dispatch.planCall('gltf_validate', { path: 'D:/models/a.glb' }))
    expect(out).toMatchObject({ ok: false, installed: false, path: 'D:/models/a.glb' })
    expect((out.tried as unknown[]).length).toBe(3)
    expect(String(out.install)).toContain('npm i gltf-validator')
    expect(String(out.hint)).toContain('runtime')
  })

  it('maps the open3d EXT lane', async () => {
    spawnSyncMock.mockReturnValue({
      status: 0,
      stdout: 'EXT ' + JSON.stringify({ ok: true, vertices: 8, watertight: true }),
      stderr: '',
    })
    const { dispatch } = makeDeps()
    const out = rec(await dispatch.planCall('ext_mesh_check', { path: 'D:/models/a.stl' }))
    expect(out).toMatchObject({ ok: true, tool: 'open3d', vertices: 8, watertight: true })
    expect(String(out.used)).toContain('python')
    expect(spawnSyncMock).toHaveBeenCalledTimes(1)
  })
})

describe('render guard ops', () => {
  it('render_state probe reports the Blender-side payload', async () => {
    const { deps, addonSend, dispatch } = makeDeps({
      readRenderFlag: vi.fn(() => ({ present: false, stale: true, ageMs: 5, info: { x: 1 } })),
    })
    addonSend.mockResolvedValue(loop({ ok: true, flag: { present: false } }))
    const out = rec(await dispatch.planCall('render_state', { probe: true }))
    expect(out).toMatchObject({ ok: true, busy: false, stale: true })
    expect(rec(out.blender)).toEqual({ ok: true, flag: { present: false } })
    expect(deps.ensureRenderGuard).toHaveBeenCalledTimes(1)
    expect(addonSend).toHaveBeenCalledWith(
      'execute_code',
      expect.objectContaining({ code: expect.stringContaining('dsh_render_guard') }),
      120000,
    )
  })

  it('render_state probe failure is non-fatal', async () => {
    const { addonSend, dispatch } = makeDeps()
    addonSend.mockRejectedValue(new Error('socket closed'))
    const out = rec(await dispatch.planCall('render_state', { probe: true }))
    expect(out.ok).toBe(true)
    expect(String(out.probe_error)).toContain('socket closed')
    expect(out.blender).toBeUndefined()
  })

  it('render_wait returns immediately when the flag is idle', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    const { dispatch } = makeDeps()
    const out = rec(await dispatch.planCall('render_wait'))
    expect(out).toMatchObject({ ok: true, idle: true, waitedMs: 0, stale_cleared: false })
  })

  it('render_wait waits for the flag to clear and reports stale_cleared', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    const read = vi
      .fn()
      .mockReturnValueOnce({ present: true, stale: false, ageMs: 10, info: { pid: 1 } })
      .mockReturnValue({ present: true, stale: true, ageMs: 20, info: { pid: 1 } })
    const { dispatch } = makeDeps({ readRenderFlag: read })
    const pending = dispatch.planCall('render_wait')
    await vi.advanceTimersByTimeAsync(400)
    const out = rec(await pending)
    expect(out).toMatchObject({ ok: true, idle: true, waitedMs: 400, stale_cleared: true })
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('render_wait times out and points at the job lane', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    const { dispatch } = makeDeps({
      readRenderFlag: vi.fn(() => ({ present: true, stale: false, ageMs: 1, info: { pid: 2 } })),
    })
    const pending = dispatch.planCall('render_wait', { timeout_ms: 1000 })
    await vi.advanceTimersByTimeAsync(1200)
    const out = rec(await pending)
    expect(out).toMatchObject({ ok: false, idle: false, timeout: true })
    expect(String(out.hint)).toContain('blender_run')
  })

  it('render_reset clears the local flag and reports the Blender clear', async () => {
    const { deps, addonSend, dispatch } = makeDeps({
      readRenderFlag: vi.fn(() => ({ present: true, stale: false, ageMs: 3, info: { pid: 3 } })),
    })
    addonSend.mockResolvedValue(loop({ ok: true, cleared: 1 }))
    const out = rec(await dispatch.planCall('render_reset', { why: 'crashed' }))
    expect(out).toMatchObject({ ok: true, cleared: true, previous: { pid: 3 } })
    expect(rec(out.blender)).toEqual({ ok: true, cleared: 1 })
    expect(deps.clearRenderFlag).toHaveBeenCalledTimes(1)
  })

  it('render_reset tolerates a Blender-side clear failure', async () => {
    const { addonSend, dispatch } = makeDeps()
    addonSend.mockRejectedValue(new Error('gone'))
    const out = rec(await dispatch.planCall('render_reset'))
    expect(out.ok).toBe(true)
    const blender = rec(out.blender)
    expect(blender.ok).toBe(false)
    expect(String(blender.error)).toContain('gone')
  })

  it('render_guard_install and render_guard_selftest delegate to Blender', async () => {
    const { addonSend, dispatch } = makeDeps()
    addonSend
      .mockResolvedValueOnce(loop({ ok: true, installed: true }))
      .mockResolvedValueOnce(loop({ ok: true, checks: 3 }))
    const install = rec(await dispatch.planCall('render_guard_install', { force: true }))
    expect(install).toMatchObject({ ok: true, install: { ok: true, installed: true } })
    const installArgs = addonSend.mock.calls[0][1] as unknown as { code: string }
    expect(installArgs.code).toContain('force')
    const self = rec(await dispatch.planCall('render_guard_selftest'))
    expect(self).toEqual({ ok: true, checks: 3 })
    expect(addonSend).toHaveBeenCalledTimes(2)
  })
})

describe('vision ops', () => {
  it('routes all 8 vision ops to the injected dispatcher without a Blender round-trip', async () => {
    const visionOp = vi.fn(async (op: string) => ({ ok: true, op }))
    const { deps, addonSend, dispatch } = makeDeps({ visionOp })
    for (const op of [
      'vision_status',
      'vision_analyze',
      'vision_compare',
      'vision_detect',
      'vision_track',
      'vision_pass',
      'vision_selftest',
      'vision_help',
    ]) {
      const out = rec(await dispatch.planCall(op, { probe: true }))
      expect(out).toEqual({ ok: true, op })
    }
    expect(visionOp).toHaveBeenCalledTimes(8)
    expect(deps.planCallInner).not.toHaveBeenCalled()
    expect(addonSend).not.toHaveBeenCalled()
  })

  it('returns a structured vision_unavailable when no dispatcher is wired', async () => {
    const { deps, addonSend, dispatch } = makeDeps()
    const out = rec(await dispatch.planCall('vision_analyze', { path: 'D:/shots/a.png' }))
    expect(out).toMatchObject({
      ok: false,
      op: 'vision_analyze',
      stage: 'vision',
      code: 'vision_unavailable',
      retryable: true,
    })
    expect(String(out.fix)).toContain('Gateway')
    expect(rec(out.fallback)).toMatchObject({ kind: 'unavailable' })
    expect(deps.planCallInner).not.toHaveBeenCalled()
    expect(addonSend).not.toHaveBeenCalled()
  })

  it('leaves unknown vision_-prefixed names on the normal addon path', async () => {
    const { deps, addonSend, dispatch } = makeDeps()
    vi.mocked(deps.planCallInner).mockResolvedValue({ ok: false, error: 'unknown op: vision_nope' })
    const out = rec(await dispatch.planCall('vision_nope'))
    expect(deps.planCallInner).toHaveBeenCalledWith('vision_nope', undefined)
    expect(addonSend).not.toHaveBeenCalled()
    expect(out.ok).toBe(false)
  })
})

describe('perf delegation', () => {
  it('sends a kernel perf dispatch and parses the LOOP marker', async () => {
    const { deps, addonSend, dispatch } = makeDeps()
    addonSend.mockResolvedValue(loop({ ok: true, ops: 2 }))
    const out = await dispatch.perfCall('summary', { a: 1 })
    expect(out).toEqual({ ok: true, ops: 2 })
    expect(deps.ensurePerf).toHaveBeenCalledTimes(1)
    expect(addonSend).toHaveBeenCalledTimes(1)
    const [cmd, args, timeout] = addonSend.mock.calls[0] as unknown as [
      string,
      { code: string },
      number,
    ]
    expect(cmd).toBe('execute_code')
    expect(timeout).toBe(300000)
    expect(args.code).toContain('dsh_perf_api')
    expect(args.code).toContain('"summary"')
    expect(args.code).toContain('a')
  })
})

describe('unknown op discovery', () => {
  it('adds did_you_mean and a skeleton hint to planCallInner errors', async () => {
    const { deps, dispatch } = makeDeps()
    vi.mocked(deps.planCallInner).mockResolvedValue({ ok: false, error: 'unknown op: shape_searc' })
    const out = rec(await dispatch.planCall('shape_searc'))
    expect(rec(out.did_you_mean)).toMatchObject({ op: 'shape_search' })
    expect(String(out.hint)).toContain('did_you_mean')
    expect(out.hint_shown).toBe(true)
    expect(deps.planCallInner).toHaveBeenCalledWith('shape_searc', undefined)
  })

  it('preserves string envelopes and non-unknown errors', async () => {
    const { dispatch } = makeDeps()
    const enhanced = dispatch.enhancePlanResult(
      JSON.stringify({ ok: false, error: 'not found: shape_searc' }),
      'shape_searc',
    )
    expect(typeof enhanced).toBe('string')
    const parsed = rec(JSON.parse(enhanced as string))
    expect(rec(parsed.did_you_mean)).toMatchObject({ op: 'shape_search' })
    const untouched = { ok: false, error: 'params bad' }
    expect(dispatch.enhancePlanResult(untouched, 'shape_searc')).toBe(untouched)
    expect(dispatch.enhancePlanResult({ ok: true }, 'shape_searc')).toEqual({ ok: true })
    expect(dispatch.enhancePlanResult('plain text', 'shape_searc')).toBe('plain text')
  })
})

describe('shape_search dry-run spec generation', () => {
  it('generates a grid spec from a range axis and snaps the upper bound', async () => {
    const out = await dry({ params: { dy: { min: 0, max: 10, step: 3 } } })
    expect(out).toMatchObject({ ok: true, dry_run: true })
    expect(rec(out.spec_echo)).toMatchObject({
      objective: 'silhouette_iou',
      strategy: 'grid',
      combos: 5,
      iterations: 5,
      param_names: ['dy'],
      views: 1,
      ref: 'D:/ref/side.png',
    })
    const spec = rec(out.spec)
    expect(String(spec.step)).toContain('[0, 3, 6, 9, 10]')
    expect(String(spec.setup)).toContain('def apply(p):')
    expect(out.warnings).toEqual([])
  })

  it('caps enum values at 500 and range expansions at 600', async () => {
    const enumOut = await dry({
      params: { n: { values: Array.from({ length: 700 }, (_, i) => i) } },
      strategy: 'grid',
      iterations: 600,
    })
    expect(rec(enumOut.spec_echo)).toMatchObject({ combos: 500, iterations: 500 })
    const rangeOut = await dry({ params: { x: { min: 0, max: 999, step: 1 } }, iterations: 200 })
    expect(rec(rangeOut.spec_echo)).toMatchObject({ strategy: 'random', combos: 600 })
  })

  it('rounds integer axes and emits Python literals', async () => {
    const out = await dry({
      params: { i: { min: 0, max: 1, step: 0.3, integer: true } },
      strategy: 'grid',
    })
    expect(String(rec(out.spec).step)).toContain('[0, 0, 1, 1]')
  })

  it('supports aabb_err enums, warnings, and multi-view averaging', async () => {
    const aabb = await dry({
      objective: 'aabb_err',
      target: 'GEO-hull',
      target_size: [1, 2, 3],
      apply: undefined,
      params: { n: { values: [6, 10, 16] } },
      iterations: 10,
      budget_ms: 5000,
    })
    expect(String(rec(aabb.spec).measure)).toContain('aabb_err')
    expect(String(rec(aabb.spec).step)).toContain('[6, 10, 16]')
    expect((aabb.warnings as string[])[0]).toContain('没有给 apply')
    const multi = await dry({
      views: [VIEW, { from: [6, 0, 1], look_at: [0, 0, 0.5], lens: 50 }],
      iterations: 10,
    })
    expect(String(rec(multi.spec).measure)).toContain('sum(_ss)')
    expect(rec(multi.spec_echo).views).toBe(2)
  })

  it('validates input with copyable envelopes', async () => {
    const cases: Array<[string, Record<string, unknown>, string]> = [
      ['invalid objective', { objective: 'nope' }, 'objective 只支持'],
      ['missing params', { params: undefined }, 'params 必填'],
      ['empty params', { params: {} }, 'params 必填'],
      ['array params', { params: [] }, 'params 必填'],
      ['invalid range', { params: { d: { min: 2, max: 1 } } }, '参数区间有问题'],
      ['empty enum', { params: { d: { values: [] } } }, '参数区间有问题'],
      ['bad strategy', { params: { d: { min: 0, max: 1 } }, strategy: 'brute' }, 'strategy 只支持'],
      ['missing view', { view: undefined }, '需要 view 或 views'],
      ['missing ref', { ref: undefined }, '需要 ref'],
      ['missing apply for render objective', { apply: undefined }, '渲染类目标需要 apply'],
      ['aabb missing target', { objective: 'aabb_err', target_size: [1, 1, 1] }, '需要 target'],
      [
        'aabb bad target_size',
        { objective: 'aabb_err', target: 'X', target_size: [1, 2] },
        '需要 target_size',
      ],
      [
        'grid explosion',
        {
          objective: 'aabb_err',
          target: 'X',
          target_size: [1, 1, 1],
          strategy: 'grid',
          params: {
            a: { min: 0, max: 100, step: 1 },
            b: { min: 0, max: 100, step: 1 },
            c: { min: 0, max: 100, step: 1 },
            d: { min: 0, max: 100, step: 1 },
          },
        },
        '太大',
      ],
    ]
    for (const [label, payload, fragment] of cases) {
      const out = await dry(payload)
      expect(out.ok, label).toBe(false)
      expect(out.stage, label).toBe('validate')
      expect(String(out.skeleton), label).toContain('shape_search')
      expect(String(out.error), label).toContain(fragment)
    }
  })
})

describe('shape_search loop delegation', () => {
  it('runs the inner loop, exports, and applies the best params', async () => {
    const { deps, addonSend, dispatch } = makeDeps()
    vi.mocked(deps.loopStart).mockResolvedValue({ ok: true })
    vi.mocked(deps.loopStatus).mockResolvedValue({
      running: false,
      i: 7,
      tps: 3.5,
      elapsed_ms: 1234,
      stop_reason: 'budget',
      best: { params: { dy: 5 }, score: 0.9 },
    })
    vi.mocked(deps.loopBoard).mockResolvedValue({
      board: [{ params: { dy: 5 }, score: 0.9 }],
      best: null,
    })
    vi.mocked(deps.loopExport).mockResolvedValue({ ok: true, path: 'D:/out.py' })
    addonSend.mockResolvedValue({ result: 'SEARCH_APPLIED {"ok":true,"params":{"dy":5}}' })
    const out = rec(
      await dispatch.planCall('shape_search', {
        ...BASE_SEARCH,
        params: { dy: { min: 0, max: 10, step: 5 } },
        iterations: 3,
        export: 'D:/out.py',
      }),
    )
    expect(out).toMatchObject({
      ok: true,
      op: 'shape_search',
      iterations_done: 7,
      tps: 3.5,
      elapsed_ms: 1234,
      stop_reason: 'budget',
      timed_out: false,
    })
    expect(out.best).toEqual({ params: { dy: 5 }, score: 0.9 })
    expect(out.board).toEqual([{ params: { dy: 5 }, score: 0.9 }])
    expect(out.applied).toMatchObject({ ok: true, params: { dy: 5 } })
    expect(out.exported).toMatchObject({ ok: true, path: 'D:/out.py' })
    expect(deps.ensureRunner).toHaveBeenCalledTimes(1)
    expect(deps.ensureQc).toHaveBeenCalledTimes(1)
    expect(deps.ensureView).toHaveBeenCalledTimes(1)
    expect(deps.loopStart).toHaveBeenCalledTimes(1)
    const spec = vi.mocked(deps.loopStart).mock.calls[0][0] as Record<string, unknown>
    expect(spec).toMatchObject({
      iterations: 3,
      budget_ms: 30000,
      minimize: true,
      top_k: 10,
      patience: 0,
      redraw_every: 0,
    })
    expect(String(spec.setup)).toContain('def apply(p):')
    expect(deps.loopStatus).toHaveBeenCalledWith(8)
    expect(deps.loopBoard).toHaveBeenCalledWith(10, false)
    expect(deps.loopExport).toHaveBeenCalledWith(
      expect.objectContaining({ path: 'D:/out.py', top: 1, includeVariants: false }),
    )
    expect(addonSend).toHaveBeenCalledTimes(1)
    expect(addonSend).toHaveBeenCalledWith(
      'execute_code',
      expect.objectContaining({ code: expect.stringContaining('SEARCH_APPLIED') }),
      60000,
    )
  })

  it('restore:"none" keeps the found best without a write-back', async () => {
    const { addonSend, dispatch } = makeDeps({
      loopStatus: vi.fn(async () => ({ running: false, best: { params: { dy: 5 } } })),
    })
    addonSend.mockClear()
    const out = rec(
      await dispatch.planCall('shape_search', {
        ...BASE_SEARCH,
        params: { dy: { min: 0, max: 10, step: 5 } },
        restore: 'none',
      }),
    )
    expect(out.ok).toBe(true)
    expect(rec(out.applied)).toMatchObject({ ok: false, skipped: true })
    expect(addonSend).not.toHaveBeenCalled()
  })

  it('reports preload and start failures as stage envelopes', async () => {
    const preload = makeDeps()
    vi.mocked(preload.deps.ensureRunner).mockRejectedValue(new Error('runner missing'))
    const preloadOut = rec(await preload.dispatch.planCall('shape_search', BASE_SEARCH))
    expect(preloadOut).toMatchObject({ ok: false, stage: 'preload' })
    expect(String(preloadOut.error)).toContain('runner missing')
    expect(preload.deps.loopStart).not.toHaveBeenCalled()
    expect(preload.addonSend).not.toHaveBeenCalled()

    const start = makeDeps()
    vi.mocked(start.deps.loopStart).mockResolvedValue({ ok: false, error: 'loop already running' })
    const startOut = rec(await start.dispatch.planCall('shape_search', BASE_SEARCH))
    expect(startOut).toMatchObject({ ok: false, stage: 'start' })
    expect(String(startOut.error)).toContain('loop already running')
    expect(start.deps.loopStatus).not.toHaveBeenCalled()
  })
})
