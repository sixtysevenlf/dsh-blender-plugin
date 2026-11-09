import { describe, expect, it } from 'vitest'
import { injectModule } from '../../../runtime/core/kernel/api.js'
import { VIEW_PATH } from '../../../runtime/paths.js'

describe('per-instance kernel module injection', () => {
  it('keys the injected cache and routes both probes and loads by instance', async () => {
    const sends: Array<{ opts?: { instanceId?: string; noMark?: boolean } }> = []
    const injected = new Set<string>()
    let n = 0
    const deps = {
      injected,
      addon: {
        async send(_type: string, _params: unknown, _timeout?: number, opts?: any) {
          sends.push({ opts })
          n += 1
          return { result: n % 2 === 1 ? 'HAVE False FP none' : 'VIEW_READY v1' }
        },
      },
    } as any

    await injectModule(deps, VIEW_PATH, 'VIEW_READY', 'VIEW_VERSION', '', 'inst-a')
    expect(injected.has('inst-a|' + VIEW_PATH)).toBe(true)
    expect(sends).toHaveLength(2)
    expect(sends[0].opts).toEqual({ noMark: true, instanceId: 'inst-a' })
    expect(sends[1].opts).toEqual({ noMark: true, instanceId: 'inst-a' })

    await injectModule(deps, VIEW_PATH, 'VIEW_READY', 'VIEW_VERSION', '', 'inst-b')
    expect(injected.has('inst-b|' + VIEW_PATH)).toBe(true)
    expect(sends).toHaveLength(4)
    expect(sends[2].opts).toEqual({ noMark: true, instanceId: 'inst-b' })
  })
})
