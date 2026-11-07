/**
 * Regression (PR #75 review): doctor's attribution of a non-timeout addon error.
 *
 * At the time, a clear addon error (such as "AI operations are paused") was
 * followed by a legacy diagnose() that reported "main-thread-busy". 3.1 removed
 * that legacy probe path; the contract is still that a non-timeout addon error
 * is addon-error and is not rewritten as a transport/thread failure.
 */
import { describe, expect, it, vi } from 'vitest'

import { addonErrorDiagnosis, createDoctorApi } from '../../../runtime/core/engine-api/doctor.js'
import type { EngineApiDeps } from '../../../../runtime/core/engine-api/index.js'

function fakeE(send: (type: string, params?: unknown, timeoutMs?: number) => Promise<unknown>) {
  return {
    LEDGER: '/tmp/ledger.json',
    RESULTS_DIR: '/tmp/results',
    SESSION_NAME: 'unit-test',
    TRAJ_DIR: '/tmp/traj',
    TRAJ_FULL: false,
    TRAJ_OFF: true,
    addon: { send, instances: [{ instanceId: 'unit-instance', ready: true }] },
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

  it('does not attach a stale legacy diagnosis payload', async () => {
    const send = vi.fn(async () => {
      throw new Error('AI 操作已暂停（用户在 DSH 里暂停了）')
    })
    const api = createDoctorApi(fakeE(send))

    const out = (await api.doctor()) as Record<string, any>

    expect(out.ok).toBe(false)
    expect(out.addon.kind).toBe('addon-error')
    expect(String(out.addon.summary)).toContain('AI 操作已暂停')
    expect(out.addon.diagnosis).toBeUndefined()
  })
})
