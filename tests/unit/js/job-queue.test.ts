import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { JobQueue, QueueError } from '../../../runtime/core/queue/manager.js'
import { MemoryJobStore, JsonJobStore } from '../../../runtime/core/queue/store.js'
import type { JobExecutor, JobHandle, JobRecord } from '../../../runtime/core/queue/types.js'
import { JobSurface, callerFromHeaders } from '../../../runtime/core/queue/handler.js'
import { SpawnJobExecutor, buildJobArgs } from '../../../runtime/core/queue/spawn-executor.js'
import type { CallerIdentity } from '../../../runtime/core/sessions/types.js'

const main = (over: Partial<CallerIdentity> = {}): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-1',
  agentId: 'agent-main',
  role: 'main',
  ...over,
})
const sub = (over: Partial<CallerIdentity> = {}): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-1',
  agentId: 'agent-sub',
  parentAgentId: 'agent-main',
  role: 'subagent',
  ...over,
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
    return { pid: 1000 + this.starts.length, done, cancel }
  }
  finish(index = 0, exitCode = 0): void {
    this.starts[index].resolve({ exitCode, signal: null, stdout: 'ok', stderr: '' })
  }
}

function setup(
  over: { capacity?: Record<string, number>; reserved?: number; store?: MemoryJobStore } = {},
) {
  const executor = new FakeExecutor()
  const store = over.store || new MemoryJobStore()
  let id = 0
  const queue = new JobQueue({
    executor,
    store,
    capacity: over.capacity,
    reservedGpu: () => over.reserved || 0,
    id: () => 'job-' + ++id,
    now: () => 1000 + id,
  })
  return { queue, executor, store }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('3.1 job queue', () => {
  it('submit returns one job id immediately and uses it for later operations', () => {
    const { queue, executor } = setup()
    const job = queue.submit(main(), { kind: 'headless', script: 'print(1)' })
    expect(job.id).toMatch(/^job-/)
    expect(['queued', 'assigned', 'running']).toContain(job.state)
    expect(executor.starts).toHaveLength(1)
    expect(queue.status(job.id)?.id).toBe(job.id)
    expect(queue.list().map((j) => j.id)).toEqual([job.id])
  })

  it('rejects a render script and an unknown kind', () => {
    const { queue } = setup()
    expect(() => queue.submit(main(), { kind: 'render', script: 'print(1)' })).toThrow(QueueError)
    expect(() => queue.submit(main(), { kind: 'other' })).toThrow(QueueError)
  })

  it('does not oversell the GPU slot, while headless uses a CPU slot', async () => {
    const { queue, executor } = setup()
    const first = queue.submit(main(), { kind: 'render', file: 'a.blend', frames: '1' })
    const second = queue.submit(main(), { kind: 'render', file: 'b.blend', frames: '1' })
    const cpu = queue.submit(main(), { kind: 'headless', script: 'print(1)' })
    expect(executor.starts).toHaveLength(2)
    expect(queue.status(first.id)?.state).toBe('running')
    expect(queue.status(second.id)?.state).toBe('queued')
    expect(queue.status(cpu.id)?.state).toBe('running')
    executor.finish(0)
    await Promise.resolve()
    expect(queue.status(second.id)?.state).toBe('running')
    expect(queue.perf().slots).toMatchObject({
      gpu: { used: 1, total: 1 },
      cpu: { used: 1, total: 2 },
    })
  })

  it('rejects a full pending queue with QUEUE_FULL instead of waiting', () => {
    const { queue } = setup({ capacity: { pending: 2 } })
    queue.submit(main(), { kind: 'render', file: 'a.blend' })
    queue.submit(main(), { kind: 'render', file: 'b.blend' })
    const t0 = performance.now()
    try {
      queue.submit(main(), { kind: 'render', file: 'c.blend' })
      throw new Error('expected queue full')
    } catch (e) {
      const elapsedMs = performance.now() - t0
      expect(e).toBeInstanceOf(QueueError)
      expect((e as QueueError).failure.code).toBe('queue_full')
      expect((e as QueueError).failure.evidence.limit).toBe(2)
      // 3.6：队列满的决策是立即返回，不等待队列空位（queue 预算 ≤3s，决策 ≤100ms）。
      expect(elapsedMs).toBeLessThan(100)
    }
  })

  it('records every transition and keeps terminal states immutable', async () => {
    const { queue, executor } = setup()
    const job = queue.submit(main(), { kind: 'headless', script: 'print(1)' })
    executor.finish(0)
    await Promise.resolve()
    const done = queue.status(job.id)
    expect(done?.state).toBe('completed')
    expect(done?.events.map((e) => e.to)).toEqual(['queued', 'assigned', 'running', 'completed'])
    executor.starts[0].resolve({ exitCode: 7, signal: null, stdout: '', stderr: '' })
    expect(queue.status(job.id)?.state).toBe('completed')
  })

  it('marks a running job failed after a Gateway restart and never replays it', () => {
    const store = new MemoryJobStore()
    const first = setup({ store })
    const job = first.queue.submit(main(), { kind: 'headless', script: 'print(1)' })
    expect(first.queue.status(job.id)?.state).toBe('running')
    const executor = new FakeExecutor()
    const second = new JobQueue({ executor, store, id: () => 'job-2' })
    const restored = second.status(job.id)
    expect(restored?.state).toBe('failed')
    expect(restored?.error?.code).toBe('gateway_restart')
    expect(restored?.events.map((e) => e.to)).toContain('failed')
    expect(executor.starts).toHaveLength(0)
  })

  it('cancels a running job once and keeps the canceled state', () => {
    const { queue, executor } = setup()
    const job = queue.submit(main(), { kind: 'render', file: 'a.blend' })
    const first = queue.cancel(main(), job.id)
    expect(first.canceled).toBe(true)
    expect(queue.status(job.id)?.state).toBe('canceled')
    expect(executor.starts[0].cancel).toHaveBeenCalledTimes(1)
    const again = queue.cancel(main(), job.id)
    expect(again.alreadyFinished).toBe(true)
    expect(queue.status(job.id)?.state).toBe('canceled')
  })

  it('restricts cancel to the owner, its parent, and the main agent', () => {
    const { queue } = setup()
    const job = queue.submit(sub(), { kind: 'render', file: 'a.blend' })
    expect(() => queue.cancel(other(), job.id)).toThrow(QueueError)
    expect(queue.status(job.id)?.state).not.toBe('canceled')
    expect(queue.cancel(main(), job.id).canceled).toBe(true)
  })

  it('explains quota and admits another owner instead of starving', () => {
    const { queue, executor } = setup({ capacity: { ownerQuota: 1 } })
    queue.submit(main(), { kind: 'headless', script: 'print(1)' })
    const blocked = queue.submit(main(), { kind: 'headless', script: 'print(2)' })
    const otherJob = queue.submit(other(), { kind: 'headless', script: 'print(3)' })
    expect(queue.status(blocked.id)?.state).toBe('queued')
    expect(queue.status(blocked.id)?.decision?.reason).toBe('quota')
    expect(executor.starts.map((s) => s.job.id)).toContain(otherJob.id)
  })

  it('admits a higher priority job at the next slot boundary', async () => {
    const { queue, executor } = setup({ capacity: { gpu: 1, ownerQuota: 8 } })
    queue.submit(main(), { kind: 'render', file: 'running.blend' })
    const low = queue.submit(main(), { kind: 'render', file: 'low.blend', priority: 0 })
    const high = queue.submit(main(), { kind: 'render', file: 'high.blend', priority: 10 })
    expect(queue.status(low.id)?.state).toBe('queued')
    expect(queue.status(high.id)?.state).toBe('queued')
    executor.finish(0)
    await Promise.resolve()
    expect(queue.status(high.id)?.state).toBe('running')
    expect(queue.status(high.id)?.decision?.reason).toBe('priority')
    expect(queue.status(low.id)?.state).toBe('queued')
  })

  it('ties resource admission to a virtual-gui GPU reservation', () => {
    const { queue, executor } = setup({ reserved: 1 })
    const job = queue.submit(main(), { kind: 'render', file: 'a.blend' })
    expect(queue.status(job.id)?.state).toBe('queued')
    expect(queue.status(job.id)?.decision?.reason).toBe('virtual_gui')
    expect(executor.starts).toHaveLength(0)
  })

  it('times a bounded wait out without canceling the job', async () => {
    vi.useFakeTimers()
    const { queue } = setup()
    const job = queue.submit(main(), { kind: 'render', file: 'a.blend' })
    const pending = queue.wait(main(), job.id, 1000)
    await vi.advanceTimersByTimeAsync(1200)
    const result = await pending
    expect(result.waitTimedOut).toBe(true)
    expect(queue.status(job.id)?.state).toBe('running')
  })
})

describe('3.1 internal queue surface', () => {
  it('covers render and headless submission through one handler', async () => {
    vi.useFakeTimers()
    const { queue } = setup()
    const surface = new JobSurface(queue)
    const render = await surface.handle(main(), {
      op: 'submit',
      kind: 'render',
      file: 'scene.blend',
    })
    const headless = await surface.handle(main(), {
      op: 'submit',
      kind: 'headless',
      script: 'print(1)',
    })
    expect(render.ok).toBe(true)
    expect(headless.ok).toBe(true)
    const list = await surface.handle(main(), { op: 'list' })
    expect((list.jobs as JobRecord[]).map((j) => j.kind).sort()).toEqual(['headless', 'render'])
    const perf = await surface.handle(main(), { op: 'perf' })
    expect(perf.perf).toMatchObject({ capacity: { gpu: 1, cpu: 2, pending: 64 } })
  })

  it('returns a structured ownership failure instead of throwing', async () => {
    const { queue } = setup()
    const surface = new JobSurface(queue)
    const job = queue.submit(sub(), { kind: 'render', file: 'scene.blend' })
    const denied = await surface.handle(other(), { op: 'cancel', id: job.id })
    expect(denied.ok).toBe(false)
    expect(denied.code).toBe('job_ownership_denied')
    expect(denied.stage).toBe('queue')
  })

  it('reads caller identity from the DSH headers and rejects an unknown op', async () => {
    const caller = callerFromHeaders({
      'x-dsh-host-id': 'host-a',
      'x-dsh-session-id': 'dsh-1',
      'x-dsh-agent-id': 'agent-main',
      'x-dsh-agent-role': 'main',
    })
    expect(caller).toEqual({
      hostId: 'host-a',
      dshSessionId: 'dsh-1',
      agentId: 'agent-main',
      role: 'main',
    })
    const { queue } = setup()
    const result = await new JobSurface(queue).handle(caller, { op: 'nope' })
    expect(result.ok).toBe(false)
    expect(result.code).toBe('queue_unknown_op')
  })
})

describe('3.1 queue executor command shape', () => {
  const record = (kind: 'render' | 'headless', params: Record<string, unknown>): JobRecord => ({
    id: 'job-x',
    kind,
    params,
    state: 'running',
    priority: 0,
    slot: kind === 'render' ? 'gpu' : 'cpu',
    owner: { hostId: 'h', dshSessionId: 'd', agentId: 'a' },
    createdAt: 1,
    updatedAt: 1,
    assignedAt: 1,
    startedAt: 1,
    finishedAt: null,
    pid: null,
    decision: null,
    result: null,
    artifacts: [],
    logs: { stdout: '', stderr: '' },
    error: null,
    events: [],
  })

  it('render uses the fixed declarative entry and never a caller script', () => {
    const { args } = buildJobArgs(
      record('render', { file: 'scene.blend', resolution: [64, 64], frames: [1, 2] }),
      os.tmpdir(),
    )
    expect(args).toContain('--python-expr')
    expect(args).not.toContain('--python')
    expect(args.join(' ')).toContain('s.render.resolution_x=64')
    expect(args.slice(-5)).toEqual(['-s', '1', '-e', '2', '-a'])
  })

  it('render with camera + frames previews an animation without a GUI session', () => {
    const { args } = buildJobArgs(
      record('render', {
        file: 'scene.blend',
        camera: 'Camera',
        frames: [3, 7],
        resolution: [160, 90],
        samples: 4,
      }),
      os.tmpdir(),
    )
    const expr = args[args.indexOf('--python-expr') + 1]
    expect(expr).toContain('s.camera=bpy.data.objects.get("Camera")')
    expect(args.slice(-5)).toEqual(['-s', '3', '-e', '7', '-a'])
    expect(args).not.toContain('--python')
  })

  it('render without output writes under the writable workDir and collects that outdir', () => {
    const { args, outdir } = buildJobArgs(
      record('render', { resolution: [64, 64], frames: [1, 1] }),
      os.tmpdir(),
    )
    expect(outdir).toBeTruthy()
    expect(outdir!.includes(path.join('renders', 'job-x'))).toBe(true)
    expect(args.join(' ')).toContain('s.render.filepath=')
    expect(fs.existsSync(outdir!)).toBe(true)
    fs.rmSync(outdir!, { recursive: true, force: true })
  })

  it('render with an explicit output collects artifacts from its parent directory', () => {
    const output = path.join(os.tmpdir(), 'dsh-render-out-' + process.pid, 'frame-')
    const { outdir } = buildJobArgs(record('render', { output }), os.tmpdir())
    expect(outdir).toBe(path.dirname(output))
    fs.rmSync(outdir!, { recursive: true, force: true })
  })

  it('headless runs blender -b with a script file and no GUI session', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-queue-args-'))
    const { args } = buildJobArgs(record('headless', { script: 'print("HEADLESS {}\\n")' }), dir)
    expect(args).toEqual(['-b', '--factory-startup', '--python', path.join(dir, 'job.py')])
    expect(fs.readFileSync(path.join(dir, 'job.py'), 'utf8')).toContain('HEADLESS')
  })

  it('attributes a missing Blender executable to stage spawn', async () => {
    const missing = path.join(os.tmpdir(), 'missing-blender-' + process.pid)
    const executor = new SpawnJobExecutor({ blenderExe: missing })
    const handle = executor.start(record('headless', { script: 'print(1)' }))
    const result = await handle.done
    expect(result.error?.code).toBe('blender_exe_missing')
    expect(result.error?.stage).toBe('spawn')
    expect(result.error?.evidence.blenderExe).toBe(missing)
  })
})

describe('3.1 job queue persistence', () => {
  it('quarantines a corrupt state file instead of failing the Gateway', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-queue-'))
    const file = path.join(dir, 'queue.json')
    fs.writeFileSync(file, '{not json')
    const store = new JsonJobStore(file)
    expect(store.load()).toEqual([])
    expect(fs.readdirSync(dir).some((name) => name.includes('.corrupt-'))).toBe(true)
  })

  it('round-trips jobs and reports a corrupt file without touching it silently', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-queue-'))
    const file = path.join(dir, 'queue.json')
    const store = new JsonJobStore(file)
    const job = {
      id: 'job-1',
      kind: 'render',
      params: {},
      state: 'completed',
      priority: 0,
      slot: 'gpu',
      owner: { hostId: 'h', dshSessionId: 'd', agentId: 'a' },
      createdAt: 1,
      updatedAt: 2,
      assignedAt: 1,
      startedAt: 1,
      finishedAt: 2,
      pid: null,
      decision: null,
      result: { exitCode: 0, signal: null, ms: 1 },
      artifacts: ['out.png'],
      logs: { stdout: 'ok', stderr: '' },
      error: null,
      events: [],
    } satisfies JobRecord
    store.save([job])
    expect(store.load()).toHaveLength(1)
    expect(store.load()[0].artifacts).toEqual(['out.png'])
  })
})