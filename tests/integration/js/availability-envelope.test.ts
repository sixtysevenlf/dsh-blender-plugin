/**
 * 3.2: every failed Gateway response carries the four-question envelope, even
 * when the route still uses the historical {ok:false, error} shape. The HTTP
 * edge normalizes instead of letting a bare "failed" reach the model.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { startBackend } from '../../helpers/backend'

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

describe('availability envelope at the Gateway edge', () => {
  it('turns a 404 into a staged failure with a fix and a fallback', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const response = await backend.get('/definitely-not-a-route')
    expect(response.status).toBe(404)
    expect(response.json.ok).toBe(false)
    expect(response.json.stage).toBe('config')
    expect(response.json.root_cause).toBe('not found')
    expect(response.json.fix).toBeTruthy()
    expect(response.json.fallback.kind).toBe('unavailable')
    expect(response.json.fallback.summary).toBeTruthy()
    expect(response.json.evidence).toBeTypeOf('object')
  })

  it('adds the envelope to doctor when no addon is attached', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const doctor = await backend.get('/doctor')
    expect(doctor.json.ok).toBe(false)
    expect(doctor.json.stage).toBe('transport')
    expect(doctor.json.root_cause).toContain('没有 Blender addon')
    expect(doctor.json.fix).toContain('Blender 4.2.10')
    expect(doctor.json.fallback.kind).toBe('unavailable')
    expect(doctor.json.evidence).toBeTypeOf('object')
  })
})
