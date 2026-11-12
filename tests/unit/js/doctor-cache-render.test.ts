import { describe, expect, it } from 'vitest'
import { renderCacheDoctorView } from '../../../runtime/core/engine-api/doctor.js'
import {
  RENDER_CACHE_DEFAULTS,
  RENDER_CACHE_LIMIT_RANGES,
} from '../../../runtime/core/queue/render-cache-key.js'

describe('doctor cache.render view', () => {
  it('reports field, raw value, range and the default actually used for every invalid value', () => {
    const view = renderCacheDoctorView({ maxEntries: 0, maxBytes: 1024, ttlDays: 99 })
    expect(view.limits).toEqual(RENDER_CACHE_DEFAULTS)
    expect(view.issues).toHaveLength(3)
    for (const issue of view.issues) {
      expect(issue.reason).toBe('out-of-range')
      expect(issue.allowed).toContain(
        String(RENDER_CACHE_LIMIT_RANGES[issue.field as 'maxEntries'].min),
      )
      expect(issue.value).not.toBeUndefined()
      expect(issue.used).toBe(view.limits[issue.field as 'maxEntries'])
    }
  })

  it('keeps valid values and reports no issues', () => {
    const view = renderCacheDoctorView({ maxEntries: 4, maxBytes: 1024 ** 3, ttlDays: 2 })
    expect(view).toEqual({ limits: { maxEntries: 4, maxBytes: 1024 ** 3, ttlDays: 2 }, issues: [] })
  })

  it('does not throw on a missing or non-object config', () => {
    for (const raw of [undefined, null, 'nope', 3, []]) {
      const view = renderCacheDoctorView(raw)
      expect(view.limits).toEqual(RENDER_CACHE_DEFAULTS)
      expect(Array.isArray(view.issues)).toBe(true)
    }
  })
})
