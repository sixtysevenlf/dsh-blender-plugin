/**
 * 3.4/3.5: status and doctor share one readiness source (Gateway liveness +
 * addon control + bpy probe + instance identity), and /health is liveness only.
 * The 3.1 model surface exposes the same data through blender_session status/doctor.
 */
import { afterEach, describe, expect, it } from 'vitest'
import * as plugin from '../../../src/index'
import { createFakeContext } from '../../helpers/fake-context'
import { startBackend } from '../../helpers/backend'
import { startFakeWsAddon } from '../../helpers/fake-ws-addon.mjs'

const EXEC = { agent: { id: 'main', session: { id: 'it-session' } } }

/** Run one session action through the registered tool so caller headers exist. */
async function sessionAction(port: number, action: string): Promise<string> {
  const fake = createFakeContext()
  plugin.apply(fake.ctx, { autoStart: false, tui: false, port })
  try {
    const tool = fake.tools.find((t) => t.name === 'blender_session')!
    const out: any = await (tool.execute as any)({ action }, EXEC)
    return String(out.text)
  } finally {
    fake.dispose()
  }
}

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

    const text = await sessionAction(backend.port, 'status')
    expect(text).toContain('SESSION status')
    expect(text).toContain('ready_instances=0')
  })

  it('turns an attached addon into the same ready doctor verdict', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({ gatewayPort: backend.port })
    closers.push(() => addon.close())
    await addon.ready

    const statusText = await sessionAction(backend.port, 'status')
    expect(statusText).toContain('gateway  up')
    expect(statusText).toContain('ready_instances=1')
    expect(statusText).toContain('bpy      ready')

    const doctor = await backend.get('/doctor')
    expect(doctor.json.ok).toBe(true)
    expect(doctor.json.addon.instanceId).toBe(addon.instanceId)
    expect(doctor.json.readiness.ready).toBe(true)

    const doctorText = await sessionAction(backend.port, 'doctor')
    expect(doctorText).toContain('SESSION doctor · ok=true')
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
