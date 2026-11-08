import { clipMiddle } from '../../core/output/index.js'
import type {
  ArtifactCollection,
  ExpectEval,
  FailureClass,
  FailureContext,
  OutJsonInfo,
  ShotsSummary,
} from './result.js'
import type { HeadlessRunRec, ScriptFileInfo, SpawnResult } from './types.js'
import type { BuiltHeadlessScript } from './script.js'

export interface HeadlessReceiptInput {
  ok: boolean
  runId: string
  session: string
  pluginVersion: string | null
  expect: ExpectEval | null
  runRec: HeadlessRunRec
  stdout: string
  stderr: string
  status: FailureClass
  failureHint: string | null
  ledgerWin: string
  scriptFile: ScriptFileInfo | null
  inputFile: unknown
  outJson: OutJsonInfo
  result: unknown
  parseError: { message: string; line: string } | null
  shots: ShotsSummary | null
  pathWarnings: unknown[]
  ms: number
  timeoutMs: number
  outdirWin: string
  outdirWsl: string | null
  childEnvKeys: string[]
  res: SpawnResult
  logs: HeadlessRunRec['logs']
  failure: FailureContext
  artifacts: ArtifactCollection
  noiseFiltered: number
  blenderExe: string | null
  built: BuiltHeadlessScript
  preload: unknown[] | undefined
  gpu: Record<string, unknown> | null
  engine: unknown
  engineMode: unknown
  useUserConfig: boolean
  resultTruncated: boolean
}

export function buildHeadlessReceipt(r: HeadlessReceiptInput): Record<string, unknown> {
  const rec = r.runRec
  const stdoutTail = r.stdout.length > 4000 ? r.stdout.slice(-4000) : r.stdout
  const stderrTail = r.stderr.length > 2000 ? r.stderr.slice(-2000) : r.stderr
  return {
    ok: r.ok,
    kind: 'headless',
    runId: r.runId,
    pluginVersion: r.pluginVersion,
    expect: r.expect,
    warnings: rec.warnings,
    progress: rec.progress || null,
    progressAt: rec.progressAt || null,
    heartbeat: rec.heartbeat || null,
    progressHistory: rec.progressHistory || [],
    stdoutTruncated: r.stdout.length > 8000,
    status: r.status,
    resumable: true,
    session: r.session,
    failure_hint: r.failureHint,
    how_to_recover:
      '客户端超时/断连不代表失败：这是独立子进程。用 blender_run(action=""status"|"collect"|"wait", id="' +
      r.runId +
      '") 按 runId 回收结果与产物（台账：' +
      r.ledgerWin +
      '）',
    scriptFile: r.scriptFile,
    inputFile: r.inputFile, // v0.9.3
    outJson: r.outJson.path,
    outJsonWsl: r.outJson.wsl || null,
    resultBytes: r.outJson.bytes,
    resultPath: r.outJson.path,
    resultPathWsl: r.outJson.wsl || null,
    resultTruncated: r.resultTruncated,
    resultJson: r.result,
    resultParseError: r.parseError, // v0.9.3
    stdoutTail: stdoutTail,
    stderrTail: stderrTail,
    stage: rec.stage,
    stageName: rec.stageName,
    stageAt: rec.stageAt,
    lastOutputAt: rec.lastOutputAt,
    lines: rec.lines,
    shots: r.shots,
    pathWarnings: r.pathWarnings, // v0.9.3
    outdir: { win: r.outdirWin, wsl: r.outdirWsl },
    childEnvKeys: r.childEnvKeys,
    exitCode: r.res.exitCode,
    signal: r.res.signal,
    timedOut: !!r.res.timedOut,
    ms: r.ms,
    timeoutMs: r.timeoutMs,
    blender: r.blenderExe,
    script: r.built.sp ? r.built.sp.win : null,
    args: r.built.args,
    scriptStaging: r.built.sp ? r.built.sp.staging || null : null, // v0.9.6：脚本落在哪 + 是否替换了 workDir
    preload: r.preload,
    gpu: r.gpu,
    engine: r.engine,
    engineMode: r.engineMode,
    useUserConfig: r.useUserConfig,
    logs: r.logs,
    lastException: r.failure.lastException,
    traceback: r.failure.traceback,
    hint: r.failure.hint,
    noiseFiltered: r.noiseFiltered,
    reason: r.res.timedOut
      ? 'killed after timeout ' + r.timeoutMs + 'ms'
      : r.status === 'gpu_required_missing'
        ? 'gpu required but unavailable'
        : r.res.exitCode === 0
          ? null
          : 'exit code ' + String(r.res.exitCode),
    result: r.result,
    stdout: clipMiddle(r.stdout, 4000, 4000),
    stderr: clipMiddle(r.stderr, 2000, 2000),
    artifacts: r.artifacts.artifacts,
  }
}
