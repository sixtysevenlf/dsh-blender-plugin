// Default queue executor: spawn one fresh Blender process per job.
//
// The queue never executes job work in the Gateway process. This module only
// turns declarative parameters into Blender CLI arguments, captures the logs,
// and terminates the exact process tree on cancel or timeout.
import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { IS_WIN } from '../../config.js'
import type { AvailabilityFailure } from '../availability.js'
import { homePath } from '../../home.js'
import { BLENDER_EXE } from '../../paths.js'
import { RENDER_PASSES_MANIFEST_VERSION, RENDER_PASSES_PY } from './render-passes-script.js'
import { asString, frameRange, headlessArgs } from './job-args.js'
import type { JobExecutionResult, JobExecutor, JobHandle, JobRecord } from './types.js'

export interface SpawnExecutorOptions {
  blenderExe?: string
  now?: () => number
  tempRoot?: string
  spawnImpl?: typeof spawn
}

const MAX_LOG = 262144
const DEFAULT_TIMEOUT_MS = 60 * 60 * 1000

/**
 * Job temp files live under the plug-in home (queue/staging). The home layout is
 * created at activation; never rely on %TEMP% for job files.
 */
function workRoot(): string {
  return homePath('queue', 'staging')
}

function ensureDir(dir: string): void {
  fs.mkdirSync(dir, { recursive: true })
}

interface Prepared {
  args: string[]
  tempDir: string | null
  outdir: string | null
}

function renderPrelude(params: Record<string, unknown>): string {
  const lines = ['import bpy, json, os', 's=bpy.context.scene']
  const res = params.resolution
  if (Array.isArray(res) && res.length === 2)
    lines.push(
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

/**
 * Declarative pass list from submitted params. Passes are opt-in: an absent
 * field keeps the legacy render path, while a present-but-empty or malformed
 * list is a rejected submission, not a silent fallback.
 */
export class JobParamError extends Error {
  readonly code = 'invalid_params'
  constructor(message: string) {
    super(message)
    this.name = 'JobParamError'
  }
}

export class PassConfigError extends Error {
  readonly code = 'render_config_invalid'
  constructor(message: string) {
    super(message)
    this.name = 'PassConfigError'
  }
}

function renderPasses(params: Record<string, unknown>): string[] {
  const raw = params.passes
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw))
    throw new PassConfigError('passes must be an array of pass names, got ' + typeof raw + '.')
  if (raw.length === 0)
    throw new PassConfigError(
      'passes is empty: name at least one pass, or omit the field to render without a cache.',
    )
  const passes: string[] = []
  for (const item of raw) {
    if (typeof item !== 'string')
      throw new PassConfigError('passes must contain only pass names, got a non-string entry.')
    const name = item.trim()
    if (!name) throw new PassConfigError('passes must contain only non-empty pass names.')
    passes.push(name)
  }
  return passes
}

interface PassEntry {
  scriptPath: string
  configPath: string
}

/**
 * Write the two fixed files of the pass entry into the job temp directory.
 * render-config.json carries only declarative values copied from the submitted
 * params; nothing a caller wrote is ever executed.
 */
function writePassEntry(
  params: Record<string, unknown>,
  tempDir: string,
  outdir: string,
): PassEntry {
  const scriptPath = path.join(tempDir, 'render_passes.py')
  fs.writeFileSync(scriptPath, RENDER_PASSES_PY, 'utf8')
  const frames = frameRange(params)
  const resolution =
    Array.isArray(params.resolution) && params.resolution.length === 2
      ? [Number(params.resolution[0]), Number(params.resolution[1])]
      : null
  const camera = params.camera === true ? true : asString(params.camera) || null
  const config = {
    version: 1,
    manifestVersion: RENDER_PASSES_MANIFEST_VERSION,
    passes: renderPasses(params),
    frames: frames ? [frames.start, frames.end] : null,
    outdir,
    camera,
    resolution,
    samples:
      params.samples === undefined || params.samples === null ? null : Number(params.samples),
    engine: asString(params.engine) || null,
    format: asString(params.format) || 'PNG',
  }
  const configPath = path.join(tempDir, 'render-config.json')
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + '\n', 'utf8')
  return { scriptPath, configPath }
}

/** Headless: one fresh `blender -b` per job, the caller's script is the only entry. */
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
    const passes = renderPasses(params)
    if (passes.length > 0) {
      // The pass entry owns the frame loop (one manifest per frame), so the
      // legacy -s/-e/-a animation flags must not be appended here.
      const passOutdir = outdir || path.join(workRoot(), 'renders', job.id)
      ensureDir(passOutdir)
      const entry = writePassEntry(params, tempDir, passOutdir)
      const passArgs: string[] = ['-b', '--factory-startup']
      if (file) passArgs.push(file)
      // --python-exit-code: an uncaught Python error must fail the job instead of
      // exiting 0. --disable-autoexec: only the bundled entry may run.
      passArgs.push('--python-exit-code', '2', '--disable-autoexec')
      passArgs.push('--python', entry.scriptPath, '--', entry.configPath)
      return { args: passArgs, outdir: passOutdir }
    }
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
    // --python-exit-code: the fixed entry runs as --python-expr here too, so an
    // uncaught Python error (e.g. a bad parameter) must fail the job instead of
    // exiting 0 and reporting a render that produced no file.
    args.push(
      '-b',
      '--factory-startup',
      '--python-exit-code',
      '2',
      '--python-expr',
      renderPrelude(renderParams),
    )
    const frames = frameRange(params)
    if (!frames && params.frames !== undefined && params.frames !== null)
      throw new JobParamError(
        'frames=' +
          JSON.stringify(params.frames) +
          ' is invalid: pass N, "A-B", or [A, B] with A <= B',
      )
    if (frames) args.push('-s', String(frames.start), '-e', String(frames.end), '-a')
    if (output && !output.endsWith('/') && !output.endsWith('\\')) ensureDir(path.dirname(output))
    return { args, outdir }
  }
  return { args: headlessArgs(params, tempDir), outdir }
}

function removeTempDir(dir: string | null): void {
  if (!dir) return
  try {
    fs.rmSync(dir, { recursive: true, force: true })
  } catch {
    /* temp cleanup is best effort */
  }
}

function prepare(job: JobRecord, spawnOptions: SpawnExecutorOptions): Prepared {
  const tempRoot = spawnOptions.tempRoot || path.join(workRoot(), 'tmp')
  ensureDir(tempRoot)
  const tempDir = fs.mkdtempSync(path.join(tempRoot, 'dsh-job-'))
  try {
    const built = buildJobArgs(job, tempDir)
    return { ...built, tempDir }
  } catch (e) {
    // A rejected submission must not leave a dsh-job-* directory behind.
    removeTempDir(tempDir)
    throw e
  }
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

/** A rejected frames/parameter value happens before Blender starts. */
function paramFailure(e: JobParamError): JobExecutionResult {
  return {
    exitCode: null,
    signal: null,
    stdout: '',
    stderr: e.message,
    error: {
      ok: false,
      code: e.code,
      stage: 'config',
      root_cause: e.message,
      fix: 'Fix the job parameter (frames must be N, "A-B", or [A, B] with A <= B), then resubmit.',
      fallback: { kind: 'unavailable', summary: 'correct the parameter and resubmit' },
      evidence: { param: 'frames' },
    },
  }
}

/** A rejected pass configuration happens before Blender starts. */
function configFailure(e: unknown): JobExecutionResult {
  const message = e instanceof Error ? e.message : String(e)
  return {
    exitCode: null,
    signal: null,
    stdout: '',
    stderr: message,
    error: {
      ok: false,
      code: 'render_config_invalid',
      stage: 'config',
      root_cause: message,
      fix: 'Pass passes=["beauty", …] with at least one known pass, or drop the field to render without the pass cache.',
      fallback: { kind: 'unavailable', summary: 'resubmit a valid pass list' },
      evidence: { stage: 'config' },
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

function markerJson(text: string, marker: string): unknown {
  const matches = text.match(new RegExp('^' + marker + ' (\\{.*\\})$', 'gm'))
  if (!matches || matches.length === 0) return undefined
  const last = matches[matches.length - 1].slice(marker.length + 1)
  try {
    return JSON.parse(last)
  } catch {
    return undefined
  }
}

function parseHeadless(text: string): unknown {
  return markerJson(text, 'HEADLESS')
}

/**
 * headless out_json: persist the structured result the script printed (the
 * `HEADLESS {json}` contract) so callers do not have to scrape stdout. The
 * parameter used to be accepted and silently ignored, leaving no file at all.
 */
function writeOutJson(
  job: JobRecord,
  parsed: unknown,
  exitCode: number | null,
): AvailabilityFailure | null {
  const target = asString(job.params.out_json).trim()
  if (!target) return null
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(
      target,
      JSON.stringify({ ok: exitCode === 0, exitCode, result: parsed ?? null }, null, 2) + '\n',
      'utf8',
    )
    return null
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e)
    return {
      ok: false,
      code: 'out_json_write_failed',
      stage: 'queue',
      root_cause: 'Could not write out_json to ' + target + ': ' + message,
      fix: 'Point out_json at a writable path (an absolute directory the DSH host can write), then resubmit.',
      fallback: { kind: 'unavailable', summary: 'read the job logs for the structured result' },
      evidence: { out_json: target },
    }
  }
}

/** The pass entry prints PASSES_DONE on success and PASSES_ERROR on a fatal failure. */
function parsePassesDone(text: string): unknown {
  return markerJson(text, 'PASSES_DONE')
}

function passesFailure(stdout: string): AvailabilityFailure | null {
  const marker = markerJson(stdout, 'PASSES_ERROR') as Record<string, unknown> | undefined
  if (!marker || marker.ok === true) return null
  const code = String(marker.code || 'render_passes_failed')
  const passName = String(marker.passName || '')
  const format = typeof marker.format === 'string' ? marker.format : ''
  return {
    ok: false,
    code,
    stage: 'render',
    root_cause: String(marker.error || 'The render-pass entry failed.'),
    fix:
      code !== 'pass_unsupported'
        ? 'Read the job logs, fix the reported render error, then resubmit.'
        : format
          ? 'The render-pass path writes 8-bit PNG beauty only; set format=PNG (or drop passes and use the legacy render path) instead of ' +
            format +
            '.'
          : passName === 'object_index'
            ? 'object_index needs engine=CYCLES on every supported Blender (4.2 / 4.5 / 5.x expose Object Index only under Cycles); switch the engine to CYCLES or drop the pass.'
            : 'Pick another pass, update Blender, or render without that pass; the report names the unavailable pass.',
    fallback: { kind: 'unavailable', summary: 'inspect the render pass logs and retry' },
    evidence: marker,
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
      if (e instanceof JobParamError)
        return { pid: null, cancel: () => {}, done: Promise.resolve(paramFailure(e)) }
      if (e instanceof PassConfigError)
        return { pid: null, cancel: () => {}, done: Promise.resolve(configFailure(e)) }
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
      removeTempDir(prepared.tempDir)
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
        const parsed =
          job.kind === 'headless'
            ? parseHeadless(logs.stdout)
            : job.kind === 'render'
              ? parsePassesDone(logs.stdout)
              : undefined
        const failed = job.kind === 'render' ? passesFailure(logs.stdout) : null
        const writeFailure = job.kind === 'headless' ? writeOutJson(job, parsed, exitCode) : null
        resolve({
          exitCode,
          signal,
          stdout: logs.stdout,
          stderr: logs.stderr,
          artifacts: artifactsIn(prepared.outdir, started),
          ...(parsed === undefined ? {} : { parsed }),
          ...(writeFailure
            ? { error: writeFailure }
            : failed && exitCode !== 0
              ? { error: failed }
              : {}),
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
    return {
      pid: child.pid ?? null,
      cancel: () => killTree(child),
      done,
      tempDir: prepared.tempDir,
    }
  }
}
