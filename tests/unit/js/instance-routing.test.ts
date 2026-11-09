import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { createActApi } from '../../../runtime/core/engine-api/act.js'
import { createViewApi } from '../../../runtime/core/engine-api/view.js'

interface SendCall {
  type: string
  params: unknown
  timeoutMs: number | undefined
  opts: unknown
}

afterEach(() => {
  // no shared state
})

describe('per-instance routing from the engine API', () => {
  it('forwards the caller instance on act and cmd', async () => {
    const calls: SendCall[] = []
    const api = createActApi({
      addon: {
        async send(type: string, params: unknown, timeoutMs?: number, opts?: unknown) {
          calls.push({ type, params, timeoutMs, opts })
          return { result: 'DSH_ACT_OK {"stdout":"ok","stderr":"","epoch":"e1"}' }
        },
      },
    } as any)

    await api.act('print(1)', 1234, null, 'inst-a')
    await api.cmd('scene_stats', {}, 999, 'inst-b')

    expect(calls).toHaveLength(2)
    expect(calls[0]).toMatchObject({
      type: 'execute_code',
      timeoutMs: 1234,
      opts: { instanceId: 'inst-a' },
    })
    expect(calls[1]).toMatchObject({
      type: 'scene_stats',
      timeoutMs: 999,
      opts: { instanceId: 'inst-b' },
    })
  })

  it('ensures the view module and captures on the caller instance', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-view-'))
    const png = path.join(dir, 'frame.png')
    fs.writeFileSync(png, Buffer.from([137, 80, 78, 71]))
    const ensured: Array<string | undefined> = []
    const sends: SendCall[] = []
    const metrics = { views: 0, lastView: null as unknown }
    const api = createViewApi({
      addon: {
        async send(type: string, params: unknown, timeoutMs?: number, opts?: unknown) {
          sends.push({ type, params, timeoutMs, opts })
          return {
            result: 'VIEW ' + JSON.stringify({ ok: true, ms: 1, mode: 'viewport', path: png }),
          }
        },
      },
      ensureView: async (instanceId?: string) => {
        ensured.push(instanceId)
      },
      metrics,
    } as any)

    try {
      const out = await api.view({ width: 32, height: 32 }, 'inst-c')
      expect(out.png.length).toBe(4)
      expect(ensured).toEqual(['inst-c'])
      expect(sends).toHaveLength(1)
      expect(sends[0].opts).toEqual({ instanceId: 'inst-c' })
      expect(metrics.views).toBe(1)
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })
})
