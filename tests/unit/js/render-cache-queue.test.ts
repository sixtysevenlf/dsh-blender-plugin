import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { JobQueue } from '../../../runtime/core/queue/manager.js'
import { MemoryJobStore } from '../../../runtime/core/queue/store.js'
import { RenderCache } from '../../../runtime/core/queue/render-cache.js'
import type { JobExecutor, JobHandle, JobRecord } from '../../../runtime/core/queue/types.js'
import type { CallerIdentity } from '../../../runtime/core/sessions/types.js'

const main: CallerIdentity = {
  hostId: 'host-a',
  dshSessionId: 'dsh-1',
  agentId: 'agent-main',
  role: 'main',
}

const roots: string[] = []

function tempHome(): string {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-cache-queue-'))
  roots.push(home)
  return home
}

function sceneFile(home: string): string {
  const file = path.join(home, 'scene.blend')
  fs.writeFileSync(file, 'BLENDER-scene')
  return file
}

interface FakeResult {
  exitCode?: number
  signal?: string | null
  stdout?: string
  stderr?: string
  parsed?: unknown
  artifacts?: string[]
}

/** Writes a complete per-frame staging tree so promotion has real manifests. */
function writeStaging(job: JobRecord, degradedFrames: number[] = []): unknown {
  const cacheState = job.cache
  if (!cacheState) throw new Error('cache executor only runs cached jobs')
  const passes = (job.params.passes as string[]) || ['beauty']
  const summaries: Array<Record<string, unknown>> = []
  for (const frame of cacheState.frames) {
    const dir = path.join(
      cacheState.stagingDir,
      'passes',
      'frame_' + String(frame).padStart(4, '0'),
    )
    fs.mkdirSync(dir, { recursive: true })
    const entries: Record<string, unknown> = {}
    for (const name of passes) {
      const file = path.join(dir, name + '.png')
      fs.writeFileSync(file, 'pixels-' + name + '-' + String(frame))
      entries[name] = {
        path: file.split(path.sep).join('/'),
        bytes: fs.statSync(file).size,
        dtype: name === 'depth' ? 'uint16' : 'uint8',
        channels: 1,
        range: [0, 1],
        colorspace: 'Non-Color',
        encoding: name === 'depth' ? 'depth_m = near + (pixel/65535)*(far-near)' : 'none',
      }
    }
    const degraded = degradedFrames.includes(frame) ? [passes[passes.length - 1]] : []
    const manifest = {
      manifestVersion: 1,
      frame,
      camera: 'Camera',
      resolution: [64, 64],
      samples: 16,
      engine: 'CYCLES',
      requestedPasses: passes,
      passes: entries,
      degraded,
    }
    const manifestPath = path.join(dir, 'manifest.json')
    fs.writeFileSync(manifestPath, JSON.stringify(manifest))
    summaries.push({
      frame,
      manifest: manifestPath.split(path.sep).join('/'),
      passes: entries,
      bytes: 100,
      degraded,
      requestedPasses: passes,
    })
  }
  return {
    ok: true,
    manifestVersion: 1,
    passes,
    frames: summaries,
    degradedFrames: [],
    outdir: cacheState.stagingDir.split(path.sep).join('/'),
  }
}

class CacheExecutor implements JobExecutor {
  starts: JobRecord[] = []
  private pending: Array<(result: FakeResult) => void> = []
  private parsed = new Map<string, unknown>()
  writeStaging = true
  degradedFrames: number[] = []

  start(job: JobRecord): JobHandle {
    this.starts.push(job)
    if (this.writeStaging && job.cache && !job.cache.hit)
      this.parsed.set(job.id, writeStaging(job, this.degradedFrames))
    let resolve!: (result: FakeResult) => void
    const done = new Promise<FakeResult>((r) => {
      resolve = r
    }).then((result) => ({
      exitCode: result.exitCode ?? 0,
      signal: result.signal ?? null,
      stdout: result.stdout ?? '',
      stderr: result.stderr ?? '',
      artifacts: result.artifacts ?? [],
      parsed: result.parsed ?? this.parsed.get(job.id),
    })) as Promise<Awaited<JobHandle['done']>>
    this.pending.push(resolve)
    return { pid: 1000 + this.starts.length, done, cancel: vi.fn() }
  }

  finish(index = 0, result: FakeResult = {}): void {
    const resolve = this.pending[index]
    if (!resolve) throw new Error('no pending start at ' + String(index))
    resolve(result)
  }
}

function setup(home: string, over: { maxEntryBytes?: number } = {}) {
  const cache = new RenderCache({ home, maxEntryBytes: over.maxEntryBytes })
  const executor = new CacheExecutor()
  let id = 0
  const queue = new JobQueue({
    executor,
    store: new MemoryJobStore(),
    renderCache: cache,
    id: () => 'job-' + String(++id),
    now: () => 1000 + id * 10,
  })
  return { cache, executor, queue }
}

const renderParams = (file: string, frames: number[] = [1, 1]) => ({
  kind: 'render',
  file,
  passes: ['beauty', 'depth'],
  frames,
  resolution: [64, 64],
  samples: 16,
  engine: 'CYCLES',
})

afterEach(() => {
  for (const home of roots.splice(0)) fs.rmSync(home, { recursive: true, force: true })
})

describe('render cache in the job queue', () => {
  it('promotes a completed miss and reports the cache metadata without pixels', async () => {
    const home = tempHome()
    const { cache, executor, queue } = setup(home)
    const file = sceneFile(home)
    const first = queue.submit(main, renderParams(file))
    expect(first.cache?.hit).toBe(false)
    expect(executor.starts).toHaveLength(1)
    executor.finish(0)
    const waited = await queue.wait(main, first.id, 5000)
    const done = waited.job as JobRecord
    expect(done.state).toBe('completed')
    expect(done.result?.cache).toMatchObject({ hit: false, entries: 1 })
    expect(done.artifacts.every((artifact) => artifact.startsWith(cache.root))).toBe(true)
    expect(cache.stats().entries).toBe(1)
    const index = JSON.parse(fs.readFileSync(cache.indexFile, 'utf8'))
    expect(index.entries).toHaveLength(1)
    const parsed = done.result?.parsed as {
      frames: Array<{ manifest: string; passes: Record<string, { path: string }> }>
    }
    expect(parsed.frames[0].manifest.startsWith(cache.root.replace(/\\/g, '/'))).toBe(true)
    expect(
      Object.values(parsed.frames[0].passes).every((pass) =>
        pass.path.startsWith(cache.root.replace(/\\/g, '/')),
      ),
    ).toBe(true)
    expect(JSON.stringify(done.result?.cache)).not.toContain('base64')
    expect(fs.existsSync(cache.stagingDir('job-1'))).toBe(false)
  })

  it('returns a completed cache-hit job without spawning Blender or taking a slot', async () => {
    const home = tempHome()
    const { cache, executor, queue } = setup(home)
    const file = sceneFile(home)
    const params = renderParams(file)
    const first = queue.submit(main, params)
    executor.finish(0)
    await queue.wait(main, first.id, 5000)
    const startsAfterMiss = executor.starts.length

    const hit = queue.submit(main, params)
    expect(hit.state).toBe('completed')
    expect(hit.slot).toBeNull()
    expect(hit.result?.cache).toMatchObject({ hit: true, entries: 1 })
    expect(executor.starts).toHaveLength(startsAfterMiss)
    expect(hit.artifacts.length).toBeGreaterThan(0)
    expect(hit.artifacts.every((artifact) => artifact.startsWith(cache.root))).toBe(true)

    // A hit needs no GPU slot: keep the only gpu slot busy with a legacy render.
    const blocking = queue.submit(main, { kind: 'render', file })
    expect(blocking.slot).toBe('gpu')
    const stillHit = queue.submit(main, params)
    expect(stillHit.state).toBe('completed')
    expect(stillHit.slot).toBeNull()
    expect(executor.starts).toHaveLength(startsAfterMiss + 1)
  })

  it('copies a cache hit into the requested outdir instead of pointing at the cache', async () => {
    const home = tempHome()
    const { executor, queue } = setup(home)
    const file = sceneFile(home)
    const userOutdir = path.join(home, 'out-hit')
    const params = { ...renderParams(file), outdir: userOutdir }
    const first = queue.submit(main, params)
    executor.finish(0)
    await queue.wait(main, first.id, 5000)

    const hit = queue.submit(main, params)
    expect(hit.state).toBe('completed')
    // The submitted outdir must stay the caller's path, and the frames must be there.
    expect(hit.params.outdir).toBe(userOutdir)
    const manifestPath = path.join(userOutdir, 'passes', 'frame_0001', 'manifest.json')
    expect(fs.existsSync(manifestPath)).toBe(true)
    expect(hit.artifacts.length).toBeGreaterThan(0)
    expect(hit.artifacts.every((artifact) => artifact.startsWith(userOutdir))).toBe(true)
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
    const frameDir = path.dirname(manifestPath).split(path.sep).join('/')
    expect(
      Object.values(manifest.passes as Record<string, { path: string }>).every((pass) =>
        pass.path.startsWith(frameDir),
      ),
    ).toBe(true)
  })

  it('reports too_many_frames instead of silently rendering without a cache', async () => {
    const home = tempHome()
    const { cache, executor, queue } = setup(home)
    const file = sceneFile(home)
    const job = queue.submit(main, renderParams(file, [1, 900]))
    expect(job.cache).toBeUndefined()
    executor.finish(0)
    const waited = await queue.wait(main, job.id, 5000)
    const done = waited.job as JobRecord
    expect(done.state).toBe('completed')
    expect(done.result?.cache).toMatchObject({ hit: false, skipped: 'too_many_frames' })
    expect(cache.stats().entries).toBe(0)
  })

  it('never caches a completed render whose frames lost a requested pass', async () => {
    const home = tempHome()
    const { cache, executor, queue } = setup(home)
    const file = sceneFile(home)
    executor.degradedFrames = [1]
    const job = queue.submit(main, renderParams(file))
    executor.finish(0)
    const waited = await queue.wait(main, job.id, 5000)
    const done = waited.job as JobRecord
    expect(done.state).toBe('completed')
    expect(done.result?.cache).toMatchObject({ hit: false, entries: 0, skipped: 'degraded' })
    expect(cache.stats().entries).toBe(0)
    // The rendered frame is preserved for the caller instead of deleted.
    expect(done.artifacts.length).toBeGreaterThan(0)

    const again = queue.submit(main, renderParams(file))
    expect(again.state).not.toBe('completed')
    expect(executor.starts).toHaveLength(2)
  })

  it('misses again when the .blend identity changes', async () => {
    const home = tempHome()
    const { executor, queue } = setup(home)
    const file = sceneFile(home)
    const params = renderParams(file)
    const first = queue.submit(main, params)
    executor.finish(0)
    await queue.wait(main, first.id, 5000)

    fs.writeFileSync(file, 'BLENDER-scene-v2')
    fs.utimesSync(file, new Date(Date.now() + 5000), new Date(Date.now() + 5000))
    const second = queue.submit(main, params)
    expect(second.state).not.toBe('completed')
    expect(executor.starts).toHaveLength(2)
    expect(second.cache?.hit).toBe(false)
  })

  it('keeps no entry for a failed render and re-renders the same inputs', async () => {
    const home = tempHome()
    const { cache, executor, queue } = setup(home)
    const file = sceneFile(home)
    const params = renderParams(file)
    const failed = queue.submit(main, params)
    executor.finish(0, { exitCode: 1, stderr: 'boom' })
    const waited = await queue.wait(main, failed.id, 5000)
    expect((waited.job as JobRecord).state).toBe('failed')
    expect((waited.job as JobRecord).result?.cache).toMatchObject({ hit: false })
    expect(cache.stats().entries).toBe(0)
    expect(fs.existsSync(cache.stagingDir(failed.id))).toBe(false)

    const retry = queue.submit(main, params)
    expect(retry.state).not.toBe('completed')
    expect(executor.starts).toHaveLength(2)
  })

  it('keeps no entry when a cached render is canceled', async () => {
    const home = tempHome()
    const { cache, executor, queue } = setup(home)
    const file = sceneFile(home)
    const job = queue.submit(main, renderParams(file))
    const canceled = queue.cancel(main, job.id)
    expect((canceled.job as JobRecord).state).toBe('canceled')
    expect(fs.existsSync(cache.stagingDir(job.id))).toBe(false)
    expect(cache.stats().entries).toBe(0)

    const next = queue.submit(main, renderParams(file))
    expect(next.state).not.toBe('completed')
    expect(executor.starts).toHaveLength(2)
  })

  it('skips the cache for an oversized entry but completes with cache_skipped', async () => {
    const home = tempHome()
    const { cache, executor, queue } = setup(home, { maxEntryBytes: 8 })
    const file = sceneFile(home)
    const job = queue.submit(main, renderParams(file))
    executor.finish(0)
    const waited = await queue.wait(main, job.id, 5000)
    const done = waited.job as JobRecord
    expect(done.state).toBe('completed')
    expect(done.result?.cache).toMatchObject({ hit: false, skipped: 'oversize' })
    expect(cache.stats().entries).toBe(0)
    expect(
      done.artifacts.some((artifact) => artifact.startsWith(path.join(home, 'jobs', job.id))),
    ).toBe(true)
  })

  it('leaves legacy render jobs without passes on the old path', () => {
    const home = tempHome()
    const { cache, executor, queue } = setup(home)
    const file = sceneFile(home)
    const job = queue.submit(main, { kind: 'render', file, outdir: path.join(home, 'out') })
    expect(job.cache).toBeUndefined()
    expect(job.params.outdir).toBe(path.join(home, 'out'))
    expect(executor.starts).toHaveLength(1)
    expect(cache.stats().entries).toBe(0)
  })
})
