import { describe, expect, it } from 'vitest'
import {
  buildEnginePrelude,
  buildHeadlessPreload,
} from '../../../runtime/execution/headless/options/gpu.js'

// split headless options into phases; these direct assertions pin the
// engine/GPU prelude and the preload module assembly without spawning Blender.

const deps = {
  preloadChunk: (name: string) => '# preload ' + name,
} as any

describe('buildEnginePrelude', () => {
  it('defaults to eevee with the GPU prelude', () => {
    const out = buildEnginePrelude({} as any)
    expect(out.engineMode).toBe('eevee')
    expect(out.gpuOff).toBe(false)
    expect(out.gpuManual).toBe(false)
    expect(out.gpuPre).toContain('DSH engine prelude (eevee)')
    expect(out.gpuPre).not.toContain('__DSH_ENGINE__')
    expect(out.gpuPre).not.toContain('__DSH_GPU_MANUAL__')
    expect(out.gpuPre).not.toContain('__DSH_GPU_MANUAL__')
  })

  it('selects cycles and marks manual GPU', () => {
    const out = buildEnginePrelude({ engine: 'CYCLES', gpu: 'required' } as any)
    expect(out.engineMode).toBe('cycles')
    expect(out.gpuManual).toBe(true)
    expect(out.gpuPre).toContain('DSH engine prelude (cycles)')
    expect(out.gpuPre).toContain('True')
  })

  it.each(['keep', 'off', 'none'])('turns the prelude off for engine=%s', (engine) => {
    const out = buildEnginePrelude({ engine } as any)
    expect(out.gpuOff).toBe(true)
    expect(out.gpuPre).toBe('')
  })

  it.each(['false', 'off', '0'])('honours the legacy gpu=%s switch', (gpu) => {
    const out = buildEnginePrelude({ gpu } as any)
    expect(out.gpuOff).toBe(true)
    expect(out.gpuPre).toBe('')
  })
})

describe('buildHeadlessPreload', () => {
  it('normalizes an array, a comma string and an empty preload', () => {
    expect(buildHeadlessPreload({ preload: ['view', 'perf'] } as any, deps).mods).toEqual([
      'view',
      'perf',
    ])
    const comma = buildHeadlessPreload({ preload: 'view.py, perf' } as any, deps)
    // mods keeps the raw split values (legacy behavior); normalization only
    // happens while rendering the preload chunk.
    expect(comma.mods).toEqual(['view.py', ' perf'])
    expect(comma.pre).toBe('# preload view\n# preload perf\n')
    expect(buildHeadlessPreload({} as any, deps).mods).toEqual([])
  })

  it('forces qc_render when shots carry views, exactly once', () => {
    const out = buildHeadlessPreload(
      { preload: ['qc_render'], shots: [{ name: 'front' }] } as any,
      deps,
    )
    expect(out.shotsWanted).toBe(true)
    expect(out.shotsSpec).toEqual({ views: [{ name: 'front' }] })
    expect(out.mods.filter((m) => m === 'qc_render')).toHaveLength(1)
    expect(out.pre).toContain('# preload qc_render')

    const noShots = buildHeadlessPreload({ shots: { views: [] } } as any, deps)
    expect(noShots.shotsWanted).toBe(false)
    expect(noShots.mods).toEqual([])
  })

  it('rejects a preload module outside the runtime/py manifest', () => {
    expect(() => buildHeadlessPreload({ preload: ['no_such_module_xyz'] } as any, deps)).toThrow(
      /未知的 runtime Python 模块：no_such_module_xyz/,
    )
  })
})
