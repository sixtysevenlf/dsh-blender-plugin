/** blender_rt_job 的呈现层：作业列表/各类回执装配（行为与 v2.6.2 一致）。 */
import { headlessReceipt, promotedReceipt } from '../../headless/receipts/index.js'
import { clipText, safeStringify } from '../../ui/cards.js'
import { asArray, asRecord, text } from '../../contracts/dynamic.js'

const LF = String.fromCharCode(10)

interface JobCtx {
  mode: 'job'
  jobId: string | null
  runId: string | null
  notes: string[]
  hint: string | null
}

export function renderJobList(r: Record<string, unknown>): { text: string } {
  const js = asArray(r.jobs)
  if (!js.length) return { text: '没有作业' }
  const lines = js.map((raw) => {
    const j = asRecord(raw)
    const artifacts = asArray(j.artifacts)
    return (
      '  ' +
      text(j.id) +
      ' · ' +
      text(j.status) +
      (j.stageName ? ' · stage=' + text(j.stageName) : '') +
      (typeof j.ms === 'number' ? ' · ' + String(Math.round(j.ms / 1000)) + 's' : '') +
      (j.fromLedger ? ' · (磁盘台账)' : '') +
      ' · 产物 ' +
      String(artifacts.length) +
      ' 项'
    )
  })
  return {
    text: '作业（' + String(js.length) + '）：' + LF + lines.join(LF),
  }
}

export function buildJobStart(j: Record<string, unknown>, ctx: JobCtx): unknown {
  ctx.notes.push('已后台化（pid ' + text(j.pid) + '，日志目录 ' + text(j.logDir) + '）')
  ctx.hint =
    '用 op=wait id=' +
    text(j.id) +
    ' 阻塞等结果，或 op=collect 看进展（stage/idleMs）；op=kill 中止'
  const out = promotedReceipt(j, text(j.id), 'op=start 已接受')
  out.envelope.mode = 'job-start'
  out.envelope.notes = ctx.notes
  out.envelope.hint = ctx.hint
  return out
}

export function buildJobKill(j: Record<string, unknown>): unknown {
  const t =
    j.alreadyFinished || j.status === 'unknown'
      ? 'JOB kill · ' +
        text(j.id) +
        ' · ' +
        text(j.status) +
        '（' +
        text(j.note || '已结束/不存在，无需 kill') +
        '）'
      : 'JOB kill · ' + text(j.id) + ' · status=' + text(j.status) + '（已发 SIGKILL）'
  return {
    text: t,
    envelope: {
      kind: 'job',
      mode: 'job-kill',
      id: j.id || null,
      status: j.status || null,
      ok: true,
      alreadyFinished: !!j.alreadyFinished,
      note: j.note || null,
      fromLedger: !!j.fromLedger,
    },
  }
}

export function buildRecoveredJob(j: Record<string, unknown>, op: string): unknown {
  return {
    text:
      'JOB ' +
      op +
      ' · ' +
      text(j.id) +
      ' · status=' +
      text(j.status) +
      LF +
      '（已完成 · 磁盘产物对账 recovered from ' +
      text(j.recoveredFrom || '?') +
      '）' +
      LF +
      text(j.hint),
    envelope: {
      kind: 'job',
      mode: 'job-' + op,
      id: j.id || null,
      status: j.status || null,
      ok: true,
      recovered: true,
      recoveredFrom: j.recoveredFrom || null,
      recoveredEvidence: j.recoveredEvidence || null,
      resultJson: j.resultJson || null,
      resultPath: j.resultPath || null,
      stdoutTail: clipText(j.stdoutTail, 1000, 1000),
    },
  }
}

export function buildLedgerJob(j: Record<string, unknown>, op: string): unknown {
  return {
    text:
      'JOB ' +
      op +
      ' · ' +
      text(j.id) +
      ' · status=' +
      text(j.status) +
      LF +
      '（磁盘台账记录）' +
      text(j.staleReason) +
      LF +
      text(j.hint),
    envelope: {
      kind: 'ledger',
      mode: 'job-' + op,
      id: j.id || null,
      status: j.status || null,
      ok: true,
      fromLedger: true,
      staleReason: j.staleReason || null,
      pid: j.pid || null,
      pidAlive: !!j.pidAlive,
      resultJson: j.resultJson || null,
      resultPath: j.resultPath || null,
      stdoutTail: clipText(j.stdoutTail, 1000, 1000),
    },
  }
}

export function buildCollectRunning(j: Record<string, unknown>, ctx: JobCtx): unknown {
  const hb = asRecord(j.heartbeat)
  const at = Number(hb.at || 0)
  ctx.notes.push(
    '任务仍在跑（不是失败）：stage=' +
      safeStringify(j.stage || j.stageName) +
      (j.progress ? ' · progress=' + safeStringify(j.progress) : '') +
      (j.heartbeat ? ' · 心跳 ' + String(Math.round((Date.now() - at || 0) / 1000)) + 's 前' : '') +
      ' · 最近输出 ' +
      String(Math.round(Number(j.idleMs || 0) / 1000)) +
      's 前',
  )
  const out = promotedReceipt(j, text(j.id), 'op=collect：任务还在跑')
  out.envelope.mode = 'collect-running'
  out.envelope.notes = ctx.notes
  return out
}

export function buildWaitTimeout(j: Record<string, unknown>): unknown {
  const out = promotedReceipt(
    j,
    text(j.id),
    'op=wait：等待窗口（' +
      String(Math.round(Number(j.waitedMs || 0) / 1000)) +
      's）到点，任务仍在跑',
  )
  out.envelope.mode = 'wait-timeout'
  return out
}

export function buildFinalJob(j: Record<string, unknown>, ctx: JobCtx, notes: string[]): unknown {
  return headlessReceipt(j, {
    mode: 'job',
    jobId: ctx.jobId,
    runId: ctx.runId,
    notes: notes,
  })
}
