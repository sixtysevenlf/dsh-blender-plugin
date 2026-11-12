// Content-addressed render-pass cache (render-cache design).
//
// Enabled only for render jobs that explicitly request passes. Every entry is a
// whole frame directory (pass files + manifest.json) named by a key over every
// render input, so a hit is never a stale picture: the .blend identity (path,
// size, mtime, head hash), frame, camera, resolution, samples, engine, pass set,
// beauty format and manifest version are all part of the key
// (see render-cache-key.ts).
//
// Failure is never cached: promotion happens only after Blender exited 0, every
// frame manifest is complete and no frame lost a requested pass (degraded).
// Index and entry writes are atomic.
import fs from 'node:fs'
import path from 'node:path'
import { CFG } from '../../config.js'
import { getHome } from '../../home.js'
import {
  RENDER_CACHE_DEFAULTS,
  RENDER_CACHE_MAX_ENTRY_BYTES,
  normalizeRenderCacheLimits,
  renderCacheKey,
} from './render-cache-key.js'
import type {
  RenderCacheKeyInput,
  RenderCacheLimitIssue,
  RenderCacheLimits,
  StoredSceneIdentity,
} from './render-cache-key.js'
import {
  KEY_PATTERN,
  STAGING_LOCK,
  inspectFrame,
  readFrameManifest,
  relocateManifest,
  copyDir,
  frameDirOf,
  frameMeta,
  listFiles,
  materializeFrame,
  moveDir,
  treeBytes,
  yieldToEventLoop,
} from './render-cache-io.js'
import type {
  FrameManifest,
  FrameRejection,
  PlannedFrame,
  PromoteFrame,
  RenderCacheEntry,
} from './render-cache-io.js'

export type { PromoteFrame, RenderCacheEntry } from './render-cache-io.js'

export * from './render-cache-key.js'

export interface RenderCacheOptions {
  home: string
  limits?: Partial<RenderCacheLimits>
  now?: () => number
  maxEntryBytes?: number
  /** Re-hash cached pass files on a hit (design: verify hash or do not claim one). */
  verifyHashes?: boolean
}

export interface PromoteResult {
  ok: boolean
  artifacts: string[]
  entries: number
  skipped: string | null
  reason?: string
  /** Final (cache or user-outdir) locations per frame, for receipts and parsed summaries. */
  frames: PromoteFrame[]
}

export class RenderCache {
  readonly root: string
  readonly stagingRoot: string
  readonly jobsRoot: string
  readonly indexFile: string
  readonly stagingLock: string
  readonly limits: RenderCacheLimits
  readonly maxEntryBytes: number
  private readonly now: () => number
  private readonly verifyHashes: boolean
  private entries = new Map<string, RenderCacheEntry>()
  private loaded = false

  constructor(opts: RenderCacheOptions) {
    this.root = path.join(opts.home, 'cache', 'render')
    // Staging lives inside the cache tree: home/queue/staging is the legacy render
    // work root, so clearing it here used to delete non-cache artifacts.
    this.stagingRoot = path.join(this.root, '.staging')
    this.jobsRoot = path.join(opts.home, 'jobs')
    this.indexFile = path.join(this.root, 'index.json')
    this.stagingLock = path.join(this.stagingRoot, STAGING_LOCK)
    this.limits = { ...RENDER_CACHE_DEFAULTS, ...opts.limits }
    this.maxEntryBytes = opts.maxEntryBytes ?? RENDER_CACHE_MAX_ENTRY_BYTES
    this.verifyHashes = opts.verifyHashes !== false
    this.now = opts.now || (() => Date.now())
  }

  /** Read the index; a corrupt index is quarantined and rebuilt empty. */
  load(): void {
    if (this.loaded) return
    this.loaded = true
    let raw: unknown
    try {
      raw = JSON.parse(fs.readFileSync(this.indexFile, 'utf8'))
    } catch {
      return
    }
    const record = typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : {}
    const list = Array.isArray(record.entries) ? record.entries : []
    for (const item of list) {
      const entry = item as RenderCacheEntry
      if (!entry || typeof entry.key !== 'string' || !Array.isArray(entry.artifacts)) continue
      // The key is a directory name: anything that is not a sha256 digest could
      // escape home/cache/render, so a corrupt index entry is simply dropped.
      if (!KEY_PATTERN.test(entry.key)) continue
      this.entries.set(entry.key, entry)
    }
  }

  /** Startup: drop expired/over-limit/missing entries and orphan staging dirs. */
  startupCleanup(keepJobIds: string[] = []): void {
    this.load()
    this.clearStaging(keepJobIds)
    this.prune()
    this.writeIndex()
  }

  /**
   * Another live Gateway already owns this staging tree: never delete its work.
   * Two Gateways sharing one home used to delete each other's in-flight frames.
   */
  private stagingOwnedElsewhere(): boolean {
    try {
      const raw = JSON.parse(fs.readFileSync(this.stagingLock, 'utf8')) as { pid?: unknown }
      const pid = Number(raw?.pid)
      if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) return false
      process.kill(pid, 0)
      return true
    } catch {
      return false
    }
  }

  private writeStagingLock(): void {
    try {
      fs.mkdirSync(this.stagingRoot, { recursive: true })
      fs.writeFileSync(
        this.stagingLock,
        JSON.stringify({ pid: process.pid, startedAt: this.now() }) + '\n',
        'utf8',
      )
    } catch {
      /* an advisory lock never blocks the queue */
    }
  }

  /**
   * Remove orphan staging work only. Native `.staging` only: the legacy render work
   * root (home/queue/staging) is not ours to clean, and a lock from another live
   * Gateway makes the whole tree somebody else's work in progress.
   */
  clearStaging(keepJobIds: string[] = []): void {
    if (this.stagingOwnedElsewhere()) return
    this.writeStagingLock()
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(this.stagingRoot, { withFileTypes: true })
    } catch {
      return
    }
    const keep = new Set(keepJobIds)
    for (const entry of entries) {
      if (entry.name === STAGING_LOCK || keep.has(entry.name)) continue
      try {
        fs.rmSync(path.join(this.stagingRoot, entry.name), { recursive: true, force: true })
      } catch {
        /* best effort */
      }
    }
  }

  stagingDir(jobId: string): string {
    return path.join(this.stagingRoot, jobId)
  }

  private entryDir(key: string): string {
    return path.join(this.root, key)
  }

  /**
   * A frame may enter the cache only as a clean, complete render:
   * - the manifest parses, matches this frame and the current manifest version;
   * - every declared pass file exists;
   * - no requested pass is missing (`degraded` array or an uncovered
   *   `requestedPasses` entry) — a degraded frame is not a clean success.
   * `verifyHash` re-hashes the declared pass files so a silent disk corrupt hit
   * cannot be served as fresh pixels.
   */
  /** All frames must hit; a corrupt entry is deleted and reported so the caller re-renders. */
  lookupAll(
    inputs: RenderCacheKeyInput[],
  ): { hit: true; entries: RenderCacheEntry[] } | { hit: false; corrupt: boolean } {
    this.load()
    const entries: RenderCacheEntry[] = []
    let corrupt = false
    for (const input of inputs) {
      const key = renderCacheKey(input)
      const entry = this.entries.get(key)
      if (!entry) return { hit: false, corrupt }
      const frameDir = this.entryDir(key)
      const manifest = readFrameManifest(frameDir, input.frame, this.verifyHashes)
      const missing = entry.artifacts.some((file) => !fs.existsSync(file))
      if (!manifest || missing) {
        this.deleteEntry(key)
        corrupt = true
        return { hit: false, corrupt }
      }
      entry.lastAccess = this.now()
      entries.push(entry)
    }
    // A hit is a use: persist the refreshed lastAccess so LRU survives restarts.
    if (entries.length > 0) this.writeIndex()
    return { hit: true, entries }
  }

  /**
   * Copy one cached frame into a caller directory: a cache hit must still deliver
   * the artifacts at the user's outdir. Returns null when the copy cannot be
   * reproduced, so the caller keeps the cache path instead of a dead one.
   */
  materialize(entry: RenderCacheEntry, destRoot: string): PromoteFrame | null {
    return materializeFrame(this.entryDir(entry.key), entry.frame, destRoot)
  }

  /**
   * Promote a finished job's staging tree into the cache. All-or-nothing: one
   * incomplete or degraded manifest, one frame over the per-entry cap, or a frame
   * set larger than the whole-cache budget leaves the cache untouched and moves the
   * artifacts to home/jobs/<jobId>/.
   *
   * Frames move through a temporary incoming directory first, and a failure mid-way
   * puts them back in the job dir instead of deleting rendered pixels.
   */
  async promoteJob(args: {
    jobId: string
    keys: string[]
    frames: number[]
    scene: StoredSceneIdentity
    userOutdir: string | null
  }): Promise<PromoteResult> {
    this.load()
    const staging = this.stagingDir(args.jobId)
    if (!fs.existsSync(staging)) {
      return { ok: false, artifacts: [], entries: 0, skipped: 'staging_missing', frames: [] }
    }
    const inspection = await this.inspectPromotion(staging, args.keys, args.frames)
    if ('rejected' in inspection)
      return this.abandonStaging(args.jobId, staging, inspection.rejected, args.userOutdir)
    const planned = inspection.planned
    // Size the whole frame set first: promote-then-prune would briefly write far
    // more than cache.render.maxBytes.
    const totalBytes = planned.reduce((sum, item) => sum + item.bytes, 0)
    if (totalBytes > this.limits.maxBytes)
      return this.abandonStaging(args.jobId, staging, 'oversize', args.userOutdir)
    fs.mkdirSync(this.root, { recursive: true })
    const incoming = fs.mkdtempSync(path.join(this.root, '.incoming-'))
    const moved: PlannedFrame[] = []
    const promoted: Array<{ entry: RenderCacheEntry; manifest: FrameManifest }> = []
    try {
      const placed = await this.moveIntoIncoming(planned, incoming, moved)
      await this.moveIntoCache(placed, args.scene, promoted)
    } catch (e) {
      this.rollbackPromotion(staging, incoming, moved, promoted)
      return this.abandonStaging(
        args.jobId,
        staging,
        'promote_failed:' + (e instanceof Error ? e.message : String(e)),
        args.userOutdir,
      )
    }
    this.writeIndex()
    try {
      fs.rmSync(incoming, { recursive: true, force: true })
      fs.rmSync(staging, { recursive: true, force: true })
    } catch {
      /* the index already points at the promoted copies */
    }
    // An entry the budget evicted again must not appear in the receipt.
    const kept = promoted.filter((item) => this.entries.has(item.entry.key))
    const receipt = await this.deliver(kept, args.userOutdir)
    return { ok: true, entries: kept.length, skipped: null, ...receipt }
  }

  /** Inspect every staging frame up front: one rejection refuses the whole job. */
  private async inspectPromotion(
    staging: string,
    keys: string[],
    frames: number[],
  ): Promise<{ planned: PlannedFrame[] } | { rejected: FrameRejection | 'oversize' }> {
    const planned: PlannedFrame[] = []
    for (let index = 0; index < keys.length; index += 1) {
      const frame = frames[index]
      const dir = frameDirOf(staging, frame)
      // A degraded frame lost a requested pass: it is not a clean render.
      const inspected = inspectFrame(dir, frame)
      if (!inspected.ok) return { rejected: inspected.reason }
      const bytes = treeBytes(dir)
      if (bytes > this.maxEntryBytes) return { rejected: 'oversize' }
      planned.push({ key: keys[index], frame, dir, manifest: inspected.manifest, bytes })
      await yieldToEventLoop()
    }
    return { planned }
  }

  /** Move frames into the temporary incoming dir; each move is registered for rollback. */
  private async moveIntoIncoming(
    planned: PlannedFrame[],
    incoming: string,
    moved: PlannedFrame[],
  ): Promise<PlannedFrame[]> {
    const placed: PlannedFrame[] = []
    for (const item of planned) {
      const dir = path.join(incoming, item.key)
      if (!moveDir(item.dir, dir)) throw new Error('incoming_move_failed')
      // Register the move immediately: a later failure must put this frame back.
      moved.push({ ...item, dir })
      const manifest = relocateManifest(dir)
      if (!manifest) throw new Error('manifest_relocate_failed')
      placed.push({ ...item, dir, manifest })
      await yieldToEventLoop()
    }
    return placed
  }

  /** Rename the incoming frames into their cache entries, evicting as they land. */
  private async moveIntoCache(
    placed: PlannedFrame[],
    scene: StoredSceneIdentity,
    promoted: Array<{ entry: RenderCacheEntry; manifest: FrameManifest }>,
  ): Promise<void> {
    for (const item of placed) {
      const target = this.entryDir(item.key)
      fs.rmSync(target, { recursive: true, force: true })
      fs.mkdirSync(path.dirname(target), { recursive: true })
      if (!moveDir(item.dir, target)) throw new Error('promote_move_failed')
      const manifest = relocateManifest(target)
      if (!manifest) throw new Error('manifest_relocate_failed')
      const entry = this.entryFromDir(item.key, item.frame, target, manifest, scene)
      this.entries.set(entry.key, entry)
      promoted.push({ entry, manifest })
      // Evict as frames land so one promotion never overshoots the budget.
      this.prune()
      await yieldToEventLoop()
    }
  }

  /** Undo a partial promotion: no entry survives and every rendered frame goes back. */
  private rollbackPromotion(
    staging: string,
    incoming: string,
    moved: PlannedFrame[],
    promoted: Array<{ entry: RenderCacheEntry; manifest: FrameManifest }>,
  ): void {
    for (const item of promoted) {
      this.entries.delete(item.entry.key)
      try {
        fs.rmSync(this.entryDir(item.entry.key), { recursive: true, force: true })
      } catch {
        /* best effort */
      }
    }
    for (const item of moved) {
      const back = frameDirOf(staging, item.frame)
      if (fs.existsSync(back)) continue
      try {
        fs.mkdirSync(path.dirname(back), { recursive: true })
      } catch {
        /* best effort */
      }
      for (const candidate of [this.entryDir(item.key), path.join(incoming, item.key)]) {
        if (fs.existsSync(candidate) && moveDir(candidate, back)) break
      }
    }
    try {
      fs.rmSync(incoming, { recursive: true, force: true })
    } catch {
      /* best effort */
    }
  }

  /** Where the caller finds the frames: the user outdir when asked, else the cache. */
  private async deliver(
    kept: Array<{ entry: RenderCacheEntry; manifest: FrameManifest }>,
    userOutdir: string | null,
  ): Promise<{ artifacts: string[]; frames: PromoteFrame[] }> {
    const cachedArtifacts = kept.flatMap((item) => item.entry.artifacts)
    const cachedFrames = kept.map((item) =>
      frameMeta(this.entryDir(item.entry.key), item.entry.frame, item.manifest),
    )
    if (!userOutdir) return { artifacts: cachedArtifacts, frames: cachedFrames }
    const copies: string[] = []
    const copiedFrames: PromoteFrame[] = []
    for (const item of kept) {
      const dest = path.join(
        userOutdir,
        'passes',
        'frame_' + String(item.entry.frame).padStart(4, '0'),
      )
      try {
        copyDir(this.entryDir(item.entry.key), dest)
        const manifest = relocateManifest(dest)
        if (!manifest) throw new Error('manifest_relocate_failed')
        copiedFrames.push(frameMeta(dest, item.entry.frame, manifest))
        copies.push(...listFiles(dest))
      } catch {
        // A failed user copy keeps the cache path rather than a dead one.
        return { artifacts: cachedArtifacts, frames: cachedFrames }
      }
      await yieldToEventLoop()
    }
    return { artifacts: copies, frames: copiedFrames }
  }
  private entryFromDir(
    key: string,
    frame: number,
    dir: string,
    manifest: FrameManifest,
    scene: StoredSceneIdentity,
  ): RenderCacheEntry {
    return {
      key,
      createdAt: this.now(),
      lastAccess: this.now(),
      bytes: treeBytes(dir),
      scene,
      frame,
      manifest: path.join(dir, 'manifest.json'),
      artifacts: listFiles(dir),
      passes: manifest.passes,
    }
  }

  /**
   * A skipped promotion still returns the artifacts: move staging to
   * home/jobs/<jobId>/ and, when the caller asked for an outdir, copy the
   * frames there too so the render is never lost because caching declined it.
   */
  private abandonStaging(
    jobId: string,
    staging: string,
    skipped: string,
    userOutdir: string | null,
  ): PromoteResult {
    const target = path.join(this.jobsRoot, jobId)
    try {
      fs.mkdirSync(this.jobsRoot, { recursive: true })
      fs.rmSync(target, { recursive: true, force: true })
      fs.renameSync(staging, target)
    } catch {
      return {
        ok: false,
        artifacts: [],
        entries: 0,
        skipped,
        reason: 'staging_move_failed',
        frames: [],
      }
    }
    let artifacts = listFiles(target)
    const frames: PromoteFrame[] = []
    if (userOutdir) {
      try {
        const copies = path.join(userOutdir, 'passes')
        fs.mkdirSync(copies, { recursive: true })
        const source = path.join(target, 'passes')
        if (fs.existsSync(source)) {
          for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
            if (!entry.isDirectory()) continue
            const dest = path.join(copies, entry.name)
            copyDir(path.join(source, entry.name), dest)
            const manifest = relocateManifest(dest)
            const frame = Number(entry.name.replace(/[^0-9]/g, ''))
            if (manifest && Number.isFinite(frame)) frames.push(frameMeta(dest, frame, manifest))
          }
          const copied = listFiles(copies)
          if (copied.length > 0) artifacts = copied
        }
      } catch {
        /* the moved artifacts under home/jobs remain the receipt */
      }
    }
    return { ok: true, artifacts, entries: 0, skipped, frames }
  }

  discardStaging(jobId: string): void {
    // Never resolve to the staging root itself: a job without a staging dir
    // must not wipe the shared cache staging tree, and a partial promotion
    // must not delete rendered frames.
    if (!jobId) return
    try {
      fs.rmSync(this.stagingDir(jobId), { recursive: true, force: true })
    } catch {
      /* best effort */
    }
  }

  deleteEntry(key: string): void {
    this.entries.delete(key)
    try {
      fs.rmSync(this.entryDir(key), { recursive: true, force: true })
    } catch {
      /* best effort */
    }
  }

  private totalBytes(): number {
    let total = 0
    for (const entry of this.entries.values()) total += Number(entry.bytes) || 0
    return total
  }

  /** TTL + LRU pruning; corrupt entries are dropped first. */
  prune(): void {
    const now = this.now()
    const ttlMs = this.limits.ttlDays * 24 * 60 * 60 * 1000
    for (const [key, entry] of Array.from(this.entries.entries())) {
      if (!fs.existsSync(this.entryDir(key)) || now - Number(entry.lastAccess) > ttlMs)
        this.deleteEntry(key)
    }
    let list = Array.from(this.entries.values()).sort((a, b) => a.lastAccess - b.lastAccess)
    while (
      list.length > 0 &&
      (list.length > this.limits.maxEntries || this.totalBytes() > this.limits.maxBytes)
    ) {
      const oldest = list[0]
      this.deleteEntry(oldest.key)
      list = list.slice(1)
    }
  }

  /** Atomic index write (temp file + rename), then evict to the configured limits. */
  writeIndex(): void {
    this.prune()
    try {
      fs.mkdirSync(this.root, { recursive: true })
      const payload = JSON.stringify(
        { version: 1, entries: Array.from(this.entries.values()) },
        null,
        2,
      )
      const temporary = this.indexFile + '.tmp-' + String(process.pid)
      fs.writeFileSync(temporary, payload + '\n', 'utf8')
      fs.renameSync(temporary, this.indexFile)
    } catch {
      /* a cache write must never fail a render job */
    }
  }

  stats(): { entries: number; bytes: number; limits: RenderCacheLimits } {
    return { entries: this.entries.size, bytes: this.totalBytes(), limits: { ...this.limits } }
  }
}

/** Default production cache: home/cache/render with cache.render.* limits. */
export function createRenderCache(
  options: { home?: string; rawLimits?: unknown; now?: () => number } = {},
): { cache: RenderCache; issues: RenderCacheLimitIssue[] } | null {
  try {
    const home = options.home || getHome()
    const raw =
      options.rawLimits ?? (CFG as unknown as { cache?: { render?: unknown } }).cache?.render
    const resolved = normalizeRenderCacheLimits(raw)
    return {
      cache: new RenderCache({ home, limits: resolved.limits, now: options.now }),
      issues: resolved.issues,
    }
  } catch {
    return null
  }
}
