/**
 * Host-side, one-shot DNN availability probe (DNN probe).
 *
 * The host resolves an interpreter (custom venv -> explicit python -> managed
 * venv -> system discovery), spawns fixed `probe.py` WITHOUT a shell, sends one
 * JSON request on stdin, enforces a 10 s budget and returns the checks.
 *
 * It never writes runtime.json, never starts the resident worker, never loads
 * an ONNX file, never creates a session/GPU allocation, never touches the
 * network and never installs anything. The child runs with
 * PYTHONDONTWRITEBYTECODE=1 and `-B` so even imports leave no __pycache__.
 */
import { spawn as nodeSpawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { asRecord, isRecord, text } from '../contracts/dynamic.js'
import { dnnPaths, getDnnHome, probeScriptPath } from './dnn-home.js'

export const DNN_PROBE_TIMEOUT_MS = 10_000
const MAX_STDOUT_BYTES = 1 << 20

export interface DnnProbeCandidate {
  features: { dnn: boolean }
  vision: { python: string; venv: string; model: string }
  dnn: { backend: 'opencv' | 'onnxruntime'; device: 'cpu' | 'auto' | 'gpu' }
}

export interface ProbeCheck {
  id: string
  label: string
  status: 'pass' | 'warn' | 'fail'
  detail: string
  fix?: string
}

export interface ProbeSummary {
  pass: number
  warn: number
  fail: number
}

export interface DnnProbeResult {
  ok: boolean
  checks: ProbeCheck[]
  summary: ProbeSummary
  elapsedMs: number
  candidate: DnnProbeCandidate | null
  code?: string
  message?: string
}

export interface ProbeInterpreter {
  command: string
  args: string[]
  source: 'venv' | 'python' | 'managed' | 'env' | 'system'
}

export interface ProbeProcess {
  stdin: {
    write: (chunk: string) => unknown
    end: () => unknown
    on?: (event: 'error', cb: (error: unknown) => void) => unknown
  } | null
  stdout: { on: (event: 'data', cb: (chunk: unknown) => void) => unknown } | null
  stderr: { on: (event: 'data', cb: (chunk: unknown) => void) => unknown } | null
  on: (event: string, cb: (...args: unknown[]) => void) => unknown
  kill: (signal?: string) => unknown
}

export type ProbeSpawn = (
  command: string,
  args: string[],
  options: Record<string, unknown>,
) => ProbeProcess

export interface ProbeOptions {
  candidate: unknown
  home?: string
  modelsDir?: string
  envLocked?: boolean
  /** Effective value locked by DSH_BLENDER_DNN (null when absent/invalid). */
  envDnnValue?: boolean | null
  env?: NodeJS.ProcessEnv
  timeoutMs?: number
  probeScript?: string
  spawn?: ProbeSpawn
  which?: (name: string) => string | null
  /** Test seam: bypass interpreter resolution entirely. */
  resolveInterpreter?: (candidate: DnnProbeCandidate) => ProbeInterpreter | null
}

export function resetProbeCacheForTests(): void {
  /* no process-wide probe cache exists by design: every click re-checks */
}

function failCheck(id: string, label: string, detail: string, fix: string): ProbeCheck {
  return { id, label, status: 'fail', detail, fix }
}

function passCheck(id: string, label: string, detail: string): ProbeCheck {
  return { id, label, status: 'pass', detail }
}

function summarize(checks: readonly ProbeCheck[]): ProbeSummary {
  const out: ProbeSummary = { pass: 0, warn: 0, fail: 0 }
  for (const item of checks) out[item.status] += 1
  return out
}

function validBackend(value: unknown): value is 'opencv' | 'onnxruntime' {
  return value === 'opencv' || value === 'onnxruntime'
}

function validDevice(value: unknown): value is 'cpu' | 'auto' | 'gpu' {
  return value === 'cpu' || value === 'auto' || value === 'gpu'
}

export function normalizeDnnCandidate(
  raw: unknown,
): { ok: true; candidate: DnnProbeCandidate } | { ok: false; message: string } {
  const body = asRecord(raw)
  const features = asRecord(body.features)
  const vision = asRecord(body.vision)
  // The config card and runtime.json use the nested vision.dnn shape; the probe
  // wire format keeps it at the top level. Accept both so the RPC and probe share
  // one candidate builder.
  const nestedDnn = asRecord(vision.dnn)
  const dnn = asRecord(body.dnn)
  const backend = dnn.backend ?? nestedDnn.backend
  const device = dnn.device ?? nestedDnn.device
  if (typeof features.dnn !== 'boolean') {
    return { ok: false, message: 'features.dnn must be a boolean' }
  }
  for (const key of ['python', 'venv', 'model'] as const) {
    if (vision[key] !== undefined && typeof vision[key] !== 'string') {
      return { ok: false, message: 'vision.' + key + ' must be a string' }
    }
  }
  if (!validBackend(backend))
    return { ok: false, message: 'dnn.backend must be opencv or onnxruntime' }
  if (!validDevice(device)) return { ok: false, message: 'dnn.device must be cpu, auto or gpu' }
  return {
    ok: true,
    candidate: {
      features: { dnn: features.dnn },
      vision: {
        python: text(vision.python),
        venv: text(vision.venv),
        model: text(vision.model),
      },
      dnn: { backend, device },
    },
  }
}

function isFile(file: string): boolean {
  try {
    return fs.statSync(file).isFile()
  } catch {
    return false
  }
}

function defaultWhich(name: string): string | null {
  const env = process.env
  const exts =
    process.platform === 'win32'
      ? (env.PATHEXT || '.EXE;.CMD;.BAT;.COM').split(';').filter(Boolean)
      : ['']
  const dirs = (env.PATH || '').split(path.delimiter).filter(Boolean)
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = path.join(
        dir,
        process.platform === 'win32' ? name + ext.toLowerCase() : name + ext,
      )
      if (isFile(candidate)) return candidate
      if (process.platform === 'win32') {
        const upper = path.join(dir, name + ext.toUpperCase())
        if (isFile(upper)) return upper
      }
    }
  }
  return null
}

function venvPython(venv: string): string | null {
  const trimmed = venv.trim()
  if (!trimmed) return null
  const candidates =
    process.platform === 'win32'
      ? [path.join(trimmed, 'Scripts', 'python.exe'), path.join(trimmed, 'python.exe'), trimmed]
      : [path.join(trimmed, 'bin', 'python'), path.join(trimmed, 'bin', 'python3'), trimmed]
  for (const candidate of candidates) if (isFile(candidate)) return candidate
  return null
}

/**
 * Probe order: vision.venv -> vision.python -> managed venv -> system
 * discovery (with DSH_BLENDER_PYTHON as an explicit override before discovery).
 */
export function resolveProbeInterpreter(
  candidate: DnnProbeCandidate,
  options: { home: string; env: NodeJS.ProcessEnv; which?: (name: string) => string | null },
): ProbeInterpreter | null {
  const which = options.which ?? defaultWhich
  const fromVenv = venvPython(candidate.vision.venv)
  if (fromVenv) return { command: fromVenv, args: [], source: 'venv' }
  const explicit = candidate.vision.python.trim()
  if (explicit) {
    if (path.isAbsolute(explicit)) {
      if (isFile(explicit)) return { command: explicit, args: [], source: 'python' }
    } else {
      const found = which(explicit)
      if (found) return { command: found, args: [], source: 'python' }
    }
  }
  const managed = venvPython(path.join(options.home, 'vision', 'venv'))
  if (managed) return { command: managed, args: [], source: 'managed' }
  const override = text(options.env.DSH_BLENDER_PYTHON).trim()
  if (override) {
    if (path.isAbsolute(override) && isFile(override)) {
      return { command: override, args: [], source: 'env' }
    }
    const found = which(override)
    if (found) return { command: found, args: [], source: 'env' }
  }
  const names =
    process.platform === 'win32'
      ? ['python3.13', 'python3.12', 'python3.11', 'python3', 'python']
      : ['python3.13', 'python3.12', 'python3.11', 'python3', 'python']
  for (const name of names) {
    const found = which(name)
    if (found) return { command: found, args: [], source: 'system' }
  }
  const py = which('py')
  if (py) return { command: py, args: [], source: 'system' }
  return null
}

function defaultSpawn(
  command: string,
  args: string[],
  options: Record<string, unknown>,
): ProbeProcess {
  return nodeSpawn(command, args, options) as unknown as ProbeProcess
}

/**
 * Environment whitelist for the probe child. The probe runs a user-selected
 * interpreter/venv, so the host must never hand it the full process.env
 * (tokens, API keys, cloud credentials). PATH/HOME/TEMP keep Python and the OS
 * loader working; PYTHON* keeps interpreter tuning; *_PROXY keeps downloads
 * reachable behind a corporate proxy.
 */
const PROBE_ENV_ALLOW = new Set([
  'PATH',
  'HOME',
  'USERPROFILE',
  'SYSTEMROOT',
  'SYSTEMDRIVE',
  'TEMP',
  'TMP',
  'PATHEXT',
  'COMSPEC',
  'WINDIR',
])

function probeEnvAllowed(key: string): boolean {
  const upper = key.toUpperCase()
  return PROBE_ENV_ALLOW.has(upper) || upper.startsWith('PYTHON') || upper.endsWith('_PROXY')
}

function probeEnv(env: NodeJS.ProcessEnv): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(env)) {
    if (typeof value !== 'string') continue
    if (probeEnvAllowed(key)) out[key] = value
  }
  out.PYTHONIOENCODING = 'utf-8'
  out.PYTHONUTF8 = '1'
  out.PYTHONDONTWRITEBYTECODE = '1'
  return out
}

function parseProbeStdout(
  raw: string,
): { ok: true; body: Record<string, unknown> } | { ok: false; message: string } {
  const trimmed = raw.trim()
  if (!trimmed) return { ok: false, message: 'probe.py produced no output' }
  const candidates = [trimmed, ...trimmed.split(/\r?\n/).reverse()]
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate) as unknown
      if (isRecord(parsed)) return { ok: true, body: parsed }
    } catch {
      /* try the next candidate */
    }
  }
  return { ok: false, message: 'probe.py output is not JSON: ' + trimmed.slice(0, 300) }
}

function errorResult(
  code: string,
  message: string,
  checks: ProbeCheck[],
  startedAt: number,
  candidate: DnnProbeCandidate | null,
): DnnProbeResult {
  return {
    ok: false,
    code,
    message,
    checks,
    summary: summarize(checks),
    elapsedMs: Date.now() - startedAt,
    candidate,
  }
}

interface ProbeRunState {
  done: boolean
  timerRef: { id: ReturnType<typeof setTimeout> | undefined }
  stdout: string
  stdoutBytes: number
  stderr: string
  timedOut: boolean
  /** Set by the child's 'close' event; finishProbe only kills a live child. */
  exited: boolean
  spawnError: Error | null
  resolve: (result: DnnProbeResult) => void
  child: ProbeProcess
}

interface ProbeRunContext {
  candidate: DnnProbeCandidate
  checks: ProbeCheck[]
  payload: string
  spawn: ProbeSpawn
  timeoutMs: number
  interpreter: ProbeInterpreter
  script: string
  env: NodeJS.ProcessEnv
  startedAt: number
}

function finishProbe(state: ProbeRunState, result: DnnProbeResult): void {
  if (state.done) return
  state.done = true
  if (state.timerRef.id !== undefined) clearTimeout(state.timerRef.id)
  // Never leave a child running after we stopped caring about its output (a
  // second apply/probe would otherwise race it for the interpreter and the
  // open pipes). 'close' already fired for normal exits.
  if (!state.exited) {
    try {
      state.child.kill('SIGKILL')
    } catch {
      /* already gone */
    }
  }
  state.resolve(result)
}

function startProbeTimer(
  state: ProbeRunState,
  timeoutMs: number,
  checks: ProbeCheck[],
  startedAt: number,
  candidate: DnnProbeCandidate,
): void {
  state.timerRef.id = setTimeout(() => {
    state.timedOut = true
    try {
      state.child.kill('SIGKILL')
    } catch {
      /* already gone */
    }
    const failure = failCheck(
      'timeout',
      'probe 超时',
      '超过 ' + String(timeoutMs) + 'ms 未返回（冷启动的自定义 venv 可能较慢）',
      '重试；若稳定超时，README/文档记录了 probe 预算上限',
    )
    finishProbe(
      state,
      errorResult(
        'dnn_probe_timeout',
        'probe timed out after ' + String(timeoutMs) + 'ms',
        [...checks, failure],
        startedAt,
        candidate,
      ),
    )
  }, timeoutMs)
}

function onProbeSpawnError(
  state: ProbeRunState,
  error: unknown,
  checks: ProbeCheck[],
  startedAt: number,
  candidate: DnnProbeCandidate,
): void {
  state.spawnError = error instanceof Error ? error : new Error(text(error))
  try {
    state.child.kill('SIGKILL')
  } catch {
    /* ignore */
  }
  const failure = failCheck(
    'spawn',
    '启动 probe',
    state.spawnError.message,
    '检查解释器路径、可执行权限与 venv 是否完整',
  )
  finishProbe(
    state,
    errorResult(
      'dnn_probe_failed',
      state.spawnError.message,
      [...checks, failure],
      startedAt,
      candidate,
    ),
  )
}

function onProbeStdoutData(state: ProbeRunState, chunk: unknown): void {
  const piece = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk))
  state.stdoutBytes += piece.byteLength
  if (state.stdoutBytes > MAX_STDOUT_BYTES) {
    state.stdout = ''
    try {
      state.child.kill('SIGKILL')
    } catch {
      /* ignore */
    }
    return
  }
  state.stdout += piece.toString('utf8')
}

function onProbeStderrData(state: ProbeRunState, chunk: unknown): void {
  state.stderr += Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk)
}

function parsePythonChecks(body: Record<string, unknown>): ProbeCheck[] {
  const pythonChecks: ProbeCheck[] = []
  const rawChecks = Array.isArray(body.checks) ? body.checks : []
  for (const raw of rawChecks) {
    const item = asRecord(raw)
    const id = text(item.id)
    const status = item.status
    if (!id || (status !== 'pass' && status !== 'warn' && status !== 'fail')) continue
    pythonChecks.push({
      id,
      label: text(item.label) || id,
      status,
      detail: text(item.detail),
      ...(text(item.fix) ? { fix: text(item.fix) } : {}),
    })
  }
  return pythonChecks
}

function onProbeClose(
  state: ProbeRunState,
  code: unknown,
  checks: ProbeCheck[],
  startedAt: number,
  candidate: DnnProbeCandidate,
): void {
  state.exited = true
  if (state.done) return
  if (state.timedOut) return
  if (state.spawnError) return
  if (state.stdoutBytes > MAX_STDOUT_BYTES) {
    finishProbe(
      state,
      errorResult(
        'dnn_probe_failed',
        'probe.py output exceeded 1 MiB',
        checks,
        startedAt,
        candidate,
      ),
    )
    return
  }
  const exitCode = typeof code === 'number' ? code : null
  if (exitCode !== 0) {
    finishProbe(
      state,
      errorResult(
        'dnn_probe_failed',
        'probe.py exited with ' +
          String(exitCode) +
          (state.stderr ? ': ' + state.stderr.trim().slice(0, 300) : ''),
        checks,
        startedAt,
        candidate,
      ),
    )
    return
  }
  const parsed = parseProbeStdout(state.stdout)
  if (!parsed.ok) {
    finishProbe(
      state,
      errorResult('dnn_probe_failed', parsed.message, checks, startedAt, candidate),
    )
    return
  }
  const pythonChecks = parsePythonChecks(parsed.body)
  const merged = [...checks, ...pythonChecks]
  finishProbe(state, {
    ok: summarize(merged).fail === 0,
    checks: merged,
    summary: summarize(merged),
    elapsedMs: Date.now() - startedAt,
    candidate,
  })
}

function runProbeProcess(ctx: ProbeRunContext): Promise<DnnProbeResult> {
  return new Promise<DnnProbeResult>((resolve) => {
    const state: ProbeRunState = {
      done: false,
      timerRef: { id: undefined },
      stdout: '',
      stdoutBytes: 0,
      stderr: '',
      timedOut: false,
      exited: false,
      spawnError: null,
      resolve,
      child: null as unknown as ProbeProcess,
    }
    let child: ProbeProcess
    try {
      child = ctx.spawn(ctx.interpreter.command, [...ctx.interpreter.args, '-B', ctx.script], {
        shell: false,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: probeEnv(ctx.env),
      })
    } catch (error) {
      ctx.checks.push(
        failCheck(
          'spawn',
          '启动 probe',
          error instanceof Error ? error.message : String(error),
          '检查解释器路径与权限',
        ),
      )
      finishProbe(
        state,
        errorResult('dnn_probe_failed', 'spawn failed', ctx.checks, ctx.startedAt, ctx.candidate),
      )
      return
    }
    state.child = child
    startProbeTimer(state, ctx.timeoutMs, ctx.checks, ctx.startedAt, ctx.candidate)
    child.on('error', (error: unknown) =>
      onProbeSpawnError(state, error, ctx.checks, ctx.startedAt, ctx.candidate),
    )
    child.stdout?.on('data', (chunk: unknown) => onProbeStdoutData(state, chunk))
    child.stderr?.on('data', (chunk: unknown) => onProbeStderrData(state, chunk))
    // A closed pipe rejects the async write with EPIPE; without a listener that
    // is an unhandled 'error' on the stream and takes the host down.
    child.stdin?.on?.('error', (error: unknown) => {
      if (state.done) return
      finishProbe(
        state,
        errorResult(
          'dnn_probe_failed',
          'failed to send probe request: ' +
            (error instanceof Error ? error.message : String(error)),
          ctx.checks,
          ctx.startedAt,
          ctx.candidate,
        ),
      )
    })
    child.on('close', (code: unknown) =>
      onProbeClose(state, code, ctx.checks, ctx.startedAt, ctx.candidate),
    )
    try {
      child.stdin?.write(ctx.payload)
      child.stdin?.end()
    } catch (error) {
      finishProbe(
        state,
        errorResult(
          'dnn_probe_failed',
          'failed to send probe request: ' +
            (error instanceof Error ? error.message : String(error)),
          ctx.checks,
          ctx.startedAt,
          ctx.candidate,
        ),
      )
    }
  })
}

export async function probeDnn(options: ProbeOptions): Promise<DnnProbeResult> {
  const startedAt = Date.now()
  const env = options.env ?? process.env
  const home = path.resolve(options.home ?? getDnnHome())
  const normalized = normalizeDnnCandidate(options.candidate)
  if (!normalized.ok) {
    return errorResult('invalid_params', normalized.message, [], startedAt, null)
  }
  const candidate = normalized.candidate
  const checks: ProbeCheck[] = []
  const lockedValue = options.envDnnValue
  if (
    options.envLocked &&
    typeof lockedValue === 'boolean' &&
    candidate.features.dnn !== lockedValue
  ) {
    checks.push({
      id: 'feature',
      label: 'features.dnn',
      status: 'warn',
      detail:
        '候选 features.dnn=' +
        String(candidate.features.dnn) +
        '，但 DSH_BLENDER_DNN 锁定为 ' +
        String(lockedValue) +
        '；保存会被拒绝（dnn_env_locked）',
      fix: '移除 DSH_BLENDER_DNN，或让开关与锁定值一致后重启 Gateway',
    })
  } else {
    checks.push(
      passCheck(
        'feature',
        'features.dnn',
        candidate.features.dnn
          ? 'true' + (options.envLocked ? '；DSH_BLENDER_DNN 已锁定该值' : '')
          : 'false；DNN 未启用，模型缺失只会是 warning',
      ),
    )
  }
  if (candidate.dnn.backend === 'opencv' && candidate.dnn.device !== 'cpu') {
    checks.push(
      failCheck(
        'candidate',
        'backend/device 组合',
        'backend=opencv 只支持 CPU，device=' + candidate.dnn.device + ' 需要自备 ONNX Runtime venv',
        '把 vision.dnn.device 改成 cpu，或设置 vision.venv 与 backend=onnxruntime',
      ),
    )
    return errorResult(
      'invalid_params',
      'opencv backend only supports device=cpu',
      checks,
      startedAt,
      candidate,
    )
  }
  const interpreter = options.resolveInterpreter
    ? options.resolveInterpreter(candidate)
    : resolveProbeInterpreter(candidate, { home, env, which: options.which })
  if (!interpreter) {
    checks.push(
      failCheck(
        'interpreter',
        'Python 解释器',
        '未找到可用解释器（vision.venv / vision.python / 托管 venv / 系统 Python 均不可用）',
        '安装 Python 3.11–3.13 并让插件完成托管 venv 安装，或设置 vision.venv 指向自备环境',
      ),
    )
    return errorResult(
      'dnn_probe_failed',
      'no usable Python interpreter',
      checks,
      startedAt,
      candidate,
    )
  }
  checks.push(
    passCheck('interpreter', 'Python 解释器', interpreter.source + ': ' + interpreter.command),
  )

  const script = options.probeScript ?? probeScriptPath()
  const modelsDir = options.modelsDir ?? dnnPaths(home).modelsDir
  if (!fs.existsSync(script)) {
    checks.push(
      failCheck(
        'probe_script',
        'probe.py',
        '未找到 ' + script,
        '插件安装不完整；重新安装/更新插件',
      ),
    )
    return errorResult('dnn_probe_failed', 'probe.py not found', checks, startedAt, candidate)
  }
  const payload = JSON.stringify({
    candidate,
    home,
    modelsDir,
    workerDir: path.dirname(script),
  })
  const spawn = options.spawn ?? defaultSpawn
  const timeoutMs = options.timeoutMs ?? DNN_PROBE_TIMEOUT_MS

  return runProbeProcess({
    candidate,
    checks,
    payload,
    spawn,
    timeoutMs,
    interpreter,
    script,
    env,
    startedAt,
  })
}
