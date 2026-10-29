import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
// @ts-expect-error - runtime is plain .mjs, no declaration file
import { catalogPayload, planOpNames } from '../../../../runtime/engine.mjs'
import { pkgPath } from '../../../helpers/pkg'

const lock = JSON.parse(fs.readFileSync(pkgPath('tests', 'fixtures', 'capability.lock.json'), 'utf8'))

describe('catalog contract', () => {
  const payload = catalogPayload({ full: true })

  it('reports the frozen 34 family / 228 op surface', () => {
    expect(payload.ok).toBe(true)
    expect(payload.families).toHaveLength(lock.families_count)
    expect(payload.ops_count).toBe(lock.ops_count)
    expect(planOpNames()).toHaveLength(lock.ops_count)
    expect(payload.detail).toHaveLength(lock.families_count)
    expect(payload.detail.reduce((n: number, f: any) => n + f.ops.length, 0)).toBe(lock.ops_count)
  })

  it('keeps per-family op counts identical to the capability lock', () => {
    const actual = new Map(payload.detail.map((f: any) => [f.family, f.ops.length]))
    for (const f of lock.families) expect(actual.get(f.f), f.f).toBe(f.ops)
    expect([...actual.keys()].sort()).toEqual(lock.families.map((f: any) => f.f).sort())
  })

  it('exposes family and tool detail views', () => {
    const family = catalogPayload({ family: 'surface' })
    expect(family.ok).toBe(true)
    expect(family.text).toContain('fit')
    expect(family.text).toContain('M3')
    const tool = catalogPayload({ tool: 'rt_plan' })
    expect(tool.ok).toBe(true)
    expect(typeof tool.text).toBe('string')
    expect(catalogPayload({ family: '__nope__' }).ok).toBe(false)
  })

  it('rejects duplicate family members', () => {
    const names = payload.families
    expect(new Set(names).size).toBe(names.length)
    for (const f of payload.detail) expect(new Set(f.ops).size).toBe(f.ops.length)
  })
})
