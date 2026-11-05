// engine API 入口（server-compat 更名并拆分）
import type { AddonClient } from '../../transport/probes.js'
import type { JobRecord } from '../../execution/jobs/index.js'
import { createMetricsApi } from './metrics.js'
import { createDoctorApi } from './doctor.js'
import { createActApi } from './act.js'
import { createLoopApi } from './loop.js'
import { createFrameApi } from './frame.js'
import { createViewApi } from './view.js'
import { createHostApi } from './host.js'

export interface EngineApiMetrics {
  [k: string]: unknown
  lastCmdAt: number | null
  views: number
}

export interface EngineApiDeps {
  LEDGER: string
  RESULTS_DIR: string
  SESSION_NAME: string
  TRAJ_DIR: string
  TRAJ_FULL: boolean
  TRAJ_OFF: boolean
  addon: AddonClient
  clearWorkerReaper: () => void
  ensureRunner: () => Promise<unknown>
  ensureView: () => Promise<unknown>
  jobSnapshot: (j: JobRecord) => Record<string, unknown>
  jobs: Map<string, JobRecord>
  killWorkers: () => void
  ledgerList: (limit?: number) => Array<Record<string, unknown>>
  ledgerState: () => Record<string, unknown>
  metrics: EngineApiMetrics
  perfCall: (op: string, payload?: unknown) => Promise<unknown>
  runSnapshot: (r: Record<string, unknown>) => Record<string, unknown>
  runs: Map<string, unknown>
}

export function createEngineApi(E: EngineApiDeps) {
  return {
    ...createMetricsApi(E),
    ...createDoctorApi(E),
    ...createActApi(E),
    ...createLoopApi(E),
    ...createFrameApi(E),
    ...createViewApi(E),
    ...createHostApi(E),
  }
}
