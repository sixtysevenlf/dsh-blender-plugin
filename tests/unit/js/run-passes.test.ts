import { describe, expect, it } from 'vitest'
import {
  RENDER_PASSES,
  normalizeRenderPasses,
  submitPayload,
} from '../../../src/tools/run/execute.js'

function renderPayload(extra: Record<string, unknown> = {}): Record<string, unknown> {
  const built = submitPayload({ action: 'submit', kind: 'render', ...extra })
  expect(typeof built).toBe('object')
  return built as Record<string, unknown>
}

describe('render passes: host-side validation (5.1)', () => {
  it('keeps the old render payload shape when passes is absent', () => {
    const built = renderPayload({ frames: [1, 2], camera: 'Camera' })
    expect(built).toEqual({ op: 'submit', kind: 'render', frames: [1, 2], camera: 'Camera' })
    expect(Object.keys(built)).not.toContain('passes')
  })

  it('treats null and undefined as absent instead of an empty pass set', () => {
    expect(renderPayload({ passes: undefined })).not.toHaveProperty('passes')
    expect(renderPayload({ passes: null })).not.toHaveProperty('passes')
    expect(normalizeRenderPasses(undefined)).toEqual([])
    expect(normalizeRenderPasses(null)).toEqual([])
  })

  it('accepts every allowed pass and preserves the requested order', () => {
    const built = renderPayload({ passes: ['depth', 'beauty', 'object_index'] })
    expect(built.passes).toEqual(['depth', 'beauty', 'object_index'])
    expect(RENDER_PASSES).toEqual(['beauty', 'depth', 'normal', 'object_index', 'alpha'])
  })

  it('accepts a comma-separated string as one declaration', () => {
    expect(normalizeRenderPasses('beauty, alpha')).toEqual(['beauty', 'alpha'])
    expect(normalizeRenderPasses(' depth ')).toEqual(['depth'])
  })

  it('rejects an empty pass set and lists the allowed values', () => {
    const empty = normalizeRenderPasses([])
    expect(typeof empty).toBe('string')
    expect(String(empty)).toContain('不能为空')
    for (const name of RENDER_PASSES) expect(String(empty)).toContain(name)
    const blank = normalizeRenderPasses('   ')
    expect(typeof blank).toBe('string')
    expect(String(blank)).toContain('不能为空')
  })

  it('rejects an unknown pass with the allowed list', () => {
    const got = submitPayload({
      action: 'submit',
      kind: 'render',
      passes: ['depth', 'cryptomatte'],
    })
    expect(typeof got).toBe('string')
    expect(String(got)).toContain('cryptomatte')
    expect(String(got)).toContain('beauty/depth/normal/object_index/alpha')
  })

  it('rejects duplicate passes instead of silently deduplicating', () => {
    for (const value of [['depth', 'depth'], 'beauty,beauty']) {
      const got = submitPayload({ action: 'submit', kind: 'render', passes: value })
      expect(typeof got).toBe('string')
      expect(String(got)).toContain('重复')
    }
  })

  it('rejects a non-string or non-list declaration', () => {
    const numeric = submitPayload({ action: 'submit', kind: 'render', passes: 42 })
    expect(typeof numeric).toBe('string')
    expect(String(numeric)).toContain('passes')
    const mixed = submitPayload({ action: 'submit', kind: 'render', passes: ['beauty', 7] })
    expect(typeof mixed).toBe('string')
    expect(String(mixed)).toContain('字符串')
  })

  it('refuses passes on kind=headless instead of dropping them', () => {
    const got = submitPayload({
      action: 'submit',
      kind: 'headless',
      script: 'print(1)',
      passes: ['depth'],
    })
    expect(typeof got).toBe('string')
    expect(String(got)).toContain('passes')
    expect(String(got)).toContain('render')
  })

  it('still refuses script parameters when passes is requested', () => {
    const got = submitPayload({
      action: 'submit',
      kind: 'render',
      passes: ['depth'],
      script: 'print(1)',
    })
    expect(typeof got).toBe('string')
    expect(String(got)).toContain('render 不接受 script/script_file')
  })
})
