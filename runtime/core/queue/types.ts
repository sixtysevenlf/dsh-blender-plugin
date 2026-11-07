// Job-queue types: one bounded queue for render and headless work.
//
// The queue is deliberately independent from the legacy synchronous headless
// path: a submitted job gets one id and is observed through the same record
// until it reaches a terminal state.

import type { AvailabilityFailure } from '../availability.js'
import type { JobOwner } from '../sessions/job-owner.js'

export type JobKind = 'render' | 'headless'
export type JobState = 'queued' | 'assigned' | 'running' | 'completed' | 'failed' | 'canceled'
export type JobSlot = 'gpu' | 'cpu'

export const JOB_STATES: readonly JobState[] = [
  'queued',
  'assigned',
  'running',
  'completed',
  'failed',
  'canceled',
]
export const TERMINAL_STATES: readonly JobState[] = ['completed', 'failed', 'canceled']

export interface JobEvent {
  at: number
  from: JobState | null
  to: JobState
  slot: JobSlot | null
  pid: number | null
  reason: string
}

export interface JobDecision {
  /** Why this job is (or is not yet) on a slot. */
  outcome: 'admitted' | 'waiting'
  reason: 'fifo' | 'priority' | 'quota' | 'capacity' | 'virtual_gui'
  detail: string
}

export interface JobLogs {
  stdout: string
  stderr: string
}

export interface JobResult {
  exitCode: number | null
  signal: string | null
  ms: number
  parsed?: unknown
}

/** Persisted job shape. Handles and timers are never part of this record. */
export interface JobRecord {
  id: string
  kind: JobKind
  params: Record<string, unknown>
  state: JobState
  priority: number
  slot: JobSlot | null
  owner: JobOwner
  createdAt: number
  updatedAt: number
  assignedAt: number | null
  startedAt: number | null
  finishedAt: number | null
  pid: number | null
  decision: JobDecision | null
  result: JobResult | null
  artifacts: string[]
  logs: JobLogs
  error: AvailabilityFailure | null
  events: JobEvent[]
}

export interface JobExecutionResult {
  exitCode: number | null
  signal: string | null
  stdout: string
  stderr: string
  artifacts?: string[]
  parsed?: unknown
  error?: AvailabilityFailure
}

export interface JobHandle {
  pid: number | null
  done: Promise<JobExecutionResult>
  cancel: () => void
}

export interface JobExecutor {
  start: (job: JobRecord) => JobHandle
}

export interface JobStore {
  load: () => JobRecord[]
  save: (jobs: JobRecord[]) => void
}

export interface QueueCapacity {
  pending: number
  gpu: number
  cpu: number
  ownerQuota: number
}

export const DEFAULT_CAPACITY: QueueCapacity = { pending: 64, gpu: 1, cpu: 2, ownerQuota: 8 }

export interface SubmitInput extends Record<string, unknown> {
  kind?: JobKind | string
  priority?: number
}
