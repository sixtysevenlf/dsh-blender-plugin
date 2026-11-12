/**
 * Python interpreter discovery for the managed vision environment (installer design).
 *
 * Discovery order: vision.venv (explicit, no fallback) -> vision.python ->
 * DSH_BLENDER_PYTHON -> platform launchers (3.13/3.12/3.11) -> python3/python.
 * Supported versions are CPython 3.11-3.13 and the candidate must be able to
 * import venv. Every subprocess goes through an injected CommandRunner so the
 * state machine can be tested without spawning.
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { VisionError } from './protocol.js'

export const SUPPORTED_MAJOR = 3
export const SUPPORTED_MIN_MINOR = 11
export const SUPPORTED_MAX_MINOR = 13
export const DISCOVERY_TIMEOUT_MS = 15_000

export interface PythonVersion {
  major: number
  minor: number
  patch: number
}

export interface CommandResult {
  code: number | null
  stdout: string
  stderr: string
}

export interface CommandOptions {
  env?: NodeJS.ProcessEnv
  cwd?: string
  timeoutMs?: number
}

export type CommandRunner = (
  command: string,
  args: string[],
  options: CommandOptions,
) => Promise<CommandResult>

export interface PythonProbe {
  ok: true
  version: PythonVersion
  versionText: string
  hasVenv: boolean
  cv2: string | null
  numpy: string | null
  onnxruntime: string | null
}

export interface PythonCandidateValues {
  python: string
  venv: string
  backend: string
}

export interface PythonDetection {
  interpreter: string
  args: string[]
  source: 'venv' | 'python' | 'env' | 'system'
  version: PythonVersion
  versionText: string
  probe: PythonProbe
}

export interface PythonDetectOptions {
  run: CommandRunner
  values: PythonCandidateValues
  env?: NodeJS.ProcessEnv
  platform?: NodeJS.Platform
  timeoutMs?: number
}

/** Prints one JSON line; module imports never fail the process. */
export const PYTHON_PROBE_SCRIPT = [
  'import json, sys',
  'out = {',
  '  "version": [sys.version_info.major, sys.version_info.minor, sys.version_info.micro],',
  '  "versionText": ".".join(str(part) for part in sys.version_info[:3]),',
  '  "hasVenv": False, "cv2": None, "numpy": None, "onnxruntime": None,',
  '}',
  'try:',
  '  import venv',
  '  out["hasVenv"] = True',
  'except Exception:',
  '  pass',
  'for _name in ("cv2", "numpy", "onnxruntime"):',
  '  try:',
  '    _module = __import__(_name)',
  '    out[_name] = getattr(_module, "__version__", None)',
  '  except Exception:',
  '    out[_name] = None',
  'print(json.dumps(out))',
].join('\n')

function readVersion(raw: unknown): PythonVersion | null {
  if (!Array.isArray(raw) || raw.length < 3) return null
  const [major, minor, patch] = raw
  if (typeof major !== 'number' || typeof minor !== 'number' || typeof patch !== 'number')
    return null
  if (!Number.isInteger(major) || !Number.isInteger(minor) || !Number.isInteger(patch)) return null
  return { major, minor, patch }
}

export function parseVersionText(text: string): PythonVersion | null {
  const match = /(\d+)\.(\d+)\.(\d+)/.exec(text)
  if (!match) return null
  return { major: Number(match[1]), minor: Number(match[2]), patch: Number(match[3]) }
}

/** Parses the probe JSON; malformed or module-looking payloads return null. */
export function parsePythonProbe(stdout: string): PythonProbe | null {
  let raw: unknown
  try {
    raw = JSON.parse(stdout.trim())
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const record = raw as Record<string, unknown>
  const version = readVersion(record.version)
  if (!version) return null
  if (typeof record.hasVenv !== 'boolean') return null
  const versionText =
    typeof record.versionText === 'string' && record.versionText
      ? record.versionText
      : version.major + '.' + version.minor + '.' + version.patch
  const optional = (value: unknown): string | null =>
    typeof value === 'string' && value ? value : null
  return {
    ok: true,
    version,
    versionText,
    hasVenv: record.hasVenv,
    cv2: optional(record.cv2),
    numpy: optional(record.numpy),
    onnxruntime: optional(record.onnxruntime),
  }
}

export function isSupportedPython(version: PythonVersion): boolean {
  return (
    version.major === SUPPORTED_MAJOR &&
    version.minor >= SUPPORTED_MIN_MINOR &&
    version.minor <= SUPPORTED_MAX_MINOR
  )
}

export function venvPythonPath(
  venvDir: string,
  platform: NodeJS.Platform = process.platform,
): string {
  return platform === 'win32'
    ? path.join(venvDir, 'Scripts', 'python.exe')
    : path.join(venvDir, 'bin', 'python')
}

export async function probePython(
  run: CommandRunner,
  command: string,
  args: string[] = [],
  options: CommandOptions = {},
): Promise<PythonProbe | null> {
  let result: CommandResult
  try {
    result = await run(command, [...args, '-c', PYTHON_PROBE_SCRIPT], options)
  } catch {
    return null
  }
  if (result.code !== 0 || !result.stdout.trim()) return null
  return parsePythonProbe(result.stdout)
}

function customEnvError(
  message: string,
  details: Record<string, unknown>,
  fix = '检查 vision.venv 指向的目录，或清空 vision.venv 让插件创建托管环境。',
): VisionError {
  return new VisionError({
    code: 'vision_custom_env_invalid',
    stage: 'config',
    message,
    fix,
    retryable: false,
    details,
  })
}

async function validateCustomVenv(
  options: PythonDetectOptions,
  venv: string,
): Promise<PythonDetection> {
  const platform = options.platform ?? process.platform
  const python = venvPythonPath(venv, platform)
  // spec 要求 vision.venv 是绝对路径：相对路径会随 cwd 漂移，拒绝而不是按 cwd 猜。
  if (!path.isAbsolute(venv)) {
    throw customEnvError(
      'vision.venv 必须是绝对路径：' + venv,
      { reason: 'relative_path', venv, python },
      '把 vision.venv 设为绝对路径（例如 <home>/vision/venv），或清空它改用托管 venv。',
    )
  }
  if (!fs.existsSync(venv)) {
    throw customEnvError('vision.venv 不存在：' + venv, { reason: 'not_found', venv, python })
  }
  let isDirectory: boolean
  try {
    isDirectory = fs.statSync(venv).isDirectory()
  } catch {
    isDirectory = false
  }
  if (!isDirectory)
    throw customEnvError('vision.venv 不是目录：' + venv, { reason: 'not_directory', venv, python })
  if (!fs.existsSync(python)) {
    throw customEnvError('vision.venv 里没有可执行的 python：' + python, {
      reason: 'no_python',
      venv,
      python,
    })
  }
  const probe = await probePython(options.run, python, [], {
    timeoutMs: options.timeoutMs ?? DISCOVERY_TIMEOUT_MS,
    env: options.env,
  })
  if (!probe) {
    throw customEnvError('无法运行 vision.venv 的 python：' + python, {
      reason: 'probe_failed',
      venv,
      python,
    })
  }
  if (!isSupportedPython(probe.version)) {
    throw customEnvError('vision.venv 的 Python 版本不受支持：' + probe.versionText, {
      reason: 'version',
      venv,
      python,
      version: probe.versionText,
      supported: '3.11-3.13',
    })
  }
  const missing: string[] = []
  if (!probe.cv2) missing.push('cv2')
  if (!probe.numpy) missing.push('numpy')
  if (options.values.backend === 'onnxruntime' && !probe.onnxruntime) missing.push('onnxruntime')
  if (missing.length > 0) {
    throw customEnvError(
      'vision.venv 缺少模块：' + missing.join(', '),
      {
        reason: 'missing_modules',
        venv,
        python,
        backend: options.values.backend,
        missingModules: missing,
        version: probe.versionText,
      },
      '在自备 venv 里安装 ' + missing.join('/') + '（插件不会替你 pip），或清空 vision.venv。',
    )
  }
  return {
    interpreter: python,
    args: [],
    source: 'venv',
    version: probe.version,
    versionText: probe.versionText,
    probe,
  }
}

interface Candidate {
  command: string
  args: string[]
  source: PythonDetection['source']
}

export function discoveryCandidates(
  values: PythonCandidateValues,
  env: NodeJS.ProcessEnv,
  platform: NodeJS.Platform = process.platform,
): Candidate[] {
  const candidates: Candidate[] = []
  const push = (command: string, args: string[], source: Candidate['source']): void => {
    if (!command) return
    if (
      candidates.some(
        (item) => item.command === command && item.args.join('\u0000') === args.join('\u0000'),
      )
    )
      return
    candidates.push({ command, args, source })
  }
  push(values.python.trim(), [], 'python')
  push((env.DSH_BLENDER_PYTHON ?? '').trim(), [], 'env')
  if (platform === 'win32') {
    for (const minor of [13, 12, 11])
      push('py', ['-' + String(SUPPORTED_MAJOR) + '.' + String(minor)], 'system')
  } else {
    for (const minor of [13, 12, 11])
      push('python' + String(SUPPORTED_MAJOR) + '.' + String(minor), [], 'system')
  }
  push('python' + String(SUPPORTED_MAJOR), [], 'system')
  push('python', [], 'system')
  return candidates
}

export async function discoverPython(options: PythonDetectOptions): Promise<PythonDetection> {
  const platform = options.platform ?? process.platform
  const env = options.env ?? process.env
  const venv = options.values.venv.trim()
  if (venv) return validateCustomVenv(options, venv)
  const attempts: string[] = []
  for (const candidate of discoveryCandidates(options.values, env, platform)) {
    const probe = await probePython(options.run, candidate.command, candidate.args, {
      timeoutMs: options.timeoutMs ?? DISCOVERY_TIMEOUT_MS,
      env,
    })
    if (!probe) {
      attempts.push(
        candidate.command +
          (candidate.args.length > 0 ? ' ' + candidate.args.join(' ') : '') +
          ': not found or no probe output',
      )
      continue
    }
    if (!isSupportedPython(probe.version)) {
      attempts.push(candidate.command + ': unsupported version ' + probe.versionText)
      continue
    }
    if (!probe.hasVenv) {
      attempts.push(candidate.command + ': cannot import venv')
      continue
    }
    return {
      interpreter: candidate.command,
      args: candidate.args,
      source: candidate.source,
      version: probe.version,
      versionText: probe.versionText,
      probe,
    }
  }
  throw new VisionError({
    code: 'python_missing',
    stage: 'config',
    message: '找不到可用的 Python 3.11-3.13 解释器',
    fix: '安装 CPython 3.11-3.13，或在 vision.python / vision.venv 里指定解释器；插件只支持这两个入口。',
    retryable: false,
    details: { attempts, supported: '3.11-3.13' },
  })
}

/** Production runner: no shell, bounded output, windowsHide. */
export function createProcessRunner(): CommandRunner {
  return (command, args, options) =>
    new Promise((resolve) => {
      const child = spawn(command, args, {
        cwd: options.cwd,
        env: options.env,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      let stdout = ''
      let stderr = ''
      let settled = false
      const finish = (code: number | null): void => {
        if (settled) return
        settled = true
        resolve({ code, stdout, stderr })
      }
      const timer = setTimeout(() => {
        try {
          child.kill()
        } catch {
          /* already gone */
        }
        finish(null)
      }, options.timeoutMs ?? DISCOVERY_TIMEOUT_MS)
      child.stdout?.on('data', (chunk: Buffer) => {
        if (stdout.length < 64_000) stdout += chunk.toString('utf8')
      })
      child.stderr?.on('data', (chunk: Buffer) => {
        if (stderr.length < 64_000) stderr += chunk.toString('utf8')
      })
      child.on('error', (error: Error) => {
        clearTimeout(timer)
        stderr += error.message
        finish(null)
      })
      child.on('close', (code: number | null) => {
        clearTimeout(timer)
        finish(code)
      })
    })
}
