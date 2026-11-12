// Job queue manager: submission, admission, persistence, control.
import fs from 'node:fs'
import { randomUUID } from 'node:crypto'
import { availabilityFailure } from '../availability.js'
import type { AvailabilityFailure } from '../availability.js'
import type { CallerIdentity } from '../sessions/types.js'
import { canManageJob, jobOwnerFromCaller, jobPermissionFailure } from '../sessions/job-owner.js'
import type { JobManageAction } from '../sessions/job-owner.js'
import { JsonJobStore } from './store.js'
import { planRenderCacheWithReason } from './render-cache.js'
import { RENDER_PASSES_MANIFEST_VERSION } from './render-passes-script.js'
import type {
  PromoteFrame,
  RenderCache,
  RenderCacheEntry,
  RenderCachePlan,
} from './render-cache.js'
import { DEFAULT_CAPACITY, TERMINAL_STATES } from './types.js'
import type {
  JobCacheState,
  JobExecutor,
  JobHandle,
  JobRecord,
  JobState,
  JobStore,
  QueueCapacity,
  SubmitInput,
} from './types.js'
import { explainWaiting, nextAdmission, pendingCount, slotFor, slotUse } from './scheduler.js'

const LOG_CAP = 65536
const WAIT_DEFAULT_MS = 120000
const WAIT_MAX_MS = 600000

export interface QueueOptions {
  executor: JobExecutor
  store?: JobStore
  capacity?: Partial<QueueCapacity>
  reservedGpu?: () => number
  now?: () => number
  id?: () => string
  /** Render-pass cache; null/absent keeps every submission on the old path. */
  renderCache?: RenderCache | null
}

export class QueueError extends Error {
  readonly failure: AvailabilityFailure
  constructor(failure: AvailabilityFailure) {
    super(failure.root_cause)
    this.name = 'QueueError'
    this.failure = failure
  }
}

/**
 * After promotion the manifests moved: point the executor's PASSES_DONE summary
 * at the cache/user-outdir copies so receipts never quote dead staging paths.
 */
function rewriteParsedFramePaths(parsed: unknown, frames: PromoteFrame[]): void {
  if (parsed === null || typeof parsed !== 'object') return
  const list = (parsed as { frames?: unknown }).frames
  if (!Array.isArray(list)) return
  const byFrame = new Map<number, PromoteFrame>()
  for (const frame of frames) byFrame.set(frame.frame, frame)
  for (const item of list) {
    if (item === null || typeof item !== 'object') continue
    const record = item as Record<string, unknown>
    const target = byFrame.get(Number(record.frame))
    if (!target) continue
    record.manifest = target.manifest
    const passes = record.passes
    if (passes === null || typeof passes !== 'object' || Array.isArray(passes)) continue
    for (const [name, meta] of Object.entries(passes as Record<string, unknown>)) {
      const finalPath = target.passes[name]
      if (!finalPath || meta === null || typeof meta !== 'object') continue
      ;(meta as Record<string, unknown>).path = finalPath
    }
  }
}

function terminalOf(state: JobState): boolean {
  return (TERMINAL_STATES as readonly string[]).includes(state)
}

function fileSize(file: string): number {
  try {
    return fs.statSync(file).size
  } catch {
    return 0
  }
}

/**
 * A cache hit skips the render, so the executor's PASSES_DONE summary never exists.
 * Build the same shape from the materialized frames to keep receipts identical.
 */
function hitSummary(frames: PromoteFrame[], plan: RenderCachePlan, outdir: string | null): unknown {
  const passSet = plan.inputs[0]?.passSet ?? []
  return {
    ok: true,
    manifestVersion: RENDER_PASSES_MANIFEST_VERSION,
    passes: passSet,
    frames: frames.map((frame) => ({
      frame: frame.frame,
      manifest: frame.manifest,
      passes: Object.fromEntries(
        Object.entries(frame.passes).map(([name, file]) => [
          name,
          { path: file, bytes: fileSize(file) },
        ]),
      ),
      bytes: frame.artifacts.reduce((sum, file) => sum + fileSize(file), 0),
      degraded: [],
      requestedPasses: passSet,
    })),
    degradedFrames: [],
    outdir,
  }
}

function queueFailure(
  code: string,
  rootCause: string,
  fix: string,
  evidence: Record<string, unknown> = {},
): AvailabilityFailure {
  return availabilityFailure({
    stage: 'queue',
    code,
    rootCause,
    fix,
    fallback: { kind: 'unavailable', summary: rootCause },
    evidence,
  })
}

export function failureForShutdown(): AvailabilityFailure {
  return availabilityFailure({
    stage: 'queue',
    code: 'gateway_shutdown',
    rootCause: 'Gateway shut down while this job was assigned or running.',
    fix: 'Submit the job again after the Gateway is running.',
    fallback: { kind: 'unavailable', summary: 'resubmit the job' },
    evidence: { reason: 'gateway_shutdown' },
  })
}

export function failureForRestart(): AvailabilityFailure {
  return availabilityFailure({
    stage: 'queue',
    code: 'gateway_restart',
    rootCause: 'Gateway restarted while this job was assigned or running.',
    fix: 'Submit the job again; running work is never replayed automatically.',
    fallback: { kind: 'unavailable', summary: 'resubmit the job' },
    evidence: { reason: 'gateway_restart' },
  })
}

export class JobQueue {
  private readonly store: JobStore
  private readonly executor: JobExecutor
  private readonly capacity: QueueCapacity
  private readonly reservedGpu: () => number
  private readonly now: () => number
  private readonly newId: () => string
  private readonly renderCache: RenderCache | null
  private readonly jobs: JobRecord[] = []
  private readonly handles = new Map<string, JobHandle>()
  /** Why a pass render bypassed the cache (too many frames); reported on finish. */
  private readonly cacheSkips = new Map<string, string>()
  private closed = false

  constructor(opts: QueueOptions) {
    this.executor = opts.executor
    this.store = opts.store || new JsonJobStore()
    this.capacity = { ...DEFAULT_CAPACITY, ...opts.capacity }
    this.reservedGpu = opts.reservedGpu || (() => 0)
    this.now = opts.now || (() => Date.now())
    this.newId = opts.id || (() => 'job-' + randomUUID())
    this.renderCache = opts.renderCache ?? null
    if (this.renderCache) {
      // Settings only take effect at Gateway start; stale staging is orphaned work.
      try {
        this.renderCache.startupCleanup()
      } catch {
        /* a broken cache must never block the queue */
      }
    }
    this.restore()
  }

  private restore(): void {
    const loaded = this.store.load()
    for (const job of loaded) {
      if (job.state === 'assigned' || job.state === 'running') {
        // The old process is gone: drop its temp dir and clear the slot/pid so a
        // restored record can never look live or keep a GPU slot occupied.
        this.discardTempDir(job)
        this.discardCacheStaging(job)
        job.slot = null
        job.pid = null
        job.tempDir = null
        this.transition(job, 'failed', 'gateway_restart')
        job.error = failureForRestart()
        job.finishedAt = this.now()
      }
      this.jobs.push(job)
    }
    if (loaded.length > 0) this.persist()
    this.pump()
  }

  submit(caller: CallerIdentity, input: SubmitInput): JobRecord {
    if (this.closed)
      throw new QueueError(
        queueFailure(
          'queue_closed',
          'The Gateway is shutting down and no longer accepts jobs.',
          'Wait for the Gateway to restart, then submit again.',
        ),
      )
    const kind = input.kind === 'render' || input.kind === 'headless' ? input.kind : null
    if (!kind)
      throw new QueueError(
        queueFailure(
          'queue_invalid_kind',
          'kind must be render or headless',
          'Pass kind=render or kind=headless.',
        ),
      )
    if (kind === 'render' && (input.script !== undefined || input.script_file !== undefined))
      throw new QueueError(
        queueFailure(
          'queue_render_script',
          'render does not execute Python scripts',
          'Use kind=headless for a script.',
        ),
      )
    // The cache lookup runs before admission: a hit needs neither a GPU slot nor
    // a pending slot. A miss records its staging dir and continues normally.
    const id = this.newId()
    let cachePlan: RenderCachePlan | null = null
    let cacheSkip: string | null = null
    if (this.renderCache && kind === 'render') {
      const planned = planRenderCacheWithReason({ ...input })
      cachePlan = planned.plan
      if (!cachePlan) cacheSkip = planned.skipped
    }
    let cacheState: JobCacheState | null = null
    if (cachePlan && this.renderCache) {
      const cached = this.renderCache.lookupAll(cachePlan.inputs)
      if (cached.hit) {
        const hitJob = this.buildCacheHitJob(
          id,
          kind,
          input,
          cachePlan,
          cached.entries,
          caller,
          this.renderCache,
        )
        this.events(hitJob, null, 'completed', 'render cache hit')
        this.jobs.push(hitJob)
        this.persist()
        return this.snapshot(hitJob)
      }
      cacheState = {
        keys: cachePlan.keys,
        frames: cachePlan.frames,
        scene: cachePlan.scene,
        stagingDir: this.renderCache.stagingDir(id),
        userOutdir:
          typeof input.outdir === 'string' && input.outdir.trim() ? String(input.outdir) : null,
        hit: false,
        corrupt: cached.corrupt,
      }
    }
    if (pendingCount(this.jobs) >= this.capacity.pending) {
      throw new QueueError(
        queueFailure(
          'QUEUE_FULL',
          'The job queue is full.',
          'Wait for a running job to finish, or raise the documented pending limit.',
          { pending: pendingCount(this.jobs), limit: this.capacity.pending },
        ),
      )
    }
    const job: JobRecord = {
      id,
      kind,
      params: { ...input },
      state: 'queued',
      priority: clampPriority(input.priority),
      slot: null,
      owner: jobOwnerFromCaller(caller),
      createdAt: this.now(),
      updatedAt: this.now(),
      assignedAt: null,
      startedAt: null,
      finishedAt: null,
      pid: null,
      decision: null,
      result: null,
      artifacts: [],
      logs: { stdout: '', stderr: '' },
      error: null,
      events: [],
    }
    delete job.params.kind
    job.params.kind = kind
    if (cacheState) {
      // Pass output is written to staging first; promotion decides its final home.
      job.cache = cacheState
      job.params.outdir = cacheState.stagingDir
    } else if (cacheSkip) {
      this.cacheSkips.set(job.id, cacheSkip)
    }
    this.events(job, null, 'queued', 'submitted')
    this.jobs.push(job)
    this.persist()
    this.pump()
    return this.snapshot(job)
  }

  private buildCacheHitJob(
    id: string,
    kind: 'render' | 'headless',
    input: SubmitInput,
    cachePlan: RenderCachePlan,
    entries: RenderCacheEntry[],
    caller: CallerIdentity,
    cache: RenderCache,
  ): JobRecord {
    const now = this.now()
    const userOutdir =
      typeof input.outdir === 'string' && input.outdir.trim() ? String(input.outdir) : null
    // A hit must still deliver the frames where the caller asked for them: the
    // cache directory is an implementation detail, not the receipt location.
    const artifacts: string[] = []
    const frames: PromoteFrame[] = []
    for (const entry of entries) {
      const copied = userOutdir ? cache.materialize(entry, userOutdir) : null
      if (copied) {
        frames.push(copied)
        artifacts.push(...copied.artifacts)
      } else {
        artifacts.push(...entry.artifacts)
      }
    }
    const hitJob: JobRecord = {
      id,
      kind,
      params: { ...input, kind },
      state: 'completed',
      priority: clampPriority(input.priority),
      slot: null,
      owner: jobOwnerFromCaller(caller),
      createdAt: now,
      updatedAt: now,
      assignedAt: null,
      startedAt: null,
      finishedAt: now,
      pid: null,
      decision: null,
      result: {
        exitCode: 0,
        signal: null,
        ms: 0,
        ...(frames.length > 0 ? { parsed: hitSummary(frames, cachePlan, userOutdir) } : {}),
        cache: { hit: true, keys: cachePlan.keys, entries: entries.length },
      },
      artifacts,
      logs: { stdout: '', stderr: '' },
      error: null,
      events: [],
      cache: {
        keys: cachePlan.keys,
        frames: cachePlan.frames,
        scene: cachePlan.scene,
        stagingDir: '',
        userOutdir,
        hit: true,
        corrupt: false,
      },
    }
    hitJob.params.kind = kind
    if (!userOutdir && frames.length === 0)
      hitJob.artifacts = entries.flatMap((entry) => entry.artifacts)
    return hitJob
  }

  status(id: string): JobRecord | null
  status(caller: CallerIdentity, id: string): JobRecord | null
  status(callerOrId: string | CallerIdentity, maybeId?: string): JobRecord | null {
    const id = typeof callerOrId === 'string' ? callerOrId : String(maybeId || '')
    const job = this.find(id)
    if (!job) return null
    if (typeof callerOrId !== 'string') this.authorize(callerOrId, job, 'status')
    return this.snapshot(job)
  }

  async wait(
    caller: CallerIdentity,
    id: string,
    timeoutMs = WAIT_DEFAULT_MS,
  ): Promise<Record<string, unknown>> {
    const budget = Math.max(1000, Math.min(WAIT_MAX_MS, Number(timeoutMs) || WAIT_DEFAULT_MS))
    const t0 = Date.now()
    for (;;) {
      const job = this.find(id)
      if (!job)
        throw new QueueError(
          queueFailure(
            'queue_unknown_job',
            'No job has id ' + id,
            'Call op=list and use an existing jobId.',
          ),
        )
      this.authorize(caller, job, 'status')
      if (terminalOf(job.state))
        return { job: this.snapshot(job), waitedMs: Date.now() - t0, waitTimedOut: false }
      if (Date.now() - t0 >= budget)
        return { job: this.snapshot(job), waitedMs: Date.now() - t0, waitTimedOut: true }
      await new Promise<void>((resolve) => setTimeout(resolve, 100))
    }
  }

  collect(caller: CallerIdentity, id: string, tail = 4000): Record<string, unknown> {
    const job = this.find(id)
    if (!job)
      throw new QueueError(
        queueFailure(
          'queue_unknown_job',
          'No job has id ' + id,
          'Call op=list and use an existing jobId.',
        ),
      )
    this.authorize(caller, job, 'collect')
    const snap = this.snapshot(job)
    return {
      job: {
        ...snap,
        logs: { stdout: tailText(job.logs.stdout, tail), stderr: tailText(job.logs.stderr, tail) },
      },
    }
  }

  cancel(caller: CallerIdentity, id: string): Record<string, unknown> {
    const job = this.find(id)
    if (!job)
      throw new QueueError(
        queueFailure(
          'queue_unknown_job',
          'No job has id ' + id,
          'Call op=list and use an existing jobId.',
        ),
      )
    this.authorize(caller, job, 'cancel')
    if (terminalOf(job.state))
      return { job: this.snapshot(job), canceled: false, alreadyFinished: true }
    const handle = this.handles.get(job.id)
    this.transition(job, 'canceled', 'cancel requested by caller')
    job.finishedAt = this.now()
    job.pid = null
    job.slot = null
    this.discardTempDir(job)
    this.discardCacheStaging(job)
    job.tempDir = null
    this.handles.delete(job.id)
    if (handle) {
      try {
        handle.cancel()
      } catch {
        /* cancellation is idempotent */
      }
    }
    this.persist()
    this.pump()
    return { job: this.snapshot(job), canceled: true, alreadyFinished: false }
  }

  list(filter?: JobListFilter): JobRecord[]
  list(caller: CallerIdentity, filter?: JobListFilter): JobRecord[]
  list(
    callerOrFilter: CallerIdentity | JobListFilter = {},
    maybeFilter: JobListFilter = {},
  ): JobRecord[] {
    const caller = isCallerIdentity(callerOrFilter) ? callerOrFilter : null
    const filter = caller ? maybeFilter : (callerOrFilter as JobListFilter)
    return this.jobs
      .filter((job) => !caller || canManageJob(caller, job.owner).allowed)
      .filter((job) => !filter.state || job.state === filter.state)
      .filter((job) => !filter.kind || job.kind === filter.kind)
      .filter((job) => !filter.owner || job.owner.dshSessionId === filter.owner)
      .map((job) => this.snapshot(job))
  }

  perf(): Record<string, unknown> {
    const byState: Record<string, number> = {}
    for (const state of ['queued', 'assigned', 'running', 'completed', 'failed', 'canceled'])
      byState[state] = this.jobs.filter((job) => job.state === state).length
    return {
      pending: pendingCount(this.jobs),
      byState,
      capacity: { ...this.capacity },
      slots: {
        gpu: {
          used: slotUse(this.jobs, 'gpu'),
          total: this.capacity.gpu,
          reserved: this.reservedGpu(),
        },
        cpu: { used: slotUse(this.jobs, 'cpu'), total: this.capacity.cpu },
      },
      oldestQueuedAt: this.oldestQueuedAt(),
    }
  }

  private oldestQueuedAt(): number | null {
    const queued = this.jobs.filter((job) => job.state === 'queued')
    return queued.length === 0 ? null : Math.min(...queued.map((job) => job.createdAt))
  }

  private pump(): void {
    if (this.closed) return
    for (;;) {
      const admission = nextAdmission(this.jobs, this.capacity, this.reservedGpu())
      if (!admission.job) {
        this.recordWaiting()
        return
      }
      const job = admission.job
      job.slot = slotFor(job.kind, job.params)
      job.decision = admission.decision
      this.transition(job, 'assigned', admission.decision.reason)
      job.assignedAt = this.now()
      this.persist()
      this.start(job)
    }
  }

  private recordWaiting(): void {
    const head = this.jobs
      .filter((job) => job.state === 'queued')
      .sort((a, b) => b.priority - a.priority || a.createdAt - b.createdAt)[0]
    if (!head) return
    const next = explainWaiting(head, this.jobs, this.capacity, this.reservedGpu())
    if (JSON.stringify(head.decision) === JSON.stringify(next)) return
    head.decision = next
    this.persist()
  }

  private start(job: JobRecord): void {
    let handle: JobHandle
    try {
      handle = this.executor.start(this.snapshot(job))
    } catch (e) {
      this.fail(
        job,
        availabilityFailure({
          stage: 'spawn',
          code: 'job_spawn_failed',
          rootCause: e instanceof Error ? e.message : String(e),
          fix: 'Check the Blender executable and the submitted parameters, then retry.',
          fallback: { kind: 'unavailable', summary: 'retry the job' },
          evidence: { kind: job.kind, params: job.params },
        }),
      )
      return
    }
    this.handles.set(job.id, handle)
    job.pid = handle.pid
    job.startedAt = this.now()
    job.tempDir = handle.tempDir ?? null
    this.transition(job, 'running', 'slot acquired')
    this.persist()
    void handle.done.then(
      (result) => this.finish(job, result),
      (error) =>
        this.fail(
          job,
          availabilityFailure({
            stage: job.kind === 'render' ? 'render' : 'spawn',
            code: 'job_execution_failed',
            rootCause: error instanceof Error ? error.message : String(error),
            fix: 'Read the job logs, fix the reported cause, then resubmit.',
            fallback: { kind: 'unavailable', summary: 'resubmit the job' },
            evidence: { jobId: job.id },
          }),
        ),
    )
  }

  private finish(job: JobRecord, result: Awaited<JobHandle['done']>): void {
    if (terminalOf(job.state)) return
    this.handles.delete(job.id)
    job.logs = { stdout: cap(result.stdout), stderr: cap(result.stderr) }
    job.result = {
      exitCode: result.exitCode,
      signal: result.signal,
      ms: Math.max(0, this.now() - (job.startedAt ?? job.assignedAt ?? this.now())),
      ...(result.parsed === undefined ? {} : { parsed: result.parsed }),
    }
    job.artifacts = result.artifacts || []
    job.pid = null
    job.slot = null
    job.tempDir = null
    if (result.error) {
      job.error = result.error
      this.transition(job, 'failed', result.error.code)
      this.attachCacheFailure(job)
      this.afterFinish(job)
      return
    }
    if (result.exitCode !== 0) {
      job.error = availabilityFailure({
        stage: job.kind === 'render' ? 'render' : 'spawn',
        code: 'job_exit_nonzero',
        rootCause: 'Blender exited with code ' + String(result.exitCode) + '.',
        fix: 'Read the job logs and fix the reported Blender error.',
        fallback: { kind: 'unavailable', summary: 'inspect logs then resubmit' },
        evidence: { exitCode: result.exitCode, signal: result.signal },
      })
      this.transition(job, 'failed', 'process exited nonzero')
      this.attachCacheFailure(job)
      this.afterFinish(job)
      return
    }
    this.attachCacheSkip(job)
    const cache = this.renderCache
    if (!cache || !job.cache) {
      this.transition(job, 'completed', 'process exited 0')
      this.afterFinish(job)
      return
    }
    // Promotion is asynchronous (it yields to the event loop); the terminal
    // transition waits for it so a waiter that sees `completed` also sees the
    // real artifact locations and the cache verdict.
    void this.promote(job).then(
      () => {
        this.transition(job, 'completed', 'process exited 0')
        this.afterFinish(job)
      },
      () => {
        // A cache failure never turns a successful render into a failed job.
        if (job.result)
          job.result.cache = {
            hit: false,
            keys: job.cache?.keys || [],
            skipped: 'promote_failed',
            corrupt: false,
          }
        this.transition(job, 'completed', 'process exited 0')
        this.afterFinish(job)
      },
    )
  }

  /** Shared tail of every terminal path: stamp the finish time and let the queue move. */
  private afterFinish(job: JobRecord): void {
    this.cacheSkips.delete(job.id)
    job.finishedAt = this.now()
    this.persist()
    this.pump()
  }

  /** A pass render the cache refused still says why on the receipt. */
  private attachCacheSkip(job: JobRecord): void {
    const skipped = this.cacheSkips.get(job.id)
    if (!skipped || job.cache || !job.result) return
    job.result.cache = { hit: false, keys: [], skipped, corrupt: false }
  }

  /** A failed/canceled render drops its staging and never touches an entry. */
  private attachCacheFailure(job: JobRecord): void {
    const cache = this.renderCache
    const state = job.cache
    if (!cache || !state) return
    cache.discardStaging(job.id)
    if (job.result) job.result.cache = { hit: false, keys: state.keys, corrupt: state.corrupt }
  }

  /**
   * Promote a completed render into the cache: only a clean, complete manifest
   * set may enter it, and the artifacts move to their final home either way.
   */
  private async promote(job: JobRecord): Promise<void> {
    const cache = this.renderCache
    const state = job.cache
    if (!cache || !state) return
    const promoted = await cache.promoteJob({
      jobId: job.id,
      keys: state.keys,
      frames: state.frames,
      scene: state.scene,
      userOutdir: state.userOutdir,
    })
    if (promoted.artifacts.length > 0) job.artifacts = promoted.artifacts
    else
      job.artifacts = job.artifacts.filter(
        (file) => !file.startsWith(state.stagingDir) || fs.existsSync(file),
      )
    if (job.result && promoted.frames.length > 0)
      rewriteParsedFramePaths(job.result.parsed, promoted.frames)
    if (job.result)
      job.result.cache = {
        hit: false,
        keys: state.keys,
        entries: promoted.entries,
        ...(promoted.skipped ? { skipped: promoted.skipped } : {}),
        corrupt: state.corrupt,
      }
  }

  private discardCacheStaging(job: JobRecord): void {
    if (!this.renderCache || !job.cache) return
    // A cache hit has no staging dir; an empty id would resolve to the root.
    if (!job.cache.stagingDir) return
    this.renderCache.discardStaging(job.id)
  }

  private fail(job: JobRecord, failure: AvailabilityFailure): void {
    if (terminalOf(job.state)) return
    this.handles.delete(job.id)
    job.error = failure
    job.pid = null
    job.slot = null
    this.discardTempDir(job)
    this.discardCacheStaging(job)
    job.tempDir = null
    this.transition(job, 'failed', failure.code)
    job.finishedAt = this.now()
    this.persist()
    this.pump()
  }

  /** Gateway shutdown: kill running children, mark them failed, keep queued work. */
  close(): void {
    if (this.closed) return
    this.closed = true
    let dirty = false
    for (const job of this.jobs) {
      if (job.state !== 'assigned' && job.state !== 'running') continue
      const handle = this.handles.get(job.id)
      this.handles.delete(job.id)
      if (handle) {
        try {
          handle.cancel()
        } catch {
          /* cancellation is idempotent */
        }
      }
      job.slot = null
      job.pid = null
      this.discardTempDir(job)
      this.discardCacheStaging(job)
      job.tempDir = null
      job.error = failureForShutdown()
      this.transition(job, 'failed', 'gateway_shutdown')
      job.finishedAt = this.now()
      dirty = true
    }
    if (dirty) {
      try {
        this.persist()
      } catch {
        /* a shutdown must not throw on a broken state file */
      }
    }
  }

  private discardTempDir(job: JobRecord): void {
    const dir = job.tempDir
    if (!dir) return
    try {
      fs.rmSync(dir, { recursive: true, force: true })
    } catch {
      /* temp cleanup is best effort */
    }
  }

  private transition(job: JobRecord, to: JobState, reason: string): void {
    const from = job.state
    job.state = to
    job.updatedAt = this.now()
    this.events(job, from, to, reason)
  }

  private events(job: JobRecord, from: JobState | null, to: JobState, reason: string): void {
    job.events.push({ at: this.now(), from, to, slot: job.slot, pid: job.pid, reason })
  }

  private authorize(caller: CallerIdentity, job: JobRecord, action: JobManageAction): void {
    const decision = canManageJob(caller, job.owner)
    if (!decision.allowed)
      throw new QueueError(
        queueFailure(
          'job_ownership_denied',
          jobPermissionFailure(decision, action),
          'Use the DSH session that submitted the job, its parent agent, or the main agent.',
          { action, reason: decision.reason },
        ),
      )
  }

  private find(id: string): JobRecord | null {
    return this.jobs.find((job) => job.id === id) || null
  }

  private snapshot(job: JobRecord): JobRecord {
    return JSON.parse(JSON.stringify(job)) as JobRecord
  }

  private persist(): void {
    this.store.save(this.jobs.map((job) => this.snapshot(job)))
  }
}

export interface JobListFilter {
  state?: string
  kind?: string
  owner?: string
}

function isCallerIdentity(value: unknown): value is CallerIdentity {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.hostId === 'string' &&
    typeof v.dshSessionId === 'string' &&
    typeof v.agentId === 'string'
  )
}

function clampPriority(value: unknown): number {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  return Math.max(-100, Math.min(100, Math.trunc(n)))
}

function cap(text: string): string {
  return text.length > LOG_CAP ? text.slice(-LOG_CAP) : text
}

function tailText(text: string, tail: number): string {
  const n = Math.max(0, Math.min(LOG_CAP, Number(tail) || 4000))
  return text.length > n ? text.slice(-n) : text
}
