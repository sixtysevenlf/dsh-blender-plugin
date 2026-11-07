/**
 * 3.4/3.5: status and doctor share one readiness source (Gateway liveness +
 * addon control + bpy probe + instance identity), and /health is liveness only.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { startBackend } from '../../helpers/backend'
import { startFakeWsAddon } from '../../helpers/fake-ws-addon.mjs'
import { executeViewport } from '../../../src/tools/viewport/execute.js'

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

describe('status/doctor readiness agreement', () => {
  it('reports not ready on both surfaces when no addon is attached', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())

    const health = await backend.get('/health')
    expect(health.json.ok).toBe(true)
    expect(health.json.readiness).toBeUndefined()
    expect(health.json.ready).toBeUndefined()

    const status = await backend.get('/status')
    expect(status.json.ok).toBe(true)
    expect(status.json.readiness.ready).toBe(false)
    expect(status.json.readiness.instanceId).toBe(null)
    expect(status.json.readiness.bpy.ok).toBe(false)

    const doctor = await backend.get('/doctor')
    expect(doctor.json.ok).toBe(false)
    expect(doctor.json.readiness.ready).toBe(false)
    expect(doctor.json.readiness.instanceId).toBe(null)
  })

  it('turns a successful start into the same ready doctor verdict', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({ gatewayPort: backend.port })
    closers.push(() => addon.close())
    await addon.ready

    const started = await executeViewport({ op: 'start' }, backend.port)
    expect(started).toContain('addon/bpy 可用')

    const doctor = await backend.get('/doctor')
    expect(doctor.json.ok).toBe(true)
    expect(doctor.json.addon.instanceId).toBe(addon.instanceId)
    expect(doctor.json.readiness.ready).toBe(true)
  }, 60000)

  it('reports the same instance and ready state once the addon is attached', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({ gatewayPort: backend.port })
    closers.push(() => addon.close())
    await addon.ready

    const status = await backend.get('/status')
    expect(status.json.ok).toBe(true)
    expect(status.json.readiness.ready).toBe(true)
    expect(status.json.readiness.instanceId).toBe(addon.instanceId)
    expect(status.json.readiness.addon.ready_instances).toBe(1)
    expect(status.json.readiness.bpy.ok).toBe(true)

    const doctor = await backend.get('/doctor')
    expect(doctor.json.ok).toBe(true)
    expect(doctor.json.addon.kind).toBe('ok')
    expect(doctor.json.addon.instanceId).toBe(addon.instanceId)
    expect(doctor.json.readiness.ready).toBe(true)
    expect(doctor.json.readiness.instanceId).toBe(addon.instanceId)
  })
})
