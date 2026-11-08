import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import * as plugin from '../../../../src/index'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'
import { pkgPath } from '../../../helpers/pkg'

const lock = JSON.parse(
  fs.readFileSync(pkgPath('tests', 'fixtures', 'capability.lock.json'), 'utf8'),
)

let tools: CapturedTool[] = []
let fake: ReturnType<typeof createFakeContext>

beforeAll(() => {
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port: 9897 })
  tools = fake.tools
})

afterAll(() => fake.dispose())

describe('tool schema contract', () => {
  it('registers exactly the 6 frozen model-facing tools', () => {
    expect(tools).toHaveLength(lock.tools_count)
    expect(tools.map((t) => t.name).sort()).toEqual([...lock.tools].sort())
    expect(new Set(tools.map((t) => t.name)).size).toBe(tools.length)
  })

  it('gives every tool a description, object schema and timeout', () => {
    for (const t of tools) {
      expect(typeof t.name, t.name).toBe('string')
      expect((t.description || '').length, t.name).toBeGreaterThan(20)
      expect(t.parameters, t.name).toBeTypeOf('object')
      expect(t.timeoutMs, t.name).toBeTypeOf('number')
      expect(t.timeoutMs, t.name).toBeGreaterThan(0)
      expect(t.execute, t.name).toBeTypeOf('function')
    }
  })

  it('stays within the prompt-size budget', () => {
    const total = tools.reduce((n, t) => n + (t.description || '').length, 0)
    expect(total).toBeLessThan(12_000)
    for (const t of tools) expect((t.description || '').length, t.name).toBeLessThan(1_600)
  })

  it('marks concurrency only where the tool authority allows it', () => {
    const READ_ONLY = new Set(['blender_session', 'blender_see', 'blender_inspect'])
    // The published signature is zero-argument; the registry wrapper (and the
    // host dispatch) pass the validated argument object, so probe it that way.
    const args: Record<string, unknown> = {
      blender_session: { action: 'list' },
      blender_see: {},
      blender_inspect: {},
    }
    for (const t of tools) {
      const guard = t.isConcurrencySafe as unknown as ((a?: unknown) => boolean) | undefined
      expect(typeof guard, t.name).toBe('function')
      if (typeof guard === 'function') {
        expect(guard(args[t.name] ?? {}), t.name).toBe(READ_ONLY.has(t.name))
      }
    }
  })
})
