// Queue admission policy: FIFO within a priority band, bounded slots, owner quota.
//
// The policy is pure so tests can prove that a running GPU render is never
// oversold and that a high-priority job only wins at the next slot boundary.
import type { JobDecision, JobKind, JobRecord, JobSlot, QueueCapacity } from './types.js'
import { TERMINAL_STATES } from './types.js'

export function isTerminal(job: JobRecord): boolean {
  return (TERMINAL_STATES as readonly string[]).includes(job.state)
}

export function slotFor(kind: JobKind, params: Record<string, unknown>): JobSlot {
  if (kind === 'render') return 'gpu'
  return params.gpu === true ? 'gpu' : 'cpu'
}

export function pendingCount(jobs: JobRecord[]): number {
  return jobs.filter((j) => j.state === 'queued' || j.state === 'assigned' || j.state === 'running')
    .length
}

export function slotUse(jobs: JobRecord[], slot: JobSlot): number {
  return jobs.filter((j) => j.slot === slot && !isTerminal(j)).length
}

export function ownerUse(jobs: JobRecord[], ownerKey: string): number {
  // Quota bounds concurrency, not the number of waiting rows: a queued job that
  // counts against its own owner can never be admitted.
  return jobs.filter(
    (j) => (j.state === 'assigned' || j.state === 'running') && ownerKeyOf(j) === ownerKey,
  ).length
}

export function ownerKeyOf(job: JobRecord): string {
  return [job.owner.hostId, job.owner.dshSessionId, job.owner.agentId].join('\u0000')
}

function canUseSlot(
  job: JobRecord,
  jobs: JobRecord[],
  capacity: QueueCapacity,
  reservedGpu: number,
): boolean {
  const slot = slotFor(job.kind, job.params)
  if (slot === 'gpu') return slotUse(jobs, 'gpu') < Math.max(0, capacity.gpu - reservedGpu)
  return slotUse(jobs, 'cpu') < capacity.cpu
}

export interface Admission {
  job: JobRecord | null
  decision: JobDecision
}

/** Return the next job that may start, or a waiting explanation for the head. */
export function nextAdmission(
  jobs: JobRecord[],
  capacity: QueueCapacity,
  reservedGpu = 0,
): Admission {
  const queued = jobs
    .map((job, index) => ({ job, index }))
    .filter((x) => x.job.state === 'queued')
    .sort(
      (a, b) =>
        b.job.priority - a.job.priority || a.job.createdAt - b.job.createdAt || a.index - b.index,
    )
  if (queued.length === 0) return { job: null, decision: waiting('capacity', 'no queued jobs') }
  const fifoHead = queued.reduce((best, row) =>
    row.job.createdAt < best.job.createdAt ||
    (row.job.createdAt === best.job.createdAt && row.index < best.index)
      ? row
      : best,
  )
  let blocked: JobDecision | null = null
  for (const row of queued) {
    const job = row.job
    if (ownerUse(jobs, ownerKeyOf(job)) >= capacity.ownerQuota) {
      blocked = blocked || waiting('quota', 'owner quota is full')
      continue
    }
    if (!canUseSlot(job, jobs, capacity, reservedGpu)) {
      const reason: JobDecision['reason'] =
        reservedGpu > 0 && slotFor(job.kind, job.params) === 'gpu' ? 'virtual_gui' : 'capacity'
      blocked = blocked || waiting(reason, 'target slot is busy')
      continue
    }
    return {
      job,
      decision: {
        outcome: 'admitted',
        reason: row === fifoHead ? 'fifo' : 'priority',
        detail: job.slot + ' slot admitted',
      },
    }
  }
  return {
    job: null,
    decision: blocked || waiting('capacity', 'no queued job fits the current slots'),
  }
}

export function waiting(reason: JobDecision['reason'], detail: string): JobDecision {
  return { outcome: 'waiting', reason, detail }
}

export function explainWaiting(
  job: JobRecord,
  jobs: JobRecord[],
  capacity: QueueCapacity,
  reservedGpu: number,
): JobDecision {
  if (job.state !== 'queued') return job.decision || waiting('capacity', 'job is not queued')
  if (ownerUse(jobs, ownerKeyOf(job)) >= capacity.ownerQuota)
    return waiting('quota', 'owner quota is full')
  if (!canUseSlot(job, jobs, capacity, reservedGpu))
    return waiting(
      reservedGpu > 0 && slotFor(job.kind, job.params) === 'gpu' ? 'virtual_gui' : 'capacity',
      'target slot is busy',
    )
  return waiting('fifo', 'waiting for an earlier job at the same priority')
}
