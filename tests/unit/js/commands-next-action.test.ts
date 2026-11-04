import { afterEach, describe, expect, it, vi } from 'vitest'

// P1 regression: a "socket closed" failure from /commands used to tell the model
// to run blender_viewport op=status, which itself blocks 20s and then points at
// op=start, forming a three-hop loop. The next_action must now be a bounded,
// executable step (reconnect the addon / op=doctor) instead.

const h = vi.hoisted(() => ({
  response: null as Record<string, unknown> | null,
  backendChecks: 0,
  backendJsonCalls: [] as unknown[][],
}))

vi.mock('../../../src/host/backend/index.js', () => ({
  ensureBackend: async () => {
    h.backendChecks += 1
    return true
  },
}))

vi.mock('../../../src/host/http.js', () => ({
  backendJson: async (...args: unknown[]) => {
    h.backendJsonCalls.push(args)
    return h.response
  },
}))

import { registerCommands } from '../../../src/tools/commands.js'
import { createFakeContext } from '../../helpers/fake-context'

function registered(): { fake: ReturnType<typeof createFakeContext>; tool: any } {
  const fake = createFakeContext()
  registerCommands(fake.ctx, 9877)
  const tool = fake.tools.find((t) => t.name === 'blender_rt_commands')
  expect(tool).toBeTruthy()
  return { fake, tool }
}

async function run(response: Record<string, unknown>): Promise<any> {
  h.response = response
  const { fake, tool } = registered()
  try {
    return await (tool.execute as any)({}, {})
  } finally {
    fake.dispose()
  }
}

afterEach(() => {
  h.response = null
  h.backendChecks = 0
  h.backendJsonCalls.length = 0
})

describe('blender_rt_commands next_action', () => {
  it('does not point a socket-closed failure at the unbounded status probe', async () => {
    const out: any = await run({ ok: false, error: 'socket closed' })
    const next = String(out.envelope.error.next_action)
    expect(next).toContain('doctor')
    expect(next).toMatch(/Connect/i)
    expect(next).not.toMatch(/blender_viewport op=status/)
    expect(String(out.text)).toContain('next_action:')
    // bounded diagnosed branch has its own code, not the generic backend_error
    expect(out.envelope.error.code).toBe('addon_unreachable')
  })

  it('treats other socket-level transport errors as the same bounded reconnect branch', async () => {
    const out: any = await run({ ok: false, error: 'connect ECONNREFUSED 127.0.0.1:9876' })
    expect(out.envelope.error.code).toBe('addon_unreachable')
    const next = String(out.envelope.error.next_action)
    expect(next).toContain('doctor')
    expect(next).toMatch(/Connect/i)
    expect(next).not.toMatch(/blender_viewport op=status/)
  })

  it('gives non-socket failures a bounded doctor suggestion too', async () => {
    const out: any = await run({ ok: false, error: 'addon boom' })
    expect(out.envelope.error.code).toBe('backend_error')
    const next = String(out.envelope.error.next_action)
    expect(next).toContain('doctor')
    expect(next).not.toMatch(/blender_viewport op=status/)
  })

  it('keeps the success rendering unchanged', async () => {
    const out: any = await run({
      ok: true,
      scene: 'Scene',
      total: 19,
      disabled: [],
      integrations: {},
      available: ['ping'],
    })
    expect(String(out.text)).toContain('可用命令 19 条')
    expect(String(out.text)).toContain('可用：ping')
  })

  it('keeps the /commands client budget below the host static deadline', async () => {
    await run({ ok: false, error: 'addon boom' })

    const call = h.backendJsonCalls[0]
    expect(call[1]).toBe('/commands')
    const timeoutMs = Number(call[2])
    expect(timeoutMs).toBeGreaterThan(0)
    // 宿主 timeoutMs:T.commands = 60000；内层预算必须更小，先于宿主截止收口。
    expect(timeoutMs).toBeLessThan(60000)
  })
})
