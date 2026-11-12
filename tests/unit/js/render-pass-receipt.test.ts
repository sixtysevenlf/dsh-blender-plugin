import { describe, expect, it } from 'vitest'
import { formatCollect } from '../../../src/tools/run/presenter.js'

function job(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'job-1',
    kind: 'render',
    state: 'completed',
    slot: 'gpu',
    priority: 0,
    createdAt: 1,
    updatedAt: 2,
    startedAt: 1,
    finishedAt: 2,
    pid: null,
    decision: null,
    result: { exitCode: 0, signal: null, ms: 900 },
    artifacts: [],
    logs: { stdout: '', stderr: '' },
    error: null,
    events: [],
    ...extra,
  }
}

const PASS_SUMMARY = {
  ok: true,
  manifestVersion: 1,
  passes: ['beauty', 'depth'],
  frames: [
    {
      frame: 1,
      manifest: '/home/cache/render/abc/passes/frame_0001/manifest.json',
      passes: {
        beauty: { path: '/home/cache/render/abc/passes/frame_0001/beauty_0001.png', bytes: 2048 },
        depth: { path: '/home/cache/render/abc/passes/frame_0001/depth_0001.png', bytes: 3072 },
      },
      bytes: 5120,
      degraded: [],
    },
    {
      frame: 2,
      manifest: '/home/cache/render/abc/passes/frame_0002/manifest.json',
      passes: {
        beauty: { path: '/x/beauty_0002.png', bytes: 1024 },
      },
      bytes: 1024,
      degraded: ['depth'],
    },
  ],
  degradedFrames: [2],
  outdir: '/home/cache/render/abc',
}

describe('render pass receipts (5.5)', () => {
  it('lists manifest paths and pass metadata without pixels', () => {
    const text = formatCollect(
      job({
        result: { exitCode: 0, signal: null, ms: 900, parsed: PASS_SUMMARY },
        artifacts: ['/x/beauty_0001.png', '/x/depth_0001.png'],
      }),
    )
    expect(text).toContain('passes  2 帧 · 请求 beauty/depth')
    expect(text).toContain('manifest /home/cache/render/abc/passes/frame_0001/manifest.json')
    expect(text).toContain('beauty 2.0 KiB')
    expect(text).toContain('depth 3.0 KiB')
    expect(text).toContain('degraded · 缺 depth')
    expect(text).toContain('warning  degraded frames: 2')
    // Metadata only: no raw pixels, base64 image payloads, or JSON blob dump.
    expect(text).not.toContain('base64')
    expect(text).not.toContain('data:image')
    expect(text).not.toContain('sha256')
    expect(text).not.toContain('"frames"')
  })

  it('keeps the headless parsed summary line for non-pass jobs', () => {
    const text = formatCollect(
      job({
        kind: 'headless',
        result: { exitCode: 0, signal: null, ms: 5, parsed: { ok: true, summary: 'x' } },
      }),
    )
    expect(text).toContain('parsed=')
    expect(text).not.toContain('passes  ')
  })

  it('reports cache hits and skipped/oversized misses', () => {
    const hit = formatCollect(
      job({
        result: {
          exitCode: 0,
          signal: null,
          ms: 1,
          cache: { hit: true, keys: ['abc'], entries: 1 },
        },
      }),
    )
    expect(hit).toContain('cache   hit · 1 条 entry · key=abc')
    const skipped = formatCollect(
      job({
        result: { exitCode: 0, signal: null, ms: 1, cache: { hit: false, skipped: 'oversize' } },
      }),
    )
    expect(skipped).toContain('cache   miss · skipped=oversize')
    const corrupt = formatCollect(
      job({ result: { exitCode: 0, signal: null, ms: 1, cache: { hit: false, corrupt: true } } }),
    )
    expect(corrupt).toContain('corrupt')
  })
})
