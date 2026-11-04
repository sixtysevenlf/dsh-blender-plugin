/**
 * P2 回归（PR #75 review）：doctor 的非超时 addon 错误归因。
 *
 * 现场：addon 回了明确错误（如「AI 操作已暂停」），旧逻辑仍把 addonError 当失败后跑 diagnose()；
 * diagnose 在 ping 通时固定返回 kind='main-thread-busy'，把「操作已暂停」写成了「主线程忙」。
 * 契约：非超时错误 → kind='addon-error'（summary 带 addonError），且不再串诊探针。
 */
import { describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  diagnose: vi.fn(async () => ({
    kind: 'main-thread-busy',
    tcp: true,
    ping: true,
    summary: 'stale diagnosis that must not be used',
  })),
}))

vi.mock('../../../runtime/transport/probes.js', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    diagnose: h.diagnose,
    tcpProbe: async () => true,
    freshPing: async () => 4,
    protocolInfo: async () => ({ id: 'test-protocol', label: 'test', from: 'unit' }),
  }
})

import { addonErrorDiagnosis, createDoctorApi } from '../../../runtime/core/engine-api/doctor.js'
import type { EngineApiDeps } from '../../../runtime/core/engine-api/index.js'

function fakeE(send: (type: string, params?: unknown, timeoutMs?: number) => Promise<unknown>) {
  return {
    LEDGER: '/tmp/ledger.json',
    RESULTS_DIR: '/tmp/results',
    SESSION_NAME: 'unit-test',
    TRAJ_DIR: '/tmp/traj',
    TRAJ_FULL: false,
    TRAJ_OFF: true,
    addon: { send },
    jobSnapshot: () => ({}),
    jobs: new Map(),
    ledgerList: () => [],
    ledgerState: () => ({}),
    runSnapshot: () => ({}),
    runs: new Map(),
    metrics: {},
  } as unknown as EngineApiDeps
}

describe('doctor: non-timeout addon errors are addon-error, not main-thread-busy', () => {
  it('builds an addon-error diagnosis that quotes the addonError', () => {
    const d = addonErrorDiagnosis('AI 操作已暂停（用户在 DSH 里暂停了）')
    expect(d.kind).toBe('addon-error')
    expect(String(d.summary)).toContain('AI 操作已暂停')
    expect(String(d.summary)).toContain('不是 main-thread-busy')
    expect(String(d.fix)).toContain('暂停')
    expect(String(d.hint)).toContain('addonError')
  })

  it('does not run diagnose() for a non-timeout addonError', async () => {
    h.diagnose.mockClear()
    const send = vi.fn(async () => {
      throw new Error('AI 操作已暂停（用户在 DSH 里暂停了）')
    })
    const api = createDoctorApi(fakeE(send))

    const out = (await api.doctor()) as Record<string, any>

    expect(out.ok).toBe(false)
    expect(out.addon.kind).toBe('addon-error')
    expect(String(out.addon.summary)).toContain('AI 操作已暂停')
    expect(h.diagnose).not.toHaveBeenCalled()
  })
})
