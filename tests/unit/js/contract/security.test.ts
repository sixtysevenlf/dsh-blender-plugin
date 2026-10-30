import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import {
  FILE_ALIAS_KEYS,
  describeMissingInput,
  detectFileAlias,
  headlessLogNames,
  noInputFileWarning,
  validateFileOpt,
} from '../../../../runtime/file_guard.mjs'
import { pkgPath } from '../../../helpers/pkg'

function readTree(dir: string, ext: string): string {
  return fs.readdirSync(dir, { withFileTypes: true }).map((entry) => {
    const full = path.join(dir, entry.name)
    return entry.isDirectory() ? readTree(full, ext) : entry.name.endsWith(ext) ? fs.readFileSync(full, 'utf8') : ''
  }).join('\n')
}
const addonSource = fs.readFileSync(pkgPath('addon', 'dsh_blender_bridge.py'), 'utf8')
const serverSource = readTree(pkgPath('runtime', 'http'), '.mjs')
const indexSource = readTree(pkgPath('src'), '.ts')

describe('file= parameter guard', () => {
  it('detects every documented alias spelling', () => {
    expect(FILE_ALIAS_KEYS).toEqual(['blend', 'blendFile', 'path', 'filepath', 'blend_file', 'input'])
    expect(detectFileAlias({ blend: 'x' })).toEqual(['blend'])
    expect(detectFileAlias({ input: null, filepath: undefined, path: 'a' })).toEqual(['path'])
    expect(detectFileAlias(null)).toEqual([])
  })

  it('normalizes one valid path and preserves single-element arrays', () => {
    expect(validateFileOpt('D:/x/y.blend')).toBe('D:/x/y.blend')
    expect(validateFileOpt(['D:/x/y.blend'])).toBe('D:/x/y.blend')
    expect(validateFileOpt(undefined)).toBeUndefined()
    expect(validateFileOpt(null)).toBeUndefined()
  })

  it('rejects empty, whitespace, object, and multi-element inputs by code', () => {
    for (const raw of ['', '   ']) {
      try { validateFileOpt(raw); throw new Error('expected rejection') } catch (e: any) { expect(e.code).toBe('file-empty') }
    }
    for (const raw of [{}, ['a', 'b'], 42]) {
      try { validateFileOpt(raw); throw new Error('expected rejection') } catch (e: any) { expect(e.code).toBe('file-type') }
    }
  })

  it('passes file= paths through without claiming symlink containment', () => {
    const link = 'D:/tmp/link-to-secret.blend'
    expect(validateFileOpt(link)).toBe(link)
  })

  it('explains missing input instead of returning a bare null', () => {
    expect(describeMissingInput('x.py', true)).toMatchObject({ given: true, opened: false })
    expect((describeMissingInput('   ', false) as any).reason).toContain('为空')
    expect(describeMissingInput(undefined, false)).toMatchObject({ given: false, opened: false })
    expect(noInputFileWarning(true).code).toBe('no_input_file')
    expect(noInputFileWarning(false).hint).toContain('未传 file=')
  })

  it('sanitizes run ids before they enter log file names', () => {
    const names = headlessLogNames('20260101_000000_000', '../a b')
    expect(names.stdout).toBe('dsh_headless_20260101_000000_000____a_b.stdout.log')
    expect(names.stdout.includes('/')).toBe(false)
    expect(names.stderr.includes('\\')).toBe(false)
  })
})

describe('archive and lease security boundaries', () => {
  it('checks both glTF and OBJ zip entries before extraction', () => {
    const guards = [...addonSource.matchAll(/abs_target_path\.startswith\(abs_temp_dir/g)]
    expect(guards.length).toBeGreaterThanOrEqual(2)
    expect(addonSource.match(/"\.\." in file_path/g)?.length).toBeGreaterThanOrEqual(2)
    expect(addonSource).toContain('Security issue: Zip contains files with path traversal attempt')
    expect(addonSource.indexOf('zip_ref.extractall(temp_dir)')).toBeGreaterThan(0)
  })

  it('keeps command execution out of a shell', () => {
    expect(addonSource).not.toContain('os.system(')
    expect(addonSource).not.toContain('subprocess')
    expect(addonSource).not.toContain('shell=True')
    expect(addonSource).toContain('handler(**params)')
  })

  it('keeps write routes behind the lease gate while read-only ops bypass it', () => {
    expect(serverSource).toContain('function leaseGate(payload)')
    expect(serverSource).toContain("error: 'leased'")
    expect(serverSource).toContain("const gate = leaseGate(payload);")
    expect(serverSource).toContain("const gate = isReadOnly('/loop', op) ? null : leaseGate(payload);")
    expect(serverSource).toContain("const gate = isReadOnly('/plan', op) ? null : leaseGate(payload);")
    expect(serverSource).toContain("'/plan': PLAN_READ_ONLY_OPS")
  })

  it('does not use a shell for backend process control', () => {
    expect(indexSource).toContain('spawn')
    expect(indexSource).not.toContain('shell: true')
    expect(indexSource).not.toContain('execSync(')
  })
})
