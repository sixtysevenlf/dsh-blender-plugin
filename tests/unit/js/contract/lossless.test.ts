import { describe, expect, it } from 'vitest'
// @ts-expect-error - built plugin entry has no declaration consumed here
import * as plugin from '../../../../lib/index.js'
// @ts-expect-error - runtime package is JS-only from this package's point of view
import { snapshotJsonValue } from '@deepseek-ai/dsh-session'

const { losslessSanitize, receiptEnvelope, headlessReceipt, promotedReceipt } = plugin.__internals

function hasUndefined(value: unknown, seen = new Set<unknown>()): boolean {
  if (value === undefined) return true
  if (value === null || typeof value !== 'object') return false
  if (seen.has(value)) return false
  seen.add(value)
  if (Array.isArray(value)) return value.some((v) => hasUndefined(v, seen))
  return Object.values(value as Record<string, unknown>).some((v) => hasUndefined(v, seen))
}

describe('lossless return channel', () => {
  it('removes or normalizes every non-lossless JSON value', () => {
    const circular: Record<string, unknown> = { a: 1 }
    circular.self = circular
    const { value, fixes } = losslessSanitize({
      undef: undefined,
      nan: NaN,
      inf: Infinity,
      list: [1, undefined, 2],
      big: 1n,
      fn: () => 1,
      circular,
    })
    expect(fixes.length).toBeGreaterThan(0)
    expect(hasUndefined(value)).toBe(false)
    expect(() => snapshotJsonValue(value)).not.toThrow()
    expect(JSON.stringify(value)).not.toContain('undefined')
  })

  it('keeps every receipt free of undefined keys', () => {
    const raw = { ok: true, status: 'done', resultJson: { a: undefined, b: 1 }, ms: undefined }
    const envelope = receiptEnvelope(raw, { mode: 'sync', notes: [] })
    const clean = losslessSanitize(envelope).value
    expect(hasUndefined(clean)).toBe(false)
    expect(() => snapshotJsonValue(clean)).not.toThrow()
    expect(clean.resultJson.b).toBe(1)
    expect(clean.resultJson.a).toBeUndefined()
  })

  it('builds headless receipts with text and a lossless envelope', () => {
    const out = headlessReceipt({ status: 'done', result: { verts: 8 } }, { mode: 'sync' })
    expect(out.text).toContain('HEADLESS')
    expect(out.envelope.status).toBe('done')
    const clean = losslessSanitize(out.envelope).value
    expect(() => snapshotJsonValue(clean)).not.toThrow()
  })

  it('marks promoted receipts explicitly', () => {
    const out = promotedReceipt({ id: 'job-1', status: 'running' }, 'job-1', 'waited too long')
    expect(out.envelope.promoted).toBe(true)
    expect(out.envelope.jobId).toBe('job-1')
    const clean = losslessSanitize(out.envelope).value
    expect(() => snapshotJsonValue(clean)).not.toThrow()
  })
})
