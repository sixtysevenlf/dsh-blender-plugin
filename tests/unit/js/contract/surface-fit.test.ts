import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { catalogPayload } from '../../../../runtime/engine.mjs'
import { pkgPath } from '../../../helpers/pkg'

const surfacePy = fs.readFileSync(pkgPath('runtime', 'surface.py'), 'utf8')
const lock = JSON.parse(fs.readFileSync(pkgPath('tests', 'fixtures', 'capability.lock.json'), 'utf8'))

describe('surface fit honesty contract', () => {
  it('declares fit in the catalog and capability lock', () => {
    const family = catalogPayload({ full: true }).detail.find((f: any) => f.family === 'surface')
    expect(family.ops).toContain('fit')
    const locked = lock.families.find((f: any) => f.f === 'surface')
    expect(locked.ops).toBe(family.ops.length)
  })

  it('registers a real fit handler rather than dropping the op', () => {
    expect(surfacePy).toContain('def surface_fit(')
    expect(surfacePy).toMatch(/"fit":\s*surface_fit/)
    expect(surfacePy).toContain('M3_NOT_IMPLEMENTED')
  })

  it('documents fit as an honest M3 limitation', () => {
    expect(surfacePy).toContain('"fit": {')
    expect(surfacePy).toContain('M3 fit 未实现')
  })
})
