import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import {
  PLAN_CATALOG,
  PLAN_READ_ONLY,
  PLAN_READ_ONLY_OPS,
  catalogFingerprint,
  planOpCrossChannelHint,
  planOpNames,
  planOpSuggestion,
} from '../../../../runtime/catalog.mjs'
import { catalogPayload } from '../../../../runtime/engine.mjs'
import { pkgPath } from '../../../helpers/pkg'

const baseline = JSON.parse(fs.readFileSync(pkgPath('tests', 'fixtures', 'plan_readonly.baseline.json'), 'utf8'))
const engineSource = [
  fs.readFileSync(pkgPath('runtime', 'core', 'dispatch.mjs'), 'utf8'),
  fs.readFileSync(pkgPath('runtime', 'core', 'plan-dispatch.mjs'), 'utf8'),
].join('\n')

function opsLine(family: string): string[] {
  const text = String(catalogPayload({ family }).text || '')
  const line = text.split('\n').find((x) => x.startsWith('ops: '))
  if (!line) throw new Error('no ops line for ' + family)
  return line.slice(5).split(' / ').map((x) => x.trim())
}

describe('plan op catalog contract', () => {
  it('keeps 34 families and 228 op entries with one documented duplicate', () => {
    expect(PLAN_CATALOG).toHaveLength(34)
    const names = planOpNames()
    expect(names).toHaveLength(228)
    expect(new Set(names).size).toBe(227)
    const dup = names.filter((n: any) => n === 'help')
    expect(dup).toHaveLength(2)
    expect(PLAN_CATALOG.filter((f: any) => f.prefix === '' && f.ops.includes('help')).map((f: any) => f.f)).toEqual(['contract', 'montage'])
  })

  it('maps every op to exactly one family and one canonical full name', () => {
    const expected = []
    for (const family of PLAN_CATALOG) {
      expect(family.ops.length, family.f).toBeGreaterThan(0)
      expect(new Set(family.ops).size, family.f).toBe(family.ops.length)
      for (const op of family.ops) expected.push(family.prefix ? family.prefix + op : op)
    }
    expect(expected.sort()).toEqual(planOpNames().sort())
  })

  it('exposes every family op in its catalog detail text', () => {
    for (const family of PLAN_CATALOG) {
      expect(opsLine(family.f), family.f).toEqual(family.ops)
    }
  })

  it('dispatches every family through engine routing', () => {
    const prefixlessMarker: Record<string, string> = {
      contract: 'dsh_contract_api',
      render_guard: 'dsh_render_guard',
      ext: 'gltf_validate',
      montage: 'montage',
    }
    for (const family of PLAN_CATALOG) {
      if (family.prefix) expect(engineSource.includes(family.prefix), family.f).toBe(true)
      else expect(engineSource.includes(prefixlessMarker[family.f]!), family.f).toBe(true)
    }
  })

  it('freezes the 98-name read-only whitelist and gates 137 write entries', () => {
    expect([...PLAN_READ_ONLY_OPS].sort()).toEqual([...baseline].sort())
    expect(PLAN_READ_ONLY_OPS).toHaveLength(98)
    const names = planOpNames()
    expect(names.filter((name: any) => PLAN_READ_ONLY.has(name))).toHaveLength(91)
    const writes = names.filter((name: any) => !PLAN_READ_ONLY.has(name))
    expect(writes).toHaveLength(137)
    expect(PLAN_READ_ONLY.has('catalog')).toBe(true)
    expect(PLAN_READ_ONLY.has('execute_code')).toBe(false)
  })

  it('suggests every valid op at distance zero', () => {
    for (const name of planOpNames()) {
      const s = planOpSuggestion(name)
      expect(s?.op, name).toBe(name)
      expect(s?.distance, name).toBe(0)
    }
  })

  it('routes bare op names to the plan channel and keeps command names in the command channel', () => {
    expect(planOpCrossChannelHint('get_scene_info')).toBeNull()
    expect(planOpCrossChannelHint('sculpt_scan')).toContain('blender_rt_plan')
    expect(planOpCrossChannelHint('nonexistent_xyz')).toBeNull()
  })

  it('reports one coherent fingerprint for the same catalog', () => {
    const fp = catalogFingerprint()
    expect(fp.families).toBe(34)
    expect(fp.opsCount).toBe(228)
    expect(fp.commandCatalogKeys).toBe(39)
    expect(fp.catalogHash).toMatch(/^[0-9a-f]{12}$/)
  })
})
