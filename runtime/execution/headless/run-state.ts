import { startHeartbeat } from '../../core/progress.js'
import { workdirStagingWarning } from '../../proc_env.js'
import type { BuiltHeadlessScript } from './script.js'
import type { HeadlessDeps, HeadlessRunRec, PreparedHeadless } from './types.js'

export interface RunState {
  runRec: HeadlessRunRec
  stopRunHb: () => void
}

/** Create and register the in-memory run record, then append the running ledger row. */
export function createRunState(input: {
  p: PreparedHeadless
  built: BuiltHeadlessScript
  t0: number
  logDirWsl: string
  logs: HeadlessRunRec['logs']
  runs: HeadlessDeps['runs']
  ledgerAppend: HeadlessDeps['ledgerAppend']
}): RunState {
  const { p, built, t0, logs } = input
  const runRec: HeadlessRunRec = {
    id: p.runId,
    child: null,
    pid: null,
    startedAt: t0,
    status: 'running',
    outdir: p.outdirWin,
    outdirWsl: p.outdirWsl,
    logs: logs,
    script: built.sp ? built.sp.win : null,
    scriptStaging: built.sp ? built.sp.staging || null : null,
    exitCode: null,
    timedOut: false,
    artifacts: [],
    expect: p.exp,
    workdir: p.opts.workdir || null,
    stage: null,
    stageAt: null,
    stageName: null,
    lastOutputAt: t0,
    lines: 0,
    result: null,
    resultPath: null,
    resultBytes: 0,
    resultTruncated: false,
    pathWarnings: [],
  }
  if (runRec.scriptStaging && runRec.scriptStaging.substituted) {
    const w = workdirStagingWarning(runRec.scriptStaging)
    if (w) runRec.pathWarnings.push(w)
  }
  input.runs.set(p.runId, runRec)
  const stopRunHb = startHeartbeat(runRec, input.logDirWsl)
  if (input.runs.size > 20) {
    const k = input.runs.keys().next().value
    if (k !== undefined && k !== p.runId) input.runs.delete(k)
  }
  input.ledgerAppend({
    id: p.runId,
    kind: 'headless',
    status: 'running',
    startedAt: t0,
    script: runRec.script,
    outdir: runRec.outdir,
    outdirWsl: p.outdirWsl,
    timeoutMs: p.timeoutMs,
    stage: null,
    file: p.fileOpt === undefined ? null : String(p.fileOpt), // F-09：台账记 file，事后可区分"没给档"与"给了没开"
    stdoutLog: logs ? logs.stdout : null,
    stderrLog: logs ? logs.stderr : null,
  })
  return { runRec, stopRunHb }
}
