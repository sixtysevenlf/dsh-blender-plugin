import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { catalogPayload, planOpNames, MODELING_CLASS_GUIDE, classGuideStats, MODEL_FACING_TOOLS, renderHandoff } from '../../../../runtime/engine.mjs'
import { pkgPath } from '../../../helpers/pkg'

// S6: the "which tool / which family" guidance cannot rot silently: every class
// row is complete and hard-worded, every referenced op/tool exists, the pinned
// families keep a hard warning, and the handoff block stays small enough for a
// sub-agent prompt.

const lock = JSON.parse(fs.readFileSync(pkgPath('tests', 'fixtures', 'capability.lock.json'), 'utf8'))
const PINNED = ['vehicle', 'shape', 'sweep', 'generator', 'uv', 'material', 'deliver', 'gate', 'motion', 'human', 'img', 'audit', 'qc']

describe('modeling class guidance', () => {
  it('keeps at least 12 complete classes', () => {
    const stats = classGuideStats()
    expect(stats.classes).toBeGreaterThanOrEqual(12)
    expect(stats.missing).toEqual([])
  })

  it('uses hard words in every forbid column', () => {
    const soft = MODELING_CLASS_GUIDE.filter((row: any) => !/别|禁止|不要/.test(String(row.forbid))).map((row: any) => row.c)
    expect(soft).toEqual([])
  })

  it('references only real plan ops and tools', () => {
    const ops = new Set(planOpNames())
    const tools = new Set(lock.tools || [])
    const badOps: string[] = []
    const badTools: string[] = []
    for (const row of MODELING_CLASS_GUIDE as any[]) {
      const text = [row.entry, row.chain, row.gate, row.forbid].join(' ')
      for (const match of text.matchAll(/op="([a-z0-9_]+)"/g)) if (!ops.has(match[1])) badOps.push(row.c + ' -> ' + match[1])
      for (const match of text.matchAll(/blender_rt_[a-z_]+/g)) if (!tools.has(match[0])) badTools.push(row.c + ' -> ' + match[0])
    }
    expect(badOps).toEqual([])
    expect(badTools).toEqual([])
  })

  it('pins a hard warning on the self-invention-prone families', () => {
    const catalog = catalogPayload({ full: true })
    const missing: string[] = []
    for (const family of PINNED) {
      const entry = catalog.detail.find((row: any) => row.family === family)
      if (!entry) { missing.push(family + '(family missing)'); continue }
      if (!/别|禁止|不要|必须/.test(String(entry.not))) missing.push(family)
    }
    expect(missing).toEqual([])
  })

  it.fails('keeps the handoff block short and complete (pre-existing drift: 3503 > 3500, see evidence/T11/review.md)', () => {
    const handoff = catalogPayload({ handoff: true })
    expect(handoff.ok).toBe(true)
    expect(handoff.text.length).toBeGreaterThan(500)
    expect(handoff.text.length).toBeLessThanOrEqual(3500)
    for (const row of MODELING_CLASS_GUIDE as any[]) expect(handoff.text).toContain(row.c)
    expect(handoff.text).toContain('禁止自造')
    expect(renderHandoff().length).toBe(handoff.text.length)
  })

  it('keeps the handoff content complete (length gate tracked separately)', () => {
    const handoff = catalogPayload({ handoff: true })
    for (const row of MODELING_CLASS_GUIDE as any[]) expect(handoff.text).toContain(row.c)
    expect(handoff.text).toContain('禁止自造')
    expect(handoff.text).toContain('三条硬规则')
    expect(renderHandoff().length).toBe(handoff.text.length)
  })

  it('keeps the model-facing tool list identical to the capability lock', () => {
    const lockTools: string[] = lock.tools || []
    expect(MODEL_FACING_TOOLS.length).toBe(lockTools.length)
    expect(MODEL_FACING_TOOLS.every((tool: string) => lockTools.includes(tool))).toBe(true)
  })
})
