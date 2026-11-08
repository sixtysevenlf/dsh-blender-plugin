/**
 * 回归（PR #75 review）：job spawn 前的 blender.exe 预检。
 * 为空时必须在 spawn 之前抛出结构化 blender_exe_missing（附 blender_rt_job 的重试指引），
 * 而不是让 Node 抛出 "The \"file\" argument must be of type string"。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  exe: null as string | null,
  source: 'not-found' as string | null,
  spawnCalls: [] as unknown[][],
}))

vi.mock('node:child_process', async () => {
  const spawn = vi.fn((...args: unknown[]) => {
    h.spawnCalls.push(args)
    throw new Error('spawn should not be reached when blenderExe is missing')
  })
  return { spawn, default: { spawn } }
})

vi.mock('../../../runtime/paths.js', () => ({
  get BLENDER_EXE() {
    return h.exe
  },
}))

vi.mock('../../../runtime/config.js', () => ({
  CFG: {
    get source() {
      return { blenderExe: h.source }
    },
  },
  wslToWin: (p: unknown) => String(p),
}))

import { spawnJobChild } from '../../../runtime/execution/jobs/start/spawn.js'

afterEach(() => {
  h.exe = null
  h.source = 'not-found'
  h.spawnCalls.length = 0
})

describe('job spawn exe guard', () => {
  it('throws blender_exe_missing before spawning when BLENDER_EXE is null', () => {
    let err: any = null
    try {
      spawnJobChild(
        { jobs: new Map() } as any,
        {} as any,
        { args: ['-b'], envPairs: [] } as any,
        {} as any,
        {} as any,
        {},
      )
    } catch (e) {
      err = e
    }
    expect(err).toBeInstanceOf(Error)
    expect(err.code).toBe('blender_exe_missing')
    expect(String(err.message)).toContain('not-found')
    expect(String(err.message)).not.toContain('The "file" argument')
    expect(String(err.hint)).toContain('blender_rt_job op=start')
    expect(String(err.hint)).toContain('DSH_BLENDER_EXE')
    expect(h.spawnCalls).toHaveLength(0)
  })
})
