import { afterEach, describe, expect, it, vi } from 'vitest'

// item 4: win-spawn.test.ts could only grep spawn.ts source. Here the real
// spawnChild()/childAlive()/killStaleChild()/killChildHandle() run against a
// mocked node:child_process, so issue #51's spawn options and listener order are
// proven behaviorally on every platform (including Windows).

const h = vi.hoisted(() => {
  const listeners: Record<string, Array<(...a: unknown[]) => void>> = {}
  const calls: unknown[][] = []
  const events: string[] = []
  const child: any = {
    pid: 4321,
    exitCode: null as number | null,
    signalCode: null as string | null,
    kill: vi.fn(),
    unref: vi.fn(() => events.push('unref')),
    on: vi.fn((ev: string, fn: (...a: unknown[]) => void) => {
      ;(listeners[ev] ||= []).push(fn)
      events.push('on:' + ev)
      return child
    }),
  }
  const spawn = vi.fn((...args: unknown[]) => {
    calls.push(args)
    return child
  })
  return { listeners, calls, events, child, spawn }
})
vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, spawn: h.spawn }
})
vi.mock('../../../src/host/runtime-bridge.js', () => ({
  winKillPids: vi.fn((pids: number[]) => pids.map(String)),
}))

import {
  childAlive,
  getChild,
  killChildHandle,
  killStaleChild,
  spawnChild,
} from '../../../src/host/backend/spawn.js'
import { winKillPids } from '../../../src/host/runtime-bridge.js'

function emit(ev: string): void {
  for (const fn of h.listeners[ev] || []) fn(new Error('spawn EPERM'))
}

afterEach(() => {
  // Each test spawns its own child; clear the module handle to isolate them.
  killChildHandle()
  vi.restoreAllMocks()
  h.calls.length = 0
  h.events.length = 0
  for (const k of Object.keys(h.listeners)) delete h.listeners[k]
  h.child.kill.mockClear()
  h.child.unref.mockClear()
  h.child.on.mockClear()
  h.spawn.mockClear()
  h.child.exitCode = null
  h.child.signalCode = null
})

describe('spawnChild spawn options (issue #51)', () => {
  it('spawns process.execPath with the built Gateway path and the port', () => {
    const out = spawnChild(9877)
    expect(out).toBe('spawned pid=4321')
    expect(h.spawn).toHaveBeenCalledTimes(1)
    const [file, args, opts] = h.spawn.mock.calls[0] as [string, string[], any]
    expect(file).toBe(process.execPath)
    expect(args.slice(0, 2)).toEqual([expect.stringMatching(/gateway\.(mjs|js)$/), '--port'])
    expect(args[2]).toBe('9877')
    expect(opts.env.ELECTRON_RUN_AS_NODE).toBe('1')
    expect(opts.env.PATH).toBe(process.env.PATH)
    expect(Array.isArray(opts.stdio)).toBe(true)
    expect(opts.stdio[0]).toBe('ignore')
    expect(typeof opts.stdio[1]).toBe('number')
    expect(opts.detached).toBe(false)
    expect(opts.windowsHide).toBe(true)
    expect(getChild()).toBe(h.child)
    expect(childAlive()).toBe(true)
  })

  it('attaches error/exit listeners and keeps the Gateway in the host process group', () => {
    spawnChild(9877)
    expect(h.events.indexOf('on:error')).toBeGreaterThanOrEqual(0)
    expect(h.events.indexOf('on:exit')).toBeGreaterThanOrEqual(0)
    expect(h.child.unref).not.toHaveBeenCalled()
  })

  it('turns an asynchronous spawn error into a degraded handle, not a host crash', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    spawnChild(9877)
    expect(() => emit('error')).not.toThrow()
    expect(getChild()).toBeNull()
    expect(childAlive()).toBe(false)
    expect(errSpy).toHaveBeenCalledWith(expect.stringContaining('Gateway 子进程启动失败'))
  })

  it('clears the handle when the child exits', () => {
    spawnChild(9877)
    emit('exit')
    expect(getChild()).toBeNull()
    expect(childAlive()).toBe(false)
  })
})

describe('child handle lifecycle', () => {
  it('killChildHandle reports whether a handle existed and kills it once', () => {
    expect(killChildHandle()).toBe(false)
    spawnChild(9877)
    expect(killChildHandle()).toBe(true)
    expect(h.child.kill).toHaveBeenCalledTimes(1)
    expect(getChild()).toBeNull()
    expect(killChildHandle()).toBe(false)
  })

  it('killStaleChild uses the platform cleanup path and returns the pid', () => {
    spawnChild(9877)
    const posixKill = vi.spyOn(process, 'kill').mockImplementation(() => true)
    const pid = killStaleChild()
    expect(pid).toBe(4321)
    if (process.platform === 'win32') {
      expect(vi.mocked(winKillPids)).toHaveBeenCalledWith([4321])
    } else {
      expect(posixKill).toHaveBeenCalledWith(4321, 'SIGTERM')
    }
    expect(getChild()).toBeNull()
    expect(killStaleChild()).toBeNull()
  })

  it('childAlive treats signal-killed handles as dead', () => {
    spawnChild(9877)
    h.child.signalCode = 'SIGTERM'
    expect(childAlive()).toBe(false)
    h.child.signalCode = null
    h.child.exitCode = 14
    expect(childAlive()).toBe(false)
  })
})
