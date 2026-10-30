import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { catalogPayload, planOpCrossChannelHint, planOpNames, planOpSuggestion } from '../../../../runtime/engine.js'
import { pkgPath } from '../../../helpers/pkg'

interface CatalogProvenance { catalogHash: string; note?: string; verdict?: string; diskVerdict: { ok?: boolean; catalogHash?: string } }
interface CatalogDetailRow { family: string; not?: string }
interface CatalogView {
  families: string[]
  ops_count: number
  text: string
  detail?: CatalogDetailRow[]
  tools?: unknown
  ok?: boolean
  provenance: CatalogProvenance
  [key: string]: unknown
}
const payload = (opts?: unknown): CatalogView => catalogPayload(opts) as unknown as CatalogView

// D1: tool schemas and the plan description stay inside a fixed budget, the
// catalog keeps its sizing contract, and the pure discoverability helpers keep
// correcting wrong channel choices instead of failing silently.

const TOOL_SOURCES = [
  ...fs.readdirSync(pkgPath('src', 'tools')).filter((f) => f.endsWith('.ts')).map((f) => path.join('src', 'tools', f)),
  path.join('src', 'plan', 'tool.ts'),
]

function declaredTools(): Array<{ name: string; source: string }> {
  const found: Array<{ name: string; source: string }> = []
  for (const rel of TOOL_SOURCES) {
    const text = fs.readFileSync(pkgPath(rel), 'utf8')
    for (const match of text.matchAll(/name: '(blender_rt_[a-z_]+|blender_viewport)'/g)) found.push({ name: match[1], source: rel })
  }
  return found
}

function schemaChars(name: string, sourceRel: string): number {
  const text = fs.readFileSync(pkgPath(sourceRel), 'utf8')
  const start = text.indexOf("name: '" + name + "'")
  const desc = text.indexOf('description:', start)
  const params = text.indexOf('parameters:', desc)
  const endMarkers = ['\n          output:', '\n          isConcurrencySafe:', '\n          timeoutMs:', '\n          async execute(', '\n          execute(']
    .map((marker) => text.indexOf(marker, params)).filter((at) => at > params)
  const end = endMarkers.length > 0 ? Math.min(...endMarkers) : text.indexOf('\n        },', params)
  const segment = end > desc ? text.slice(desc, end) : text.slice(desc)
  return (segment.match(/'((?:[^'\\]|\\.)*)'/g) || []).join('').length
}

describe('tool description budget', () => {
  const catalog = payload({})
  const declared = declaredTools()
  const names = [...new Set(declared.map((t) => t.name))]

  it('registers exactly 15 distinct tools and no pick tool', () => {
    expect(declared).toHaveLength(16) // viewport is declared twice: real tool + fallback placeholder
    expect(names).toHaveLength(15)
    expect(names).not.toContain('blender_rt_pick')
  })

  it('keeps the total schema budget under 12000 characters', () => {
    const rows = names.map((name) => [name, schemaChars(name, declared.find((t) => t.name === name)!.source)] as const)
    const total = rows.reduce((sum, row) => sum + row[1], 0)
    expect(total).toBeLessThanOrEqual(12000)
    const worst = rows.slice().sort((a, b) => b[1] - a[1])[0]
    expect(worst[1]).toBeLessThan(1600)
    expect((rows.find((row) => row[0] === 'blender_rt_headless') || [0, 1e9])[1]).toBeLessThan(1600)
  })

  it('keeps the plan description under 2000 characters and points at catalog', () => {
    const source = fs.readFileSync(pkgPath('src', 'plan', 'tool.ts'), 'utf8')
    const start = source.indexOf("name: 'blender_rt_plan'")
    const params = source.indexOf('parameters:', start)
    const block = source.slice(start, params)
    const chars = (block.match(/'((?:[^'\\]|\\.)*)'/g) || []).join('').length
    expect(chars).toBeLessThan(2000)
    expect(block).toContain('catalog')
    expect(block).toContain(String(catalog.families.length) + ' 个 family')
    expect(block).toContain(String(catalog.ops_count) + ' 个 op')
  })

  it('keeps the catalog text scale per family', () => {
    const text = payload({})
    expect(text.families.length).toBeGreaterThanOrEqual(26)
    expect(text.ops_count).toBeGreaterThanOrEqual(100)
    expect(text.text.length).toBeGreaterThanOrEqual(3000)
    expect(text.text.length / text.families.length).toBeLessThanOrEqual(360)
    expect(text.detail).toBeUndefined()
    expect(text.tools).toBeUndefined()
    expect(payload({ family: 'sculpt' }).text.length).toBeLessThan(1500)
    expect(payload({ full: true }).detail).toHaveLength(text.families.length)
    expect(payload({ family: '__nope__' }).ok).toBe(false)
    expect(payload({ tool: 'rt_headless' }).text).toContain('as_job')
  })

  it('suggests the nearest plan op and corrects cross-channel calls', () => {
    expect(planOpSuggestion('audti_mesh')?.op).toBe('audit_mesh')
    expect(planOpSuggestion('sculpt_appli')?.op).toBe('sculpt_apply')
    expect(planOpSuggestion('zzzzzzzzzz')).toBeNull()
    expect(planOpCrossChannelHint('audit_mesh')).toContain('blender_rt_plan(op="audit_mesh"')
    expect(planOpCrossChannelHint('get_scene_info')).toBeNull()
    expect(planOpSuggestion('sweep_analyz')?.op).toBe('sweep_analyze')
    expect(planOpNames()).toHaveLength(catalog.ops_count)
  })

  it('keeps the catalog self-verifying provenance', () => {
    const cat = payload({})
    expect(cat.provenance.catalogHash).toMatch(/^[0-9a-f]{12}$/)
    expect(cat.provenance.note).toContain('mtime')
    expect(cat.provenance.verdict).toBe('current')
    const verified = payload({ verify: true })
    expect(verified.provenance.diskVerdict.ok).toBe(true)
    expect(verified.provenance.diskVerdict.catalogHash).toBe(cat.provenance.catalogHash)
  })
})
