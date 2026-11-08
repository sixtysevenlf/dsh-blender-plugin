/** blender_run 的呈现层：作业记录、等待/收集、队列指标、tune 回执。 */
import { asRecord, text } from '../../contracts/dynamic.js'

const LF = String.fromCharCode(10)

export function iso(ms: unknown): string {
  const n = Number(ms)
  return n > 0 ? new Date(n).toISOString() : '-'
}

function ag(ms: unknown): string {
  const n = Number(ms)
  if (!(n > 0)) return '-'
  const s = Math.max(0, Math.round((Date.now() - n) / 1000))
  return s < 60 ? String(s) + 's ago' : String(Math.round(s / 60)) + 'm ago'
}

function clip(s: string, n = 3000): string {
  return s.length > n ? s.slice(0, n) + ' …' : s
}

export function formatJob(job: Record<string, unknown>): string[] {
  const result = asRecord(job.result)
  const decision = asRecord(job.decision)
  const err = asRecord(job.error)
  const lines = [
    'RUN ' +
      text(job.id) +
      ' · ' +
      text(job.kind) +
      ' · ' +
      text(job.state) +
      ' · slot=' +
      text(job.slot || '-') +
      ' · priority=' +
      text(job.priority),
    'timing  created=' +
      iso(job.createdAt) +
      ' started=' +
      iso(job.startedAt) +
      ' finished=' +
      iso(job.finishedAt) +
      (job.result ? ' · result=' + text(result.ms) + 'ms' : ''),
    'pid     ' + text(job.pid || '-') + ' · updated ' + ag(job.updatedAt),
  ]
  if (job.decision)
    lines.push(
      'decision ' +
        text(decision.outcome) +
        ' · ' +
        text(decision.reason) +
        (decision.detail ? ' · ' + text(decision.detail) : ''),
    )
  if (job.result)
    lines.push(
      'result  exit=' +
        text(
          result.exitCode === null || result.exitCode === undefined ? 'signal' : result.exitCode,
        ) +
        (result.signal ? ' · signal=' + text(result.signal) : '') +
        (result.parsed !== undefined
          ? ' · parsed=' + clip(JSON.stringify(result.parsed), 1200)
          : ''),
    )
  const artifacts = Array.isArray(job.artifacts) ? job.artifacts : []
  if (artifacts.length) {
    lines.push('artifacts ' + String(artifacts.length) + ' 个')
    for (const a of artifacts.slice(0, 8)) lines.push('  · ' + text(a))
    if (artifacts.length > 8) lines.push('  · …另有 ' + String(artifacts.length - 8) + ' 个')
  }
  if (job.error)
    lines.push(
      'error   ' + text(err.code) + ' · stage=' + text(err.stage) + ' · ' + text(err.root_cause),
      'fix     ' + text(err.fix || '-'),
    )
  return lines
}

export function formatSubmit(job: Record<string, unknown>): string {
  const lines = formatJob(job)
  lines.push(
    'next  blender_run action=wait id=' +
      text(job.id) +
      '（默认 120s，上限 600s，超时不取消）；action=collect 取日志/产物；action=cancel 取消',
  )
  return lines.join(LF)
}

export function formatWait(
  job: Record<string, unknown>,
  waitedMs: unknown,
  timedOut: unknown,
): string {
  const lines = formatJob(job)
  lines.push(
    'wait ' +
      String(waitedMs) +
      'ms · ' +
      (timedOut === true
        ? '仍在 ' +
          text(job.state) +
          '（等待超时不取消作业；稍后 action=status 或再加长 action=wait）'
        : '到达终态 ' + text(job.state)),
  )
  return lines.join(LF)
}

export function formatCollect(job: Record<string, unknown>): string {
  const logs = asRecord(job.logs)
  const lines = formatJob(job)
  if (logs.stdout && String(logs.stdout).trim())
    lines.push('--- stdout ---' + LF + clip(String(logs.stdout)))
  if (logs.stderr && String(logs.stderr).trim())
    lines.push('--- stderr ---' + LF + clip(String(logs.stderr)))
  if (!logs.stdout && !logs.stderr) lines.push('logs 空（作业还没产出输出）')
  return lines.join(LF)
}

export function formatCancel(body: Record<string, unknown>): string {
  const job = asRecord(body.job)
  if (body.alreadyFinished === true)
    return 'RUN cancel · 作业已到终态 ' + text(job.state) + '（幂等，未再终止）'
  return 'RUN cancel ok · ' + text(job.id) + ' → ' + text(job.state) + '（进程树已清理）'
}

export function formatList(jobs: Array<Record<string, unknown>>): string {
  if (!jobs.length) return 'RUN list · 0 个作业（队列为空）'
  const lines = ['RUN list · ' + String(jobs.length) + ' 个作业']
  for (const j of jobs)
    lines.push(
      '· ' +
        text(j.id) +
        ' · ' +
        text(j.kind) +
        ' · ' +
        text(j.state) +
        ' · slot=' +
        text(j.slot || '-') +
        ' · priority=' +
        text(j.priority) +
        ' · created=' +
        iso(j.createdAt),
    )
  return lines.join(LF)
}

export function formatPerf(perf: Record<string, unknown>): string {
  const byState = asRecord(perf.byState)
  const slots = asRecord(perf.slots)
  const gpu = asRecord(slots.gpu)
  const cpu = asRecord(slots.cpu)
  const lines = [
    'RUN perf · pending=' + text(perf.pending),
    'byState  ' +
      ['queued', 'assigned', 'running', 'completed', 'failed', 'canceled']
        .map((s) => s + '=' + text(byState[s] ?? 0))
        .join(' '),
    'slots    gpu ' +
      text(gpu.used) +
      '/' +
      text(gpu.total) +
      ' (reserved=' +
      text(gpu.reserved || 0) +
      ') · cpu ' +
      text(cpu.used) +
      '/' +
      text(cpu.total),
    'oldestQueuedAt ' + iso(perf.oldestQueuedAt),
  ]
  return lines.join(LF)
}

export function runHelp(): string {
  return [
    'blender_run 动作（唯一作业入口：submit → jobId → status/wait/collect/cancel；无同步模式）：',
    '  action=submit kind=render|headless + 参数',
    '    render（声明式，不接受 script）：file/output/outdir/frames/resolution/samples/engine/format/camera/priority',
    '    headless（跑脚本）：script/script_file/file/outdir/timeout_ms/engine/preload/factory_startup/bootstrap/workdir/include_noise/out_json/args/env/priority',
    '  action=status|wait|collect|cancel id=<jobId>；wait 默认 120s、上限 600s，超时不取消',
    '  action=list [state=kind=owner=] | action=perf（队列容量与槽位）',
    '  action=tune op=status|analyze|apply|revert|help（旧 blender_run 的 Cycles 分析）',
    '旧工具迁移：blender_rt_headless / blender_rt_job → blender_run action=submit（kind=headless / render）；blender_rt_perf → blender_run action=tune。',
  ].join(LF)
}
