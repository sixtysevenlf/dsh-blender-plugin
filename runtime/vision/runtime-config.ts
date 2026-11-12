/**
 * runtime-config.ts —— vision 运行时配置快照与热应用状态机（热应用设计）。
 *
 * 职责边界：
 *   - **读写** home/vision/runtime.json（宿主单写、Gateway 只读的 DNN 快照）。
 *   - **合并** 内置默认 → 包内/home 配置文件 → DSH 插件设置 → runtime.json，最后叠加
 *     DSH_BLENDER_DNN 环境锁；任何一层字段非法都 fail closed（dnn_config_invalid）。
 *   - **状态机** active / pending / switching / failed，revision 单调；状态落
 *     home/vision/runtime-state.json 供宿主跨进程读取，内存状态才是事实源。
 *
 * 这个模块不启动 worker、不猜平台、不解析 models.yml —— 那些分别属于 manager.ts 与
 * worker/models.py。它只在「配置该不该生效、要不要换 worker」这一层做判断。
 */
import fs from 'node:fs'
import path from 'node:path'
import { errorCode, errorMessage, isBoolean, isNumber, isRecord, isString } from '../core/json.js'
import { isModelId } from './models.js'

export const RUNTIME_SNAPSHOT_VERSION = 1
export const RUNTIME_SNAPSHOT_FILE = 'runtime.json'
export const RUNTIME_STATE_FILE = 'runtime-state.json'

export const DNN_BACKENDS = ['opencv', 'onnxruntime'] as const
export type DnnBackend = (typeof DNN_BACKENDS)[number]
export const DNN_DEVICES = ['cpu', 'auto', 'gpu'] as const
export type DnnDevice = (typeof DNN_DEVICES)[number]

/** 生效的 DNN 配置值（不含 revision/来源等元数据）。 */
export interface DnnConfigValues {
  dnn: boolean
  python: string
  venv: string
  model: string
  backend: DnnBackend
  device: DnnDevice
}

/** 内置默认：DNN 关闭、OpenCV CPU 后端、不指定模型。 */
export const DEFAULT_DNN_VALUES: DnnConfigValues = Object.freeze({
  dnn: false,
  python: '',
  venv: '',
  model: '',
  backend: 'opencv',
  device: 'cpu',
})

/** 配置错误：统一走 dnn_config_invalid，带可执行的 fix 提示。 */
export interface ConfigError {
  code: 'dnn_config_invalid'
  message: string
  fix?: string
  path?: string
}

function configError(message: string, extra: Partial<ConfigError> = {}): ConfigError {
  return { code: 'dnn_config_invalid', message, ...extra }
}

/** runtime.json 的规范化形态（宿主写、Gateway 读）。 */
export interface RuntimeSnapshot {
  version: typeof RUNTIME_SNAPSHOT_VERSION
  revision: number
  updatedAt: string
  values: DnnConfigValues
  /**
   * 显式清空的字段（wire 上写 `null`）：优先于低层的值。
   * 缺省 / 空串仍表示「本层未覆盖，沿用下层」。
   */
  cleared?: DnnStringField[]
}

export type DnnStringField = 'python' | 'venv' | 'model'

const DNN_STRING_FIELDS: readonly DnnStringField[] = ['python', 'venv', 'model']

export type SnapshotRead =
  { ok: true; snapshot: RuntimeSnapshot | null } | { ok: false; error: ConfigError }

export function visionDir(homeDir: string): string {
  return path.join(homeDir, 'vision')
}

export function runtimeSnapshotPath(homeDir: string): string {
  return path.join(visionDir(homeDir), RUNTIME_SNAPSHOT_FILE)
}

export function runtimeStatePath(homeDir: string): string {
  return path.join(visionDir(homeDir), RUNTIME_STATE_FILE)
}

/** 临时文件 + rename 的原子写；权限尽量 0600（Windows 忽略 mode）。 */
export function writeFileAtomic(file: string, data: string, mode = 0o600): void {
  const dir = path.dirname(file)
  fs.mkdirSync(dir, { recursive: true })
  const tmp = path.join(
    dir,
    '.' +
      path.basename(file) +
      '.tmp-' +
      String(process.pid) +
      '-' +
      Math.random().toString(36).slice(2, 10),
  )
  try {
    fs.writeFileSync(tmp, data, { encoding: 'utf8', mode })
    // 先 fsync 临时文件，再 rename；目录 fsync 是 best effort（Windows 不支持）。
    try {
      const fd = fs.openSync(tmp, 'r+')
      try {
        fs.fsyncSync(fd)
      } finally {
        fs.closeSync(fd)
      }
    } catch {
      /* fsync 不可用时仍保留 rename 的原子性 */
    }
    fs.renameSync(tmp, file)
    try {
      const dirFd = fs.openSync(dir, 'r')
      try {
        fs.fsyncSync(dirFd)
      } finally {
        fs.closeSync(dirFd)
      }
    } catch {
      /* 目录 fsync 只在支持的平台上生效 */
    }
  } catch (e) {
    try {
      fs.unlinkSync(tmp)
    } catch {
      /* tmp may not exist */
    }
    throw e
  }
}

function readLayerString(
  layer: Record<string, unknown>,
  key: string,
  label: string,
  fallback: string,
): { ok: true; value: string } | { ok: false; error: ConfigError } {
  const raw = layer[key]
  if (raw === undefined || raw === null) return { ok: true, value: fallback }
  if (!isString(raw)) {
    return { ok: false, error: configError(label + ' must be a string', { path: label }) }
  }
  return { ok: true, value: raw }
}

function readEnum<T extends string>(
  raw: unknown,
  allowed: readonly T[],
  fallback: T,
  label: string,
): { ok: true; value: T } | { ok: false; error: ConfigError } {
  if (raw === undefined || raw === null) return { ok: true, value: fallback }
  if (isString(raw) && (allowed as readonly string[]).includes(raw)) {
    return { ok: true, value: raw as T }
  }
  return {
    ok: false,
    error: configError(label + ' must be one of ' + allowed.join(' | '), { path: label }),
  }
}

interface PartialValues {
  dnn?: boolean
  python?: string
  venv?: string
  model?: string
  backend?: DnnBackend
  device?: DnnDevice
}

type LayerRead =
  { ok: true; values: PartialValues; touched: boolean } | { ok: false; error: ConfigError }

/**
 * 宽松读取一层配置（包内 config / 插件设置）：同时接受嵌套形态
 * `{features:{dnn}, vision:{python,venv,model,dnn:{backend,device}}}` 与扁平字段。
 */
function readConfigLayer(raw: unknown, label: string): LayerRead {
  if (raw === undefined || raw === null) return { ok: true, values: {}, touched: false }
  if (!isRecord(raw)) return { ok: true, values: {}, touched: false }

  const features = isRecord(raw.features) ? raw.features : {}
  const vision = isRecord(raw.vision) ? raw.vision : {}
  const topDnn = isRecord(raw.dnn) ? raw.dnn : {}
  const nestedDnn = isRecord(vision.dnn) ? vision.dnn : {}
  const dnnBlock = Object.keys(nestedDnn).length > 0 ? nestedDnn : topDnn

  const values: PartialValues = {}
  let touched = false

  const dnnRaw =
    features.dnn !== undefined ? features.dnn : typeof raw.dnn === 'boolean' ? raw.dnn : undefined
  if (dnnRaw !== undefined) {
    if (!isBoolean(dnnRaw)) {
      return {
        ok: false,
        error: configError(label + '.features.dnn must be a boolean', { path: label }),
      }
    }
    values.dnn = dnnRaw
    touched = true
  }

  for (const key of DNN_STRING_FIELDS) {
    const rawValue = vision[key]
    if (rawValue === null) {
      // 显式清空：覆盖下层（与「缺省」区分）。
      values[key] = ''
      touched = true
      continue
    }
    const read = readLayerString(vision, key, label + '.vision.' + key, '')
    if (!read.ok) return read
    if (read.value !== '') {
      if (key === 'model' && !isModelId(read.value)) {
        return {
          ok: false,
          error: configError(
            label + '.vision.model 必须是模型 id（' + MODEL_ID_HINT + '），不能是路径或 URL',
            { path: label + '.vision.model' },
          ),
        }
      }
      values[key] = read.value
      touched = true
    } else if (rawValue !== undefined) {
      touched = true
    }
  }

  const backend = readEnum(dnnBlock.backend, DNN_BACKENDS, 'opencv', label + '.vision.dnn.backend')
  if (!backend.ok) return backend
  if (dnnBlock.backend !== undefined) {
    values.backend = backend.value
    touched = true
  }
  const device = readEnum(dnnBlock.device, DNN_DEVICES, 'cpu', label + '.vision.dnn.device')
  if (!device.ok) return device
  if (dnnBlock.device !== undefined) {
    values.device = device.value
    touched = true
  }
  return { ok: true, values, touched }
}

/** 把一层配置叠加到生效值上；`''` 来自显式清空（null），会覆盖下层。 */
function applyValues(target: DnnConfigValues, partial: PartialValues): void {
  if (partial.dnn !== undefined) target.dnn = partial.dnn
  if (partial.python !== undefined) target.python = partial.python
  if (partial.venv !== undefined) target.venv = partial.venv
  if (partial.model !== undefined) target.model = partial.model
  if (partial.backend !== undefined) target.backend = partial.backend
  if (partial.device !== undefined) target.device = partial.device
}

const MODEL_ID_HINT = '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'

export type DnnConfigSource = 'default' | 'file' | 'plugin' | 'runtime'

export interface MergeInput {
  snapshot?: RuntimeSnapshot | null
  file?: unknown
  plugin?: unknown
  env?: NodeJS.ProcessEnv
}

export type MergeResult =
  | { ok: true; values: DnnConfigValues; envLocked: boolean; source: DnnConfigSource }
  | { ok: false; error: ConfigError }

// Same grammar as runtime/config.ts::parseDnnEnvLock: only on/true/1 and off/false/0 lock the
// feature. Anything else is ignored (the config layer reports it as dnn_env_invalid), so the
// Gateway store and the host cannot disagree about whether DSH_BLENDER_DNN is a lock.
const ENV_ON = new Set(['on', '1', 'true'])
const ENV_OFF = new Set(['off', '0', 'false'])

/** 合并所有配置层；DSH_BLENDER_DNN 优先且锁定 features.dnn。 */
export function mergeDnnValues(input: MergeInput): MergeResult {
  const values: DnnConfigValues = { ...DEFAULT_DNN_VALUES }
  let source: DnnConfigSource = 'default'

  const file = readConfigLayer(input.file, 'config file')
  if (!file.ok) return file
  if (file.touched) {
    applyValues(values, file.values)
    source = 'file'
  }

  const plugin = readConfigLayer(input.plugin, 'plugin settings')
  if (!plugin.ok) return plugin
  if (plugin.touched) {
    applyValues(values, plugin.values)
    source = 'plugin'
  }

  if (input.snapshot) {
    // 快照空串 = 本层未覆盖（回落低层）；显式清空写 null，parse 后进 cleared。
    const snapValues = input.snapshot.values
    for (const key of DNN_STRING_FIELDS) {
      if (snapValues[key] !== '') values[key] = snapValues[key]
    }
    values.dnn = snapValues.dnn
    values.backend = snapValues.backend
    values.device = snapValues.device
    for (const key of input.snapshot.cleared ?? []) values[key] = ''
    source = 'runtime'
  }

  let envLocked = false
  const rawEnv = input.env?.DSH_BLENDER_DNN
  if (rawEnv !== undefined) {
    const v = String(rawEnv).trim().toLowerCase()
    if (ENV_ON.has(v)) {
      values.dnn = true
      envLocked = true
    } else if (ENV_OFF.has(v)) {
      values.dnn = false
      envLocked = true
    }
  }
  return { ok: true, values, envLocked, source }
}

export function serializeRuntimeSnapshot(snapshot: RuntimeSnapshot): Record<string, unknown> {
  const v = snapshot.values
  const cleared = new Set<DnnStringField>(snapshot.cleared ?? [])
  return {
    version: RUNTIME_SNAPSHOT_VERSION,
    revision: snapshot.revision,
    updatedAt: snapshot.updatedAt,
    features: { dnn: v.dnn },
    vision: {
      python: cleared.has('python') ? null : v.python,
      venv: cleared.has('venv') ? null : v.venv,
      model: cleared.has('model') ? null : v.model,
      dnn: { backend: v.backend, device: v.device },
    },
  }
}

/** 解析 runtime.json；字段缺失回落到默认值，类型/枚举非法一律 dnn_config_invalid。 */
export function parseRuntimeSnapshot(
  raw: unknown,
): { ok: true; snapshot: RuntimeSnapshot } | { ok: false; error: ConfigError } {
  if (!isRecord(raw)) {
    return { ok: false, error: configError('runtime.json must be a JSON object') }
  }
  const version = raw.version
  if (version !== RUNTIME_SNAPSHOT_VERSION) {
    return {
      ok: false,
      error: configError(
        'runtime.json version must be ' +
          String(RUNTIME_SNAPSHOT_VERSION) +
          ', got ' +
          String(version),
      ),
    }
  }
  const revision = raw.revision
  if (!isNumber(revision) || !Number.isInteger(revision) || revision <= 0) {
    return { ok: false, error: configError('runtime.json revision must be a positive integer') }
  }
  if (!isString(raw.updatedAt) || !raw.updatedAt) {
    return {
      ok: false,
      error: configError('runtime.json updatedAt must be an ISO timestamp string'),
    }
  }

  if (raw.features !== undefined && !isRecord(raw.features)) {
    return { ok: false, error: configError('runtime.json features must be an object') }
  }
  const features = isRecord(raw.features) ? raw.features : {}
  if (features.dnn !== undefined && !isBoolean(features.dnn)) {
    return { ok: false, error: configError('runtime.json features.dnn must be a boolean') }
  }

  if (raw.vision !== undefined && !isRecord(raw.vision)) {
    return { ok: false, error: configError('runtime.json vision must be an object') }
  }
  const vision = isRecord(raw.vision) ? raw.vision : {}
  if (vision.dnn !== undefined && !isRecord(vision.dnn)) {
    return { ok: false, error: configError('runtime.json vision.dnn must be an object') }
  }
  const dnn = isRecord(vision.dnn) ? vision.dnn : isRecord(raw.dnn) ? raw.dnn : {}

  const values: DnnConfigValues = { ...DEFAULT_DNN_VALUES }
  const cleared: DnnStringField[] = []
  for (const key of DNN_STRING_FIELDS) {
    if (vision[key] === null) {
      cleared.push(key)
      values[key] = ''
      continue
    }
    const read = readLayerString(vision, key, 'runtime.json vision.' + key, '')
    if (!read.ok) return read
    if (key === 'model' && read.value !== '' && !isModelId(read.value)) {
      return {
        ok: false,
        error: configError(
          'runtime.json vision.model 必须是模型 id（' + MODEL_ID_HINT + '），不能是路径或 URL',
          { path: 'runtime.json vision.model' },
        ),
      }
    }
    values[key] = read.value
  }
  const backend = readEnum(dnn.backend, DNN_BACKENDS, 'opencv', 'runtime.json vision.dnn.backend')
  if (!backend.ok) return backend
  values.backend = backend.value
  const device = readEnum(dnn.device, DNN_DEVICES, 'cpu', 'runtime.json vision.dnn.device')
  if (!device.ok) return device
  values.device = device.value
  values.dnn = features.dnn === true

  return {
    ok: true,
    snapshot: {
      version: RUNTIME_SNAPSHOT_VERSION,
      revision: Math.trunc(revision),
      updatedAt: raw.updatedAt,
      values,
      ...(cleared.length ? { cleared } : {}),
    },
  }
}

/** 读取 home/vision/runtime.json；文件不存在返回 null（不是错误）。 */
export function readRuntimeSnapshot(homeDir: string): SnapshotRead {
  const file = runtimeSnapshotPath(homeDir)
  let text: string
  try {
    text = fs.readFileSync(file, 'utf8')
  } catch (e) {
    if (errorCode(e) === 'ENOENT') return { ok: true, snapshot: null }
    return {
      ok: false,
      error: configError('cannot read ' + file + ': ' + errorMessage(e), { path: file }),
    }
  }
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch (e) {
    return {
      ok: false,
      error: configError('runtime.json is not valid JSON: ' + file, { path: file }),
    }
  }
  const parsed = parseRuntimeSnapshot(raw)
  if (!parsed.ok) {
    return {
      ok: false,
      error: { ...parsed.error, path: file, message: parsed.error.message + ' (' + file + ')' },
    }
  }
  return parsed
}

export function writeRuntimeSnapshot(homeDir: string, snapshot: RuntimeSnapshot): void {
  const file = runtimeSnapshotPath(homeDir)
  writeFileAtomic(file, JSON.stringify(serializeRuntimeSnapshot(snapshot), null, 2) + '\n')
}

/**
 * 下一个 revision：与毫秒时间戳同量级，但单调递增。
 * 宿主每次 apply 用 max(Date.now(), 上一个 revision + 1)，时钟回拨也不会让旧快照赢。
 */
export function nextRevision(previous: number, now: number): number {
  const prev = isNumber(previous) && previous > 0 ? Math.trunc(previous) : 0
  const ts = isNumber(now) ? Math.trunc(now) : Date.now()
  return Math.max(ts, prev + 1)
}

export type SwitchPhase = 'idle' | 'pending' | 'switching' | 'failed'

export interface WaitingFor {
  op: string
  startedAt: string
  requestId: string
}

export interface SwitchError {
  code: string
  message: string
}

export interface ActiveConfig {
  revision: number
  values: DnnConfigValues
}

export interface RuntimeConfigState {
  activeRevision: number
  pendingRevision: number | null
  phase: SwitchPhase
  waitingFor: WaitingFor | null
  lastSwitchError: SwitchError | null
  previous: ActiveConfig | null
  previousRevision: number | null
  active: ActiveConfig | null
  pending: ActiveConfig | null
  envLocked: boolean
  source: DnnConfigSource
  experimental: true
  docs: string[]
}

export type ReconcileStatus =
  | 'idle'
  | 'stale'
  | 'invalid'
  | 'committed'
  | 'pending'
  | 'switch_required'
  | 'switching'
  | 'failed'

export type SwitchReason =
  'none' | 'dnn' | 'model' | 'worker_env' | 'backend' | 'device' | 'first_apply'

export interface ReconcileOutcome {
  status: ReconcileStatus
  reason: SwitchReason
  needsWorkerRestart: boolean
  activeRevision: number
  pendingRevision: number | null
  previousRevision: number | null
}

export interface ReconcileInput {
  /** 最老的在途 worker-backed 请求；有值即只登记 pending，不打断。 */
  inFlight?: WaitingFor | null
  /** worker 是否已启动；未启动时任何配置变化都只提交、不换 worker（懒启动）。 */
  workerRunning?: boolean
}

export interface RuntimeConfigStoreOptions {
  homeDir: string
  env?: NodeJS.ProcessEnv
  fileConfig?: unknown
  pluginConfig?: unknown
  now?: () => number
  docs?: string[]
}

export interface RuntimeConfigStore {
  readonly homeDir: string
  getState(): RuntimeConfigState
  reconcile(input?: ReconcileInput): ReconcileOutcome
  beginSwitch(): void
  commitActive(): void
  failSwitch(code: string, message: string): void
}

const DEFAULT_DOCS = ['docs/配置参考.md', 'docs/操作教程.md']

function requiresWorkerRestart(active: DnnConfigValues | null, target: DnnConfigValues): boolean {
  if (!active) return false
  if (active.python !== target.python || active.venv !== target.venv) return true
  if (!active.dnn && !target.dnn) return false
  return active.backend !== target.backend || active.device !== target.device
}

function diffReason(active: DnnConfigValues | null, target: DnnConfigValues): SwitchReason {
  if (!active) return 'first_apply'
  if (active.python !== target.python || active.venv !== target.venv) return 'worker_env'
  if (active.backend !== target.backend) return 'backend'
  if (active.device !== target.device) return 'device'
  if (active.model !== target.model) return 'model'
  if (active.dnn !== target.dnn) return 'dnn'
  return 'none'
}

/** 跨进程状态文件的序列化形态（宿主只读）。 */
export function serializeRuntimeState(
  state: RuntimeConfigState,
  updatedAt: string,
): Record<string, unknown> {
  return {
    version: 1,
    updatedAt,
    activeRevision: state.activeRevision,
    pendingRevision: state.pendingRevision,
    phase: state.phase,
    waitingFor: state.waitingFor,
    lastSwitchError: state.lastSwitchError,
    previous: state.previous,
    active: state.active,
    pending: state.pending,
    previousRevision: state.previousRevision,
    envLocked: state.envLocked,
    source: state.source,
    docs: state.docs,
  }
}

function cloneState(state: RuntimeConfigState): RuntimeConfigState {
  return JSON.parse(JSON.stringify(state)) as RuntimeConfigState
}

interface StoreCore {
  homeDir: string
  fileConfig: unknown
  pluginConfig: unknown
  env: NodeJS.ProcessEnv
  now: () => number
  snapshot: RuntimeSnapshot | null
  loadError: ConfigError | null
  merged: DnnConfigValues
  envLocked: boolean
  source: DnnConfigSource
  state: RuntimeConfigState
}

function storeSync(core: StoreCore): void {
  core.loadError = null
  const read = readRuntimeSnapshot(core.homeDir)
  core.snapshot = read.ok ? read.snapshot : null
  if (!read.ok) core.loadError = read.error
  if (core.loadError) {
    // 快照坏了仍然尝试合并静态配置层：状态机据此 fail closed，但保留可读的来源信息。
    const fallback = mergeDnnValues({
      snapshot: null,
      file: core.fileConfig,
      plugin: core.pluginConfig,
      env: core.env,
    })
    if (fallback.ok) {
      core.merged = fallback.values
      core.envLocked = fallback.envLocked
      core.source = fallback.source
    }
    return
  }
  const result = mergeDnnValues({
    snapshot: core.snapshot,
    file: core.fileConfig,
    plugin: core.pluginConfig,
    env: core.env,
  })
  if (!result.ok) {
    core.loadError = result.error
    return
  }
  core.merged = result.values
  core.envLocked = result.envLocked
  core.source = result.source
  core.state.envLocked = core.envLocked
  core.state.source = core.source
  if (!core.state.active) {
    core.state.active = { revision: 0, values: { ...core.merged } }
    core.state.activeRevision = 0
  }
}

function storePersist(core: StoreCore): void {
  try {
    writeFileAtomic(
      runtimeStatePath(core.homeDir),
      JSON.stringify(
        serializeRuntimeState(core.state, new Date(core.now()).toISOString()),
        null,
        2,
      ) + '\n',
    )
  } catch {
    /* 状态文件是跨进程只读视图；写失败不改变内存事实源。 */
  }
}

function storeOutcome(
  core: StoreCore,
  status: ReconcileStatus,
  reason: SwitchReason,
  needsWorkerRestart: boolean,
): ReconcileOutcome {
  return {
    status,
    reason,
    needsWorkerRestart,
    activeRevision: core.state.activeRevision,
    pendingRevision: core.state.pendingRevision,
    previousRevision: core.state.previousRevision,
  }
}

function storeCommitSnapshot(
  core: StoreCore,
  target: RuntimeSnapshot,
  values: DnnConfigValues,
): void {
  const prev = core.state.active
  // revision 0 是「内置默认」基线，不算可回滚的历史配置。
  if (prev && prev.revision > 0 && prev.revision !== target.revision) core.state.previous = prev
  core.state.active = { revision: target.revision, values: { ...values } }
  core.state.activeRevision = target.revision
  core.state.previousRevision = core.state.previous?.revision ?? null
  core.state.pending = null
  core.state.pendingRevision = null
  core.state.phase = 'idle'
  core.state.waitingFor = null
  core.state.lastSwitchError = null
  storePersist(core)
}

function storeFailInvalid(core: StoreCore, error: ConfigError): ReconcileOutcome {
  core.state.phase = 'failed'
  core.state.lastSwitchError = { code: error.code, message: error.message }
  storePersist(core)
  return storeOutcome(core, 'invalid', 'none', false)
}

function storeReconcile(core: StoreCore, input: ReconcileInput): ReconcileOutcome {
  storeSync(core)
  if (core.state.phase === 'switching') return storeOutcome(core, 'switching', 'none', false)
  if (core.loadError) return storeFailInvalid(core, core.loadError)

  if (!core.snapshot) {
    if (!core.state.active) {
      core.state.active = { revision: 0, values: { ...core.merged } }
      storePersist(core)
    }
    return storeOutcome(core, 'idle', 'none', false)
  }

  const snap = core.snapshot
  const pendingRevision = core.state.pendingRevision
  if (pendingRevision !== null && snap.revision < pendingRevision) {
    // 比已登记的 pending 还旧：不让热应用倒退。
    return storeOutcome(core, core.state.phase === 'pending' ? 'pending' : 'stale', 'none', false)
  }
  if (snap.revision <= core.state.activeRevision && pendingRevision === null) {
    return storeOutcome(core, 'stale', 'none', false)
  }
  if (
    snap.revision <= core.state.activeRevision &&
    pendingRevision !== null &&
    snap.revision !== pendingRevision
  ) {
    return storeOutcome(core, 'stale', 'none', false)
  }

  const activeValues = core.state.active?.values ?? null
  const target = core.merged
  const reason = diffReason(activeValues, target)
  const restart = requiresWorkerRestart(activeValues, target) && input.workerRunning === true

  if (
    core.state.phase === 'failed' &&
    core.state.lastSwitchError?.code === 'dnn_config_switch_failed' &&
    core.state.pendingRevision === snap.revision
  ) {
    // 换 worker 失败必须显式可见：同一 revision 不自动重试，等新的 apply / 回滚。
    return storeOutcome(core, 'failed', reason, false)
  }

  if (!restart) {
    storeCommitSnapshot(core, snap, target)
    return storeOutcome(core, 'committed', reason, false)
  }

  if (core.state.pendingRevision !== snap.revision) {
    core.state.lastSwitchError = null
  }
  core.state.pending = { revision: snap.revision, values: { ...target } }
  core.state.pendingRevision = snap.revision
  core.state.phase = 'pending'
  core.state.waitingFor = input.inFlight ?? core.state.waitingFor
  if (input.inFlight) core.state.waitingFor = input.inFlight
  storePersist(core)
  return storeOutcome(core, input.inFlight ? 'pending' : 'switch_required', reason, true)
}

function storeBeginSwitch(core: StoreCore): void {
  if (core.state.phase !== 'pending' || !core.state.pending) {
    throw new Error('vision runtime-config: beginSwitch requires a pending revision')
  }
  core.state.phase = 'switching'
  storePersist(core)
}

function storeCommitActive(core: StoreCore): void {
  const pending = core.state.pending
  if (!pending) throw new Error('vision runtime-config: commitActive requires a pending revision')
  const prev = core.state.active
  if (prev && prev.revision > 0 && prev.revision !== pending.revision) core.state.previous = prev
  core.state.active = { revision: pending.revision, values: { ...pending.values } }
  core.state.activeRevision = pending.revision
  core.state.previousRevision = core.state.previous?.revision ?? null
  core.state.pending = null
  core.state.pendingRevision = null
  core.state.phase = 'idle'
  core.state.waitingFor = null
  core.state.lastSwitchError = null
  storePersist(core)
}

function storeFailSwitch(core: StoreCore, code: string, message: string): void {
  core.state.phase = 'failed'
  core.state.lastSwitchError = { code, message }
  core.state.waitingFor = null
  storePersist(core)
}

export function createRuntimeConfigStore(opts: RuntimeConfigStoreOptions): RuntimeConfigStore {
  const core: StoreCore = {
    homeDir: opts.homeDir,
    fileConfig: opts.fileConfig,
    pluginConfig: opts.pluginConfig,
    env: opts.env ?? process.env,
    now: opts.now ?? Date.now,
    snapshot: null,
    loadError: null,
    merged: { ...DEFAULT_DNN_VALUES },
    envLocked: false,
    source: 'default',
    state: {
      activeRevision: 0,
      pendingRevision: null,
      phase: 'idle',
      waitingFor: null,
      lastSwitchError: null,
      previous: null,
      previousRevision: null,
      active: null,
      pending: null,
      envLocked: false,
      source: 'default',
      experimental: true,
      docs: opts.docs ?? DEFAULT_DOCS,
    },
  }

  storeSync(core)

  return {
    homeDir: core.homeDir,
    getState(): RuntimeConfigState {
      return cloneState(core.state)
    },
    reconcile(input: ReconcileInput = {}): ReconcileOutcome {
      return storeReconcile(core, input)
    },
    beginSwitch(): void {
      storeBeginSwitch(core)
    },
    commitActive(): void {
      storeCommitActive(core)
    },
    failSwitch(code: string, message: string): void {
      storeFailSwitch(core, code, message)
    },
  }
}
