import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// F2.2 tail: createHeadlessRun().headless() was split into stages. These tests
// exercise the stage seams directly (setup/job branch/ledger completion/warnings);
// headless-execution.test.ts keeps the end-to-end createHeadlessRun behavior.

import {
  finishRunRecord,
  headlessJobReceipt,
  prepareRun,
  recordCompletedRun,
} from '../../../runtime/execution/headless/run-stages.js'
import { closeHeadlessLogStreams } from '../../../runtime/execution/headless/execute.js'
import type { HeadlessDeps, HeadlessRunRec } from '../../../runtime/execution/headless/types.js'
import type { RunSetup } from '../../../runtime/execution/headless/run-stages.js'

let tmp = ''
let ledger: Array<Record<string, unknown>> = []
let runs: Map<string, HeadlessRunRec> = new Map()
let deps: HeadlessDeps
let live: RunSetup[] = []

function makeDeps(dir: string): HeadlessDeps {
  const metrics = { headlessRuns: 0, lastHeadlessMs: undefined as number | undefined }
  return {
    HERE: dir,
    LEDGER: path.join(dir, 'ledger.jsonl'),
    SESSION_NAME: 'test-session',
    dumpResult: vi.fn(() => ({ path: path.join(dir, 'result.json'), bytes: 12 })),
    jobStart: vi.fn(() => ({ id: 'job-1', stdoutLog: 'job-out.log', stderrLog: 'job-err.log' })),
    ledgerAppend: (rec: Record<string, unknown>) => ledger.push(rec),
    metrics,
    preloadChunk: (name: string) => '# preload ' + name,
    readScriptPath: (p: unknown) => ({ win: String(p), wsl: String(p) }),
    runs,
  } as unknown as HeadlessDeps
}

async function cleanup(run: RunSetup): Promise<void> {
  try {
    run.stopRunHb()
  } catch {
    /* already stopped */
  }
  await closeHeadlessLogStreams(run.logStreams)
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-headless-stages-'))
  ledger = []
  runs = new Map()
  live = []
  deps = makeDeps(tmp)
})

afterEach(async () => {
  for (const run of live) await cleanup(run)
  fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 50, retryDelay: 10 })
})

describe('prepareRun stage', () => {
  it('builds script/options/log streams and registers the running record', () => {
    const run = prepareRun({ script: 'print("hello")', outdir: tmp, runId: 'run-stage-1' }, deps)
    live.push(run)
    expect(run.t0).toBeGreaterThan(0)
    expect(run.p.runId).toBe('run-stage-1')
    expect(run.built.args).toContain('--python')
    expect(run.logDirWsl).toBe(run.p.outdirWsl)
    expect(run.logStreams.logs).not.toBeNull()
    expect(String(run.logStreams.logs?.stdout)).toContain(tmp.replace(/\\/g, path.sep))
    expect(deps.metrics.headlessRuns).toBe(1)
    expect(runs.get('run-stage-1')).toBe(run.runRec)
    expect(run.runRec.status).toBe('running')
    expect(run.runRec.id).toBe('run-stage-1')
    expect(ledger).toHaveLength(1)
    expect(ledger[0]).toMatchObject({
      id: 'run-stage-1',
      kind: 'headless',
      status: 'running',
      stage: null,
    })
  })
})

describe('headlessJobReceipt stage', () => {
  it('returns null and keeps the run in synchronous mode', async () => {
    const run = prepareRun({ script: 'print(1)', outdir: tmp, runId: 'run-sync' }, deps)
    live.push(run)
    const job = await headlessJobReceipt({
      opts: { script: 'print(1)' },
      p: run.p,
      t0: run.t0,
      logStreams: run.logStreams,
      runs,
      jobStart: deps.jobStart,
    })
    expect(job).toBeNull()
    expect(runs.has('run-sync')).toBe(true)
    expect(deps.jobStart).not.toHaveBeenCalled()
  })

  it('deletes the fake running record and returns the job receipt in job mode', async () => {
    const run = prepareRun({ script: 'print(1)', outdir: tmp, asJob: true }, deps)
    expect(runs.has(run.p.runId)).toBe(true)
    const job = (await headlessJobReceipt({
      opts: { asJob: true },
      p: run.p,
      t0: run.t0,
      logStreams: run.logStreams,
      runs,
      jobStart: deps.jobStart,
    })) as Record<string, unknown>
    expect(job).toMatchObject({ ok: true, mode: 'job', kind: 'job', jobId: 'job-1' })
    expect(runs.has(run.p.runId)).toBe(false)
    const jobArgs = vi.mocked(deps.jobStart).mock.calls[0][0] as Record<string, unknown>
    expect(jobArgs.runId).toBe(run.p.runId)
    expect(jobArgs.timeoutMs).toBeGreaterThanOrEqual(3600000)
    // Streams were closed by the stage; the heartbeat still needs stopping.
    try {
      run.stopRunHb()
    } catch {
      /* already stopped */
    }
  })
})

function fakeRunRec(): HeadlessRunRec {
  return {
    id: 'run-done',
    child: null,
    pid: 7,
    startedAt: 1000,
    status: 'running',
    outdir: 'out',
    outdirWsl: 'out',
    logs: null,
    script: 'script.py',
    scriptStaging: null,
    exitCode: null,
    timedOut: false,
    artifacts: [],
    expect: null,
    workdir: null,
    stage: 'render',
    stageAt: 1200,
    stageName: 'render',
    lastOutputAt: 1001,
    lines: 0,
    result: null,
    resultPath: null,
    resultBytes: 0,
    resultTruncated: false,
    pathWarnings: [],
  } as unknown as HeadlessRunRec
}

describe('recordCompletedRun stage', () => {
  const baseInput = () => ({
    p: { runId: 'run-done', inputFileInfo: null, fileOpt: undefined } as any,
    res: { so: '', se: '', exitCode: 0, signal: null, spawnErr: null, timedOut: false },
    failureClass: 'finished' as const,
    artifacts: {
      artifacts: [
        { name: 'a.png', bytes: 1, mtimeMs: 1, fresh: true },
        { name: 'b.png', bytes: 1, mtimeMs: 1, fresh: true },
      ],
      noiseFiltered: 0,
    },
    logs: null,
    expectEval: null,
    parsed: { ok: true },
    parsedOut: { parsed: { ok: true }, parseError: null, shotsRes: null, gpu: null },
    outJsonInfo: { path: 'res.json', bytes: 8, wsl: null },
    resultTruncated: false,
    pathWarnings: [],
    shotsSummary: null,
    ledgerAppend: (rec: Record<string, unknown>) => ledger.push(rec),
  })

  it('writes the terminal state and appends the done ledger row', () => {
    const runRec = fakeRunRec()
    recordCompletedRun({ ...(baseInput() as any), runRec })
    expect(runRec.status).toBe('done')
    expect(runRec.failureClass).toBe('finished')
    expect(runRec.exitCode).toBe(0)
    expect(runRec.timedOut).toBe(false)
    expect(runRec.artifacts).toEqual(['a.png', 'b.png'])
    expect(runRec.resultPath).toBe('res.json')
    expect(runRec.resultBytes).toBe(8)
    expect(runRec.ms).toBeGreaterThanOrEqual(0)
    expect(ledger).toHaveLength(1)
    expect(ledger[0]).toMatchObject({
      id: 'run-done',
      kind: 'headless',
      status: 'done',
      exitCode: 0,
      stage: 'render',
      stageMs: 200,
      artifacts: ['a.png', 'b.png'],
      expectOk: null,
    })
  })

  it('records killed/timeout and ignore ledger failures', () => {
    const runRec = fakeRunRec()
    recordCompletedRun({
      ...(baseInput() as any),
      runRec,
      res: { so: '', se: '', exitCode: null, signal: 'SIGKILL', spawnErr: null, timedOut: true },
      ledgerAppend: () => {
        throw new Error('ledger down')
      },
    })
    expect(runRec.status).toBe('killed')
    expect(runRec.timedOut).toBe(true)
    expect(runRec.signal).toBe('SIGKILL')
  })
})

describe('finishRunRecord stage', () => {
  it('stops the heartbeat and merges warnings in the historical order', () => {
    const runRec = fakeRunRec()
    const stop = vi.fn()
    finishRunRecord({
      runRec,
      stopRunHb: stop,
      pathWarnings: [{ code: 'PATH_WARN' }],
      stdout: '',
      stderr: '',
      fileOpt: undefined,
      fileLooksPython: true,
    })
    expect(stop).toHaveBeenCalledTimes(1)
    const warnings = runRec.warnings as unknown[]
    expect(warnings[0]).toMatchObject({ code: 'PATH_WARN' })
    expect(warnings.length).toBeGreaterThan(1)
  })

  it('never throws when the heartbeat stop fails', () => {
    const runRec = fakeRunRec()
    expect(() =>
      finishRunRecord({
        runRec,
        stopRunHb: () => {
          throw new Error('already stopped')
        },
        pathWarnings: [],
        stdout: '',
        stderr: '',
        fileOpt: 'scene.blend',
        fileLooksPython: false,
      }),
    ).not.toThrow()
  })
})
