/**
 * P1 回归（Lead 追加第 6 条根因）：/commands 的 addon 预算必须小于宿主静态截止。
 *
 * 现场：宿主 T.commands = 60000ms，但 commands() 的 FLAGS_CODE addon.send 不传 timeout
 * （默认 120000），再串行 5 个集成 status 各 15000 → 最坏 195s。addon 半开/慢时宿主先到 60s，
 * 模型只拿到 aborted，连 scene/file/integrations 都看不到。
 *
 * 契约：FLAGS_CODE ≤15000；每个集成 status ≤5000（最坏 ≈15+25=40s < 60s）；
 *       集成超时时结果仍返回，且 message 如实写超时。
 */
import { describe, expect, it, vi } from 'vitest'
import { createDoctorApi } from '../../../runtime/core/engine-api/doctor.js'
import type { EngineApiDeps } from '../../../runtime/core/engine-api/index.js'

const FLAGS_REPLY = {
  result:
    '{"scene":"Scene","file":null,"flags":{"dshbb_use_polyhaven":true,"dshbb_use_sketchfab":false}}',
}

function fakeApi(send: ReturnType<typeof vi.fn>) {
  const E = { addon: { send }, metrics: {} } as unknown as EngineApiDeps
  return createDoctorApi(E)
}

describe('commands(): bounded addon budgets', () => {
  it('bounds FLAGS_CODE and every integration status call', async () => {
    const send = vi.fn(async (type: string, _params?: unknown, _timeoutMs?: number) => {
      if (type === 'execute_code') return FLAGS_REPLY
      return { enabled: false, message: 'off' }
    })

    const api = fakeApi(send)
    const out = (await api.commands()) as Record<string, unknown>

    expect(out.scene).toBe('Scene')
    expect((out.gates as Record<string, boolean>).dshbb_use_polyhaven).toBe(true)
    expect(Array.isArray(out.available)).toBe(true)
    expect(Array.isArray(out.disabled)).toBe(true)
    expect(typeof out.total).toBe('number')

    const flagsCall = send.mock.calls.find((call) => call[0] === 'execute_code')
    expect(flagsCall).toBeTruthy()
    const flagsTimeout = Number(flagsCall![2])
    expect(flagsTimeout).toBeGreaterThan(0)
    expect(flagsTimeout).toBeLessThanOrEqual(15000)

    const integrationTypes = [
      'get_polyhaven_status',
      'get_hyper3d_status',
      'get_sketchfab_status',
      'get_polypizza_status',
      'get_hunyuan3d_status',
    ]
    const integrationCalls = send.mock.calls.filter((call) => integrationTypes.includes(call[0]))
    expect(integrationCalls).toHaveLength(5)
    for (const call of integrationCalls) {
      const timeoutMs = Number(call[2])
      expect(timeoutMs, String(call[0])).toBeGreaterThan(0)
      expect(timeoutMs, String(call[0])).toBeLessThanOrEqual(3000)
    }
  })

  it('still returns the payload when an integration status times out, with an honest message', async () => {
    const send = vi.fn(async (type: string, _params?: unknown, _timeoutMs?: number) => {
      if (type === 'execute_code') return FLAGS_REPLY
      if (type === 'get_sketchfab_status') throw new Error('addon timeout: get_sketchfab_status')
      return { enabled: true, message: 'ok' }
    })

    const api = fakeApi(send)
    const out = (await api.commands()) as Record<string, unknown>
    const integrations = out.integrations as Record<string, { enabled: boolean; message: string }>

    expect(integrations.sketchfab).toBeTruthy()
    expect(integrations.sketchfab.enabled).toBe(false)
    expect(integrations.sketchfab.message).toContain('timeout')
    expect(integrations.polyhaven.enabled).toBe(true)
    expect(out.scene).toBe('Scene')
  })

  it('propagates a bounded FLAGS_CODE timeout instead of hanging past the host budget', async () => {
    const send = vi.fn(async (type: string, _params?: unknown, _timeoutMs?: number) => {
      if (type === 'execute_code') throw new Error('addon timeout: execute_code')
      return { enabled: false, message: 'off' }
    })

    const api = fakeApi(send)
    await expect(api.commands()).rejects.toThrow(/addon timeout: execute_code/)

    const flagsCall = send.mock.calls.find((call) => call[0] === 'execute_code')
    expect(Number(flagsCall![2])).toBeLessThanOrEqual(15000)
  })
})
