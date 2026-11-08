// Default queue executor: spawn one fresh Blender process per job.
//
// The queue never executes job work in the Gateway process. This module only
// turns declarative parameters into Blender CLI arguments, captures the logs,
// and terminates the exact process tree on cancel or timeout.
import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { CFG, IS_WIN } from '../../config.js'
import { BLENDER_EXE } from '../../paths.js'
import type { JobExecutionResult, JobExecutor, JobHandle, JobRecord } from './types.js'

export interface SpawnExecutorOptions {
  blenderExe?: string
  now?: () => number
  tempRoot?: string
  spawnImpl?: typeof spawn
}

const MAX_LOG = 262144
const DEFAULT_TIMEOUT_MS = 60 * 60 * 1000

/** The Gateway workDir is probed writable at startup; never rely on %TEMP% for job files. */
function workRoot(): string {
  return IS_WIN ? CFG.workDirWin : CFG.workDirWsl
}

function ensureDir(dir: string): void {
  fs.mkdirSync(dir, { recursive: true })
}

interface Prepared {
  args: string[]
  tempDir: string | null
  outdir: string | null
}

function asString(value: unknown): string {
  return value === undefined || value === null ? '' : String(value)
}

function frameRange(params: Record<string, unknown>): { start: number; end: number } | null {
  const raw = params.frames
  if (Array.isArray(raw) && raw.length === 2) return { start: Number(raw[0]), end: Number(raw[1]) }
  if (typeof raw === 'string' && raw.includes('-')) {
    const [a, b] = raw.split('-')
    if (Number.isFinite(Number(a)) && Number.isFinite(Number(b)))
      return { start: Number(a), end: Number(b) }
  }
  if (raw !== undefined && raw !== null) {
    const n = Number(raw)
    if (Number.isFinite(n)) return { start: n, end: n }
  }
  return null
}

/** A fixed internal Python entry for render parameters: no caller script is ever accepted. */
function renderPrelude(params: Record<string, unknown>): string {
  const lines = ['import bpy, json, os']
  const res = params.resolution
  if (Array.isArray(res) && res.length === 2)
    lines.push(
      's=bpy.context.scene',
      's.render.resolution_x=' + Number(res[0]),
      's.render.resolution_y=' + Number(res[1]),
      's.render.resolution_percentage=100',
    )
  if (params.engine) lines.push('s.render.engine=' + JSON.stringify(asString(params.engine)))
  if (params.samples !== undefined) {
    const samples = Math.max(1, Number(params.samples) || 1)
    const engine = asString(params.engine)
    if (engine === 'CYCLES') lines.push('s.cycles.samples=' + samples)
    else if (engine === 'BLENDER_EEVEE_NEXT' || engine === 'BLENDER_EEVEE')
      lines.push('s.eevee.taa_render_samples=' + samples)
  }
  if (params.format)
    lines.push('s.render.image_settings.file_format=' + JSON.stringify(asString(params.format)))
  if (params.output) {
    const out = asString(params.output)
    const target = out.endsWith('/') || out.endsWith('\\') ? out : out + path.sep
    lines.push('s.render.filepath=' + JSON.stringify(target))
  }
  if (params.camera !== undefined) {
    const camera = params.camera
    if (camera === true) lines.push('pass')
    else lines.push('s.camera=bpy.data.objects.get(' + JSON.stringify(asString(camera)) + ')')
  }
  return lines.join('\n')
}

/** Exported for contract tests: render and headless use one fixed internal entry. */
export function buildJobArgs(
  job: JobRecord,
  tempDir: string,
): { args: string[]; outdir: string | null } {
  const params = job.params
  let outdir = asString(params.outdir) || null
  if (outdir) ensureDir(outdir)
  if (job.kind === 'render') {
    const file = asString(params.file)
    const args: string[] = []
    if (file) args.push(file)
    let output = asString(params.output)
    // A render without an explicit output still needs a persistent, writable place:
    // Blender's own fallback (C:/tmp on Windows) is not writable in every install.
    if (!output) {
      outdir = outdir || path.join(workRoot(), 'renders', job.id)
      ensureDir(outdir)
      output = outdir.endsWith('/') || outdir.endsWith('\\') ? outdir : outdir + path.sep
    } else if (!outdir) {
      const trimmed = output.replace(/[\\/]+$/, '')
      outdir = path.dirname(trimmed)
      ensureDir(outdir)
    }
    const renderParams = { ...params, output }
    args.push('-b', '--factory-startup', '--python-expr', renderPrelude(renderParams))
    const frames = frameRange(params)
    if (frames) args.push('-s', String(frames.start), '-e', String(frames.end), '-a')
    if (output && !output.endsWith('/') && !output.endsWith('\\')) ensureDir(path.dirname(output))
    return { args, outdir }
  }
  const file = asString(params.file)
  const args: string[] = []
  if (file) args.push(file)
  args.push('-b', '--factory-startup')
  const scriptPath = path.join(tempDir, 'job.py')
  const script = asString(params.script)
  if (script) fs.writeFileSync(scriptPath, script, 'utf8')
  const scriptFile = asString(params.script_file)
  if (scriptFile) args.push('--python', scriptFile)
  else if (script) args.push('--python', scriptPath)
  const extra = params.args
  if (Array.isArray(extra)) args.push(...extra.map(asString))
  return { args, outdir }
}

function prepare(job: JobRecord, spawnOptions: SpawnExecutorOptions): Prepared {
  const tempRoot = spawnOptions.tempRoot || path.join(workRoot(), 'tmp')
  ensureDir(tempRoot)
  const tempDir = fs.mkdtempSync(path.join(tempRoot, 'dsh-job-'))
  const built = buildJobArgs(job, tempDir)
  return { ...built, tempDir }
}

function prepareFailure(e: unknown, blenderExe: string | null): JobExecutionResult {
  return {
    exitCode: null,
    signal: null,
    stdout: '',
    stderr: e instanceof Error ? e.message : String(e),
    error: {
      ok: false,
      code: 'blender_exe_missing',
      stage: 'spawn',
      root_cause: 'Could not start Blender: ' + (e instanceof Error ? e.message : String(e)),
      fix: 'Set DSH_BLENDER_EXE or write dsh-blender.config.json with blenderExe.',
      fallback: { kind: 'unavailable', summary: 'install Blender or set DSH_BLENDER_EXE' },
      evidence: { blenderExe },
    },
  }
}

function artifactsIn(outdir: string | null, since: number): string[] {
  if (!outdir) return []
  const found: string[] = []
  const walk = (dir: string, depth: number): void => {
    if (depth > 4 || found.length >= 100) return
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      if (found.length >= 100) return
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(full, depth + 1)
      else {
        try {
          if (fs.statSync(full).mtimeMs >= since) found.push(full)
        } catch {
          /* a file may disappear while collecting */
        }
      }
    }
  }
  walk(outdir, 0)
  return found
}

function killTree(child: ChildProcess): void {
  const pid = child.pid
  if (!pid) return
  if (IS_WIN) {
    try {
      spawn('taskkill.exe', ['/PID', String(pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      })
    } catch {
      child.kill('SIGKILL')
    }
    return
  }
  try {
    process.kill(-pid, 'SIGKILL')
  } catch {
    try {
      child.kill('SIGKILL')
    } catch {
      /* already exited */
    }
  }
}

function spawnBlender(opts: SpawnExecutorOptions, prepared: Prepared, jobId: string): ChildProcess {
  return (opts.spawnImpl || spawn)(opts.blenderExe || (BLENDER_EXE as string), prepared.args, {
    env: { ...process.env, DSH_JOB_ID: jobId },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    detached: !IS_WIN,
  })
}

function captureLogs(child: ChildProcess): { stdout: string; stderr: string } {
  const logs = { stdout: '', stderr: '' }
  child.stdout?.on('data', (buf: Buffer) => {
    logs.stdout = (logs.stdout + buf.toString('utf8')).slice(-MAX_LOG)
  })
  child.stderr?.on('data', (buf: Buffer) => {
    logs.stderr = (logs.stderr + buf.toString('utf8')).slice(-MAX_LOG)
  })
  return logs
}

function armTimeout(child: ChildProcess, timeoutMs: number, onTimeout: () => void): NodeJS.Timeout {
  const timer = setTimeout(
    () => {
      onTimeout()
      killTree(child)
    },
    Math.max(1000, timeoutMs),
  )
  timer.unref?.()
  return timer
}

function parseHeadless(text: string): unknown {
  const matches = text.match(/^HEADLESS (\{.*\})$/gm)
  if (!matches || matches.length === 0) return undefined
  const last = matches[matches.length - 1].slice('HEADLESS '.length)
  try {
    return JSON.parse(last)
  } catch {
    return undefined
  }
}

export class SpawnJobExecutor implements JobExecutor {
  private readonly opts: SpawnExecutorOptions
  constructor(opts: SpawnExecutorOptions = {}) {
    this.opts = opts
  }

  start(job: JobRecord): JobHandle {
    let prepared: Prepared
    try {
      prepared = prepare(job, this.opts)
    } catch (e) {
      return {
        pid: null,
        cancel: () => {},
        done: Promise.resolve(prepareFailure(e, this.opts.blenderExe || BLENDER_EXE)),
      }
    }
    let child: ChildProcess
    try {
      child = spawnBlender(this.opts, prepared, job.id)
    } catch (e) {
      return {
        pid: null,
        cancel: () => {},
        done: Promise.resolve(prepareFailure(e, this.opts.blenderExe || BLENDER_EXE)),
      }
    }
    const started = (this.opts.now || Date.now)()
    const logs = captureLogs(child)
    let timedOut = false
    const timeoutMs = Number(job.params.timeout_ms) || DEFAULT_TIMEOUT_MS
    const timer = armTimeout(child, timeoutMs, () => {
      timedOut = true
    })
    const done = new Promise<JobExecutionResult>((resolve) => {
      let settled = false
      const finish = (exitCode: number | null, signal: string | null): void => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        if (prepared.tempDir) {
          try {
            fs.rmSync(prepared.tempDir, { recursive: true, force: true })
          } catch {
            /* temp cleanup is best effort */
          }
        }
        const parsed = job.kind === 'headless' ? parseHeadless(logs.stdout) : undefined
        resolve({
          exitCode,
          signal,
          stdout: logs.stdout,
          stderr: logs.stderr,
          artifacts: artifactsIn(prepared.outdir, started),
          ...(parsed === undefined ? {} : { parsed }),
          ...(timedOut
            ? {
                error: {
                  ok: false,
                  code: 'job_timeout',
                  stage: 'render' as const,
                  root_cause: 'The job exceeded its timeout.',
                  fix: 'Raise timeout_ms or reduce the workload, then resubmit.',
                  fallback: {
                    kind: 'unavailable' as const,
                    summary: 'resubmit with a larger timeout',
                  },
                  evidence: { timeoutMs },
                },
              }
            : {}),
        })
      }
      child.once('error', (e) => {
        clearTimeout(timer)
        settled = true
        resolve(prepareFailure(e, this.opts.blenderExe || BLENDER_EXE))
      })
      child.once('close', (code, signal) => finish(code, signal ? String(signal) : null))
    })
    return { pid: child.pid ?? null, cancel: () => killTree(child), done }
  }
}
