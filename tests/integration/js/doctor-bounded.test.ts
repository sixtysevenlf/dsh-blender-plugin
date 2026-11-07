/**
 * 集成回归（验收）：addon 已连接 Gateway 但不回包（半开）时，
 * 真实 Gateway 的 /status 与 /doctor 必须在 10s 内返回结构化诊断，且指向 addon，
 * 而不是 host 先 20s abort / 统一报 backend_unreachable。
 *
 * 半开 addon 用 tests/helpers/fake-ws-addon.mjs 的 behavior 构造：握手后对任何请求都不回写。
 * 后端用 tests/helpers/backend.ts 的 startBackend() 起真实 lib/runtime/gateway.js。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { startBackend } from '../../helpers/backend'
import { startFakeWsAddon } from '../../helpers/fake-ws-addon.mjs'

// 3.1 预算：addon 单次 status 探测 ≤2500ms，端到端 /status 与 /doctor ≤3000ms
// （config/availability.json 的 transport/addon 预算）。本机实测冷/热 /status 2525/2522ms、
// /doctor 2523/2530ms；这里先预热一次再计时，并断言一次调用只发一个探测请求（3.7：不做嵌套重试）。
const BUDGET_MS = 3_000
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

/** 握手后对任何请求都不回写：模拟 addon 线程卡死/主线程忙到不响应。 */
function halfOpenBehavior() {
  return { action: 'hang' }
}

describe('half-open addon: bounded status/doctor with honest diagnosis', () => {
  it('answers /status in bounded time with a non-empty addonError', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({
      gatewayPort: backend.port,
      behavior: halfOpenBehavior,
    })
    closers.push(() => addon.close())
    await addon.ready

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

    const probesBefore = (addon.requests as Array<{ type: string }>).filter(
      (r) => r.type === 'execute_code',
    ).length
    const t0 = Date.now()
    const status = await backend.get('/status')
    const statusMs = Date.now() - t0
    const probesAfter = (addon.requests as Array<{ type: string }>).filter(
      (r) => r.type === 'execute_code',
    ).length
    console.log('[doctor-bounded] GET /status = ' + String(statusMs) + 'ms')

    expect(status.status).toBe(200)
    expect(statusMs).toBeLessThan(BUDGET_MS)
    // 启动探测不再占串行队列头：改造前冷热差 ~3s，现在应接近 0。
    expect(coldMs - statusMs).toBeLessThan(2_000)
    expect(status.json.ok).toBe(true)
    expect(status.json.plugin.version).toMatch(/^\d+\.\d+\.\d+$/)
    expect(typeof status.json.addonError).toBe('string')
    expect(status.json.addonError).toContain('addon timeout')
    expect(status.json.region).toBeNull()
    // 3.7：一次 /status 只发一次有界 addon 探测；超时后不得再 ping 或二次探测。
    expect(probesAfter - probesBefore).toBe(1)
  }, 60_000)

  it('answers /doctor in bounded time with a diagnosis that points at the addon, not a missing backend', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({
      gatewayPort: backend.port,
      behavior: halfOpenBehavior,
    })
    closers.push(() => addon.close())
    await addon.ready

    // 冷启动首调：改造前启动探测让 doctor 首调多付 ~3s，这里记录冷热差防回归。
    const tCold = Date.now()
    const cold = await backend.get('/doctor')
    const coldMs = Date.now() - tCold
    console.log('[doctor-bounded] cold GET /doctor = ' + String(coldMs) + 'ms')
    expect(cold.status).toBe(200)

    // 预热：首调含启动队列/首次 diagnose，计时只量稳态。
    await backend.get('/doctor')

    const probesBefore = (addon.requests as Array<{ type: string }>).filter(
      (r) => r.type === 'execute_code',
    ).length
    const t0 = Date.now()
    const doctor = await backend.get('/doctor')
    const doctorMs = Date.now() - t0
    const probesAfter = (addon.requests as Array<{ type: string }>).filter(
      (r) => r.type === 'execute_code',
    ).length
    console.log('[doctor-bounded] GET /doctor = ' + String(doctorMs) + 'ms')

    expect(doctor.status).toBe(200)
    expect(doctorMs).toBeLessThan(BUDGET_MS)
    // 启动探测不再占串行队列头：改造前冷热差 ~3s，现在应接近 0。
    expect(coldMs - doctorMs).toBeLessThan(2_000)
    expect(doctor.json.ok).toBe(false)
    const addonDiagnosis = doctor.json.addon as Record<string, unknown>
    expect(addonDiagnosis.tcp).toBe(true)
    expect(addonDiagnosis.connected).toBe(true)
    expect(addonDiagnosis.stage).toBe('addon')
    expect(addonDiagnosis.kind).not.toBe('blender-unreachable')
    expect(addonDiagnosis.kind).toBe('addon-unresponsive')
    expect(String(addonDiagnosis.error)).toContain('addon timeout')
    expect(Number(addonDiagnosis.timeout_ms)).toBeGreaterThan(0)
    expect(Number(addonDiagnosis.timeout_ms)).toBeLessThanOrEqual(5000)
    expect(String(addonDiagnosis.fix)).toContain('Blender')
    expect(String(addonDiagnosis.fix)).toContain('DSH Blender Bridge')
    expect(String(doctor.json.error)).toContain('addon timeout')
    // 3.7：/doctor 复用 /status 的同源 readiness，一次有界探测后直接给 addon 归因，不 ping 重试。
    expect(probesAfter - probesBefore).toBe(1)
  }, 60_000)

  it('reports no addon connected when only Gateway is running', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const t0 = Date.now()
    const doctor = await backend.get('/doctor')
    const doctorMs = Date.now() - t0
    console.log('[doctor-bounded] GET /doctor (no addon) = ' + String(doctorMs) + 'ms')

    expect(doctorMs).toBeLessThan(5000)
    expect(doctor.json.ok).toBe(false)
    const addonDiagnosis = doctor.json.addon as Record<string, unknown>
    expect(addonDiagnosis.kind).toBe('addon-unreachable')
    expect(addonDiagnosis.stage).toBe('transport')
    expect(addonDiagnosis.connected).toBe(false)
    expect(addonDiagnosis.tcp).toBe(false)
    expect(String(addonDiagnosis.summary)).toContain('Gateway')
  }, 60_000)
})
