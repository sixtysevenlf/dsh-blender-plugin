import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { writeFallbackLogs } from '../../../runtime/execution/headless/logs.js'

let tmp = ''
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-headless-logs-'))
})
afterEach(() => {
  try {
    fs.rmSync(tmp, { recursive: true, force: true })
  } catch {
    /* best effort */
  }
})

describe('headless fallback logs', () => {
  it('writes stdout and stderr when live streams were unavailable', () => {
    const dir = path.join(tmp, 'logs')
    const logs = writeFallbackLogs(dir, 'stamp', 'run-1', 'out text', 'err text')
    expect(logs).not.toBeNull()
    expect(logs?.stdout).toContain('stdout.log')
    expect(logs?.stderr).toContain('stderr.log')
    expect(fs.readFileSync(String(logs?.stdoutWsl), 'utf8')).toBe('out text')
    expect(fs.readFileSync(String(logs?.stderrWsl), 'utf8')).toBe('err text')
  })

  it('returns null when the log directory cannot be created', () => {
    const blocked = path.join(tmp, 'blocked')
    fs.writeFileSync(blocked, 'file')
    expect(writeFallbackLogs(blocked, 'stamp', 'run-2', 'out', 'err')).toBeNull()
  })
})
