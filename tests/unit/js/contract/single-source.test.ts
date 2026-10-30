import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { catalogPayload, PLAN_READ_ONLY_OPS } from '../../../../runtime/engine.mjs'
import { pkgPath } from '../../../helpers/pkg'

// S4: the read-only plan whitelist is derived from the engine, the frozen
// baseline must match it name by name, and the hand-written family/op counts in
// the model-facing description must equal what the catalog computes.

describe('single source of truth', () => {
  const baseline: string[] = JSON.parse(fs.readFileSync(pkgPath('tests', 'fixtures', 'plan_readonly.baseline.json'), 'utf8'))
  const derived = [...PLAN_READ_ONLY_OPS]

  it('derives the read-only whitelist from the baseline', () => {
    expect(derived.length).toBeGreaterThanOrEqual(40)
    expect(new Set(derived).size).toBe(derived.length)
    expect([...derived].sort()).toEqual([...baseline].sort())
  })

  it('server consumes the derived constant instead of a hand copy', () => {
    const lease = fs.readFileSync(pkgPath('runtime', 'http', 'lease.mjs'), 'utf8')
    expect(lease).toContain("'/plan': PLAN_READ_ONLY_OPS")
  })

  it('keeps the plan tool description counts equal to the catalog', () => {
    const catalog = catalogPayload({})
    const source = fs.readFileSync(pkgPath('src', 'plan', 'tool.ts'), 'utf8')
    const match = source.match(/【判定 \/ 验收 \/ 导出 \/ 造型】(\d+) 个 family · (\d+) 个 op/)
    expect(match).not.toBeNull()
    expect(Number(match![1])).toBe(catalog.families.length)
    expect(Number(match![2])).toBe(catalog.ops_count)
  })

  it('keeps the workspace AGENTS.md counts equal to the catalog when present', () => {
    const agents = pkgPath('..', 'AGENTS.md')
    if (!fs.existsSync(agents)) return
    const text = fs.readFileSync(agents, 'utf8')
    const match = text.match(/(\d+) 个 family \/ (\d+) 个 op/)
    const catalog = catalogPayload({})
    expect(match).not.toBeNull()
    expect(Number(match![1])).toBe(catalog.families.length)
    expect(Number(match![2])).toBe(catalog.ops_count)
  })
})
