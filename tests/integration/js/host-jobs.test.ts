
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { startBackend, type Backend } from '../../helpers/backend'

// v2.6.1 read-only job area (contract v2.3 addendum section 8.3), split from the
// old host_routes_selftest.mjs. The list reads the backend ledger only: no Blender
// round-trip, so a missing Blender must still answer a well-formed empty list.

let backend: Backend

beforeAll(async () => { backend = await startBackend() })
afterAll(async () => { await backend.stop() })

describe('read-only job routes', () => {
  it('answers a well-formed empty job list without Blender', async () => {
    const jobs = await backend.get('/host/jobs?limit=5')
    expect(jobs.status).toBe(200)
    expect(jobs.json.ok).toBe(true)
    expect(Array.isArray(jobs.json.jobs)).toBe(true)
    expect(jobs.json.total).toBeGreaterThanOrEqual(0)
    expect(jobs.json.running).toBeGreaterThanOrEqual(0)
  })

  it('clamps the list limit instead of failing', async () => {
    for (const q of ['0', '1000', '-2', 'abc']) {
      const jobs = await backend.get('/host/jobs?limit=' + q)
      expect(jobs.status, q).toBe(200)
      expect(jobs.json.ok).toBe(true)
    }
  })

  it('reports an unknown job log explicitly', async () => {
    const log = await backend.get('/host/jobs/log?id=job-does-not-exist&tail=200')
    expect(log.status).toBe(200)
    expect(log.json.ok).toBe(false)
    expect(log.json.error).toBe('unknown_job')
    expect(log.json.stdout).toBeNull()
    expect(log.json.stderr).toBeNull()
    expect(log.json.job).toBeNull()
  })

  it('exposes the zero-Blender host state shape used by the TUI footer', async () => {
    const state = await backend.get('/host/state')
    expect(state.status).toBe(200)
    const body = state.json
    expect(body.ok).toBe(true)
    expect(body.channel).toMatchObject({ inflight: expect.any(Number), aiPaused: false })
    expect(body.lease).toBeTruthy()
    expect(body.render).toMatchObject({ busy: expect.any(Boolean) })
    expect(body.jobs).toMatchObject({ running: expect.any(Number) })
    expect(body.backend).toMatchObject({ pid: expect.any(Number), uptimeMs: expect.any(Number) })
    expect(body.channel.lastOkAgeMs === null || typeof body.channel.lastOkAgeMs === 'number').toBe(true)
  })

  it('stays fast because it never rounds-trips to Blender', async () => {
    const started = Date.now()
    for (let i = 0; i < 10; i += 1) await backend.get('/host/state')
    expect(Date.now() - started).toBeLessThan(2000)
  })
})
