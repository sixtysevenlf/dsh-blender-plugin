import { afterEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  type DnnConfigValues,
  createRuntimeConfigStore,
  writeRuntimeSnapshot,
} from '../../../runtime/vision/runtime-config.js'
import {
  VISION_DOCS_URL,
  VISION_OPS,
  createVisionDispatch,
  deriveDnnStatus,
} from '../../../runtime/vision/dispatch.js'

// vision_* dispatch: host-local status/help/pass must never touch the worker;
// worker-backed ops are validated and gated in the DNN gate order before a single
// request reaches the manager. Manager and worker are fakes — no Python runs.

const DEFAULT_VALUES: DnnConfigValues = {
  dnn: false,
  python: '',
  venv: '',
  model: '',
  backend: 'opencv',
  device: 'cpu',
}

interface Harness {
  dir: string
  store: ReturnType<typeof createRuntimeConfigStore>
  calls: Array<{ op: string; args?: Record<string, unknown> }>
  results: Record<string, unknown>
  dispatch: ReturnType<typeof createVisionDispatch>
  manager: {
    request: (op: string, args?: Record<string, unknown>) => Promise<unknown>
    status: () => Record<string, unknown>
    dnnStatus: () => ReturnType<typeof deriveDnnStatus>
  }
}

const dirs: string[] = []

function tmpHome(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-vision-dispatch-'))
  dirs.push(dir)
  return dir
}

afterEach(() => {
  while (dirs.length) fs.rmSync(dirs.pop() as string, { recursive: true, force: true })
})

function makeHarness(
  opts: {
    dnn?: boolean
    values?: Partial<DnnConfigValues>
    revision?: number
    models?: string[]
    image?: string
    manager?: boolean
    statusThrows?: boolean
  } = {},
): Harness {
  const dir = tmpHome()
  const values: DnnConfigValues = {
    ...DEFAULT_VALUES,
    ...(opts.dnn === undefined ? {} : { dnn: opts.dnn }),
    ...opts.values,
  }
  writeRuntimeSnapshot(dir, {
    version: 1,
    revision: opts.revision ?? 1,
    updatedAt: new Date(0).toISOString(),
    values,
  })
  // 环境变量不覆盖 dnn，让 snapshot 的 features.dnn 决定门。
  const store = createRuntimeConfigStore({ homeDir: dir, env: {} })
  store.reconcile({ workerRunning: false })
  const modelsDir = path.join(dir, 'vision', 'models')
  fs.mkdirSync(modelsDir, { recursive: true })
  for (const name of opts.models ?? []) {
    const file = path.join(modelsDir, name)
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, 'fake-onnx')
  }
  for (const sub of ['images']) fs.mkdirSync(path.join(dir, sub), { recursive: true })

  const calls: Array<{ op: string; args?: Record<string, unknown> }> = []
  const results: Record<string, unknown> = {}
  const manager = {
    request: async (op: string, args?: Record<string, unknown>) => {
      calls.push({ op, args })
      return results[op] ?? { echoed: op }
    },
    status: () => {
      if (opts.statusThrows) throw new Error('manager exploded')
      return {
        worker: {
          state: 'ready',
          pid: 4242,
          startedAt: '2026-11-11T00:00:00.000Z',
          protocolVersion: 1,
          alive: true,
          restarts: 0,
          lastStartError: null,
          idleSince: null,
        },
        inFlight: 0,
      }
    },
    dnnStatus: () => deriveDnnStatus(store),
  }
  const dispatch = createVisionDispatch({
    homeDir: dir,
    store,
    manager: opts.manager === false ? undefined : manager,
    cwd: () => dir,
    modelCacheTtlMs: 0,
  })
  return { dir, store, calls, results, dispatch, manager }
}

function imageFile(dir: string, name = 'shot.png'): string {
  const file = path.join(dir, 'images', name)
  fs.writeFileSync(file, 'not-really-a-png')
  return file
}

function rec(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>
}

describe('vision dispatch routing', () => {
  it('exposes exactly the 8 catalog op names', () => {
    const { dispatch } = makeHarness()
    expect([...dispatch.ops]).toEqual([...VISION_OPS])
    expect(VISION_OPS).toHaveLength(8)
  })

  it('treats an unknown vision op as an input error instead of falling through', async () => {
    const { dispatch, calls } = makeHarness()
    const out = rec(await dispatch.handle('vision_nope', {}))
    expect(out).toMatchObject({ ok: false, stage: 'input', code: 'vision_input_invalid' })
    expect(calls).toHaveLength(0)
  })

  it('serves vision_help locally without a manager or worker request', async () => {
    const h = makeHarness({ manager: false })
    const out = rec(await h.dispatch.handle('vision_help'))
    expect(out).toMatchObject({ ok: true, op: 'vision_help' })
    const text = String(out.text)
    expect(text).toContain('vision_pass')
    expect(text).toContain('models.yml')
    expect(text).toContain('64 MP')
    expect(text).toContain(VISION_DOCS_URL)
    expect(h.calls).toHaveLength(0)
  })

  it('wires vision_pass to the host-local pass reader (no manager needed)', async () => {
    const h = makeHarness({ manager: false })
    const out = rec(await h.dispatch.handle('vision_pass', { manifests: [] }))
    expect(out).toMatchObject({
      ok: false,
      op: 'vision_pass',
      stage: 'input',
      code: 'vision_input_invalid',
    })
    expect(h.calls).toHaveLength(0)
  })

  it('reports status without starting a worker', async () => {
    const h = makeHarness({ dnn: true, models: ['yolov5n.onnx'], values: { model: 'yolov5n' } })
    const out = rec(await h.dispatch.handle('vision_status'))
    expect(out.ok).toBe(true)
    expect(out.op).toBe('vision_status')
    expect(out.home).toBe(h.dir)
    const worker = rec(out.worker)
    expect(worker.state).toBe('ready')
    expect(worker.pid).toBe(4242)
    const dnn = rec(out.dnn)
    expect(dnn.experimental).toBe(true)
    expect(dnn.enabled).toBe(true)
    expect(dnn.backend).toBe('opencv')
    expect(rec(dnn.device)).toMatchObject({ requested: 'cpu', used: 'cpu' })
    expect(rec(dnn.docs)).toMatchObject({ url: VISION_DOCS_URL })
    expect(String(rec(dnn.docs).localPath).replace(/\\/g, '/')).toContain(
      'vision/models/GPU-DIY.md',
    )
    expect(rec(dnn.models).available).toEqual(['yolov5n.onnx'])
    expect(rec(dnn.config)).toMatchObject({ activeRevision: 1, phase: 'idle' })
    expect(h.calls).toHaveLength(0)
  })

  it('keeps status available when the manager status throws', async () => {
    const h = makeHarness({ statusThrows: true })
    const out = rec(await h.dispatch.handle('vision_status'))
    expect(out.ok).toBe(true)
    expect(rec(out.worker).state).toBe('stopped')
    expect(rec(out.worker).pid).toBeNull()
  })
})

describe('vision_analyze / vision_compare / vision_track validation', () => {
  it('delegates analyze with an absolute path and bounded defaults', async () => {
    const h = makeHarness({ dnn: true })
    const file = imageFile(h.dir)
    const out = rec(await h.dispatch.handle('vision_analyze', { path: file }))
    expect(out).toMatchObject({ ok: true, op: 'vision_analyze', configRevision: 1 })
    expect(h.calls).toHaveLength(1)
    expect(h.calls[0].op).toBe('analyze')
    expect(h.calls[0].args).toMatchObject({ path: file, maxContours: 32 })
    expect(h.calls[0].args?.roi).toBeUndefined()
  })

  it('rejects a missing image before touching the worker', async () => {
    const h = makeHarness({ dnn: true })
    const out = rec(
      await h.dispatch.handle('vision_analyze', { path: path.join(h.dir, 'nope.png') }),
    )
    expect(out).toMatchObject({ ok: false, stage: 'input', code: 'vision_invalid_image' })
    expect(h.calls).toHaveLength(0)
  })

  it('rejects URLs, globs and script paths', async () => {
    const h = makeHarness({ dnn: true })
    for (const bad of ['https://example.com/a.png', 'C:/shots/*.png', '$(rm -rf /)']) {
      const out = rec(await h.dispatch.handle('vision_analyze', { path: bad }))
      expect(out.ok).toBe(false)
      expect(out.stage).toBe('input')
      expect(h.calls).toHaveLength(0)
    }
  })

  it('rejects an invalid roi and an out-of-range maxContours', async () => {
    const h = makeHarness({ dnn: true })
    const file = imageFile(h.dir)
    const badRoi = rec(
      await h.dispatch.handle('vision_analyze', { path: file, roi: { x: -1, y: 0, w: 4, h: 4 } }),
    )
    expect(badRoi).toMatchObject({ ok: false, stage: 'input', code: 'vision_input_invalid' })
    const badMax = rec(await h.dispatch.handle('vision_analyze', { path: file, maxContours: 999 }))
    expect(badMax).toMatchObject({ ok: false, stage: 'input', code: 'vision_input_invalid' })
    expect(h.calls).toHaveLength(0)
  })

  it('delegates compare with both absolute paths and the default alignment', async () => {
    const h = makeHarness()
    const ref = imageFile(h.dir, 'ref.png')
    const candidate = imageFile(h.dir, 'candidate.png')
    const out = rec(await h.dispatch.handle('vision_compare', { ref, candidate }))
    expect(out).toMatchObject({ ok: true, op: 'vision_compare', configRevision: 1 })
    expect(h.calls[0]).toEqual({ op: 'compare', args: { ref, candidate, align: 'ecc' } })
  })

  it('rejects a bad compare alignment and a missing candidate', async () => {
    const h = makeHarness()
    const ref = imageFile(h.dir, 'ref.png')
    const candidate = imageFile(h.dir, 'candidate.png')
    const badAlign = rec(
      await h.dispatch.handle('vision_compare', { ref, candidate, align: 'sift' }),
    )
    expect(badAlign).toMatchObject({ ok: false, stage: 'input', code: 'vision_input_invalid' })
    const missing = rec(
      await h.dispatch.handle('vision_compare', { ref, candidate: path.join(h.dir, 'nope.png') }),
    )
    expect(missing).toMatchObject({ ok: false, stage: 'input', code: 'vision_invalid_image' })
    expect(h.calls).toHaveLength(0)
  })

  it('delegates track with absolute frame paths and the default method', async () => {
    const h = makeHarness()
    const frames = [imageFile(h.dir, 'f0.png'), imageFile(h.dir, 'f1.png')]
    const out = rec(await h.dispatch.handle('vision_track', { frames }))
    expect(out).toMatchObject({ ok: true, op: 'vision_track' })
    expect(h.calls[0]).toEqual({ op: 'track', args: { frames, method: 'farneback' } })
  })

  it('rejects empty frames, too many frames and an unknown method', async () => {
    const h = makeHarness()
    const frame = imageFile(h.dir, 'f0.png')
    expect(await h.dispatch.handle('vision_track', { frames: [] })).toMatchObject({
      code: 'vision_input_invalid',
    })
    expect(
      await h.dispatch.handle('vision_track', { frames: Array.from({ length: 121 }, () => frame) }),
    ).toMatchObject({ code: 'vision_input_invalid' })
    expect(
      await h.dispatch.handle('vision_track', { frames: [frame], method: 'dense' }),
    ).toMatchObject({
      code: 'vision_input_invalid',
    })
    expect(h.calls).toHaveLength(0)
  })
})

describe('vision_detect host gate order (task 4.4)', () => {
  it('short-circuits on dnn_disabled before any worker or model check', async () => {
    const h = makeHarness({ dnn: false })
    const file = imageFile(h.dir)
    const out = rec(await h.dispatch.handle('vision_detect', { path: file }))
    expect(out).toMatchObject({
      ok: false,
      op: 'vision_detect',
      stage: 'config',
      code: 'dnn_disabled',
      retryable: false,
    })
    expect(String(out.fix)).toContain('features.dnn')
    expect(h.calls).toHaveLength(0)
  })

  it('reports dnn_config_switching while a switch is pending, before model checks', async () => {
    const h = makeHarness({ dnn: true })
    fs.mkdirSync(path.join(h.dir, 'v2'))
    // 切到一个需要重启 worker 的 backend，并制造在途请求 → pending。
    writeRuntimeSnapshot(h.dir, {
      version: 1,
      revision: 2,
      updatedAt: new Date(0).toISOString(),
      values: {
        ...DEFAULT_VALUES,
        dnn: true,
        backend: 'onnxruntime',
        venv: path.join(h.dir, 'v2'),
      },
    })
    h.store.reconcile({
      workerRunning: true,
      inFlight: { op: 'analyze', startedAt: '2026-11-11T00:00:01.000Z', requestId: 'r1' },
    })
    expect(h.store.getState().phase).toBe('pending')
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({
      ok: false,
      stage: 'config',
      code: 'dnn_config_switching',
      retryable: true,
    })
    expect(out.evidence).toMatchObject({ activeRevision: 1, pendingRevision: 2 })
    expect(rec(out.evidence).waitingFor).toMatchObject({ op: 'analyze', requestId: 'r1' })
    expect(h.calls).toHaveLength(0)
  })

  it('does not gate detect on dnn_config_invalid (keep the previous active config)', async () => {
    const h = makeHarness({ dnn: true, models: ['yolo.onnx'], values: { model: 'yolo' } })
    h.store.reconcile({ workerRunning: true })
    h.store.failSwitch('dnn_config_invalid', 'runtime.json is not valid JSON')
    expect(h.store.getState().phase).toBe('failed')
    h.results.detect = { detections: [], backend: 'opencv' }
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out.ok).toBe(true)
    expect(h.calls).toHaveLength(1)
    // 状态/doctor 仍然能看到这个错误。
    const status = rec(await h.dispatch.handle('vision_status', {}))
    expect(rec(rec(status.dnn).config).lastSwitchError).toMatchObject({
      code: 'dnn_config_invalid',
    })
  })

  it('rejects a per-call model that is a path or URL', async () => {
    const h = makeHarness({ dnn: true, models: ['yolo.onnx'], values: { model: 'yolo' } })
    const out = rec(
      await h.dispatch.handle('vision_detect', {
        path: imageFile(h.dir),
        model: 'https://example.com/yolo.onnx',
      }),
    )
    expect(out).toMatchObject({ ok: false, stage: 'input', code: 'vision_model_unsupported' })
    expect(h.calls).toHaveLength(0)
  })

  it('finds models in nested directories (nested model dirs)', async () => {
    const h = makeHarness({
      dnn: true,
      models: ['deep/nested/yolo.onnx'],
      values: { model: 'yolo' },
    })
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out.ok).toBe(true)
    expect(h.calls).toHaveLength(1)
  })

  it('reports dnn_config_switch_failed when the previous swap failed', async () => {
    const h = makeHarness({ dnn: true })
    writeRuntimeSnapshot(h.dir, {
      version: 1,
      revision: 2,
      updatedAt: new Date(0).toISOString(),
      values: {
        ...DEFAULT_VALUES,
        dnn: true,
        backend: 'onnxruntime',
        venv: path.join(h.dir, 'v2'),
      },
    })
    h.store.reconcile({ workerRunning: true })
    h.store.beginSwitch()
    h.store.failSwitch('dnn_config_switch_failed', 'old worker did not exit')
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({
      ok: false,
      stage: 'config',
      code: 'dnn_config_switch_failed',
      retryable: true,
    })
    expect(out.evidence).toMatchObject({ pendingRevision: 2 })
    expect(h.calls).toHaveLength(0)
  })

  it('validates a custom venv before reporting backend/device/model problems', async () => {
    const missingVenv = path.join(tmpHome(), 'missing-venv')
    const h = makeHarness({
      dnn: true,
      values: { backend: 'onnxruntime', venv: missingVenv },
    })
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({ ok: false, stage: 'config', code: 'vision_custom_env_invalid' })
    expect(h.calls).toHaveLength(0)
  })

  it('requires a venv for backend=onnxruntime', async () => {
    const h = makeHarness({ dnn: true, values: { backend: 'onnxruntime', venv: '' } })
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({ ok: false, stage: 'config', code: 'dnn_backend_unavailable' })
    expect(h.calls).toHaveLength(0)
  })

  it('refuses device=gpu on the opencv backend instead of falling back', async () => {
    const h = makeHarness({ dnn: true, values: { backend: 'opencv', device: 'gpu' } })
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({ ok: false, stage: 'config', code: 'dnn_device_unavailable' })
    expect(h.calls).toHaveLength(0)
  })

  it('reports dnn_no_model when the models directory has no .onnx file', async () => {
    const h = makeHarness({ dnn: true, models: [] })
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({
      ok: false,
      stage: 'config',
      code: 'dnn_no_model',
      retryable: false,
    })
    expect(
      String((out.evidence as Record<string, unknown> | undefined)?.modelsDir ?? ''),
    ).toContain('vision')
    expect(h.calls).toHaveLength(0)
  })

  it('respects the documented gate precedence when several problems coexist', async () => {
    const file = (h: Harness) => imageFile(h.dir)
    const disabled = makeHarness({ dnn: false })
    expect(await disabled.dispatch.handle('vision_detect', { path: file(disabled) })).toMatchObject(
      {
        code: 'dnn_disabled',
      },
    )

    const noVenv = makeHarness({ dnn: true, values: { backend: 'onnxruntime', venv: '' } })
    expect(await noVenv.dispatch.handle('vision_detect', { path: file(noVenv) })).toMatchObject({
      code: 'dnn_backend_unavailable',
    })

    const gpu = makeHarness({ dnn: true, values: { device: 'gpu' } })
    expect(await gpu.dispatch.handle('vision_detect', { path: file(gpu) })).toMatchObject({
      code: 'dnn_device_unavailable',
    })

    const noModel = makeHarness({ dnn: true })
    expect(await noModel.dispatch.handle('vision_detect', { path: file(noModel) })).toMatchObject({
      code: 'dnn_no_model',
    })
  })

  it('delegates detect once every host gate is satisfied', async () => {
    const h = makeHarness({ dnn: true, models: ['yolo.onnx'], values: { model: 'yolo' } })
    h.results.detect = {
      detections: [{ class: 0, label: 'person', confidence: 0.91, xyxy: [1, 2, 30, 40] }],
      backend: 'opencv',
      device: { requested: 'cpu', used: 'cpu' },
      provider: 'cpu',
    }
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir), conf: 0.4 }))
    expect(out).toMatchObject({
      ok: true,
      op: 'vision_detect',
      configRevision: 1,
      backend: 'opencv',
      provider: 'cpu',
    })
    expect(rec(out).detections).toHaveLength(1)
    expect(h.calls[0]).toMatchObject({ op: 'detect' })
    expect(h.calls[0].args).toMatchObject({ conf: 0.4, iou: 0.45, maxDetections: 100 })
    expect(h.calls[0].args?.path).toBe(imageFile(h.dir))
  })

  it('validates detect arguments only after the gates pass', async () => {
    const h = makeHarness({ dnn: true, models: ['yolo.onnx'] })
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir), conf: 2 }))
    expect(out).toMatchObject({ ok: false, stage: 'input', code: 'vision_input_invalid' })
    expect(h.calls).toHaveLength(0)
  })
})

describe('vision_selftest', () => {
  it('still runs the non-DNN checks when features.dnn is off', async () => {
    const h = makeHarness({ dnn: false })
    h.results.selftest = {
      ok: true,
      checks: [
        { name: 'import', status: 'pass' },
        { name: 'dnn', status: 'skipped' },
      ],
    }
    const out = rec(await h.dispatch.handle('vision_selftest'))
    expect(out).toMatchObject({ ok: true, op: 'vision_selftest' })
    expect(h.calls).toHaveLength(1)
    expect(h.calls[0]).toMatchObject({ op: 'selftest' })
    expect(h.calls[0].args).toMatchObject({ dnn: false })
  })

  it('does not start a worker while a switch is pending', async () => {
    const h = makeHarness({ dnn: true })
    writeRuntimeSnapshot(h.dir, {
      version: 1,
      revision: 2,
      updatedAt: new Date(0).toISOString(),
      values: {
        ...DEFAULT_VALUES,
        dnn: true,
        backend: 'onnxruntime',
        venv: path.join(h.dir, 'v2'),
      },
    })
    h.store.reconcile({ workerRunning: true })
    expect(h.store.getState().phase).toBe('pending')
    const out = rec(await h.dispatch.handle('vision_selftest'))
    expect(out).toMatchObject({ ok: false, stage: 'config', code: 'dnn_config_switching' })
    expect(h.calls).toHaveLength(0)
  })
})

describe('worker failure mapping', () => {
  it('passes VisionError payloads through without masking the stage or code', async () => {
    const h = makeHarness({ dnn: true, models: ['yolo.onnx'] })
    const { VisionError } = await import('../../../runtime/vision/protocol.js')
    h.manager.request = async () => {
      throw new VisionError({
        code: 'vision_model_missing',
        stage: 'config',
        message: 'model file is missing',
        fix: 'put the .onnx under vision/models',
        retryable: false,
        details: { model: 'yolo' },
      })
    }
    const out = rec(await h.dispatch.handle('vision_detect', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({
      ok: false,
      op: 'vision_detect',
      stage: 'config',
      code: 'vision_model_missing',
      retryable: false,
      details: { model: 'yolo' },
    })
    expect(String(out.fix)).toContain('vision/models')
  })

  it('maps an unexpected throw to vision_worker_failed instead of crashing', async () => {
    const h = makeHarness({ manager: false })
    const out = rec(await h.dispatch.handle('vision_analyze', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({
      ok: false,
      op: 'vision_analyze',
      stage: 'vision',
      code: 'vision_unavailable',
      retryable: true,
    })
  })

  it('classifies image-too-large from the structured worker payload, not the message', async () => {
    const h = makeHarness()
    const { VisionError } = await import('../../../runtime/vision/protocol.js')
    h.manager.request = async () => {
      throw new VisionError({
        code: 'vision_image_too_large',
        stage: 'input',
        message: 'worker rejected the frame',
        retryable: false,
        details: { limit: 64_000_000, pixels: 68_000_000 },
      })
    }
    const out = rec(await h.dispatch.handle('vision_analyze', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({ ok: false, code: 'vision_image_too_large', stage: 'input' })
    expect(rec(out.evidence)).toMatchObject({ limit: 64_000_000, pixels: 68_000_000 })
  })

  it('does not sniff "too large" out of unrelated messages', async () => {
    const h = makeHarness()
    const { VisionError } = await import('../../../runtime/vision/protocol.js')
    h.manager.request = async () => {
      throw new VisionError({
        code: 'vision_invalid_image',
        stage: 'input',
        message: 'image too large for a stable decode',
        retryable: false,
      })
    }
    const out = rec(await h.dispatch.handle('vision_analyze', { path: imageFile(h.dir) }))
    expect(out).toMatchObject({ ok: false, code: 'vision_invalid_image' })
  })
})

describe('vision_pass dispatch guards', () => {
  it('maps manifest read errors into the error envelope instead of throwing', async () => {
    const h = makeHarness({ manager: false })
    // 父路径是普通文件 -> statSync 报 ENOTDIR，不能以异常形式逸出。
    const broken = path.join(h.dir, 'images', 'shot.png', 'manifest.json')
    const out = rec(await h.dispatch.handle('vision_pass', { manifests: [broken] }))
    expect(out.ok).toBe(false)
    expect(out.op).toBe('vision_pass')
    expect(out.code).toBe('vision_pass_manifest_invalid')
  })
})
