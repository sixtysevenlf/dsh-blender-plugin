import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  DEFAULT_DNN_VALUES,
  RUNTIME_STATE_FILE,
  createRuntimeConfigStore,
  nextRevision,
  parseRuntimeSnapshot,
  readRuntimeSnapshot,
  writeRuntimeSnapshot,
} from '../../../runtime/vision/runtime-config.js'

// runtime-config.ts is the Gateway-side authority for the hot-applied vision
// snapshot: parse/validate runtime.json, merge it over plugin/file config, and
// run the pending -> switching -> active state machine. Everything here is
// filesystem-only (no Python, no child processes).

let home: string

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-vision-config-'))
})

afterEach(() => {
  fs.rmSync(home, { recursive: true, force: true })
})

function snapshotFile(
  revision: number,
  values: Partial<Record<string, unknown>> = {},
  dnn = false,
): string {
  const file = path.join(home, 'vision', 'runtime.json')
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(
    file,
    JSON.stringify({
      version: 1,
      revision,
      updatedAt: new Date(revision * 1000).toISOString(),
      features: { dnn },
      vision: {
        python: '',
        venv: '',
        model: '',
        dnn: { backend: 'opencv', device: 'cpu' },
        ...values,
      },
    }),
    'utf8',
  )
  return file
}

function store(extra: Record<string, unknown> = {}) {
  return createRuntimeConfigStore({ homeDir: home, env: {}, now: () => 1000, ...extra })
}

describe('parseRuntimeSnapshot', () => {
  it('parses the canonical runtime.json shape', () => {
    const parsed = parseRuntimeSnapshot({
      version: 1,
      revision: 7,
      updatedAt: '2026-11-11T00:00:00.000Z',
      features: { dnn: true },
      vision: {
        python: '',
        venv: 'C:/venv',
        model: 'yolov5s.onnx',
        dnn: { backend: 'onnxruntime', device: 'gpu' },
      },
    })
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.snapshot.revision).toBe(7)
    expect(parsed.snapshot.values).toEqual({
      dnn: true,
      python: '',
      venv: 'C:/venv',
      model: 'yolov5s.onnx',
      backend: 'onnxruntime',
      device: 'gpu',
    })
  })

  it('rejects an unknown version, a bad revision and an invalid backend', () => {
    const cases: unknown[] = [
      { version: 2, revision: 1, updatedAt: 'x' },
      { version: 1, revision: 0, updatedAt: 'x' },
      { version: 1, revision: 1, updatedAt: 'x', vision: { dnn: { backend: 'cuda' } } },
      { version: 1, revision: 1, updatedAt: 'x', vision: { dnn: { device: 'tpu' } } },
      { version: 1, revision: 1, updatedAt: 'x', features: { dnn: 'yes' } },
    ]
    for (const raw of cases) {
      const parsed = parseRuntimeSnapshot(raw)
      expect(parsed.ok).toBe(false)
      if (!parsed.ok) expect(parsed.error.code).toBe('dnn_config_invalid')
    }
  })

  it('accepts a top-level dnn block for forward compatibility', () => {
    const parsed = parseRuntimeSnapshot({
      version: 1,
      revision: 3,
      updatedAt: 'x',
      dnn: { backend: 'opencv', device: 'auto' },
    })
    expect(parsed.ok).toBe(true)
    if (parsed.ok) expect(parsed.snapshot.values.device).toBe('auto')
  })

  it('treats missing optional fields as unset, not as an error', () => {
    const parsed = parseRuntimeSnapshot({ version: 1, revision: 2, updatedAt: 'x' })
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.snapshot.values.python).toBe('')
    expect(parsed.snapshot.values.backend).toBe('opencv')
    expect(parsed.snapshot.values.device).toBe('cpu')
  })
})

describe('readRuntimeSnapshot / writeRuntimeSnapshot', () => {
  it('round-trips the canonical shape and creates the vision directory', () => {
    const file = path.join(home, 'vision', 'runtime.json')
    writeRuntimeSnapshot(home, {
      version: 1,
      revision: 4,
      updatedAt: '2026-11-11T00:00:00.000Z',
      values: { ...DEFAULT_DNN_VALUES, dnn: true, model: 'm.onnx' },
    })
    const raw = JSON.parse(fs.readFileSync(file, 'utf8'))
    expect(raw.version).toBe(1)
    expect(raw.revision).toBe(4)
    expect(raw.features).toEqual({ dnn: true })
    expect(raw.vision.model).toBe('m.onnx')
    expect(raw.vision.dnn).toEqual({ backend: 'opencv', device: 'cpu' })
    const read = readRuntimeSnapshot(home)
    expect(read.ok).toBe(true)
    if (read.ok) expect(read.snapshot?.revision).toBe(4)
  })

  it('returns a null snapshot when the file is absent', () => {
    const read = readRuntimeSnapshot(home)
    expect(read.ok).toBe(true)
    if (read.ok) expect(read.snapshot).toBeNull()
  })

  it('reports corrupt JSON as dnn_config_invalid with the file path', () => {
    const file = path.join(home, 'vision', 'runtime.json')
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, '{not json', 'utf8')
    const read = readRuntimeSnapshot(home)
    expect(read.ok).toBe(false)
    if (!read.ok) {
      expect(read.error.code).toBe('dnn_config_invalid')
      expect(read.error.message).toContain('runtime.json')
    }
  })
})

describe('nextRevision', () => {
  it('is monotonic even when the clock moves backwards', () => {
    expect(nextRevision(10, 5)).toBe(11)
    expect(nextRevision(10, 100)).toBe(100)
    expect(nextRevision(0, 0)).toBe(1)
  })
})

describe('createRuntimeConfigStore', () => {
  it('starts from built-in defaults without touching the worker', () => {
    const s = store()
    const st = s.getState()
    expect(st.active?.values).toEqual(DEFAULT_DNN_VALUES)
    expect(st.activeRevision).toBe(0)
    expect(st.phase).toBe('idle')
    expect(st.envLocked).toBe(false)
    expect(s.reconcile({ workerRunning: false }).status).toBe('idle')
  })

  it('merges file < plugin < snapshot with empty snapshot fields falling back', () => {
    const s = store({
      fileConfig: { vision: { model: 'file.onnx', venv: 'file-venv' } },
      pluginConfig: { vision: { model: 'plugin.onnx' } },
    })
    snapshotFile(2, { model: 'snapshot.onnx' })
    const out = s.reconcile({ workerRunning: false })
    expect(out.status).toBe('committed')
    expect(s.getState().active?.values.model).toBe('snapshot.onnx')
    // venv only set by the file config; snapshot left it empty
    expect(s.getState().active?.values.venv).toBe('file-venv')
  })

  it('treats an explicit null as clearing a lower layer', () => {
    const s = store({
      fileConfig: { vision: { python: 'C:/py/python.exe', venv: 'file-venv', model: 'file.onnx' } },
    })
    snapshotFile(2, { python: null, venv: null, model: null })
    const out = s.reconcile({ workerRunning: false })
    expect(out.status).toBe('committed')
    const values = s.getState().active?.values
    // null = 显式清除 → 回落到插件内置默认（空字符串 = 托管 venv）。
    expect(values?.python).toBe('')
    expect(values?.venv).toBe('')
    expect(values?.model).toBe('')
  })

  it('round-trips cleared fields as null in runtime.json', () => {
    const parsed = parseRuntimeSnapshot({
      version: 1,
      revision: 2,
      updatedAt: 'x',
      vision: { python: '', venv: null, model: '' },
    })
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.snapshot.cleared).toEqual(['venv'])
    expect(parsed.snapshot.values.venv).toBe('')
  })

  it('rejects model ids that are paths or URLs', () => {
    for (const model of ['../evil.onnx', 'https://example.com/y.onnx', 'a b.onnx']) {
      const parsed = parseRuntimeSnapshot({
        version: 1,
        revision: 1,
        updatedAt: 'x',
        vision: { model },
      })
      expect(parsed.ok).toBe(false)
      if (!parsed.ok) expect(parsed.error.code).toBe('dnn_config_invalid')
    }
  })

  it('locks features.dnn from DSH_BLENDER_DNN and reports envLocked', () => {
    snapshotFile(1, {}, false)
    const on = createRuntimeConfigStore({ homeDir: home, env: { DSH_BLENDER_DNN: 'on' } })
    expect(on.reconcile({ workerRunning: false }).status).toBe('committed')
    expect(on.getState().active?.values.dnn).toBe(true)
    expect(on.getState().envLocked).toBe(true)

    const off = createRuntimeConfigStore({ homeDir: home, env: { DSH_BLENDER_DNN: 'off' } })
    expect(off.reconcile({ workerRunning: false }).status).toBe('committed')
    expect(off.getState().active?.values.dnn).toBe(false)
    expect(off.getState().envLocked).toBe(true)
  })

  it('ignores an invalid DSH_BLENDER_DNN value instead of locking a bad state', () => {
    const s = createRuntimeConfigStore({ homeDir: home, env: { DSH_BLENDER_DNN: 'maybe' } })
    s.reconcile({ workerRunning: false })
    expect(s.getState().envLocked).toBe(false)
  })

  it('ignores a stale revision without changing the active configuration', () => {
    snapshotFile(5)
    const s = store()
    expect(s.reconcile({ workerRunning: false }).status).toBe('committed')
    expect(s.getState().activeRevision).toBe(5)

    snapshotFile(4)
    const out = s.reconcile({ workerRunning: false })
    expect(out.status).toBe('stale')
    expect(s.getState().activeRevision).toBe(5)
  })

  it('commits a model-only change immediately even while the worker runs', () => {
    snapshotFile(5, { model: 'a.onnx' }, true)
    const s = store()
    s.reconcile({ workerRunning: true })
    snapshotFile(6, { model: 'b.onnx' }, true)
    const out = s.reconcile({ workerRunning: true })
    expect(out.status).toBe('committed')
    expect(out.needsWorkerRestart).toBe(false)
    expect(s.getState().activeRevision).toBe(6)
    expect(s.getState().previousRevision).toBe(5)
    expect(s.getState().phase).toBe('idle')
  })

  it('requires a two-phase switch for a backend change while DNN is on', () => {
    snapshotFile(5, {}, true)
    const s = store()
    s.reconcile({ workerRunning: true })
    snapshotFile(6, { dnn: { backend: 'onnxruntime', device: 'cpu' } }, true)
    const out = s.reconcile({ workerRunning: true })
    expect(out.status).toBe('switch_required')
    expect(out.needsWorkerRestart).toBe(true)
    expect(out.reason).toBe('backend')
    expect(s.getState().phase).toBe('pending')
    expect(s.getState().pendingRevision).toBe(6)
    expect(s.getState().activeRevision).toBe(5)

    s.beginSwitch()
    expect(s.getState().phase).toBe('switching')
    s.commitActive()
    const st = s.getState()
    expect(st.phase).toBe('idle')
    expect(st.activeRevision).toBe(6)
    expect(st.pendingRevision).toBeNull()
    expect(st.previousRevision).toBe(5)
  })

  it('keeps the pending revision while requests are in flight', () => {
    snapshotFile(5, {}, true)
    const s = store()
    s.reconcile({ workerRunning: true })
    snapshotFile(6, { dnn: { backend: 'onnxruntime', device: 'cpu' } }, true)
    const out = s.reconcile({
      workerRunning: true,
      inFlight: { op: 'detect', startedAt: '2026-11-11T00:00:01.000Z', requestId: 'r1' },
    })
    expect(out.status).toBe('pending')
    expect(s.getState().phase).toBe('pending')
    expect(s.getState().waitingFor).toEqual({
      op: 'detect',
      startedAt: '2026-11-11T00:00:01.000Z',
      requestId: 'r1',
    })
  })

  it('commits a change that needs no worker restart while a switch is pending', () => {
    snapshotFile(5, { model: 'a.onnx' }, true)
    const s = store()
    s.reconcile({ workerRunning: true })
    snapshotFile(6, { model: 'b.onnx', dnn: { backend: 'onnxruntime', device: 'cpu' } }, true)
    expect(s.reconcile({ workerRunning: true }).status).toBe('switch_required')
    expect(s.getState().pendingRevision).toBe(6)

    snapshotFile(7, { model: 'c.onnx' }, true)
    const out = s.reconcile({ workerRunning: true })
    expect(out.status).toBe('committed')
    expect(s.getState().activeRevision).toBe(7)
    expect(s.getState().pendingRevision).toBeNull()
    expect(s.getState().phase).toBe('idle')
  })

  it('does not restart the worker when DNN is turned off', () => {
    snapshotFile(5, {}, true)
    const s = store()
    s.reconcile({ workerRunning: true })
    snapshotFile(6, {}, false)
    const out = s.reconcile({ workerRunning: true })
    expect(out.status).toBe('committed')
    expect(out.reason).toBe('dnn')
    expect(out.needsWorkerRestart).toBe(false)
    expect(s.getState().active?.values.dnn).toBe(false)
  })

  it('fails closed on an invalid snapshot without touching the active configuration', () => {
    snapshotFile(5, {}, true)
    const s = store()
    s.reconcile({ workerRunning: false })
    const file = path.join(home, 'vision', 'runtime.json')
    fs.writeFileSync(
      file,
      JSON.stringify({
        version: 1,
        revision: 6,
        updatedAt: 'x',
        vision: { dnn: { backend: 'cuda' } },
      }),
      'utf8',
    )
    const out = s.reconcile({ workerRunning: false })
    expect(out.status).toBe('invalid')
    const st = s.getState()
    expect(st.phase).toBe('failed')
    expect(st.lastSwitchError?.code).toBe('dnn_config_invalid')
    expect(st.activeRevision).toBe(5)
  })

  it('keeps a failed switch visible instead of retrying on every reconcile', () => {
    snapshotFile(5, {}, true)
    const s = store()
    s.reconcile({ workerRunning: true })
    snapshotFile(6, { venv: 'C:/other-venv' }, true)
    expect(s.reconcile({ workerRunning: true }).status).toBe('switch_required')
    s.beginSwitch()
    s.failSwitch('dnn_config_switch_failed', 'worker did not exit')
    const st = s.getState()
    expect(st.phase).toBe('failed')
    expect(st.pendingRevision).toBe(6)
    expect(st.lastSwitchError?.code).toBe('dnn_config_switch_failed')

    const again = s.reconcile({ workerRunning: true })
    expect(again.status).toBe('failed')
    expect(s.getState().activeRevision).toBe(5)
  })

  it('persists a host-readable runtime-state.json after each transition', () => {
    snapshotFile(5, {}, true)
    const s = store()
    s.reconcile({ workerRunning: true })
    snapshotFile(6, { venv: 'C:/other-venv' }, true)
    s.reconcile({
      workerRunning: true,
      inFlight: { op: 'analyze', startedAt: '2026-11-11T00:00:02.000Z', requestId: 'r9' },
    })
    const file = path.join(home, 'vision', RUNTIME_STATE_FILE)
    const raw = JSON.parse(fs.readFileSync(file, 'utf8'))
    expect(raw.version).toBe(1)
    expect(raw.phase).toBe('pending')
    expect(raw.activeRevision).toBe(5)
    expect(raw.pendingRevision).toBe(6)
    expect(raw.waitingFor.requestId).toBe('r9')
    expect(raw.active.values.model).toBe('')
    expect(raw.previousRevision).toBeNull()
    expect(Array.isArray(raw.docs)).toBe(true)
    expect(raw.docs.length).toBeGreaterThan(0)
  })

  it('records the previous active revision after a committed switch', () => {
    snapshotFile(5, {}, true)
    const s = store()
    s.reconcile({ workerRunning: true })
    snapshotFile(6, { venv: 'C:/other-venv' }, true)
    expect(s.reconcile({ workerRunning: true }).status).toBe('switch_required')
    s.beginSwitch()
    s.commitActive()
    const st = s.getState()
    expect(st.activeRevision).toBe(6)
    expect(st.previousRevision).toBe(5)
    expect(st.previous?.revision).toBe(5)
    const raw = JSON.parse(fs.readFileSync(path.join(home, 'vision', RUNTIME_STATE_FILE), 'utf8'))
    expect(raw.previousRevision).toBe(5)
    expect(raw.previous.values.venv).toBe('')
  })
})
