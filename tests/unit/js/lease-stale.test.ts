import { beforeEach, describe, expect, it } from 'vitest'
import { spawn } from 'node:child_process'
import { LEASE, holderAlive, leaseAcquire, leaseGate, leaseView, releaseStaleLease } from '../../../runtime/http/lease.js'

// Dead-session lease reclaim (v0.9.4): a crashed session used to keep the write
// lease for the whole TTL and lock every other session out. The lease must be
// reclaimed only when the holder process is provably gone; an unknown holder
// naming scheme stays conservative (never steal).

const ME = 'plugin-pid-' + String(process.pid)

async function deadPid(): Promise<number> {
  return await new Promise((resolve) => {
    const child = spawn(process.execPath, ['-e', 'process.exit(0)'], { stdio: 'ignore' })
    const pid = child.pid as number
    child.on('exit', () => setTimeout(() => resolve(pid), 150))
  })
}

function resetLease() {
  LEASE.holder = null; LEASE.token = null; LEASE.acquiredAt = null; LEASE.expiresAt = null
  LEASE.renewals = 0; LEASE.scope = null
}

describe('stale write lease reclaim', () => {
  beforeEach(() => resetLease())

  it('proves a dead holder dead, a live holder alive and an unknown scheme unknowable', async () => {
    const pid = await deadPid()
    expect(holderAlive('plugin-pid-' + String(pid))).toBe(false)
    expect(holderAlive(ME)).toBe(true)
    expect(holderAlive('orca-model-session')).toBeNull()
    expect(holderAlive('')).toBeNull()
  })

  it('reclaims a lease whose holder process no longer exists', async () => {
    const dead = 'plugin-pid-' + String(await deadPid())
    expect(leaseAcquire(dead, 60000, true).granted).toBe(true)
    expect(leaseView()).toMatchObject({ active: true, holderAlive: false })
    const reclaimed = releaseStaleLease('probe')
    expect(reclaimed).toBe(dead)
    expect(leaseView().active).toBe(false)
  })

  it('lets the next live writer in through the gate after the reclaim', async () => {
    const dead = 'plugin-pid-' + String(await deadPid())
    leaseAcquire(dead, 60000, true)
    const before = leaseView().staleReclaimed
    expect(leaseGate({ holder: ME })).toBeNull()
    expect(leaseView()).toMatchObject({ active: true, holder: ME, holderAlive: true })
    expect(leaseView().staleReclaimed).toBe(before + 1)
  })

  it('keeps blocking a live holder without force', () => {
    const other = 'plugin-pid-' + String(process.ppid || 1)
    expect(leaseAcquire(other, 60000).granted).toBe(true)
    const denied = leaseGate({ holder: ME })
    expect(denied).toMatchObject({ ok: false, error: 'leased' })
    expect(denied && denied.hint).toContain('force')
  })

  it('stays conservative for non-conventional holder names', () => {
    leaseAcquire('orca-model-session', 60000, true)
    expect(leaseView().holderAlive).toBeNull()
    expect(leaseGate({ holder: ME })).toMatchObject({ ok: false, error: 'leased' })
  })

  it('releases stale leases only when the holder is provably dead', () => {
    leaseAcquire(ME, 60000)
    expect(releaseStaleLease('probe')).toBeNull()
    expect(leaseView()).toMatchObject({ active: true, holder: ME })
  })
})
