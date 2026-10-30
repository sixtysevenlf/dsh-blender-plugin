import { afterEach, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
// @ts-expect-error - runtime is plain .mjs, no declaration file
import { LEASE, isReadOnly, leaseAcquire, leaseGate, leaseView, normalizeScope, releaseStaleLease } from '../../../runtime/http/lease.mjs'

function resetLease() {
  LEASE.holder = null
  LEASE.token = null
  LEASE.acquiredAt = null
  LEASE.expiresAt = null
  LEASE.renewals = 0
  LEASE.scope = null
}

afterEach(resetLease)

describe('http lease module', () => {
  it('normalizes collection and prefix scopes', () => {
    expect(normalizeScope('collection:COL_Lights, prefix:LGT-')).toEqual([
      { kind: 'collection', value: 'COL_Lights' },
      { kind: 'prefix', value: 'LGT-' },
    ])
    expect(normalizeScope(['COL_Assets', 'LGT-*'])).toEqual([
      { kind: 'collection', value: 'COL_Assets' },
      { kind: 'prefix', value: 'LGT-' },
    ])
    expect(normalizeScope('   ')).toBeNull()
  })

  it('grants, denies another holder, and allows force takeover', () => {
    resetLease()
    const a = leaseAcquire('plugin-a', null, false)
    expect(a.granted).toBe(true)
    expect(a.lease.holder).toBe('plugin-a')
    expect(leaseView().active).toBe(true)

    const b = leaseAcquire('plugin-b', null, false)
    expect(b.denied).toBe(true)
    expect(b.holder).toBe('plugin-a')

    expect(leaseAcquire('plugin-a', null, false).granted).toBe(true) // 续期同一 holder
    expect(leaseView().renewals).toBe(1)

    const forced = leaseAcquire('plugin-b', 5 * 60 * 1000, true)
    expect(forced.granted).toBe(true)
    expect(leaseView().holder).toBe('plugin-b')
    expect(leaseView().ttlMs).toBe(5 * 60 * 1000)
  })

  it('returns 409 envelope from leaseGate for a foreign holder', () => {
    resetLease()
    leaseAcquire('plugin-a', null, false)
    const gate = leaseGate({ holder: 'plugin-b' })
    expect(gate).toMatchObject({ ok: false, error: 'leased', holder: 'plugin-a' })
    expect(gate.holderScope).toBeNull()
    expect(leaseGate({ holder: 'plugin-a' })).toBeNull()
  })

  it('reclaims the lease of a dead holder', () => {
    resetLease()
    const dead = spawnSync(process.execPath, ['-e', ''])
    expect(dead.pid).toBeGreaterThan(0)
    leaseAcquire('plugin-pid-' + String(dead.pid), null, false)
    expect(leaseView().holderAlive).toBe(false)
    expect(releaseStaleLease('unit test')).toBe('plugin-pid-' + String(dead.pid))
    expect(leaseView().active).toBe(false)
  })

  it('keeps the read-only op table', () => {
    expect(isReadOnly('/plan', 'catalog')).toBe(true)
    expect(isReadOnly('/txn', 'list')).toBe(true)
    expect(isReadOnly('/job', 'collect')).toBe(true)
    expect(isReadOnly('/act', 'anything')).toBe(false)
    expect(isReadOnly('/loop', 'start')).toBe(false)
  })
})
