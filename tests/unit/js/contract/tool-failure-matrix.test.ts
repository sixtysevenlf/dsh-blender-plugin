import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import * as plugin from '../../../../lib/index.js'
import { createFakeContext, type CapturedTool } from '../../../helpers/fake-context'
import { closedPort } from '../../../helpers/fake-backend.mjs'

// A1 requires every one of the 15 model-facing tools to fail cleanly when the
// backend is unreachable. Success/parameter/presenter/lossless cases live in
// tool-execution, tool-presenter and lossless; this file owns the transport
// failure matrix so a single tool cannot silently hang forever.
const CASES: Array<[string, Record<string, unknown>]> = [
  ['blender_rt_see', { max_size: 320 }],
  ['blender_rt_do', { code: 'x = 1', see: false }],
  ['blender_rt_watch', { code: 'x = 1', seconds: 0.1, fps: 1, max_size: 120 }],
  ['blender_rt_cmd', { name: 'ping' }],
  ['blender_rt_commands', {}],
  ['blender_rt_loop', { op: 'status' }],
  ['blender_rt_perf', { op: 'status' }],
  ['blender_rt_opt', { op: 'join', args: '{"dry_run":true}' }],
  ['blender_rt_headless', { script: 'print(1)' }],
  ['blender_rt_plan', { op: 'catalog' }],
  ['blender_rt_worker', { op: 'start', name: 'w1' }],
  ['blender_rt_txn', { op: 'list' }],
  ['blender_rt_preset', { op: 'list' }],
  ['blender_rt_job', { op: 'list' }],
  ['blender_viewport', { op: 'status' }]
]

let port: number
let fake: ReturnType<typeof createFakeContext>
let tools: CapturedTool[]

beforeAll(async () => {
  port = await closedPort()
  fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port })
  tools = fake.tools
})

afterAll(() => fake.dispose())

describe('model tool unreachable-backend matrix', () => {
  it('covers all 15 model-facing tools', () => {
    expect(CASES.map(([name]) => name).sort()).toEqual(tools.map((t) => t.name).sort())
  })

  it.each(CASES)('%s settles with a readable error', async (name, args) => {
    const tool = tools.find((t) => t.name === name)!
    const started = Date.now()
    let out: unknown
    try {
      out = await Promise.race([
        (tool.execute as any)(args, {}),
        new Promise((_, reject) => setTimeout(() => reject(new Error('tool hung for 5s')), 5000))
      ])
    } catch (error) {
      // A thrown transport error is acceptable only if it names the failure.
      expect(String(error)).toMatch(/backend|连接|connect|ECONNREFUSED|不可用|启动|http \d{3}|frame/i)
      expect(Date.now() - started).toBeLessThan(5000)
      return
    }
    expect(out, name).toBeDefined()
    expect(String(typeof out === 'string' ? out : JSON.stringify(out))).not.toBe('')
    expect(Date.now() - started).toBeLessThan(5000)
  })
})
