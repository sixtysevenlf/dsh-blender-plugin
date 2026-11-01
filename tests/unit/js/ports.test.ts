import fs from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// F4.1 / F0.3: runtime/ports.ts is the EADDRINUSE holder cleanup used by the
// HTTP lifecycle. These tests inject fake /proc rows and force the win32 probe
// module, so the parsers, the kill signal and the Windows fallback are proven
// without touching the real process table (no taskkill/netstat side effects).

const h = vi.hoisted(() => ({
  winKillPids: vi.fn((pids: number[]) => pids.map(String)),
  winSamePortHolders: vi.fn((_port: number) => [] as number[]),
}))
vi.mock('../../../runtime/win_proc.js', () => ({
  winKillPids: h.winKillPids,
  winSamePortHolders: h.winSamePortHolders,
}))

import {
  holderResponsive,
  killHolders,
  parsePortArg,
  readProcRows,
  samePortHolders,
  serverPidsFromProcRows,
} from '../../../runtime/ports.js'

type ProcMap = Record<string, string[]>

function stubProc(rows: ProcMap): void {
  vi.spyOn(fs, 'readdirSync').mockImplementation(((p: unknown) => {
    if (String(p) !== '/proc') return []
    return Object.keys(rows)
  }) as never)
  vi.spyOn(fs, 'readFileSync').mockImplementation(((p: unknown) => {
    const m = /^\/proc\/([0-9]+)\/cmdline$/.exec(String(p))
    if (!m || !rows[m[1]]) throw new Error('ENOENT')
    return rows[m[1]].join(String.fromCharCode(0)) + String.fromCharCode(0)
  }) as never)
}

function stubNoProc(): void {
  vi.spyOn(fs, 'readdirSync').mockImplementation(((p: unknown) => {
    expect(String(p)).toBe('/proc')
    throw new Error('ENOENT')
  }) as never)
}

beforeEach(() => {
  h.winKillPids.mockImplementation((pids: number[]) => pids.map(String))
  h.winSamePortHolders.mockImplementation(() => [])
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('parsePortArg legacy semantics', () => {
  it('returns null only when the flag is absent', () => {
    expect(parsePortArg([])).toBeNull()
    expect(parsePortArg(['--other', '9877'])).toBeNull()
  })

  it('passes the next argv token through Number(), like the pre-F1.3 code', () => {
    expect(parsePortArg(['--port', '9877'])).toBe(9877)
    expect(parsePortArg(['--port', '0'])).toBe(0)
    expect(parsePortArg(['--port', '-1'])).toBe(-1)
    expect(parsePortArg(['--port', '12.5'])).toBe(12.5)
    expect(parsePortArg(['--port', 'abc'])).toBeNaN()
    expect(parsePortArg(['--port'])).toBeNaN()
  })

  it('supports a custom flag token and ignores substrings', () => {
    expect(parsePortArg(['--http-port', '9000'], '--http-port')).toBe(9000)
    expect(parsePortArg(['--x--port', '9000'])).toBeNull()
  })
})

describe('readProcRows over a fake /proc (F0.3)', () => {
  it('reads numeric pid cmdlines and skips its own pid / bad entries', () => {
    stubProc({
      '101': ['node', '/opt/dsh/lib/runtime/server.js', '--port', '9877'],
      abc: ['node', 'noise'],
      [String(process.pid)]: ['node', '/opt/dsh/lib/runtime/server.js', '--port', '9877'],
    })
    expect(readProcRows()).toEqual([
      { pid: 101, toks: ['node', '/opt/dsh/lib/runtime/server.js', '--port', '9877'] },
    ])
  })

  it('returns [] when /proc does not exist (macOS/Windows)', () => {
    stubNoProc()
    expect(readProcRows()).toEqual([])
  })
})

describe('serverPidsFromProcRows exact matching', () => {
  it('matches only a real server argv with the exact port', () => {
    const rows = [
      { pid: 101, toks: ['node', '/opt/dsh/lib/runtime/server.js', '--port', '9877'] },
      { pid: 102, toks: ['node', '/old/runtime/server.mjs', '--port', '9877'] },
      { pid: 103, toks: ['node', '/opt/dsh/lib/runtime/server.js', '--port', '98770'] },
      { pid: 104, toks: ['bash', '-c', 'node /opt/dsh/lib/runtime/server.js --port 9877'] },
      { pid: 105, toks: ['node', '/opt/dsh/lib/runtime/server.js', '--port', '9877'] },
    ]
    expect(serverPidsFromProcRows(rows, 9877, 999).sort()).toEqual([101, 102, 105])
    expect(serverPidsFromProcRows(rows, 98770, 999)).toEqual([103])
    expect(serverPidsFromProcRows(rows, 9877, 101)).toEqual([102, 105])
  })
})

describe('samePortHolders and killHolders', () => {
  it('uses the fake /proc holders and does not call the Windows fallback', () => {
    stubProc({
      '201': ['node', '/opt/dsh/lib/runtime/server.js', '--port', '9877'],
    })
    expect(samePortHolders(9877)).toEqual([201])
    expect(h.winSamePortHolders).not.toHaveBeenCalled()
  })

  it('falls back to the Windows port probe only when /proc has no holder', () => {
    stubNoProc()
    h.winSamePortHolders.mockImplementation(() => [555])
    const holders = samePortHolders(9877)
    if (process.platform === 'win32') {
      expect(h.winSamePortHolders).toHaveBeenCalledWith(9877)
      expect(holders).toEqual([555])
    } else {
      expect(h.winSamePortHolders).not.toHaveBeenCalled()
      expect(holders).toEqual([])
    }
  })

  it('sends SIGTERM to POSIX holders and delegates to winKillPids on win32', () => {
    stubProc({
      '301': ['node', '/opt/dsh/lib/runtime/server.js', '--port', '9877'],
    })
    const posixKill = vi.spyOn(process, 'kill').mockImplementation(() => true)
    const killed = killHolders(9877, 'SIGTERM')
    if (process.platform === 'win32') {
      expect(h.winKillPids).toHaveBeenCalledWith([301])
      expect(posixKill).not.toHaveBeenCalled()
      expect(killed).toEqual([301])
    } else {
      expect(posixKill).toHaveBeenCalledWith(301, 'SIGTERM')
      expect(h.winKillPids).not.toHaveBeenCalled()
      expect(killed).toEqual([301])
    }
  })

  it('returns [] when the platform kill fails', () => {
    stubProc({
      '401': ['node', '/opt/dsh/lib/runtime/server.js', '--port', '9877'],
    })
    if (process.platform === 'win32') {
      h.winKillPids.mockImplementation(() => {
        throw new Error('taskkill failed')
      })
      expect(killHolders(9877, 'SIGTERM')).toEqual([])
    } else {
      vi.spyOn(process, 'kill').mockImplementation(() => {
        throw new Error('ESRCH')
      })
      expect(killHolders(9877, 'SIGTERM')).toEqual([])
    }
  })
})

describe('holderResponsive', () => {
  it('returns true only when /health answers ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true })),
    )
    await expect(holderResponsive(9877, 50)).resolves.toBe(true)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false })),
    )
    await expect(holderResponsive(9877, 50)).resolves.toBe(false)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('ECONNREFUSED')
      }),
    )
    await expect(holderResponsive(9877, 50)).resolves.toBe(false)
  })
})
