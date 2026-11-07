import { afterEach, describe, expect, it, vi } from 'vitest'

// item 4 / issue #51: process-scan.ts used to be verified by grepping its
// source for "IS_WIN". This test forces the win32 branch (IS_WIN=true) and
// proves that the Windows cleanup path is taken, the POSIX /proc path is NOT,
// and that a probe failure degrades to [] instead of throwing.

const h = vi.hoisted(() => ({
  winSamePortHolders: vi.fn((_port: number) => [4242]),
  winKillPids: vi.fn((pids: number[]) => pids.map(String)),
  serverPidsFromProcRows: vi.fn(() => [9999]),
}))
vi.mock('../../../src/constants.js', () => ({
  IS_WIN: true,
  GATEWAY_PATH: 'D:/fake/lib/runtime/gateway.js',
}))
vi.mock('../../../src/host/runtime-bridge.js', () => ({
  winKillPids: h.winKillPids,
  winSamePortHolders: h.winSamePortHolders,
  serverPidsFromProcRows: h.serverPidsFromProcRows,
}))

import { killBackendByCmd } from '../../../src/host/backend/process-scan.js'

afterEach(() => {
  vi.clearAllMocks()
  h.winSamePortHolders.mockImplementation(() => [4242])
  h.winKillPids.mockImplementation((pids: number[]) => pids.map(String))
})

describe('killBackendByCmd on win32', () => {
  it('kills the Windows port holders without touching the POSIX probes', () => {
    const killed = killBackendByCmd(9877)
    expect(h.winSamePortHolders).toHaveBeenCalledWith(9877)
    expect(h.winKillPids).toHaveBeenCalledWith([4242])
    expect(h.serverPidsFromProcRows).not.toHaveBeenCalled()
    expect(killed).toEqual(['4242'])
  })

  it('passes the signal through to winKillPids callers unharmed (win path ignores it)', () => {
    expect(killBackendByCmd(9877, 'SIGKILL')).toEqual(['4242'])
    expect(h.winKillPids).toHaveBeenCalledTimes(1)
  })

  it('returns [] when the Windows probe itself fails', () => {
    h.winSamePortHolders.mockImplementation(() => {
      throw new Error('tasklist unavailable')
    })
    expect(killBackendByCmd(9877)).toEqual([])
    expect(h.winKillPids).not.toHaveBeenCalled()
    expect(h.serverPidsFromProcRows).not.toHaveBeenCalled()
  })

  it('returns [] when the kill call fails', () => {
    h.winKillPids.mockImplementation(() => {
      throw new Error('taskkill failed')
    })
    expect(killBackendByCmd(9877)).toEqual([])
  })
})
