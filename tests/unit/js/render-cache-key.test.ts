import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  RENDER_CACHE_DEFAULTS,
  canonicalJson,
  createRenderCache,
  normalizeRenderCacheLimits,
  planRenderCache,
  planRenderCacheWithReason,
  renderCacheKey,
  sceneIdentity,
} from '../../../runtime/core/queue/render-cache.js'

const roots: string[] = []

function tempHome(): string {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-cache-key-'))
  roots.push(home)
  return home
}

function sceneFile(home: string, name = 'scene.blend', body = 'BLENDER-scene'): string {
  const file = path.join(home, name)
  fs.writeFileSync(file, body)
  return file
}

function planFor(file: string, frames: number[], passes = ['beauty', 'depth']) {
  const plan = planRenderCache({ file, passes, frames: [frames[0], frames[frames.length - 1]] })
  if (!plan) throw new Error('scene must be cacheable for this fixture')
  return plan
}

afterEach(() => {
  for (const home of roots.splice(0)) fs.rmSync(home, { recursive: true, force: true })
})

describe('render cache key', () => {
  it('hashes the same inputs to the same key regardless of object key order', () => {
    const first = {
      scene: {
        path: 'D:/work/scene.blend',
        size: 12,
        mtimeMs: 34,
        headSha256: 'a'.repeat(64),
      },
      frame: 3,
      camera: 'Camera',
      resolution: [64, 48] as [number, number],
      samples: 16,
      engine: 'CYCLES',
      passSet: ['beauty', 'depth'],
      format: 'PNG',
      manifestVersion: 1,
    }
    expect(renderCacheKey(first)).toBe(renderCacheKey({ ...first }))
    expect(renderCacheKey(first)).toMatch(/^[0-9a-f]{64}$/)
    expect(canonicalJson({ b: 1, a: [2, 3] })).toBe('{"a":[2,3],"b":1}')
  })

  it('changes the key for every documented cache input component', () => {
    const home = tempHome()
    const plan = planFor(sceneFile(home), [1])
    const base = plan.inputs[0]
    const keys = new Set([renderCacheKey(base)])
    keys.add(renderCacheKey({ ...base, frame: 2 }))
    keys.add(renderCacheKey({ ...base, camera: 'Other' }))
    keys.add(renderCacheKey({ ...base, resolution: [32, 32] }))
    keys.add(renderCacheKey({ ...base, samples: 1 }))
    keys.add(renderCacheKey({ ...base, engine: 'BLENDER_EEVEE_NEXT' }))
    keys.add(renderCacheKey({ ...base, passSet: ['beauty'] }))
    keys.add(renderCacheKey({ ...base, manifestVersion: 2 }))
    keys.add(renderCacheKey({ ...base, scene: { ...base.scene, size: 99 } }))
    keys.add(renderCacheKey({ ...base, scene: { ...base.scene, mtimeMs: 99 } }))
    keys.add(renderCacheKey({ ...base, scene: { ...base.scene, headSha256: 'b'.repeat(64) } }))
    expect(keys.size).toBe(11)
  })

  it('puts the beauty format in the key so a JPEG never answers a PNG request', () => {
    const home = tempHome()
    const file = sceneFile(home)
    const png = planRenderCache({ file, passes: ['beauty'], frames: [1], format: 'PNG' })
    const jpeg = planRenderCache({ file, passes: ['beauty'], frames: [1], format: 'JPEG' })
    expect(png?.inputs[0].format).toBe('PNG')
    expect(jpeg?.inputs[0].format).toBe('JPEG')
    expect(png?.keys[0]).not.toBe(jpeg?.keys[0])
    // An absent format is Blender's default still format, so it must fold into PNG.
    expect(planRenderCache({ file, passes: ['beauty'], frames: [1] })?.keys[0]).toBe(png?.keys[0])
    expect(
      planRenderCache({ file, passes: ['beauty'], frames: [1], format: ' png ' })?.keys[0],
    ).toBe(png?.keys[0])
  })

  it('hashes illegal numbers as null so they cannot share a legal input key', () => {
    const home = tempHome()
    const plan = planRenderCache({
      file: sceneFile(home),
      passes: ['beauty'],
      frames: [1],
      resolution: [-64, Number.NaN],
      samples: Number.POSITIVE_INFINITY,
    })
    expect(plan?.inputs[0].resolution).toBeNull()
    expect(plan?.inputs[0].samples).toBeNull()
    expect(plan?.keys[0]).toMatch(/^[0-9a-f]{64}$/)
  })

  it('refuses to cache an unsaved or unstattable scene, an empty pass set, or a bad frame range', () => {
    const home = tempHome()
    const file = sceneFile(home)
    expect(sceneIdentity('')).toBeNull()
    expect(sceneIdentity(path.join(home, 'missing.blend'))).toBeNull()
    expect(sceneIdentity(home)).toBeNull()
    expect(planRenderCache({ file, frames: [1, 1] })).toBeNull()
    expect(
      planRenderCache({
        file: path.join(home, 'missing.blend'),
        passes: ['beauty'],
        frames: [1, 1],
      }),
    ).toBeNull()
    expect(planRenderCache({ file, passes: ['beauty'] })).toBeNull()
    expect(planRenderCache({ file, passes: ['beauty'], frames: ['9-2'] })).toBeNull()
  })

  it('reports a frame budget refusal instead of silently disabling the cache', () => {
    const home = tempHome()
    const file = sceneFile(home)
    const refused = planRenderCacheWithReason({ file, passes: ['beauty'], frames: [1, 600] })
    expect(refused.plan).toBeNull()
    expect(refused.skipped).toBe('too_many_frames')
    // A render that never asked for passes is not a refusal.
    expect(planRenderCacheWithReason({ file, frames: [1, 600] }).skipped).toBeNull()
  })

  it('normalizes the pass set order and resolves a single frame', () => {
    const home = tempHome()
    const file = sceneFile(home)
    const plan = planRenderCache({ file, passes: ['depth', 'beauty', 'depth'], frames: 7 })
    expect(plan?.frames).toEqual([7])
    expect(plan?.inputs[0].passSet).toEqual(['beauty', 'depth'])
    expect(plan?.inputs[0].camera).toBeNull()
  })

  it('identifies an edited scene whose size and mtime were not refreshed', () => {
    const home = tempHome()
    const file = sceneFile(home)
    const before = sceneIdentity(file)
    fs.writeFileSync(file, 'BLENDER-scene-v2')
    const stat = fs.statSync(file)
    fs.utimesSync(file, stat.atime, stat.mtime)
    const after = sceneIdentity(file)
    expect(after?.headSha256).not.toBe(before?.headSha256)
  })
})

describe('render cache limits', () => {
  it('keeps documented defaults and reports invalid values without clamping', () => {
    expect(normalizeRenderCacheLimits(undefined)).toEqual({
      limits: RENDER_CACHE_DEFAULTS,
      issues: [],
    })
    const resolved = normalizeRenderCacheLimits({ maxEntries: 4, maxBytes: 1024, ttlDays: 2.5 })
    expect(resolved.limits).toEqual({
      maxEntries: 4,
      maxBytes: RENDER_CACHE_DEFAULTS.maxBytes,
      ttlDays: RENDER_CACHE_DEFAULTS.ttlDays,
    })
    expect(resolved.issues.map((issue) => issue.field).sort()).toEqual(['maxBytes', 'ttlDays'])
    expect(
      resolved.issues.every(
        (issue) =>
          issue.used === RENDER_CACHE_DEFAULTS[issue.field as keyof typeof RENDER_CACHE_DEFAULTS],
      ),
    ).toBe(true)
  })

  it('reports field, value and range for doctor', () => {
    const resolved = normalizeRenderCacheLimits({ maxEntries: 99 })
    expect(resolved.issues).toHaveLength(1)
    expect(resolved.issues[0]).toMatchObject({
      field: 'maxEntries',
      value: 99,
      min: 1,
      max: 32,
      reason: 'out-of-range',
      used: RENDER_CACHE_DEFAULTS.maxEntries,
    })
    expect(resolved.issues[0].allowed).toContain('1..32')
  })

  it('builds the production cache from raw cache.render values and reports issues', () => {
    const home = tempHome()
    const built = createRenderCache({ home, rawLimits: { maxEntries: 0, ttlDays: 3 } })
    expect(built).not.toBeNull()
    expect(built?.issues).toHaveLength(1)
    expect(built?.issues[0].field).toBe('maxEntries')
    expect(built?.cache.limits).toEqual({ ...RENDER_CACHE_DEFAULTS, ttlDays: 3 })
    expect(built?.cache.root).toBe(path.join(home, 'cache', 'render'))
  })
})
