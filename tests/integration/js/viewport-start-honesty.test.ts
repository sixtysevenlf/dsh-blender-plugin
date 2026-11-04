/**
 * P1-1 集成回归（PR #75 review）：op=start 探 /status 的 host 预算必须大于 backend /status 的实际耗时。
 *
 * 修前 VIEWPORT_START_STATUS_TIMEOUT_MS=6000，而半开 addon 下 /status 稳态 ~6.0s、
 * 后端刚起首调 8.98–9.43s → execute.ts 走 catch，只输出「addon/bpy 未响应：This operation was aborted」，
 * status.addonError / 结构化诊断全部丢失。修后预算 12s，应拿到 addonError 文本而不是 host abort。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { startBackend } from '../../helpers/backend'
import { startFakeAddon } from '../../helpers/fake-addon.mjs'
import { executeViewport } from '../../../src/tools/viewport/execute.js'

const closers: Array<() => Promise<void>> = []

afterEach(async () => {
  while (closers.length) {
    const close = closers.pop()!
    try {
      await close()
    } catch {
      /* best effort teardown */
    }
  }
})

/** TCP accept 后对任何请求都不回写：模拟 addon 线程卡死 / Blender 主线程被占。 */
function halfOpenBehavior(): Promise<never> {
  return new Promise<never>(() => {})
}

describe('op=start: /status probe must outlive the backend addon budget', () => {
  it('returns the addonError diagnosis instead of a host-side abort', async () => {
    const addon = await startFakeAddon({ behavior: halfOpenBehavior })
    closers.push(() => addon.close())
    const backend = await startBackend({ addonPort: addon.port })
    closers.push(() => backend.stop())

    const t0 = Date.now()
    const out = await executeViewport({ op: 'start' }, backend.port)
    const elapsedMs = Date.now() - t0
    console.log('[viewport-start] execute(op=start) = ' + String(elapsedMs) + 'ms: ' + out)

    expect(elapsedMs).toBeLessThan(15000)
    expect(out).toContain('addon/bpy 未响应')
    expect(out).toContain('timeout')
    expect(out).not.toMatch(/aborted/i)
    expect(out).toContain('DSH Blender Bridge')
  }, 60000)
})
