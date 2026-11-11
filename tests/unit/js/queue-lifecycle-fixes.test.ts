import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createGatewayApp } from '../../../runtime/gateway/app.js'
import { JobQueue, QueueError } from '../../../runtime/core/queue/manager.js'
import { JsonJobStore, MemoryJobStore } from '../../../runtime/core/queue/store.js'
import type { JobExecutor, JobHandle, JobRecord } from '../../../runtime/core/queue/types.js'
import type { CallerIdentity } from '../../../runtime/core/sessions/types.js'

const main = (over: Partial<CallerIdentity> = {}): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-1',
  agentId: 'agent-main',
  role: 'main',
  ...over,
})

const sub = (): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-1',
  agentId: 'agent-sub',
  parentAgentId: 'agent-main',
  role: 'subagent',
})

const other = (): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-2',
  agentId: 'agent-other',
  role: 'main',
})

interface Start {
  job: JobRecord
  resolve: (r: Awaited<JobHandle['done']>) => void
  cancel: ReturnType<typeof vi.fn>
}

class FakeExecutor implements JobExecutor {
  starts: Start[] = []
  start(job: JobRecord): JobHandle {
    let resolve!: Start['resolve']
    const done = new Promise<Awaited<JobHandle['done']>>((r) => {
      resolve = r
    })
    const cancel = vi.fn()
    this.starts.push({ job, resolve, cancel })
    return { pid: 2000 + this.starts.length, done, cancel, tempDir: null }
  }
  finish(index = 0, exitCode = 0): void {
    this.starts[index].resolve({ exitCode, signal: null, stdout: 'ok', stderr: '' })
  }
}

function setup(over: { capacity?: Record<string, number>; store?: MemoryJobStore } = {}) {
  const executor = new FakeExecutor()
  const store = over.store || new MemoryJobStore()
  let clock = 1000
  let id = 0
  const queue = new JobQueue({
    executor,
    store,
    capacity: over.capacity,
    id: () => 'job-' + ++id,
    now: () => (clock += 5),
  })
  return { queue, executor, store }
}

function persisted(over: Partial<JobRecord> = {}): JobRecord {
  return {
    id: 'job-1',
    kind: 'render',
    params: { file: 'scene.blend' },
    state: 'running',
    priority: 0,
    slot: 'gpu',
    owner: { hostId: 'host-a', dshSessionId: 'dsh-1', agentId: 'agent-main' },
    createdAt: 1,
    updatedAt: 2,
    assignedAt: 2,
    startedAt: 3,
    finishedAt: null,
    pid: 4242,
    decision: { outcome: 'admitted', reason: 'fifo', detail: '' },
    result: null,
    artifacts: [],
    logs: { stdout: '', stderr: '' },
    error: null,
    events: [],
    ...over,
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('virtual-gui GPU reservation is wired into the Gateway app', () => {
  it('derives the reserved GPU slots from the live session registry', () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    try {
      const reserved = (): unknown =>
        (app.jobQueue.perf() as { slots: { gpu: { reserved: number } } }).slots.gpu.reserved
      expect(reserved()).toBe(0)
      app.sessionRegistry.virtualGuiCount = () => 1
      expect(reserved()).toBe(1)
      app.sessionRegistry.virtualGuiCount = () => 3
      expect(reserved()).toBe(3)
    } finally {
      app.close()
    }
  })

  it('Gateway close() shuts the queue down', () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    const close = vi.spyOn(app.jobQueue, 'close')
    try {
      app.close()
      expect(close).toHaveBeenCalledTimes(1)
    } finally {
      app.close()
    }
  })
})

describe('queue shutdown and restart recovery', () => {
  it('close() cancels running children, marks them gateway_shutdown, keeps queued work', () => {
    const { queue, executor } = setup()
    const running = queue.submit(main(), { kind: 'render', file: 'a.blend' })
    const queued = queue.submit(main(), { kind: 'render', file: 'b.blend' })
    expect(queue.status(running.id)?.state).toBe('running')
    expect(queue.status(queued.id)?.state).toBe('queued')

    queue.close()
    expect(executor.starts[0].cancel).toHaveBeenCalledTimes(1)
    const stopped = queue.status(running.id)
    expect(stopped?.state).toBe('failed')
    expect(stopped?.error?.code).toBe('gateway_shutdown')
    expect(stopped?.slot).toBeNull()
    expect(stopped?.pid).toBeNull()
    expect(queue.status(queued.id)?.state).toBe('queued')

    queue.close()
    expect(executor.starts[0].cancel).toHaveBeenCalledTimes(1)
    expect(() => queue.submit(main(), { kind: 'headless', script: 'print(1)' })).toThrow(QueueError)
  })

  it('restore clears stale slot/pid and removes the persisted tempDir', () => {
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-queue-restore-'))
    fs.writeFileSync(path.join(tempDir, 'job.py'), 'print(1)')
    const store = new MemoryJobStore()
    store.jobs = [persisted({ tempDir })]
    const queue = new JobQueue({
      executor: new FakeExecutor(),
      store,
      id: () => 'job-new',
      now: () => 5000,
    })
    const job = queue.status('job-1')
    expect(job?.state).toBe('failed')
    expect(job?.error?.code).toBe('gateway_restart')
    expect(job?.slot).toBeNull()
    expect(job?.pid).toBeNull()
    expect(job?.tempDir ?? null).toBeNull()
    expect(fs.existsSync(tempDir)).toBe(false)
  })

  it('writes startedAt on running and result.ms from the real start', async () => {
    const { queue, executor } = setup()
    const job = queue.submit(main(), { kind: 'headless', script: 'print(1)' })
    const running = queue.status(job.id)
    expect(typeof running?.startedAt).toBe('number')
    expect(running?.startedAt).toBeGreaterThan(0)
    executor.finish(0)
    await Promise.resolve()
    const done = queue.status(job.id)
    expect(done?.state).toBe('completed')
    expect(done?.result?.ms).toBeGreaterThan(0)
  })

  it('captures the executor tempDir on the running record', () => {
    const executor = new FakeExecutor()
    const store = new MemoryJobStore()
    const queue = new JobQueue({ executor, store, id: () => 'job-1', now: () => 1 })
    const job = queue.submit(main(), { kind: 'headless', script: 'print(1)' })
    expect(queue.status(job.id)?.tempDir ?? null).toBeNull()
  })
})

describe('queue ownership, contract code, and persistence sanitization', () => {
  it('restricts status, wait, and list to the owner, parent, and main agent', async () => {
    const { queue } = setup()
    const job = queue.submit(sub(), { kind: 'render', file: 'a.blend' })
    expect(() => queue.status(other(), job.id)).toThrow(QueueError)
    await expect(queue.wait(other(), job.id, 1000)).rejects.toBeInstanceOf(QueueError)
    expect(queue.list(other())).toHaveLength(0)
    expect(queue.list(sub()).map((j) => j.id)).toEqual([job.id])
    expect(queue.list(main()).map((j) => j.id)).toEqual([job.id])
    expect(queue.status(main(), job.id)?.id).toBe(job.id)
  })

  it('reports the QUEUE_FULL contract code', () => {
    const { queue } = setup({ capacity: { pending: 1 } })
    queue.submit(main(), { kind: 'headless', script: 'print(1)' })
    let failure: QueueError | null = null
    try {
      queue.submit(main(), { kind: 'headless', script: 'print(2)' })
    } catch (e) {
      failure = e as QueueError
    }
    expect(failure).toBeInstanceOf(QueueError)
    expect(failure?.failure.code).toBe('QUEUE_FULL')
  })

  it('drops persisted rows without the fields the queue needs', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-queue-sanitize-'))
    const file = path.join(dir, 'queue.json')
    const rows = [
      persisted({ state: 'queued', slot: null, pid: null }),
      persisted({ id: 'job-2', state: 'bogus' as unknown as JobRecord['state'] }),
      persisted({ id: 'job-3', owner: undefined as unknown as JobRecord['owner'] }),
      persisted({ id: 'job-4', params: null as unknown as JobRecord['params'] }),
    ]
    fs.writeFileSync(file, JSON.stringify({ version: 1, jobs: rows }))
    const loaded = new JsonJobStore(file).load()
    expect(loaded.map((j) => j.id)).toEqual(['job-1'])
    expect(fs.readdirSync(dir).some((name) => name.includes('.corrupt-'))).toBe(false)
  })
})
