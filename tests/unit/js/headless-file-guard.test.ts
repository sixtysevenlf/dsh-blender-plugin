import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import {
  FILE_ALIAS_KEYS, detectFileAlias, headlessLogNames, noInputFileWarning, validateFileOpt,
} from '../../../runtime/file_guard.mjs'
import { describeMissingInput as describeMissingRaw } from '../../../runtime/file_guard.mjs'

// runtime/file_guard.d.mts types describeMissingInput as string; the frozen
// receipt shape is {given, opened, reason}, so keep the runtime truth local.
const describeMissingInput = describeMissingRaw as unknown as (a: unknown, b?: boolean) => { given: boolean, opened: boolean, reason: string }
import { headlessScriptName } from '../../../runtime/staging.mjs'
import { pkgPath } from '../../helpers/pkg'

// F-09: a missing/empty/misnamed file= used to look like a successful headless
// run (ok=true, no warning, empty scene). The tool-side guard must name the
// mistake and the receipt must stay distinguishable.

const throwsWith = (fn: () => unknown): string => {
  try { fn(); return '<no-throw>' } catch (error: any) { return error && error.code ? error.code : 'plain:' + String(error && error.message) }
}

describe('headless file option guard', () => {
  it('rejects empty and non-string file values', () => {
    expect(throwsWith(() => validateFileOpt(''))).toBe('file-empty')
    expect(throwsWith(() => validateFileOpt('   '))).toBe('file-empty')
    expect(throwsWith(() => validateFileOpt(123))).toBe('file-type')
    expect(throwsWith(() => validateFileOpt({ path: 'x.blend' }))).toBe('file-type')
    expect(throwsWith(() => validateFileOpt(['a.blend', 'b.blend']))).toBe('file-type')
    try { validateFileOpt('') } catch (error: any) { expect(String(error.hint)).toContain('file') }
  })

  it('keeps legacy conveniences and passes paths through untouched', () => {
    expect(validateFileOpt(['x.blend'])).toBe('x.blend')
    expect(validateFileOpt(null)).toBeUndefined()
    expect(validateFileOpt(undefined)).toBeUndefined()
    expect(validateFileOpt(' D:\\x\\y.blend ')).toBe(' D:\\x\\y.blend ')
    expect(validateFileOpt('D:\\档 案\\y.blend')).toBe('D:\\档 案\\y.blend')
  })

  it('names aliased file parameters at the tool boundary', () => {
    expect(detectFileAlias(null)).toEqual([])
    expect(detectFileAlias({ script: 'x', file: 'a.blend' })).toEqual([])
    expect(detectFileAlias({ blend: 'a.blend' })).toEqual(['blend'])
    expect(detectFileAlias({ path: 'a', filepath: 'b' })).toHaveLength(2)
    for (const key of ['blend', 'blendFile', 'path', 'filepath', 'blend_file', 'input']) expect(FILE_ALIAS_KEYS).toContain(key)
    expect(detectFileAlias({ file: 'a.blend', blend: undefined })).toEqual([])
    expect(detectFileAlias({ blend: null, script: 'x' })).toEqual([])
  })

  it('describes missing input and warns explicitly', () => {
    const none = describeMissingInput(undefined, false)
    expect(none).toMatchObject({ given: false, opened: false })
    expect(typeof none.reason).toBe('string')
    const py = describeMissingInput('x.py', true)
    expect(py).toMatchObject({ given: true, opened: false })
    expect(py.reason).toContain('.py')
    expect(none.given).not.toBe(py.given)
    expect(describeMissingInput('', false).reason).toContain('为空')
    expect(noInputFileWarning(false).code).toBe('no_input_file')
    expect(noInputFileWarning(false).hint).toContain('file')
    expect(noInputFileWarning(true).hint).toContain('.py')
  })

  it('generates collision-free staging and log names', () => {
    const names = new Set<string>()
    for (let i = 0; i < 5000; i += 1) names.add(headlessScriptName())
    expect(names.size).toBe(5000)
    expect(headlessScriptName()).toMatch(/^dsh_headless_[0-9a-z]+_[0-9a-z]+\.py$/)
    const first = headlessLogNames('2026-10-24T00-00-00-000Z', 'run-aaaa1111')
    const second = headlessLogNames('2026-10-24T00-00-00-000Z', 'run-bbbb2222')
    expect(new Set([first.stdout, first.stderr, second.stdout, second.stderr]).size).toBe(4)
    expect(first.stdout).toContain('2026-10-24T00-00-00-000Z')
    expect(first.stdout).toContain('run-aaaa1111')
    expect(first.stdout.endsWith('.stdout.log')).toBe(true)
    expect(headlessLogNames('s', 'run-../../evil').stdout).not.toContain('../')
  })

  it('is wired into both execution paths and the tools', () => {
    const jobs = fs.readFileSync(pkgPath('runtime', 'execution', 'jobs.mjs'), 'utf8')
    const headless = fs.readFileSync(pkgPath('runtime', 'execution', 'headless.mjs'), 'utf8')
    const headlessTool = fs.readFileSync(pkgPath('src', 'tools', 'headless.ts'), 'utf8')
    const jobTool = fs.readFileSync(pkgPath('src', 'tools', 'job.ts'), 'utf8')
    expect(jobs).toContain('from "../file_guard.mjs"')
    expect(jobs).toContain('validateFileOpt(opts.file)')
    expect(jobs).toContain('describeMissingInput')
    expect(jobs).toContain('noInputFileWarning')
    expect(headless).toContain('validateFileOpt(opts.file)')
    expect(headless).toContain('describeMissingInput')
    expect(headless).toContain('noInputFileWarning')
    expect(headless).toContain('headlessLogNames(stamp, runId)')
    expect(jobs).not.toContain('headlessLogNames(')
    expect(headlessTool).toContain('detectFileAlias(args)')
    expect(headlessTool).toContain('file= 传了空字符串')
    expect(jobTool).toContain('detectFileAlias(args)')
  })
})
