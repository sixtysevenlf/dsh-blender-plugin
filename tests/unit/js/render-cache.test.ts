import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  RENDER_CACHE_MAX_ENTRY_BYTES,
  RenderCache,
  planRenderCache,
} from '../../../runtime/core/queue/render-cache.js'

const roots: string[] = []

function tempHome(): string {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-cache-home-'))
  roots.push(home)
  return home
}

function sceneFile(home: string, name = 'scene.blend'): string {
  const file = path.join(home, name)
  fs.writeFileSync(file, 'BLENDER-scene')
  return file
}

interface FrameSpec {
  passes?: string[]
  /** Omit manifest.json to simulate an incomplete render. */
  manifest?: boolean
  /** Production shape: an array of the pass names whose file is missing. */
  degraded?: string[]
  requestedPasses?: string[]
}

function frameDirOf(staging: string, frame: number): string {
  return path.join(staging, 'passes', 'frame_' + String(frame).padStart(4, '0'))
}

function writeFrame(
  staging: string,
  frame: number,
  spec: FrameSpec = {},
): { dir: string; manifest: Record<string, unknown> } {
  const names = spec.passes || ['beauty', 'depth']
  const dir = frameDirOf(staging, frame)
  fs.mkdirSync(dir, { recursive: true })
  const passes: Record<string, unknown> = {}
  for (const name of names) {
    const file = path.join(dir, name + '.png')
    const body = Buffer.from('pixels-' + name + '-' + String(frame))
    fs.writeFileSync(file, body)
    passes[name] = {
      path: file.replace(/\\/g, '/'),
      bytes: fs.statSync(file).size,
      sha256: createHash('sha256').update(body).digest('hex'),
      dtype: name === 'depth' ? 'uint16' : 'uint8',
      channels: 1,
      range: [0, 1],
      colorspace: 'Non-Color',
      encoding:
        name === 'depth'
          ? 'depth_m = near + ((pixel/65535 - 1/65535)/(1 - 1/65535))*(far-near); pixel 0 = no geometry'
          : 'none',
    }
  }
  const manifest: Record<string, unknown> = {
    manifestVersion: 1,
    frame,
    camera: 'Camera',
    resolution: [64, 64],
    samples: 16,
    engine: 'CYCLES',
    requestedPasses: spec.requestedPasses || names,
    passes,
    // The Blender entry writes `degraded` as a list of lost pass names.
    degraded: spec.degraded || [],
  }
  if (spec.manifest !== false) {
    fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2))
  }
  return { dir, manifest }
}

function planFor(file: string, frames: number[], passes = ['beauty', 'depth']) {
  const plan = planRenderCache({ file, passes, frames: [frames[0], frames[frames.length - 1]] })
  if (!plan) throw new Error('scene must be cacheable for this fixture')
  return plan
}

afterEach(() => {
  vi.restoreAllMocks()
  for (const home of roots.splice(0)) fs.rmSync(home, { recursive: true, force: true })
})

describe('render cache promotion and lookup', () => {
  it('promotes a complete staging frame into the cache and hits it afterwards', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home, now: () => 5000 })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-a')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)

    const promoted = await cache.promoteJob({
      jobId: 'job-a',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    expect(promoted.ok).toBe(true)
    expect(promoted.entries).toBe(1)
    expect(promoted.skipped).toBeNull()
    expect(promoted.artifacts.some((artifact) => artifact.endsWith('manifest.json'))).toBe(true)

    const entryDir = path.join(cache.root, plan.keys[0])
    const manifest = JSON.parse(fs.readFileSync(path.join(entryDir, 'manifest.json'), 'utf8'))
    expect(
      Object.values(manifest.passes)
        .map((pass) => (pass as { path: string }).path)
        .every((file) => file.startsWith(entryDir.replace(/\\/g, '/'))),
    ).toBe(true)

    const hit = cache.lookupAll(plan.inputs)
    expect(hit.hit).toBe(true)
    const index = JSON.parse(fs.readFileSync(cache.indexFile, 'utf8'))
    expect(index.version).toBe(1)
    expect(index.entries).toHaveLength(1)
    expect(index.entries[0].key).toBe(plan.keys[0])
    expect(fs.existsSync(staging)).toBe(false)
  })

  it('supports multi-frame jobs as one entry per frame', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1, 2])
    const staging = cache.stagingDir('job-b')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)
    writeFrame(staging, 2)
    const promoted = await cache.promoteJob({
      jobId: 'job-b',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    expect(promoted.entries).toBe(2)
    expect(cache.stats().entries).toBe(2)
    expect(cache.lookupAll(plan.inputs).hit).toBe(true)
  })

  it('copies promoted frames to the user outdir and keeps its own copy', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-c')
    const userOutdir = path.join(home, 'user-out')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)
    const promoted = await cache.promoteJob({
      jobId: 'job-c',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir,
    })
    expect(promoted.ok).toBe(true)
    const userManifest = path.join(userOutdir, 'passes', 'frame_0001', 'manifest.json')
    expect(fs.existsSync(userManifest)).toBe(true)
    expect(fs.existsSync(path.join(cache.root, plan.keys[0], 'manifest.json'))).toBe(true)
    const manifest = JSON.parse(fs.readFileSync(userManifest, 'utf8'))
    const userDir = path.dirname(userManifest).replace(/\\/g, '/')
    expect(
      Object.values(manifest.passes).every((pass) =>
        (pass as { path: string }).path.startsWith(userDir),
      ),
    ).toBe(true)
    expect(promoted.artifacts.every((artifact) => artifact.startsWith(userOutdir))).toBe(true)
    expect(cache.lookupAll(plan.inputs).hit).toBe(true)
  })

  it('materializes a cached frame into a user outdir with rewritten manifest paths', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-m')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)
    await cache.promoteJob({
      jobId: 'job-m',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    const hit = cache.lookupAll(plan.inputs)
    if (!hit.hit) throw new Error('fixture must hit')
    const dest = path.join(home, 'user-hit')
    const frame = cache.materialize(hit.entries[0], dest)
    expect(frame).not.toBeNull()
    const manifestPath = path.join(dest, 'passes', 'frame_0001', 'manifest.json')
    expect(frame?.manifest).toBe(manifestPath.split(path.sep).join('/'))
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
    const frameDir = path.dirname(manifestPath).replace(/\\/g, '/')
    expect(
      Object.values(manifest.passes).every((pass) =>
        (pass as { path: string }).path.startsWith(frameDir),
      ),
    ).toBe(true)
    expect(cache.materialize({ ...hit.entries[0], key: 'f'.repeat(64) }, dest)).toBeNull()
  })

  it('never caches an incomplete manifest and moves the artifacts aside', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-d')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1, { manifest: false })
    const promoted = await cache.promoteJob({
      jobId: 'job-d',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    expect(promoted.ok).toBe(true)
    expect(promoted.entries).toBe(0)
    expect(promoted.skipped).toBe('manifest_incomplete')
    expect(promoted.artifacts.length).toBeGreaterThan(0)
    expect(
      promoted.artifacts.every((artifact) => artifact.startsWith(path.join(home, 'jobs', 'job-d'))),
    ).toBe(true)
    expect(cache.stats().entries).toBe(0)
    expect(cache.lookupAll(plan.inputs).hit).toBe(false)
  })

  it('never caches a frame whose manifest lost a requested pass', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-degraded')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1, { degraded: ['normal'] })
    const promoted = await cache.promoteJob({
      jobId: 'job-degraded',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    expect(promoted.entries).toBe(0)
    expect(promoted.skipped).toBe('degraded')
    expect(cache.stats().entries).toBe(0)
    // The rendered pixels survive the refusal.
    expect(promoted.artifacts.some((artifact) => artifact.endsWith('beauty.png'))).toBe(true)
  })

  it('never caches a frame whose requestedPasses are not all present', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-missing')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1, {
      passes: ['beauty', 'depth'],
      requestedPasses: ['beauty', 'depth', 'normal'],
    })
    const promoted = await cache.promoteJob({
      jobId: 'job-missing',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    expect(promoted.entries).toBe(0)
    expect(promoted.skipped).toBe('degraded')
    expect(cache.stats().entries).toBe(0)
  })

  it('never caches one entry above the per-entry cap but still returns artifacts', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home, maxEntryBytes: 8 })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-e')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)
    const promoted = await cache.promoteJob({
      jobId: 'job-e',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    expect(promoted.skipped).toBe('oversize')
    expect(promoted.entries).toBe(0)
    expect(promoted.artifacts.some((artifact) => artifact.endsWith('manifest.json'))).toBe(true)
    expect(cache.stats().entries).toBe(0)
    expect(RENDER_CACHE_MAX_ENTRY_BYTES).toBe(2 * 1024 * 1024 * 1024)
  })

  it('refuses a whole promotion that would exceed the cache byte budget', async () => {
    const home = tempHome()
    const cache = new RenderCache({
      home,
      limits: { maxBytes: 512 * 1024 * 1024 },
      maxEntryBytes: 64 * 1024 * 1024,
    })
    // One frame is small; three frames together exceed the budget once maxBytes is tiny.
    ;(cache as unknown as { limits: { maxBytes: number } }).limits.maxBytes = 20
    const file = sceneFile(home)
    const plan = planFor(file, [1, 2])
    const staging = cache.stagingDir('job-total')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)
    writeFrame(staging, 2)
    const promoted = await cache.promoteJob({
      jobId: 'job-total',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    expect(promoted.skipped).toBe('oversize')
    expect(promoted.entries).toBe(0)
    expect(cache.stats().entries).toBe(0)
    // Nothing entered the cache, so no partial incoming directory is left behind.
    expect(fs.readdirSync(cache.root).filter((name) => name.startsWith('.incoming-'))).toHaveLength(
      0,
    )
  })

  it('restores frames to the job dir when a promotion fails mid-way', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1, 2])
    const staging = cache.stagingDir('job-rollback')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)
    writeFrame(staging, 2)
    const realRename = fs.renameSync.bind(fs)
    let moves = 0
    vi.spyOn(fs, 'renameSync').mockImplementation(((from: fs.PathLike, to: fs.PathLike) => {
      moves += 1
      // Fail the second frame's move into the incoming directory.
      if (moves === 2) throw new Error('disk-full')
      return realRename(from, to)
    }) as typeof fs.renameSync)
    const promoted = await cache.promoteJob({
      jobId: 'job-rollback',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    vi.restoreAllMocks()
    expect(promoted.ok).toBe(true)
    expect(promoted.entries).toBe(0)
    expect(promoted.skipped).toMatch(/^promote_failed:/)
    expect(cache.stats().entries).toBe(0)
    // The rendered pixels are preserved, not deleted with the failed promotion.
    const jobDir = path.join(home, 'jobs', 'job-rollback', 'passes')
    expect(fs.existsSync(path.join(jobDir, 'frame_0001', 'beauty.png'))).toBe(true)
    expect(fs.existsSync(path.join(jobDir, 'frame_0002', 'beauty.png'))).toBe(true)
    expect(fs.existsSync(path.join(cache.root, plan.keys[0]))).toBe(false)
  })
})

describe('render cache index, TTL and eviction', () => {
  async function promoteFrames(
    home: string,
    cache: RenderCache,
    frames: number[],
    jobId = 'job-x',
  ) {
    const file = sceneFile(home)
    const plan = planFor(file, frames)
    const staging = cache.stagingDir(jobId)
    fs.mkdirSync(staging, { recursive: true })
    for (const frame of frames) writeFrame(staging, frame)
    const promoted = await cache.promoteJob({
      jobId,
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    return { plan, promoted }
  }

  it('expires entries after the configured TTL', async () => {
    const home = tempHome()
    let clock = 1_000_000
    const cache = new RenderCache({ home, limits: { ttlDays: 2 }, now: () => clock })
    const { plan } = await promoteFrames(home, cache, [1])
    expect(cache.stats().entries).toBe(1)
    clock += 3 * 24 * 60 * 60 * 1000
    const restarted = new RenderCache({ home, limits: { ttlDays: 2 }, now: () => clock })
    restarted.startupCleanup()
    expect(restarted.stats().entries).toBe(0)
    expect(fs.existsSync(path.join(cache.root, plan.keys[0]))).toBe(false)
  })

  it('evicts the least recently used entries down to maxEntries on startup', async () => {
    const home = tempHome()
    let clock = 1_000_000
    const cache = new RenderCache({ home, now: () => clock })
    const plan = planFor(sceneFile(home), [1, 2, 3])
    for (const frame of plan.frames) {
      const staging = cache.stagingDir('job-' + String(frame))
      fs.mkdirSync(staging, { recursive: true })
      writeFrame(staging, frame)
      clock += 1000
      await cache.promoteJob({
        jobId: 'job-' + String(frame),
        keys: [plan.keys[frame - 1]],
        frames: [frame],
        scene: plan.scene,
        userOutdir: null,
      })
    }
    expect(cache.stats().entries).toBe(3)
    clock += 1000
    const restarted = new RenderCache({ home, limits: { maxEntries: 2 }, now: () => clock })
    restarted.startupCleanup()
    expect(restarted.stats().entries).toBe(2)
    // Frames 2 and 3 are newer than frame 1.
    const remaining = fs
      .readdirSync(restarted.root)
      .filter((name) => name !== 'index.json' && name !== '.staging')
    expect(remaining.sort()).toEqual([plan.keys[1], plan.keys[2]].sort())
  })

  it('rebuilds an empty index when index.json is corrupt', () => {
    const home = tempHome()
    fs.mkdirSync(path.join(home, 'cache', 'render'), { recursive: true })
    fs.writeFileSync(path.join(home, 'cache', 'render', 'index.json'), '{not json')
    const cache = new RenderCache({ home })
    cache.startupCleanup()
    expect(cache.stats().entries).toBe(0)
    const index = JSON.parse(fs.readFileSync(cache.indexFile, 'utf8'))
    expect(index).toEqual({ version: 1, entries: [] })
  })

  it('drops an index entry whose key is not a sha256 directory name', () => {
    const home = tempHome()
    const root = path.join(home, 'cache', 'render')
    fs.mkdirSync(root, { recursive: true })
    const escape = path.join(home, 'cache', 'escape')
    fs.mkdirSync(escape, { recursive: true })
    fs.writeFileSync(path.join(escape, 'manifest.json'), '{}')
    fs.writeFileSync(
      path.join(root, 'index.json'),
      JSON.stringify({
        version: 1,
        entries: [
          { key: '../escape', artifacts: [path.join(escape, 'manifest.json')] },
          { key: 'A'.repeat(64), artifacts: [] },
        ],
      }),
    )
    const cache = new RenderCache({ home })
    cache.startupCleanup()
    expect(cache.stats().entries).toBe(0)
    expect(fs.existsSync(escape)).toBe(true)
    const index = JSON.parse(fs.readFileSync(cache.indexFile, 'utf8'))
    expect(index.entries).toEqual([])
  })

  it('deletes a corrupt entry on a hit and falls back to a miss', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-f')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)
    await cache.promoteJob({
      jobId: 'job-f',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    fs.rmSync(path.join(cache.root, plan.keys[0], 'depth.png'))
    const lookup = cache.lookupAll(plan.inputs)
    expect(lookup).toEqual({ hit: false, corrupt: true })
    expect(fs.existsSync(path.join(cache.root, plan.keys[0]))).toBe(false)
  })

  it('verifies the stored hash on a hit so a silent disk corruption is not served', async () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const file = sceneFile(home)
    const plan = planFor(file, [1])
    const staging = cache.stagingDir('job-hash')
    fs.mkdirSync(staging, { recursive: true })
    writeFrame(staging, 1)
    await cache.promoteJob({
      jobId: 'job-hash',
      keys: plan.keys,
      frames: plan.frames,
      scene: plan.scene,
      userOutdir: null,
    })
    fs.writeFileSync(path.join(cache.root, plan.keys[0], 'beauty.png'), 'rewritten pixels')
    const lookup = cache.lookupAll(plan.inputs)
    expect(lookup).toEqual({ hit: false, corrupt: true })
    expect(fs.existsSync(path.join(cache.root, plan.keys[0]))).toBe(false)
  })
})

describe('render cache staging', () => {
  it('keeps its staging tree inside the cache and never touches the legacy work root', () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    expect(cache.stagingRoot).toBe(path.join(home, 'cache', 'render', '.staging'))
    // A legacy render wrote to home/queue/staging; the cache must not clear it.
    const legacy = path.join(home, 'queue', 'staging', 'job-legacy')
    fs.mkdirSync(legacy, { recursive: true })
    fs.writeFileSync(path.join(legacy, 'beauty_0001.png'), 'pixels')
    const orphan = cache.stagingDir('job-orphan')
    fs.mkdirSync(orphan, { recursive: true })
    fs.writeFileSync(path.join(orphan, 'beauty_0001.png'), 'pixels')
    cache.startupCleanup()
    expect(fs.existsSync(path.join(legacy, 'beauty_0001.png'))).toBe(true)
    expect(fs.existsSync(orphan)).toBe(false)
  })

  it('keeps the staging dir of a job recovered from the queue', () => {
    const home = tempHome()
    const cache = new RenderCache({ home })
    const kept = cache.stagingDir('job-kept')
    fs.mkdirSync(kept, { recursive: true })
    cache.startupCleanup(['job-kept'])
    expect(fs.existsSync(kept)).toBe(true)
    cache.discardStaging('')
    expect(fs.existsSync(cache.stagingRoot)).toBe(true)
  })
})
