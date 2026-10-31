// 作业层：start/status/collect/wait/kill/list（T6）
// @imports
import path from 'node:path'
import { winToWsl } from '../config.js'
import { createLedger } from './ledger.js'
import type { JobsDeps, JobRecord } from './jobs/types.js'
import { jobArtifacts, jobDir, jobSnapshot, tailFile } from './jobs/snapshot.js'
import { startJob } from './jobs/start.js'

export type { JobsDeps, JobRecord } from './jobs/types.js'

export function createJobsApi(E: JobsDeps) {
  const ledger = E.ledger || createLedger(E)
  const { runs, ledgerAppend, ledgerList, reconcileLedger, runSnapshot } = ledger
  // ---------------------------------------------------------------- 作业层（v0.8.5）
  // 长任务后台化：start 立刻返回 job id（不占客户端连接）；status/collect/kill 轮询；日志与产物落在 outdir/jobs/<id>/。
  const jobs = new Map<string, JobRecord>()
  const startDeps = { E: E, jobs: jobs, ledgerAppend: ledgerAppend }
  function jobStart(opts: Record<string, unknown> = {}): Record<string, unknown> {
    return startJob(startDeps, opts)
  }
  function jobStatus(id: unknown): Record<string, unknown> {
    const key = String(id)
    const j = jobs.get(key)
    if (j) {
      const s = jobSnapshot(j)
      s.stale = false
      return s
    }
    const r = runs.get(key) as Record<string, unknown> | undefined
    if (r) {
      const s = runSnapshot(r)
      s.stale = false
      return s
    }
    const led = ledgerList(200).find((x) => x.id === key)
    return led
      ? Object.assign({ kind: led.kind || 'ledger', fromLedger: true }, reconcileLedger(led))
      : {
          id: key,
          status: 'unknown',
          ok: false,
          hint: '没有这个 id 的记录（内存台账 + 磁盘台账都查过了）',
        }
  }
  function jobCollect(id: unknown, tail = 4000): Record<string, unknown> {
    const key = String(id)
    const j = jobs.get(key)
    if (j) {
      const snap = jobSnapshot(j)
      const so = tailFile(path.join(j.dir, 'stdout.log'), tail)
      const se = tailFile(path.join(j.dir, 'stderr.log'), tail)
      if (so !== null) snap.stdoutTail = so
      if (se !== null) snap.stderrTail = se
      return snap
    }
    const r = runs.get(key) as Record<string, unknown> | undefined
    if (r) {
      const snap = runSnapshot(r)
      const rLogs = (r.logs || null) as { stdoutWsl?: string; stderrWsl?: string } | null
      const soP = rLogs && rLogs.stdoutWsl ? rLogs.stdoutWsl : null
      const seP = rLogs && rLogs.stderrWsl ? rLogs.stderrWsl : null
      // v0.9.3（D5）：日志是边跑边写的 → 运行中也能 tail（旧版 collect 对 run 直接回 unknown）
      if (soP) {
        const t = tailFile(soP, tail)
        if (t !== null) snap.stdoutTail = t
      }
      if (seP) {
        const t = tailFile(seP, tail)
        if (t !== null) snap.stderrTail = t
      }
      snap.hint = 'run 与 job 同一 id 空间；本条目来自 blender_rt_headless 的同步执行'
      return snap
    }
    const led = ledgerList(200).find((x) => x.id === key)
    if (!led)
      return { id: key, status: 'unknown', ok: false, hint: '没有这个 id 的记录；op=list 看全部' }
    const rec = reconcileLedger(led) || {}
    if (rec.status === 'running' || rec.status === 'stale') {
      rec.stdoutTail = rec.stdoutLog ? tailFile(winToWsl(String(rec.stdoutLog)), tail) : null
      rec.stderrTail = rec.stderrLog ? tailFile(winToWsl(String(rec.stderrLog)), tail) : null
    }
    return Object.assign({ kind: led.kind || 'ledger', fromLedger: true }, rec)
  }
  /**
   * v0.9.3（D6.2）：阻塞等待 —— 旧版只能循环 op=status，会撞外层 harness 的
   * "Repeated tool call detected (consecutive_calls: 5/8)"；op=collect 又不等。
   * 语义：等到终态或 timeout_ms 到点（默认 120s，上限 600s/次，可反复调）。
   */
  async function jobWait(id: unknown, timeoutMs = 120000): Promise<Record<string, unknown>> {
    const key = String(id)
    const budget = Math.max(1000, Math.min(600000, Number(timeoutMs) || 120000))
    const t0 = Date.now()
    let waited = 0
    for (;;) {
      const st = jobStatus(key)
      if (st.status !== 'running')
        return Object.assign(jobCollect(key), { waitedMs: Date.now() - t0, waitTimedOut: false })
      if (Date.now() - t0 >= budget) {
        return Object.assign(jobCollect(key), {
          waitedMs: Date.now() - t0,
          waitTimedOut: true,
          hint: '等待窗口到点，任务**仍在跑**（不是失败）：再调一次 op=wait，或 op=collect 看 stage/idleMs',
        })
      }
      await new Promise<void>((r2) => setTimeout(r2, 500))
      waited++
      if (waited > 2400) break // 安全上限（>20min 的极端轮次）
    }
    return Object.assign(jobCollect(key), { waitedMs: Date.now() - t0, waitTimedOut: true })
  }
  function jobKill(id: unknown): Record<string, unknown> {
    const key = String(id)
    const j = jobs.get(key)
    if (j) {
      if (j.status === 'running') {
        j.status = 'killed'
        try {
          if (j.child) j.child.kill('SIGKILL')
        } catch (e) {
          /* 可能已退出 */
        }
      }
      return jobSnapshot(j)
    }
    const r = runs.get(key) as JobRecord | undefined
    if (r) {
      if (r.status === 'running') {
        r.status = 'killed'
        r.finishedAt = Date.now()
        try {
          if (r.child) r.child.kill('SIGKILL')
        } catch (e) {
          /* 可能已退出 */
        }
        ledgerAppend({
          id: key,
          kind: 'headless',
          status: 'killed',
          killedBy: 'op=kill',
          ms: Date.now() - r.startedAt,
        })
      }
      return runSnapshot(r as unknown as Record<string, unknown>)
    }
    const led = ledgerList(200).find((x) => x.id === key)
    const rec = led ? reconcileLedger(led) : null
    // v0.9.3（D6.4）：未知 id **不抛错** —— 旧版回 unknown 让调用方流程中断
    return {
      id: key,
      ok: true,
      status: (rec && rec.status) || 'unknown',
      alreadyFinished: !(rec && rec.status === 'running'),
      fromLedger: !!rec,
      note: rec
        ? '这条来自磁盘台账，内存里没有在跑的句柄（已结束或后端重启过）；无需 kill'
        : '没有这个 id 的记录（可能已结束/被清理）—— 不需要 kill，op=list 看当前作业',
    }
  }
  function jobList(): Array<Record<string, unknown>> {
    const mem = Array.from(jobs.values())
      .map(jobSnapshot)
      .concat(Array.from(runs.values()).map((r) => runSnapshot(r as Record<string, unknown>)))
    const ids = new Set(mem.map((x) => x.id))
    const disk = ledgerList(60)
      .filter((x) => !ids.has(x.id))
      .map((x) => Object.assign({ kind: x.kind || 'ledger', fromLedger: true }, reconcileLedger(x)))
    return mem.concat(disk)
  }
  return {
    jobs,
    jobStart,
    jobSnapshot,
    jobStatus,
    jobCollect,
    jobWait,
    jobKill,
    jobList,
    jobDir,
    jobArtifacts,
    tailFile,
    ledger,
  }
}
