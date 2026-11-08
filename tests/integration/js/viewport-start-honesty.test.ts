/**
 * 集成回归（PR #75 review，3.1 迁移）：blender_session action=status 的 host 预算必须大于
 * backend /status 的实际耗时。半开 addon 下 backend /status 稳态 ~6s / 首调 ~9.4s，
 * host 必须拿到 addonError 文本，而不是自己先 abort 把诊断丢掉。
 */
import { afterEach, describe, expect, it } from 'vitest'
import * as plugin from '../../../src/index'
import { createFakeContext } from '../../helpers/fake-context'
import { startBackend } from '../../helpers/backend'
import { startFakeWsAddon } from '../../helpers/fake-ws-addon.mjs'

const closers: Array<() => Promise<void>> = []
const EXEC = { agent: { id: 'main', session: { id: 'it-session' } } }

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

/** 握手后对任何请求都不回写：模拟 addon 线程卡死 / Blender 主线程被占。 */
function halfOpenBehavior() {
  return { action: 'hang' }
}

describe('blender_session status: host budget must outlive the backend addon budget', () => {
  it('returns the addonError diagnosis instead of a host-side abort', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({
      gatewayPort: backend.port,
      behavior: halfOpenBehavior,
    })
    closers.push(() => addon.close())
    await addon.ready

    const fake = createFakeContext()
    plugin.apply(fake.ctx, { autoStart: false, tui: false, port: backend.port })
    try {
      const tool = fake.tools.find((t) => t.name === 'blender_session')!
      const t0 = Date.now()
      const out: any = await (tool.execute as any)({ action: 'status' }, EXEC)
      const elapsedMs = Date.now() - t0
      console.log(
        '[session-status] execute(action=status) = ' + String(elapsedMs) + 'ms: ' + out.text,
      )

      expect(elapsedMs).toBeLessThan(25000)
      expect(String(out.text)).toContain('addonError')
      expect(String(out.text)).not.toMatch(/aborted/i)
    } finally {
      fake.dispose()
    }
  }, 60000)
})
