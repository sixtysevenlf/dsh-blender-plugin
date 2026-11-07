import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { serverPidsFromProcRows } from '../../../runtime/ports.js'
import {
  parseNetstatListeners,
  parseTasklistImage,
  winKillPids,
  winSamePortHolders,
} from '../../../runtime/win_proc.js'

const ROOT = process.cwd()

function read(rel: string): string {
  return readFileSync(path.join(ROOT, rel), 'utf8').replace(/\r\n/g, '\n')
}

describe('win_proc parsers', () => {
  const netstat = [
    '活动连接',
    '',
    '  协议  本地地址          外部地址        状态           PID',
    '  TCP    0.0.0.0:9877           0.0.0.0:0              LISTENING       1234',
    '  TCP    127.0.0.1:9877         127.0.0.1:50013        ESTABLISHED     1234',
    '  TCP    [::]:9877              [::]:0                 LISTENING       5678',
    '  TCP    0.0.0.0:17860          0.0.0.0:0              LISTENING       9999',
    '  TCP    0.0.0.0:98770          0.0.0.0:0              LISTENING       7777',
    '  TCP    0.0.0.0:19877          0.0.0.0:0              LISTENING       8888',
  ].join('\r\n')

  it('only accepts LISTENING rows', () => {
    expect(parseNetstatListeners(netstat, 9877)).toEqual([1234, 5678])
  })

  it('matches the port exactly', () => {
    expect(parseNetstatListeners(netstat, 98770)).toEqual([7777])
    expect(parseNetstatListeners(netstat, 19877)).toEqual([8888])
    expect(parseNetstatListeners(netstat, 17860)).toEqual([9999])
  })

  it('parses IPv6 listeners and dedupes PIDs', () => {
    expect(
      parseNetstatListeners(
        '  TCP    [::]:9877              [::]:0                 LISTENING       5678',
        9877,
      ),
    ).toEqual([5678])
    expect(
      parseNetstatListeners(
        '  TCP    0.0.0.0:9877  0.0.0.0:0  LISTENING  42\n  TCP  10.0.0.1:9877  10.0.0.2:1  LISTENING  42',
        9877,
      ),
    ).toEqual([42])
  })

  it('rejects garbage input and invalid ports', () => {
    expect(parseNetstatListeners('', 9877)).toEqual([])
    expect(parseNetstatListeners('活动连接\r\n', 9877)).toEqual([])
    expect(parseNetstatListeners(null, 9877)).toEqual([])
    expect(parseNetstatListeners(netstat, 0)).toEqual([])
    expect(parseNetstatListeners(netstat, Number.NaN)).toEqual([])
    expect(parseNetstatListeners(netstat, -1)).toEqual([])
  })

  it('parses tasklist CSV image names', () => {
    expect(parseTasklistImage('"node.exe","1234","Console","1","35,216 K"')).toBe('node.exe')
    expect(parseTasklistImage('"DeepSeek Harness.exe","4321","Console","1","120,000 K"')).toBe(
      'DeepSeek Harness.exe',
    )
    expect(
      parseTasklistImage('INFO: No tasks are running which match the specified criteria.'),
    ).toBe('')
    expect(parseTasklistImage('')).toBe('')
    expect(parseTasklistImage(null)).toBe('')
  })

  it('returns safe arrays on every platform, including Windows (was vacuous)', () => {
    // regression: the old body was `if (IS_WIN) return`, so on Windows the
    // only coverage for these probes was skipped. Assert the observable
    // contract on all platforms; invalid/empty input must fail closed to [].
    expect(winKillPids([])).toEqual([])
    const holders = winSamePortHolders(0)
    expect(Array.isArray(holders)).toBe(true)
    expect(holders.every((pid) => Number.isInteger(pid) && pid > 0)).toBe(true)
  })
})

describe('issue #51 static invariants', () => {
  // Behavior for spawnChild/killBackendByCmd lives in src-backend-spawn.test.ts and
  // backend-win-cleanup.test.ts. What remains here are artifacts that can
  // only be checked as source: the generated catalog probe and cross-platform guards.
  const catalog = read('runtime/catalog/logic.ts')
  const winProc = read('runtime/win_proc.ts')

  it('catalog probe runs in node mode when the host is Electron', () => {
    expect(catalog).toContain('ELECTRON_RUN_AS_NODE')
  })

  it('win_proc guards every probe/kill entry for non-Windows', () => {
    expect((winProc.match(/if \(!IS_WIN\) return/g) ?? []).length).toBeGreaterThanOrEqual(5)
  })
})

describe('EADDRINUSE proc matcher', () => {
  const rows = [
    { pid: 101, toks: ['node', '/opt/dsh/lib/runtime/gateway.js', '--port', '9877'] },
    { pid: 102, toks: ['node', '/old/runtime/gateway.mjs', '--port', '9877'] },
    { pid: 103, toks: ['node', '/opt/dsh/lib/runtime/gateway.js', '--port', '98770'] },
    { pid: 104, toks: ['bash', '-c', 'node /opt/dsh/lib/runtime/gateway.js --port 9877'] },
    { pid: 105, toks: ['node', '/opt/dsh/lib/runtime/gateway.js', '--port', '9877'] },
  ]

  it('matches exact server argv and port, not shell wrappers', () => {
    expect(serverPidsFromProcRows(rows, 9877, 999).sort()).toEqual([101, 102, 105])
  })

  it('never matches a sibling port or itself', () => {
    expect(serverPidsFromProcRows(rows, 98770, 999)).toEqual([103])
    expect(serverPidsFromProcRows(rows, 9877, 101)).toEqual([102, 105])
  })
})
