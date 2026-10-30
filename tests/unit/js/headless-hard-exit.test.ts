import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import { hardExitJobParts, hardExitSyncTail } from '../../../runtime/hard_exit.mjs'
import { pkgPath } from '../../helpers/pkg'

// F-14: Blender can AV on exit *after* the result is already emitted. The
// default is a hard exit after flushing stdio; explicit false keeps the legacy
// byte-identical behaviour, and the ordering (done < flush < _exit) is the
// contract that prevents lost output.

const DEF = ['import sys as _dsh_sys', '_dsh_sys.stdout.flush(); _dsh_sys.stderr.flush()', 'import os as _dsh_os; _dsh_os._exit(0)']
const NL = String.fromCharCode(10)

describe('headless hard exit', () => {
  it('is on by default and matches the frozen snippet', () => {
    expect(hardExitJobParts()).toEqual(DEF)
    expect(hardExitJobParts({})).toEqual(DEF)
    expect(hardExitJobParts({ hardExit: true })).toEqual(DEF)
    expect(hardExitJobParts({ hardExit: undefined })).toEqual(DEF)
  })

  it('is a no-op when explicitly disabled', () => {
    expect(hardExitJobParts({ hardExit: false })).toEqual([])
    expect(hardExitSyncTail({ hardExit: false }, NL)).toBe('')
  })

  it('keeps the done < flush < _exit ordering', () => {
    const jobScript = ['_dsh_stage_emit("done")'].concat(hardExitJobParts({})).join(NL)
    expect(jobScript.indexOf('_dsh_stage_emit("done")')).toBeLessThan(jobScript.indexOf('_dsh_sys.stdout.flush(); _dsh_sys.stderr.flush()'))
    expect(jobScript.indexOf('_dsh_sys.stdout.flush(); _dsh_sys.stderr.flush()')).toBeLessThan(jobScript.indexOf('_dsh_os._exit(0)'))
    const syncBody = 'USER_SCRIPT' + NL + '_dsh_stage_emit("done")' + NL + hardExitSyncTail({}, NL)
    expect(syncBody.indexOf('_dsh_stage_emit("done")')).toBeLessThan(syncBody.indexOf('_dsh_sys.stdout.flush()'))
    expect(syncBody.indexOf('_dsh_sys.stdout.flush()')).toBeLessThan(syncBody.indexOf('_dsh_os._exit(0)'))
    expect(hardExitSyncTail({}, NL).startsWith(NL)).toBe(true)
    expect(hardExitSyncTail({}, NL).endsWith(NL)).toBe(false)
    expect('OLD' + hardExitSyncTail({ hardExit: false }, NL)).toBe('OLD')
  })

  it('is wired into both execution paths and the tool schema', () => {
    const jobs = fs.readFileSync(pkgPath('runtime', 'execution', 'jobs.mjs'), 'utf8')
    const headless = fs.readFileSync(pkgPath('runtime', 'execution', 'headless.mjs'), 'utf8')
    const tool = fs.readFileSync(pkgPath('src', 'tools', 'headless.ts'), 'utf8')
    expect(jobs).toContain('from "../hard_exit.mjs"')
    expect(jobs).toContain('hardExitJobParts(opts)')
    expect(headless).toContain('from "../hard_exit.mjs"')
    expect(headless).toContain('body += hardExitSyncTail(opts, NL)')
    expect(jobs.indexOf('hardExitJobParts(opts)')).toBeLessThan(jobs.lastIndexOf('writeHeadlessScript'))
    expect(tool).toContain('hard_exit: {')
    expect(tool).toContain("type: 'boolean'")
    expect(tool).toContain('默认 true')
    expect(tool).toContain("hardExit: args && typeof args.hard_exit === 'boolean' ? args.hard_exit : undefined,")
  })
})
