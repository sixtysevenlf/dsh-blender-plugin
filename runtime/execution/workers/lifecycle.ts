// worker 生命周期：stop/restart/空闲回收
import type { WorkersCtx } from './api.js'
import { workerAlive, workerCall, workerState } from './api.js'
import { workerStart } from './worker-start.js'

export async function workerStop(
  ctx: WorkersCtx,
  nameArg?: unknown,
): Promise<Record<string, unknown>> {
  const w = workerState(ctx, nameArg)
  if (!workerAlive(w)) {
    w.ready = false
    return { name: w.name, stopped: false, wasAlive: false }
  }
  let bye = null
  try {
    bye = await workerCall(w, { op: 'shutdown' }, 8000)
  } catch (e) {
    /* 直接杀 */
  }
  await new Promise<void>((r) => setTimeout(r, 300))
  try {
    if (workerAlive(w) && w.child) w.child.kill('SIGKILL')
  } catch (e) {}
  w.ready = false
  return { name: w.name, stopped: true, wasAlive: true, bye: bye }
}

/** 重启：换一个干净会话（复用 = 放弃进程隔离，脚本脏了就用它） */
export async function workerRestart(
  ctx: WorkersCtx,
  nameArg?: unknown,
  opts?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  await workerStop(ctx, nameArg)
  return workerStart(ctx, opts || {}, nameArg)
}

// 会话池：空闲回收（默认 15 min；DSH_WORKER_TTL_MS 可调；0=关闭）。只回收 ready 且无请求在飞的热进程。
export function createWorkerReaper(ctx: WorkersCtx): {
  clearWorkerReaper: () => void
  killWorkers: () => void
} {
  const WORKER_TTL_MS = (() => {
    const n = Number(process.env.DSH_WORKER_TTL_MS)
    return Number.isFinite(n) && n >= 0 ? n : 900000
  })()
  let workerReaper: NodeJS.Timeout | null = null
  if (WORKER_TTL_MS > 0) {
    workerReaper = setInterval(() => {
      const now = Date.now()
      for (const w of ctx.WORKERS.values()) {
        if (!workerAlive(w) || !w.ready) continue
        if (w.lastUsed && now - w.lastUsed > WORKER_TTL_MS) {
          try {
            workerStop(ctx, w.name).catch(() => {})
          } catch (e) {
            /* 下一轮再试 */
          }
        }
      }
    }, 30000)
    try {
      if (workerReaper.unref) workerReaper.unref()
    } catch (e) {}
  }
  function clearWorkerReaper(): void {
    if (workerReaper) {
      try {
        clearInterval(workerReaper)
      } catch (e) {}
      workerReaper = null
    }
  }
  function killWorkers(): void {
    try {
      for (const w of ctx.WORKERS.values()) {
        if (workerAlive(w) && w.child) w.child.kill('SIGKILL')
      }
    } catch (e) {}
  }
  return { clearWorkerReaper, killWorkers }
}
