import { describe, expect, it } from 'vitest'
import { AUDIT_PATH, pyModuleFile, pyModuleNames } from '../../../runtime/paths.js'
import { injectModule, preloadChunk } from '../../../runtime/core/kernel/api.js'

describe('kernel module injection', () => {
  it('gives preloaded modules their real __file__ path', () => {
    const code = preloadChunk('audit')
    expect(code).toContain('"__file__": ' + JSON.stringify(AUDIT_PATH))
    expect(pyModuleFile('audit')).toBe(AUDIT_PATH)
    expect(code).toContain('"__name__": "audit"')
  })

  it('gives every registered preload module its real __file__', () => {
    for (const name of pyModuleNames()) {
      const code = preloadChunk(name)
      expect(code).toContain('"__file__": ' + JSON.stringify(pyModuleFile(name)))
    }
  })

  it('sets __file__ before injecting a module into the kernel globals', async () => {
    const calls: string[] = []
    const deps = {
      injected: new Set<string>(),
      addon: {
        send: async (_command: string, args: { code: string }) => {
          calls.push(args.code)
          return { result: calls.length === 1 ? 'HAVE False FP none' : 'AUDIT_READY' }
        },
      },
    } as unknown as Parameters<typeof injectModule>[0]

    await injectModule(deps, AUDIT_PATH, 'AUDIT_READY', 'AUDIT_VERSION')

    expect(calls).toHaveLength(2)
    const injected = calls[1]
    const fileLine = injected.indexOf('__file__ = ' + JSON.stringify(AUDIT_PATH))
    expect(fileLine).toBeGreaterThanOrEqual(0)
    expect(fileLine).toBeLessThan(injected.indexOf('"""网格体检 public facade'))
    expect(injected).toContain('AUDIT_READY')
  })
})
