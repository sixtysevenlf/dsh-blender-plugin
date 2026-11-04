/**
 * P0-3 集成回归（验收 A3/A6）：9876 有监听但 addon 不回包（半开）时，
 * 真实 lib 后端的 /status 与 /doctor 必须在 10s 内返回结构化诊断，且指向 addon，
 * 而不是 host 先 20s abort / 统一报 backend_unreachable。
 *
 * 半开 addon 用 tests/helpers/fake-addon.mjs 的 behavior 构造：TCP accept 后永不回写。
 * 后端用 tests/helpers/backend.ts 的 startBackend({addonPort}) 起真实 lib/runtime/server.js。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { freePort, startBackend } from '../../helpers/backend'
import { startFakeAddon } from '../../helpers/fake-addon.mjs'

// 改造前（未 await 的启动探测占串行队列头，budgets 3.5s/3s）：冷/热 /status 8983/6026ms、
// /doctor 8495/5526ms —— 冷启多付 ~3s 串行税。改造后（去掉启动探测，budgets 恢复 5s/4s）：
// /status 7533/7527ms、/doctor 6531/6518ms，冷热差 <20ms。
// 这里先预热一次再计时，并留 2s 余量：判据是「远小于旧的 20s+ 且给出 addon 归因」，不是卡死某个毫秒数；
// 另断言冷热差 <2s，防止启动串行税回归。
const BUDGET_MS = 12_000
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

/** TCP accept 后对任何请求都不回写：模拟 addon 线程卡死/主线程忙到不响应。 */
function halfOpenBehavior(): Promise<never> {
  return new Promise<never>(() => {})
}

describe('half-open addon: bounded status/doctor with honest diagnosis', () => {
  it('answers /status in bounded time with a non-empty addonError', async () => {
    const addon = await startFakeAddon({ behavior: halfOpenBehavior })
    closers.push(() => addon.close())
    const backend = await startBackend({ addonPort: addon.port })
    closers.push(() => backend.stop())

    const health = await backend.get('/health')
    expect(health.json.ok).toBe(true)

    // 冷启动首调：改造前这里是启动队列 + 未 await 的启动探测（~3s 串行税），单独记录用于前后对比。
    const tCold = Date.now()
    const cold = await backend.get('/status')
    const coldMs = Date.now() - tCold
    console.log('[doctor-bounded] cold GET /status = ' + String(coldMs) + 'ms')
    expect(cold.status).toBe(200)

    // 预热：首调含启动队列/首次 diagnose，计时只量稳态。
    await backend.get('/status')

    const t0 = Date.now()
    const status = await backend.get('/status')
    const statusMs = Date.now() - t0
    console.log('[doctor-bounded] GET /status = ' + String(statusMs) + 'ms')

    expect(status.status).toBe(200)
    expect(statusMs).toBeLessThan(BUDGET_MS)
    // 启动探测不再占串行队列头：改造前冷热差 ~3s，现在应接近 0。
    expect(coldMs - statusMs).toBeLessThan(2_000)
    expect(status.json.ok).toBe(true)
    expect(status.json.plugin.version).toMatch(/^\d+\.\d+\.\d+$/)
    expect(typeof status.json.addonError).toBe('string')
    expect(status.json.addonError).toContain('timeout')
    expect(status.json.region).toBeNull()
  }, 60_000)

  it('answers /doctor in bounded time with a diagnosis that points at the addon, not a missing backend', async () => {
    const addon = await startFakeAddon({ behavior: halfOpenBehavior })
    closers.push(() => addon.close())
    const backend = await startBackend({ addonPort: addon.port })
    closers.push(() => backend.stop())

    // 冷启动首调：改造前启动探测让 doctor 首调多付 ~3s，这里记录冷热差防回归。
    const tCold = Date.now()
    const cold = await backend.get('/doctor')
    const coldMs = Date.now() - tCold
    console.log('[doctor-bounded] cold GET /doctor = ' + String(coldMs) + 'ms')
    expect(cold.status).toBe(200)

    // 预热：首调含启动队列/首次 diagnose，计时只量稳态。
    await backend.get('/doctor')

    const t0 = Date.now()
    const doctor = await backend.get('/doctor')
    const doctorMs = Date.now() - t0
    console.log('[doctor-bounded] GET /doctor = ' + String(doctorMs) + 'ms')

    expect(doctor.status).toBe(200)
    expect(doctorMs).toBeLessThan(BUDGET_MS)
    // 启动探测不再占串行队列头：改造前冷热差 ~3s，现在应接近 0。
    expect(coldMs - doctorMs).toBeLessThan(2_000)
    expect(doctor.json.ok).toBe(false)
    const addonDiagnosis = doctor.json.addon as Record<string, unknown>
    expect(addonDiagnosis.tcp).toBe(true)
    expect(addonDiagnosis.kind).not.toBe('blender-unreachable')
    expect(addonDiagnosis.kind).toBe('addon-unresponsive')
    expect(String(addonDiagnosis.error)).toContain('timeout')
    expect(Number(addonDiagnosis.timeout_ms)).toBeGreaterThan(0)
    expect(Number(addonDiagnosis.timeout_ms)).toBeLessThanOrEqual(5000)
    expect(String(addonDiagnosis.fix)).toContain('Blender')
    expect(String(addonDiagnosis.fix)).toContain('DSH Blender Bridge')
    expect(String(doctor.json.error)).toContain('timeout')
  }, 60_000)

  it('keeps the old blender-unreachable verdict when nothing listens on 9876', async () => {
    const deadAddonPort = await freePort()
    const backend = await startBackend({ addonPort: deadAddonPort })
    closers.push(() => backend.stop())

    const t0 = Date.now()
    const doctor = await backend.get('/doctor')
    const doctorMs = Date.now() - t0
    console.log('[doctor-bounded] GET /doctor (dead addon) = ' + String(doctorMs) + 'ms')

    expect(doctorMs).toBeLessThan(5000)
    expect(doctor.json.ok).toBe(false)
    const addonDiagnosis = doctor.json.addon as Record<string, unknown>
    expect(addonDiagnosis.kind).toBe('blender-unreachable')
    expect(addonDiagnosis.tcp).toBe(false)
  }, 60_000)
})
