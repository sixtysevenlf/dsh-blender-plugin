/**
 * vision dispatcher —— blender_op 的 8 个 vision op 在 Gateway 侧的唯一入口。
 *
 * 边界（vision 设计）：
 *   - vision_status / vision_help / vision_pass 是纯 host-local：不需要 worker、不需要
 *     Blender、不会因为调用而启动 worker。
 *   - vision_analyze / vision_compare / vision_track / vision_detect / vision_selftest 由
 *     manager 的私有 worker 执行；dispatch 只做参数校验与 DNN 门（feature / 切换中 /
 *     切换失败 / 自定义环境 / backend / device / 模型目录为空），然后委托 manager.request。
 *   - DNN 门顺序（DNN 门顺序与 task 4.4）：
 *       dnn_disabled → dnn_config_switching → dnn_config_switch_failed →
 *       vision_custom_env_invalid / dnn_backend_unavailable / dnn_device_unavailable →
 *       dnn_no_model →（之后交给 worker）vision_model_unconfigured / vision_model_missing /
 *       vision_model_unsupported。
 *     前面的门永远先短路，后面的门不越权上报。
 */
import fs from 'node:fs'
import path from 'node:path'
import { type DnnBackend, type DnnDevice } from './runtime-config.js'
import type { RuntimeConfigStore } from './runtime-config.js'
import type { DnnConfigValues, SwitchError, WaitingFor } from './runtime-config.js'
import { VisionError } from './protocol.js'
import type { VisionErrorPayload, VisionFailureReceipt } from './protocol.js'
import { scanModelFiles } from './models.js'
import {
  MAX_CONTOURS,
  MAX_DETECTIONS,
  checkEnum,
  checkFramePaths,
  checkImagePath,
  checkIntInRange,
  checkOptionalId,
  checkRoi,
  checkUnitFloat,
} from './args.js'
import { runVisionPassAsync } from './pass.js'

/** 8 个 host-local op 的全名（catalog 家族与 host 只读名单都以此为准）。 */
export const VISION_OPS = [
  'vision_status',
  'vision_analyze',
  'vision_compare',
  'vision_detect',
  'vision_track',
  'vision_pass',
  'vision_selftest',
  'vision_help',
] as const
export type VisionOp = (typeof VISION_OPS)[number]

export const VISION_DOCS_URL =
  'https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md'

/** status/help 里 models 列表的扫描上限；模型目录不参与 hot path。 */
const MODEL_LIST_LIMIT = 200
const MODEL_CACHE_TTL_MS = 2_000

type Json = Record<string, unknown>

/** manager 对外最小接口（runtime/vision/manager.ts 的 VisionManager 子集，测试用假件）。 */
export interface VisionDispatchManager {
  request(
    op: string,
    args?: Record<string, unknown>,
    opts?: { timeoutMs?: number },
  ): Promise<unknown>
  status(): {
    worker: {
      state: string
      pid: number | null
      startedAt: string | null
      protocolVersion: number | null
      alive: boolean
      restarts: number
      lastStartError: VisionErrorPayload | null
      idleSince: string | null
    }
    inFlight: number
  }
  dnnStatus(): VisionDnnStatus
}

/** vision_status.dnn 的形态；与 manager.dnnStatus() 保持同构。 */
export interface VisionDnnStatus {
  features: { dnn: boolean }
  dnn: boolean
  backend: DnnBackend
  device: DnnDevice
  model: string
  venv: string
  python: string
  phase: 'idle' | 'pending' | 'switching' | 'failed'
  activeRevision: number
  pendingRevision: number | null
  waitingFor: WaitingFor | null
  lastSwitchError: SwitchError | null
  previousRevision: number | null
  active: { revision: number; values: DnnConfigValues } | null
  pending: { revision: number; values: DnnConfigValues } | null
  envLocked: boolean
  experimental: true
  docs: string[]
}

export interface VisionDispatchDeps {
  /** DSH home；缺省用 getHome()。 */
  homeDir?: string
  store: RuntimeConfigStore
  manager?: VisionDispatchManager
  /** 图片相对路径按这个 cwd 解析；缺省 process.cwd()。 */
  cwd?: () => string
  env?: NodeJS.ProcessEnv
  /** 文档 URL；默认指向仓库 docs。 */
  docsUrl?: string
  /** installer 侧的环境/许可快照（可选；不提供时 status 只报告配置推断值）。 */
  installStatus?: () => Record<string, unknown> | null
  now?: () => number
  modelCacheTtlMs?: number
}

/** 失败回执：既满足 vision 的 stage/code/fix/retryable，也满足 availability 的 evidence。 */
export interface VisionFailure extends VisionFailureReceipt, Json {
  op: VisionOp
  fallback: { kind: 'unavailable'; summary: string }
}

function failure(
  op: VisionOp,
  code: string,
  stage: VisionFailure['stage'],
  message: string,
  opts: {
    fix?: string
    retryable?: boolean
    evidence?: Record<string, unknown>
    fallback?: string
  } = {},
): VisionFailure {
  const fix = opts.fix ?? defaultFix(code)
  return {
    ok: false,
    op,
    stage,
    code,
    message,
    error: message,
    root_cause: message,
    fix,
    fallback: {
      kind: 'unavailable',
      summary: opts.fallback ?? '该视觉能力当前不可用；不要把它当成成功结果。',
    },
    evidence: opts.evidence ?? {},
    retryable: opts.retryable === true,
  }
}

function defaultFix(code: string): string {
  switch (code) {
    case 'vision_image_too_large':
      return '先把图缩小到 64 MP 以内（或传 roi 只分析局部），再重试。'
    case 'dnn_disabled':
      return '在 DSH 插件设置里打开 features.dnn（或设 DSH_BLENDER_DNN=on），确认已选择模型后再调用 vision_detect。'
    case 'dnn_config_switching':
      return '视觉配置正在切换：等在途视觉请求结束、pending 自动提交后重试；不需要重启 Gateway。'
    case 'dnn_config_switch_failed':
      return '旧 worker 未能在 2 秒内优雅退出：保留 pending，先 dnn.cancel-pending 或显式回滚上一版配置，再重试。'
    case 'vision_custom_env_invalid':
      return '检查 vision.venv 指向的用户 venv：目录存在、包含可执行的 python（Windows Scripts\\python.exe，其它 bin/python），且已装 cv2/numpy。插件不会替你 pip。'
    case 'dnn_backend_unavailable':
      return 'backend=onnxruntime 需要 vision.venv 指向已安装 onnxruntime 的用户 venv；默认 opencv 后端不需要 ORT。'
    case 'dnn_device_unavailable':
      return 'opencv 后端只支持 device=cpu；要用 GPU 请显式设置 backend=onnxruntime + 自备带 GPU provider 的 venv（device=auto 可回退 CPU 并给出 reason）。'
    case 'dnn_no_model':
      return '把 .onnx 模型放进 <home>/vision/models/ 并配置 vision.model / models.yml；插件不会自动下载。'
    case 'vision_invalid_image':
      return '传已存在的本地图片路径（不支持 URL/通配符），并确认文件可解码。'
    case 'vision_input_invalid':
      return '按 vision_help 的参数表修正 args（路径、roi、枚举、上限）。'
    case 'vision_worker_failed':
      return '读取 vision_status.worker.lastStartError 与 <home>/logs/vision-worker.log；修好后重试。'
    case 'vision_unavailable':
      return '视觉 worker 未安装/未就绪：先看 vision_status 的 env/install 状态。'
    default:
      return '按 stage 修正输入或配置后重试；不要让调用方把失败当成功。'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 由 store 状态推断 dnnStatus（manager 缺席时的后备；测试也用它构造假 manager）。 */
export function deriveDnnStatus(store: RuntimeConfigStore, docs: string[] = []): VisionDnnStatus {
  const state = store.getState()
  const values: DnnConfigValues = state.active?.values ?? {
    dnn: false,
    python: '',
    venv: '',
    model: '',
    backend: 'opencv',
    device: 'cpu',
  }
  return {
    features: { dnn: values.dnn },
    dnn: values.dnn,
    backend: values.backend,
    device: values.device,
    model: values.model,
    venv: values.venv,
    python: values.python,
    phase: state.phase,
    activeRevision: state.activeRevision,
    pendingRevision: state.pendingRevision,
    waitingFor: state.waitingFor,
    lastSwitchError: state.lastSwitchError,
    previousRevision: state.previousRevision,
    active: state.active,
    pending: state.pending,
    envLocked: state.envLocked,
    experimental: true,
    docs,
  }
}

interface ModelCacheEntry {
  at: number
  mtimeMs: number | null
  names: string[]
}

export interface VisionDispatch {
  readonly ops: readonly VisionOp[]
  handle(op: string, payload?: unknown): Promise<unknown>
  /** 只测过期判断/扫描逻辑时暴露；生产调用只经 handle。 */
  modelsSnapshot(): { dir: string; names: string[]; modelsYml: boolean }
}

/** createVisionDispatch 内部的依赖与可变状态，供文件级 helper 通过参数访问。 */
interface VisionDispatchContext {
  store: RuntimeConfigStore
  now: () => number
  cwdFn: () => string
  docsUrl: string
  modelCacheTtlMs: number
  modelCache: ModelCacheEntry | null
  deps: VisionDispatchDeps
}

function homeDirOf(ctx: VisionDispatchContext): string {
  return ctx.deps.homeDir ?? String(ctx.store.homeDir ?? '')
}

function visionDirOf(ctx: VisionDispatchContext): string {
  return path.join(homeDirOf(ctx), 'vision')
}

function modelsDirOf(ctx: VisionDispatchContext): string {
  return path.join(visionDirOf(ctx), 'models')
}

function docsLocalPathOf(ctx: VisionDispatchContext): string {
  return path.join(modelsDirOf(ctx), 'GPU-DIY.md')
}

function dnnStatus(ctx: VisionDispatchContext): VisionDnnStatus {
  if (ctx.deps.manager) {
    try {
      return ctx.deps.manager.dnnStatus()
    } catch {
      /* manager 状态坏了也不能让 status/help 抛错：退回 store 推断值。 */
    }
  }
  return deriveDnnStatus(ctx.store, [ctx.docsUrl, docsLocalPathOf(ctx)])
}

/** models 目录快照：mtime 未变且未过 TTL 时复用，避免每次 detect/status 都扫目录。 */
function modelsSnapshot(ctx: VisionDispatchContext): {
  dir: string
  names: string[]
  modelsYml: boolean
} {
  const dir = modelsDirOf(ctx)
  const dirMtime = (): number | null => {
    try {
      return fs.statSync(dir).mtimeMs
    } catch {
      return null
    }
  }
  const mtimeMs = dirMtime()
  const cached = ctx.modelCache
  if (
    cached &&
    cached.mtimeMs === mtimeMs &&
    ctx.now() - cached.at < ctx.modelCacheTtlMs &&
    cached.names.length <= MODEL_LIST_LIMIT
  ) {
    return { dir, names: cached.names, modelsYml: fs.existsSync(path.join(dir, 'models.yml')) }
  }
  let names: string[] = []
  if (mtimeMs !== null) {
    try {
      // 递归扫 models/**/*.onnx（单层扫描会漏掉子目录模型）；仍受 200 条上限约束。
      names = scanModelFiles(dir)
        .map((file) => file.relativePath)
        .sort()
        .slice(0, MODEL_LIST_LIMIT)
    } catch {
      names = []
    }
  }
  ctx.modelCache = { at: ctx.now(), mtimeMs, names }
  return { dir, names, modelsYml: fs.existsSync(path.join(dir, 'models.yml')) }
}

function switchFailure(status: VisionDnnStatus): VisionFailure | null {
  if (status.phase === 'pending' || status.phase === 'switching') {
    return failure(
      'vision_analyze',
      'dnn_config_switching',
      'config',
      '视觉配置正在切换（phase=' + status.phase + '），新请求暂不启动 worker。',
      {
        retryable: true,
        evidence: {
          activeRevision: status.activeRevision,
          pendingRevision: status.pendingRevision,
          waitingFor: status.waitingFor,
        },
      },
    )
  }
  if (status.phase === 'failed' && status.lastSwitchError?.code === 'dnn_config_switch_failed') {
    return failure(
      'vision_analyze',
      'dnn_config_switch_failed',
      'config',
      '上一次切换 worker 失败：' + status.lastSwitchError.message,
      {
        retryable: true,
        evidence: {
          activeRevision: status.activeRevision,
          pendingRevision: status.pendingRevision,
          previousRevision: status.previousRevision,
          lastSwitchError: status.lastSwitchError,
        },
      },
    )
  }
  return null
}

/** 自定义 venv 的静态校验：目录 + 解释器存在；不 import（那由 worker probe 负责）。 */
function customEnvFailure(status: VisionDnnStatus): VisionFailure | null {
  if (status.backend !== 'onnxruntime') return null
  if (!status.venv) return null // backend_unavailable 会处理空 venv
  const venv = path.resolve(status.venv)
  let stat: fs.Stats
  try {
    stat = fs.statSync(venv)
  } catch {
    return failure(
      'vision_detect',
      'vision_custom_env_invalid',
      'config',
      'vision.venv 不存在：' + venv,
      {
        evidence: { venv },
      },
    )
  }
  if (!stat.isDirectory()) {
    return failure(
      'vision_detect',
      'vision_custom_env_invalid',
      'config',
      'vision.venv 不是目录：' + venv,
      {
        evidence: { venv },
      },
    )
  }
  const candidates =
    process.platform === 'win32'
      ? [path.join(venv, 'Scripts', 'python.exe')]
      : [path.join(venv, 'bin', 'python3'), path.join(venv, 'bin', 'python')]
  if (!candidates.some((candidate) => fs.existsSync(candidate))) {
    return failure(
      'vision_detect',
      'vision_custom_env_invalid',
      'config',
      'vision.venv 里没有可执行的 python：' + venv,
      {
        evidence: { venv, expected: candidates },
      },
    )
  }
  return null
}

function backendFailure(status: VisionDnnStatus): VisionFailure | null {
  if (status.backend === 'onnxruntime' && !status.venv) {
    return failure(
      'vision_detect',
      'dnn_backend_unavailable',
      'config',
      'backend=onnxruntime 需要 vision.venv 指向自备 venv（含 onnxruntime），当前为空。',
      {
        evidence: { backend: status.backend, venv: status.venv },
      },
    )
  }
  return null
}

function deviceFailure(status: VisionDnnStatus): VisionFailure | null {
  if (status.backend === 'opencv' && status.device !== 'cpu') {
    return failure(
      'vision_detect',
      'dnn_device_unavailable',
      'config',
      'opencv 后端只支持 device=cpu；device=' + status.device + ' 不会静默回退。',
      {
        evidence: { backend: status.backend, device: status.device },
      },
    )
  }
  return null
}

/** detect 的 host 门（顺序即 task 4.4 的优先级），返回 null 表示可以交给 worker。 */
function detectGate(ctx: VisionDispatchContext, status: VisionDnnStatus): VisionFailure | null {
  if (!status.dnn) {
    return failure(
      'vision_detect',
      'dnn_disabled',
      'config',
      'features.dnn 关闭，vision_detect 不启动 worker 也不下载模型。',
      {
        retryable: false,
        evidence: { featuresDnn: false, envLocked: status.envLocked },
      },
    )
  }
  const switching = switchFailure(status)
  if (switching) return { ...switching, op: 'vision_detect' }
  const customEnv = customEnvFailure(status)
  if (customEnv) return customEnv
  const backend = backendFailure(status)
  if (backend) return backend
  const device = deviceFailure(status)
  if (device) return device
  const models = modelsSnapshot(ctx)
  if (models.names.length === 0) {
    return failure(
      'vision_detect',
      'dnn_no_model',
      'config',
      '模型目录里没有任何 .onnx 模型：' + models.dir,
      {
        retryable: false,
        evidence: { modelsDir: models.dir, modelsYml: models.modelsYml },
      },
    )
  }
  return null
}

/** 通用 worker-backed 门：只有切换状态门（analyze/compare/track/selftest 不需要 DNN feature）。 */
function workerGate(op: VisionOp, status: VisionDnnStatus): VisionFailure | null {
  const switching = switchFailure(status)
  return switching ? { ...switching, op } : null
}

function invalidInput(
  op: VisionOp,
  message: string,
  details?: Record<string, unknown>,
): VisionFailure {
  return failure(op, 'vision_input_invalid', 'input', message, { evidence: details })
}

function imageFailure(
  op: VisionOp,
  checked: { ok: false; message: string; details?: Record<string, unknown> },
): VisionFailure {
  const details = checked.details ?? {}
  // 结构化判定（worker 的 code 或 details.limit/pixels），不靠文案正则。
  const oversized =
    details.code === 'vision_image_too_large' ||
    (isRecord(details.limit) && typeof details.pixels === 'number') ||
    (typeof details.limit === 'number' && typeof details.pixels === 'number')
  return failure(
    op,
    oversized ? 'vision_image_too_large' : 'vision_invalid_image',
    'input',
    checked.message,
    {
      evidence: details,
    },
  )
}

function requestFailure(op: VisionOp, error: unknown): VisionFailure {
  if (error instanceof VisionError) {
    const payload = error.toPayload()
    return {
      ...failure(op, payload.code, payload.stage, payload.message, {
        retryable: payload.retryable,
        evidence: payload.details ?? {},
        fix: payload.fix,
      }),
      ...(payload.details ? { details: payload.details } : {}),
    }
  }
  const payload = VisionError.from(error).toPayload()
  return failure(op, payload.code, payload.stage, payload.message, {
    retryable: payload.retryable,
    fix: payload.fix,
  })
}

function okResult(op: VisionOp, result: unknown): Json {
  if (isRecord(result)) return { ok: true, op, ...result }
  return { ok: true, op, result }
}

async function callWorker(
  ctx: VisionDispatchContext,
  op: VisionOp,
  opName: string,
  args: Record<string, unknown>,
  revision: number,
): Promise<unknown> {
  if (!ctx.deps.manager) {
    return failure(
      op,
      'vision_unavailable',
      'vision',
      '视觉 worker 未装配（Gateway 未注入 manager），该 op 当前不可用。',
      {
        retryable: true,
      },
    )
  }
  try {
    const result = await ctx.deps.manager.request(opName, args)
    const out = okResult(op, result)
    // 回执的 configRevision 永远是本请求携带的 revision，不用 worker 的旧上下文覆盖。
    out.configRevision = revision
    return out
  } catch (e) {
    return requestFailure(op, e)
  }
}

async function handleStatus(ctx: VisionDispatchContext, payload: unknown): Promise<unknown> {
  void payload
  const status = dnnStatus(ctx)
  const models = modelsSnapshot(ctx)
  let worker: Json = {
    state: 'stopped',
    pid: null,
    startedAt: null,
    protocolVersion: null,
    alive: false,
    restarts: 0,
    lastStartError: null,
    idleSince: null,
  }
  let inFlight = 0
  if (ctx.deps.manager) {
    try {
      const snapshot = ctx.deps.manager.status()
      worker = { ...snapshot.worker }
      inFlight = snapshot.inFlight
    } catch {
      /* status 不能因为 manager 状态不可读而失败 */
    }
  }
  const install = (() => {
    try {
      return ctx.deps.installStatus?.() ?? null
    } catch {
      return null
    }
  })() as Json | null
  const env: Json = {
    source: status.venv ? 'custom' : 'managed',
    venv: status.venv || path.join(visionDirOf(ctx), 'venv'),
    python: status.python || null,
    cv2: null,
    numpy: null,
    onnxruntime: null,
    ...(isRecord(install?.env) ? install.env : {}),
  }
  return {
    ok: true,
    op: 'vision_status',
    home: homeDirOf(ctx),
    env,
    install: install ?? null,
    licenses: Array.isArray(install?.licenses) ? install.licenses : null,
    worker: { ...worker, inFlight },
    dnn: {
      experimental: true,
      enabled: status.dnn,
      source: ctx.store.getState().source,
      envLocked: status.envLocked,
      backend: status.backend,
      device: {
        requested: status.device,
        // opencv 只有 CPU；onnxruntime 的实际 provider 只能由 worker probe 给出。
        used: status.backend === 'opencv' ? 'cpu' : null,
        provider: null,
        availableProviders: null,
      },
      reason:
        status.backend === 'opencv'
          ? '默认路径：OpenCV DNN CPU，不 import onnxruntime。'
          : 'onnxruntime 路径使用 vision.venv 的自带 provider；GPU 需用户自备环境。',
      docs: {
        url: ctx.docsUrl,
        localPath: docsLocalPathOf(ctx),
        localExists: fs.existsSync(docsLocalPathOf(ctx)),
      },
      models: {
        dir: models.dir,
        modelsYml: models.modelsYml,
        configured: status.model || null,
        available: models.names,
        availableCount: models.names.length,
      },
      downloadStatus: null,
      config: {
        activeRevision: status.activeRevision,
        pendingRevision: status.pendingRevision,
        phase: status.phase,
        waitingFor: status.waitingFor,
        lastSwitchError: status.lastSwitchError,
        previousRevision: status.previousRevision,
        active: status.active,
        pending: status.pending,
      },
    },
    notes: [
      'vision_status 不启动 worker；worker 未运行时 cv2/numpy/onnxruntime 版本与 provider 为 null。',
    ],
  }
}

async function handleAnalyze(ctx: VisionDispatchContext, payload: Json): Promise<unknown> {
  const op: VisionOp = 'vision_analyze'
  const status = dnnStatus(ctx)
  const gate = workerGate(op, status)
  if (gate) return gate
  const pathChecked = checkImagePath(payload.path, ctx.cwdFn())
  if (!pathChecked.ok) return imageFailure(op, pathChecked)
  const roi = checkRoi(payload.roi)
  if (!roi.ok) return invalidInput(op, roi.message, roi.details)
  const maxContours = checkIntInRange(
    payload.maxContours,
    1,
    MAX_CONTOURS,
    MAX_CONTOURS,
    'maxContours',
  )
  if (!maxContours.ok) return invalidInput(op, maxContours.message, maxContours.details)
  const args: Json = { path: pathChecked.value.absolute }
  if (roi.value) args.roi = roi.value
  args.maxContours = maxContours.value
  return callWorker(ctx, op, 'analyze', args, status.activeRevision)
}

async function handleCompare(ctx: VisionDispatchContext, payload: Json): Promise<unknown> {
  const op: VisionOp = 'vision_compare'
  const status = dnnStatus(ctx)
  const gate = workerGate(op, status)
  if (gate) return gate
  const ref = checkImagePath(payload.ref, ctx.cwdFn(), 'ref')
  if (!ref.ok) return imageFailure(op, ref)
  const candidate = checkImagePath(payload.candidate, ctx.cwdFn(), 'candidate')
  if (!candidate.ok) return imageFailure(op, candidate)
  const align = checkEnum(payload.align, ['ecc', 'orb'] as const, 'ecc', 'align')
  if (!align.ok) return invalidInput(op, align.message, align.details)
  const roi = checkRoi(payload.roi)
  if (!roi.ok) return invalidInput(op, roi.message, roi.details)
  const args: Json = {
    ref: ref.value.absolute,
    candidate: candidate.value.absolute,
    align: align.value,
  }
  if (roi.value) args.roi = roi.value
  return callWorker(ctx, op, 'compare', args, status.activeRevision)
}

async function handleTrack(ctx: VisionDispatchContext, payload: Json): Promise<unknown> {
  const op: VisionOp = 'vision_track'
  const status = dnnStatus(ctx)
  const gate = workerGate(op, status)
  if (gate) return gate
  const frames = checkFramePaths(payload.frames, ctx.cwdFn())
  if (!frames.ok) return invalidInput(op, frames.message, frames.details)
  const method = checkEnum(payload.method, ['farneback', 'lk'] as const, 'farneback', 'method')
  if (!method.ok) return invalidInput(op, method.message, method.details)
  const args: Json = { frames: frames.value, method: method.value }
  if (payload.reference !== undefined && payload.reference !== null) {
    const reference = checkFramePaths(payload.reference, ctx.cwdFn())
    if (!reference.ok) {
      return invalidInput(op, reference.message.replace(/^frames/, 'reference'), {
        ...(reference.details ?? {}),
        label: 'reference',
      })
    }
    args.reference = reference.value
  }
  return callWorker(ctx, op, 'track', args, status.activeRevision)
}

async function handleDetect(ctx: VisionDispatchContext, payload: Json): Promise<unknown> {
  const op: VisionOp = 'vision_detect'
  const status = dnnStatus(ctx)
  // 1) DNN feature / 切换 / 环境 / backend / device / 模型目录：host 门先全部走完。
  const gate = detectGate(ctx, status)
  if (gate) return gate
  const pathChecked = checkImagePath(payload.path, ctx.cwdFn())
  if (!pathChecked.ok) return imageFailure(op, pathChecked)
  const model = checkOptionalId(payload.model, 'model')
  if (!model.ok) {
    if (model.details?.reason === 'invalid_model_id') {
      return failure(op, 'vision_model_unsupported', 'input', model.message, {
        evidence: model.details,
        fix: 'model 只传 models.yml 的 key（模型 id）；路径/URL 无效，也从不代表可用模型。',
      })
    }
    return invalidInput(op, model.message, model.details)
  }
  const conf = checkUnitFloat(payload.conf, 0.25, 'conf')
  if (!conf.ok) return invalidInput(op, conf.message, conf.details)
  const iou = checkUnitFloat(payload.iou, 0.45, 'iou')
  if (!iou.ok) return invalidInput(op, iou.message, iou.details)
  const maxDetections = checkIntInRange(
    payload.maxDetections,
    1,
    MAX_DETECTIONS,
    MAX_DETECTIONS,
    'maxDetections',
  )
  if (!maxDetections.ok) return invalidInput(op, maxDetections.message, maxDetections.details)
  const args: Json = {
    path: pathChecked.value.absolute,
    conf: conf.value,
    iou: iou.value,
    maxDetections: maxDetections.value,
  }
  if (model.value) args.model = model.value
  return callWorker(ctx, op, 'detect', args, status.activeRevision)
}

async function handleSelftest(ctx: VisionDispatchContext, payload: Json): Promise<unknown> {
  const op: VisionOp = 'vision_selftest'
  const status = dnnStatus(ctx)
  // selftest 在不启用 DNN 时仍然跑非 DNN 检查；只有切换中/切换失败时短路。
  const gate = workerGate(op, status)
  if (gate) return gate
  void payload
  const models = modelsSnapshot(ctx)
  return callWorker(
    ctx,
    op,
    'selftest',
    {
      dnn: status.dnn === true,
      backend: status.backend,
      device: status.device,
      model: status.model || null,
      modelsDir: models.dir,
      hasModels: models.names.length > 0,
    },
    status.activeRevision,
  )
}

async function handleHelp(payload: unknown): Promise<unknown> {
  void payload
  return { ok: true, op: 'vision_help', text: visionHelpText() }
}

async function handlePass(ctx: VisionDispatchContext, payload: Json): Promise<unknown> {
  // vision_pass 是 host-local：不读配置、不启动 worker、不带 configRevision。
  // 读取/解码必须包在 try/catch 里：TOCTOU 或内存错误不能以异常形式逃出错误信封。
  try {
    // 异步跑：帧间让出事件循环，addon RPC / heartbeat 不被长批次饿死。
    return await runVisionPassAsync(payload, { cwd: ctx.cwdFn() })
  } catch (e) {
    return failure(
      'vision_pass',
      'vision_pass_failed',
      'vision',
      'vision_pass 读取/解码 manifest 失败：' + (e instanceof Error ? e.message : String(e)),
      {
        retryable: false,
        evidence: { reason: e instanceof Error ? e.name : typeof e },
        fix: '确认每个 manifest 与 pass 文件都在原渲染目录里、是可读的普通文件（不是 FIFO/设备/目录），然后重新渲染生成 manifest。',
      },
    )
  }
}

async function dispatchHandle(
  ctx: VisionDispatchContext,
  op: string,
  payload?: unknown,
): Promise<unknown> {
  const body = isRecord(payload) ? payload : {}
  switch (op) {
    case 'vision_status':
      return handleStatus(ctx, payload)
    case 'vision_help':
      return handleHelp(payload)
    case 'vision_pass':
      return handlePass(ctx, body)
    case 'vision_analyze':
      return handleAnalyze(ctx, body)
    case 'vision_compare':
      return handleCompare(ctx, body)
    case 'vision_track':
      return handleTrack(ctx, body)
    case 'vision_detect':
      return handleDetect(ctx, body)
    case 'vision_selftest':
      return handleSelftest(ctx, body)
    default:
      return failure('vision_status', 'vision_input_invalid', 'input', '未知 vision op：' + op, {
        evidence: { op, supported: [...VISION_OPS] },
      })
  }
}

export function createVisionDispatch(deps: VisionDispatchDeps): VisionDispatch {
  const ctx: VisionDispatchContext = {
    store: deps.store,
    now: deps.now ?? Date.now,
    cwdFn: () => deps.cwd?.() ?? process.cwd(),
    docsUrl: deps.docsUrl ?? VISION_DOCS_URL,
    modelCacheTtlMs: deps.modelCacheTtlMs ?? MODEL_CACHE_TTL_MS,
    modelCache: null,
    deps,
  }
  return {
    ops: VISION_OPS,
    handle: (op, payload) => dispatchHandle(ctx, op, payload),
    modelsSnapshot: () => modelsSnapshot(ctx),
  }
}

const HELP_LINES = [
  'vision_* —— Gateway host-local 视觉 op（不需要 GUI 会话、不启动 Blender、不走 lease）。',
  '路径规则：只接受已存在的本地文件（绝对或相对 cwd），不接受 URL / 通配符 / 脚本；解码上限 64 MP，超过返回 vision_image_too_large。',
  '坐标约定：原点左上，x 向右，y 向下；bbox=[x0,y0,x1,y1) 0-based；长度单位像素；深度单位米。回执只含标量与有界小数组，不含像素/mask/光流数组。',
  '',
  'vision_status()：home/env（venv、python、cv2/numpy/onnxruntime 版本）/worker(pid、启动时间、lastStartError)/dnn(experimental、backend、device.requested+used、provider、availableProviders、reason、docs、config.active/pending revision、models)/licenses。调用本身不启动 worker。',
  'vision_analyze({path, roi?, maxContours?})：宽高/通道、颜色均值与标准差、清晰度（Laplacian 方差）、曝光（高/低光裁剪百分比）、前景 bbox、最多 32 条轮廓与 32 条直线摘要。前景规则：有 alpha 用 alpha>0；否则 Otsu 取较小（前景）分割后最大连通域；都不可靠时 foreground=null 并说明。',
  'vision_compare({ref, candidate, align?, roi?})：先 ECC(MOTION_EUCLIDEAN)，失败再 ORB+单应；返回对齐方法、2x3 变换、inliers、IoU、平均绝对差、变化像素百分比、变化 bbox 与 home 下的预览路径；对齐失败返回 aligned=false，不伪造 IoU。',
  'vision_track({frames, method?, reference?})：frames 为 1–120 个图片或 manifest 路径；method=farneback|lk（默认 farneback）。返回每帧运动摘要/有限轨迹（最多 20 条）、序列稳定性（闪烁/抖动/跳变）与可选参考序列偏差；参考长度不一致时显式列出缺失帧，不把缺帧当 0，不返回完整光流场。',
  'vision_detect({path, model?, conf?, iou?, maxDetections?})：features.dnn 开启后按 models.yml 的模型 id 推理（默认 backend=opencv、device=cpu；onnxruntime 需 vision.venv 自备）。返回 class/可选 label/confidence/xyxy（最多 100 条）、backend、device.used、provider、configRevision。',
  'vision_pass({manifests, passes?, roi?, objects?})：读 1–240 个 manifest.json（render 产物），按 manifest 声明的 dtype/range/near+far/space/objects 解码；depth 用 depth_m=near+(raw/max)*(far-near) 还原为米并给分位数，normal 给相机空间均值/角离散，object_index 给每对象可见像素/占比/bbox/中位深度顺序，alpha 给覆盖率；不返回原始数组。',
  'vision_selftest()：在合成图上检查 import/颜色/轮廓/ECC/前后向；DNN 关闭或无模型时该检查 skipped、不算失败；切换 pending 时返回 dnn_config_switching 而不是启动旧 worker。',
  'vision_help()：本说明。',
  '',
  'models.yml 字段（OpenCV 5 models.yml 语义；key 是模型 id；放在 <home>/vision/models/ 下，路径必须是 models 目录内相对路径）：',
  '  yolov5n:',
  '    model: yolov5n.onnx        # 必填，models 目录内相对路径',
  '    width: 640                 # 必填，推理输入宽',
  '    height: 640                # 必填，推理输入高',
  '    postprocessing: yolov5     # 必填，本期只接受 yolov5',
  '    rgb: true                  # 可选，默认 false',
  '    mean: [0, 0, 0]            # 可选，默认 0',
  '    scale: 0.00392156862745098 # 可选，默认 1',
  '    labels: coco80.txt         # 可选，一行一类名，行号即 class id',
  '调用 vision_detect 时 model 只传 id；传路径/URL 会返回 vision_model_unsupported。未配置 model 时用 vision.model 或返回 vision_model_unconfigured；文件缺失返回 vision_model_missing。labels 可选，缺省时只返回 class 索引。',
  '',
  'DNN 边界（DIY）：默认 opencv+cpu，不安装/不 import onnxruntime。GPU 需用户自备 venv：vision.venv 指向 venv，安装 onnxruntime(-gpu)，再设 backend=onnxruntime、device=auto|cpu|gpu；device=gpu 无 provider 时返回 dnn_device_unavailable 且不回退，device=auto 可回退 CPU 并在 reason 说明。保存后经 runtime.json 热应用：无在途请求立即切换；有在途请求时新请求返回 dnn_config_switching（retryable），旧请求用旧 revision 跑完；旧 worker 无法退出返回 dnn_config_switch_failed。',
  '检测可用性请用配置卡的 dnn.probe：不写 runtime.json、不启动常驻 worker、不加载 ONNX/不联网。文档：' +
    VISION_DOCS_URL +
    '；模型目录本地快捷版：GPU-DIY.md。',
]

function visionHelpText(): string {
  return HELP_LINES.join('\n')
}

export { visionHelpText }
