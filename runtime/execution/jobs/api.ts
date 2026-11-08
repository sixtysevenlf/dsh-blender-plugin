// 作业查询面
import path from 'node:path'
import { winToWsl } from '../../config.js'
import type { JobsDeps, JobRecord } from './types.js'
import type { LedgerApi } from '../ledger/index.js'
import { jobSnapshot, tailFile } from './snapshot.js'
import { startJob } from './start/index.js'
import type { JobStartDeps } from './start/index.js'

export interface JobsCtx {
  E: JobsDeps
  jobs: Map<string, JobRecord>
  ledger: LedgerApi
}

export function jobStart(
  ctx: JobsCtx,
  opts: Record<string, unknown> = {},
): Record<string, unknown> {
  const startDeps: JobStartDeps = {
    E: ctx.E,
    jobs: ctx.jobs,
    ledgerAppend: ctx.ledger.ledgerAppend,
  }
  return startJob(startDeps, opts)
}
export function jobStatus(ctx: JobsCtx, id: unknown): Record<string, unknown> {
  const key = String(id)
  const j = ctx.jobs.get(key)
  if (j) {
    const s = jobSnapshot(j)
    s.stale = false
    return s
  }
  const r = ctx.ledger.runs.get(key) as Record<string, unknown> | undefined
  if (r) {
    const s = ctx.ledger.runSnapshot(r)
    s.stale = false
    return s
  }
  const led = ctx.ledger.ledgerList(200).find((x) => x.id === key)
  return led
    ? Object.assign(
        { kind: led.kind || 'ledger', fromLedger: true },
        ctx.ledger.reconcileLedger(led),
      )
    : {
        id: key,
        status: 'unknown',
        ok: false,
        hint: '没有这个 id 的记录（内存台账 + 磁盘台账都查过了）',
      }
}
export function jobCollect(ctx: JobsCtx, id: unknown, tail = 4000): Record<string, unknown> {
  const key = String(id)
  const j = ctx.jobs.get(key)
  if (j) {
    const snap = jobSnapshot(j)
    const so = tailFile(path.join(j.dir, 'stdout.log'), tail)
    const se = tailFile(path.join(j.dir, 'stderr.log'), tail)
    if (so !== null) snap.stdoutTail = so
    if (se !== null) snap.stderrTail = se
    return snap
  }
  const r = ctx.ledger.runs.get(key) as Record<string, unknown> | undefined
  if (r) {
    const snap = ctx.ledger.runSnapshot(r)
    const rLogs = (r.logs || null) as { stdoutWsl?: string; stderrWsl?: string } | null
    const soP = rLogs && rLogs.stdoutWsl ? rLogs.stdoutWsl : null
    const seP = rLogs && rLogs.stderrWsl ? rLogs.stderrWsl : null
    // v0.9.3：日志是边跑边写的 → 运行中也能 tail（旧版 collect 对 run 直接回 unknown）
    if (soP) {
      const t = tailFile(soP, tail)
      if (t !== null) snap.stdoutTail = t
    }
    if (seP) {
      const t = tailFile(seP, tail)
      if (t !== null) snap.stderrTail = t
    }
    snap.hint = 'run 与 job 同一 id 空间；本条目来自 blender_run 的同步执行'
    return snap
  }
  const led = ctx.ledger.ledgerList(200).find((x) => x.id === key)
  if (!led)
    return { id: key, status: 'unknown', ok: false, hint: '没有这个 id 的记录；op=list 看全部' }
  const rec = ctx.ledger.reconcileLedger(led) || {}
  if (rec.status === 'running' || rec.status === 'stale') {
    rec.stdoutTail = rec.stdoutLog ? tailFile(winToWsl(String(rec.stdoutLog)), tail) : null
    rec.stderrTail = rec.stderrLog ? tailFile(winToWsl(String(rec.stderrLog)), tail) : null
  }
  return Object.assign({ kind: led.kind || 'ledger', fromLedger: true }, rec)
}
export async function jobWait(
  ctx: JobsCtx,
  id: unknown,
  timeoutMs = 120000,
): Promise<Record<string, unknown>> {
  const key = String(id)
  const budget = Math.max(1000, Math.min(600000, Number(timeoutMs) || 120000))
  const t0 = Date.now()
  let waited = 0
  for (;;) {
    const st = jobStatus(ctx, key)
    if (st.status !== 'running')
      return Object.assign(jobCollect(ctx, key), { waitedMs: Date.now() - t0, waitTimedOut: false })
    if (Date.now() - t0 >= budget) {
      return Object.assign(jobCollect(ctx, key), {
        waitedMs: Date.now() - t0,
        waitTimedOut: true,
        hint: '等待窗口到点，任务**仍在跑**（不是失败）：再调一次 op=wait，或 op=collect 看 stage/idleMs',
      })
    }
    await new Promise<void>((r2) => setTimeout(r2, 500))
    waited++
    if (waited > 2400) break // 安全上限（>20min 的极端轮次）
  }
  return Object.assign(jobCollect(ctx, key), { waitedMs: Date.now() - t0, waitTimedOut: true })
}
export function jobList(ctx: JobsCtx): Array<Record<string, unknown>> {
  const mem = Array.from(ctx.jobs.values())
    .map(jobSnapshot)
    .concat(
      Array.from(ctx.ledger.runs.values()).map((r) =>
        ctx.ledger.runSnapshot(r as Record<string, unknown>),
      ),
    )
  const ids = new Set(mem.map((x) => x.id))
  const disk = ctx.ledger
    .ledgerList(60)
    .filter((x) => !ids.has(x.id))
    .map((x) =>
      Object.assign({ kind: x.kind || 'ledger', fromLedger: true }, ctx.ledger.reconcileLedger(x)),
    )
  return mem.concat(disk)
}
