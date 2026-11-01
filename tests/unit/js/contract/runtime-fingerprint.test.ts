import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { pkgPath } from '../../../helpers/pkg'
import { RUNTIME_DIR, runtimeFingerprint, sha12 } from '../../../../runtime/core/evidence.js'
import { pyModuleFile, pyModuleNames } from '../../../../runtime/paths.js'

// F2.3 follow-up: runtimeFingerprint() must enumerate the Python module registry
// (runtime/py/modules.json) instead of flat runtime/<name>.py shims. The keys stay
// stable (<name>.py) so receipts remain comparable with pre-F2.3 builds.
const manifest = JSON.parse(fs.readFileSync(pkgPath('runtime', 'py', 'modules.json'), 'utf8')) as {
  modules: Record<string, string>
}

function nonPythonRuntimeFiles(): string[] {
  return fs
    .readdirSync(RUNTIME_DIR)
    .filter((f) => /\.(js|ts)$/.test(f) && !/\.d\.ts$/.test(f))
    .sort()
}

describe('runtime fingerprint (F2.3 registry-only Python sources)', () => {
  it('keys every modules.json module plus non-Python runtime files', () => {
    const fp = runtimeFingerprint()
    expect(
      Object.keys(fp)
        .filter((k) => k.endsWith('.py'))
        .sort(),
    ).toEqual(
      Object.keys(manifest.modules)
        .map((n) => n + '.py')
        .sort(),
    )
    expect(
      Object.keys(fp)
        .filter((k) => !k.endsWith('.py'))
        .sort(),
    ).toEqual(nonPythonRuntimeFiles())
    expect(pyModuleNames().length).toBe(Object.keys(manifest.modules).length)
  })

  it('hashes generator / uv_tools from their geometry implementation', () => {
    const fp = runtimeFingerprint()
    for (const name of ['generator', 'uv_tools']) {
      expect(manifest.modules[name]).toBe('geometry')
      const source = pyModuleFile(name).replace(/\\/g, '/')
      expect(source).toMatch(/\/runtime\/py\/geometry\/[a-z_]+\.py$/)
      expect(fp[name + '.py']).toBe(sha12(fs.readFileSync(pyModuleFile(name))))
    }
  })

  it('does not depend on flat runtime/*.py shims', () => {
    const flat = fs
      .readdirSync(pkgPath('runtime'))
      .filter((f) => f.endsWith('.py'))
      .sort()
    expect(flat).toEqual([])
    const fp = runtimeFingerprint()
    for (const name of Object.keys(manifest.modules)) {
      expect(fp[name + '.py'], name).toBeTruthy()
    }
    const source = fs.readFileSync(pkgPath('runtime', 'core', 'evidence.ts'), 'utf8')
    expect(source).not.toContain('readdirSync(PY_ROOT)')
    expect(source).not.toContain('PY_ROOT')
  })
})
