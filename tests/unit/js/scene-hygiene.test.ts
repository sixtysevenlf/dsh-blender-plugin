import { describe, expect, it } from 'vitest'
import { KERNEL_BOOTSTRAP, sceneHygieneAudit } from '../../../runtime/engine.js'

// Feedback #3: 36 lights overflow the EEVEE shadow pool and the old behaviour
// only spammed stderr. The receipt now carries a structured warning with a
// paste-ready fix; a clean run must stay silent (no false positives).

const NL = String.fromCharCode(10)

describe('scene hygiene audit', () => {
  it('reports one warning with used/limit for a single overflow line', () => {
    const warnings = sceneHygieneAudit('', 'Error: Shadow buffer full, may result in missing shadows and lower performance. (3040 / 2048)' + NL)
    expect(warnings).toHaveLength(1)
    expect(warnings[0]).toMatchObject({ code: 'eevee_shadow_pool_full', used_mb: 3040, limit_mb: 2048 })
    expect(warnings[0].occurrences).toBe(1)
  })

  it('counts repeated overflows and keeps the last numeric pair', () => {
    const warnings = sceneHygieneAudit('', ['x', 'Error: Shadow buffer full (10 / 2048)', 'Error: Shadow buffer full (3000 / 2048)'].join(NL))
    expect(warnings).toHaveLength(1)
    expect(warnings[0].occurrences).toBe(2)
    expect(warnings[0].used_mb).toBe(3000)
  })

  it('never reports on a clean run', () => {
    expect(sceneHygieneAudit('building geometry' + NL + 'Saved: ok.png' + NL, '')).toEqual([])
  })

  it('names 2048 and use_shadow in the fix', () => {
    const fix = sceneHygieneAudit('', 'Error: Shadow buffer full (1 / 2048)')[0].fix
    expect(fix).toContain('2048')
    expect(fix).toContain('use_shadow')
  })

  it('keeps the light-hygiene and route-collections kernel helpers', () => {
    expect(String(KERNEL_BOOTSTRAP)).toContain('K.light_hygiene')
    expect(String(KERNEL_BOOTSTRAP)).toContain('K.route_collections')
  })
})
