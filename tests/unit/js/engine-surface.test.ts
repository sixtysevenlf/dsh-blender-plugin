import { afterAll, describe, expect, it } from 'vitest'
import { createEngine } from '../../../runtime/engine.js'

const engine = createEngine() as any
afterAll(() => engine.stop())

describe('engine assembly (T6)', () => {
  it('exposes the frozen public surface', () => {
    expect(Object.keys(engine)).toHaveLength(32)
    expect(engine.addon).toBeTruthy()
    for (const k of ['plan', 'txn', 'preset', 'launchBlender', 'sceneEpoch', 'renderFlag', 'metrics', 'protocol',
      'doctor', 'status', 'act', 'cmd', 'commands', 'loopStart', 'loopStop', 'loopBoard', 'loopExport', 'loopHelp',
      'frameArea', 'perf', 'opt', 'frame', 'view', 'hostSceneItems', 'headless', 'start', 'stop']) {
      expect(typeof engine[k], k).toBe('function')
    }
    expect(Object.keys(engine.worker).sort()).toEqual(['exec', 'list', 'reset', 'restart', 'see', 'snapshot', 'start', 'state', 'status', 'stop', 'watch'])
    expect(Object.keys(engine.job).sort()).toEqual(['collect', 'kill', 'list', 'start', 'status', 'wait'])
  })

  it('serves catalog locally without an addon (34 families / 228 ops)', async () => {
    const c = await engine.plan('catalog', {})
    expect(c.ok).toBe(true)
    expect(c.families).toHaveLength(34)
    expect(c.ops_count).toBe(228)
    expect(c.provenance).toBeTruthy()
  })

  it('reports metrics and render flag shapes', async () => {
    const m = engine.metrics()
    for (const k of ['calls', 'errors', 'timeouts', 'inflight', 'views', 'headlessRuns', 'workerStarts', 'workerExecs']) {
      expect(typeof m[k], k).toBe('number')
    }
    expect(typeof m.lastCmdAgeMs === 'number' || m.lastCmdAgeMs === null).toBe(true)
    const flag = engine.renderFlag()
    expect(flag).toMatchObject({ present: expect.any(Boolean), stale: expect.any(Boolean) })
  })
})
