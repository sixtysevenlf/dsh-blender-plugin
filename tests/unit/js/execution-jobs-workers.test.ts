import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// T5b: deterministic P0 behaviour tests for runtime/execution/jobs.ts and
// runtime/execution/workers.ts.
//
// No real Blender process, no real listening socket and no real sleep:
// spawn() and net.connect() are replaced with controllable fakes, files live
// in mkdtemp() directories, and the waiting contracts (kill timeout, wait
// window, worker TTL) run on fake timers.

const NL = '\n'

const h = vi.hoisted(() => ({
  spawnCalls: [] as any[],
  sockets: [] as any[],
  socketsAnswered: 0,
  metrics: { workerStarts: 0, workerExecs: 0 },
  pidAlive: false as boolean,
  ledgerRows: [] as any[],
  pathWarnings: [] as any[],
  stagingSubstituted: false as boolean,
}))

vi.mock('node:child_process', async () => {
  const { EventEmitter } = await import('node:events')
  const { PassThrough } = await import('node:stream')
  const spawn = vi.fn((exe: unknown, args: unknown, opts: unknown) => {
    const child: any = new EventEmitter()
    child.pid = 4100 + h.spawnCalls.length
    child.exitCode = null
    child.signalCode = null
    child.stdout = new PassThrough()
    child.stderr = new PassThrough()
    child.exe = exe
    child.args = args
    child.spawnOpts = opts
    child.kill = vi.fn()
    h.spawnCalls.push(child)
    return child
  })
  return { spawn, default: { spawn } }
})

vi.mock('node:net', async () => {
  const { EventEmitter } = await import('node:events')
  const connect = vi.fn(() => {
    const sock: any = new EventEmitter()
    sock.written = ''
    sock.destroy = vi.fn()
    sock.write = vi.fn((s: string) => {
      sock.written += s
      return true
    })
    h.sockets.push(sock)
    return sock
  })
  return { default: { connect }, connect }
})

vi.mock('../../../runtime/config.js', () => ({
  winToWsl: (p: unknown) => String(p),
  wslToWin: (p: unknown) => String(p),
  CFG: { workerPort: 9879 },
}))

vi.mock('../../../runtime/paths.js', () => ({
  BLENDER_EXE: 'C:/fake/blender.exe',
  GPU_PRELUDE: '# gpu __DSH_ENGINE__ __DSH_GPU_MANUAL__',
  WIN_TMP: 'C:/fake/tmp',
  pyModuleFile: () => null,
  pyModuleNames: () => ['vehicle', 'audit'],
  WORKER_PATH: 'C:/fake/worker.py',
  USER_CONFIG_WIN: 'C:/fake/user-config',
  USER_SCRIPTS_WIN: 'C:/fake/user-scripts',
}))

vi.mock('../../../runtime/core/evidence.js', () => ({
  PLUGIN_VERSION: '3.0.0-test',
  pathAudit: vi.fn(() => h.pathWarnings.slice()),
  pidAlive: vi.fn(() => h.pidAlive),
  sceneHygieneAudit: vi.fn(() => []),
}))

vi.mock('../../../runtime/core/output.js', () => ({
  slipFixPath: (p: unknown) => String(p),
}))

vi.mock('../../../runtime/core/scripts.js', () => ({
  PATH_GUARD: '# path guard',
  envPrelude: (pairs: Record<string, unknown>) => '# env ' + JSON.stringify(pairs),
  fileFingerprint: (win: string) => ({ given: win, md5: 'f'.repeat(32), bytes: 1 }),
  scriptFileBlock: (p: string) => '# script-file ' + p,
  shotsBlock: (o: unknown) => '# shots ' + JSON.stringify(o),
  workdirBlock: (p: string) => '# workdir ' + p,
}))

vi.mock('../../../runtime/core/kernel.js', () => ({
  KERNEL_BOOTSTRAP: '# kernel bootstrap',
}))

vi.mock('../../../runtime/hard_exit.js', () => ({
  hardExitJobParts: (opts?: { hardExit?: boolean }) =>
    opts && opts.hardExit === false ? [] : ['# flush', '# _exit'],
}))

vi.mock('../../../runtime/staging.js', () => ({
  writeHeadlessScript: vi.fn((code: string) => ({
    win: 'C:/fake/headless.py',
    staging: {
      requested: 'C:/work',
      requestedShared: true,
      requestedWhy: 'shared',
      used: 'C:/work',
      usedWin: 'C:/work',
      usedShared: true,
      usedWhy: 'shared',
      label: 'workDir',
      substituted: h.stagingSubstituted,
      note: h.stagingSubstituted ? 'moved to fallback' : 'shared',
    },
    code,
  })),
}))

vi.mock('../../../runtime/proc_env.js', () => ({
  baseChildEnv: (extra: Record<string, unknown>) => Object.assign({}, extra),
  withWslEnv: (env: Record<string, unknown>) => env,
  workdirStagingWarning: (s: any) =>
    s && s.substituted
      ? { code: 'WORKDIR_NOT_SHARED', value: 'why', evidence: 'note', hint: 'hint' }
      : null,
}))

vi.mock('../../../runtime/execution/ledger.js', () => ({
  createLedger: vi.fn(),
}))

import { createJobsApi } from '../../../runtime/execution/jobs.js'
import { createWorkersApi } from '../../../runtime/execution/workers.js'

const TMP_DIRS: string[] = []

function mkTmp(prefix: string): string {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-t5b-' + prefix + '-'))
  TMP_DIRS.push(d)
  return d
}

async function flush(n = 12): Promise<void> {
  for (let i = 0; i < n; i += 1) await Promise.resolve()
}

function lastSpawn(): any {
  return h.spawnCalls[h.spawnCalls.length - 1]
}

// Answers the next short-lived worker request socket: emits connect, parses the
// JSON request the worker layer wrote, then replies with one newline-delimited
// frame carrying the same id.
async function answerSocket(payload: Record<string, unknown> = {}): Promise<any> {
  const idx = h.socketsAnswered
  for (let i = 0; i < 200 && h.sockets.length <= idx; i += 1) await Promise.resolve()
  const sock = h.sockets[idx]
  if (!sock) throw new Error('no worker socket was created')
  h.socketsAnswered += 1
  sock.emit('connect')
  const req = JSON.parse(String(sock.written).trim())
  sock.emit('data', Buffer.from(JSON.stringify(Object.assign({ id: req.id }, payload)) + NL))
  await flush()
  return req
}

function makeLedger(): any {
  const appended: any[] = []
  const runs = new Map<string, any>()
  const api: any = {
    runs,
    LEDGER: 'C:/fake/ledger.jsonl',
    ledgerAppend: vi.fn((rec: any) => {
      appended.push(rec)
    }),
    ledgerState: vi.fn(() => ({})),
    ledgerList: vi.fn((limit = 40) => h.ledgerRows.slice(0, limit)),
    runSnapshot: vi.fn((r: any) =>
      Object.assign({ kind: 'headless', id: r.id, status: r.status }, r),
    ),
    reconcileLedger: vi.fn((rec: any) =>
      rec && rec.status === 'running' ? Object.assign({}, rec, { status: 'stale' }) : rec,
    ),
    appended,
  }
  return api
}

function makeJobs(overrides: Record<string, unknown> = {}): { api: any; ledger: any } {
  const ledger = (overrides.ledger as any) || makeLedger()
  const deps: Record<string, unknown> = {
    HERE: 'C:/here',
    SESSION_NAME: 'session-test',
    dumpResult: vi.fn(() => ({ path: 'C:/fake/result.json', bytes: 7 })),
    preloadChunk: vi.fn((name: string) => '# preload ' + name),
    readScriptPath: vi.fn((p: unknown) => ({ win: String(p), wsl: String(p) })),
    ledger,
  }
  Object.assign(deps, overrides)
  const api: any = createJobsApi(deps as any)
  return { api, ledger }
}

function makeWorkers(): any {
  return createWorkersApi({
    metrics: h.metrics,
    preloadChunk: (name: string) => '# preload ' + name,
  } as any)
}

// Start a worker against the fake child and answer the optional warmup see.
async function bootWorker(
  api: any,
  opts: Record<string, unknown> = {},
  name?: string,
): Promise<any> {
  const promise: Promise<any> =
    name === undefined ? api.workerStart(opts) : api.workerStart(opts, name)
  const child = lastSpawn()
  child.stdout.write('DSH_WORKER ' + JSON.stringify({ ok: true, pid: 777, gpu: 'fake-gpu' }) + NL)
  await flush()
  if (opts.warmup !== false) await answerSocket({ ok: true, ms: 5 })
  return await promise
}

beforeEach(() => {
  h.spawnCalls.length = 0
  h.sockets.length = 0
  h.socketsAnswered = 0
  h.metrics.workerStarts = 0
  h.metrics.workerExecs = 0
  h.pidAlive = false
  h.ledgerRows = []
  h.pathWarnings = []
  h.stagingSubstituted = false
  delete process.env.DSH_WORKER_TTL_MS
  delete process.env.DSH_BLENDER_WORKER_PORT
})

afterEach(async () => {
  // Close any job still wired to a fake child before the temp dir disappears;
  // otherwise the log write streams can emit an unhandled ENOENT after the test.
  for (const c of h.spawnCalls) {
    try {
      if (c.stdout) c.stdout.end()
      if (c.stderr) c.stderr.end()
      c.emit('close', c.exitCode === null ? 0 : c.exitCode, null)
    } catch (e) {
      /* already closed */
    }
  }
  vi.useRealTimers()
  // Give the lazily-opened log write streams a couple of macrotask turns to
  // settle before the temp directory is removed (no sleeps involved).
  await new Promise<void>((resolve) => setImmediate(resolve))
  await new Promise<void>((resolve) => setImmediate(resolve))
  for (const d of TMP_DIRS.splice(0)) {
    try {
      fs.rmSync(d, { recursive: true, force: true })
    } catch (e) {
      /* windows may still hold log streams */
    }
  }
  vi.clearAllMocks()
})

describe('jobs', () => {
  it('starts a background job: spawn args, run id, log dir and running ledger row', () => {
    const { api, ledger } = makeJobs()
    const outdir = mkTmp('start')
    const snap: any = api.jobStart({ outdir, script: 'print(1)', runId: 'run-fixed' })

    expect(snap.kind).toBe('job')
    expect(snap.status).toBe('running')
    expect(snap.runId).toBe('run-fixed')
    expect(String(snap.id)).toMatch(/^job-/)

    expect(h.spawnCalls).toHaveLength(1)
    const child = lastSpawn()
    expect(child.exe).toBe('C:/fake/blender.exe')
    expect(child.args).toContain('-b')
    expect(child.args).toContain('--factory-startup')
    expect(child.args).toContain('--python')
    expect(child.args).toContain('C:/fake/headless.py')
    expect(child.spawnOpts.env.DSH_RUN_ID).toBe('run-fixed')
    expect(child.spawnOpts.env.DSH_OUTDIR).toBe(outdir)

    expect(fs.existsSync(path.join(outdir, 'jobs', String(snap.id)))).toBe(true)
    expect(ledger.appended[0]).toMatchObject({
      id: snap.id,
      kind: 'job',
      status: 'running',
      runId: 'run-fixed',
      engine: 'eevee',
    })
    expect(ledger.appended[0].timeoutMs).toBe(3600000)
  })

  it('exposes stage, line count and liveness while the job is still running', async () => {
    const { api } = makeJobs()
    const outdir = mkTmp('live')
    const snap: any = api.jobStart({ outdir })
    const child = lastSpawn()
    child.stdout.write('DSH_STAGE {"name":"build"}' + NL + 'building' + NL)
    await flush()

    const st: any = api.jobStatus(snap.id)
    expect(st.status).toBe('running')
    expect(st.stageName).toBe('build')
    expect(st.lines).toBeGreaterThanOrEqual(2)
    expect(st.pidAlive).toBe(false)

    h.pidAlive = true
    expect((api.jobStatus(snap.id) as any).pidAlive).toBe(true)

    api.jobKill(snap.id)
  })

  it('finishes a job: parses HEADLESS, writes summary.json, lists artifacts and appends a terminal ledger row', async () => {
    const { api, ledger } = makeJobs()
    const outdir = mkTmp('done')
    const shot = path.join(outdir, 'shot.png')
    const model = path.join(outdir, 'model.blend')
    fs.writeFileSync(model, 'x')
    fs.writeFileSync(shot, 'png')
    fs.mkdirSync(path.join(outdir, '__pycache__'))
    fs.writeFileSync(path.join(outdir, 'tmp_noise.txt'), 'noise')
    fs.utimesSync(model, 100, 100)
    fs.utimesSync(shot, 200, 200)
    fs.utimesSync(path.join(outdir, 'tmp_noise.txt'), 300, 300)

    const snap: any = api.jobStart({ outdir })
    const id = String(snap.id)
    const dir = path.join(outdir, 'jobs', id)
    fs.appendFileSync(path.join(dir, 'stdout.log'), 'HEADLESS {"ok":true,"x":42}' + NL)
    fs.appendFileSync(path.join(dir, 'stderr.log'), 'noise' + NL + 'RuntimeError: boom' + NL)

    const child = lastSpawn()
    child.exitCode = 0
    child.emit('close', 0, null)
    await flush()

    const st: any = api.jobStatus(id)
    expect(st.status).toBe('done')
    expect(st.exitCode).toBe(0)
    expect(st.resultJson).toEqual({ ok: true, x: 42 })
    expect(st.resultParseError).toBeNull()
    expect(st.resultBytes).toBeGreaterThan(0)
    expect(st.stdoutTail).toContain('HEADLESS')
    expect(st.lastException).toContain('RuntimeError')
    expect(st.artifacts.map((a: any) => a.name)).toEqual(['shot.png', 'model.blend'])
    expect(st.warnings.some((w: any) => w.code === 'no_input_file')).toBe(true)

    const summary = JSON.parse(fs.readFileSync(path.join(dir, 'summary.json'), 'utf8'))
    expect(summary.status).toBe('done')
    expect(summary.artifacts).toContain('model.blend(1B)')
    expect(ledger.appended.some((r: any) => r.id === id && r.status === 'done')).toBe(true)
  })

  it('failed exit code and unparsable HEADLESS are reported, raw tail is kept', async () => {
    const { api } = makeJobs()
    const outdir = mkTmp('failed')
    const snap: any = api.jobStart({ outdir })
    const id = String(snap.id)
    fs.appendFileSync(path.join(outdir, 'jobs', id, 'stdout.log'), 'HEADLESS {oops' + NL)
    const child = lastSpawn()
    child.exitCode = 1
    child.emit('close', 1, null)
    await flush()

    const st: any = api.jobStatus(id)
    expect(st.status).toBe('failed')
    expect(st.exitCode).toBe(1)
    expect(st.resultJson).toBeNull()
    expect(st.resultParseError.message).toBeTruthy()
    expect(st.resultParseError.line).toContain('{oops')
    expect(st.stdoutTail).toContain('HEADLESS {oops')
  })

  it('kill marks a running job killed; a later close does not resurrect it', () => {
    const { api } = makeJobs()
    const outdir = mkTmp('kill')
    const snap: any = api.jobStart({ outdir })
    const child = lastSpawn()

    const killed: any = api.jobKill(snap.id)
    expect(killed.status).toBe('killed')
    expect(child.kill).toHaveBeenCalledWith('SIGKILL')

    child.emit('close', 0, null)
    expect((api.jobStatus(snap.id) as any).status).toBe('killed')
  })

  it('kill of an unknown id is a safe no-op', () => {
    const { api } = makeJobs()
    const r: any = api.jobKill('missing-job')
    expect(r.ok).toBe(true)
    expect(r.status).toBe('unknown')
    expect(r.alreadyFinished).toBe(true)
    expect(r.fromLedger).toBe(false)
  })

  it('job timeout kills the child (deterministic fake timers, no sleep)', () => {
    vi.useFakeTimers()
    const { api } = makeJobs()
    const outdir = mkTmp('timeout')
    const snap: any = api.jobStart({ outdir, timeoutMs: 1000 })
    const child = lastSpawn()
    vi.advanceTimersByTime(1000)
    expect((api.jobStatus(snap.id) as any).status).toBe('killed')
    expect(child.kill).toHaveBeenCalledWith('SIGKILL')
  })

  it('recovers queued/running records from the disk ledger and merges list output', async () => {
    h.ledgerRows = [
      { id: 'job-q', kind: 'job', status: 'queued', startedAt: 2 },
      { id: 'job-done', kind: 'job', status: 'done', startedAt: 1 },
    ]
    const { api, ledger } = makeJobs()

    const queued: any = api.jobStatus('job-q')
    expect(queued.status).toBe('queued')
    expect(queued.fromLedger).toBe(true)
    expect(queued.kind).toBe('job')
    expect(ledger.reconcileLedger).toHaveBeenCalled()

    const collected: any = api.jobCollect('job-q')
    expect(collected.fromLedger).toBe(true)
    expect(collected.status).toBe('queued')

    h.ledgerRows = [{ id: 'job-run', kind: 'job', status: 'running', startedAt: 3 }]
    expect((api.jobStatus('job-run') as any).status).toBe('stale')

    h.ledgerRows = [
      { id: 'job-q', kind: 'job', status: 'queued', startedAt: 2 },
      { id: 'job-done', kind: 'job', status: 'done', startedAt: 1 },
      { id: 'job-run', kind: 'job', status: 'running', startedAt: 3 },
    ]
    const outdir = mkTmp('list')
    const started: any = api.jobStart({ outdir })
    const list: any[] = api.jobList()
    expect(list.map((x) => x.id)).toEqual(
      expect.arrayContaining(['job-q', 'job-done', 'job-run', String(started.id)]),
    )
  })

  it('waits until the job reaches a terminal state', async () => {
    vi.useFakeTimers()
    const { api } = makeJobs()
    const outdir = mkTmp('wait')
    const snap: any = api.jobStart({ outdir })
    const id = String(snap.id)
    fs.appendFileSync(path.join(outdir, 'jobs', id, 'stdout.log'), 'HEADLESS {"ok":true}' + NL)
    const child = lastSpawn()

    const waiting: Promise<any> = api.jobWait(id, 1000)
    await vi.advanceTimersByTimeAsync(500)
    child.emit('close', 0, null)
    await vi.advanceTimersByTimeAsync(500)
    const r: any = await waiting
    expect(r.status).toBe('done')
    expect(r.waitTimedOut).toBe(false)
    expect(r.waitedMs).toBeGreaterThanOrEqual(0)
  })

  it('reports waitTimedOut while the job is still running', async () => {
    vi.useFakeTimers()
    const { api } = makeJobs()
    const outdir = mkTmp('wait-timeout')
    const snap: any = api.jobStart({ outdir })

    const waiting: Promise<any> = api.jobWait(snap.id, 1000)
    await vi.advanceTimersByTimeAsync(500)
    await vi.advanceTimersByTimeAsync(500)
    const r: any = await waiting
    expect(r.waitTimedOut).toBe(true)
    expect(String(r.hint)).toContain('仍在跑')

    api.jobKill(snap.id)
  })

  it('resolves status and collect from runs and the disk ledger', () => {
    const outdir = mkTmp('status-collect')
    const outLog = path.join(outdir, 'stdout.log')
    const errLog = path.join(outdir, 'stderr.log')
    fs.writeFileSync(outLog, 'run output')
    fs.writeFileSync(errLog, 'run error')

    const first = makeJobs()
    first.ledger.runs.set('run-live', {
      id: 'run-live',
      kind: 'headless',
      status: 'running',
      logs: { stdoutWsl: outLog, stderrWsl: errLog },
    })
    const runStatus: any = first.api.jobStatus('run-live')
    expect(runStatus).toMatchObject({ id: 'run-live', status: 'running', stale: false })
    const runCollect: any = first.api.jobCollect('run-live')
    expect(runCollect.stdoutTail).toBe('run output')
    expect(runCollect.stderrTail).toBe('run error')
    expect(String(runCollect.hint)).toContain('run 与 job')

    h.ledgerRows = [{ id: 'led-done', kind: 'headless', status: 'done' }]
    expect(first.api.jobStatus('led-done')).toMatchObject({
      id: 'led-done',
      status: 'done',
      fromLedger: true,
    })
    expect(first.api.jobStatus('missing')).toMatchObject({ status: 'unknown' })
    expect(first.api.jobCollect('missing')).toMatchObject({ status: 'unknown' })

    const second = makeJobs()
    second.ledger.reconcileLedger = (rec: any) => rec
    h.ledgerRows = [
      {
        id: 'led-running',
        kind: 'headless',
        status: 'running',
        stdoutLog: outLog,
        stderrLog: errLog,
      },
    ]
    const ledCollect: any = second.api.jobCollect('led-running')
    expect(ledCollect.fromLedger).toBe(true)
    expect(ledCollect.stdoutTail).toBe('run output')
    expect(ledCollect.stderrTail).toBe('run error')
  })

  it('kills job, run and ledger records without throwing on unknown ids', () => {
    const { api, ledger } = makeJobs()
    const outdir = mkTmp('kill-paths')
    const snap: any = api.jobStart({ outdir })
    const child = lastSpawn()
    child.kill = vi.fn(() => {
      throw new Error('already gone')
    })
    expect(api.jobKill(snap.id)).toMatchObject({ status: 'killed' })
    expect(child.kill).toHaveBeenCalled()

    const runChild = {
      kill: vi.fn(() => {
        throw new Error('already gone')
      }),
    }
    ledger.runs.set('run-kill', {
      id: 'run-kill',
      kind: 'headless',
      status: 'running',
      startedAt: Date.now() - 5,
      child: runChild,
    })
    expect(api.jobKill('run-kill')).toMatchObject({ status: 'killed' })
    expect(runChild.kill).toHaveBeenCalled()
    expect(ledger.appended.some((row: any) => row.id === 'run-kill')).toBe(true)

    h.ledgerRows = [{ id: 'led-done', kind: 'headless', status: 'done' }]
    expect(api.jobKill('led-done')).toMatchObject({
      id: 'led-done',
      status: 'done',
      alreadyFinished: true,
      fromLedger: true,
    })
    expect(api.jobKill('nope')).toMatchObject({
      id: 'nope',
      status: 'unknown',
      alreadyFinished: true,
      fromLedger: false,
    })
  })

  it('artifacts skip noise, respect the 40 entry cap and sort by mtime', () => {
    const { api } = makeJobs()
    const outdir = mkTmp('artifacts')
    for (let i = 0; i < 45; i += 1) {
      const p = path.join(outdir, 'file_' + String(i).padStart(2, '0') + '.png')
      fs.writeFileSync(p, 'x')
      fs.utimesSync(p, 1000 + i, 1000 + i)
    }
    fs.writeFileSync(path.join(outdir, '__pycache__'), 'x')
    fs.writeFileSync(path.join(outdir, 'tmp_junk.txt'), 'x')
    fs.writeFileSync(path.join(outdir, 'old.blend1'), 'x')

    const arts: any[] = api.jobArtifacts({ outdir } as any)
    expect(arts).toHaveLength(40)
    expect(arts[0].name).toBe('file_44.png')
    expect(arts.some((a) => a.name.indexOf('tmp') === 0)).toBe(false)
    expect(arts.some((a) => a.name.endsWith('.blend1'))).toBe(false)
  })

  it('start rejects a missing script file and an unknown preload module', () => {
    const outdir = mkTmp('errors')
    const missing = makeJobs({
      readScriptPath: () => {
        throw new Error('ENOENT')
      },
    })
    expect(() => missing.api.jobStart({ outdir, scriptFile: 'C:/nope.py' })).toThrow(
      /读不到脚本文件/,
    )

    const { api } = makeJobs()
    expect(() => api.jobStart({ outdir, preload: ['nope'] })).toThrow(/preload 找不到模块/)
    expect(h.spawnCalls).toHaveLength(0)
  })
})

describe('workers', () => {
  it('starts once with flat protocol args and reports hello plus warmup', async () => {
    const api = makeWorkers()
    const snap: any = await bootWorker(api, {}, 'default')

    expect(h.spawnCalls).toHaveLength(1)
    const child = lastSpawn()
    expect(child.exe).toBe('C:/fake/blender.exe')
    expect(child.args).toEqual(
      expect.arrayContaining([
        '-b',
        '--factory-startup',
        '--python',
        'C:/fake/worker.py',
        '--port',
        '9879',
        '--gpu',
        'auto',
        '--engine',
        'eevee',
        '--frame-dir',
        'C:/fake/tmp',
      ]),
    )
    expect(child.spawnOpts.env.PYTHONIOENCODING).toBe('utf-8')
    expect(snap.ready).toBe(true)
    expect(snap.alive).toBe(true)
    expect(snap.hello).toMatchObject({ ok: true, pid: 777 })
    expect(snap.warmup).toEqual({ ok: true, ms: 5 })
    expect(h.metrics.workerStarts).toBe(1)

    // A second start reuses the live worker instead of spawning again.
    const again: any = await api.workerStart({}, 'default')
    expect(again.ready).toBe(true)
    expect(h.spawnCalls).toHaveLength(1)
  })

  it('exec sends one exec frame, counts metrics and keeps null purgePrefixes out of the payload', async () => {
    const api = makeWorkers()
    await bootWorker(api, { warmup: false }, 'default')

    const running: Promise<any> = api.workerExec('import bpy', 5000, null, 'default')
    await flush()
    const req = await answerSocket({ ok: true, value: [1, 2] })
    const r: any = await running

    expect(req.op).toBe('exec')
    expect(req.code).toBe('import bpy')
    expect(req.purgePrefixes).toBeUndefined()
    expect(r.value).toEqual([1, 2])
    expect(h.metrics.workerExecs).toBe(1)
  })

  it('see/watch/reset/status map to their op codes and expose status payloads', async () => {
    const api = makeWorkers()
    await bootWorker(api, { warmup: false }, 'default')

    let running: Promise<any> = api.workerSee({ width: 64 }, 'default')
    await flush()
    let req = await answerSocket({ ok: true })
    expect(req.op).toBe('see')
    expect(req.spec).toEqual({ width: 64 })
    await running

    running = api.workerWatch({ frame: 2 }, 'default')
    await flush()
    req = await answerSocket({ ok: true })
    expect(req.op).toBe('watch')
    await running

    running = api.workerReset('default')
    await flush()
    req = await answerSocket({ ok: true })
    expect(req.op).toBe('reset')
    await running

    running = api.workerStatus('default')
    await flush()
    req = await answerSocket({ status: { frame: 7 } })
    expect(req.op).toBe('status')
    const st: any = await running
    expect(st.status).toEqual({ frame: 7 })

    // A worker that was never started reports alive=false without a socket.
    const idle: any = await api.workerStatus('never-started')
    expect(idle.alive).toBe(false)
    expect(idle.ready).toBe(false)
  })

  it('stop sends shutdown, waits 300ms and kills an unresponsive child', async () => {
    vi.useFakeTimers()
    const api = makeWorkers()
    await bootWorker(api, { warmup: false }, 'default')
    const child = lastSpawn()

    const stopping: Promise<any> = api.workerStop('default')
    await flush()
    const req = await answerSocket({ bye: true })
    expect(req.op).toBe('shutdown')
    await vi.advanceTimersByTimeAsync(300)
    const r: any = await stopping

    expect(r.stopped).toBe(true)
    expect(r.wasAlive).toBe(true)
    expect(r.bye).toEqual({ id: req.id, bye: true })
    expect(child.kill).toHaveBeenCalledWith('SIGKILL')
    expect(api.workerList()[0].ready).toBe(false)
  })

  it('stop of a worker that was never started is a no-op', async () => {
    const api = makeWorkers()
    const r: any = await api.workerStop('ghost')
    expect(r).toEqual({ name: 'ghost', stopped: false, wasAlive: false })
  })

  it('restart stops the old child and starts a fresh one', async () => {
    vi.useFakeTimers()
    const api = makeWorkers()
    await bootWorker(api, { warmup: false }, 'default')
    const oldChild = lastSpawn()

    const restarting: Promise<any> = api.workerRestart('default', { warmup: false })
    await flush()
    await answerSocket({ bye: true })
    await vi.advanceTimersByTimeAsync(300)
    for (let i = 0; i < 200 && h.spawnCalls.length < 2; i += 1) await Promise.resolve()
    expect(h.spawnCalls).toHaveLength(2)
    const fresh = lastSpawn()
    expect(fresh).not.toBe(oldChild)
    fresh.stdout.write('DSH_WORKER ' + JSON.stringify({ ok: true, pid: 888 }) + NL)
    await flush()
    const snap: any = await restarting
    expect(snap.ready).toBe(true)
    expect(snap.pid).toBe(888)
  })

  it('restarts a crashed worker on the next exec call', async () => {
    const api = makeWorkers()
    await bootWorker(api, { warmup: false }, 'default')
    const crashed = lastSpawn()
    crashed.exitCode = 1
    crashed.emit('close', 1, null)

    const running: Promise<any> = api.workerExec('print(1)', 5000, null, 'default')
    await flush()
    for (let i = 0; i < 200 && h.spawnCalls.length < 2; i += 1) await Promise.resolve()
    expect(h.spawnCalls).toHaveLength(2)
    const fresh = lastSpawn()
    fresh.stdout.write('DSH_WORKER ' + JSON.stringify({ ok: true, pid: 999 }) + NL)
    await flush()
    await answerSocket({ ok: true, ms: 1 })
    await flush()
    const req = await answerSocket({ ok: true, value: 'ok' })
    const r: any = await running
    expect(req.op).toBe('exec')
    expect(r.value).toBe('ok')
    expect(h.metrics.workerStarts).toBe(2)
  })

  it('records connect failures on the worker instead of throwing at list time', async () => {
    const api = makeWorkers()
    await bootWorker(api, { warmup: false }, 'default')

    const running: Promise<any> = api.workerExec('import bpy', 5000, null, 'default')
    await flush()
    const idx = h.socketsAnswered
    const sock = h.sockets[idx]
    h.socketsAnswered += 1
    sock.emit('connect')
    sock.emit(
      'error',
      Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:9879'), { code: 'ECONNREFUSED' }),
    )
    await expect(running).rejects.toThrow(/ECONNREFUSED/)

    const list: any[] = api.workerList()
    expect(list[0].lastError).toContain('ECONNREFUSED')
  })

  it('normalises names, advances ports and honours an explicit port', async () => {
    const api = makeWorkers()
    const a: any = api.workerState('Alpha Worker')
    expect(a.name).toBe('alpha-worker')
    expect(a.port).toBe(9879)
    const b: any = api.workerState('second')
    expect(b.port).toBe(9880)
    const c: any = api.workerState('third')
    expect(c.port).toBe(9881)
    expect(api.workerList()).toHaveLength(3)

    const started: Promise<any> = api.workerStart({ port: 6000, warmup: false }, 'custom')
    const child = lastSpawn()
    expect(child.args).toEqual(expect.arrayContaining(['--port', '6000']))
    child.stdout.write('DSH_WORKER ' + JSON.stringify({ ok: true, pid: 9 }) + NL)
    await flush()
    const snap: any = await started
    expect(snap.port).toBe(6000)
  })

  it('reaps an idle worker after the TTL and clearWorkerReaper stops the timer', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(1000)
    process.env.DSH_WORKER_TTL_MS = '1000'
    const api = makeWorkers()
    await bootWorker(api, { warmup: false }, 'default')
    const child = lastSpawn()
    expect(api.workerList()[0].ready).toBe(true)

    vi.setSystemTime(5000) // idle for 4000ms > TTL
    await vi.advanceTimersByTimeAsync(30000) // one reaper interval
    await flush()
    const req = await answerSocket({ bye: true })
    expect(req.op).toBe('shutdown')
    await vi.advanceTimersByTimeAsync(300)
    await flush()
    expect(child.kill).toHaveBeenCalledWith('SIGKILL')
    expect(api.workerList()[0].ready).toBe(false)

    api.clearWorkerReaper()
    const socketsBefore = h.sockets.length
    await vi.advanceTimersByTimeAsync(30000)
    expect(h.sockets.length).toBe(socketsBefore)
  })

  it('start failures are explicit: spawn error, boot/preload failure, start timeout', async () => {
    const api = makeWorkers()

    const spawnFail: Promise<any> = api.workerStart({ warmup: false }, 'spawn-fail')
    const c1 = lastSpawn()
    c1.emit('error', new Error('spawn ENOENT'))
    await expect(spawnFail).rejects.toThrow(/worker 起不来/)

    const bootFail: Promise<any> = api.workerStart({ warmup: false }, 'boot-fail')
    const c2 = lastSpawn()
    c2.stdout.write(
      'DSH_WORKER ' +
        JSON.stringify({ ok: true, pid: 1, boot: { ok: false, error: 'preload boom' } }) +
        NL,
    )
    await expect(bootFail).rejects.toThrow(/worker boot\/preload 失败/)
    expect(c2.kill).toHaveBeenCalledWith('SIGKILL')

    vi.useFakeTimers()
    const api2 = makeWorkers()
    const timedOut: Promise<any> = api2.workerStart(
      { warmup: false, startTimeoutMs: 20000 },
      'timeout',
    )
    const timedOutAssertion = expect(timedOut).rejects.toThrow(/worker 未就绪/)
    lastSpawn()
    await vi.advanceTimersByTimeAsync(20000)
    await timedOutAssertion
    vi.useRealTimers()
  })
})
