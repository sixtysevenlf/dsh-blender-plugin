import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  runVisionPass,
  runVisionPassAsync,
  VISION_PASS_LIMITS,
} from '../../../runtime/vision/pass.js'
import { encodePng, gray16Png, gray8Png, rgb8Png } from '../../helpers/png-writer.js'

let dir: string

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-vision-pass-'))
})

afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true })
})

function writePass(file: string, png: Buffer): string {
  const full = path.join(dir, file)
  fs.mkdirSync(path.dirname(full), { recursive: true })
  fs.writeFileSync(full, png)
  return file
}

function manifestFile(name: string, body: Record<string, unknown>): string {
  const file = path.join(dir, name + '.json')
  fs.writeFileSync(file, JSON.stringify(body))
  return file
}

function findArrays(value: unknown, out: unknown[][] = []): unknown[][] {
  if (Array.isArray(value)) {
    out.push(value)
    for (const item of value) findArrays(item, out)
  } else if (value && typeof value === 'object') {
    for (const item of Object.values(value as Record<string, unknown>)) findArrays(item, out)
  }
  return out
}

function hasKey(value: unknown, key: string): boolean {
  if (Array.isArray(value)) return value.some((item) => hasKey(item, key))
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).some(
      ([k, v]) => k === key || hasKey(v, key),
    )
  }
  return false
}

describe('vision_pass: depth', () => {
  it('decodes 16-bit depth to meters with the manifest near/far formula', () => {
    const depth = gray16Png(8, 8, (x, y) =>
      x === 0 && y === 0 ? 0 : x === 7 && y === 7 ? 65535 : 32768,
    )
    writePass('f0/depth.png', depth)
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [8, 8],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 10.1 },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(true)
    const frames = out.frames as Array<Record<string, any>>
    expect(frames).toHaveLength(1)
    const d = frames[0].depth
    expect(d.unit).toBe('m')
    expect(d.near).toBe(0.1)
    expect(d.far).toBe(10.1)
    // 32768 -> near + (32768/65535)*(far-near). Only raw 0 (no geometry) is
    // invalid now; raw 65535 is a far-plane sample and stays valid.
    const expected = 0.1 + (32768 / 65535) * 10
    expect(d.valid_pixels).toBe(63)
    expect(d.invalid_ratio).toBeCloseTo(1 / 64, 6)
    expect(d.min).toBeCloseTo(expected, 6)
    expect(d.max).toBeCloseTo(10.1, 6)
    expect(d.mean).toBeCloseTo((62 * expected + 10.1) / 63, 6)
    expect(d.p50).toBeCloseTo(expected, 6)
    expect(d.decoded).toContain('near')
    expect(d.decoded).toContain('noGeometryValue')
    const aggregate = out.aggregate as Record<string, any>
    expect(aggregate.depth.unit).toBe('m')
    expect(aggregate.depth.min.mean).toBeCloseTo(expected, 6)
  })

  it('reports null statistics when every pixel is a sentinel', () => {
    writePass(
      'f0/depth.png',
      gray16Png(4, 4, () => 0),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [4, 4],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    const d = (out.frames as Array<Record<string, any>>)[0].depth
    expect(d.min).toBeNull()
    expect(d.mean).toBeNull()
    expect(d.invalid_ratio).toBe(1)
  })

  it('keeps near-plane raw 1 valid and only rejects noGeometryValue 0', () => {
    writePass(
      'f0/depth.png',
      gray16Png(4, 4, (x, y) => (x === 0 && y === 0 ? 0 : x === 1 && y === 0 ? 1 : 32768)),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [4, 4],
      passes: {
        depth: {
          path: 'f0/depth.png',
          dtype: 'uint16',
          range: [0, 65535],
          near: 0.1,
          far: 10.1,
          noGeometryValue: 0,
          clampedAt: 'far',
        },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    const d = (out.frames as Array<Record<string, any>>)[0].depth
    expect(d.valid_pixels).toBe(15)
    expect(d.invalid_ratio).toBeCloseTo(1 / 16, 6)
    expect(d.min).toBeCloseTo(0.1 + (1 / 65535) * 10, 6)
  })

  it('honours a manifest-declared noGeometryValue instead of assuming 0', () => {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 65535),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: {
          path: 'f0/depth.png',
          dtype: 'uint16',
          range: [0, 65535],
          near: 0.1,
          far: 2,
          noGeometryValue: 65535,
        },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    const d = (out.frames as Array<Record<string, any>>)[0].depth
    expect(d.valid_pixels).toBe(0)
    expect(d.invalid_ratio).toBe(1)
  })
})

describe('vision_pass: normal', () => {
  it('returns a camera-space mean and near-zero dispersion for a constant normal', () => {
    writePass(
      'f0/normal.png',
      rgb8Png(8, 8, (_x, _y, c) => (c === 2 ? 255 : 128)),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [8, 8],
      passes: {
        normal: { path: 'f0/normal.png', dtype: 'uint8', range: [0, 255], space: 'camera' },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    const n = (out.frames as Array<Record<string, any>>)[0].normal
    expect(n.space).toBe('camera')
    expect(n.valid_ratio).toBe(1)
    expect(n.angular_dispersion_deg).toBeCloseTo(0, 3)
    const [mx, , mz] = n.mean as [number, number, number]
    expect(mx).toBeCloseTo((128 / 255) * 2 - 1, 2)
    expect(mz).toBeGreaterThan(0.99)
    expect((out.aggregate as Record<string, any>).normal.space).toBe('camera')
  })

  it('reads 16-bit normals at full precision', () => {
    writePass(
      'f0/normal.png',
      encodePng({
        width: 4,
        height: 4,
        bitDepth: 16,
        colorType: 2,
        sample: (_x, _y, c) => (c === 2 ? 65535 : 32768),
      }),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [4, 4],
      passes: {
        normal: { path: 'f0/normal.png', dtype: 'uint16', range: [0, 65535], space: 'camera' },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    const n = (out.frames as Array<Record<string, any>>)[0].normal
    const [mx] = n.mean as [number, number, number]
    expect(mx).toBeCloseTo((32768 / 65535) * 2 - 1, 4)
  })
})

describe('vision_pass: object_index and alpha', () => {
  function objectScene(): string {
    // Left half = Cube (1), right half = Sphere (2); depth left 2 m, right 5 m.
    writePass(
      'f0/index.png',
      gray16Png(8, 8, (x) => (x < 4 ? 1 : 2)),
    )
    writePass(
      'f0/depth.png',
      gray16Png(8, 8, (x) => {
        const normalized = x < 4 ? (2 - 0.1) / 10 : (5 - 0.1) / 10
        return Math.round(normalized * 65535)
      }),
    )
    writePass(
      'f0/alpha.png',
      gray8Png(8, 8, (x, y) => (x < 2 && y < 4 ? 255 : 0)),
    )
    return manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [8, 8],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 10.1 },
        object_index: {
          path: 'f0/index.png',
          dtype: 'uint16',
          range: [0, 65535],
          objects: { 0: 'Background', 1: 'Cube', 2: 'Sphere' },
        },
        alpha: { path: 'f0/alpha.png', dtype: 'uint8', range: [0, 255] },
      },
    })
  }

  it('maps indices to names with visible pixels, coverage, bbox and depth order', () => {
    const out = runVisionPass({ manifests: [objectScene()] })
    expect(out.ok).toBe(true)
    const frame = (out.frames as Array<Record<string, any>>)[0]
    expect(frame.objects.object_count).toBe(2)
    const cube = frame.objects.rows.find((row: any) => row.name === 'Cube')
    const sphere = frame.objects.rows.find((row: any) => row.name === 'Sphere')
    expect(cube.pixels).toBe(32)
    expect(cube.coverage).toBeCloseTo(0.5, 6)
    expect(cube.bbox).toEqual([0, 0, 4, 8])
    expect(sphere.bbox).toEqual([4, 0, 8, 8])
    expect(cube.median_depth_m).toBeCloseTo(2, 1)
    expect(sphere.median_depth_m).toBeCloseTo(5, 1)
    // Aggregate ordering: nearest median depth first.
    const aggregate = out.aggregate as Record<string, any>
    expect(aggregate.objects[0].name).toBe('Cube')
    expect(aggregate.objects[0].depth_order).toBe(1)
    expect(aggregate.objects[1].depth_order).toBe(2)
    expect(aggregate.ordering.method).toBe('median_depth')
    expect(String(aggregate.ordering.note)).toContain('不是')
    expect(frame.objects.depth_available).toBe(true)
  })

  it('counts only visible pixels and treats index 0 as background', () => {
    writePass(
      'f0/index.png',
      gray16Png(4, 4, (x, y) => (x < 2 && y < 2 ? 7 : 0)),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [4, 4],
      passes: {
        object_index: {
          path: 'f0/index.png',
          dtype: 'uint16',
          range: [0, 65535],
          objects: { 7: 'Lamp' },
        },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    const frame = (out.frames as Array<Record<string, any>>)[0]
    const lamp = frame.objects.rows[0]
    expect(lamp.pixels).toBe(4)
    expect(lamp.coverage).toBeCloseTo(0.25, 6)
    expect(frame.objects.background_pixels).toBe(12)
    // No depth pass -> no ordering, only visible coverage.
    expect(frame.objects.depth_available).toBe(false)
    expect(frame.objects.rows[0].median_depth_m).toBeNull()
    expect(frame.warnings).toContain('depth_order_unavailable: 没有 depth pass，前后顺序为 null')
    expect((out.aggregate as Record<string, any>).objects[0].depth_order).toBeNull()
  })

  it('reports bbox-level overlap order without claiming occlusion', () => {
    // L-shaped Cube (bbox 0,0,4,4) and Sphere square (bbox 2,2,8,8): projections
    // overlap but no pixel belongs to both objects.
    writePass(
      'f0/index.png',
      gray16Png(8, 8, (x, y) =>
        x < 4 && y < 4 && !(x >= 2 && y >= 2) ? 1 : x >= 2 && y >= 2 ? 2 : 0,
      ),
    )
    writePass(
      'f0/depth.png',
      gray16Png(8, 8, (x, y) => Math.round(((x >= 2 && y >= 2 ? 3 : 1) / 10) * 65535)),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [8, 8],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 10.1 },
        object_index: {
          path: 'f0/index.png',
          dtype: 'uint16',
          range: [0, 65535],
          objects: { 1: 'Cube', 2: 'Sphere' },
        },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    const aggregate = out.aggregate as Record<string, any>
    expect(aggregate.bbox_overlap_order).toHaveLength(1)
    const pair = aggregate.bbox_overlap_order[0]
    expect(pair.front).toBe('Cube')
    expect(pair.basis).toBe('bbox_overlap_median_depth')
    expect(String(pair.note)).toContain('bbox')
  })

  it('reports alpha foreground coverage', () => {
    const out = runVisionPass({ manifests: [objectScene()] })
    const frame = (out.frames as Array<Record<string, any>>)[0]
    expect(frame.alpha.coverage).toBeCloseTo(8 / 64, 6)
    expect((out.aggregate as Record<string, any>).alpha.coverage.mean).toBeCloseTo(0.125, 6)
  })
})

describe('vision_pass: aggregation and bounds', () => {
  function twoFrames(): string[] {
    const frames: string[] = []
    for (const frame of [0, 1]) {
      writePass(
        'f' + String(frame) + '/depth.png',
        gray16Png(4, 4, () => (frame === 0 ? 10000 : 40000)),
      )
      writePass(
        'f' + String(frame) + '/index.png',
        gray16Png(4, 4, (x) => (x === 0 ? 1 : frame === 0 ? 2 : 0)),
      )
      frames.push(
        manifestFile('m' + String(frame), {
          manifestVersion: 1,
          frame,
          resolution: [4, 4],
          passes: {
            depth: {
              path: 'f' + String(frame) + '/depth.png',
              dtype: 'uint16',
              range: [0, 65535],
              near: 0,
              far: 10,
            },
            object_index: {
              path: 'f' + String(frame) + '/index.png',
              dtype: 'uint16',
              range: [0, 65535],
              objects: { 1: 'Cube', 2: 'Sphere' },
            },
          },
        }),
      )
    }
    return frames
  }

  it('aggregates depth ranges and object coverage stability across frames', () => {
    const out = runVisionPass({ manifests: twoFrames() })
    expect(out.ok).toBe(true)
    const aggregate = out.aggregate as Record<string, any>
    expect(aggregate.frame_count).toBe(2)
    const cube = aggregate.objects.find((row: any) => row.name === 'Cube')
    expect(cube.visible_frames).toBe(2)
    expect(cube.coverage_mean).toBeCloseTo(0.25, 6)
    expect(cube.coverage_std).toBeCloseTo(0, 6)
    const sphere = aggregate.objects.find((row: any) => row.name === 'Sphere')
    // Sphere only exists in frame 0; missing frames are warnings, never 0 coverage.
    expect(sphere.visible_frames).toBe(1)
    expect(
      (out.warnings as string[]).some((w) => w.startsWith('object_missing_in_frames: Sphere')),
    ).toBe(true)
    expect(aggregate.depth.frames).toBe(2)
    expect(aggregate.depth.mean.min).toBeLessThan(aggregate.depth.mean.max)
  })

  it('warns about duplicate frame numbers instead of silently merging', () => {
    const frames = twoFrames()
    const out = runVisionPass({ manifests: [frames[0], frames[0]] })
    expect(out.ok).toBe(true)
    expect((out.warnings as string[]).some((w) => w.startsWith('duplicate_frame: 0'))).toBe(true)
  })

  it('caps manifests at 240 with a warning', () => {
    const frames = twoFrames()
    const many = Array.from({ length: 241 }, () => frames[0])
    const out = runVisionPass({ manifests: many })
    expect(out.ok).toBe(true)
    expect((out.aggregate as Record<string, any>).manifest_count).toBe(VISION_PASS_LIMITS.manifests)
    expect((out.warnings as string[]).some((w) => w.startsWith('manifests_truncated:'))).toBe(true)
  })

  it('rejects more than 64 object filters', () => {
    const frames = twoFrames()
    const out = runVisionPass({
      manifests: [frames[0]],
      objects: Array.from({ length: 65 }, (_v, i) => 'obj' + String(i)),
    })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_input_invalid')
  })
})

describe('vision_pass: explicit failures', () => {
  function depthManifest(name = 'm0'): string {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 1),
    )
    return manifestFile(name, {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
  }

  it('returns vision_pass_unsupported for a requested pass missing from the manifest', () => {
    const manifest = depthManifest()
    const out = runVisionPass({ manifests: [manifest], passes: ['normal'] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_unsupported')
    expect(out.stage).toBe('input')
  })

  it('accepts a missing pass only when that frame declared it in manifest.degraded', () => {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 1),
    )
    writePass(
      'f0/normal.png',
      rgb8Png(2, 2, () => 128),
    )
    writePass(
      'f1/depth.png',
      gray16Png(2, 2, () => 1),
    )
    const first = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
        normal: { path: 'f0/normal.png', dtype: 'uint8', range: [0, 255], space: 'camera' },
      },
    })
    const second = manifestFile('m1', {
      manifestVersion: 1,
      frame: 1,
      resolution: [2, 2],
      degraded: ['normal'],
      passes: {
        depth: { path: 'f1/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [first, second] })
    expect(out.ok).toBe(true)
    const frames = out.frames as Array<Record<string, any>>
    expect(frames).toHaveLength(2)
    expect(frames[1].depth).toBeTruthy()
    expect(frames[1].normal).toBeUndefined()
    expect(frames[1].warnings.join(' ')).toContain('frame_degraded')
    expect((out.warnings as string[]).join(' ')).toContain('frame_degraded')
    expect((out.aggregate as Record<string, any>).frame_count).toBe(2)
  })

  it('still fails when a missing pass is not declared degraded', () => {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 1),
    )
    writePass(
      'f0/normal.png',
      rgb8Png(2, 2, () => 128),
    )
    writePass(
      'f1/depth.png',
      gray16Png(2, 2, () => 1),
    )
    const first = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
        normal: { path: 'f0/normal.png', dtype: 'uint8', range: [0, 255], space: 'camera' },
      },
    })
    const second = manifestFile('m1', {
      manifestVersion: 1,
      frame: 1,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f1/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [first, second] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
    expect(out.stage).toBe('input')
    expect(JSON.stringify(out.evidence)).toContain('normal')
  })

  it('keeps unsupported when an explicitly requested pass is absent from every manifest', () => {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 1),
    )
    const degradedOnly = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      degraded: ['normal'],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [degradedOnly], passes: ['normal'] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_unsupported')
  })

  it('rejects a manifest without depth near/far', () => {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 1),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: { depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535] } },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
    expect(out.stage).toBe('input')
  })

  it('rejects a normal manifest without space=camera', () => {
    writePass(
      'f0/normal.png',
      rgb8Png(2, 2, () => 128),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        normal: { path: 'f0/normal.png', dtype: 'uint8', range: [0, 255], space: 'world' },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
  })

  it('rejects an object_index pass without a name mapping', () => {
    writePass(
      'f0/index.png',
      gray16Png(2, 2, () => 1),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: { object_index: { path: 'f0/index.png', dtype: 'uint16', range: [0, 65535] } },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
  })

  it('rejects resolution mismatches between manifest and image', () => {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 1),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [4, 4],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
    expect(JSON.stringify(out.evidence ?? out)).toContain('resolution')
  })

  it('rejects a corrupt PNG instead of returning partial statistics', () => {
    const file = path.join(dir, 'f0', 'depth.png')
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, Buffer.from('broken'))
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
  })

  it('rejects a pass path that escapes the manifest directory', () => {
    const outside = path.join(dir, '..', 'escape-' + String(process.pid) + '.png')
    fs.writeFileSync(
      outside,
      gray16Png(2, 2, () => 1),
    )
    try {
      const manifest = manifestFile('m0', {
        manifestVersion: 1,
        frame: 0,
        resolution: [2, 2],
        passes: {
          depth: {
            path: '../' + path.basename(outside),
            dtype: 'uint16',
            range: [0, 65535],
            near: 0.1,
            far: 2,
          },
        },
      })
      const out = runVisionPass({ manifests: [manifest] })
      expect(out.ok).toBe(false)
      expect(out.code).toBe('vision_pass_manifest_invalid')
    } finally {
      fs.rmSync(outside, { force: true })
    }
  })

  it('rejects an unknown manifestVersion', () => {
    const manifest = manifestFile('m0', {
      manifestVersion: 2,
      frame: 0,
      resolution: [2, 2],
      passes: {},
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.code).toBe('vision_pass_manifest_invalid')
  })

  it('rejects a missing manifest file and an empty manifests list', () => {
    const missing = runVisionPass({ manifests: [path.join(dir, 'nope.json')] })
    expect(missing.code).toBe('vision_pass_manifest_invalid')
    const empty = runVisionPass({ manifests: [] })
    expect(empty.code).toBe('vision_input_invalid')
  })

  it('rejects a roi outside the resolution', () => {
    const manifest = depthManifest()
    const out = runVisionPass({ manifests: [manifest], roi: { x: 1, y: 1, w: 4, h: 1 } })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_input_invalid')
  })
})

describe('vision_pass: bounded receipts', () => {
  it('never returns raw pixel/depth/index arrays', () => {
    writePass(
      'f0/index.png',
      gray16Png(8, 8, (x) => (x < 4 ? 1 : 2)),
    )
    writePass(
      'f0/depth.png',
      gray16Png(8, 8, () => 10000),
    )
    writePass(
      'f0/normal.png',
      rgb8Png(8, 8, () => 128),
    )
    writePass(
      'f0/alpha.png',
      gray8Png(8, 8, () => 255),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [8, 8],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
        normal: { path: 'f0/normal.png', dtype: 'uint8', range: [0, 255], space: 'camera' },
        object_index: {
          path: 'f0/index.png',
          dtype: 'uint16',
          range: [0, 65535],
          objects: { 0: 'Background', 1: 'Cube', 2: 'Sphere' },
        },
        alpha: { path: 'f0/alpha.png', dtype: 'uint8', range: [0, 255] },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(true)
    expect(hasKey(out, 'data')).toBe(false)
    // Per-object "pixels" is a scalar count; raw arrays would show up as long arrays.
    expect(hasKey(out, 'values')).toBe(false)
    const arrays = findArrays(out)
    for (const array of arrays) expect(array.length).toBeLessThanOrEqual(8)
    // The receipt stays small even for four passes at 8x8.
    expect(JSON.stringify(out).length).toBeLessThan(12000)
  })
})

describe('vision_pass: manifest cross-checks', () => {
  it('rejects a declared dtype that differs from the PNG bit depth', () => {
    writePass(
      'f0/depth.png',
      gray8Png(2, 2, () => 1),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
    expect(JSON.stringify(out.evidence)).toContain('declaredBits')
    expect(JSON.stringify(out.evidence)).toContain('actualBits')
  })

  it('rejects an unknown pass encoding instead of guessing the formula', () => {
    writePass(
      'f0/normal.png',
      rgb8Png(2, 2, () => 128),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        normal: {
          path: 'f0/normal.png',
          dtype: 'uint8',
          range: [0, 255],
          space: 'camera',
          encoding: 'magic-normal-encoding',
        },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
    expect(JSON.stringify(out.evidence)).toContain('magic-normal-encoding')
  })

  it('rejects a value range inconsistent with the declared bit depth', () => {
    writePass(
      'f0/depth.png',
      gray8Png(2, 2, () => 1),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint8', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
  })
})

describe('vision_pass: sampling and unmapped indices', () => {
  const limits = VISION_PASS_LIMITS as unknown as { exactPixels: number }

  it('sizes the sampled depth array by rows, not by pixels/stride^2', () => {
    const previous = limits.exactPixels
    limits.exactPixels = 25 // 9x9 > 25 -> stride 2 -> 5x5 = 25 samples (old capacity: 21)
    try {
      writePass(
        'f0/depth.png',
        gray16Png(9, 9, () => 32768),
      )
      const manifest = manifestFile('m0', {
        manifestVersion: 1,
        frame: 0,
        resolution: [9, 9],
        passes: {
          depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
        },
      })
      const out = runVisionPass({ manifests: [manifest] })
      expect(out.ok).toBe(true)
      const d = (out.frames as Array<Record<string, any>>)[0].depth
      expect(d.valid_pixels).toBe(25)
      expect((out.roi as Record<string, any>).sampled).toBe(true)
      expect((out.aggregate as Record<string, any>).sampled).toBe(true)
    } finally {
      limits.exactPixels = previous
    }
  })

  it('marks sampled object counts and aggregates the marker', () => {
    const previous = limits.exactPixels
    limits.exactPixels = 25
    try {
      writePass(
        'f0/index.png',
        gray16Png(9, 9, () => 1),
      )
      const manifest = manifestFile('m0', {
        manifestVersion: 1,
        frame: 0,
        resolution: [9, 9],
        passes: {
          object_index: {
            path: 'f0/index.png',
            dtype: 'uint16',
            range: [0, 65535],
            objects: { 1: 'Cube' },
          },
        },
      })
      const out = runVisionPass({ manifests: [manifest] })
      const frame = (out.frames as Array<Record<string, any>>)[0]
      expect(frame.objects.sampled).toBe(true)
      expect(frame.objects.rows[0].sampled).toBe(true)
      expect(frame.objects.rows[0].pixels).toBe(25)
      const cube = (out.aggregate as Record<string, any>).objects[0]
      expect(cube.sampled).toBe(true)
      expect((out.aggregate as Record<string, any>).sampled).toBe(true)
    } finally {
      limits.exactPixels = previous
    }
  })

  it('counts unmapped non-zero indices and warns instead of ignoring them', () => {
    writePass(
      'f0/index.png',
      gray16Png(4, 4, (x, y) => (x === 0 && y === 0 ? 1 : x === 2 && y === 0 ? 2 : 0)),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [4, 4],
      passes: {
        object_index: {
          path: 'f0/index.png',
          dtype: 'uint16',
          range: [0, 65535],
          objects: { 1: 'Cube' },
        },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(true)
    const frame = (out.frames as Array<Record<string, any>>)[0]
    expect(frame.objects.unmapped_pixels).toBe(1)
    expect(frame.warnings.join(' ')).toContain('unmapped_object_index')
  })

  it('caps the object_index mapping at 4096 entries', () => {
    const mapping: Record<string, string> = {}
    for (let i = 1; i <= 4097; i += 1) mapping[String(i)] = 'obj' + String(i)
    writePass(
      'f0/index.png',
      gray16Png(2, 2, () => 1),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        object_index: {
          path: 'f0/index.png',
          dtype: 'uint16',
          range: [0, 65535],
          objects: mapping,
        },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
    expect(JSON.stringify(out.evidence)).toContain('4096')
  })
})

describe('vision_pass: budgets, yields and file guards', () => {
  it('stops before decoding a 64 MP frame that exceeds the pixel budget', () => {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 1),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [8000, 8000],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [manifest] }, { totalPixels: 1_000_000 })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_budget_exceeded')
    const evidence = out.evidence as Record<string, any>
    expect(evidence.totalPixels).toBe(1_000_000)
    expect(String(out.fix)).toContain('单片请求上限'.replace('单片', '单'))
  })

  it('yields to the event loop between frames via runVisionPassAsync', async () => {
    const files: string[] = []
    for (const frame of [0, 1]) {
      writePass(
        'f' + String(frame) + '/depth.png',
        gray16Png(2, 2, () => 1000),
      )
      files.push(
        manifestFile('m' + String(frame), {
          manifestVersion: 1,
          frame,
          resolution: [2, 2],
          passes: {
            depth: {
              path: 'f' + String(frame) + '/depth.png',
              dtype: 'uint16',
              range: [0, 65535],
              near: 0.1,
              far: 2,
            },
          },
        }),
      )
    }
    let yields = 0
    const out = await runVisionPassAsync(
      { manifests: files },
      {
        yieldBetweenFrames: async () => {
          yields += 1
        },
      },
    )
    expect(out.ok).toBe(true)
    expect(yields).toBe(1)
  })

  it('rejects an oversized manifest by bytes before parsing it', () => {
    const file = path.join(dir, 'huge.json')
    const padding = 'x'.repeat(VISION_PASS_LIMITS.manifestBytes + 16)
    fs.writeFileSync(file, JSON.stringify({ manifestVersion: 1, frame: 0, padding }))
    const out = runVisionPass({ manifests: [file] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
    expect((out.evidence as Record<string, any>).limit).toBe(VISION_PASS_LIMITS.manifestBytes)
  })

  it('rejects a manifest or pass path that is not a regular file', () => {
    fs.mkdirSync(path.join(dir, 'f0', 'depth.png'), { recursive: true })
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_pass_manifest_invalid')
    expect(String(out.error)).toContain('普通文件')
  })

  it('rejects a pass PNG over the byte cap', () => {
    const limits = VISION_PASS_LIMITS as unknown as { pngBytes: number }
    const previous = limits.pngBytes
    limits.pngBytes = 32
    try {
      writePass(
        'f0/depth.png',
        gray16Png(2, 2, () => 1),
      )
      const manifest = manifestFile('m0', {
        manifestVersion: 1,
        frame: 0,
        resolution: [2, 2],
        passes: {
          depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
        },
      })
      const out = runVisionPass({ manifests: [manifest] })
      expect(out.ok).toBe(false)
      expect((out.evidence as Record<string, any>).limit).toBe(32)
    } finally {
      limits.pngBytes = previous
    }
  })

  it('rejects an empty objects list instead of reporting zero objects', () => {
    writePass(
      'f0/depth.png',
      gray16Png(2, 2, () => 1),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [2, 2],
      passes: {
        depth: { path: 'f0/depth.png', dtype: 'uint16', range: [0, 65535], near: 0.1, far: 2 },
      },
    })
    const out = runVisionPass({ manifests: [manifest], objects: [] })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('vision_input_invalid')
  })
})

describe('vision_pass: alpha channel shapes', () => {
  it('reads RGBA (4-channel) alpha from channel 3', () => {
    writePass(
      'f0/alpha.png',
      encodePng({
        width: 4,
        height: 4,
        bitDepth: 8,
        colorType: 6,
        sample: (x, _y, c) => (c === 3 ? (x < 2 ? 255 : 0) : 10),
      }),
    )
    const manifest = manifestFile('m0', {
      manifestVersion: 1,
      frame: 0,
      resolution: [4, 4],
      passes: {
        alpha: { path: 'f0/alpha.png', dtype: 'uint8', range: [0, 255], encoding: 'alpha' },
      },
    })
    const out = runVisionPass({ manifests: [manifest] })
    expect(out.ok).toBe(true)
    const frame = (out.frames as Array<Record<string, any>>)[0]
    expect(frame.alpha.coverage).toBeCloseTo(0.5, 6)
  })
})

describe('vision_pass: render manifest schema', () => {
  it('consumes the schema render writes (range [0,1], root depth/normal/objectIndex)', () => {
    const frameDir = 'passes/frame_0000'
    writePass(
      frameDir + '/depth.png',
      gray16Png(4, 4, () => 32768),
    )
    writePass(
      frameDir + '/normal.png',
      rgb8Png(4, 4, (_x, _y, c) => (c === 2 ? 255 : 128)),
    )
    writePass(
      frameDir + '/object_index.png',
      gray16Png(4, 4, (x) => (x < 2 ? 1 : 2)),
    )
    const manifestPath = path.join(dir, frameDir, 'manifest.json')
    fs.writeFileSync(
      manifestPath,
      JSON.stringify({
        manifestVersion: 1,
        frame: 0,
        camera: 'Camera',
        resolution: [4, 4],
        samples: 64,
        engine: 'CYCLES',
        blenderVersion: '4.5.0',
        passes: {
          depth: {
            path: 'depth.png',
            bytes: 100,
            sha256: 'deadbeef',
            dtype: 'uint16',
            channels: 1,
            range: [0, 1],
            colorspace: 'linear',
            encoding: 'normalized_depth',
            surfaces: 'camera',
            decodeFormula: 'depth_m=near+(pixel/65535)*(far-near)',
          },
          normal: {
            path: 'normal.png',
            dtype: 'uint8',
            channels: 3,
            range: [0, 1],
            colorspace: 'linear',
            encoding: 'n*0.5+0.5',
            coordinateSpace: 'camera',
            source: 'native',
            decodeFormula: 'n=rgb/255*2-1',
          },
          object_index: {
            path: 'object_index.png',
            dtype: 'uint16',
            channels: 1,
            range: [0, 1],
            colorspace: 'non-color',
            encoding: 'index/65535',
          },
        },
        requestedPasses: ['depth', 'normal', 'object_index'],
        depth: {
          near: 0.5,
          far: 4.5,
          units: 'm',
          formula: 'depth_m=near+(pixel/65535)*(far-near)',
        },
        normal: { coordinateSpace: 'camera', encoding: 'n*0.5+0.5' },
        objectIndex: {
          background: 0,
          mapping: {
            1: { name: 'Cube', type: 'MESH' },
            2: { name: 'Sphere', type: 'MESH' },
          },
        },
        filmTransparent: false,
        degraded: [],
        generatedAt: '2026-11-12T00:00:00.000Z',
      }),
    )
    const out = runVisionPass({
      manifests: [manifestPath],
      passes: ['depth', 'normal', 'object_index'],
    })
    expect(out.ok).toBe(true)
    const frame = (out.frames as Array<Record<string, any>>)[0]
    // near/far live at the manifest root in render output.
    expect(frame.depth.near).toBe(0.5)
    expect(frame.depth.far).toBe(4.5)
    expect(frame.depth.p50).toBeCloseTo(0.5 + (32768 / 65535) * 4, 5)
    // normal pass uses coordinateSpace (not the legacy space field) and range [0,1].
    expect(frame.normal.space).toBe('camera')
    expect(frame.normal.valid_ratio).toBe(1)
    // object_index uses the root objectIndex.mapping; raw 16-bit values are indices.
    const rows = frame.objects.rows as Array<Record<string, any>>
    expect(rows.map((r) => r.name)).toEqual(['Cube', 'Sphere'])
    expect(rows.map((r) => r.index)).toEqual([1, 2])
    expect(rows[0].coverage).toBe(0.5)
    expect(rows[0].bbox).toEqual([0, 0, 2, 4])
    expect(rows[0].median_depth_m).toBeCloseTo(0.5 + (32768 / 65535) * 4, 5)
    expect(frame.objects.background_pixels).toBe(0)
    expect(frame.warnings.join(' ')).not.toContain('depth_order_unavailable')
  })
})
