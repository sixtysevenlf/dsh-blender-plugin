import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { pyModuleFile, pyModuleNames, PY_DOMAINS } from '../../../../runtime/paths.js'
import { pkgPath } from '../../../helpers/pkg'

// 唯一映射源 runtime/py/modules.json；这里不再维护第二份硬编码清单。
const manifest = JSON.parse(fs.readFileSync(pkgPath('runtime', 'py', 'modules.json'), 'utf8')) as {
  version: number
  domains: string[]
  modules: Record<string, string>
}
const DOMAINS: Record<string, string[]> = Object.fromEntries(
  manifest.domains.map((d) => [
    d,
    Object.keys(manifest.modules).filter((n) => manifest.modules[n] === d),
  ]),
)
const ALL = Object.values(DOMAINS).flat()

describe('runtime domain package layout', () => {
  it('keeps the plan domain packages and their exact module sets', () => {
    expect(PY_DOMAINS).toEqual(['core', 'geometry', 'assembly', 'render', 'export', 'animation'])
    for (const [dom, names] of Object.entries(DOMAINS)) {
      const dir = pkgPath('runtime', 'py', dom)
      expect(fs.existsSync(pkgPath('runtime', 'py', dom, '__init__.py'))).toBe(true)
      const present = fs
        .readdirSync(dir)
        .filter((f) => f.endsWith('.py') && f !== '__init__.py')
        .map((f) => f.slice(0, -3))
        .sort()
      expect(present).toEqual([...names].sort())
    }
    expect(fs.existsSync(pkgPath('runtime', 'py', 'assembly', 'assembly_features.json'))).toBe(true)
    expect(fs.existsSync(pkgPath('runtime', 'ext', 'open3d_check.py'))).toBe(false)
  })

  it('maps every module name to a real file inside its domain package', () => {
    expect(pyModuleNames().length).toBe(42)
    expect(pyModuleNames().sort()).toEqual(ALL.sort())
    for (const n of ALL) expect(fs.existsSync(pyModuleFile(n))).toBe(true)
    expect(pyModuleFile('generator')).toBe(pkgPath('runtime', 'py', 'geometry', 'generator.py'))
    expect(pyModuleFile('uv_tools')).toBe(pkgPath('runtime', 'py', 'geometry', 'uv_tools.py'))
    expect(pyModuleFile('kit.py')).toBe(pkgPath('runtime', 'py', 'core', 'kit.py'))
  })

  it('has no flat runtime modules left', () => {
    const flat = fs
      .readdirSync(pkgPath('runtime'))
      .filter((f) => f.endsWith('.py'))
      .sort()
    expect(flat).toEqual([])
    expect(fs.readFileSync(pkgPath('runtime', 'paths.ts'), 'utf8')).not.toContain("d === '.'")
  })

  it('feeds the split engine from the real package files, not the shims', () => {
    const kernel = fs.readFileSync(pkgPath('runtime', 'core', 'kernel', 'api.ts'), 'utf8')
    expect(kernel).toContain("fs.readFileSync(pyModuleFile(name), 'utf8')")
    const extCheck = fs.readFileSync(pkgPath('runtime', 'core', 'plan', 'ext-check.ts'), 'utf8')
    expect(extCheck).toContain('resolveExtPaths()')
    expect(extCheck).toContain("path.join(PY_ROOT, 'py', 'export', 'open3d_check.py')")
    const evidence = fs.readFileSync(pkgPath('runtime', 'core', 'evidence.ts'), 'utf8')
    expect(evidence).toContain('pyModuleNames()')
    expect(evidence).toContain("pyModuleFile(name), name + '.py'")
    expect(evidence).not.toContain('readdirSync(PY_ROOT)')
    const engine = fs.readFileSync(pkgPath('runtime', 'engine.ts'), 'utf8')
    expect(engine).not.toMatch(/path\.join\(HERE, String\(name\)\.replace/)
  })

  it('reads the Python module registry from modules.json only', () => {
    expect(manifest.version).toBeGreaterThanOrEqual(1)
    expect(manifest.domains).toEqual([
      'core',
      'geometry',
      'assembly',
      'render',
      'export',
      'animation',
    ])
    expect(PY_DOMAINS).toEqual(manifest.domains)
    expect(pyModuleNames().sort()).toEqual(Object.keys(manifest.modules).sort())
    for (const [n, dom] of Object.entries(manifest.modules)) {
      expect(manifest.domains.includes(dom)).toBe(true)
      expect(fs.existsSync(pyModuleFile(n))).toBe(true)
    }
    const pathsSrc = fs.readFileSync(pkgPath('runtime', 'paths.ts'), 'utf8')
    expect(pathsSrc).toContain("'py', 'modules.json'")
    expect(pathsSrc).not.toMatch(/PY_MODULE_DOMAIN: Record<string, string> = \{/)
  })
})
