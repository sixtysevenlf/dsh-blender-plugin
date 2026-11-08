import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import * as plugin from '../../../../src/index'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'
import { closedPort } from '../../../helpers/fake-backend.mjs'
import { setPaused } from '../../../../src/host/backend/index'

// Requires every one of the 6 model-facing tools to fail cleanly when the
// Gateway is unreachable. Success/parameter/presenter/lossless cases live in
// tool-execution, tool-presenter and lossless; this file owns the transport
// failure matrix so a single tool cannot silently hang forever.
const CASES: Array<[string, Record<string, unknown>]> = [
  ['blender_session', { action: 'list' }],
  ['blender_op', { action: 'plan', op: 'catalog' }],
  ['blender_py', { code: 'x = 1', see: false }],
  ['blender_see', {}],
  ['blender_run', { action: 'list' }],
  ['blender_inspect', {}],
]

let fake: ReturnType<typeof createFakeContext>
let tools: CapturedTool[]

beforeAll(async () => {
  const port = await closedPort()
  setPaused(true)
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port })
  tools = fake.tools
})

afterAll(() => {
  fake.dispose()
  setPaused(false)
})

describe('model tool unreachable-backend matrix', () => {
  it('covers all 6 model-facing tools', () => {
    expect(CASES.map(([name]) => name).sort()).toEqual(tools.map((t) => t.name).sort())
  })

  it.each(CASES)('%s settles with a readable error', async (name, args) => {
    const tool = tools.find((t) => t.name === name)!
    const started = Date.now()
    const out: any = await (tool.execute as any)(args, {})
    expect(out, name).toBeDefined()
    expect(String(out.text), name).toContain('code:')
    expect(String(out.text), name).toContain('next_action:')
    expect(String(out.text), name).toMatch(/Gateway|网关|连接|不可用/i)
    expect(Date.now() - started).toBeLessThan(5000)
  })
})
