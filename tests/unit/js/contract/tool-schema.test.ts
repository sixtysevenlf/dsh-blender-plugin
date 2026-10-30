import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import * as plugin from '../../../../lib/index.js'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'
import { pkgPath } from '../../../helpers/pkg'

const lock = JSON.parse(fs.readFileSync(pkgPath('tests', 'fixtures', 'capability.lock.json'), 'utf8'))

let tools: CapturedTool[] = []
let fake: ReturnType<typeof createFakeContext>

beforeAll(() => {
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false })
  tools = fake.tools
})

afterAll(() => fake.dispose())

describe('tool schema contract', () => {
  it('registers exactly the 15 frozen model-facing tools', () => {
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
    for (const t of tools) {
      if (t.name === 'blender_viewport') expect(t.isConcurrencySafe).toBeUndefined()
      else {
        const guard = t.isConcurrencySafe
        expect(typeof guard, t.name).toBe('function')
        if (typeof guard === 'function') expect(typeof guard(), t.name).toBe('boolean')
      }
    }
  })
})
