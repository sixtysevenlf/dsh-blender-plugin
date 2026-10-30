import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { pyModuleFile, pyModuleNames, PY_DOMAINS } from '../../../../runtime/paths.js'
import { pkgPath } from '../../../helpers/pkg'

const DOMAINS: Record<string, string[]> = {
  core: ['kit', 'contract', 'gate', 'txn', 'runner', 'worker', 'planner', 'presets', 'pipeline', 'perf',
    'evalharness', 'motion', 'sweep', 'audit', 'offline_bootstrap'],
  geometry: ['geo', 'mesh_fix', 'sculpt', 'shapegen', 'shapesolve', 'faceeval', 'blockout', 'surface'],
  assembly: ['assembly', 'clearance', 'vehicle', 'human'],
  render: ['view', 'qc', 'qc_render', 'render_guard', 'material', 'visualreport', 'imgtools', 'calib', 'montage'],
  export: ['deliver', 'printcheck', 'open3d_check'],
}
const ALL = Object.values(DOMAINS).flat()
const SHIMS = ALL.filter((n) => n !== 'open3d_check')
const UNLISTED = ['generator', 'uv_tools']

describe('runtime domain package layout (T8)', () => {
  it('keeps the five plan domain packages and their exact module sets', () => {
    expect(PY_DOMAINS).toEqual(['core', 'geometry', 'assembly', 'render', 'export'])
    for (const [dom, names] of Object.entries(DOMAINS)) {
      const dir = pkgPath('runtime', 'py', dom)
      expect(fs.existsSync(pkgPath('runtime', 'py', dom, '__init__.py'))).toBe(true)
      const present = fs.readdirSync(dir).filter((f) => f.endsWith('.py') && f !== '__init__.py')
        .map((f) => f.slice(0, -3)).sort()
      expect(present).toEqual([...names].sort())
    }
    expect(fs.existsSync(pkgPath('runtime', 'py', 'assembly', 'assembly_features.json'))).toBe(true)
    expect(fs.existsSync(pkgPath('runtime', 'ext', 'open3d_check.py'))).toBe(false)
  })

  it('maps every module name to a real file and keeps the two unlisted modules flat', () => {
    expect(pyModuleNames().length).toBe(41)
    expect(pyModuleNames().sort()).toEqual([...ALL, ...UNLISTED].sort())
    for (const n of [...ALL, ...UNLISTED]) expect(fs.existsSync(pyModuleFile(n))).toBe(true)
    expect(pyModuleFile('generator')).toBe(pkgPath('runtime', 'generator.py'))
    expect(pyModuleFile('uv_tools')).toBe(pkgPath('runtime', 'uv_tools.py'))
    expect(pyModuleFile('kit.py')).toBe(pkgPath('runtime', 'py', 'core', 'kit.py'))
  })

  it('leaves old flat paths as one-version re-export shims only', () => {
    for (const n of SHIMS) {
      const src = fs.readFileSync(pkgPath('runtime', n + '.py'), 'utf8')
      const dom = Object.keys(DOMAINS).find((d) => DOMAINS[d].includes(n))
      expect(src).toContain('from runtime.py.' + dom + '.' + n + ' import *')
      expect(src).toContain('from py.' + dom + '.' + n + ' import *')
      expect(src.split('\n').length).toBeLessThan(40)
      expect(src).not.toMatch(/\n(def|class)\s/)
      expect(src).not.toMatch(/\n[A-Za-z_][A-Za-z0-9_]*\s*=/)
    }
    for (const n of UNLISTED) {
      const src = fs.readFileSync(pkgPath('runtime', n + '.py'), 'utf8')
      expect(src.split('\n').length).toBeGreaterThan(60)
    }
  })

  it('feeds the split engine from the real package files, not the shims', () => {
    const kernel = fs.readFileSync(pkgPath('runtime', 'core', 'kernel.ts'), 'utf8')
    expect(kernel).toContain("fs.readFileSync(pyModuleFile(name), 'utf8')")
    const planDispatch = fs.readFileSync(pkgPath('runtime', 'core', 'plan-dispatch.ts'), 'utf8')
    expect(planDispatch).toContain("path.join(RUNTIME_DIR, 'py', 'export', 'open3d_check.py')")
    const evidence = fs.readFileSync(pkgPath('runtime', 'core', 'evidence.ts'), 'utf8')
    expect(evidence).toContain('pyModuleFile(f)')
    const engine = fs.readFileSync(pkgPath('runtime', 'engine.ts'), 'utf8')
    expect(engine).not.toMatch(/path\.join\(HERE, String\(name\)\.replace/)
  })
})
