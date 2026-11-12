import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import vm from 'node:vm'
import { pkgPath } from '../../helpers/pkg'

const source = fs.readFileSync(pkgPath('src', 'client', 'index.js'), 'utf8')

function loadClient() {
  let registration: any = null
  const sandbox = {
    window: {
      __ModuleLoader__: {
        load: (def: any) => {
          registration = def
        },
      },
      localStorage: { getItem: () => null, setItem: () => {} },
      setInterval: () => 0,
      clearInterval: () => {},
    },
    console,
    fetch: async () => ({ ok: false, status: 0, json: async () => ({}) }),
  }
  vm.runInNewContext(source, sandbox, { filename: 'src/client/index.js' })
  const react = {
    createElement: () => null,
    useState: () => [null, () => {}],
    useEffect: () => {},
    useRef: (v: unknown) => ({ current: v }),
  }
  const exported = registration.factory((id: string) => {
    if (id === 'react') return react
    throw new Error('unexpected require: ' + id)
  })
  return exported.__selftest
}

const selftest = loadClient()

function candidate(overrides: any = {}) {
  return {
    features: { dnn: true },
    vision: {
      python: '',
      venv: 'C:/venvs/onnx',
      model: 'yolov5s',
      dnn: { backend: 'onnxruntime', device: 'auto' },
      ...overrides,
    },
  }
}

describe('dnn config card pure helpers', () => {
  it('offers five preset models in n/s/m/l/x order and defaults to yolov5n', () => {
    expect(selftest.DNN_MODEL_IDS).toEqual(['yolov5n', 'yolov5s', 'yolov5m', 'yolov5l', 'yolov5x'])
    expect(selftest.dnnModelOptions().map((m: any) => m.id)).toEqual(selftest.DNN_MODEL_IDS)
    expect(selftest.dnnDefaultSelection()).toEqual(['yolov5n'])
  })

  it('builds the nested runtime.json candidate from a wrapped doctor active config', () => {
    const values = {
      dnn: true,
      python: 'C:/py/python.exe',
      venv: '',
      model: 'yolov5m',
      backend: 'onnxruntime',
      device: 'gpu',
    }
    const status = {
      features: { dnn: true },
      config: { active: { revision: 12, values } },
    }
    expect(selftest.dnnCandidateFromStatus(status)).toEqual({
      features: { dnn: true },
      vision: {
        python: 'C:/py/python.exe',
        venv: '',
        model: 'yolov5m',
        dnn: { backend: 'onnxruntime', device: 'gpu' },
      },
    })
    // Older/flat payloads (host status fallback) still work.
    expect(
      selftest.dnnCandidateFromStatus({ features: { dnn: true }, config: { active: values } }),
    ).toEqual({
      features: { dnn: true },
      vision: {
        python: 'C:/py/python.exe',
        venv: '',
        model: 'yolov5m',
        dnn: { backend: 'onnxruntime', device: 'gpu' },
      },
    })
  })

  it('locks only the features.dnn switch (not the other DNN fields) under an env lock', () => {
    expect(selftest.dnnControlState({ envLocked: true }, false, true)).toEqual({
      switchDisabled: true,
      fieldDisabled: false,
    })
    expect(selftest.dnnControlState({ envLocked: false }, true, true)).toEqual({
      switchDisabled: true,
      fieldDisabled: true,
    })
    expect(selftest.dnnControlState(null, false, false)).toEqual({
      switchDisabled: true,
      fieldDisabled: false,
    })
  })

  it('uses the effective feature flag from status so an env lock wins over the saved snapshot', () => {
    const status = {
      features: { dnn: false, source: 'env', locked: true },
      saved: {
        features: { dnn: true },
        vision: {
          python: '',
          venv: '',
          model: 'yolov5n',
          dnn: { backend: 'opencv', device: 'cpu' },
        },
      },
      config: {
        active: {
          dnn: true,
          python: '',
          venv: '',
          model: 'yolov5s',
          backend: 'opencv',
          device: 'cpu',
        },
      },
    }
    const candidate = selftest.dnnCandidateFromStatus(status)
    expect(candidate.features.dnn).toBe(false)
    expect(candidate.vision.model).toBe('yolov5s')
  })

  it('falls back to CPU-only defaults when no snapshot exists', () => {
    expect(selftest.dnnCandidateFromStatus(null)).toEqual({
      features: { dnn: false },
      vision: { python: '', venv: '', model: 'yolov5n', dnn: { backend: 'opencv', device: 'cpu' } },
    })
  })

  it('rejects the opencv + non-cpu combination before sending apply', () => {
    const bad = {
      features: { dnn: true },
      vision: { python: '', venv: '', model: 'yolov5n', dnn: { backend: 'opencv', device: 'gpu' } },
    }
    expect(selftest.dnnCandidateInvalidReason(bad)).toContain('opencv')
    expect(selftest.dnnCandidateInvalidReason(candidate())).toBeNull()
    expect(
      selftest.dnnCandidateInvalidReason({ features: { dnn: true }, vision: { model: '../evil' } }),
    ).toContain('模型')
  })

  it('wraps apply/cancel-pending/rollback/download/probe payloads', () => {
    expect(selftest.dnnApplyPayload(candidate())).toEqual({ op: 'apply', candidate: candidate() })
    expect(selftest.dnnRpcPayload('cancel-pending')).toEqual({ op: 'cancel-pending' })
    expect(selftest.dnnRpcPayload('rollback')).toEqual({ op: 'rollback' })
    expect(selftest.dnnRpcPayload('probe', { candidate: candidate() })).toEqual({
      op: 'probe',
      candidate: candidate(),
    })
    expect(selftest.dnnRpcPayload('download', { ids: ['yolov5n'] })).toEqual({
      op: 'download',
      ids: ['yolov5n'],
    })
  })

  it('renders idle / pending / switching / failed / env-locked status text', () => {
    expect(selftest.dnnStatusText(null)).toContain('读取')
    expect(selftest.dnnStatusText({ envLocked: true, config: {} })).toContain('DSH_BLENDER_DNN')
    expect(
      selftest.dnnStatusText({
        config: { activeRevision: 12, pendingRevision: null, phase: 'idle' },
      }),
    ).toContain('已生效')
    expect(
      selftest.dnnStatusText({
        config: { activeRevision: 12, pendingRevision: 13, phase: 'pending' },
      }),
    ).toContain('待应用')
    expect(
      selftest.dnnStatusText({
        config: { activeRevision: 12, pendingRevision: 13, phase: 'pending' },
      }),
    ).toContain('13')
    expect(
      selftest.dnnStatusText({
        config: {
          activeRevision: 12,
          pendingRevision: 13,
          phase: 'switching',
          waitingFor: { op: 'vision_detect' },
        },
      }),
    ).toContain('切换中')
    expect(
      selftest.dnnStatusText({
        config: {
          activeRevision: 12,
          pendingRevision: 13,
          phase: 'failed',
          lastSwitchError: { code: 'dnn_config_switch_failed', message: 'worker exited' },
        },
      }),
    ).toContain('失败')
    expect(
      selftest.dnnStatusText({
        config: {
          activeRevision: 12,
          pendingRevision: 13,
          phase: 'failed',
          lastSwitchError: { code: 'dnn_config_switch_failed', message: 'worker exited' },
        },
      }),
    ).toContain('worker exited')
  })

  it('shows the download prompt only when DNN is enabled with no ONNX model present', () => {
    expect(
      selftest.dnnDownloadPromptVisible({ features: { dnn: true }, models: { onnxCount: 0 } }),
    ).toBe(true)
    expect(
      selftest.dnnDownloadPromptVisible({ features: { dnn: true }, models: { onnxCount: 1 } }),
    ).toBe(false)
    expect(
      selftest.dnnDownloadPromptVisible({ features: { dnn: false }, models: { onnxCount: 0 } }),
    ).toBe(false)
    expect(selftest.dnnDownloadPromptVisible(null)).toBe(false)
  })

  it('summarizes probe results and explains failures', () => {
    const ok = { ok: true, summary: { pass: 7, warn: 1, fail: 0 }, checks: [] }
    expect(selftest.dnnProbeSummary(ok)).toEqual({ pass: 7, warn: 1, fail: 0, ok: true })
    expect(selftest.dnnProbeText(ok)).toContain('警告')
    const bad = {
      ok: false,
      summary: { pass: 3, warn: 0, fail: 2 },
      checks: [
        {
          id: 'import_cv2',
          status: 'fail',
          detail: 'No module named cv2',
          fix: 'pip install opencv-python',
        },
      ],
    }
    expect(selftest.dnnProbeText(bad)).toContain('不可用')
    expect(selftest.dnnProbeText(bad)).toContain('2')
    expect(selftest.dnnProbeChecksText(bad)).toContain('pip install opencv-python')
  })

  it('gates rollback and cancel-pending from status revisions', () => {
    expect(selftest.dnnCanRollback({ config: { previousRevision: 11 } })).toBe(true)
    expect(selftest.dnnCanRollback({ config: { previousRevision: null } })).toBe(false)
    expect(selftest.dnnCanRollback(null)).toBe(false)
    expect(selftest.dnnCanCancelPending({ config: { pendingRevision: 13 } })).toBe(true)
    expect(selftest.dnnCanCancelPending({ config: { pendingRevision: null } })).toBe(false)
  })

  it('describes a download selection and rejects an empty one', () => {
    expect(selftest.dnnDownloadSelectionText(['yolov5n'])).toContain('yolov5n')
    expect(selftest.dnnDownloadSelectionText([])).toContain('至少')
    expect(selftest.dnnDownloadSelectionText(['yolov5n', 'yolov5x'])).toContain('2')
  })

  it('keeps the switch on and the warning after refusing the download prompt', () => {
    const status = {
      features: { dnn: true },
      models: { onnxCount: 0 },
      config: { activeRevision: 12, pendingRevision: null, phase: 'idle' },
    }
    const form = selftest.dnnCandidateFromStatus(status)
    expect(form.features.dnn).toBe(true)
    // "暂不下载" only closes the modal: the form and the persisted status keep
    // features.dnn=true, and the warning plus the download entry stay visible.
    expect(selftest.dnnFormUpdate(form, {}).features.dnn).toBe(true)
    expect(selftest.dnnDownloadPromptVisible(status)).toBe(true)
  })

  it('does not claim applied while a pending switch is busy', () => {
    const pending = {
      config: {
        activeRevision: 12,
        pendingRevision: 13,
        phase: 'pending',
        waitingFor: { op: 'vision_detect' },
      },
    }
    expect(selftest.dnnStatusText(pending)).not.toContain('已生效')
    expect(selftest.dnnCanCancelPending(pending)).toBe(true)
    expect(selftest.dnnCanRollback(pending)).toBe(false)
  })

  it('preserves a non-preset vision.model instead of coercing it to yolov5n', () => {
    const fromSaved = selftest.dnnCandidateFromStatus({
      saved: {
        features: { dnn: true },
        vision: {
          python: '',
          venv: '',
          model: 'my-detector_v2',
          dnn: { backend: 'opencv', device: 'cpu' },
        },
      },
    })
    expect(fromSaved.vision.model).toBe('my-detector_v2')

    const fromActive = selftest.dnnCandidateFromStatus({
      config: {
        active: {
          dnn: true,
          python: '',
          venv: '',
          model: 'yolov5s-custom',
          backend: 'opencv',
          device: 'cpu',
        },
      },
    })
    expect(fromActive.vision.model).toBe('yolov5s-custom')

    // Missing/blank model still falls back to the documented default.
    expect(
      selftest.dnnCandidateFromStatus({ config: { active: { model: '  ' } } }).vision.model,
    ).toBe('yolov5n')
    expect(selftest.dnnCandidateFromStatus(null).vision.model).toBe('yolov5n')
  })

  it('applies the pending form after a download so the next vision_detect really uses the model', () => {
    const status = {
      features: { dnn: false },
      models: { onnxCount: 0 },
      config: {
        activeRevision: 12,
        pendingRevision: null,
        phase: 'idle',
        active: {
          dnn: false,
          python: '',
          venv: '',
          model: 'yolov5n',
          backend: 'opencv',
          device: 'cpu',
        },
      },
    }
    const untouched = selftest.dnnCandidateFromStatus(status)
    expect(selftest.dnnShouldApplyAfterDownload(untouched, status)).toBe(false)
    expect(selftest.dnnShouldApplyAfterDownload(null, status)).toBe(false)

    // The download modal opens right when the switch is turned on: that form is
    // not saved yet, so a successful download must apply it.
    const switchOn = selftest.dnnFormUpdate(untouched, { dnn: true })
    expect(switchOn.features.dnn).toBe(true)
    expect(selftest.dnnShouldApplyAfterDownload(switchOn, status)).toBe(true)

    const otherModel = selftest.dnnFormUpdate(untouched, { model: 'yolov5x' })
    expect(selftest.dnnShouldApplyAfterDownload(otherModel, status)).toBe(true)

    const invalid = { features: { dnn: true }, vision: { model: '../evil' } }
    expect(selftest.dnnShouldApplyAfterDownload(invalid, status)).toBe(false)
  })

  it('exposes the experimental copy and the fixed docs URL', () => {
    expect(selftest.DNN_DOCS_URL).toBe(
      'https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md',
    )
    expect(selftest.DNN_EXPERIMENTAL_TEXT).toContain('实验性')
    expect(selftest.DNN_EXPERIMENTAL_TEXT).toContain('GPL-3.0')
    expect(selftest.DNN_EXPERIMENTAL_TEXT).toContain('CPU')
  })
})
