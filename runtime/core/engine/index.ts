// engine 装配入口：类型定义 + createEngine 薄装配。
// createEngine 只把各工厂接到同一个 deps 对象上；业务实现位于 core/*、execution/*、transport/*。
// 对外 surface 覆盖工具 / 路由 / 指标 / 租约相关回执。
import type { AddonClient as AddonClientType } from '../../transport/probes.js'
import type { createJobsApi } from '../../execution/jobs/index.js'
import type { createWorkersApi } from '../../execution/workers/index.js'

/** 通道侧指标：谁在写 / 忙不忙 / 队列多深（供 /status、/who 用） */
export interface EngineMetrics {
  [k: string]: unknown
  calls: number
  errors: number
  timeouts: number
  inflight: number
  lastCmd: string | null
  lastCmdAt: number | null
  lastOkAt: number | null
  lastError: string | null
  lastDiagnosis: unknown
  views: number
  headlessRuns: number
  lastHeadlessMs: number | null
  workerStarts: number
  workerExecs: number
  lastErrorAt: number | null
  aiPausedAt: number | null
}

export type JobApi = ReturnType<typeof createJobsApi>
export type WorkerApi = ReturnType<typeof createWorkersApi>

/** 引擎 surface 上的作业视图（方法名与 server.mjs 兼容面一致）。 */
export interface JobView {
  start: JobApi['jobStart']
  status: JobApi['jobStatus']
  collect: JobApi['jobCollect']
  kill: JobApi['jobKill']
  list: JobApi['jobList']
  wait: JobApi['jobWait']
}

/** 引擎 surface 上的 worker 视图。 */
export interface WorkerView {
  start: WorkerApi['workerStart']
  exec: WorkerApi['workerExec']
  see: WorkerApi['workerSee']
  watch: WorkerApi['workerWatch']
  reset: WorkerApi['workerReset']
  status: WorkerApi['workerStatus']
  stop: WorkerApi['workerStop']
  restart: WorkerApi['workerRestart']
  list: WorkerApi['workerList']
  snapshot: WorkerApi['workerSnapshot']
  state: WorkerApi['workerState']
}

export interface EngineOptions {
  /** 3.1 Gateway: the only supported addon client; there is no direct TCP fallback. */
  addonClient?: AddonClientType
  [k: string]: unknown
}

/** 引擎公开面：http 层与脚本按能力名取用；多余字段经 index signature 保留 */
export interface EngineSurface {
  [k: string]: unknown
  addon: AddonClientType
  plan: (op: string, payload?: unknown) => Promise<unknown>
  txn: (op: string, payload?: unknown) => Promise<unknown>
  preset: (op: string, payload?: unknown) => Promise<unknown>
  worker: WorkerView
  job: JobView
  launchBlender: (o?: unknown) => unknown
  sceneEpoch: () => Promise<unknown>
  renderFlag: () => unknown
  headless: (opts?: Record<string, unknown>) => Promise<Record<string, unknown>>
  start: () => Promise<unknown>
  stop: () => unknown
  metrics: () => unknown
  protocol: () => Promise<unknown>
  doctor: () => Promise<unknown>
  status: () => Promise<unknown>
  act: (code: unknown, timeoutMs?: number, file?: unknown) => Promise<unknown>
  cmd: (name: unknown, params?: Record<string, unknown>, timeoutMs?: number) => Promise<unknown>
  commands: () => Promise<unknown>
  loopStart: (spec: unknown) => Promise<unknown>
  loopStatus: (history?: number) => Promise<unknown>
  loopStop: () => Promise<unknown>
  loopBench: (iterations?: number) => Promise<unknown>
  loopBoard: (limit?: number, groups?: boolean) => Promise<unknown>
  loopExport: (opts?: Record<string, unknown>) => Promise<unknown>
  loopHelp: () => Promise<unknown>
  frameArea: (areaIndex?: number) => Promise<unknown>
  perf: (op: string, payload?: unknown) => Promise<unknown>
  opt: (op: string, payload?: unknown) => Promise<unknown>
  frame: (maxSize?: number) => Promise<unknown>
  view: (spec?: Record<string, unknown>) => Promise<unknown>
  hostSceneItems: (spec?: Record<string, unknown>) => Promise<unknown>
}

import { assembleEngine, createEngineContext } from './assemble.js'

/**
 * 装配引擎。参数只有 opts.address（addon 地址覆盖）。
 * 这里不实现任何业务；只负责把各个工厂接到同一个 deps 对象上，
 * 保证闭包共享同一份 addon / metrics / ledger / jobs。
 */

export function createEngine(opts: EngineOptions = {}): EngineSurface {
  const ctx = createEngineContext(opts)
  const { surface, trajectory } = assembleEngine(ctx)
  return trajectory.withTrajectory(surface) as EngineSurface
}
