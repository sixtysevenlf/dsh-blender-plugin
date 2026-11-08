// Linux L1 display provider (preview-only in 3.1, task 5.7).
//
// One Xvfb display per virtual-gui instance. When VirtualGL and a readable
// /dev/dri render node are present the Blender process runs under
// `vglrun -d egl` for hardware GL; otherwise it runs directly on Xvfb and the
// provider reports preview-only software GL. It never silently claims hardware:
// capabilities() keeps the hardware prerequisites in view, and start records the
// chosen GL transport in the handle evidence. X11 is forced explicitly so the
// provider cannot inherit WSLg/Wayland and hit the Blender Wayland exit crash.
import { spawn } from 'node:child_process'
import type { ChildProcess, SpawnOptions } from 'node:child_process'
import fs from 'node:fs'
import { CFG } from '../config.js'
import { DisplayProviderError } from './errors.js'
import { resolveVirtualGuiFeature } from './feature.js'
import { buildDisplayCapabilities, canReadLinuxGpu, onPath } from './prerequisites.js'
import type {
  DisplayCapabilities,
  DisplayHealth,
  DisplayInstance,
  DisplayProvider,
  DisplayStartRequest,
} from './types.js'

const READY_TIMEOUT_MS = 15_000
const READY_GRACE_MS = 750
const STOP_TIMEOUT_MS = 10_000
const LOG_LINES = 80
const XVFB_FIX =
  'Ubuntu/Debian: sudo apt install xvfb; Fedora: sudo dnf install xorg-x11-server-Xvfb'
const BLENDER_FIX = 'set DSH_BLENDER_EXE or install Blender 4.2.10 LTS'

export type SpawnDisplayChild = (
  command: string,
  args: readonly string[],
  options: SpawnOptions,
) => ChildProcess

interface SpawnSpec {
  command: string
  args: string[]
  options: SpawnOptions
}

interface CommandSpec {
  display: string
  xvfb: SpawnSpec
  blender: SpawnSpec
  hardwareGl: boolean
  glTransport: string
}

interface ResolvedPrograms {
  xvfb: string
  blender: string
  vglrun: string | null
  hardwareGl: boolean
}

export interface LinuxXvfbVirtualGlProviderOptions {
  env?: NodeJS.ProcessEnv
  /** Test seam; production uses process.platform. */
  platform?: string
  blenderExe?: string
  xvfbPath?: string
  /** Explicit vglrun path; null disables the hardware transport. */
  vglrunPath?: string | null
  spawnImpl?: SpawnDisplayChild
  /** Test seam for the X socket / prerequisite probes. */
  fileExists?: (file: string) => boolean
  displayReady?: (display: string, xvfb: ChildProcess) => Promise<boolean>
  gpuReadable?: () => boolean
  signalGroup?: (child: ChildProcess, signal: NodeJS.Signals) => void
  checks?: Record<string, boolean>
  /** Test seam for the feature gate; production reads CFG. */
  feature?: string
  featureSource?: string
  displayBase?: number
  width?: number
  height?: number
  workDir?: string
  readyTimeoutMs?: number
  readyGraceMs?: number
  stopTimeoutMs?: number
  onOutput?: (line: string, stream: 'stdout' | 'stderr') => void
  onExit?: (handle: DisplayInstance, code: number | null) => void
}

interface ProviderRecord {
  handle: DisplayInstance
  xvfb: ChildProcess
  blender: ChildProcess
  number: number
  hardwareGl: boolean
  glTransport: string
  stdout: string[]
  stderr: string[]
  exitCode: number | null
  startedAt: number
}

function pushTail(lines: string[], line: string): void {
  lines.push(line)
  if (lines.length > LOG_LINES) lines.shift()
}

function attachLines(
  child: ChildProcess,
  onLine: (line: string, stream: 'stdout' | 'stderr') => void,
): void {
  const bind = (stream: NodeJS.ReadableStream | null, name: 'stdout' | 'stderr') => {
    if (!stream) return
    let buffer = ''
    stream.on('data', (chunk: Buffer | string) => {
      buffer += chunk.toString()
      let index = buffer.indexOf('\n')
      while (index >= 0) {
        onLine(buffer.slice(0, index), name)
        buffer = buffer.slice(index + 1)
        index = buffer.indexOf('\n')
      }
    })
  }
  bind(child.stdout, 'stdout')
  bind(child.stderr, 'stderr')
}

function displayPath(number: number): string {
  return '/tmp/.X11-unix/X' + String(number)
}

function lockPath(number: number): string {
  return '/tmp/.X' + String(number) + '-lock'
}

function runnable(file: string, exists: (f: string) => boolean): boolean {
  try {
    return exists(file)
  } catch (e) {
    return false
  }
}

function signalTree(
  child: ChildProcess,
  signal: NodeJS.Signals,
  custom?: (child: ChildProcess, signal: NodeJS.Signals) => void,
): void {
  const pid = child.pid
  if (custom) {
    try {
      custom(child, signal)
    } catch (e) {
      /* already gone */
    }
    return
  }
  try {
    if (pid) process.kill(-pid, signal)
    else child.kill(signal)
  } catch (e) {
    try {
      child.kill(signal)
    } catch (ignored) {
      /* already gone */
    }
  }
}

function waitExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null) return Promise.resolve(true)
  return new Promise((resolve) => {
    let timer: NodeJS.Timeout | null = null
    const done = (value: boolean) => {
      if (timer) clearTimeout(timer)
      child.removeListener('exit', onExit)
      resolve(value)
    }
    const onExit = () => done(true)
    child.once('exit', onExit)
    timer = setTimeout(() => done(child.exitCode !== null), timeoutMs)
    if (typeof timer.unref === 'function') timer.unref()
  })
}

/** One Xvfb + Blender GUI pair; the Linux L1 implementation of DisplayProvider. */
export class LinuxXvfbVirtualGlProvider implements DisplayProvider {
  readonly kind = 'linux-xvfb-virtualgl'
  private readonly options: LinuxXvfbVirtualGlProviderOptions
  private readonly records = new Map<string, ProviderRecord>()
  private readonly usedDisplays = new Set<number>()
  private counter = 0

  constructor(options: LinuxXvfbVirtualGlProviderOptions = {}) {
    this.options = options
  }

  private env(): NodeJS.ProcessEnv {
    return { ...process.env, ...(this.options.env || {}) }
  }

  private exists(file: string): boolean {
    return (this.options.fileExists || fs.existsSync)(file)
  }

  capabilities(): DisplayCapabilities {
    return buildDisplayCapabilities({
      platform: 'linux',
      configured:
        this.options.feature === undefined ? CFG.features.virtualGui : this.options.feature,
      source:
        this.options.featureSource === undefined ? CFG.features.source : this.options.featureSource,
      env: this.options.env,
      checks: this.options.checks,
      blenderExe: this.options.blenderExe,
      providerImplemented: true,
    })
  }

  /** Resolve commands and the GL transport before spawning anything. */
  private commandSpec(request: DisplayStartRequest): CommandSpec {
    this.assertPlatform()
    this.assertFeatureEnabled()
    const env = this.env()
    const programs = this.resolvePrograms(env)
    const number = this.pickDisplay()
    const display = ':' + String(number)
    const spawnEnv = this.buildSpawnEnv(env, display, request.instanceId)
    const options = this.buildSpawnOptions(spawnEnv)
    const width = this.options.width || 1280
    const height = this.options.height || 720
    const extra = Array.isArray(request.blenderArgs) ? request.blenderArgs.map(String) : []
    const hardwareGl = programs.hardwareGl && programs.vglrun !== null
    const blenderArgs =
      hardwareGl && programs.vglrun ? ['-d', 'egl', programs.blender, ...extra] : extra
    return {
      display,
      xvfb: {
        command: programs.xvfb,
        args: [display, '-screen', '0', width + 'x' + height + 'x24', '-nolisten', 'tcp', '-ac'],
        options,
      },
      blender: {
        command: hardwareGl && programs.vglrun ? programs.vglrun : programs.blender,
        args: blenderArgs,
        options,
      },
      hardwareGl,
      glTransport: hardwareGl ? 'vglrun -d egl' : 'xvfb software GL (preview-only)',
    }
  }

  private assertPlatform(): void {
    const platform = this.options.platform || process.platform
    if (!this.options.spawnImpl && platform !== 'linux') {
      throw new DisplayProviderError(
        'config',
        'unsupported_platform',
        'the Linux Xvfb provider can only run on Linux (platform=' + platform + ')',
        'virtual-gui on Windows uses the hidden-desktop provider; on macOS use user-gui or headless render',
      )
    }
  }

  private assertFeatureEnabled(): void {
    const feature = resolveVirtualGuiFeature({
      platform: 'linux',
      configured:
        this.options.feature === undefined ? CFG.features.virtualGui : this.options.feature,
      source:
        this.options.featureSource === undefined ? CFG.features.source : this.options.featureSource,
      v0Released: true,
    })
    if (feature.value !== 'on') {
      throw new DisplayProviderError(
        'config',
        'feature_disabled',
        'Linux virtual-gui is preview-only and disabled (features.virtualGui=' +
          feature.value +
          ')',
        'set features.virtualGui=on in dsh-blender.config.json (or DSH_BLENDER_VIRTUAL_GUI=on) to opt into the preview',
        { feature: feature.value, feature_source: feature.source },
      )
    }
  }

  private resolvePrograms(env: NodeJS.ProcessEnv): ResolvedPrograms {
    const xvfb = this.options.xvfbPath === undefined ? onPath('Xvfb', env) : this.options.xvfbPath
    if (!xvfb) {
      throw new DisplayProviderError('config', 'xvfb_missing', 'Xvfb is not on PATH', XVFB_FIX, {
        path: String(env.PATH || ''),
      })
    }
    const blender = String(this.options.blenderExe || CFG.blenderExe || '')
    if (!blender || !runnable(blender, (f) => this.exists(f))) {
      throw new DisplayProviderError(
        'config',
        'blender_exe_missing',
        blender
          ? 'blender executable does not exist: ' + blender
          : 'blender executable not configured',
        BLENDER_FIX,
        { blenderExe: blender || null, source: CFG.source.blenderExe },
      )
    }
    const vglrun =
      this.options.vglrunPath === undefined ? onPath('vglrun', env) : this.options.vglrunPath
    const gpuOk = (this.options.gpuReadable || (() => canReadLinuxGpu().ok))()
    return { xvfb, blender, vglrun: vglrun || null, hardwareGl: Boolean(vglrun) && gpuOk }
  }

  private buildSpawnEnv(
    env: NodeJS.ProcessEnv,
    display: string,
    instanceId?: string,
  ): NodeJS.ProcessEnv {
    const spawnEnv: NodeJS.ProcessEnv = {
      ...env,
      DISPLAY: display,
      XDG_SESSION_TYPE: 'x11',
      GDK_BACKEND: 'x11',
      DSHBB_GATEWAY_PORT: String(CFG.gatewayPort),
      DSHBB_SESSION_KIND: 'virtual-gui',
    }
    // WSLg inherits Wayland; Blender must never pick it (5.19 exit-139 crash).
    delete spawnEnv.WAYLAND_DISPLAY
    if (instanceId) spawnEnv.DSHBB_INSTANCE_ID = String(instanceId)
    return spawnEnv
  }

  private buildSpawnOptions(spawnEnv: NodeJS.ProcessEnv): SpawnOptions {
    return {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: spawnEnv,
      detached: true,
      ...(this.options.workDir ? { cwd: this.options.workDir } : {}),
    }
  }

  private pickDisplay(): number {
    let number = this.options.displayBase || 90
    while (
      this.usedDisplays.has(number) ||
      this.exists(lockPath(number)) ||
      this.exists(displayPath(number))
    )
      number += 1
    this.usedDisplays.add(number)
    return number
  }

  private displayReady(display: string, xvfb: ChildProcess): Promise<boolean> {
    if (this.options.displayReady) return this.options.displayReady(display, xvfb)
    const number = Number(display.slice(1))
    const socket = displayPath(number)
    const timeoutMs = this.options.readyTimeoutMs || READY_TIMEOUT_MS
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeoutMs
      const timer = setInterval(() => {
        if (this.exists(socket)) {
          clearInterval(timer)
          resolve(true)
          return
        }
        if (xvfb.exitCode !== null) {
          clearInterval(timer)
          reject(
            new DisplayProviderError(
              'spawn',
              'xvfb_exited',
              'Xvfb exited before ' + display + ' became ready (exit ' + xvfb.exitCode + ')',
              XVFB_FIX,
              { display, exitCode: xvfb.exitCode },
            ),
          )
          return
        }
        if (Date.now() >= deadline) {
          clearInterval(timer)
          reject(
            new DisplayProviderError(
              'spawn',
              'provider_timeout',
              'Xvfb did not create ' + display + ' within ' + timeoutMs + 'ms',
              XVFB_FIX,
              { display, timeoutMs },
            ),
          )
        }
      }, 25)
      if (typeof timer.unref === 'function') timer.unref()
    })
  }

  private grace(blender: ChildProcess, stdout: string[], stderr: string[]): Promise<void> {
    const delay =
      this.options.readyGraceMs === undefined ? READY_GRACE_MS : this.options.readyGraceMs
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (blender.exitCode !== null)
          reject(
            new DisplayProviderError(
              'spawn',
              'blender_exited',
              'Blender exited before the display became usable (exit ' + blender.exitCode + ')',
              'inspect the provider output; a missing X connection or add-on error is the usual cause',
              { exitCode: blender.exitCode, stdout: stdout.slice(), stderr: stderr.slice() },
            ),
          )
        else resolve()
      }, delay)
      if (typeof timer.unref === 'function') timer.unref()
    })
  }

  async start(request: DisplayStartRequest): Promise<DisplayInstance> {
    const spec = this.commandSpec(request)
    const spawnFn: SpawnDisplayChild =
      this.options.spawnImpl || ((command, args, options) => spawn(command, args, options))
    const stdout: string[] = []
    const stderr: string[] = []
    const xvfb = spawnFn(spec.xvfb.command, spec.xvfb.args, spec.xvfb.options)
    attachLines(xvfb, (line, stream) => {
      if (line) pushTail(stream === 'stdout' ? stdout : stderr, '[xvfb] ' + line)
      this.options.onOutput?.(line, stream)
    })
    try {
      await this.displayReady(spec.display, xvfb)
    } catch (error) {
      signalTree(xvfb, 'SIGKILL', this.options.signalGroup)
      this.usedDisplays.delete(Number(spec.display.slice(1)))
      throw error
    }
    const blender = spawnFn(spec.blender.command, spec.blender.args, spec.blender.options)
    attachLines(blender, (line, stream) => {
      if (line) pushTail(stream === 'stdout' ? stdout : stderr, line)
      this.options.onOutput?.(line, stream)
    })
    try {
      await this.grace(blender, stdout, stderr)
    } catch (error) {
      signalTree(blender, 'SIGKILL', this.options.signalGroup)
      signalTree(xvfb, 'SIGKILL', this.options.signalGroup)
      this.usedDisplays.delete(Number(spec.display.slice(1)))
      throw error
    }
    const id = 'display_' + ++this.counter
    const handle: DisplayInstance = {
      id,
      display: spec.display,
      pid: blender.pid || null,
      provider: this.kind,
    }
    const record: ProviderRecord = {
      handle,
      xvfb,
      blender,
      number: Number(spec.display.slice(1)),
      hardwareGl: spec.hardwareGl,
      glTransport: spec.glTransport,
      stdout,
      stderr,
      exitCode: null,
      startedAt: Date.now(),
    }
    this.records.set(id, record)
    blender.once('exit', (code) => {
      record.exitCode = code
      this.options.onExit?.(handle, code)
    })
    return handle
  }

  private cleanupDisplay(number: number): void {
    this.usedDisplays.delete(number)
    for (const file of [displayPath(number), lockPath(number)]) {
      try {
        if (fs.existsSync(file)) fs.unlinkSync(file)
      } catch (e) {
        /* Xvfb normally removes both itself */
      }
    }
  }

  async stop(instance: DisplayInstance): Promise<void> {
    const record = this.records.get(instance.id)
    if (!record) return
    this.records.delete(instance.id)
    const timeoutMs = this.options.stopTimeoutMs || STOP_TIMEOUT_MS
    signalTree(record.blender, 'SIGTERM', this.options.signalGroup)
    signalTree(record.xvfb, 'SIGTERM', this.options.signalGroup)
    const [blenderExit, xvfbExit] = await Promise.all([
      waitExit(record.blender, timeoutMs),
      waitExit(record.xvfb, timeoutMs),
    ])
    if (!blenderExit) signalTree(record.blender, 'SIGKILL', this.options.signalGroup)
    if (!xvfbExit) signalTree(record.xvfb, 'SIGKILL', this.options.signalGroup)
    await Promise.all([waitExit(record.blender, 1000), waitExit(record.xvfb, 1000)])
    this.cleanupDisplay(record.number)
  }

  async health(instance: DisplayInstance): Promise<DisplayHealth> {
    const record = this.records.get(instance.id)
    if (!record) {
      return { ok: false, detail: 'display handle is not owned by this Gateway (already stopped)' }
    }
    if (record.xvfb.exitCode !== null) {
      return { ok: false, detail: 'Xvfb exited with code ' + String(record.xvfb.exitCode) }
    }
    if (record.exitCode !== null) {
      return { ok: false, detail: 'Blender exited with code ' + String(record.exitCode) }
    }
    return {
      ok: true,
      detail:
        'xvfb pid=' +
        String(record.xvfb.pid || '?') +
        ' blender pid=' +
        String(record.blender.pid || '?') +
        ' display=' +
        record.handle.display +
        ' gl=' +
        record.glTransport,
    }
  }

  /** Relay tail plus the chosen GL transport for diagnostics; null when unowned. */
  output(instance: DisplayInstance): {
    stdout: string[]
    stderr: string[]
    exitCode: number | null
    startedAt: number
    hardware_gl: boolean
    gl_transport: string
  } | null {
    const record = this.records.get(instance.id)
    if (!record) return null
    return {
      stdout: record.stdout.slice(),
      stderr: record.stderr.slice(),
      exitCode: record.exitCode,
      startedAt: record.startedAt,
      hardware_gl: record.hardwareGl,
      gl_transport: record.glTransport,
    }
  }
}
