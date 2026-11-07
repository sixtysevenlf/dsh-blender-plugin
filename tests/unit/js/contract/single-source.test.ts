import { describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import { catalogPayload, PLAN_READ_ONLY_OPS } from '../../../../runtime/engine.js'
import { pkgPath } from '../../../helpers/pkg'

interface CatalogView {
  families: string[]
  ops_count: number
}
const payload = (opts?: unknown): CatalogView => catalogPayload(opts) as unknown as CatalogView

// the read-only plan whitelist is derived from the engine, the frozen
// baseline must match it name by name, and the hand-written family/op counts in
// the model-facing description must equal what the catalog computes.

describe('single source of truth', () => {
  const baseline: string[] = JSON.parse(
    fs.readFileSync(pkgPath('tests', 'fixtures', 'plan_readonly.baseline.json'), 'utf8'),
  )
  const derived = [...PLAN_READ_ONLY_OPS]

  it('derives the read-only whitelist from the baseline', () => {
    expect(derived.length).toBeGreaterThanOrEqual(40)
    expect(new Set(derived).size).toBe(derived.length)
    expect([...derived].sort()).toEqual([...baseline].sort())
  })

  it('server consumes the derived constant instead of a hand copy', () => {
    const lease = fs.readFileSync(pkgPath('runtime', 'http', 'lease.ts'), 'utf8')
    expect(lease).toContain("'/plan': PLAN_READ_ONLY_OPS")
  })

  it('keeps the plan tool description counts equal to the catalog', () => {
    const catalog = payload({})
    const source = fs.readFileSync(pkgPath('src', 'plan', 'tool.ts'), 'utf8')
    const match = source.match(/【判定 \/ 验收 \/ 导出 \/ 造型】(\d+) 个 family · (\d+) 个 op/)
    expect(match).not.toBeNull()
    expect(Number(match![1])).toBe(catalog.families.length)
    expect(Number(match![2])).toBe(catalog.ops_count)
  })

  it('keeps the repository AGENTS.md counts equal to the catalog', () => {
    // this used to resolve pkgPath('..', 'AGENTS.md') (the parent directory,
    // where no AGENTS.md exists) and silently return. Assert existence and match
    // the real "34 families / 228 ops" wording used in the repo guide.
    const agents = pkgPath('AGENTS.md')
    expect(fs.existsSync(agents)).toBe(true)
    const text = fs.readFileSync(agents, 'utf8')
    const match = text.match(/(\d+) families \/ (\d+) ops/)
    const catalog = payload({})
    expect(match).not.toBeNull()
    expect(Number(match![1])).toBe(catalog.families.length)
    expect(Number(match![2])).toBe(catalog.ops_count)
    // The remaining contract counts in the guide must stay advertised as well.
    expect(text).toMatch(/25 addon commands\s*\/\s*29 routes\s*\/\s*protocol 12/)
  })

  it('keeps the count sync script runnable against the built runtime', () => {
    const out = execFileSync(process.execPath, [pkgPath('scripts', 'sync_counts.mjs'), '--dry'], {
      cwd: pkgPath(),
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    expect(out).toContain('需改 0 个文件')
  })
})
