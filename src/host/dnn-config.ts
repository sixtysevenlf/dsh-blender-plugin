/**
 * Owner-only `/dsh-blender` `dnn` endpoint (DNN config).
 *
 * Operations: apply / status / cancel-pending / rollback / download / probe.
 * The host is the ONLY writer of <home>/vision/runtime.json; the Gateway reads
 * it and reconciles on /doctor and on every vision_* boundary. After an apply
 * the host calls the existing GET /doctor (no new HTTP route) to force that
 * reconcile and reports whether the new revision is actually active — a
 * deferred switch must never be presented as applied.
 *
 * No secrets are read or written: the snapshot contains only the feature flag
 * and the DNN/vision fields. runtime-state.json is written by the Gateway; the
 * host only reads it for fallback status.
 */
import fs from 'node:fs'
import path from 'node:path'
import { asRecord, isRecord, text } from '../contracts/dynamic.js'
import { base } from './backend/index.js'
import {
  dnnDownloadStatus,
  downloadDnnPins,
  type DownloadOutcome,
  type DownloadStatus,
} from './dnn-download.js'
import { DNN_DOCS_URL, dnnPaths, getDnnHome } from './dnn-home.js'
import { dnnPinReport, type DnnPinReportRow } from './dnn-pins.js'
import {
  normalizeDnnCandidate,
  probeDnn,
  type DnnProbeCandidate,
  type DnnProbeResult,
} from './dnn-probe.js'

export const DNN_RUNTIME_SNAPSHOT_VERSION = 1
const DOCTOR_TIMEOUT_MS = 5000

export type DnnBackend = 'opencv' | 'onnxruntime'
export type DnnDevice = 'cpu' | 'auto' | 'gpu'

export interface DnnSnapshotValues {
  dnn: boolean
  python: string
  venv: string
  model: string
  backend: DnnBackend
  device: DnnDevice
}

interface RuntimeSnapshot {
  version: number
  revision: number
  updatedAt: string
  features: { dnn: boolean }
  vision: {
    python: string
    venv: string
    model: string
    dnn: { backend: DnnBackend; device: DnnDevice }
  }
}

type SnapshotRead =
  { ok: true; snapshot: RuntimeSnapshot } | { ok: false; code: string; message: string }

interface RpcFailure {
  ok: false
  error: { code: string; message: string; details?: Record<string, unknown> }
}

interface RpcSuccess<T = unknown> {
  ok: true
  value: T
}

export interface DnnRpcDeps {
  home?: string
  port?: number
  env?: NodeJS.ProcessEnv
  now?: () => number
  /** Test seam: fetch /doctor (may throw when the Gateway is unreachable). */
  fetchDoctor?: (port: number) => Promise<Record<string, unknown> | null>
  /** Test seam: replace the pinned downloader. */
  download?: (ids: readonly string[], options: { home: string }) => Promise<DownloadOutcome>
  /** Test seam: replace the one-shot probe process. */
  probe?: typeof probeDnn
}

function failure(code: string, message: string, details?: Record<string, unknown>): RpcFailure {
  return { ok: false, error: { code, message, ...(details ? { details } : {}) } }
}

function ownerPeer(peer: unknown): boolean {
  const principal = isRecord(peer) ? peer.principal : undefined
  return isRecord(principal) && principal.kind === 'owner'
}

function validBackend(value: unknown): value is DnnBackend {
  return value === 'opencv' || value === 'onnxruntime'
}

function validDevice(value: unknown): value is DnnDevice {
  return value === 'cpu' || value === 'auto' || value === 'gpu'
}

function candidateValues(candidate: DnnProbeCandidate): DnnSnapshotValues {
  return {
    dnn: candidate.features.dnn,
    python: candidate.vision.python,
    venv: candidate.vision.venv,
    model: candidate.vision.model,
    backend: candidate.dnn.backend,
    device: candidate.dnn.device,
  }
}

function valuesFrom(raw: unknown): DnnSnapshotValues | null {
  const root = asRecord(raw)
  // /doctor and runtime-state.json wrap active/previous as {revision, values}.
  const wrapped = asRecord(root.values)
  const body = Object.keys(wrapped).length > 0 ? wrapped : root
  if (typeof body.dnn !== 'boolean') return null
  if (!validBackend(body.backend) || !validDevice(body.device)) return null
  return {
    dnn: body.dnn,
    python: text(body.python),
    venv: text(body.venv),
    model: text(body.model),
    backend: body.backend,
    device: body.device,
  }
}

export function readRuntimeSnapshot(home: string = getDnnHome()): SnapshotRead {
  const file = dnnPaths(home).runtimeJson
  let raw: string
  try {
    raw = fs.readFileSync(file, 'utf8')
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code
    if (code === 'ENOENT')
      return { ok: false, code: 'dnn_config_missing', message: '尚未保存运行时配置' }
    return {
      ok: false,
      code: 'dnn_config_invalid',
      message: error instanceof Error ? error.message : String(error),
    }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    return {
      ok: false,
      code: 'dnn_config_invalid',
      message:
        'runtime.json 不是有效 JSON: ' + (error instanceof Error ? error.message : String(error)),
    }
  }
  const body = asRecord(parsed)
  const vision = asRecord(body.vision)
  const dnn = asRecord(vision.dnn)
  const features = asRecord(body.features)
  const revision = Number(body.revision)
  const snapshot: RuntimeSnapshot = {
    version: Number(body.version),
    revision,
    updatedAt: text(body.updatedAt),
    features: { dnn: features.dnn === true },
    vision: {
      python: text(vision.python),
      venv: text(vision.venv),
      model: text(vision.model),
      dnn: { backend: 'opencv', device: 'cpu' },
    },
  }
  if (
    snapshot.version !== DNN_RUNTIME_SNAPSHOT_VERSION ||
    !Number.isSafeInteger(revision) ||
    revision <= 0 ||
    !snapshot.updatedAt ||
    (features.dnn !== undefined && typeof features.dnn !== 'boolean') ||
    !validBackend(dnn.backend) ||
    !validDevice(dnn.device) ||
    typeof vision.python !== 'string' ||
    typeof vision.venv !== 'string' ||
    typeof vision.model !== 'string'
  ) {
    return { ok: false, code: 'dnn_config_invalid', message: 'runtime.json 字段不完整或非法' }
  }
  snapshot.vision.dnn = { backend: dnn.backend, device: dnn.device }
  return { ok: true, snapshot }
}

export interface SnapshotWriteResult {
  revision: number
  previousRevision: number | null
  path: string
  snapshot: RuntimeSnapshot
}

/**
 * Per-snapshot-file revision high-water mark. readRuntimeSnapshot(+1) already
 * handles sequential writes; this guards the racing case where two applies
 * read the same previous revision within one millisecond (and a clock that
 * steps backwards). Keyed by home so independent homes/tests do not interfere.
 */
const LAST_REVISION = new Map<string, number>()

/**
 * Atomic host write: temp file in the same directory + rename. The revision is
 * strictly monotonic (max(now, previous + 1, high-water + 1)) so a Gateway
 * comparing revisions can never see a rollback or a collision.
 */
export function writeRuntimeSnapshot(
  home: string = getDnnHome(),
  values: DnnSnapshotValues,
  deps: { now?: () => number } = {},
): SnapshotWriteResult {
  const paths = dnnPaths(home)
  fs.mkdirSync(paths.visionDir, { recursive: true })
  const previous = readRuntimeSnapshot(home)
  const previousRevision = previous.ok ? previous.snapshot.revision : null
  const now = deps.now ? deps.now() : Date.now()
  const highWater = LAST_REVISION.get(paths.runtimeJson) ?? 0
  const revision = Math.max(now, (previousRevision ?? 0) + 1, highWater + 1)
  LAST_REVISION.set(paths.runtimeJson, revision)
  const snapshot: RuntimeSnapshot = {
    version: DNN_RUNTIME_SNAPSHOT_VERSION,
    revision,
    updatedAt: new Date(now).toISOString(),
    features: { dnn: values.dnn },
    vision: {
      python: values.python,
      venv: values.venv,
      model: values.model,
      dnn: { backend: values.backend, device: values.device },
    },
  }
  const tmp = paths.runtimeJson + '.tmp-' + String(process.pid) + '-' + String(now)
  try {
    fs.writeFileSync(tmp, JSON.stringify(snapshot, null, 2) + '\n', {
      encoding: 'utf8',
      mode: 0o600,
    })
    fs.renameSync(tmp, paths.runtimeJson)
  } catch (error) {
    try {
      fs.unlinkSync(tmp)
    } catch {
      /* temp already gone */
    }
    throw error
  }
  return { revision, previousRevision, path: paths.runtimeJson, snapshot }
}

/** Canonical DSH_BLENDER_DNN parsing shared with runtime/config.ts. */
export interface DnnEnvLock {
  lock: 'on' | 'off' | null
  /** true/false when lock is set, null otherwise. */
  value: boolean | null
  /** Raw value when it is not on|true|1 / off|false|0; null otherwise. */
  invalid: string | null
}

export function dnnEnvLock(env: NodeJS.ProcessEnv): DnnEnvLock {
  const raw = text(env.DSH_BLENDER_DNN).trim()
  const lower = raw.toLowerCase()
  if (!lower) return { lock: null, value: null, invalid: null }
  if (lower === 'on' || lower === 'true' || lower === '1') {
    return { lock: 'on', value: true, invalid: null }
  }
  if (lower === 'off' || lower === 'false' || lower === '0') {
    return { lock: 'off', value: false, invalid: null }
  }
  // Not a lock: the underlying config wins and status carries dnn_env_invalid.
  return { lock: null, value: null, invalid: raw }
}

async function defaultFetchDoctor(port: number): Promise<Record<string, unknown> | null> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), DOCTOR_TIMEOUT_MS)
  try {
    const response = await fetch(base(port) + '/doctor', { signal: controller.signal })
    if (!response.ok) return null
    const parsed = (await response.json()) as unknown
    return isRecord(parsed) ? parsed : null
  } finally {
    clearTimeout(timer)
  }
}

export interface DoctorView {
  reachable: boolean
  dnn: Record<string, unknown> | null
  /** /doctor.vision.features.source (e.g. 'home:/path', 'plugin:...'); null if absent. */
  featureSource: string | null
}

async function fetchDoctorView(port: number, deps: DnnRpcDeps): Promise<DoctorView> {
  const fetchDoctor = deps.fetchDoctor ?? defaultFetchDoctor
  try {
    const doctor = await fetchDoctor(port)
    if (!doctor) return { reachable: true, dnn: null, featureSource: null }
    // The Gateway serves visionDoctorView() as /doctor.vision, so the DNN block is
    // normally nested at vision.dnn. Accept a future top-level dnn too, preferring
    // whichever block is actually populated.
    const top = asRecord(doctor.dnn)
    const vision = asRecord(doctor.vision)
    const nested = asRecord(vision.dnn)
    const features = asRecord(vision.features)
    const featureSource = text(features.source) || null
    const dnn = Object.keys(top).length > 0 ? top : Object.keys(nested).length > 0 ? nested : null
    return { reachable: true, dnn, featureSource }
  } catch {
    return { reachable: false, dnn: null, featureSource: null }
  }
}

/** /doctor may expose dnn.features as a boolean or as the raw {dnn, source} block. */
function effectiveFeatureFromDoctor(view: DoctorView): boolean | null {
  const features = view.dnn ? view.dnn.features : undefined
  if (typeof features === 'boolean') return features
  const record = asRecord(features)
  return typeof record.dnn === 'boolean' ? record.dnn : null
}

function doctorReportsEnvLock(view: DoctorView): boolean {
  return view.dnn !== null && view.dnn.envLocked === true
}

/**
 * DSH_BLENDER_DNN (host or Gateway) locks only features.dnn. Other DNN fields
 * may still be applied; the snapshot must keep the feature flag that the lock
 * controls instead of writing the env-effective value over it.
 */
function preserveLockedFeature(
  home: string,
  values: DnnSnapshotValues,
  env: NodeJS.ProcessEnv,
  view: DoctorView,
): DnnSnapshotValues {
  const local = dnnEnvLock(env).lock
  if (local === null && !doctorReportsEnvLock(view)) return values
  const current = readRuntimeSnapshot(home)
  const feature = current.ok
    ? current.snapshot.features.dnn
    : local !== null
      ? local === 'on'
      : (effectiveFeatureFromDoctor(view) ?? values.dnn)
  return { ...values, dnn: feature }
}

function readRuntimeState(home: string): Record<string, unknown> | null {
  const file = dnnPaths(home).runtimeState
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(file, 'utf8'))
    return isRecord(parsed) ? parsed : null
  } catch {
    return null
  }
}

function doctorConfig(view: DoctorView): Record<string, unknown> | null {
  if (!view.dnn) return null
  const config = asRecord(view.dnn.config)
  return Object.keys(config).length > 0 ? config : null
}

function scanOnnxCount(dir: string): number {
  let count = 0
  const walk = (current: string) => {
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      if (entry.isDirectory()) walk(path.join(current, entry.name))
      else if (entry.isFile() && entry.name.toLowerCase().endsWith('.onnx')) count += 1
    }
  }
  walk(dir)
  return count
}

function configView(
  view: DoctorView,
  state: Record<string, unknown> | null,
): Record<string, unknown> {
  const fromDoctor = doctorConfig(view)
  if (fromDoctor) return fromDoctor
  const root = asRecord(state)
  const wrapped = asRecord(root.config)
  if (Object.keys(wrapped).length > 0) return wrapped
  // runtime-state.json stores the switch fields at the top level.
  if ('activeRevision' in root || 'phase' in root) {
    return {
      activeRevision: root.activeRevision,
      pendingRevision: root.pendingRevision,
      phase: root.phase,
      waitingFor: root.waitingFor,
      lastSwitchError: root.lastSwitchError,
      previous: root.previous,
      active: root.active,
      pending: root.pending,
    }
  }
  return {}
}

interface ApplyResult {
  applied: boolean
  revision: number
  phase: string | null
  waitingFor: unknown
  activeRevision: number | null
  pendingRevision: number | null
  savedForNextStart: boolean
  gatewayReachable: boolean
}

async function reconcileAndReport(
  revision: number,
  port: number,
  deps: DnnRpcDeps,
): Promise<RpcSuccess<ApplyResult> | RpcFailure> {
  const view = await fetchDoctorView(port, deps)
  if (!view.reachable) {
    return failure('gateway_unreachable', 'Gateway 未运行；配置已保存，下次启动生效', {
      applied: false,
      savedForNextStart: true,
      revision,
    })
  }
  const config = doctorConfig(view) ?? {}
  const activeRevision = Number.isSafeInteger(Number(config.activeRevision))
    ? Number(config.activeRevision)
    : null
  const phase = text(config.phase) || null
  const applied = activeRevision === revision && phase === 'idle'
  return {
    ok: true,
    value: {
      applied,
      revision,
      phase,
      waitingFor: config.waitingFor ?? null,
      activeRevision,
      pendingRevision: Number.isSafeInteger(Number(config.pendingRevision))
        ? Number(config.pendingRevision)
        : null,
      savedForNextStart: false,
      gatewayReachable: true,
    },
  }
}

function normalizeApplyCandidate(
  raw: unknown,
): { ok: true; values: DnnSnapshotValues } | { ok: false; message: string } {
  const normalized = normalizeDnnCandidate(raw)
  if (!normalized.ok) return { ok: false, message: normalized.message }
  const candidate = normalized.candidate
  if (candidate.vision.venv && !path.isAbsolute(candidate.vision.venv)) {
    return { ok: false, message: 'vision.venv 必须是绝对路径（或留空使用托管 venv）' }
  }
  if (candidate.vision.model && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(candidate.vision.model)) {
    return { ok: false, message: 'vision.model 只能是模型 id（字母/数字/._-，不允许路径或 URL）' }
  }
  if (candidate.dnn.backend === 'opencv' && candidate.dnn.device !== 'cpu') {
    return {
      ok: false,
      message: 'backend=opencv 只支持 device=cpu；GPU 需要自备 vision.venv + backend=onnxruntime',
    }
  }
  return { ok: true, values: candidateValues(candidate) }
}

async function opApply(
  body: Record<string, unknown>,
  port: number,
  deps: DnnRpcDeps,
): Promise<unknown> {
  const home = deps.home ?? getDnnHome()
  const normalized = normalizeApplyCandidate(body.candidate)
  if (!normalized.ok) return failure('invalid_params', normalized.message)
  const values = normalized.values
  const env = deps.env ?? process.env
  const localLock = dnnEnvLock(env).lock
  const beforeView = await fetchDoctorView(port, deps)
  const locked = localLock !== null || doctorReportsEnvLock(beforeView)
  if (locked) {
    const effective =
      localLock !== null ? localLock === 'on' : effectiveFeatureFromDoctor(beforeView)
    if (effective !== null && values.dnn !== effective) {
      return failure(
        'dnn_env_locked',
        'features.dnn 由 DSH_BLENDER_DNN 锁定为 ' + String(effective) + '，不能写入运行时快照',
        {
          envLocked: true,
          source: 'DSH_BLENDER_DNN',
          effective,
        },
      )
    }
  }
  const writeValues = locked ? preserveLockedFeature(home, values, env, beforeView) : values
  let written: SnapshotWriteResult
  try {
    written = writeRuntimeSnapshot(home, writeValues, { now: deps.now })
  } catch (error) {
    return failure(
      'dnn_config_write_failed',
      error instanceof Error ? error.message : String(error),
    )
  }
  return await reconcileAndReport(written.revision, port, deps)
}

async function opStatus(
  body: Record<string, unknown>,
  port: number,
  deps: DnnRpcDeps,
): Promise<unknown> {
  const home = deps.home ?? getDnnHome()
  const env = deps.env ?? process.env
  const view = await fetchDoctorView(port, deps)
  const state = readRuntimeState(home)
  const snapshot = readRuntimeSnapshot(home)
  const config = configView(view, state)
  const localEnv = dnnEnvLock(env)
  const localLock = localEnv.lock
  const lockedByGateway = doctorReportsEnvLock(view)
  const envLocked = localLock !== null || lockedByGateway || asRecord(state).envLocked === true
  const doctorFeatureRecord = asRecord(view.dnn ? view.dnn.features : undefined)
  const doctorFeature = effectiveFeatureFromDoctor(view)
  const snapshotFeature = snapshot.ok ? snapshot.snapshot.features.dnn : null
  const effectiveDnn =
    localLock !== null
      ? localLock === 'on'
      : doctorFeature !== null
        ? doctorFeature
        : snapshotFeature
  const modelsDir = dnnPaths(home).modelsDir
  const modelsExists = fs.existsSync(modelsDir)
  const downloadable: Array<DnnPinReportRow & { installed: boolean }> = dnnPinReport().map(
    (row) => ({
      ...row,
      installed: fs.existsSync(path.join(modelsDir, row.filename)),
    }),
  )
  const download: DownloadStatus = dnnDownloadStatus()
  return {
    ok: true,
    value: {
      experimental: true,
      envLocked,
      docs: { url: DNN_DOCS_URL, localPath: path.join(modelsDir, 'GPU-DIY.md') },
      models: {
        modelsDir,
        exists: modelsExists,
        onnxCount: modelsExists ? scanOnnxCount(modelsDir) : 0,
      },
      configuredModels: Array.isArray(view.dnn?.configuredModels)
        ? view.dnn?.configuredModels
        : null,
      saved: snapshot.ok ? snapshot.snapshot : null,
      savedError: snapshot.ok ? null : { code: snapshot.code, message: snapshot.message },
      config,
      gateway: { reachable: view.reachable },
      downloadable,
      download,
      features: {
        dnn: effectiveDnn,
        source:
          localLock !== null || lockedByGateway
            ? 'env'
            : // Pass the Gateway's own source through unchanged (home:/path,
              // plugin:DSH_BLENDER_PLUGIN_CONFIG, ...) so a home-sourced value is
              // never mislabelled as plugin/runtime.
              view.featureSource ||
              text(doctorFeatureRecord.source) ||
              (snapshot.ok ? 'runtime' : doctorFeature !== null ? 'plugin' : 'default'),
        locked: envLocked,
        /** Raw DSH_BLENDER_DNN value when it is not a valid lock (else null). */
        invalidEnv: localEnv.invalid,
      },
    },
  }
}

async function opCancelPending(port: number, deps: DnnRpcDeps): Promise<unknown> {
  const home = deps.home ?? getDnnHome()
  const view = await fetchDoctorView(port, deps)
  if (!view.reachable) {
    return failure('gateway_unreachable', 'Gateway 未运行，无法读取当前生效配置', {
      savedForNextStart: false,
    })
  }
  const state = readRuntimeState(home)
  const active = valuesFrom(doctorConfig(view)?.active) ?? valuesFrom(asRecord(state).active)
  if (!active)
    return failure(
      'dnn_status_unavailable',
      '无法从 /doctor 或 runtime-state.json 读取当前生效配置',
    )
  const writeValues = preserveLockedFeature(home, active, deps.env ?? process.env, view)
  let written: SnapshotWriteResult
  try {
    written = writeRuntimeSnapshot(home, writeValues, { now: deps.now })
  } catch (error) {
    return failure(
      'dnn_config_write_failed',
      error instanceof Error ? error.message : String(error),
    )
  }
  return await reconcileAndReport(written.revision, port, deps)
}

async function opRollback(port: number, deps: DnnRpcDeps): Promise<unknown> {
  const home = deps.home ?? getDnnHome()
  const view = await fetchDoctorView(port, deps)
  if (!view.reachable) {
    return failure('gateway_unreachable', 'Gateway 未运行，无法读取上一版配置', {
      savedForNextStart: false,
    })
  }
  const state = readRuntimeState(home)
  const doctorConfigBody = doctorConfig(view) ?? {}
  const previousRaw = doctorConfigBody.previous ?? asRecord(state).previous
  const previous = isRecord(previousRaw) ? valuesFrom(previousRaw.values ?? previousRaw) : null
  if (!previous) return failure('dnn_rollback_unavailable', '没有可回滚的上一版配置')
  const writeValues = preserveLockedFeature(home, previous, deps.env ?? process.env, view)
  let written: SnapshotWriteResult
  try {
    written = writeRuntimeSnapshot(home, writeValues, { now: deps.now })
  } catch (error) {
    return failure(
      'dnn_config_write_failed',
      error instanceof Error ? error.message : String(error),
    )
  }
  return await reconcileAndReport(written.revision, port, deps)
}

async function opDownload(body: Record<string, unknown>, deps: DnnRpcDeps): Promise<unknown> {
  const home = deps.home ?? getDnnHome()
  const rawIds = Array.isArray(body.ids) ? body.ids : []
  const ids = rawIds.filter((id): id is string => typeof id === 'string')
  if (ids.length !== rawIds.length) return failure('invalid_params', 'ids 必须是字符串数组')
  // Dedupe (the same id twice is one download) and cap one confirmed batch so a
  // single RPC cannot kick off an unbounded multi-hundred-MB download.
  const unique = Array.from(new Set(ids))
  if (unique.length > 5) {
    return failure(
      'invalid_params',
      '一次最多下载 5 个模型（收到 ' + String(unique.length) + ' 个）',
    )
  }
  const download =
    deps.download ??
    ((selected: readonly string[], options: { home: string }) => downloadDnnPins(selected, options))
  const outcome = await download(unique, { home })
  return { ok: true, value: { ...outcome, status: dnnDownloadStatus() } }
}

async function opProbe(body: Record<string, unknown>, deps: DnnRpcDeps): Promise<unknown> {
  const home = deps.home ?? getDnnHome()
  const env = deps.env ?? process.env
  const view = await fetchDoctorView(portOf(deps), deps)
  const localEnv = dnnEnvLock(env)
  const envLocked = localEnv.lock !== null || (view.dnn !== null && view.dnn.envLocked === true)
  const runProbe = deps.probe ?? probeDnn
  const result: DnnProbeResult = await runProbe({
    candidate: body.candidate,
    home,
    env,
    envLocked,
    // Local lock value (null when the env value is invalid or absent) so the
    // probe can warn when the candidate contradicts the lock.
    envDnnValue: localEnv.lock !== null && localEnv.value !== null ? localEnv.value : null,
  })
  return { ok: true, value: result }
}

function portOf(deps: DnnRpcDeps): number {
  return typeof deps.port === 'number' ? deps.port : 39877
}

/** RPC handler for the owner-only `dnn` endpoint (exported for unit tests). */
export async function handleDnnRpc(
  payload: unknown,
  peer: unknown,
  port: number,
  deps: DnnRpcDeps = {},
): Promise<unknown> {
  if (!ownerPeer(peer))
    return failure('forbidden', 'DNN configuration is controlled by the human owner')
  const body = asRecord(payload)
  const op = text(body.op) || 'status'
  const effectiveDeps: DnnRpcDeps = { ...deps, port }
  if (op === 'status') return await opStatus(body, port, effectiveDeps)
  if (op === 'apply') return await opApply(body, port, effectiveDeps)
  if (op === 'cancel-pending' || op === 'cancel_pending')
    return await opCancelPending(port, effectiveDeps)
  if (op === 'rollback') return await opRollback(port, effectiveDeps)
  if (op === 'download') return await opDownload(body, effectiveDeps)
  if (op === 'probe') return await opProbe(body, effectiveDeps)
  return failure('not_found', 'unknown dnn op: ' + op)
}
