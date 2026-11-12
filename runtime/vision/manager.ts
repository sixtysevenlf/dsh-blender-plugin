/**
 * manager.ts —— vision worker 的生命周期与热应用切换（worker 生命周期设计）。
 *
 * 职责：
 *   - 懒启动 **至多一个** 私有 worker 子进程（stdin/stdout 上的 JSON-lines 协议），
 *     不监听端口、不联网、stderr 只追加 home/logs/vision-worker.log 的尾部。
 *   - 请求超时 / 崩溃 / 协议错乱时结束进程树，并按 5 分钟 3 次的重启预算止损。
 *   - 空闲 10 分钟回收 worker（vision_status / vision_pass 永远不会碰这里）。
 *   - 配置热应用的两阶段切换：无 in-flight 时先停旧 worker 再提交 pending；
 *     有 in-flight 时只登记 pending，等归零后切换；关闭失败保持旧 active 并显式报错。
 *
 * 这一层不做 DNN feature gate（那在 dispatch 的 op 层）、不解析 models.yml（worker 侧）、
 * 不探测 Python 安装（installer.ts）。它只负责「谁来跑、什么时候换、失败怎么报」。
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { errorMessage, isNumber, isRecord } from '../core/json.js'
import {
  IDLE_REAP_MS,
  JsonLinesDecoder,
  MAX_RESTARTS_PER_WINDOW,
  PROTOCOL_VERSION,
  REQUEST_TIMEOUT_MS,
  RESTART_WINDOW_MS,
  SHUTDOWN_GRACE_MS,
  VisionError,
  encodeFrame,
  toVisionErrorPayload,
} from './protocol.js'
import type { VisionErrorPayload, VisionRequestConfig } from './protocol.js'
import {
  DEFAULT_DNN_VALUES,
  type DnnConfigValues,
  type ReconcileOutcome,
  type ReconcileStatus,
  type RuntimeConfigStore,
  type SwitchError,
  type SwitchPhase,
  type SwitchReason,
  type WaitingFor,
} from './runtime-config.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const HANDSHAKE_TIMEOUT_MS = 10_000
const WORKER_LOG_MAX_BYTES = 256 * 1024
const WORKER_LOG_COMPACT_AT = 2 * WORKER_LOG_MAX_BYTES

/** 需要 worker 的 op（dispatch 侧据此决定要不要走 manager；status/pass 不算）。 */
export const WORKER_BACKED_OPS = ['analyze', 'compare', 'detect', 'track', 'selftest'] as const
export type WorkerBackedOp = (typeof WORKER_BACKED_OPS)[number]

export function isWorkerBackedOp(op: string): op is WorkerBackedOp {
  return (WORKER_BACKED_OPS as readonly string[]).includes(op)
}

/** worker 子进程的最小结构；生产用 nodeSpawn，测试注入假实现。 */
export interface VisionChildProcess {
  pid?: number
  stdin: NodeJS.WritableStream | null
  stdout: NodeJS.ReadableStream | null
  stderr: NodeJS.ReadableStream | null
  kill(signal?: NodeJS.Signals | number): boolean
  on(event: string, listener: (...args: unknown[]) => void): unknown
}

export type VisionSpawn = (
  command: string,
  args: string[],
  options: Record<string, unknown>,
) => VisionChildProcess

/** 默认 spawn：最小环境、cwd=home、windowsHide；POSIX 独立进程组以便杀整棵树。 */
export function nodeSpawnVisionWorker(
  command: string,
  args: string[],
  options: Record<string, unknown>,
): VisionChildProcess {
  const child = spawn(command, args, {
    ...options,
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
    detached: process.platform !== 'win32',
    // windowsHide: console-subsystem 的 python.exe 在宿主无控制台时会被新配控制台（issue #43）。
  })
  return child as unknown as VisionChildProcess
}

export interface VisionLaunch {
  command: string
  args: string[]
  cwd: string
  env: NodeJS.ProcessEnv
  python: string
  pythonSource: 'venv' | 'python' | 'managed'
}

/** 解释器优先级：vision.venv → vision.python → 托管 venv（home/vision/venv）。 */
export function resolveVisionWorkerLaunch(
  homeDir: string,
  values: DnnConfigValues,
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
): VisionLaunch {
  const win = platform === 'win32'
  const venvPython = (dir: string): string =>
    win ? path.join(dir, 'Scripts', 'python.exe') : path.join(dir, 'bin', 'python')

  let python: string
  let pythonSource: VisionLaunch['pythonSource']
  if (values.venv.trim()) {
    const venv = values.venv.trim()
    // spec 要求 vision.venv 是绝对路径：相对路径会随 cwd 漂移，拒绝而不是按 homeDir 猜。
    if (!path.isAbsolute(venv)) {
      throw new VisionError({
        code: 'vision_custom_env_invalid',
        stage: 'config',
        message: 'vision.venv 必须是绝对路径：' + venv,
        fix: '把 vision.venv 设为绝对路径（例如 <home>/vision/venv），或在设置里清空它改用托管 venv。',
        retryable: false,
        details: { venv, reason: 'relative_path' },
      })
    }
    python = venvPython(venv)
    pythonSource = 'venv'
  } else if (values.python.trim()) {
    python = values.python.trim()
    pythonSource = 'python'
  } else {
    python = venvPython(path.join(homeDir, 'vision', 'venv'))
    pythonSource = 'managed'
  }

  const allowedEnvKeys = [
    'PATH',
    'Path',
    'SystemRoot',
    'SYSTEMROOT',
    'windir',
    'TEMP',
    'TMP',
    'TMPDIR',
    'HOME',
    'USERPROFILE',
    'LOCALAPPDATA',
    'APPDATA',
    'PATHEXT',
    'ComSpec',
    'NUMBER_OF_PROCESSORS',
    'PROCESSOR_ARCHITECTURE',
  ]
  const childEnv: NodeJS.ProcessEnv = {}
  for (const key of allowedEnvKeys) {
    const value = env[key]
    if (value !== undefined) childEnv[key] = value
  }
  // 只给 worker 它需要的东西：不继承宿主 token / API key / PYTHONPATH。
  childEnv.PYTHONUTF8 = '1'
  childEnv.PYTHONIOENCODING = 'utf-8'
  childEnv.PYTHONDONTWRITEBYTECODE = '1'
  childEnv.DSH_BLENDER_VISION_HOME = homeDir
  childEnv.DSH_BLENDER_VISION_MODELS = path.join(homeDir, 'vision', 'models')
  childEnv.DSH_BLENDER_VISION_LOG_DIR = path.join(homeDir, 'logs')

  return {
    command: python,
    args: ['-u', visionWorkerScriptPath()],
    cwd: homeDir,
    env: childEnv,
    python,
    pythonSource,
  }
}

/** worker 入口脚本：源码树 runtime/vision/worker/main.py 或构建后包根 runtime/...。 */
export function visionWorkerScriptPath(): string {
  const candidates = [
    path.join(HERE, 'worker', 'main.py'),
    path.resolve(HERE, '..', '..', '..', 'runtime', 'vision', 'worker', 'main.py'),
  ]
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  return candidates[0]
}

export type WorkerState = 'stopped' | 'starting' | 'ready' | 'stopping' | 'failed'

export interface VisionWorkerInfo {
  state: WorkerState
  pid: number | null
  startedAt: string | null
  protocolVersion: number | null
  alive: boolean
  restarts: number
  lastStartError: VisionErrorPayload | null
  idleSince: string | null
}

export interface VisionManagerStatus {
  worker: VisionWorkerInfo
  inFlight: number
}

export interface VisionDnnStatus {
  features: { dnn: boolean }
  dnn: boolean
  backend: DnnConfigValues['backend']
  device: DnnConfigValues['device']
  model: string
  venv: string
  python: string
  phase: SwitchPhase
  activeRevision: number
  pendingRevision: number | null
  waitingFor: WaitingFor | null
  lastSwitchError: SwitchError | null
  previousRevision: number | null
  previous: { revision: number; values: DnnConfigValues } | null
  active: { revision: number; values: DnnConfigValues } | null
  pending: { revision: number; values: DnnConfigValues } | null
  envLocked: boolean
  experimental: true
  docs: string[]
}

export interface VisionRequestOptions {
  timeoutMs?: number
}

export interface VisionManager {
  request(op: string, args?: Record<string, unknown>, opts?: VisionRequestOptions): Promise<unknown>
  reconcile(): Promise<ReconcileOutcome>
  status(): VisionManagerStatus
  dnnStatus(): VisionDnnStatus
  shutdown(): Promise<void>
  /** 与 shutdown 相同，便于装配层按 dispose 语义调用。 */
  dispose(): Promise<void>
}

export interface VisionManagerDeps {
  homeDir: string
  store: RuntimeConfigStore
  spawn?: VisionSpawn
  /** 覆盖解释器解析（测试 / 未来自定义安装位置）。 */
  launch?: (values: DnnConfigValues, homeDir: string) => VisionLaunch
  now?: () => number
  logFile?: string
  idleReapMs?: number
  shutdownGraceMs?: number
  forceKillWaitMs?: number
}

interface WorkerSession {
  child: VisionChildProcess
  decoder: JsonLinesDecoder
  exited: boolean
  exitCode: number | null
  exitSignal: string | null
  exitWaiters: Array<() => void>
  stopping: boolean
  startedAtMs: number
  /** 该 session 的启动/崩溃失败是否已计入重启预算（一次失败只计一次）。 */
  failureCounted: boolean
}

interface PendingRequest {
  id: string
  op: string
  session: WorkerSession
  startedAt: string
  timer: NodeJS.Timeout
  resolve: (value: unknown) => void
  reject: (err: unknown) => void
}

interface InFlightRequest {
  op: string
  startedAt: string
  requestId: string
}

function protocolError(message: string): VisionError {
  return new VisionError({
    code: 'vision_protocol_error',
    stage: 'vision',
    message,
    retryable: true,
  })
}

function workerUnavailable(message: string, fix: string): VisionError {
  return new VisionError({
    code: 'vision_worker_failed',
    stage: 'vision',
    message,
    fix,
    retryable: true,
  })
}

/** createVisionManager 内部的全部可变状态与依赖，供文件级 helper 通过参数访问。 */
interface VisionManagerContext {
  homeDir: string
  store: RuntimeConfigStore
  now: () => number
  spawnFn: VisionSpawn
  launch?: (values: DnnConfigValues, homeDir: string) => VisionLaunch
  idleReapMs: number
  shutdownGraceMs: number
  forceKillWaitMs: number
  logFile: string
  state: WorkerState
  session: WorkerSession | null
  startPromise: Promise<WorkerSession> | null
  switchPromise: Promise<ReconcileOutcome> | null
  sequence: number
  startedAt: string | null
  idleSince: string | null
  protocolVersion: number | null
  lastStartError: VisionErrorPayload | null
  restarts: number[]
  idleTimer: NodeJS.Timeout | null
  pending: Map<string, PendingRequest>
  inFlight: Map<string, InFlightRequest>
  /** shutdown/dispose 后置位：迟到的定时器与请求不得再碰已关闭的 manager。 */
  disposed: boolean
  /** start/stop 串行队列：空闲回收与懒启动不会交错出两个 child。 */
  lifecycle: Promise<unknown>
}

function iso(ms: number): string {
  return new Date(ms).toISOString()
}

function shutdownError(): VisionError {
  return new VisionError({
    code: 'vision_worker_shutdown',
    stage: 'vision',
    message: 'Gateway 正在关闭，vision 请求已取消',
    retryable: true,
  })
}

function noteRestart(ctx: VisionManagerContext): void {
  ctx.restarts.push(ctx.now())
}

/** 同一个 session 的启动/崩溃失败只计入重启预算一次。 */
function countFailureOnce(ctx: VisionManagerContext, target: WorkerSession | null): void {
  if (target) {
    if (target.failureCounted) return
    target.failureCounted = true
  }
  noteRestart(ctx)
}

/** start/stop 生命周期串行化：一次只允许一个 start 或 stop 在跑。 */
function withLifecycle<T>(ctx: VisionManagerContext, fn: () => Promise<T>): Promise<T> {
  const run = ctx.lifecycle.then(fn, fn)
  ctx.lifecycle = run.then(
    () => undefined,
    () => undefined,
  )
  return run
}

function assertRestartBudget(ctx: VisionManagerContext): void {
  const t = ctx.now()
  ctx.restarts = ctx.restarts.filter((ts) => t - ts < RESTART_WINDOW_MS)
  if (ctx.restarts.length >= MAX_RESTARTS_PER_WINDOW) {
    throw new VisionError({
      code: 'vision_worker_restart_limited',
      stage: 'vision',
      message:
        'vision worker 在 5 分钟内已重启 ' + String(ctx.restarts.length) + ' 次，暂停自动重启',
      fix: '检查 home/logs/vision-worker.log；确认 vision.venv / vision.python 可执行后重试',
      retryable: false,
    })
  }
}

function clearIdle(ctx: VisionManagerContext): void {
  if (ctx.idleTimer) {
    clearTimeout(ctx.idleTimer)
    ctx.idleTimer = null
  }
}

function touchIdle(ctx: VisionManagerContext): void {
  clearIdle(ctx)
  if (ctx.disposed || ctx.state !== 'ready' || !ctx.session) return
  ctx.idleSince = iso(ctx.now())
  ctx.idleTimer = setTimeout(() => {
    if (ctx.disposed) return
    void idleReap(ctx).catch(() => {})
  }, ctx.idleReapMs)
  ctx.idleTimer.unref?.()
}

function appendStderr(ctx: VisionManagerContext, text: string): void {
  if (!text) return
  try {
    fs.mkdirSync(path.dirname(ctx.logFile), { recursive: true })
    fs.appendFileSync(ctx.logFile, text)
    const stat = fs.statSync(ctx.logFile)
    if (stat.size > WORKER_LOG_COMPACT_AT) {
      const buf = fs.readFileSync(ctx.logFile)
      fs.writeFileSync(ctx.logFile, buf.subarray(Math.max(0, buf.length - WORKER_LOG_MAX_BYTES)))
    }
  } catch {
    /* 日志写失败不改变请求语义 */
  }
}

function writeFrame(
  ctx: VisionManagerContext,
  target: WorkerSession,
  frame: Record<string, unknown>,
): void {
  const stdin = target.child.stdin
  if (!stdin || target.exited) {
    throw workerUnavailable('vision worker stdin 已关闭', '重启 Gateway 后重试')
  }
  stdin.write(encodeFrame(frame))
}

function rejectAllPending(
  ctx: VisionManagerContext,
  error: VisionError,
  only?: WorkerSession,
): void {
  for (const [id, rec] of [...ctx.pending]) {
    if (only && rec.session !== only) continue
    ctx.pending.delete(id)
    clearTimeout(rec.timer)
    rec.reject(error)
  }
}

function forceKillTree(ctx: VisionManagerContext, child: VisionChildProcess): void {
  const pid = typeof child.pid === 'number' && child.pid > 0 ? child.pid : null
  if (pid === null) {
    try {
      child.kill('SIGKILL')
    } catch {
      /* already gone */
    }
    return
  }
  if (process.platform === 'win32') {
    try {
      const killer = ctx.spawnFn('taskkill.exe', ['/PID', String(pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      })
      killer.on('error', () => {})
    } catch {
      try {
        child.kill('SIGKILL')
      } catch {
        /* already gone */
      }
    }
    return
  }
  try {
    process.kill(-pid, 'SIGKILL')
  } catch {
    try {
      child.kill('SIGKILL')
    } catch {
      /* already gone */
    }
  }
}

function abortSession(
  ctx: VisionManagerContext,
  target: WorkerSession,
  error: VisionError,
  countRestart: boolean,
): void {
  if (target.exited || target.stopping) return
  target.stopping = true
  if (ctx.state !== 'failed') ctx.state = 'stopped'
  if (countRestart) countFailureOnce(ctx, target)
  rejectAllPending(ctx, error, target)
  forceKillTree(ctx, target.child)
  if (ctx.session === target) ctx.session = null
  clearIdle(ctx)
}

function onExit(
  ctx: VisionManagerContext,
  target: WorkerSession,
  code: unknown,
  signal: unknown,
): void {
  if (target.exited) return
  target.exited = true
  target.exitCode = isNumber(code) ? code : null
  target.exitSignal = typeof signal === 'string' ? signal : null
  const waiters = target.exitWaiters
  target.exitWaiters = []
  for (const waiter of waiters) waiter()
  if (ctx.session !== target) return
  ctx.session = null
  clearIdle(ctx)
  if (target.stopping) {
    ctx.state = 'stopped'
    return
  }
  ctx.state = 'stopped'
  countFailureOnce(ctx, target)
  const err = new VisionError({
    code: 'vision_worker_crashed',
    stage: 'vision',
    message: 'vision worker 意外退出',
    fix: '检查 home/logs/vision-worker.log；Gateway 会在重启预算内自动拉起',
    retryable: true,
    details: { exitCode: target.exitCode, signal: target.exitSignal },
  })
  ctx.lastStartError = err.toPayload()
  rejectAllPending(ctx, err, target)
}

function onChildError(ctx: VisionManagerContext, target: WorkerSession, error: unknown): void {
  if (target.exited) return
  const err = workerUnavailable(
    'vision worker 启动/运行失败：' + errorMessage(error),
    '检查 vision.venv / vision.python 是否可执行',
  )
  if (ctx.state === 'starting' && ctx.session === target) {
    ctx.session = null
    target.stopping = true
    forceKillTree(ctx, target.child)
    ctx.lastStartError = err.toPayload()
    ctx.state = 'failed'
    countFailureOnce(ctx, target)
    rejectAllPending(ctx, err, target)
    return
  }
  abortSession(ctx, target, err, true)
}

function handleFrame(
  ctx: VisionManagerContext,
  target: WorkerSession,
  frame: Record<string, unknown>,
): void {
  const id = frame.id
  if (typeof id !== 'string' || !id) {
    abortSession(ctx, target, protocolError('worker 响应缺少 id'), true)
    return
  }
  const rec = ctx.pending.get(id)
  if (!rec) return // 迟到响应（请求已超时/取消）：忽略，不制造第二个结果
  ctx.pending.delete(id)
  clearTimeout(rec.timer)
  if (frame.ok === true) {
    rec.resolve(frame.result)
    return
  }
  let payload: VisionErrorPayload
  try {
    payload = toVisionErrorPayload(frame.error)
  } catch (e) {
    rec.reject(VisionError.from(e))
    abortSession(ctx, target, VisionError.from(e), true)
    return
  }
  rec.reject(new VisionError(payload))
}

function onStdout(ctx: VisionManagerContext, target: WorkerSession, chunk: Buffer): void {
  if (target.exited) return
  let frames: Record<string, unknown>[]
  try {
    frames = target.decoder.push(chunk)
  } catch (e) {
    abortSession(ctx, target, VisionError.from(e), true)
    return
  }
  for (const frame of frames) {
    if (target.exited) return
    handleFrame(ctx, target, frame)
  }
}

function attach(ctx: VisionManagerContext, target: WorkerSession): void {
  target.child.stdout?.on('data', (chunk: unknown) => {
    if (Buffer.isBuffer(chunk)) onStdout(ctx, target, chunk)
    else if (typeof chunk === 'string') onStdout(ctx, target, Buffer.from(chunk, 'utf8'))
  })
  target.child.stderr?.on('data', (chunk: unknown) => {
    appendStderr(ctx, Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk))
  })
  target.child.on('exit', (code: unknown, signal: unknown) => onExit(ctx, target, code, signal))
  target.child.on('error', (error: unknown) => onChildError(ctx, target, error))
}

function waitExit(target: WorkerSession, ms: number): Promise<boolean> {
  if (target.exited) return Promise.resolve(true)
  return new Promise<boolean>((resolve) => {
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      resolve(false)
    }, ms)
    target.exitWaiters.push(() => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(true)
    })
  })
}

async function stopSession(
  ctx: VisionManagerContext,
  target: WorkerSession,
  graceMs: number,
): Promise<void> {
  if (target.exited) {
    if (ctx.session === target) {
      ctx.session = null
      ctx.state = 'stopped'
    }
    return
  }
  target.stopping = true
  if (ctx.session === target) ctx.state = 'stopping'
  const exited = waitExit(target, graceMs)
  try {
    writeFrame(ctx, target, { id: 'shutdown-' + String(++ctx.sequence), op: 'shutdown', args: {} })
  } catch {
    /* stdin 可能已关闭；直接走强杀 */
  }
  try {
    target.child.stdin?.end?.()
  } catch {
    /* ignore */
  }
  if (await exited) {
    if (ctx.session === target) {
      ctx.session = null
      ctx.state = 'stopped'
    }
    return
  }
  forceKillTree(ctx, target.child)
  const killed = await waitExit(target, ctx.forceKillWaitMs)
  if (!killed) {
    throw new VisionError({
      code: 'dnn_config_switch_failed',
      stage: 'config',
      message: 'vision worker 在优雅关闭与强杀后仍未退出',
      fix: '检查 vision worker 进程是否卡死；必要时重启 Gateway',
      retryable: true,
    })
  }
  if (ctx.session === target) {
    ctx.session = null
    ctx.state = 'stopped'
  }
}

function send(
  ctx: VisionManagerContext,
  target: WorkerSession,
  id: string,
  op: string,
  args: Record<string, unknown>,
  timeoutMs: number,
  config?: VisionRequestConfig,
): Promise<unknown> {
  return new Promise<unknown>((resolve, reject) => {
    const timer = setTimeout(() => {
      const rec = ctx.pending.get(id)
      if (!rec) return
      ctx.pending.delete(id)
      const err = new VisionError({
        code: 'vision_worker_timeout',
        stage: 'vision',
        message: 'vision 请求超时（' + String(timeoutMs) + 'ms）',
        fix: '检查模型大小与 worker 日志；必要时重试',
        retryable: true,
        details: { op, timeoutMs },
      })
      rec.reject(err)
      abortSession(ctx, target, err, true)
    }, timeoutMs)
    timer.unref?.()
    const rec: PendingRequest = {
      id,
      op,
      session: target,
      startedAt: iso(ctx.now()),
      timer,
      resolve: (value) => resolve(value),
      reject: (err) => reject(err),
    }
    ctx.pending.set(id, rec)
    try {
      const frame: Record<string, unknown> = { id, op, args }
      if (config) {
        // 热更新：每个 worker-backed 请求都带生效 revision + model，worker 据此合并上下文。
        frame.configRevision = config.revision
        frame.config = config
      }
      writeFrame(ctx, target, frame)
    } catch (e) {
      ctx.pending.delete(id)
      clearTimeout(timer)
      reject(VisionError.from(e))
    }
  })
}

async function startWorker(ctx: VisionManagerContext): Promise<WorkerSession> {
  if (ctx.disposed) throw shutdownError()
  try {
    assertRestartBudget(ctx)
  } catch (e) {
    // 预算耗尽也是可诊断的启动失败：结构化写回 lastStartError。
    const err = e instanceof VisionError ? e : VisionError.from(e)
    ctx.lastStartError = err.toPayload()
    ctx.state = 'failed'
    throw err
  }
  const active = ctx.store.getState().active
  const values = active?.values ?? DEFAULT_DNN_VALUES
  const launch = ctx.launch
    ? ctx.launch(values, ctx.homeDir)
    : resolveVisionWorkerLaunch(ctx.homeDir, values)
  let child: VisionChildProcess
  try {
    child = ctx.spawnFn(launch.command, launch.args, {
      cwd: launch.cwd,
      env: launch.env,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
  } catch (e) {
    const err = workerUnavailable(
      '无法启动 vision worker：' + errorMessage(e),
      '检查 vision.venv / vision.python',
    )
    ctx.lastStartError = err.toPayload()
    ctx.state = 'failed'
    countFailureOnce(ctx, null)
    throw err
  }
  const target: WorkerSession = {
    child,
    decoder: new JsonLinesDecoder(),
    exited: false,
    exitCode: null,
    exitSignal: null,
    exitWaiters: [],
    stopping: false,
    startedAtMs: ctx.now(),
    failureCounted: false,
  }
  ctx.session = target
  ctx.state = 'starting'
  ctx.idleSince = null
  attach(ctx, target)
  try {
    const helloConfig: VisionRequestConfig = {
      revision: active?.revision ?? ctx.store.getState().activeRevision,
      model: values.model,
    }
    const hello = await send(
      ctx,
      target,
      'hello-' + String(++ctx.sequence),
      'hello',
      {
        protocolVersion: PROTOCOL_VERSION,
        home: ctx.homeDir,
        modelsDir: path.join(ctx.homeDir, 'vision', 'models'),
        config: {
          revision: helloConfig.revision,
          model: values.model,
          backend: values.backend,
          device: values.device,
          python: values.python,
          venv: values.venv,
        },
      },
      HANDSHAKE_TIMEOUT_MS,
      helloConfig,
    )
    if (!isRecord(hello) || !isNumber(hello.protocolVersion)) {
      throw protocolError('worker 握手响应缺少 protocolVersion')
    }
    if (hello.protocolVersion !== PROTOCOL_VERSION) {
      throw new VisionError({
        code: 'vision_protocol_error',
        stage: 'vision',
        message:
          'worker 协议版本不匹配：期望 ' +
          String(PROTOCOL_VERSION) +
          '，实际 ' +
          String(hello.protocolVersion),
        fix: '插件安装不完整：重装或更新包内 runtime/vision/worker',
        retryable: false,
      })
    }
  } catch (e) {
    const err = e instanceof VisionError ? e : VisionError.from(e)
    target.stopping = true
    if (ctx.session === target) ctx.session = null
    forceKillTree(ctx, target.child)
    ctx.lastStartError = err.toPayload()
    ctx.state = 'failed'
    countFailureOnce(ctx, target)
    throw err
  }
  if (ctx.disposed) {
    target.stopping = true
    if (ctx.session === target) ctx.session = null
    forceKillTree(ctx, target.child)
    throw shutdownError()
  }
  ctx.state = 'ready'
  ctx.startedAt = iso(target.startedAtMs)
  ctx.protocolVersion = PROTOCOL_VERSION
  ctx.lastStartError = null
  touchIdle(ctx)
  return target
}

async function ensureWorker(ctx: VisionManagerContext): Promise<WorkerSession> {
  const session = await withLifecycle(ctx, async () => {
    if (ctx.disposed) throw shutdownError()
    if (ctx.session && !ctx.session.exited && ctx.state === 'ready') return ctx.session
    if (ctx.startPromise) return ctx.startPromise
    ctx.startPromise = startWorker(ctx)
    try {
      return await ctx.startPromise
    } finally {
      ctx.startPromise = null
    }
  })
  // 锁外再收敛 pending：handler 里不能重入生命周期队列。
  maybeDrain(ctx)
  return session
}

function oldestInFlight(ctx: VisionManagerContext): WaitingFor | null {
  for (const rec of ctx.inFlight.values()) {
    return { op: rec.op, startedAt: rec.startedAt, requestId: rec.requestId }
  }
  return null
}

function dnnState(ctx: VisionManagerContext): VisionDnnStatus {
  const st = ctx.store.getState()
  const values = st.active?.values ?? DEFAULT_DNN_VALUES
  return {
    features: { dnn: values.dnn },
    dnn: values.dnn,
    backend: values.backend,
    device: values.device,
    model: values.model,
    venv: values.venv,
    python: values.python,
    phase: st.phase,
    activeRevision: st.activeRevision,
    pendingRevision: st.pendingRevision,
    waitingFor: st.waitingFor,
    lastSwitchError: st.lastSwitchError,
    previousRevision: st.previousRevision,
    previous: st.previous ?? null,
    active: st.active ?? null,
    pending: st.pending ?? null,
    envLocked: st.envLocked,
    experimental: true,
    docs: st.docs,
  }
}

function outcomeOf(
  ctx: VisionManagerContext,
  status: ReconcileStatus,
  reason: SwitchReason,
): ReconcileOutcome {
  const st = ctx.store.getState()
  return {
    status,
    reason,
    needsWorkerRestart: false,
    activeRevision: st.activeRevision,
    pendingRevision: st.pendingRevision,
    previousRevision: st.previousRevision,
  }
}

function configBlockError(ctx: VisionManagerContext): VisionError | null {
  const st = ctx.store.getState()
  if (st.phase === 'pending' || st.phase === 'switching') {
    return new VisionError({
      code: 'dnn_config_switching',
      stage: 'config',
      message: 'DNN 配置切换中，暂不接收新的视觉请求',
      fix: '等待在途请求结束（状态见 vision_status）后重试',
      retryable: true,
      details: {
        activeRevision: st.activeRevision,
        pendingRevision: st.pendingRevision,
        waitingFor: st.waitingFor,
      },
    })
  }
  // 只有换 worker 失败才阻塞新请求；dnn_config_invalid/stale 是 apply/status 错误，
  // 保留 previous active 配置继续服务。
  if (st.phase === 'failed' && st.lastSwitchError?.code === 'dnn_config_switch_failed') {
    return new VisionError({
      code: 'dnn_config_switch_failed',
      stage: 'config',
      message: st.lastSwitchError.message,
      fix: '旧 worker 未退出；可在设置中回滚到 previous 配置后重试',
      retryable: true,
      details: {
        activeRevision: st.activeRevision,
        pendingRevision: st.pendingRevision,
      },
    })
  }
  return null
}

async function performSwitch(
  ctx: VisionManagerContext,
  trigger: ReconcileOutcome,
): Promise<ReconcileOutcome> {
  ctx.store.beginSwitch()
  const target = ctx.session
  try {
    if (target && !target.exited) await stopSession(ctx, target, ctx.shutdownGraceMs)
  } catch (e) {
    const err = e instanceof VisionError ? e : VisionError.from(e)
    ctx.store.failSwitch('dnn_config_switch_failed', err.message)
    // 已经关掉 stdin 的旧 worker 不能再服务请求：先杀进程树再释放句柄。
    const live = ctx.session
    ctx.session = null
    ctx.state = 'stopped'
    if (live && !live.exited) {
      live.stopping = true
      forceKillTree(ctx, live.child)
      await waitExit(live, ctx.forceKillWaitMs)
    }
    return outcomeOf(ctx, 'failed', trigger.reason)
  }
  ctx.store.commitActive()
  touchIdle(ctx)
  return outcomeOf(ctx, 'committed', trigger.reason)
}

/** 有现存 session（starting/ready/stopping）就算 worker 在运行，配置变更需登记 pending。 */
function workerSessionLive(ctx: VisionManagerContext): boolean {
  return ctx.session !== null && !ctx.session.exited && ctx.state !== 'stopped'
}

async function managerReconcile(ctx: VisionManagerContext): Promise<ReconcileOutcome> {
  if (ctx.disposed) return outcomeOf(ctx, 'idle', 'none')
  if (ctx.switchPromise) return ctx.switchPromise
  const outcome = ctx.store.reconcile({
    inFlight: oldestInFlight(ctx),
    workerRunning: workerSessionLive(ctx),
  })
  if (outcome.status !== 'switch_required') return outcome
  if (ctx.state === 'starting' && ctx.startPromise) {
    // 握手期不能打断：pending 已登记；startWorker 就绪后由 ensureWorker/maybeDrain 触发切换。
    return { ...outcome, status: 'pending' }
  }
  ctx.switchPromise = withLifecycle(ctx, () => performSwitch(ctx, outcome))
  try {
    return await ctx.switchPromise
  } finally {
    ctx.switchPromise = null
  }
}

function maybeDrain(ctx: VisionManagerContext): void {
  if (ctx.disposed) return
  if (ctx.inFlight.size > 0) return
  if (ctx.store.getState().phase !== 'pending') return
  void managerReconcile(ctx).catch(() => {})
}

async function idleReap(ctx: VisionManagerContext): Promise<void> {
  if (ctx.disposed) return
  // 与懒启动串行：回收中的 session 不能被另一个请求绕过。
  await withLifecycle(ctx, async () => {
    if (ctx.inFlight.size > 0) return
    const target = ctx.session
    if (!target || ctx.state !== 'ready') return
    try {
      await stopSession(ctx, target, ctx.shutdownGraceMs)
    } catch {
      abortSession(ctx, target, workerUnavailable('空闲回收失败', '重启 Gateway 后重试'), false)
    }
  })
}

async function managerRequest(
  ctx: VisionManagerContext,
  op: string,
  args: Record<string, unknown> = {},
  opts: VisionRequestOptions = {},
): Promise<unknown> {
  const outcome = await managerReconcile(ctx)
  if (ctx.disposed) throw shutdownError()
  const blocked = configBlockError(ctx)
  if (blocked) throw blocked
  if (outcome.status === 'invalid' || outcome.status === 'failed') {
    const err = configBlockError(ctx)
    if (err) throw err
  }
  const target = await ensureWorker(ctx)
  // 握手期间可能刚登记了 pending：这一请求不能用旧配置发出去。
  const blockedAfterStart = configBlockError(ctx)
  if (blockedAfterStart) throw blockedAfterStart
  const id = 'r' + String(++ctx.sequence)
  const started = iso(ctx.now())
  const rec: InFlightRequest = { op, startedAt: started, requestId: id }
  const tracked = isWorkerBackedOp(op)
  if (tracked) ctx.inFlight.set(id, rec)
  try {
    const active = ctx.store.getState().active
    const values = active?.values ?? DEFAULT_DNN_VALUES
    const result = await send(ctx, target, id, op, args, opts.timeoutMs ?? REQUEST_TIMEOUT_MS, {
      revision: ctx.store.getState().activeRevision,
      model: values.model,
    })
    return result
  } finally {
    if (tracked) {
      ctx.inFlight.delete(id)
      maybeDrain(ctx)
    }
    touchIdle(ctx)
  }
}

async function managerShutdown(ctx: VisionManagerContext): Promise<void> {
  if (ctx.disposed) return
  ctx.disposed = true
  clearIdle(ctx)
  const error = shutdownError()
  rejectAllPending(ctx, error)
  await withLifecycle(ctx, async () => {
    const target = ctx.session
    if (target && !target.exited) {
      try {
        await stopSession(ctx, target, ctx.shutdownGraceMs)
      } catch {
        abortSession(ctx, target, error, false)
      }
    }
    ctx.session = null
    ctx.state = 'stopped'
    ctx.startPromise = null
  })
}

function managerStatus(ctx: VisionManagerContext): VisionManagerStatus {
  const target = ctx.session
  const alive = !!target && !target.exited && ctx.state === 'ready'
  return {
    worker: {
      state: ctx.state,
      pid: target?.child.pid ?? null,
      startedAt: ctx.startedAt,
      protocolVersion: ctx.protocolVersion,
      alive,
      restarts: ctx.restarts.length,
      lastStartError: ctx.lastStartError,
      idleSince: ctx.idleSince,
    },
    inFlight: ctx.inFlight.size,
  }
}

export function createVisionManager(deps: VisionManagerDeps): VisionManager {
  const ctx: VisionManagerContext = {
    homeDir: deps.homeDir,
    store: deps.store,
    now: deps.now ?? Date.now,
    spawnFn: deps.spawn ?? nodeSpawnVisionWorker,
    launch: deps.launch,
    idleReapMs: deps.idleReapMs ?? IDLE_REAP_MS,
    shutdownGraceMs: deps.shutdownGraceMs ?? SHUTDOWN_GRACE_MS,
    forceKillWaitMs: deps.forceKillWaitMs ?? 1_000,
    logFile: deps.logFile ?? path.join(deps.homeDir, 'logs', 'vision-worker.log'),
    state: 'stopped',
    session: null,
    startPromise: null,
    switchPromise: null,
    sequence: 0,
    startedAt: null,
    idleSince: null,
    protocolVersion: null,
    lastStartError: null,
    restarts: [],
    idleTimer: null,
    pending: new Map(),
    inFlight: new Map(),
    disposed: false,
    lifecycle: Promise.resolve(),
  }
  return {
    request: (op, args, opts) => managerRequest(ctx, op, args, opts),
    reconcile: () => managerReconcile(ctx),
    status: () => managerStatus(ctx),
    dnnStatus: () => dnnState(ctx),
    shutdown: () => managerShutdown(ctx),
    dispose: () => managerShutdown(ctx),
  }
}
