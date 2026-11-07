/**
 * 2.11: Gateway, addon and handshake failures must be attributable and must not
 * impersonate one another. The Gateway keeps the last rejected handshake so a
 * protocol-11 addon is reported as stage=handshake, not "no addon connected".
 */
import { afterEach, describe, expect, it } from 'vitest'
import { startBackend } from '../../helpers/backend'
import { startFakeWsAddon } from '../../helpers/fake-ws-addon.mjs'

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

describe('Gateway connection errors are distinguishable', () => {
  it('reports no addon connected when the Gateway is alive but nothing is attached', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const doctor = await backend.get('/doctor')
    expect(doctor.status).toBe(200)
    expect(doctor.json.ok).toBe(false)
    expect(doctor.json.addon.kind).toBe('addon-unreachable')
    expect(doctor.json.addon.stage).toBe('transport')
    expect(doctor.json.addon.connected).toBe(false)
    expect(doctor.json.addon.fix).toContain('Blender 4.2.10')
  })

  it('attributes a protocol-11 addon to the handshake stage, not to a missing addon', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({ gatewayPort: backend.port, protocol: 11 })
    closers.push(() => addon.close())
    await addon.ready.catch(() => {})
    // The rejection is recorded before the socket close; give the close event a beat.
    await new Promise((resolve) => setTimeout(resolve, 100))

    const doctor = await backend.get('/doctor')
    expect(doctor.status).toBe(200)
    expect(doctor.json.ok).toBe(false)
    expect(doctor.json.addon.kind).toBe('addon-handshake-rejected')
    expect(doctor.json.addon.kind).not.toBe('addon-unreachable')
    expect(doctor.json.addon.stage).toBe('handshake')
    expect(doctor.json.addon.code).toBe('unsupported_protocol')
    expect(doctor.json.addon.upgrade_required).toBe(true)
    expect(String(doctor.json.addon.summary)).toContain('11')
    expect(String(doctor.json.addon.fix)).toContain('2.0.0')
    expect(String(doctor.json.addon.fix)).toContain('protocol 12')
  })
})
