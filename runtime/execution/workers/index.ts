// 热无头 worker 会话池入口（拆分）
import { CFG } from '../../config.js'
import { errorMessage } from '../../core/json.js'
import type { WorkerRecord } from './api.js'
import { workerAlive, workerCall, workerList, workerSnapshot, workerState } from './api.js'
import type { WorkersCtx, WorkersDeps } from './api.js'
import { workerStart } from './worker-start.js'
import { createWorkerReaper, workerRestart, workerStop } from './lifecycle.js'

export type { WorkersDeps, WorkerRecord } from './api.js'
export type { WorkerInfo } from './worker-start.js'

export function createWorkersApi(E: WorkersDeps) {
  const ctx: WorkersCtx = {
    metrics: E.metrics,
    preloadChunk: E.preloadChunk,
    WORKERS: new Map<string, WorkerRecord>(),
    WORKER_PORT: Number(process.env.DSH_BLENDER_WORKER_PORT) || CFG.workerPort || 9879,
  }
  const { metrics } = ctx
  const start = (opts: Record<string, unknown> = {}, nameArg?: unknown) =>
    workerStart(ctx, opts, nameArg)
  async function workerExec(
    code: string,
    timeoutMs = 120000,
    purgePrefixes: unknown = null,
    nameArg?: unknown,
  ): Promise<Record<string, unknown>> {
    const w = workerState(ctx, nameArg)
    if (!workerAlive(w) || !w.ready) await workerStart(ctx, {}, w.name)
    w.lastUsed = Date.now()
    metrics.workerExecs++
    try {
      return await workerCall(
        w,
        { op: 'exec', code: String(code || ''), purgePrefixes: purgePrefixes || undefined },
        timeoutMs,
      )
    } catch (e) {
      w.lastError = errorMessage(e)
      throw e
    }
  }
  async function workerSee(spec: unknown, nameArg?: unknown): Promise<Record<string, unknown>> {
    const w = workerState(ctx, nameArg)
    if (!workerAlive(w) || !w.ready) await workerStart(ctx, {}, w.name)
    w.lastUsed = Date.now()
    metrics.workerExecs = (metrics.workerExecs || 0) + 1
    try {
      return await workerCall(w, { op: 'see', spec: spec || {} }, 60000)
    } catch (e) {
      w.lastError = errorMessage(e)
      throw e
    }
  }
  async function workerWatch(spec: unknown, nameArg?: unknown): Promise<Record<string, unknown>> {
    const w = workerState(ctx, nameArg)
    if (!workerAlive(w) || !w.ready)
      await workerStart(ctx, { engine: 'workbench', warmup: true }, w.name)
    w.lastUsed = Date.now()
    metrics.workerExecs = (metrics.workerExecs || 0) + 1
    try {
      return await workerCall(w, { op: 'watch', spec: spec || {} }, 180000)
    } catch (e) {
      w.lastError = errorMessage(e)
      throw e
    }
  }
  async function workerReset(nameArg?: unknown): Promise<Record<string, unknown>> {
    const w = workerState(ctx, nameArg)
    if (!workerAlive(w) || !w.ready) await workerStart(ctx, {}, w.name)
    try {
      return await workerCall(w, { op: 'reset' }, 60000)
    } catch (e) {
      w.lastError = errorMessage(e)
      throw e
    }
  }
  async function workerStatus(nameArg?: unknown): Promise<Record<string, unknown>> {
    const w = workerState(ctx, nameArg)
    if (!workerAlive(w) || !w.ready) return workerSnapshot(w)
    w.lastUsed = Date.now()
    try {
      const r = await workerCall(w, { op: 'status' }, 15000)
      return Object.assign(workerSnapshot(w), { status: r.status || null })
    } catch (e) {
      w.lastError = errorMessage(e)
      return Object.assign(workerSnapshot(w), { statusError: w.lastError })
    }
  }
  const { clearWorkerReaper, killWorkers } = createWorkerReaper(ctx)
  return {
    workerStart: start,
    workerExec,
    workerSee,
    workerWatch,
    workerReset,
    workerStatus,
    workerStop: (nameArg?: unknown) => workerStop(ctx, nameArg),
    workerRestart: (nameArg?: unknown, opts?: Record<string, unknown>) =>
      workerRestart(ctx, nameArg, opts),
    workerList: () => workerList(ctx),
    workerSnapshot,
    workerState: (name: unknown) => workerState(ctx, name),
    clearWorkerReaper,
    killWorkers,
  }
}
