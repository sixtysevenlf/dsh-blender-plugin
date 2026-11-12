// Render-pass cache key: pure helpers, no filesystem mutation.
//
// The key covers every render input (render-cache design): scene identity (normalized
// .blend path + size + mtime + head hash), frame, camera, resolution, samples, engine,
// the sorted pass set, the beauty image format and the manifest version. A scene that
// cannot be stat'ed is never cacheable.
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { RENDER_PASSES_MANIFEST_VERSION } from './render-passes-script.js'

export interface RenderCacheLimits {
  maxEntries: number
  maxBytes: number
  ttlDays: number
}

export const RENDER_CACHE_DEFAULTS: RenderCacheLimits = {
  maxEntries: 8,
  maxBytes: 4 * 1024 * 1024 * 1024,
  ttlDays: 7,
}

/**
 * Documented ranges, mirroring the host config schema (src/plugin.ts) so the
 * config card, the docs and doctor all quote one set of numbers. An
 * out-of-range value is rejected, never silently clamped.
 */
export const RENDER_CACHE_LIMIT_RANGES = {
  maxEntries: { min: 1, max: 32 },
  maxBytes: { min: 512 * 1024 * 1024, max: 16 * 1024 * 1024 * 1024 },
  ttlDays: { min: 1, max: 30 },
}

/** One entry larger than this is not cached; the job still returns its artifacts. */
export const RENDER_CACHE_MAX_ENTRY_BYTES = 2 * 1024 * 1024 * 1024

/**
 * One rejected cache.render.* value, shaped for doctor: field name, the value
 * that was given, the human-readable rule, the machine-readable bounds, why it
 * was rejected, and the documented default that is actually used instead.
 */
export interface RenderCacheLimitIssue {
  field: string
  value: unknown
  allowed: string
  min: number
  max: number
  reason: string
  used: number
}

export interface RenderCacheLimitResolution {
  limits: RenderCacheLimits
  issues: RenderCacheLimitIssue[]
}

function isIntInRange(value: unknown, min: number, max: number): boolean {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max
}

/**
 * Resolve cache.render.* without ever clamping: a bad value falls back to the
 * documented default and is reported so `doctor` can show field/value/range.
 */
export function normalizeRenderCacheLimits(raw: unknown): RenderCacheLimitResolution {
  const source = typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : {}
  const issues: RenderCacheLimitIssue[] = []
  const limits = { ...RENDER_CACHE_DEFAULTS }
  const rules: Array<{ field: keyof RenderCacheLimits; allowed: string }> = [
    {
      field: 'maxEntries',
      allowed:
        '整数 ' +
        RENDER_CACHE_LIMIT_RANGES.maxEntries.min +
        '..' +
        RENDER_CACHE_LIMIT_RANGES.maxEntries.max,
    },
    {
      field: 'maxBytes',
      allowed:
        '整数 ' +
        String(RENDER_CACHE_LIMIT_RANGES.maxBytes.min) +
        '..' +
        String(RENDER_CACHE_LIMIT_RANGES.maxBytes.max) +
        ' 字节',
    },
    {
      field: 'ttlDays',
      allowed:
        '整数 ' +
        RENDER_CACHE_LIMIT_RANGES.ttlDays.min +
        '..' +
        RENDER_CACHE_LIMIT_RANGES.ttlDays.max,
    },
  ]
  for (const rule of rules) {
    const value = source[rule.field]
    if (value === undefined || value === null) continue
    const range = RENDER_CACHE_LIMIT_RANGES[rule.field]
    if (isIntInRange(value, range.min, range.max)) {
      limits[rule.field] = value as number
      continue
    }
    const reason =
      typeof value !== 'number' || !Number.isInteger(value) ? 'not-an-integer' : 'out-of-range'
    issues.push({
      field: rule.field,
      value,
      allowed: rule.allowed,
      min: range.min,
      max: range.max,
      reason,
      used: limits[rule.field],
    })
  }
  return { limits, issues }
}

/**
 * Stable JSON (recursively sorted keys) so one input set hashes to one key.
 * Numbers are normalized first: `NaN`/`Infinity` cannot be part of a key (they
 * stringify to `null`, which would let an illegal input share a legal one's key),
 * so a non-finite number is hashed as `null` deterministically.
 */
export function canonicalJson(value: unknown): string {
  if (typeof value === 'number') return JSON.stringify(Number.isFinite(value) ? value : null)
  if (Array.isArray(value)) return '[' + value.map((item) => canonicalJson(item)).join(',') + ']'
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>
    const keys = Object.keys(record).sort()
    return (
      '{' +
      keys.map((key) => JSON.stringify(key) + ':' + canonicalJson(record[key])).join(',') +
      '}'
    )
  }
  return JSON.stringify(value === undefined ? null : value)
}

export interface SceneIdentity {
  path: string
  size: number
  mtimeMs: number
  /**
   * sha256 of the first `SCENE_HEAD_BYTES` of the .blend. Size+mtime alone is coarse
   * on some filesystems (FAT/network shares can keep a 2 s or 1 s mtime granularity),
   * and an in-place edit that keeps the size would otherwise hit the old frame.
   */
  headSha256: string
}

/** A stored entry only needs the identity fields it reports; the head hash is optional. */
export type StoredSceneIdentity = Omit<SceneIdentity, 'headSha256'> & { headSha256?: string }

export interface RenderCacheKeyInput {
  scene: SceneIdentity
  frame: number
  camera: string | null
  resolution: [number, number] | null
  samples: number | null
  engine: string | null
  passSet: string[]
  /** Normalized beauty image format (upper case, `PNG` when not requested). */
  format: string
  manifestVersion: number
}

export function renderCacheKey(input: RenderCacheKeyInput): string {
  return createHash('sha256').update(canonicalJson(input)).digest('hex')
}

/** Cheap identity for a large file: the first N bytes, never the whole scene. */
export const SCENE_HEAD_BYTES = 64 * 1024

function headHash(file: string): string | null {
  const buffer = Buffer.alloc(SCENE_HEAD_BYTES)
  let fd: number
  try {
    fd = fs.openSync(file, 'r')
  } catch {
    return null
  }
  try {
    const read = fs.readSync(fd, buffer, 0, SCENE_HEAD_BYTES, 0)
    return createHash('sha256')
      .update(buffer.subarray(0, Math.max(0, read)))
      .digest('hex')
  } catch {
    return null
  } finally {
    fs.closeSync(fd)
  }
}

/** Scene identity is statable or the render is not cacheable at all. */
export function sceneIdentity(file: string): SceneIdentity | null {
  if (!file || !file.trim()) return null
  try {
    const resolved = path.resolve(file)
    const stat = fs.statSync(resolved)
    if (!stat.isFile()) return null
    const normalized = process.platform === 'win32' ? resolved.toLowerCase() : resolved
    const head = headHash(resolved)
    if (head === null) return null
    return { path: normalized, size: stat.size, mtimeMs: stat.mtimeMs, headSha256: head }
  } catch {
    return null
  }
}

function resolveFrames(raw: unknown): { start: number; end: number } | null {
  if (Array.isArray(raw) && raw.length === 2) {
    const start = Number(raw[0])
    const end = Number(raw[1])
    if (Number.isFinite(start) && Number.isFinite(end) && end >= start) return { start, end }
    return null
  }
  if (typeof raw === 'string' && raw.includes('-')) {
    const [a, b] = raw.split('-')
    if (Number.isFinite(Number(a)) && Number.isFinite(Number(b)) && Number(b) >= Number(a))
      return { start: Number(a), end: Number(b) }
    return null
  }
  if (raw !== undefined && raw !== null) {
    const n = Number(raw)
    if (Number.isFinite(n)) return { start: n, end: n }
  }
  return null
}

const MAX_CACHE_FRAMES = 512

function passSetOf(params: Record<string, unknown>): string[] {
  const raw = params.passes
  if (!Array.isArray(raw)) return []
  const names: string[] = []
  for (const item of raw) if (typeof item === 'string' && item.trim()) names.push(item.trim())
  return Array.from(new Set(names)).sort()
}

function cameraOf(params: Record<string, unknown>): string | null {
  if (params.camera === true) return 'scene'
  const raw = params.camera
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null
}

/**
 * Validate before hashing: an unusable resolution is part of the key as `null` and
 * can never share a key with a real one, and no NaN/negative value is hashed.
 */
function resolutionOf(params: Record<string, unknown>): [number, number] | null {
  const raw = params.resolution
  if (!Array.isArray(raw) || raw.length !== 2) return null
  const width = numberOrNull(raw[0])
  const height = numberOrNull(raw[1])
  if (width === null || height === null || width <= 0 || height <= 0) return null
  return [Math.round(width), Math.round(height)]
}

/** Blender's default still format is PNG; a format change must never reuse pixels. */
function formatOf(params: Record<string, unknown>): string {
  const raw = params.format
  const text = typeof raw === 'string' ? raw.trim().toUpperCase() : ''
  return text || 'PNG'
}

function numberOrNull(value: unknown): number | null {
  if (value === undefined || value === null) return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export interface RenderCachePlan {
  keys: string[]
  frames: number[]
  inputs: RenderCacheKeyInput[]
  scene: SceneIdentity
}

/**
 * A plan plus why there is none. `skipped` is only set when the render is a real
 * pass render that the cache had to refuse (today: the frame budget), because a
 * render that never asked for passes must not report cache metadata at all.
 */
export interface RenderCachePlanResult {
  plan: RenderCachePlan | null
  skipped: string | null
  scene: SceneIdentity | null
}

/** Per-frame key plan for a render job; null means "not cacheable, do not cache". */
export function planRenderCache(params: Record<string, unknown>): RenderCachePlan | null {
  return planRenderCacheWithReason(params).plan
}

/**
 * Same plan, but a refused pass render explains itself instead of silently
 * rendering without a cache (the receipt reports `skipped`).
 */
export function planRenderCacheWithReason(params: Record<string, unknown>): RenderCachePlanResult {
  const passSet = passSetOf(params)
  if (passSet.length === 0) return { plan: null, skipped: null, scene: null }
  const file = typeof params.file === 'string' ? params.file : ''
  const scene = sceneIdentity(file)
  if (!scene) return { plan: null, skipped: null, scene: null }
  const range = resolveFrames(params.frames)
  if (!range) return { plan: null, skipped: null, scene }
  if (range.end - range.start + 1 > MAX_CACHE_FRAMES)
    return { plan: null, skipped: 'too_many_frames', scene }
  const camera = cameraOf(params)
  const resolution = resolutionOf(params)
  const samples = numberOrNull(params.samples)
  const format = formatOf(params)
  const engine =
    typeof params.engine === 'string' && params.engine.trim() ? params.engine.trim() : null
  const inputs: RenderCacheKeyInput[] = []
  const frames: number[] = []
  const keys: string[] = []
  for (let frame = range.start; frame <= range.end; frame += 1) {
    const input: RenderCacheKeyInput = {
      scene,
      frame,
      camera,
      resolution,
      samples,
      engine,
      passSet,
      format,
      manifestVersion: RENDER_PASSES_MANIFEST_VERSION,
    }
    inputs.push(input)
    frames.push(frame)
    keys.push(renderCacheKey(input))
  }
  return { plan: { keys, frames, inputs, scene }, skipped: null, scene }
}
