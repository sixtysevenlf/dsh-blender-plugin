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

  it('ensures the view module then captures through the read-only command', async () => {
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
          return { ok: true, ms: 1, mode: 'viewport', path: png }
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
      expect(sends[0].type).toBe('get_viewport_screenshot')
      const spec = (sends[0].params as { spec: Record<string, unknown> }).spec
      expect(spec).toMatchObject({ width: 32, height: 32 })
      expect(typeof spec.path).toBe('string')
      expect(sends[0].opts).toEqual({ instanceId: 'inst-c' })
      expect(metrics.views).toBe(1)
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('still captures during a human pause when execute_code is rejected', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-view-'))
    const png = path.join(dir, 'frame.png')
    fs.writeFileSync(png, Buffer.from([137, 80, 78, 71]))
    const sends: SendCall[] = []
    const api = createViewApi({
      addon: {
        async send(type: string, params: unknown, timeoutMs?: number, opts?: unknown) {
          sends.push({ type, params, timeoutMs, opts })
          return { ok: true, ms: 2, mode: 'camera', path: png }
        },
      },
      ensureView: async () => {
        throw new Error('AI 操作已暂停：请在 Blender N 面板「活动状态」里点「继续 AI 操作」')
      },
      metrics: { views: 0, lastView: null as unknown },
    } as any)

    try {
      const out = await api.view({ mode: 'camera', max_size: 544 }, 'inst-p')
      expect(out.png.length).toBe(4)
      expect(sends).toHaveLength(1)
      expect(sends[0].type).toBe('get_viewport_screenshot')
      expect(sends[0].opts).toEqual({ instanceId: 'inst-p' })
    } finally {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })

  it('reports kernel_not_loaded with the resume hint instead of faking success', async () => {
    const sends: SendCall[] = []
    const api = createViewApi({
      addon: {
        async send(type: string, params: unknown, timeoutMs?: number, opts?: unknown) {
          sends.push({ type, params, timeoutMs, opts })
          return {
            ok: false,
            code: 'kernel_not_loaded',
            error: 'view capture needs the shared kernel; resume AI operations once and retry',
          }
        },
      },
      ensureView: async () => {
        throw new Error('AI 操作已暂停')
      },
      metrics: { views: 0, lastView: null as unknown },
    } as any)

    await expect(api.view({ mode: 'camera' }, 'inst-p')).rejects.toThrow(
      /kernel_not_loaded|恢复 AI 操作/,
    )
    expect(sends).toHaveLength(1)
  })
})
