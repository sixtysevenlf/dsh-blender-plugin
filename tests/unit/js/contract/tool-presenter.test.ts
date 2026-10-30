import { afterAll, beforeAll, describe, expect, it } from 'vitest'
// @ts-expect-error - built plugin entry has no declaration consumed here
import * as plugin from '../../../../lib/index.js'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'

let fake: ReturnType<typeof createFakeContext>
let tools: CapturedTool[]

beforeAll(() => {
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port: 9899 })
  tools = fake.tools
})

afterAll(() => fake.dispose())

describe('tool presenters', () => {
  it('attaches presentCall and presentResult to all 15 tools', () => {
    expect(tools).toHaveLength(15)
    for (const t of tools) {
      expect(typeof t.presentCall, t.name).toBe('function')
      expect(typeof t.presentResult, t.name).toBe('function')
    }
  })

  it('keeps card specs aligned with the registered tool set', () => {
    expect(Object.keys(plugin.__internals.cards.specs).sort()).toEqual(tools.map((t) => t.name).sort())
  })

  it('preserves the model-facing render channel', () => {
    for (const t of tools) {
      expect(typeof (t.output as any)?.render, t.name).toBe('function')
      const value = t.name === 'blender_viewport' ? 'hello' : { text: 'hello', envelope: { ok: true } }
      const block = (t.output as any).render({}, value)
      expect(Array.isArray(block), t.name).toBe(true)
      expect(block.every((b: any) => typeof b.text === 'string'), t.name).toBe(true)
      expect(block.map((b: any) => b.text).join('\n'), t.name).toContain('hello')
    }
  })

  it('produces lossless card views for odd arguments', () => {
    for (const t of tools) {
      const call = (t.presentCall as any)({})
      const result = (t.presentResult as any)({}, {
        content: [{ type: 'text', text: JSON.stringify({ ok: true }) }, { type: 'text', text: 'x' }],
        isError: false,
      })
      for (const view of [call, result]) {
        if (view === undefined) continue
        expect(JSON.parse(JSON.stringify(view)), t.name).toEqual(view)
      }
    }
  })

  it('renders a readable viewport call card', () => {
    const viewport = tools.find((t) => t.name === 'blender_viewport')!
    const card = (viewport.presentCall as any)({ op: 'doctor' })
    expect(card.title).toBe('Blender 通道 · doctor')
  })
})
