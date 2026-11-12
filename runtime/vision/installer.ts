/**
 * Managed Python/OpenCV environment for the vision worker (installer design).
 *
 * The installer is deliberately boring: it resolves an interpreter, creates a
 * venv, installs the hash-locked requirements, verifies cv2/numpy with a small
 * contour check and writes a host-readable state file. It never installs into
 * the system Python and never pip-installs into a user-provided vision.venv.
 */
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { fileURLToPath } from 'node:url'

import {
  createProcessRunner,
  discoverPython,
  probePython,
  venvPythonPath,
  type CommandRunner,
  type PythonCandidateValues,
} from './python-detect.js'
import { VisionError, type VisionErrorPayload } from './protocol.js'
import { writeFileAtomic } from './runtime-config.js'

export const INSTALL_STATE_VERSION = 1
export const STATE_FILE = 'state.json'
export const INSTALL_LOCK_FILE = 'install.lock'
export const INSTALL_LOCK_STALE_MS = 10 * 60_000
export const PIP_MAX_ATTEMPTS = 3
export const REQUIREMENTS_LOCK = 'requirements.lock'
export const PBS_RELEASE = '20261110'
export const PBS_PYTHON_VERSION = '3.13.18'
export const INSTALL_LOG = 'vision-install.log'

export type InstallStatus =
  | 'not_installed'
  | 'resolving_python'
  | 'downloading_python'
  | 'creating_venv'
  | 'installing'
  | 'verifying'
  | 'ready'
  | 'failed'

export type InstallSource = 'managed' | 'custom' | null

export type InstallError = VisionErrorPayload

export interface InstallLicense {
  name: string
  version: string | null
  license: string
  path: string | null
}

export interface VisionEnvInfo {
  source: 'managed' | 'custom'
  venv: string
  python: string | null
  pythonVersion: string | null
  cv2: string | null
  numpy: string | null
  onnxruntime: string | null
  backend: string
}

export interface VisionInstallState {
  version: 1
  status: InstallStatus
  source: InstallSource
  python: string | null
  pythonVersion: string | null
  venv: string | null
  backend: string
  cv2: string | null
  numpy: string | null
  onnxruntime: string | null
  updatedAt: string
  error: InstallError | null
  logPath: string
  hashPolicy: 'require-hashes' | 'pinned-only'
  hashVerified: boolean
  attempts: number
  licenses: InstallLicense[]
  noticesPath: string | null
}

export interface VisionInstallStatus extends VisionInstallState {
  env: VisionEnvInfo
}

/** 安装各阶段的子进程预算；15 s 的发现超时只适合探针，不够 venv/pip/tar。 */
export interface VisionInstallTimeouts {
  /** 托管 Python 下载（fetch）总时长。 */
  downloadMs: number
  /** python -m venv。 */
  venvMs: number
  /** tar -xzf。 */
  extractMs: number
  /** pip install（首次可能要解一个 43 MB wheel + 编译缓存，给足 30 分钟）。 */
  pipMs: number
  /** 解释器/依赖探针（python -c）。 */
  probeMs: number
}

export const DEFAULT_INSTALL_TIMEOUTS: VisionInstallTimeouts = {
  downloadMs: 15 * 60_000,
  venvMs: 300_000,
  extractMs: 300_000,
  pipMs: 1_800_000,
  probeMs: 60_000,
}

export interface VisionInstallerOptions {
  homeDir: string
  values?: () => PythonCandidateValues
  run?: CommandRunner
  env?: NodeJS.ProcessEnv
  platform?: NodeJS.Platform
  arch?: string
  now?: () => number
  download?: (url: string, dest: string) => Promise<{ bytes: number }>
  timeoutMs?: number
  /** 覆盖安装阶段预算（未给的阶段用 DEFAULT_INSTALL_TIMEOUTS）。 */
  timeouts?: Partial<VisionInstallTimeouts>
}

export interface VisionInstaller {
  status(): VisionInstallStatus
  /** Idempotent; a second call while installing shares the same promise. */
  ensureInstalled(): Promise<VisionInstallStatus>
  dispose(): void
}

interface LockFileShape {
  version: number
  format: string
  /** requirements.lock 顶部声明的安装策略；缺省等价于 requireHashes: true。 */
  installer?: { requireHashes?: boolean; onlyBinary?: boolean; noInput?: boolean }
  packages: Array<{
    name: string
    version: string
    requirement: string
    license?: string
    source?: string
    files: Array<{
      filename: string
      sha256: string
      size: number
      kind: string
      pythonTag: string
      abiTag: string
      platforms: string[]
    }>
  }>
}

interface RequirementsTarget {
  platform: NodeJS.Platform
  arch: string
  pythonMinor: number
}

const HERE = path.dirname(fileURLToPath(import.meta.url))

export function requirementsLockPath(): string {
  const candidates = [
    path.join(HERE, REQUIREMENTS_LOCK),
    path.resolve(HERE, '..', '..', '..', 'runtime', 'vision', REQUIREMENTS_LOCK),
  ]
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  return candidates[0] as string
}

export function visionStatePath(homeDir: string): string {
  return path.join(homeDir, 'vision', STATE_FILE)
}

export function installLockPath(homeDir: string): string {
  return path.join(homeDir, 'vision', INSTALL_LOCK_FILE)
}

export function installLogPath(homeDir: string): string {
  return path.join(homeDir, 'logs', INSTALL_LOG)
}

export function managedVenvPath(homeDir: string): string {
  return path.join(homeDir, 'vision', 'venv')
}

export function noticesPath(homeDir: string): string {
  return path.join(homeDir, 'vision', 'THIRD_PARTY_NOTICES.md')
}

export function platformKey(platform: NodeJS.Platform, arch: string): string | null {
  if (platform === 'win32') {
    if (arch === 'x64') return 'win32-x64'
    if (arch === 'ia32') return 'win32-ia32'
    if (arch === 'arm64') return 'win32-arm64'
    return null
  }
  if (platform === 'linux') {
    if (arch === 'x64') return 'linux-x64'
    if (arch === 'arm64') return 'linux-arm64'
    return null
  }
  if (platform === 'darwin') {
    if (arch === 'x64') return 'darwin-x64'
    if (arch === 'arm64') return 'darwin-arm64'
    return null
  }
  return null
}

export function pythonTagFor(minor: number): string {
  return 'cp3' + String(minor)
}

/** Reads requirements.lock; a missing or malformed lock is a packaging error. */
export function readRequirementsLock(file = requirementsLockPath()): LockFileShape {
  let raw: unknown
  try {
    raw = JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch (error) {
    throw new Error(
      'cannot read ' + file + ': ' + (error instanceof Error ? error.message : String(error)),
      {
        cause: error,
      },
    )
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new Error('requirements.lock must be a JSON object')
  }
  const record = raw as Record<string, unknown>
  if (record.format !== 'dsh-vision-lock/1' || !Array.isArray(record.packages)) {
    throw new Error('requirements.lock is not a dsh-vision-lock/1 document')
  }
  return record as unknown as LockFileShape
}

export interface LockSelection {
  requirements: string
  hashPolicy: 'require-hashes' | 'pinned-only'
  hashVerified: boolean
}

/**
 * Selects the hashes for one platform/python and renders a pip requirements
 * file. Hash policy is decided once from `installer.requireHashes` (default
 * true): never mix hashed and unhashed requirements, because pip rejects that.
 */
export function buildRequirementsText(
  lock: LockFileShape,
  target: RequirementsTarget,
): LockSelection {
  const key = platformKey(target.platform, target.arch)
  if (!key) throw new Error('unsupported platform: ' + target.platform + '-' + target.arch)
  const tag = pythonTagFor(target.pythonMinor)
  const requireHashes = lock.installer?.requireHashes !== false
  const lines: string[] = []
  for (const pkg of lock.packages) {
    const files = pkg.files.filter((file) => {
      if (!file.platforms.includes(key)) return false
      if (pkg.name === 'numpy') return file.pythonTag === tag
      // opencv ships cp37-abi3 wheels that work on every supported CPython.
      return file.pythonTag === tag || (file.abiTag === 'abi3' && file.pythonTag.startsWith('cp37'))
    })
    if (files.length === 0)
      throw new Error('requirements.lock has no ' + pkg.name + ' wheel for ' + key)
    if (!requireHashes) {
      // 锁声明不强制 hash：整份文件都不带 hash，保持 pip requirements 语义一致。
      lines.push(pkg.requirement)
      continue
    }
    const hashes: string[] = []
    for (const file of files) {
      const digest = typeof file.sha256 === 'string' ? file.sha256.trim().toLowerCase() : ''
      if (!/^[0-9a-f]{64}$/.test(digest)) {
        // 不静默降级：同一份 requirements 里混 hashed/unhashed 会被 pip 拒绝。
        throw new Error(
          'requirements.lock file ' +
            String(file.filename) +
            ' for ' +
            pkg.name +
            ' has no sha256 while installer.requireHashes=true',
        )
      }
      hashes.push('--hash=sha256:' + digest)
    }
    lines.push(pkg.requirement + ' \n  ' + hashes.join(' \n  '))
  }
  return {
    requirements: lines.join('\n') + '\n',
    hashPolicy: requireHashes ? 'require-hashes' : 'pinned-only',
    hashVerified: requireHashes,
  }
}

export const PBS_PINS: Record<string, { url: string; sha256: string; bytes: number }> = {
  'win32-x64': {
    url:
      'https://github.com/astral-sh/python-build-standalone/releases/download/' +
      PBS_RELEASE +
      '/cpython-' +
      PBS_PYTHON_VERSION +
      '%2B' +
      PBS_RELEASE +
      '-x86_64-pc-windows-msvc-install_only.tar.gz',
    sha256: '7e93813bd362c6413f30788b248590e2f169df2d17dcac81aafbcc7a6eeb542c',
    bytes: 48466149,
  },
  'win32-arm64': {
    url:
      'https://github.com/astral-sh/python-build-standalone/releases/download/' +
      PBS_RELEASE +
      '/cpython-' +
      PBS_PYTHON_VERSION +
      '%2B' +
      PBS_RELEASE +
      '-aarch64-pc-windows-msvc-install_only.tar.gz',
    sha256: 'f050f4df570616e75aac723a5d05319c0d69ea65243506071bcff162774050ca',
    bytes: 45874327,
  },
  'linux-x64': {
    url:
      'https://github.com/astral-sh/python-build-standalone/releases/download/' +
      PBS_RELEASE +
      '/cpython-' +
      PBS_PYTHON_VERSION +
      '%2B' +
      PBS_RELEASE +
      '-x86_64-unknown-linux-gnu-install_only.tar.gz',
    sha256: '50c774f6674740739f791c05a9177ed03e2fe02abebda9bca3512eb7309b37cc',
    bytes: 77155083,
  },
  'linux-arm64': {
    url:
      'https://github.com/astral-sh/python-build-standalone/releases/download/' +
      PBS_RELEASE +
      '/cpython-' +
      PBS_PYTHON_VERSION +
      '%2B' +
      PBS_RELEASE +
      '-aarch64-unknown-linux-gnu-install_only.tar.gz',
    sha256: 'd25a65036d5e149db902fa52ad65878d0c00e9301360ec197832f99063087e9e',
    bytes: 59041869,
  },
}

const VERIFY_SCRIPT = [
  'import json',
  'import cv2',
  'import numpy as np',
  'image = np.zeros((8, 8), dtype="uint8")',
  'image[2:6, 2:6] = 255',
  '_, mask = cv2.threshold(image, 127, 255, cv2.THRESH_BINARY)',
  'contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)',
  'if len(contours) != 1:',
  '    raise SystemExit("contour check failed: " + str(len(contours)))',
  'out = {"cv2": getattr(cv2, "__version__", None), "numpy": getattr(np, "__version__", None), "onnxruntime": None}',
  'try:',
  '    import onnxruntime',
  '    out["onnxruntime"] = getattr(onnxruntime, "__version__", None)',
  'except Exception:',
  '    pass',
  'print(json.dumps(out))',
].join('\n')

function defaultState(homeDir: string): VisionInstallState {
  return {
    version: INSTALL_STATE_VERSION,
    status: 'not_installed',
    source: null,
    python: null,
    pythonVersion: null,
    venv: null,
    backend: 'opencv',
    cv2: null,
    numpy: null,
    onnxruntime: null,
    updatedAt: new Date(0).toISOString(),
    error: null,
    logPath: installLogPath(homeDir),
    hashPolicy: 'require-hashes',
    hashVerified: false,
    attempts: 0,
    licenses: [],
    noticesPath: null,
  }
}

function readState(homeDir: string): VisionInstallState | null {
  try {
    const raw = JSON.parse(fs.readFileSync(visionStatePath(homeDir), 'utf8')) as unknown
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
    const record = raw as Record<string, unknown>
    if (record.version !== INSTALL_STATE_VERSION || typeof record.status !== 'string') return null
    return { ...defaultState(homeDir), ...(record as Partial<VisionInstallState>) }
  } catch {
    return null
  }
}

function errorPayload(error: unknown): InstallError {
  return error instanceof VisionError ? error.toPayload() : VisionError.from(error).toPayload()
}

function appendInstallLog(homeDir: string, line: string): void {
  try {
    fs.mkdirSync(path.dirname(installLogPath(homeDir)), { recursive: true })
    fs.appendFileSync(installLogPath(homeDir), new Date().toISOString() + ' ' + line + '\n', 'utf8')
  } catch {
    /* logging must never mask the install failure */
  }
}

function hashFile(file: string): string {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
}

function defaultDownload(url: string, dest: string): Promise<{ bytes: number }> {
  return (async () => {
    const response = await fetch(url)
    if (!response.ok || !response.body)
      throw new Error('HTTP ' + String(response.status) + ' for ' + url)
    fs.mkdirSync(path.dirname(dest), { recursive: true })
    const body = Readable.fromWeb(response.body as import('node:stream/web').ReadableStream)
    await pipeline(body, fs.createWriteStream(dest))
    return { bytes: fs.statSync(dest).size }
  })()
}

/** THIRD_PARTY_NOTICES.md is written once per successful managed install. */
export function noticesText(): string {
  return [
    '# Third-party notices',
    '',
    'The managed vision environment installs these packages into <home>/vision/venv:',
    '',
    '| Package | Version | License | Source |',
    '| --- | --- | --- | --- |',
    '| Python | ' +
      PBS_PYTHON_VERSION +
      ' (python-build-standalone) | PSF-2.0 | https://github.com/astral-sh/python-build-standalone |',
    '| opencv-python-headless | 5.0.0.93 | Apache-2.0 | https://pypi.org/project/opencv-python-headless/5.0.0.93/ |',
    '| numpy | 2.4.6 | BSD-3-Clause | https://pypi.org/project/numpy/2.4.6/ |',
    '',
    'Custom vision.venv environments are user-managed: the plugin does not install, upgrade or',
    'modify packages there and does not write this file for them.',
    '',
  ].join('\n')
}

/** createVisionInstaller 内部的依赖与可变状态，供文件级 helper 通过参数访问。 */
interface VisionInstallerContext {
  homeDir: string
  platform: NodeJS.Platform
  arch: string
  env: NodeJS.ProcessEnv
  now: () => number
  run: CommandRunner
  download: (url: string, dest: string) => Promise<{ bytes: number }>
  values: () => PythonCandidateValues
  timeoutMs?: number
  timeouts: VisionInstallTimeouts
  inFlight: Promise<VisionInstallStatus> | null
  current: VisionInstallState
}

type ManagedTmpVenv =
  | { kind: 'created'; tmp: string; tmpPython: string }
  | { kind: 'failed'; status: VisionInstallStatus }

function save(
  ctx: VisionInstallerContext,
  patch: Partial<VisionInstallState>,
): VisionInstallStatus {
  ctx.current = {
    ...ctx.current,
    ...patch,
    version: INSTALL_STATE_VERSION,
    updatedAt: new Date(ctx.now()).toISOString(),
  }
  if (ctx.current.status === 'ready') ctx.current.error = null
  try {
    fs.mkdirSync(path.dirname(visionStatePath(ctx.homeDir)), { recursive: true })
    writeFileAtomic(visionStatePath(ctx.homeDir), JSON.stringify(ctx.current, null, 2) + '\n')
  } catch {
    /* state file is a cross-process view; write failures keep memory truth */
  }
  return status(ctx)
}

function status(ctx: VisionInstallerContext): VisionInstallStatus {
  const source: 'managed' | 'custom' =
    ctx.current.source ?? (ctx.values().venv.trim() ? 'custom' : 'managed')
  const envInfo: VisionEnvInfo = {
    source,
    venv:
      ctx.current.venv ??
      (source === 'custom' ? ctx.values().venv.trim() : managedVenvPath(ctx.homeDir)),
    python: ctx.current.python,
    pythonVersion: ctx.current.pythonVersion,
    cv2: ctx.current.cv2,
    numpy: ctx.current.numpy,
    onnxruntime: ctx.current.onnxruntime,
    backend: ctx.current.backend,
  }
  return { ...ctx.current, env: envInfo }
}

function fail(ctx: VisionInstallerContext, error: InstallError): VisionInstallStatus {
  return save(ctx, { status: 'failed', error })
}

function acquireLock(ctx: VisionInstallerContext): boolean {
  try {
    fs.mkdirSync(path.dirname(installLockPath(ctx.homeDir)), { recursive: true })
    fs.writeFileSync(
      installLockPath(ctx.homeDir),
      JSON.stringify({ pid: process.pid, startedAt: ctx.now() }),
      {
        encoding: 'utf8',
        flag: 'wx',
      },
    )
    return true
  } catch (error) {
    const code = error instanceof Error ? (error as NodeJS.ErrnoException).code : undefined
    if (code !== 'EEXIST') return false
    let startedAt: number
    try {
      const raw = JSON.parse(fs.readFileSync(installLockPath(ctx.homeDir), 'utf8')) as Record<
        string,
        unknown
      >
      startedAt = typeof raw.startedAt === 'number' ? raw.startedAt : 0
    } catch {
      startedAt = 0
    }
    if (startedAt > 0 && ctx.now() - startedAt < INSTALL_LOCK_STALE_MS) return false
    try {
      fs.rmSync(installLockPath(ctx.homeDir), { force: true })
      fs.writeFileSync(
        installLockPath(ctx.homeDir),
        JSON.stringify({ pid: process.pid, startedAt: ctx.now() }),
        {
          encoding: 'utf8',
          flag: 'wx',
        },
      )
      return true
    } catch {
      return false
    }
  }
}

function releaseLock(ctx: VisionInstallerContext): void {
  try {
    fs.rmSync(installLockPath(ctx.homeDir), { force: true })
  } catch {
    /* another process may have cleaned a stale lock */
  }
}

async function runCommand(
  ctx: VisionInstallerContext,
  command: string,
  args: string[],
  timeoutMs?: number,
  extraEnv?: NodeJS.ProcessEnv,
) {
  return ctx.run(command, args, {
    env: { ...ctx.env, ...extraEnv },
    cwd: ctx.homeDir,
    timeoutMs: timeoutMs ?? ctx.timeoutMs,
  })
}

/** 安装子进程只拿得到运行必需的变量：不把宿主 token/API key 泄给 pip/tar。 */
const INSTALL_ENV_ALLOW: RegExp[] = [
  /^PATH$/i,
  /^HOME$/i,
  /^USERPROFILE$/i,
  /^SYSTEMROOT$/i,
  /^SYSTEMDRIVE$/i,
  /^WINDIR$/i,
  /^TEMP$/i,
  /^TMP$/i,
  /^TMPDIR$/i,
  /^PYTHON[A-Z0-9_]*$/i,
  /^PIP_[A-Z0-9_]*$/i,
  /^HTTP_PROXY$/i,
  /^HTTPS_PROXY$/i,
  /^NO_PROXY$/i,
  /^ALL_PROXY$/i,
  /^REQUESTS_CA_BUNDLE$/i,
  /^SSL_CERT_FILE$/i,
  /^SSL_CERT_DIR$/i,
  /^LANG$/i,
  /^LC_[A-Z]*$/i,
  /^USERNAME$/i,
  /^APPDATA$/i,
  /^LOCALAPPDATA$/i,
  /^COMSPEC$/i,
  /^PATHEXT$/i,
  /^NUMBER_OF_PROCESSORS$/i,
  /^PROCESSOR_ARCHITECTURE$/i,
  /^PROGRAMDATA$/i,
  /^PROGRAMFILES$/i,
  /^PROGRAMFILES\(X86\)$/i,
  /^PROGRAMW6432$/i,
]

export function installEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = {}
  for (const [key, value] of Object.entries(env)) {
    if (value === undefined) continue
    if (INSTALL_ENV_ALLOW.some((pattern) => pattern.test(key))) out[key] = value
  }
  return out
}

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(
        new VisionError({
          code: 'python_missing',
          stage: 'config',
          message: message + '（' + String(ms) + ' ms）',
          retryable: true,
          details: { reason: 'timeout', timeoutMs: ms },
        }),
      )
    }, ms)
    timer.unref?.()
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

async function probeVenvModules(
  ctx: VisionInstallerContext,
  python: string,
): Promise<{ cv2: string; numpy: string; onnxruntime: string | null } | null> {
  const result = await runCommand(ctx, python, ['-c', VERIFY_SCRIPT], ctx.timeouts.probeMs)
  if (result.code !== 0) return null
  try {
    const raw = JSON.parse(result.stdout.trim()) as Record<string, unknown>
    if (typeof raw.cv2 !== 'string' || typeof raw.numpy !== 'string') return null
    return {
      cv2: raw.cv2,
      numpy: raw.numpy,
      onnxruntime: typeof raw.onnxruntime === 'string' ? raw.onnxruntime : null,
    }
  } catch {
    return null
  }
}

function finishReady(
  ctx: VisionInstallerContext,
  patch: Partial<VisionInstallState>,
): VisionInstallStatus {
  try {
    fs.mkdirSync(path.dirname(noticesPath(ctx.homeDir)), { recursive: true })
    writeFileAtomic(noticesPath(ctx.homeDir), noticesText())
  } catch {
    /* notices are best effort; state still reports the path */
  }
  return save(ctx, {
    status: 'ready',
    error: null,
    noticesPath: noticesPath(ctx.homeDir),
    licenses: [
      {
        name: 'opencv-python-headless',
        version: '5.0.0.93',
        license: 'Apache-2.0',
        path: noticesPath(ctx.homeDir),
      },
      { name: 'numpy', version: '2.4.6', license: 'BSD-3-Clause', path: noticesPath(ctx.homeDir) },
      {
        name: 'Python',
        version: PBS_PYTHON_VERSION,
        license: 'PSF-2.0',
        path: noticesPath(ctx.homeDir),
      },
    ],
    ...patch,
  })
}

async function downloadManagedPython(
  ctx: VisionInstallerContext,
): Promise<{ interpreter: string; minor: number }> {
  const key = platformKey(ctx.platform, ctx.arch)
  const pin = key ? PBS_PINS[key] : undefined
  if (!pin) {
    throw new VisionError({
      code: 'python_missing',
      stage: 'config',
      message:
        '没有可用的系统 Python，且当前平台没有托管解释器下载项：' + ctx.platform + '-' + ctx.arch,
      fix: '安装 CPython 3.11-3.13 并设置 vision.python，或把 vision.venv 指向已有环境。',
      retryable: false,
      details: { platform: ctx.platform, arch: ctx.arch, supported: Object.keys(PBS_PINS) },
    })
  }
  save(ctx, { status: 'downloading_python', error: null })
  const downloads = path.join(ctx.homeDir, 'vision', 'cache', 'downloads')
  fs.mkdirSync(downloads, { recursive: true })
  const archive = path.join(downloads, 'cpython-' + PBS_PYTHON_VERSION + '.tar.gz')
  const part = archive + '.part'
  try {
    await withTimeout(ctx.download(pin.url, part), ctx.timeouts.downloadMs, '托管 Python 下载超时')
    const bytes = fs.statSync(part).size
    if (bytes !== pin.bytes) {
      throw new VisionError({
        code: 'python_missing',
        stage: 'config',
        message: '托管 Python 下载字节数不匹配：' + String(bytes) + ' != ' + String(pin.bytes),
        retryable: true,
        details: { reason: 'size_mismatch', url: pin.url, expected: pin.bytes, actual: bytes },
      })
    }
    const actual = hashFile(part)
    if (actual !== pin.sha256) {
      throw new VisionError({
        code: 'python_missing',
        stage: 'config',
        message: '托管 Python 校验和不匹配（拒绝解压与执行）',
        retryable: true,
        details: { reason: 'checksum_mismatch', url: pin.url, expected: pin.sha256, actual },
      })
    }
    fs.renameSync(part, archive)
  } catch (error) {
    fs.rmSync(part, { force: true })
    throw error
  }
  const extractRoot = path.join(ctx.homeDir, 'vision', 'python')
  fs.rmSync(extractRoot, { recursive: true, force: true })
  fs.mkdirSync(extractRoot, { recursive: true })
  const tar = await runCommand(
    ctx,
    'tar',
    ['-xzf', archive, '-C', extractRoot],
    ctx.timeouts.extractMs,
  )
  if (tar.code !== 0) {
    throw new VisionError({
      code: 'python_missing',
      stage: 'config',
      message: '解压托管 Python 失败',
      fix: '确认系统提供 tar，或安装 Python 3.11-3.13 后设置 vision.python。',
      retryable: false,
      details: { reason: 'extract_failed', exitCode: tar.code, stderr: tar.stderr.slice(-2000) },
    })
  }
  const candidates =
    ctx.platform === 'win32'
      ? [path.join(extractRoot, 'python', 'python.exe')]
      : [
          path.join(extractRoot, 'python', 'bin', 'python3'),
          path.join(extractRoot, 'python', 'bin', 'python'),
        ]
  const interpreter = candidates.find((candidate) => fs.existsSync(candidate))
  if (!interpreter) {
    throw new VisionError({
      code: 'python_missing',
      stage: 'config',
      message: '托管 Python 解压后找不到解释器',
      retryable: false,
      details: { reason: 'interpreter_missing', candidates },
    })
  }
  const probe = await probePython(ctx.run, interpreter, [], {
    env: ctx.env,
    timeoutMs: ctx.timeouts.probeMs,
  })
  if (!probe || !probe.hasVenv) {
    throw new VisionError({
      code: 'python_missing',
      stage: 'config',
      message: '托管 Python 无法 import venv',
      retryable: false,
      details: { reason: 'verify_failed', interpreter },
    })
  }
  return { interpreter, minor: 13 }
}

/** 断点恢复：已有托管 venv 且 cv2/numpy 可用时只做验证，不重复 pip。 */
async function resumeManagedVenv(
  ctx: VisionInstallerContext,
  venv: string,
  python: string,
): Promise<VisionInstallStatus | null> {
  if (!fs.existsSync(python)) return null
  save(ctx, { status: 'verifying', source: 'managed', venv, python, error: null })
  const modules = await probeVenvModules(ctx, python)
  if (!modules) return null
  return finishReady(ctx, {
    source: 'managed',
    status: 'ready',
    venv,
    python,
    pythonVersion: null,
    cv2: modules.cv2,
    numpy: modules.numpy,
    onnxruntime: modules.onnxruntime,
    backend: ctx.values().backend,
    attempts: 0,
  })
}

async function resolveManagedBase(
  ctx: VisionInstallerContext,
): Promise<{ interpreter: string; minor: number }> {
  save(ctx, { status: 'resolving_python', source: 'managed', error: null })
  try {
    const detected = await discoverPython({
      run: ctx.run,
      values: ctx.values(),
      env: ctx.env,
      platform: ctx.platform,
      timeoutMs: ctx.timeoutMs,
    })
    save(ctx, { backend: ctx.values().backend })
    return { interpreter: detected.interpreter, minor: detected.version.minor }
  } catch (error) {
    const missing = error instanceof VisionError ? error : VisionError.from(error)
    if (missing.code !== 'python_missing') throw error
    const downloaded = await downloadManagedPython(ctx)
    return { interpreter: downloaded.interpreter, minor: downloaded.minor }
  }
}

async function createManagedTmpVenv(
  ctx: VisionInstallerContext,
  base: string,
): Promise<ManagedTmpVenv> {
  const venv = managedVenvPath(ctx.homeDir)
  const tmp = venv + '.tmp-' + String(process.pid)
  fs.rmSync(tmp, { recursive: true, force: true })
  save(ctx, { status: 'creating_venv', source: 'managed', python: base, venv: tmp, error: null })
  const created = await runCommand(ctx, base, ['-m', 'venv', tmp], ctx.timeouts.venvMs)
  if (created.code !== 0) {
    appendInstallLog(
      ctx.homeDir,
      'stage=creating_venv exit=' + String(created.code) + ' stderr=' + created.stderr.slice(-2000),
    )
    return {
      kind: 'failed',
      status: fail(ctx, {
        code: 'vision_install_failed',
        stage: 'config',
        message: '创建托管 venv 失败',
        fix: '确认解释器可执行；详情见安装日志。',
        retryable: true,
        details: {
          stage: 'creating_venv',
          exitCode: created.code,
          logPath: installLogPath(ctx.homeDir),
        },
      }),
    }
  }
  return { kind: 'created', tmp, tmpPython: venvPythonPath(tmp, ctx.platform) }
}

async function runPipInstall(
  ctx: VisionInstallerContext,
  tmp: string,
  tmpPython: string,
  selection: LockSelection,
): Promise<{ attempts: number; installed: boolean }> {
  const cache = path.join(ctx.homeDir, 'vision', 'cache')
  fs.mkdirSync(cache, { recursive: true })
  const requirements = path.join(cache, 'requirements.txt')
  fs.writeFileSync(requirements, selection.requirements, 'utf8')
  const pipArgs = [
    '-m',
    'pip',
    'install',
    '--disable-pip-version-check',
    '--no-input',
    '--only-binary=:all:',
    ...(selection.hashPolicy === 'require-hashes' ? ['--require-hashes'] : []),
    '-r',
    requirements,
  ]
  save(ctx, {
    status: 'installing',
    venv: tmp,
    backend: ctx.values().backend,
    hashPolicy: selection.hashPolicy,
    hashVerified: false,
    error: null,
  })
  let attempts = 0
  let installed = false
  while (attempts < PIP_MAX_ATTEMPTS) {
    attempts += 1
    const result = await runCommand(ctx, tmpPython, pipArgs, ctx.timeouts.pipMs, {
      PIP_CACHE_DIR: path.join(ctx.homeDir, 'vision', 'cache', 'pip'),
      PIP_DISABLE_PIP_VERSION_CHECK: '1',
    })
    if (result.code === 0) {
      installed = true
      break
    }
    appendInstallLog(
      ctx.homeDir,
      'stage=installing attempt=' +
        String(attempts) +
        ' exit=' +
        String(result.code) +
        ' stderr=' +
        result.stderr.slice(-4000),
    )
    if (attempts < PIP_MAX_ATTEMPTS) save(ctx, { status: 'installing', attempts, error: null })
  }
  return { attempts, installed }
}

/**
 * venv 收尾必须是原子的：旧目录先改名让开，再换入新目录；换入失败则把旧目录换回去。
 * 直接 rm 旧目录再 rename 会在中间窗口里既没有旧环境也没有新环境，失败还会留下 venv.tmp-*。
 */
function swapInVenv(venv: string, tmp: string): void {
  const old = venv + '.old-' + String(process.pid)
  fs.rmSync(old, { recursive: true, force: true })
  const hadOld = fs.existsSync(venv)
  if (hadOld) fs.renameSync(venv, old)
  try {
    fs.renameSync(tmp, venv)
  } catch (error) {
    if (hadOld) {
      try {
        fs.renameSync(old, venv)
      } catch {
        /* 恢复失败时至少保留 old 目录，下次 recover 会再试 */
      }
    }
    throw error
  }
  if (hadOld) fs.rmSync(old, { recursive: true, force: true })
}

/** 清理上次崩溃留下的临时/备份 venv；venv 缺失但存在 .old-* 时先恢复。 */
function recoverManagedVenv(homeDir: string): void {
  const venv = managedVenvPath(homeDir)
  const parent = path.dirname(venv)
  let entries: string[]
  try {
    entries = fs.readdirSync(parent)
  } catch {
    return
  }
  for (const entry of entries) {
    if (!entry.startsWith('venv.tmp-') && !entry.startsWith('venv.old-')) continue
    const full = path.join(parent, entry)
    if (entry.startsWith('venv.old-') && !fs.existsSync(venv)) {
      try {
        fs.renameSync(full, venv)
        continue
      } catch {
        /* 恢复不了就按残留清理 */
      }
    }
    try {
      fs.rmSync(full, { recursive: true, force: true })
    } catch {
      /* best effort */
    }
  }
}

async function finalizeManagedInstall(
  ctx: VisionInstallerContext,
  venv: string,
  tmp: string,
  attempts: number,
  selection: LockSelection,
): Promise<VisionInstallStatus> {
  swapInVenv(venv, tmp)
  const finalPython = venvPythonPath(venv, ctx.platform)
  save(ctx, {
    status: 'verifying',
    venv,
    python: finalPython,
    attempts,
    hashVerified: selection.hashVerified,
  })
  const modules = await probeVenvModules(ctx, finalPython)
  if (!modules) {
    appendInstallLog(
      ctx.homeDir,
      'stage=verifying result=contour-check-failed python=' + finalPython,
    )
    return fail(ctx, {
      code: 'vision_install_failed',
      stage: 'config',
      message: '托管环境已安装但导入/小轮廓校验失败',
      fix: '删除 <home>/vision/venv 后重试；若反复失败请附带安装日志反馈。',
      retryable: true,
      details: {
        stage: 'verifying',
        logPath: installLogPath(ctx.homeDir),
        hashPolicy: selection.hashPolicy,
      },
    })
  }
  return finishReady(ctx, {
    source: 'managed',
    status: 'ready',
    venv,
    python: finalPython,
    pythonVersion: null,
    cv2: modules.cv2,
    numpy: modules.numpy,
    onnxruntime: modules.onnxruntime,
    backend: ctx.values().backend,
    attempts,
    hashPolicy: selection.hashPolicy,
    hashVerified: selection.hashVerified,
  })
}

async function managedInstall(ctx: VisionInstallerContext): Promise<VisionInstallStatus> {
  recoverManagedVenv(ctx.homeDir)
  const venv = managedVenvPath(ctx.homeDir)
  const python = venvPythonPath(venv, ctx.platform)
  const resumed = await resumeManagedVenv(ctx, venv, python)
  if (resumed) return resumed

  const base = await resolveManagedBase(ctx)

  const created = await createManagedTmpVenv(ctx, base.interpreter)
  if (created.kind === 'failed') return created.status
  const tmp = created.tmp
  const tmpPython = created.tmpPython

  try {
    const lock = readRequirementsLock()
    let selection: LockSelection
    try {
      selection = buildRequirementsText(lock, {
        platform: ctx.platform,
        arch: ctx.arch,
        pythonMinor: base.minor,
      })
    } catch (error) {
      return fail(ctx, {
        code: 'vision_install_failed',
        stage: 'config',
        message:
          'requirements.lock 与当前平台不匹配：' +
          (error instanceof Error ? error.message : String(error)),
        retryable: false,
        details: {
          stage: 'installing',
          platform: ctx.platform,
          arch: ctx.arch,
          lock: requirementsLockPath(),
        },
      })
    }

    const { attempts, installed } = await runPipInstall(ctx, tmp, tmpPython, selection)
    if (!installed) {
      return fail(ctx, {
        code: 'vision_install_failed',
        stage: 'config',
        message: '安装 OpenCV/NumPy 失败（已重试 ' + String(PIP_MAX_ATTEMPTS) + ' 次）',
        fix:
          '检查网络或代理（PIP_CACHE_DIR=' +
          path.join(ctx.homeDir, 'vision', 'cache', 'pip') +
          '）；离线时可在有网机器上下载 wheel 后重试。插件不会回退到系统 pip。',
        retryable: true,
        details: {
          stage: 'installing',
          attempts,
          logPath: installLogPath(ctx.homeDir),
          requirements: requirementsLockPath(),
          hashPolicy: selection.hashPolicy,
        },
      })
    }

    return await finalizeManagedInstall(ctx, venv, tmp, attempts, selection)
  } finally {
    // 成功时 tmp 已被 rename 成 venv；失败时清掉残留 venv.tmp-<pid>。
    try {
      fs.rmSync(tmp, { recursive: true, force: true })
    } catch {
      /* best effort */
    }
  }
}

async function runInstall(ctx: VisionInstallerContext): Promise<VisionInstallStatus> {
  const candidate = ctx.values()
  ctx.current = { ...ctx.current, backend: candidate.backend }
  if (!acquireLock(ctx)) return status(ctx)
  try {
    const venv = candidate.venv.trim()
    if (venv) {
      save(ctx, { status: 'resolving_python', source: 'custom', venv, error: null })
      try {
        const detected = await discoverPython({
          run: ctx.run,
          values: candidate,
          env: ctx.env,
          platform: ctx.platform,
          timeoutMs: ctx.timeoutMs,
        })
        return save(ctx, {
          status: 'ready',
          source: 'custom',
          venv,
          python: detected.interpreter,
          pythonVersion: detected.versionText,
          cv2: detected.probe.cv2,
          numpy: detected.probe.numpy,
          onnxruntime: detected.probe.onnxruntime,
          backend: candidate.backend,
          error: null,
          attempts: 0,
          hashPolicy: 'require-hashes',
          hashVerified: false,
          licenses: [],
          noticesPath: null,
        })
      } catch (error) {
        const payload = errorPayload(error)
        appendInstallLog(ctx.homeDir, 'stage=resolving_python source=custom code=' + payload.code)
        return save(ctx, { status: 'failed', source: 'custom', venv, error: payload })
      }
    }
    return await managedInstall(ctx)
  } catch (error) {
    const payload = errorPayload(error)
    appendInstallLog(
      ctx.homeDir,
      'stage=install code=' + payload.code + ' message=' + payload.message,
    )
    return fail(ctx, payload)
  } finally {
    releaseLock(ctx)
  }
}

export function createVisionInstaller(options: VisionInstallerOptions): VisionInstaller {
  const ctx: VisionInstallerContext = {
    homeDir: options.homeDir,
    platform: options.platform ?? process.platform,
    arch: options.arch ?? process.arch,
    env: installEnv(options.env ?? process.env),
    now: options.now ?? Date.now,
    run: options.run ?? createProcessRunner(),
    download: options.download ?? defaultDownload,
    values: () => options.values?.() ?? { python: '', venv: '', backend: 'opencv' },
    timeoutMs: options.timeoutMs,
    timeouts: { ...DEFAULT_INSTALL_TIMEOUTS, ...options.timeouts },
    inFlight: null,
    current: readState(options.homeDir) ?? defaultState(options.homeDir),
  }
  return {
    status: () => status(ctx),
    ensureInstalled(): Promise<VisionInstallStatus> {
      if (ctx.inFlight) return ctx.inFlight
      ctx.inFlight = runInstall(ctx).finally(() => {
        ctx.inFlight = null
      })
      return ctx.inFlight
    },
    dispose(): void {
      ctx.inFlight = null
    },
  }
}
