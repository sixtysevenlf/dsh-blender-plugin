// Low-level IO and frame helpers for the render-pass cache.
//
// Kept apart from render-cache.ts so both the entry index and the promotion
// pipeline stay inside the per-file budget. Nothing here owns state: every
// helper is a pure function over paths.
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { RENDER_PASSES_MANIFEST_VERSION } from './render-passes-script.js'
import type { StoredSceneIdentity } from './render-cache-key.js'

/** A cache key is a sha256 hex digest; anything else is not a directory name. */
export const KEY_PATTERN = /^[0-9a-f]{64}$/
export const STAGING_LOCK = '.owner'

/** Yield to the event loop so a large promotion never stalls the Gateway. */
export function yieldToEventLoop(): Promise<void> {
  return new Promise<void>((resolve) => {
    setImmediate(resolve)
  })
}

export function sha256File(file: string): string {
  const hash = createHash('sha256')
  const buffer = Buffer.alloc(1024 * 1024)
  const fd = fs.openSync(file, 'r')
  try {
    for (;;) {
      const read = fs.readSync(fd, buffer, 0, buffer.length, null)
      if (read <= 0) break
      hash.update(buffer.subarray(0, read))
    }
  } finally {
    fs.closeSync(fd)
  }
  return hash.digest('hex')
}

/** Rename a directory, falling back to copy+delete across volumes. */
export function moveDir(from: string, to: string): boolean {
  try {
    fs.renameSync(from, to)
    return true
  } catch {
    /* a different volume or a locked handle: fall back to a copy */
  }
  try {
    copyDir(from, to)
    fs.rmSync(from, { recursive: true, force: true })
    return true
  } catch {
    return false
  }
}

export interface RenderCacheEntry {
  key: string
  createdAt: number
  lastAccess: number
  bytes: number
  scene: StoredSceneIdentity
  frame: number
  manifest: string
  artifacts: string[]
  passes: Record<string, unknown>
}

export interface FrameManifest {
  manifest: Record<string, unknown>
  manifestPath: string
  passes: Record<string, unknown>
}

/** Why a frame cannot enter the cache: a lost pass is not the same as a missing file. */
export type FrameRejection = 'manifest_incomplete' | 'degraded'

export type FrameInspection =
  { ok: true; manifest: FrameManifest } | { ok: false; reason: FrameRejection }

/** One staging frame, already inspected and sized, waiting to be promoted. */
export interface PlannedFrame {
  key: string
  frame: number
  dir: string
  manifest: FrameManifest
  bytes: number
}

/** Every frame owns one directory under `<root>/passes/`. */
export function frameDirOf(root: string, frame: number): string {
  return path.join(root, 'passes', 'frame_' + String(frame).padStart(4, '0'))
}

export interface PromoteFrame {
  frame: number
  manifest: string
  artifacts: string[]
  passes: Record<string, string>
}

export function listFiles(dir: string): string[] {
  const found: string[] = []
  const walk = (current: string, depth: number): void => {
    if (depth > 6) return
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name)
      if (entry.isDirectory()) walk(full, depth + 1)
      else found.push(full)
    }
  }
  walk(dir, 0)
  return found.sort()
}

export function treeBytes(dir: string): number {
  let total = 0
  for (const file of listFiles(dir)) {
    try {
      total += fs.statSync(file).size
    } catch {
      /* a file may disappear while sizing */
    }
  }
  return total
}

export function copyDir(from: string, to: string): void {
  fs.mkdirSync(to, { recursive: true })
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    const src = path.join(from, entry.name)
    const dest = path.join(to, entry.name)
    if (entry.isDirectory()) copyDir(src, dest)
    else fs.copyFileSync(src, dest)
  }
}

/**
 * Rewrite a frame manifest's per-pass paths so they point into its new home.
 * The Blender script records absolute staging paths; after promotion those
 * paths are gone, so a manifest that is not relocated would look corrupt.
 */
export function relocateManifest(frameDir: string): FrameManifest | null {
  const manifestPath = path.join(frameDir, 'manifest.json')
  let manifest: Record<string, unknown>
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) as Record<string, unknown>
  } catch {
    return null
  }
  const rawPasses = manifest.passes
  if (typeof rawPasses !== 'object' || rawPasses === null || Array.isArray(rawPasses)) return null
  const passes: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(rawPasses as Record<string, unknown>)) {
    const meta = { ...(value as Record<string, unknown>) }
    if (typeof meta.path !== 'string') return null
    const next = path.join(frameDir, path.basename(meta.path))
    if (!fs.existsSync(next)) return null
    meta.path = next.split(path.sep).join('/')
    passes[name] = meta
  }
  manifest.passes = passes
  try {
    const temporary = manifestPath + '.tmp-' + String(process.pid)
    fs.writeFileSync(temporary, JSON.stringify(manifest, null, 2) + '\n', 'utf8')
    fs.renameSync(temporary, manifestPath)
  } catch {
    return null
  }
  return { manifest, manifestPath, passes }
}

export function frameMeta(frameDir: string, frame: number, manifest: FrameManifest): PromoteFrame {
  const passes: Record<string, string> = {}
  for (const [name, value] of Object.entries(manifest.passes)) {
    const meta = value as Record<string, unknown>
    if (typeof meta.path === 'string') passes[name] = meta.path
  }
  return {
    frame,
    manifest: manifest.manifestPath.split(path.sep).join('/'),
    artifacts: listFiles(frameDir),
    passes,
  }
}

/**
 * Copy one cached frame into a caller directory: a cache hit must still deliver
 * the artifacts at the user's outdir. Returns null when the copy cannot be
 * reproduced, so the caller keeps the cache path instead of a dead one.
 */
export function materializeFrame(
  source: string,
  frame: number,
  destRoot: string,
): PromoteFrame | null {
  if (!fs.existsSync(source)) return null
  const dest = frameDirOf(destRoot, frame)
  try {
    fs.rmSync(dest, { recursive: true, force: true })
    copyDir(source, dest)
    const manifest = relocateManifest(dest)
    if (!manifest) return null
    return frameMeta(dest, frame, manifest)
  } catch {
    return null
  }
}
export function inspectFrame(frameDir: string, frame: number, verifyHash = false): FrameInspection {
  const manifestPath = path.join(frameDir, 'manifest.json')
  let manifest: Record<string, unknown>
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) as Record<string, unknown>
  } catch {
    return { ok: false, reason: 'manifest_incomplete' }
  }
  if (Number(manifest.manifestVersion) !== RENDER_PASSES_MANIFEST_VERSION)
    return { ok: false, reason: 'manifest_incomplete' }
  if (Number(manifest.frame) !== frame) return { ok: false, reason: 'manifest_incomplete' }
  const passes = manifest.passes
  if (typeof passes !== 'object' || passes === null || Array.isArray(passes))
    return { ok: false, reason: 'manifest_incomplete' }
  const names = Object.keys(passes as Record<string, unknown>)
  if (names.length === 0) return { ok: false, reason: 'manifest_incomplete' }
  for (const name of names) {
    const meta = (passes as Record<string, unknown>)[name] as Record<string, unknown>
    if (!meta || typeof meta.path !== 'string' || !fs.existsSync(meta.path))
      return { ok: false, reason: 'manifest_incomplete' }
    if (verifyHash && !hashMatches(meta)) return { ok: false, reason: 'manifest_incomplete' }
  }
  if (manifest.degraded === true) return { ok: false, reason: 'degraded' }
  if (Array.isArray(manifest.degraded) && manifest.degraded.length > 0)
    return { ok: false, reason: 'degraded' }
  const requested = manifest.requestedPasses
  if (Array.isArray(requested)) {
    for (const name of requested) {
      if (typeof name !== 'string' || !Object.hasOwn(passes, name))
        return { ok: false, reason: 'degraded' }
    }
  }
  return {
    ok: true,
    manifest: { manifest, manifestPath, passes: passes as Record<string, unknown> },
  }
}

function hashMatches(meta: Record<string, unknown>): boolean {
  const expected = typeof meta.sha256 === 'string' ? meta.sha256 : ''
  if (!KEY_PATTERN.test(expected)) return true
  try {
    return sha256File(String(meta.path)) === expected
  } catch {
    return false
  }
}

export function readFrameManifest(
  frameDir: string,
  frame: number,
  verifyHash = false,
): FrameManifest | null {
  const inspected = inspectFrame(frameDir, frame, verifyHash)
  return inspected.ok ? inspected.manifest : null
}
