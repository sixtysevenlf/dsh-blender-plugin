// Headless run stages: job hand-off, process result collection, ledger
// completion and receipt assembly. Extracted verbatim from createHeadlessRun().headless()
// with behavior frozen; index.ts only sequences these stages.
import { winToWsl } from '../../config.js'
import { PLUGIN_VERSION, pathAudit, sceneHygieneAudit } from '../../core/evidence.js'
import { BLENDER_EXE, WIN_TMP } from '../../paths.js'
import { buildChildEnv } from './child-env.js'
import { buildHeadlessScript } from './script.js'
import { createHeadlessLogStreams } from './process.js'
import { prepareHeadlessOptions } from './options/index.js'
import { createRunState } from './run-state.js'
import { closeHeadlessLogStreams, executeHeadless } from './execute.js'
import { isJobMode, jobModeReceipt } from './job-mode.js'
import { writeFallbackLogs } from './logs.js'
import { buildHeadlessReceipt } from './receipt.js'
import {
  applyShotsResult,
  classifyFailure,
  collectArtifacts,
  evaluateExpect,
  extractFailureContext,
  failureHint,
  gpuAfterEngine,
  parseHeadlessStdout,
  finishRunWarnings,
  summarizeShots,
  withWorkdirWarning,
  writeResultJson,
} from './result.js'
import type {
  ArtifactCollection,
  ExpectEval,
  FailureClass,
  FailureContext,
  OutJsonInfo,
  ParsedHeadlessStdout,
  ShotsSummary,
} from './result.js'
import type { BuiltHeadlessScript } from './script.js'
import type { HeadlessLogStreams } from './process.js'
import type {
  HeadlessDeps,
  HeadlessOpts,
  HeadlessRunRec,
  PreparedHeadless,
  SpawnResult,
} from './types.js'

export interface RunSetup {
  t0: number
  p: PreparedHeadless
  built: BuiltHeadlessScript
  logDirWsl: string
  logStreams: HeadlessLogStreams
  runRec: HeadlessRunRec
  stopRunHb: () => void
}

/** Prepare options/script/log streams and register the running record (metrics + ledger). */
export function prepareRun(opts: HeadlessOpts, deps: HeadlessDeps): RunSetup {
  const t0 = Date.now()
  const p = prepareHeadlessOptions(opts, deps)
  const built = buildHeadlessScript(p)
  deps.metrics.headlessRuns++
  // ---- v0.9.3：日志**边跑边写** + 运行台账登记（id 与作业同空间：op=status/collect id=run-…）
  const logDirWsl = p.outdirWsl || winToWsl(WIN_TMP)
  const logStreams = createHeadlessLogStreams(logDirWsl, t0, p.runId)
  const { runRec, stopRunHb } = createRunState({
    p,
    built,
    t0,
    logDirWsl,
    logs: logStreams.logs,
    runs: deps.runs,
    ledgerAppend: deps.ledgerAppend,
  })
  return { t0, p, built, logDirWsl, logStreams, runRec, stopRunHb }
}

export interface HeadlessOutcome {
  childEnv: Record<string, string | undefined>
  res: SpawnResult
  stdout: string
  stderr: string
  ms: number
  logsClosed: Promise<void>
  parsedOut: ParsedHeadlessStdout
  parsed: unknown
  pathWarnings: unknown[]
  logs: HeadlessRunRec['logs']
  failure: FailureContext
  artifacts: ArtifactCollection
  gpuFailed: boolean
  expectEval: ExpectEval | null
  runOk: boolean
  failureClass: FailureClass
  failureHint: string | null
  shotsSummary: ShotsSummary | null
  outJsonInfo: OutJsonInfo
  resultTruncated: boolean
  built: BuiltHeadlessScript
}

export interface JobModeInput {
  opts: HeadlessOpts
  p: PreparedHeadless
  t0: number
  logStreams: HeadlessLogStreams
  runs: HeadlessDeps['runs']
  jobStart: HeadlessDeps['jobStart']
}

/** Long-task hand-off: delete the fake running record and return the job receipt (null when synchronous). */
export async function headlessJobReceipt(
  input: JobModeInput,
): Promise<Record<string, unknown> | null> {
  const { opts, p, t0, logStreams, runs, jobStart } = input
  if (!isJobMode(opts, p)) return null
  runs.delete(p.runId) // 转作业后这次 run 不存在了，别留一个假 running
  try {
    const j = jobStart(
      Object.assign({}, opts, { timeoutMs: Math.max(p.timeoutMs, 3600000), runId: p.runId }),
    )
    return jobModeReceipt({ opts, p, t0, job: j })
  } finally {
    await closeHeadlessLogStreams(logStreams)
  }
}

export interface CollectInput {
  p: PreparedHeadless
  built: BuiltHeadlessScript
  opts: HeadlessOpts
  runRec: HeadlessRunRec
  logStreams: HeadlessLogStreams
  logDirWsl: string
  t0: number
  sessionName: string
  metrics: HeadlessDeps['metrics']
  dumpResult: HeadlessDeps['dumpResult']
  readScriptPath: HeadlessDeps['readScriptPath']
}

/** Spawn, parse, classify and collect artifacts — the synchronous body of the run. */
export async function collectHeadlessOutcome(input: CollectInput): Promise<HeadlessOutcome> {
  const { p, built, opts, runRec, logStreams, logDirWsl, t0, sessionName, metrics } = input
  // ---- 子进程环境：可选透传用户 Blender 配置（GPU 偏好在里面）
  const childEnv = buildChildEnv(p, opts, sessionName)
  const { res, stdout, stderr, ms, logsClosed } = await executeHeadless({
    p,
    built,
    childEnv,
    runRec,
    logStreams,
    t0,
  })
  metrics.lastHeadlessMs = ms
  const parsedOut = parseHeadlessStdout(stdout)
  let parsed = parsedOut.parsed
  // ---- v0.9.3：路径静默改写体检
  const pathWarnings: unknown[] = pathAudit(stdout, stderr)
  // ---- v0.9.6：workDir 不共享 → 脚本改落别处，这件事必须出现在回执里（不许静默）
  withWorkdirWarning(pathWarnings, built.sp ? built.sp.staging : null)
  // ---- 日志兜底：正常路径已在 push() 里边跑边写；流没建起来时这里补一次全量
  let logs = logStreams.logs
  if (!logStreams.soStream) {
    logs = writeFallbackLogs(logDirWsl, logStreams.stamp, p.runId, stdout, stderr)
  }
  runRec.logs = logs
  const failure = extractFailureContext(stderr, p.script)
  const artifacts = collectArtifacts(p.outdirWsl, t0, opts.includeNoise)
  const gpuFailed = p.gpuManual && parsedOut.gpu && parsedOut.gpu.ok === false
  const expectEval = evaluateExpect(p.exp, stdout)
  const runOk = !res.timedOut && res.exitCode === 0 && !gpuFailed && (!expectEval || expectEval.ok)
  const failureClass = classifyFailure(res, !!gpuFailed, failure.lastException)
  const FAIL_HINT = failureHint(failureClass)
  // ---- v0.9.3：shots 行 + 取景自诊断（主体占画面 <5% → warning）
  const shotsSummary = parsedOut.shotsRes ? summarizeShots(parsedOut.shotsRes) : null
  parsed = applyShotsResult(parsed, shotsSummary)
  const outJsonInfo = writeResultJson(
    parsed,
    opts,
    { dumpResult: input.dumpResult, readScriptPath: input.readScriptPath },
    p.runId,
  )
  // ---- v0.8.10：台账终态
  // v0.9.3：结构化回执的"真值"落在 runRec 上 —— op=collect/status 与同步回执同一份数据
  const resultTruncated = !!(parsed !== null && outJsonInfo.path && outJsonInfo.bytes > 4000)
  return {
    childEnv,
    res,
    stdout,
    stderr,
    ms,
    logsClosed,
    parsedOut,
    parsed,
    pathWarnings,
    logs,
    failure,
    artifacts,
    gpuFailed: !!gpuFailed,
    expectEval,
    runOk,
    failureClass,
    failureHint: FAIL_HINT,
    shotsSummary,
    outJsonInfo,
    resultTruncated,
    built: built,
  }
}

export interface RecordInput {
  p: PreparedHeadless
  runRec: HeadlessRunRec
  res: SpawnResult
  failureClass: FailureClass
  artifacts: ArtifactCollection
  logs: HeadlessRunRec['logs']
  expectEval: ExpectEval | null
  parsed: unknown
  parsedOut: ParsedHeadlessStdout
  outJsonInfo: OutJsonInfo
  resultTruncated: boolean
  pathWarnings: unknown[]
  shotsSummary: ShotsSummary | null
  ledgerAppend: HeadlessDeps['ledgerAppend']
}

/** Write the terminal state on runRec and append the done ledger row (failures ignored). */
export function recordCompletedRun(input: RecordInput): void {
  const { p, runRec, res, failureClass, artifacts, logs, expectEval, parsed, parsedOut } = input
  const { outJsonInfo, resultTruncated, pathWarnings, shotsSummary, ledgerAppend } = input
  try {
    runRec.status = res.timedOut ? 'killed' : res.exitCode === 0 ? 'done' : 'failed'
    runRec.failureClass = failureClass
    runRec.exitCode = res.exitCode
    runRec.signal = res.signal
    runRec.timedOut = !!res.timedOut
    runRec.finishedAt = Date.now()
    runRec.ms = Number(runRec.finishedAt) - runRec.startedAt
    runRec.artifacts = artifacts.artifacts.slice(0, 20).map((a) => a.name)
    runRec.logs = logs
    runRec.expect = expectEval
    runRec.result = parsed
    runRec.parseError = parsedOut.parseError
    runRec.resultPath = outJsonInfo.path
    runRec.resultPathWsl = outJsonInfo.wsl || null
    runRec.resultBytes = outJsonInfo.bytes
    runRec.resultTruncated = resultTruncated
    runRec.pathWarnings = pathWarnings
    runRec.shots = shotsSummary
    runRec.inputFile = p.inputFileInfo
    ledgerAppend({
      id: p.runId,
      kind: 'headless',
      status: runRec.status,
      exitCode: res.exitCode,
      ms: runRec.ms,
      outdir: runRec.outdir,
      script: runRec.script,
      pid: runRec.pid,
      file: p.fileOpt === undefined ? null : String(p.fileOpt),
      inputFile: p.inputFileInfo, // F-09
      stage: runRec.stageName || null,
      resultPath: outJsonInfo.path,
      stageMs: typeof runRec.stageAt === 'number' ? runRec.stageAt - runRec.startedAt : null,
      artifacts: runRec.artifacts.slice(0, 8),
      expectOk: expectEval ? expectEval.ok : null,
    })
  } catch (e) {
    /* ignore */
  }
}

export interface FinishInput {
  runRec: HeadlessRunRec
  stopRunHb: (() => void) | null
  pathWarnings: unknown[]
  stdout: string
  stderr: string
  fileOpt: string | undefined
  fileLooksPython: boolean
}

/** Stop the heartbeat and merge path/hygiene/no-input warnings (same order as before). */
export function finishRunRecord(input: FinishInput): void {
  const { runRec, stopRunHb, pathWarnings, stdout, stderr, fileOpt, fileLooksPython } = input
  try {
    if (stopRunHb) stopRunHb()
  } catch (e) {}
  runRec.warnings = finishRunWarnings(
    pathWarnings,
    sceneHygieneAudit(stdout, stderr),
    fileOpt,
    fileLooksPython,
  )
}

export interface ReceiptInput {
  outcome: HeadlessOutcome
  p: PreparedHeadless
  opts: HeadlessOpts
  runRec: HeadlessRunRec
  sessionName: string
  ledgerWin: string
}

/** Assemble the public receipt from the collected outcome (same fields/order as before). */
export function buildRunReceipt(input: ReceiptInput): Record<string, unknown> {
  const { outcome, p, opts, runRec, sessionName, ledgerWin } = input
  return buildHeadlessReceipt({
    ok: outcome.runOk,
    runId: p.runId,
    session: sessionName,
    pluginVersion: PLUGIN_VERSION,
    expect: outcome.expectEval,
    runRec: runRec,
    stdout: outcome.stdout,
    stderr: outcome.stderr,
    status: outcome.failureClass,
    failureHint: outcome.failureHint,
    ledgerWin: ledgerWin,
    scriptFile: p.scriptFileInfo,
    inputFile: p.inputFileInfo,
    outJson: outcome.outJsonInfo,
    result: outcome.parsed,
    parseError: outcome.parsedOut.parseError,
    shots: outcome.shotsSummary,
    pathWarnings: outcome.pathWarnings,
    ms: outcome.ms,
    timeoutMs: p.timeoutMs,
    outdirWin: p.outdirWin,
    outdirWsl: p.outdirWsl,
    childEnvKeys: Object.keys(outcome.childEnv).filter((k) => k.indexOf('DSH_') === 0),
    res: outcome.res,
    logs: outcome.logs,
    failure: outcome.failure,
    artifacts: outcome.artifacts,
    noiseFiltered: outcome.artifacts.noiseFiltered,
    blenderExe: BLENDER_EXE,
    built: outcome.built,
    preload: p.mods.length ? p.mods : undefined,
    gpu: outcome.parsedOut.gpu,
    engine: gpuAfterEngine(outcome.parsedOut.gpu),
    engineMode: (outcome.parsedOut.gpu && outcome.parsedOut.gpu.mode) || null,
    useUserConfig: !!opts.useUserConfig,
    resultTruncated: outcome.resultTruncated,
  })
}
