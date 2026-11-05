// 作业 kill
import type { JobRecord } from './types.js'
import type { JobsCtx } from './api.js'
import { jobSnapshot } from './snapshot.js'

export function jobKill(ctx: JobsCtx, id: unknown): Record<string, unknown> {
  const key = String(id)
  const j = ctx.jobs.get(key)
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
  const r = ctx.ledger.runs.get(key) as JobRecord | undefined
  if (r) {
    if (r.status === 'running') {
      r.status = 'killed'
      r.finishedAt = Date.now()
      try {
        if (r.child) r.child.kill('SIGKILL')
      } catch (e) {
        /* 可能已退出 */
      }
      ctx.ledger.ledgerAppend({
        id: key,
        kind: 'headless',
        status: 'killed',
        killedBy: 'op=kill',
        ms: Date.now() - r.startedAt,
      })
    }
    return ctx.ledger.runSnapshot(r as unknown as Record<string, unknown>)
  }
  const led = ctx.ledger.ledgerList(200).find((x) => x.id === key)
  const rec = led ? ctx.ledger.reconcileLedger(led) : null
  // v0.9.3：未知 id **不抛错** —— 旧版回 unknown 让调用方流程中断
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
