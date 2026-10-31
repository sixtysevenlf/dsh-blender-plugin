import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const H = vi.hoisted(() => ({
  spawn: vi.fn(),
  script: {
    wsl: 'C:\\tmp\\dsh_headless_test.py',
    win: 'C:\\tmp\\dsh_headless_test.py',
    staging: null as null | Record<string, unknown>,
  },
  captured: '',
}))

vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>()
  return { ...actual, spawn: H.spawn }
})

vi.mock('../../../runtime/staging.js', () => ({
  writeHeadlessScript: (code: string) => {
    H.captured = code
    return { ...H.script, wsl: H.script.wsl, win: H.script.win, staging: H.script.staging }
  },
}))

import { createHeadlessRun } from '../../../runtime/execution/headless.js'

class FakeChild extends EventEmitter {
  pid = 4242
  stdout = new EventEmitter()
  stderr = new EventEmitter()
  killed: NodeJS.Signals | null = null

  kill(signal: NodeJS.Signals = 'SIGKILL'): boolean {
    this.killed = signal
    queueMicrotask(() => this.emit('close', null, signal))
    return true
  }
}

interface Harness {
  deps: Parameters<typeof createHeadlessRun>[0]
  ledger: Array<Record<string, unknown>>
  metrics: { headlessRuns: number; lastHeadlessMs?: number }
  runs: Map<string, Record<string, unknown>>
}

function makeHarness(tmp: string): Harness {
  const ledger: Array<Record<string, unknown>> = []
  const runs = new Map<string, Record<string, unknown>>()
  const metrics: { headlessRuns: number; lastHeadlessMs?: number } = { headlessRuns: 0 }
  const deps = {
    HERE: tmp,
    LEDGER: path.join(tmp, 'ledger.jsonl'),
    SESSION_NAME: 'test-session',
    dumpResult: vi.fn(() => ({ path: path.join(tmp, 'result.json'), bytes: 12 })),
    jobStart: vi.fn(() => ({ id: 'job-1', stdoutLog: 'job-out.log', stderrLog: 'job-err.log' })),
    ledgerAppend: (rec: Record<string, unknown>) => ledger.push(rec),
    metrics,
    preloadChunk: (name: string) => '# preload ' + name,
    readScriptPath: (p: unknown) => ({ win: String(p), wsl: String(p) }),
    runs,
  } as unknown as Parameters<typeof createHeadlessRun>[0]
  return { deps, ledger, metrics, runs }
}

function lastChild(): FakeChild {
  const result = H.spawn.mock.results[H.spawn.mock.results.length - 1]
  if (!result || result.type !== 'return') throw new Error('spawn was not called')
  return result.value as FakeChild
}

function spawnChild(): FakeChild {
  const child = new FakeChild()
  H.spawn.mockImplementation(() => child)
  return child
}

async function runHeadless(
  h: Harness,
  opts: Record<string, unknown> = {},
  opts2: {
    stdout?: string
    stderr?: string
    exitCode?: number | null
    signal?: NodeJS.Signals | null
  } = {},
): Promise<Record<string, unknown>> {
  const p = createHeadlessRun(h.deps).headless({
    script: 'print("hello")',
    outdir: opts.outdir as string | undefined,
    ...opts,
  })
  const child = lastChild()
  if (opts2.stdout) child.stdout.emit('data', Buffer.from(opts2.stdout))
  if (opts2.stderr) child.stderr.emit('data', Buffer.from(opts2.stderr))
  child.emit('close', opts2.exitCode === undefined ? 0 : opts2.exitCode, opts2.signal ?? null)
  return (await p) as Record<string, unknown>
}

let tmp = ''

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-headless-'))
  H.spawn.mockReset()
  H.captured = ''
  H.script = {
    wsl: 'C:\\tmp\\dsh_headless_test.py',
    win: 'C:\\tmp\\dsh_headless_test.py',
    staging: null,
  }
})

afterEach(async () => {
  vi.useRealTimers()
  // Streams created by the run close asynchronously; let them release their handles
  // before removing the temporary directory, otherwise Windows reports ENOENT.
  await new Promise((resolve) => setTimeout(resolve, 80))
  try {
    fs.rmSync(tmp, { recursive: true, force: true })
  } catch {
    /* best effort */
  }
})

describe('headless run: success and process contract', () => {
  it('returns a successful receipt and records the run', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(
      h,
      { runId: 'run-test-1' },
      { stdout: 'HEADLESS {"ok":true,"n":1}\n' },
    )

    expect(r.ok).toBe(true)
    expect(r.status).toBe('finished')
    expect(r.runId).toBe('run-test-1')
    expect(r.resultJson).toEqual({ ok: true, n: 1 })
    expect(r.exitCode).toBe(0)
    expect(h.metrics.headlessRuns).toBe(1)
    expect(h.metrics.lastHeadlessMs).toBeGreaterThanOrEqual(0)
    expect(h.ledger.map((x) => x.status)).toEqual(['running', 'done'])
    expect(h.runs.get('run-test-1')?.status).toBe('done')
  })

  it('builds the script and spawn arguments from options', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const workdir = path.join(tmp, 'work')
    fs.mkdirSync(workdir)
    await runHeadless(h, { factoryStartup: false, outdir: tmp, args: ['--a', 'b'], workdir })

    const args = H.spawn.mock.calls[0][1] as string[]
    expect(args[0]).toBe('-b')
    expect(args).not.toContain('--factory-startup')
    expect(args).toContain('--python')
    expect(args).toContain(H.script.win)
    expect(args.slice(args.indexOf('--'))).toEqual(['--', tmp, '--a', 'b'])
    const code = H.captured
    expect(code).toContain('print("hello")')
    expect(code).toContain('_dsh_stage_emit("preload-done")')
    expect(code).toContain('_dsh_stage_emit("done")')
    expect(code).toContain('os._exit(0)')
    expect(code).toContain('DSH_RUN_ID')
    expect(code).toContain(JSON.stringify(workdir).slice(1, -1))
  })

  it('injects preloads, auto-includes qc_render for shots, and reports engine/shot receipts', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(
      h,
      { preload: ['view'], shots: { views: [{ view: 'front' }] }, engine: 'cycles', gpu: 'true' },
      {
        stdout:
          'DSH_STAGE {"name":"render"}\n' +
          'DSH_PROGRESS {"name":"render","frame":3}\n' +
          'DSH_GPU {"ok":true,"mode":"cycles","after":{"engine":"CYCLES"}}\n' +
          'DSH_SHOTS {"ok":true,"views":[{"view":"front","path":"front.png","ms":12,"coverage":0.5}]}\n' +
          'HEADLESS {"ok":true}\n',
      },
    )

    expect(r.ok).toBe(true)
    expect(r.stageName).toBe('render')
    expect((r.progressHistory as unknown[]).length).toBe(1)
    expect(r.engine).toBe('CYCLES')
    expect(r.shots).toMatchObject({ ok: true, count: 1 })
    expect(H.captured).toContain('# preload view')
    expect(H.captured).toContain('# preload qc_render')
    expect(H.captured).toContain('DSH engine prelude')
    expect(H.captured).toContain('True')
  })

  it('returns the shots payload when stdout has only DSH_SHOTS', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(
      h,
      { shots: { views: [{ view: 'front' }] } },
      {
        stdout:
          'DSH_SHOTS {"ok":true,"views":[{"view":"front","path":"front.png","ms":12,"coverage":0.5}]}\n',
      },
    )
    expect(r.ok).toBe(true)
    expect(r.resultJson).toMatchObject({ ok: true, count: 1 })
  })

  it('writes the structured result to outJson when requested', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const out = path.join(tmp, 'explicit-result.json')
    const r = await runHeadless(
      h,
      { outJson: out },
      { stdout: 'HEADLESS {"ok":true,"tag":"explicit"}\n' },
    )
    expect(r.ok).toBe(true)
    expect(fs.readFileSync(out, 'utf8')).toContain('explicit')
  })

  it('fails when expect is not met even if the process exits 0', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(
      h,
      { expect: { minNewObjects: 2 } },
      {
        stdout:
          'DSH_EXPECT {"before":{"objects":0},"after":{"objects":1},"delta":{"objects":1}}\n' +
          'HEADLESS {"ok":true}\n',
      },
    )
    expect(r.ok).toBe(false)
    expect(r.expect).toMatchObject({ ok: false })
    expect((r.expect as { warnings: string[] }).warnings.length).toBeGreaterThan(0)
  })

  it('passes expect when the observed delta meets the minimum', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(
      h,
      { expect: { min_new_objects: 2 } },
      {
        stdout:
          'DSH_EXPECT {"before":{"objects":0},"after":{"objects":2},"delta":{"objects":2}}\n' +
          'HEADLESS {"ok":true}\n',
      },
    )
    expect(r.ok).toBe(true)
    expect(r.expect).toMatchObject({ ok: true })
  })
})

describe('headless run: failure modes', () => {
  it('reports a spawn error as blender_exe_missing', async () => {
    H.spawn.mockImplementation(() => {
      throw new Error('no blender here')
    })
    const h = makeHarness(tmp)
    const p = createHeadlessRun(h.deps).headless({ script: 'print(1)', outdir: tmp })
    await expect(p).rejects.toMatchObject({ code: 'blender-exe-missing' })
  })

  it('reports a non-zero exit code', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(h, {}, { exitCode: 3, stderr: 'boom\n' })
    expect(r.ok).toBe(false)
    expect(r.status).toBe('blender_error')
    expect(r.reason).toBe('exit code 3')
    expect(r.stderrTail).toContain('boom')
  })

  it('reports a timeout and kills the child', async () => {
    vi.useFakeTimers()
    const child = spawnChild()
    const h = makeHarness(tmp)
    const p = createHeadlessRun(h.deps).headless({
      script: 'print(1)',
      outdir: tmp,
      timeoutMs: 1000,
    })
    await vi.advanceTimersByTimeAsync(1000)
    expect(child.killed).toBe('SIGKILL')
    const r = (await p) as Record<string, unknown>
    expect(r.ok).toBe(false)
    expect(r.status).toBe('timeout')
    expect(r.timedOut).toBe(true)
    expect(String(r.reason)).toContain('timeout')
  })

  it('keeps raw stdout when the result line is not valid JSON', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(h, {}, { stdout: 'HEADLESS not-json\n' })
    expect(r.resultJson).toBeNull()
    expect(r.resultParseError).toMatchObject({ message: expect.any(String) })
    expect(r.stdoutTail).toContain('HEADLESS not-json')
  })

  it('reports gpu_required_missing when a required GPU probe fails', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(
      h,
      { engine: 'cycles', gpu: 'true' },
      { stdout: 'DSH_GPU {"ok":false,"error":"no device"}\nHEADLESS {"ok":true}\n' },
    )
    expect(r.ok).toBe(false)
    expect(r.status).toBe('gpu_required_missing')
  })

  it('converts to the job layer when asJob is set without spawning', async () => {
    const h = makeHarness(tmp)
    const r = (await createHeadlessRun(h.deps).headless({
      script: 'print(1)',
      outdir: tmp,
      asJob: true,
    })) as Record<string, unknown>
    expect(r.mode).toBe('job')
    expect(r.jobId).toBe('job-1')
    expect(H.spawn).not.toHaveBeenCalled()
    expect(h.deps.jobStart).toHaveBeenCalledTimes(1)
    expect(h.ledger.map((x) => x.status)).toEqual(['running'])
  })

  it('converts to the job layer when the timeout crosses autoJobMs', async () => {
    const h = makeHarness(tmp)
    const r = (await createHeadlessRun(h.deps).headless({
      script: 'print(1)',
      outdir: tmp,
      timeoutMs: 1000,
      autoJobMs: 500,
    })) as Record<string, unknown>
    expect(r.mode).toBe('job')
    expect(H.spawn).not.toHaveBeenCalled()
  })
})

describe('headless run: artifacts, logs, input validation', () => {
  it('lists fresh artifacts and filters noise', async () => {
    spawnChild()
    fs.writeFileSync(path.join(tmp, 'a.png'), Buffer.from('png'))
    fs.writeFileSync(path.join(tmp, 'b.txt'), Buffer.from('txt'))
    fs.writeFileSync(path.join(tmp, 'skip.pyc'), Buffer.from('x'))
    const h = makeHarness(tmp)
    const r = await runHeadless(h, { outdir: tmp })
    const artifacts = r.artifacts as Array<{ name: string }>
    expect(artifacts.map((a) => a.name).sort()).toEqual(['a.png', 'b.txt'])
    expect(r.noiseFiltered).toBe(1)
  })

  it('writes stdout and stderr logs next to the run', async () => {
    spawnChild()
    const h = makeHarness(tmp)
    const r = await runHeadless(h, { outdir: tmp }, { stdout: 'out-line\n', stderr: 'err-line\n' })
    const logs = r.logs as { stdout: string; stderr: string }
    await vi.waitFor(() => expect(fs.existsSync(logs.stdout)).toBe(true))
    await vi.waitFor(() => expect(fs.existsSync(logs.stderr)).toBe(true))
    expect(fs.readFileSync(logs.stdout, 'utf8')).toContain('out-line')
    expect(fs.readFileSync(logs.stderr, 'utf8')).toContain('err-line')
  })

  it('rejects an empty file option before spawning', async () => {
    const h = makeHarness(tmp)
    await expect(createHeadlessRun(h.deps).headless({ file: '' })).rejects.toMatchObject({
      code: 'file-empty',
    })
    expect(H.spawn).not.toHaveBeenCalled()
  })

  it('reads a python script file and records the script file info', async () => {
    spawnChild()
    const scriptPath = path.join(tmp, 'input.py')
    fs.writeFileSync(scriptPath, 'print("from-file")\n')
    const h = makeHarness(tmp)
    const r = await runHeadless(h, { scriptFile: scriptPath })
    expect(r.scriptFile).toMatchObject({ given: scriptPath })
    expect((r.scriptFile as { bytes: number }).bytes).toBeGreaterThan(0)
    expect(H.captured).toContain('print("from-file")')
  })

  it('throws for a missing preload module', async () => {
    H.spawn.mockImplementation(() => {
      throw new Error('should not spawn')
    })
    const h = makeHarness(tmp)
    await expect(
      createHeadlessRun(h.deps).headless({ script: 'print(1)', preload: ['definitely_missing'] }),
    ).rejects.toThrow(/未知的 runtime Python 模块/)
  })
})
