// 作业层入口（拆分）：start/status/collect/wait/kill/list
import { createLedger } from '../ledger/index.js'
import type { JobsDeps } from './types.js'
import { jobDir, jobArtifacts, jobSnapshot, tailFile } from './snapshot.js'
import { jobStart, jobStatus, jobCollect, jobWait, jobList } from './api.js'
import type { JobsCtx } from './api.js'
import { jobKill } from './kill.js'

export type { JobsDeps, JobRecord } from './types.js'

export function createJobsApi(E: JobsDeps) {
  const ledger = E.ledger || createLedger(E)
  const jobs = new Map<string, import('./types.js').JobRecord>()
  const ctx: JobsCtx = { E, jobs, ledger }
  return {
    jobs,
    jobStart: (opts: Record<string, unknown> = {}) => jobStart(ctx, opts),
    jobSnapshot,
    jobStatus: (id: unknown) => jobStatus(ctx, id),
    jobCollect: (id: unknown, tail?: number) => jobCollect(ctx, id, tail),
    jobWait: (id: unknown, timeoutMs?: number) => jobWait(ctx, id, timeoutMs),
    jobKill: (id: unknown) => jobKill(ctx, id),
    jobList: () => jobList(ctx),
    jobDir,
    jobArtifacts,
    tailFile,
    ledger,
  }
}
