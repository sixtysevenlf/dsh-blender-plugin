import { afterEach, describe, expect, it, vi } from 'vitest'

// 3.1 narrowed the add-on command surface. A stale model call must get an
// actionable upgrade hint, never a silent alias to a different command.

const h = vi.hoisted(() => ({
  backendChecks: 0,
  backendPostCalls: [] as unknown[][],
}))

vi.mock('../../../src/host/backend/index.js', () => ({
  ensureBackend: async () => {
    h.backendChecks += 1
    return true
  },
}))

vi.mock('../../../src/host/http.js', () => ({
  backendPost: async (...args: unknown[]) => {
    h.backendPostCalls.push(args)
    return { ok: true, result: { pong: true }, ms: 1 }
  },
  leasedText: () => null,
}))

import { registerCmd } from '../../../src/tools/cmd.js'
import { createFakeContext } from '../../helpers/fake-context'

function registered(): { fake: ReturnType<typeof createFakeContext>; tool: any } {
  const fake = createFakeContext()
  registerCmd(fake.ctx, 9877)
  const tool = fake.tools.find((t) => t.name === 'blender_rt_cmd')
  expect(tool).toBeTruthy()
  return { fake, tool }
}

async function run(name: string): Promise<any> {
  const { fake, tool } = registered()
  try {
    return await (tool.execute as any)({ name }, {})
  } finally {
    fake.dispose()
  }
}

afterEach(() => {
  h.backendChecks = 0
  h.backendPostCalls.length = 0
})

describe('blender_rt_cmd removed add-on commands', () => {
  it('turns a deleted write command into an upgrade hint without a backend call', async () => {
    const out: any = await run('set_texture')
    expect(out.envelope.error.code).toBe('command_removed')
    expect(String(out.envelope.error.message)).toContain('set_texture')
    expect(String(out.envelope.error.next_action)).toContain('blender_rt_commands')
    expect(h.backendPostCalls).toHaveLength(0)
  })

  it('keeps live read commands on the normal path', async () => {
    const out: any = await run('get_scene_info')
    expect(out.envelope).toBeUndefined()
    expect(String(out.text)).toContain('CMD get_scene_info ok')
    expect(h.backendPostCalls).toHaveLength(1)
  })
})
