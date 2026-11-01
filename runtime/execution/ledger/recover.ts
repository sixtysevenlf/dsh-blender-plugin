// 台账对账（磁盘证据 + pid 收敛）：F1.1 从 ledger.ts 拆出
import fs from 'node:fs'
import { winToWsl, wslToWin } from '../../config.js'
import { WIN_TMP } from '../../paths.js'
import { PLUGIN_VERSION, pidAlive } from '../../core/evidence.js'
import { reconcileLedger as reconcileLedgerFromDisk } from '../../recover.js'

/**
 * v0.9.3（D2）：run（blender_rt_headless 的同步执行）与 job 同一份结构化回执形状 ——
 * collect/status 收 run-… 与 job-… 都是同一套字段，调用方不必分辨两者。
 */
function runSnapshot(r: Record<string, unknown>): Record<string, unknown> {
  const now = Date.now()
  const running = r.status === 'running'
  const rLogs = r.logs as { stdoutWsl?: string } | null
  const rArtifacts: unknown[] = Array.isArray(r.artifacts) ? r.artifacts : []
  return {
    kind: 'headless',
    id: r.id,
    status: r.status,
    pid: r.pid,
    pidAlive: running ? pidAlive(r.pid) : false,
    ms: (Number(r.finishedAt) || now) - Number(r.startedAt),
    timeoutMs: r.timeoutMs || null,
    outdir: r.outdir || WIN_TMP,
    outdirWsl: r.outdirWsl || null,
    logs: r.logs || null,
    script: r.script || null,
    exitCode: r.exitCode,
    signal: r.signal || null,
    timedOut: r.timedOut || false,
    failureClass: r.failureClass || null,
    stage: r.stage || null,
    stageName: r.stageName || null,
    stageAt: r.stageAt || null,
    stageAgoMs: r.stageAt ? now - Number(r.stageAt) : null,
    lastOutputAt: r.lastOutputAt || null,
    idleMs: r.lastOutputAt ? now - Number(r.lastOutputAt) : now - Number(r.startedAt),
    lines: r.lines || 0,
    logBytes: (() => {
      try {
        const p = rLogs && rLogs.stdoutWsl
        return p ? fs.statSync(p).size : 0
      } catch (e) {
        return 0
      }
    })(),
    resultJson: r.result || null,
    result: r.result || null,
    resultParseError: r.parseError || null,
    resultPath: r.resultPath || null,
    resultPathWsl: r.resultPathWsl || null,
    resultBytes: r.resultBytes || 0,
    resultTruncated: !!r.resultTruncated,
    stdoutTail: r.stdoutTail || null,
    stderrTail: r.stderrTail || null,
    pathWarnings: r.pathWarnings || [],
    warnings: r.warnings || r.pathWarnings || [],
    shots: r.shots || null,
    progress: r.progress || null,
    progressAt: r.progressAt || null,
    progressAgeMs: r.progressAt ? now - Number(r.progressAt) : null,
    heartbeat: r.heartbeat || null,
    progressHistory: r.progressHistory || [],
    inputFile: r.inputFile || null,
    artifacts: rArtifacts.map((n: unknown) => (typeof n === 'string' ? { name: n } : n)),
    artifactCount: rArtifacts.length,
    expect: r.expect || null,
    pluginVersion: PLUGIN_VERSION,
    hint:
      '这是 blender_rt_headless 的运行台账（run 与 job 同一 id 空间）：产物在 outdir，日志见 logs；' +
      '结果在 resultJson / resultPath（运行中则用 stage / idleMs 看进展）',
  }
}

/**
 * 磁盘台账记录（后端重启后的历史）：running 但 pid 已不存在时的收敛。
 *
 * v2.6（本轮反馈 #1）：旧版一律判 stale —— 但实测两次「子进程其实已经跑完、产物/日志都在」，
 * 调用方要自己 Get-Process + ls 破案，每次至少赔一轮往返。现在先做磁盘对账：
 *   summary.json / resultPath / stdout 终态（DSH_STAGE done、HEADLESS、Traceback）
 * 对得上 ⇒ status=done|failed + recovered=true（证据在 recoveredFrom/recoveredEvidence）；
 * 对不上才判 stale，并把 evidence（日志大小/lastStage）附回去。
 * 纯逻辑在 runtime/recover.ts，可脱离 Blender 单测（tests/unit/js/ledger-recover.test.ts）。
 */
function reconcileLedger(rec: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!rec || rec.status !== 'running') return rec
  return reconcileLedgerFromDisk(rec, { winToWsl: winToWsl, wslToWin: wslToWin }, pidAlive)
}

export function createLedgerRecover() {
  return { runSnapshot, reconcileLedger }
}
