import { afterEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import type { AddressInfo } from 'node:net'
import { handleDnnRpc, readRuntimeSnapshot } from '../../../src/host/dnn-config.js'

const OWNER = { principal: { kind: 'owner' } }
const homes: string[] = []
const servers: http.Server[] = []

function tempHome(): string {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-dnn-it-'))
  homes.push(home)
  return home
}

afterEach(async () => {
  for (const server of servers.splice(0)) {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
  for (const home of homes.splice(0)) fs.rmSync(home, { recursive: true, force: true })
})

interface FakeGateway {
  port: number
  close: () => Promise<void>
}

/** A real loopback HTTP server for GET /doctor; no new route is introduced by the host. */
async function startGateway(body: () => unknown): Promise<FakeGateway> {
  const server = http.createServer((req, res) => {
    const route = (req.url || '').split('?')[0]
    if (route === '/doctor') {
      res.writeHead(200, { 'content-type': 'application/json' })
      // The live Gateway exposes visionDoctorView() under /doctor.vision, so the
      // DNN block is nested at vision.dnn rather than at the top level.
      res.end(JSON.stringify({ ok: true, vision: { dnn: body() } }))
      return
    }
    res.writeHead(404, { 'content-type': 'application/json' })
    res.end('{}')
  })
  servers.push(server)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
  const port = (server.address() as AddressInfo).port
  return {
    port,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  }
}

function flat(snapshot: ReturnType<typeof readSnapshot>): Record<string, unknown> {
  return {
    dnn: snapshot.features.dnn,
    python: snapshot.vision.python,
    venv: snapshot.vision.venv,
    model: snapshot.vision.model,
    backend: snapshot.vision.dnn.backend,
    device: snapshot.vision.dnn.device,
  }
}

function readSnapshot(home: string) {
  const read = readRuntimeSnapshot(home)
  if (!read.ok) throw new Error('snapshot missing: ' + read.code)
  return read.snapshot
}

/** Simulates the Gateway reconciling runtime.json on /doctor. */
function reconcileDoctor(home: string): Record<string, unknown> {
  const read = readRuntimeSnapshot(home)
  if (!read.ok) return { envLocked: false, features: { dnn: false }, config: {} }
  const snapshot = read.snapshot
  return {
    envLocked: false,
    features: { dnn: snapshot.features.dnn, source: 'runtime', experimental: true },
    config: {
      activeRevision: snapshot.revision,
      pendingRevision: null,
      phase: 'idle',
      waitingFor: null,
      lastSwitchError: null,
      previous: null,
      active: { revision: snapshot.revision, values: flat(snapshot) },
    },
  }
}

function candidate(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    features: { dnn: true },
    vision: {
      python: '',
      venv: 'C:\\venvs\\blender-onnx',
      model: 'yolov5n',
      dnn: { backend: 'onnxruntime', device: 'cpu' },
      ...overrides,
    },
  }
}

async function call(
  payload: Record<string, unknown>,
  port: number,
  home: string,
  extra: Record<string, unknown> = {},
): Promise<any> {
  return await handleDnnRpc(payload, OWNER, port, { home, ...extra })
}

describe('DNN hot apply through a fake Gateway (3.12)', () => {
  it('writes runtime.json, forces /doctor reconcile, and reports the new revision without restarting', async () => {
    const home = tempHome()
    const gateway = await startGateway(() => reconcileDoctor(home))

    const applied = await call({ op: 'apply', candidate: candidate() }, gateway.port, home, {
      now: () => 5000,
    })
    expect(applied.ok).toBe(true)
    expect(applied.value).toMatchObject({
      applied: true,
      phase: 'idle',
      revision: 5000,
      activeRevision: 5000,
      savedForNextStart: false,
      gatewayReachable: true,
    })

    const snapshot = readSnapshot(home)
    expect(snapshot.revision).toBe(5000)
    expect(snapshot.features.dnn).toBe(true)
    expect(snapshot.vision.dnn.backend).toBe('onnxruntime')
    expect(snapshot.vision.venv).toBe('C:\\venvs\\blender-onnx')

    // Same Gateway process keeps serving; a second apply is monotonic and still applied.
    const second = await call(
      { op: 'apply', candidate: candidate({ model: 'yolov5s' }) },
      gateway.port,
      home,
      { now: () => 5000 },
    )
    expect(second.value.revision).toBe(5001)
    expect(second.value.applied).toBe(true)
    expect(readSnapshot(home).vision.model).toBe('yolov5s')

    const status = await call({ op: 'status' }, gateway.port, home)
    expect(status.ok).toBe(true)
    expect(status.value.config.activeRevision).toBe(5001)
    expect(status.value.gateway.reachable).toBe(true)
    expect(status.value.models.modelsDir).toContain('vision')
  })

  it('reports pending while a vision request is in flight instead of claiming applied', async () => {
    const home = tempHome()
    const pendingConfig = {
      activeRevision: 4000,
      pendingRevision: 9999,
      phase: 'pending',
      waitingFor: { op: 'vision_detect', startedAt: '2026-01-01T00:00:00.000Z' },
      lastSwitchError: null,
      previous: null,
      active: {
        revision: 4000,
        values: {
          dnn: true,
          python: '',
          venv: '',
          model: 'yolov5n',
          backend: 'opencv',
          device: 'cpu',
        },
      },
    }
    const gateway = await startGateway(() => ({
      envLocked: false,
      features: { dnn: true },
      config: pendingConfig,
    }))

    const result = await call({ op: 'apply', candidate: candidate() }, gateway.port, home, {
      now: () => 6000,
    })
    expect(result.ok).toBe(true)
    expect(result.value.applied).toBe(false)
    expect(result.value.phase).toBe('pending')
    expect(result.value.activeRevision).toBe(4000)
    expect(result.value.waitingFor).toEqual(pendingConfig.waitingFor)
    expect(readSnapshot(home).revision).toBe(6000)

    const status = await call({ op: 'status' }, gateway.port, home)
    expect(status.value.config.phase).toBe('pending')
    expect(status.value.config.pendingRevision).toBe(9999)
  })

  it('cancels a pending switch by rewriting the active configuration', async () => {
    const home = tempHome()
    const active = {
      dnn: true,
      python: '',
      venv: '',
      model: 'yolov5m',
      backend: 'opencv',
      device: 'cpu',
    }
    let mode: 'pending' | 'reconcile' = 'pending'
    const gateway = await startGateway(() =>
      mode === 'pending'
        ? {
            envLocked: false,
            features: { dnn: true },
            config: {
              activeRevision: 4100,
              pendingRevision: 9999,
              phase: 'pending',
              waitingFor: { op: 'vision_detect' },
              lastSwitchError: null,
              previous: null,
              active,
            },
          }
        : reconcileDoctor(home),
    )

    const result = await call({ op: 'cancel-pending' }, gateway.port, home, { now: () => 7000 })
    expect(result.ok).toBe(true)
    const snapshot = readSnapshot(home)
    expect(snapshot.revision).toBe(7000)
    expect(snapshot.vision.model).toBe('yolov5m')
    expect(snapshot.vision.dnn.backend).toBe('opencv')

    mode = 'reconcile'
    const status = await call({ op: 'status' }, gateway.port, home)
    expect(status.value.config.activeRevision).toBe(7000)
    expect(status.value.config.phase).toBe('idle')
  })

  it('reports a failed custom-venv switch and rolls back explicitly', async () => {
    const home = tempHome()
    const previous = {
      dnn: true,
      python: '',
      venv: '',
      model: 'yolov5s',
      backend: 'opencv',
      device: 'cpu',
    }
    const failedConfig = {
      activeRevision: 8000,
      pendingRevision: 8001,
      phase: 'failed',
      waitingFor: null,
      lastSwitchError: {
        code: 'dnn_config_switch_failed',
        message: 'custom venv exited: No module named cv2',
      },
      previous: { revision: 7999, values: previous },
      active: {
        revision: 8000,
        values: {
          dnn: true,
          python: '',
          venv: 'C:\\missing-venv',
          model: 'yolov5n',
          backend: 'onnxruntime',
          device: 'gpu',
        },
      },
    }
    let mode: 'failed' | 'reconcile' = 'failed'
    const gateway = await startGateway(() =>
      mode === 'failed'
        ? { envLocked: false, features: { dnn: true }, config: failedConfig }
        : reconcileDoctor(home),
    )

    const failed = await call(
      { op: 'apply', candidate: candidate({ venv: 'C:\\missing-venv' }) },
      gateway.port,
      home,
      { now: () => 8002 },
    )
    expect(failed.ok).toBe(true)
    expect(failed.value.applied).toBe(false)
    expect(failed.value.phase).toBe('failed')
    expect(failed.value.activeRevision).toBe(8000)
    expect(failed.value.savedForNextStart).toBe(false)
    expect(readSnapshot(home).revision).toBe(8002)

    const status = await call({ op: 'status' }, gateway.port, home)
    expect(status.value.config.phase).toBe('failed')
    expect(status.value.config.lastSwitchError.message).toContain('No module named cv2')
    expect(status.value.config.previous.revision).toBe(7999)

    // Rollback reads previous from the failed doctor response; it must run
    // before the Gateway starts reconciling the rewritten snapshot.
    const rolledBack = await call({ op: 'rollback' }, gateway.port, home, { now: () => 8003 })
    expect(rolledBack.ok).toBe(true)
    const snapshot = readSnapshot(home)
    expect(snapshot.revision).toBe(8003)
    expect(snapshot.vision.dnn.backend).toBe('opencv')
    expect(snapshot.vision.model).toBe('yolov5s')

    mode = 'reconcile'
    const restored = await call({ op: 'status' }, gateway.port, home)
    expect(restored.value.config.activeRevision).toBe(8003)
    expect(restored.value.config.phase).toBe('idle')
  })

  it('keeps the snapshot when the Gateway is unreachable and never claims it applied', async () => {
    const home = tempHome()
    const gateway = await startGateway(() => reconcileDoctor(home))
    const port = gateway.port
    await gateway.close()

    const result = await call({ op: 'apply', candidate: candidate() }, port, home, {
      now: () => 9000,
    })
    expect(result.ok).toBe(false)
    expect(result.error.code).toBe('gateway_unreachable')
    expect(result.error.details).toMatchObject({
      applied: false,
      savedForNextStart: true,
      revision: 9000,
    })
    expect(readSnapshot(home).revision).toBe(9000)

    // When the Gateway comes back, the same snapshot is reported as active.
    const back = await startGateway(() => reconcileDoctor(home))
    const status = await call({ op: 'status' }, back.port, home)
    expect(status.value.gateway.reachable).toBe(true)
    expect(status.value.config.activeRevision).toBe(9000)
  })

  it('runs probe without writing runtime.json or the models directory', async () => {
    const home = tempHome()
    let seen: Record<string, unknown> | null = null
    const fakeProbe = async (options: Record<string, unknown>) => {
      seen = options
      return {
        ok: true,
        checks: [{ id: 'python', status: 'pass', detail: '3.11.1' }],
        summary: { pass: 1, warn: 0, fail: 0 },
        elapsedMs: 3,
        candidate: options.candidate,
      }
    }

    const result = await call({ op: 'probe', candidate: candidate() }, 1, home, {
      probe: fakeProbe,
      env: {},
    })
    expect(result.ok).toBe(true)
    expect(result.value.ok).toBe(true)
    expect(seen && (seen as Record<string, unknown>).home).toBe(home)
    expect(fs.existsSync(path.join(home, 'vision', 'runtime.json'))).toBe(false)
    expect(fs.existsSync(path.join(home, 'vision', 'models'))).toBe(false)
  })
})
