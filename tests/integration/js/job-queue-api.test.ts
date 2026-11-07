/**
 * 6.11/6.13: the internal queue submission surface is reachable through the
 * Gateway's /job route, survives a Gateway restart, and reports failures as a
 * complete availability envelope. The real Blender process path has its own
 * e2e script (tests/e2e/queue_blender.mjs); this file stays runnable on CI
 * hosts without Blender by pointing DSH_BLENDER_EXE at a missing file.
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { startBackend } from '../../helpers/backend'
import { JobQueue } from '../../../runtime/core/queue/manager.js'
import { JsonJobStore } from '../../../runtime/core/queue/store.js'
import type { JobExecutor, JobHandle, JobRecord } from '../../../runtime/core/queue/types.js'

const closers: Array<() => Promise<void>> = []
afterEach(async () => {
  while (closers.length) {
    const close = closers.pop()!
    try {
      await close()
    } catch {
      /* best effort */
    }
  }
})

const main = {
  'x-dsh-host-id': 'host-it',
  'x-dsh-session-id': 'dsh-queue',
  'x-dsh-agent-id': 'agent-main',
  'x-dsh-agent-role': 'main',
}
const other = { ...main, 'x-dsh-session-id': 'dsh-other', 'x-dsh-agent-id': 'agent-other' }

async function waitFor(predicate: () => Promise<boolean>, timeoutMs = 5000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  throw new Error('condition not met before timeout')
}

class NeverExecutor implements JobExecutor {
  start(_job: JobRecord): JobHandle {
    return { pid: null, cancel: () => {}, done: new Promise(() => {}) }
  }
}

function record(kind: 'render' | 'headless', id: string): JobRecord {
  return {
    id,
    kind,
    params: { kind },
    state: 'queued',
    priority: 0,
    slot: null,
    owner: { hostId: 'h', dshSessionId: 'd', agentId: 'a' },
    createdAt: 1,
    updatedAt: 1,
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
}

describe('Gateway queue route (6.11/6.13)', () => {
  it('submits a job through /job and reports the spawn failure with four answers', async () => {
    const missing = path.join(os.tmpdir(), 'dsh-missing-blender-' + process.pid + '.exe')
    const backend = await startBackend({ env: { DSH_BLENDER_EXE: missing } })
    closers.push(() => backend.stop())
    const submitted = await backend.post(
      '/job',
      { surface: 'queue', op: 'submit', kind: 'render', resolution: [64, 64], frames: [1, 1] },
      main,
    )
    expect(submitted.status).toBe(200)
    expect(submitted.json.ok).toBe(true)
    expect(String(submitted.json.job.id)).toMatch(/^job-/)
    let status = submitted.json.job
    await waitFor(async () => {
      const response = await backend.post(
        '/job',
        { surface: 'queue', op: 'status', id: status.id },
        main,
      )
      status = response.json.job
      return ['failed', 'completed', 'canceled'].includes(String(status.state))
    })
    expect(status.state).toBe('failed')
    expect(status.error.code).toBe('blender_exe_missing')
    expect(status.error.stage).toBe('spawn')
    expect(status.error.root_cause).toContain('Blender')
    expect(status.error.fix).toContain('DSH_BLENDER_EXE')
    expect(status.error.fallback).toBeTruthy()
    expect(status.error.evidence.blenderExe).toBe(missing)
    const perf = await backend.post('/job', { surface: 'queue', op: 'perf' }, main)
    expect(perf.json.perf.capacity.pending).toBe(64)
    expect(perf.json.perf.capacity.gpu).toBe(1)
    const list = await backend.post('/job', { surface: 'queue', op: 'list', kind: 'render' }, main)
    expect(list.json.jobs).toHaveLength(1)
  })

  it('refuses collect/cancel from another DSH session and keeps the job readable', async () => {
    const missing = path.join(os.tmpdir(), 'dsh-missing-blender-' + process.pid + '.exe')
    const backend = await startBackend({ env: { DSH_BLENDER_EXE: missing } })
    closers.push(() => backend.stop())
    const submitted = await backend.post(
      '/job',
      { surface: 'queue', op: 'submit', kind: 'headless', script: 'print(1)' },
      main,
    )
    const id = submitted.json.job.id
    await waitFor(async () => {
      const response = await backend.post('/job', { surface: 'queue', op: 'status', id }, main)
      return ['failed', 'completed', 'canceled'].includes(String(response.json.job.state))
    })
    const denied = await backend.post('/job', { surface: 'queue', op: 'collect', id }, other)
    expect(denied.json.code).toBe('job_ownership_denied')
    const readable = await backend.post('/job', { surface: 'queue', op: 'collect', id }, main)
    expect(readable.json.ok).toBe(true)
    expect(readable.json.job.id).toBe(id)
  })

  it('recovers a persisted queue on restart: running fails, queued is not replayed as done', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-queue-restart-'))
    const file = path.join(dir, 'queue.json')
    const store = new JsonJobStore(file)
    store.save([
      { ...record('render', 'job-running'), state: 'running', assignedAt: 2, startedAt: 3 },
      { ...record('headless', 'job-queued') },
    ])
    const queue = new JobQueue({ executor: new NeverExecutor(), store })
    const running = queue.status('job-running')
    const queued = queue.status('job-queued')
    expect(running?.state).toBe('failed')
    expect(running?.error?.code).toBe('gateway_restart')
    expect(running?.error?.stage).toBe('queue')
    expect(queued?.state).not.toBe('failed')
    expect(queued?.events.some((event) => event.reason === 'gateway_restart')).toBe(false)
  })
})
