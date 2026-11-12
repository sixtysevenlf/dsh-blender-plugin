import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  DNN_RUNTIME_SNAPSHOT_VERSION,
  handleDnnRpc,
  readRuntimeSnapshot,
  writeRuntimeSnapshot,
  type DnnRpcDeps,
} from '../../../src/host/dnn-config.js'
import { resetDnnDownloadForTests } from '../../../src/host/dnn-download.js'
import { dispatchVirtualGuiRpc } from '../../../src/host/virtual-gui-permission.js'

const OWNER = { principal: { kind: 'owner' } }
const OTHER = { principal: { kind: 'agent' } }

const tempDirs: string[] = []

function tempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-blender-dnn-config-'))
  tempDirs.push(dir)
  return dir
}

function snapshotPath(home: string): string {
  return path.join(home, 'vision', 'runtime.json')
}

function readSnapshot(home: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(snapshotPath(home), 'utf8')) as Record<string, unknown>
}

function candidate(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    features: { dnn: true },
    vision: { python: '', venv: '', model: 'yolov5n' },
    dnn: { backend: 'opencv', device: 'cpu' },
    ...overrides,
  }
}

/** A fake /doctor that echoes the snapshot the host just wrote (like reconcile). */
function echoingDoctor(home: string, extra: Record<string, unknown> = {}) {
  return async () => {
    const snap = JSON.parse(fs.readFileSync(snapshotPath(home), 'utf8')) as {
      revision: number
      features: { dnn: boolean }
      vision: {
        python: string
        venv: string
        model: string
        dnn: { backend: string; device: string }
      }
    }
    return {
      ok: true,
      dnn: {
        features: { dnn: snap.features.dnn },
        config: {
          activeRevision: snap.revision,
          pendingRevision: null,
          phase: 'idle',
          waitingFor: null,
          lastSwitchError: null,
          previous: null,
          active: {
            revision: snap.revision,
            values: {
              dnn: snap.features.dnn,
              python: snap.vision.python,
              venv: snap.vision.venv,
              model: snap.vision.model,
              backend: snap.vision.dnn.backend,
              device: snap.vision.dnn.device,
            },
          },
          pending: null,
        },
        envLocked: false,
        ...extra,
      },
      // Like the real /doctor: the effective source lives under vision.features.
      vision: { features: { dnn: snap.features.dnn, source: 'runtime' } },
    }
  }
}

function deps(home: string, overrides: Partial<DnnRpcDeps> = {}): DnnRpcDeps {
  return { home, env: {}, port: 39877, now: () => 5000, ...overrides }
}

async function call(
  payload: unknown,
  home: string,
  overrides: Partial<DnnRpcDeps> = {},
  peer: unknown = OWNER,
) {
  return (await handleDnnRpc(payload, peer, 39877, deps(home, overrides))) as Record<
    string,
    unknown
  >
}

afterEach(() => {
  resetDnnDownloadForTests()
  for (const dir of tempDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true })
})

describe('dnn config rpc', () => {
  it('is owner-only', async () => {
    const home = tempDir()
    const result = await call({ op: 'status' }, home, {}, OTHER)
    expect(result.ok).toBe(false)
    expect((result.error as { code: string }).code).toBe('forbidden')
    expect(fs.existsSync(snapshotPath(home))).toBe(false)
  })

  it('writes an atomic runtime snapshot and reports applied from /doctor', async () => {
    const home = tempDir()
    const result = await call({ op: 'apply', candidate: candidate() }, home, {
      fetchDoctor: echoingDoctor(home),
    })
    expect(result.ok).toBe(true)
    const value = result.value as Record<string, unknown>
    expect(value.applied).toBe(true)
    expect(value.revision).toBe(5000)
    expect(value.phase).toBe('idle')
    const snap = readSnapshot(home)
    expect(snap.version).toBe(DNN_RUNTIME_SNAPSHOT_VERSION)
    expect(snap.revision).toBe(5000)
    expect(snap.features).toEqual({ dnn: true })
    expect(snap.vision).toEqual({
      python: '',
      venv: '',
      model: 'yolov5n',
      dnn: { backend: 'opencv', device: 'cpu' },
    })
    expect(fs.readdirSync(path.join(home, 'vision')).filter((f) => f.includes('.tmp-'))).toEqual([])
  })

  it('increments the revision monotonically', async () => {
    const home = tempDir()
    await call({ op: 'apply', candidate: candidate() }, home, {
      fetchDoctor: echoingDoctor(home),
      now: () => 5000,
    })
    const second = await call(
      { op: 'apply', candidate: candidate({ features: { dnn: false } }) },
      home,
      { fetchDoctor: echoingDoctor(home), now: () => 5000 },
    )
    expect(second.ok).toBe(true)
    expect((second.value as Record<string, unknown>).revision).toBe(5001)
    expect(readSnapshot(home).features).toEqual({ dnn: false })
  })

  it('keeps the snapshot and reports savedForNextStart when Gateway is unreachable', async () => {
    const home = tempDir()
    const result = await call({ op: 'apply', candidate: candidate() }, home, {
      fetchDoctor: async () => {
        throw new Error('ECONNREFUSED 127.0.0.1:39877')
      },
    })
    expect(result.ok).toBe(false)
    const error = result.error as Record<string, unknown>
    expect(error.code).toBe('gateway_unreachable')
    expect(error.details).toMatchObject({ savedForNextStart: true, applied: false, revision: 5000 })
    expect(fs.existsSync(snapshotPath(home))).toBe(true)
  })

  it('locks features.dnn to the env value while still allowing other DNN fields', async () => {
    const home = tempDir()
    // Writing the opposite of DSH_BLENDER_DNN=on must be refused and must not write a snapshot.
    const rejected = await call(
      { op: 'apply', candidate: candidate({ features: { dnn: false } }) },
      home,
      { env: { DSH_BLENDER_DNN: 'on' } },
    )
    expect(rejected.ok).toBe(false)
    expect((rejected.error as { code: string }).code).toBe('dnn_env_locked')
    expect(fs.existsSync(snapshotPath(home))).toBe(false)

    // Other fields are still editable; the snapshot keeps features.dnn effective.
    const allowed = await call(
      {
        op: 'apply',
        candidate: candidate({
          vision: { python: '', venv: '/tmp/blender-onnx', model: 'yolov5n' },
        }),
      },
      home,
      { env: { DSH_BLENDER_DNN: 'on' }, fetchDoctor: echoingDoctor(home) },
    )
    expect(allowed.ok).toBe(true)
    expect(readSnapshot(home).features).toEqual({ dnn: true })
    expect((readSnapshot(home).vision as { venv: string }).venv).toBe('/tmp/blender-onnx')

    // A Gateway-reported lock behaves the same way.
    const remoteRejected = await call(
      { op: 'apply', candidate: candidate({ features: { dnn: false } }) },
      home,
      {
        fetchDoctor: async () => ({
          ok: true,
          dnn: { envLocked: true, features: { dnn: true }, config: {} },
        }),
      },
    )
    expect(remoteRejected.ok).toBe(false)
    expect((remoteRejected.error as { code: string }).code).toBe('dnn_env_locked')
  })

  it('keeps features.dnn locked when rollback changes the other DNN fields', async () => {
    const home = tempDir()
    writeRuntimeSnapshot(
      home,
      {
        dnn: false,
        python: '',
        venv: 'C:/venvs/current',
        model: 'yolov5n',
        backend: 'opencv',
        device: 'cpu',
      },
      { now: () => 6100 },
    )
    const doctor = async () => ({
      ok: true,
      dnn: {
        envLocked: true,
        features: { dnn: true },
        config: {
          activeRevision: 6100,
          pendingRevision: null,
          phase: 'idle',
          previous: {
            revision: 6000,
            values: {
              dnn: true,
              python: '',
              venv: 'C:/venvs/v2',
              model: 'yolov5s',
              backend: 'opencv',
              device: 'cpu',
            },
          },
          active: null,
        },
      },
    })
    const result = await call({ op: 'rollback' }, home, {
      env: { DSH_BLENDER_DNN: 'on' },
      fetchDoctor: doctor,
    })
    expect(result.ok).toBe(true)
    const snap = readSnapshot(home)
    expect(snap.features).toEqual({ dnn: false })
    expect((snap.vision as { venv: string; model: string }).venv).toBe('C:/venvs/v2')
    expect((snap.vision as { model: string }).model).toBe('yolov5s')
  })

  it('reports the env-effective features.dnn in status while keeping the saved snapshot', async () => {
    const home = tempDir()
    writeRuntimeSnapshot(
      home,
      { dnn: true, python: '', venv: '', model: 'yolov5n', backend: 'opencv', device: 'cpu' },
      { now: () => 6000 },
    )
    const result = await call({ op: 'status' }, home, {
      env: { DSH_BLENDER_DNN: 'off' },
      fetchDoctor: async () => {
        throw new Error('gateway down')
      },
    })
    expect(result.ok).toBe(true)
    const value = result.value as Record<string, unknown>
    expect(value.features).toMatchObject({ dnn: false, source: 'env', locked: true })
    expect(value.envLocked).toBe(true)
    expect((value.saved as { features: { dnn: boolean } }).features.dnn).toBe(true)
  })

  it('reads the dnn block nested under vision from a live Gateway doctor response', async () => {
    const home = tempDir()
    writeRuntimeSnapshot(
      home,
      {
        dnn: true,
        python: '',
        venv: 'C:/venvs/ort',
        model: 'yolov5s',
        backend: 'onnxruntime',
        device: 'cpu',
      },
      { now: () => 5000 },
    )
    const echo = echoingDoctor(home)
    const result = await call({ op: 'status' }, home, {
      fetchDoctor: async () => {
        const doctor = (await echo()) as { dnn?: unknown }
        // The Gateway exposes visionDoctorView() as /doctor.vision, so the DNN
        // block arrives at vision.dnn, not at the top level.
        return { ok: true, vision: { dnn: doctor.dnn } }
      },
    })
    expect(result.ok).toBe(true)
    const value = result.value as {
      config: { activeRevision: number; phase: string; active: unknown }
      features: { dnn: boolean }
      gateway: { reachable: boolean }
    }
    expect(value.gateway.reachable).toBe(true)
    expect(value.features.dnn).toBe(true)
    expect(value.config).toMatchObject({ activeRevision: 5000, phase: 'idle' })
    expect(value.config.active).toMatchObject({ revision: 5000 })
  })

  it('reports pending instead of applied while a busy detect defers the switch', async () => {
    const home = tempDir()
    const result = await call({ op: 'apply', candidate: candidate() }, home, {
      fetchDoctor: async () => ({
        ok: true,
        dnn: {
          envLocked: false,
          config: {
            activeRevision: 4999,
            pendingRevision: 5000,
            phase: 'pending',
            waitingFor: { op: 'vision_detect', startedAt: 4900, requestId: 'r1' },
            lastSwitchError: null,
            previous: null,
            active: null,
            pending: null,
          },
        },
      }),
    })
    expect(result.ok).toBe(true)
    expect(result.value).toMatchObject({
      applied: false,
      revision: 5000,
      phase: 'pending',
      activeRevision: 4999,
    })
    expect(fs.existsSync(snapshotPath(home))).toBe(true)
  })

  it('reads status from the snapshot, /doctor and the models directory', async () => {
    const home = tempDir()
    writeRuntimeSnapshot(
      home,
      {
        dnn: true,
        python: '',
        venv: '',
        model: 'yolov5n',
        backend: 'opencv',
        device: 'cpu',
      },
      { now: () => 6000 },
    )
    const modelsDir = path.join(home, 'vision', 'models')
    fs.mkdirSync(modelsDir, { recursive: true })
    fs.writeFileSync(path.join(modelsDir, 'yolov5n.onnx'), 'x')
    fs.writeFileSync(path.join(modelsDir, 'models.yml'), '%YAML:1.0\n---\n')
    const result = await call({ op: 'status' }, home, {
      fetchDoctor: async () => ({
        ok: true,
        dnn: {
          envLocked: false,
          configuredModels: ['yolov5n'],
          config: {
            activeRevision: 6000,
            pendingRevision: null,
            phase: 'idle',
            waitingFor: null,
            lastSwitchError: null,
            previous: null,
            active: null,
            pending: null,
          },
        },
      }),
    })
    expect(result.ok).toBe(true)
    const value = result.value as Record<string, unknown>
    expect(value.experimental).toBe(true)
    expect(value.saved).toMatchObject({ revision: 6000, features: { dnn: true } })
    expect(value.models).toMatchObject({ modelsDir, onnxCount: 1 })
    expect(value.config).toMatchObject({ activeRevision: 6000, phase: 'idle' })
    expect(value.downloadable).toHaveLength(5)
    expect(value.docs).toMatchObject({ url: expect.stringContaining('vision-custom-backend.md') })
    expect(value.download).toMatchObject({ state: 'idle' })
  })

  it('falls back to runtime-state.json for status when the Gateway is unreachable', async () => {
    const home = tempDir()
    fs.mkdirSync(path.join(home, 'vision'), { recursive: true })
    fs.writeFileSync(
      path.join(home, 'vision', 'runtime-state.json'),
      JSON.stringify({
        version: 1,
        updatedAt: '2026-01-01T00:00:00.000Z',
        activeRevision: 41,
        pendingRevision: 42,
        phase: 'pending',
        waitingFor: { op: 'vision_detect' },
        lastSwitchError: null,
        previous: null,
        active: {
          revision: 41,
          values: {
            dnn: true,
            python: '',
            venv: '',
            model: 'yolov5n',
            backend: 'opencv',
            device: 'cpu',
          },
        },
        pending: {
          revision: 42,
          values: {
            dnn: true,
            python: '',
            venv: '',
            model: 'yolov5s',
            backend: 'opencv',
            device: 'cpu',
          },
        },
        envLocked: false,
        source: 'runtime',
      }),
    )
    const result = await call({ op: 'status' }, home, {
      fetchDoctor: async () => {
        throw new Error('ECONNREFUSED')
      },
    })
    expect(result.ok).toBe(true)
    const value = result.value as Record<string, unknown>
    expect(value.gateway).toMatchObject({ reachable: false })
    expect(value.config).toMatchObject({
      activeRevision: 41,
      pendingRevision: 42,
      phase: 'pending',
      waitingFor: { op: 'vision_detect' },
    })
  })

  it('cancel-pending rewrites the snapshot from the active config', async () => {
    const home = tempDir()
    const result = await call({ op: 'cancel-pending' }, home, {
      fetchDoctor: async () => ({
        ok: true,
        dnn: {
          envLocked: false,
          config: {
            activeRevision: 10,
            pendingRevision: 11,
            phase: 'pending',
            waitingFor: null,
            lastSwitchError: null,
            previous: null,
            active: {
              revision: 10,
              values: {
                dnn: true,
                python: '',
                venv: '',
                model: 'yolov5s',
                backend: 'opencv',
                device: 'cpu',
              },
            },
            pending: null,
          },
        },
      }),
      now: () => 7000,
    })
    expect(result.ok).toBe(true)
    const snap = readSnapshot(home)
    expect(snap.revision).toBe(7000)
    expect((snap.vision as Record<string, unknown>).model).toBe('yolov5s')
  })

  it('rollback writes the previous configuration and fails cleanly without one', async () => {
    const home = tempDir()
    const previous = {
      dnn: true,
      python: '',
      venv: '',
      model: 'yolov5m',
      backend: 'opencv',
      device: 'cpu',
    }
    const result = await call({ op: 'rollback' }, home, {
      fetchDoctor: async () => ({
        ok: true,
        dnn: {
          envLocked: false,
          config: {
            activeRevision: 8001,
            pendingRevision: null,
            phase: 'idle',
            waitingFor: null,
            lastSwitchError: { code: 'vision_custom_env_invalid', message: 'missing cv2' },
            previous: { revision: 7999, values: previous },
            active: null,
            pending: null,
          },
        },
      }),
      now: () => 8001,
    })
    expect(result.ok).toBe(true)
    expect((readSnapshot(home).vision as Record<string, unknown>).model).toBe('yolov5m')
    expect((result.value as Record<string, unknown>).revision).toBe(8001)

    const empty = await call({ op: 'rollback' }, tempDir(), {
      fetchDoctor: async () => ({
        ok: true,
        dnn: { config: { activeRevision: 1, phase: 'idle' } },
      }),
    })
    expect(empty.ok).toBe(false)
    expect((empty.error as { code: string }).code).toBe('dnn_rollback_unavailable')
  })

  it('delegates download to the pinned downloader and returns its outcome', async () => {
    const home = tempDir()
    const result = await call({ op: 'download', ids: ['yolov5n'] }, home, {
      download: async (_ids: readonly string[]) => ({
        ok: false,
        code: 'dnn_pin_unavailable',
        message: 'pins unverified',
        items: [],
        modelsDir: path.join(home, 'vision', 'models'),
      }),
    })
    expect(result.ok).toBe(true)
    expect(result.value).toMatchObject({ ok: false, code: 'dnn_pin_unavailable' })
  })

  it('rejects unknown ops and malformed candidates without writing', async () => {
    const home = tempDir()
    const unknown = await call({ op: 'nope' }, home)
    expect(unknown.ok).toBe(false)
    expect((unknown.error as { code: string }).code).toBe('not_found')

    const bad = await call(
      { op: 'apply', candidate: candidate({ dnn: { backend: 'opencv', device: 'gpu' } }) },
      home,
    )
    expect(bad.ok).toBe(false)
    expect((bad.error as { code: string }).code).toBe('invalid_params')
    expect(fs.existsSync(snapshotPath(home))).toBe(false)
  })

  it('exposes snapshot read validation', () => {
    const home = tempDir()
    const missing = readRuntimeSnapshot(home)
    expect(missing.ok).toBe(false)
    if (missing.ok) throw new Error('unreachable')
    expect(missing.code).toBe('dnn_config_missing')
    fs.mkdirSync(path.join(home, 'vision'), { recursive: true })
    fs.writeFileSync(snapshotPath(home), '{oops')
    const broken = readRuntimeSnapshot(home)
    expect(broken.ok).toBe(false)
    if (broken.ok) throw new Error('unreachable')
    expect(broken.code).toBe('dnn_config_invalid')
  })

  it('ignores an invalid DSH_BLENDER_DNN (maybe/yes) instead of locking DNN off', async () => {
    for (const raw of ['maybe', 'yes']) {
      const home = tempDir()
      writeRuntimeSnapshot(
        home,
        { dnn: true, python: '', venv: '', model: 'yolov5n', backend: 'opencv', device: 'cpu' },
        { now: () => 6000 },
      )
      const status = await call({ op: 'status' }, home, {
        env: { DSH_BLENDER_DNN: raw },
        fetchDoctor: async () => {
          throw new Error('gateway down')
        },
      })
      expect(status.ok).toBe(true)
      const value = status.value as Record<string, unknown>
      expect(value.features).toMatchObject({
        dnn: true,
        locked: false,
        invalidEnv: raw,
      })
      expect((value.features as { source: string }).source).not.toBe('env')
      expect(value.envLocked).toBe(false)

      // Not a lock: apply is allowed to write the candidate.
      const applied = await call(
        { op: 'apply', candidate: candidate({ features: { dnn: false } }) },
        home,
        { env: { DSH_BLENDER_DNN: raw }, fetchDoctor: echoingDoctor(home) },
      )
      expect(applied.ok).toBe(true)
      expect(readSnapshot(home).features).toEqual({ dnn: false })
    }
  })

  it('passes the doctor vision.features.source through unchanged in status', async () => {
    const home = tempDir()
    writeRuntimeSnapshot(
      home,
      { dnn: true, python: '', venv: '', model: 'yolov5n', backend: 'opencv', device: 'cpu' },
      { now: () => 6000 },
    )
    const result = await call({ op: 'status' }, home, {
      fetchDoctor: async () => ({
        ok: true,
        vision: {
          features: { dnn: true, source: 'home:/home/u/.dsh/dsh-blender' },
          dnn: {
            features: { dnn: true },
            config: { activeRevision: 6000, phase: 'idle', pendingRevision: null },
          },
        },
      }),
    })
    expect(result.ok).toBe(true)
    const value = result.value as { features: { source: string } }
    expect(value.features.source).toBe('home:/home/u/.dsh/dsh-blender')
  })

  it('never collides revisions when two applies race in the same millisecond', async () => {
    const home = tempDir()
    const doctor = async () => ({
      ok: true,
      dnn: { config: { activeRevision: 4999, pendingRevision: null, phase: 'idle' } },
    })
    const results = await Promise.all([
      call({ op: 'apply', candidate: candidate() }, home, {
        fetchDoctor: doctor,
        now: () => 5000,
      }),
      call(
        {
          op: 'apply',
          candidate: candidate({ vision: { python: '', venv: '', model: 'yolov5s' } }),
        },
        home,
        {
          fetchDoctor: doctor,
          now: () => 5000,
        },
      ),
    ])
    const revisions = results
      .map((result) => (result.value as { revision: number }).revision)
      .sort((a, b) => a - b)
    expect(results.every((result) => result.ok)).toBe(true)
    expect(revisions).toEqual([5000, 5001])
    expect(readSnapshot(home).revision).toBe(5001)
  })

  it('reports the newer pending revision when a second apply overwrites the first', async () => {
    const home = tempDir()
    const doctor = async () => {
      const current = fs.existsSync(snapshotPath(home)) ? readSnapshot(home) : null
      const pendingRevision = current ? current.revision : null
      return {
        ok: true,
        dnn: {
          config: {
            activeRevision: 4999,
            pendingRevision,
            phase: 'pending',
            waitingFor: { op: 'vision_detect' },
          },
        },
      }
    }
    const first = await call({ op: 'apply', candidate: candidate() }, home, {
      fetchDoctor: doctor,
      now: () => 5000,
    })
    expect((first.value as { pendingRevision: number }).pendingRevision).toBe(5000)
    const second = await call(
      { op: 'apply', candidate: candidate({ vision: { python: '', venv: '', model: 'yolov5s' } }) },
      home,
      { fetchDoctor: doctor, now: () => 5000 },
    )
    expect(second.ok).toBe(true)
    expect(second.value).toMatchObject({
      applied: false,
      revision: 5001,
      pendingRevision: 5001,
      phase: 'pending',
    })
    expect(readSnapshot(home).revision).toBe(5001)
  })

  it('deduplicates download ids and rejects more than five unique models', async () => {
    const home = tempDir()
    const seen: string[][] = []
    const download = async (ids: readonly string[]) => {
      seen.push([...ids])
      return {
        ok: true as const,
        items: ids.map((id) => ({ id, state: 'already' as const, path: '', bytes: 1 })),
        modelsDir: path.join(home, 'vision', 'models'),
      }
    }
    const deduped = await call(
      { op: 'download', ids: ['yolov5n', 'yolov5n', 'yolov5s', 'yolov5n'] },
      home,
      { download },
    )
    expect(deduped.ok).toBe(true)
    expect(seen).toEqual([['yolov5n', 'yolov5s']])

    const tooMany = await call(
      {
        op: 'download',
        ids: ['yolov5n', 'yolov5s', 'yolov5m', 'yolov5l', 'yolov5x', 'extra'],
      },
      home,
      { download },
    )
    expect(tooMany.ok).toBe(false)
    expect((tooMany.error as { code: string }).code).toBe('invalid_params')
    expect(seen).toHaveLength(1)
  })

  it('reconciles applied only when the revision is active and the phase is idle', async () => {
    const home = tempDir()
    for (const [phase, activeRevision] of [
      ['switching', 5000],
      ['failed', 5000],
      ['idle', 4999],
    ] as const) {
      const result = await call({ op: 'apply', candidate: candidate() }, home, {
        fetchDoctor: async () => ({
          ok: true,
          dnn: {
            config: {
              activeRevision,
              pendingRevision: 5000,
              phase,
              lastSwitchError:
                phase === 'failed' ? { code: 'dnn_config_switch_failed', message: 'boom' } : null,
            },
          },
        }),
        now: () => 5000,
      })
      expect(result.ok).toBe(true)
      expect((result.value as { applied: boolean }).applied).toBe(false)
    }
  })

  it('passes the env lock into probe and reports the invalid raw value', async () => {
    const home = tempDir()
    const captured: Array<Record<string, unknown>> = []
    const probe = async (options: Record<string, unknown>) => {
      captured.push(options)
      return { ok: true, checks: [], summary: { pass: 0, warn: 0, fail: 0 }, elapsedMs: 1 }
    }
    const locked = await call({ op: 'probe', candidate: candidate() }, home, {
      env: { DSH_BLENDER_DNN: 'off' },
      probe,
      fetchDoctor: async () => {
        throw new Error('gateway down')
      },
    })
    expect(locked.ok).toBe(true)
    expect(captured[0]).toMatchObject({ envLocked: true, envDnnValue: false })

    const invalid = await call({ op: 'probe', candidate: candidate() }, home, {
      env: { DSH_BLENDER_DNN: 'maybe' },
      probe,
      fetchDoctor: async () => {
        throw new Error('gateway down')
      },
    })
    expect(invalid.ok).toBe(true)
    expect(captured[1]).toMatchObject({ envLocked: false, envDnnValue: null })
  })

  it('returns gateway_not_configured instead of gateway_unreachable when no port exists', async () => {
    const dispatch = dispatchVirtualGuiRpc(undefined)
    const result = (await dispatch('dnn', { op: 'status' }, undefined, OWNER)) as {
      ok: boolean
      error: { code: string }
    }
    expect(result.ok).toBe(false)
    expect(result.error.code).toBe('gateway_not_configured')

    const zero = dispatchVirtualGuiRpc(0)
    const status = (await zero('dnn', { op: 'status' }, undefined, OWNER)) as {
      ok: boolean
      error: { code: string }
    }
    expect(status.error.code).toBe('gateway_not_configured')
  })
})
