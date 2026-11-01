import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// F2.2 tail: extCheckOp was split into candidates/result stages. These tests pin
// the two-platform routing decisions and the public result contract directly
// (the old tests only checked resolveExtPaths).

const H = vi.hoisted(() => ({ spawnSync: vi.fn() }))
vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>()
  return { ...actual, spawnSync: H.spawnSync }
})

import {
  extCandidates,
  extInstallHint,
  runExtCandidates,
  toWslPath,
} from '../../../runtime/core/plan/ext-check-candidates.js'
import { gltfResult, open3dResult } from '../../../runtime/core/plan/ext-check-result.js'
import { extCheckOp, resolveExtPaths } from '../../../runtime/core/plan/ext-check.js'

beforeEach(() => H.spawnSync.mockReset())
afterEach(() => vi.restoreAllMocks())

describe('ext-check path routing', () => {
  it('maps Windows drive paths to WSL mount paths and leaves others alone', () => {
    expect(toWslPath('D:\\work\\x.glb')).toBe('/mnt/d/work/x.glb')
    expect(toWslPath('d:/work/x.glb')).toBe('/mnt/d/work/x.glb')
    expect(toWslPath('/home/me/x.glb')).toBe('/home/me/x.glb')
    expect(toWslPath('relative/x.glb')).toBe('relative/x.glb')
    expect(toWslPath('')).toBe('')
  })

  it('puts the native representation first and WSL last for both platforms', () => {
    const input = {
      op: 'gltf_validate',
      extDir: 'E:/pkg/runtime/ext',
      runner: 'E:/pkg/runtime/ext/gltf_validate_runner.ts',
      winPath: 'D:\\a\\x.glb',
      wslPath: '/mnt/d/a/x.glb',
    }
    expect(extCandidates({ ...input, isWin: true })).toEqual([
      ['node', input.runner, input.winPath],
      ['node', input.runner, input.wslPath],
      ['wsl.exe', '-e', 'node', input.runner, input.wslPath],
    ])
    expect(extCandidates({ ...input, isWin: false })).toEqual([
      ['node', input.runner, input.wslPath],
      ['node', input.runner, input.winPath],
      ['wsl.exe', '-e', 'node', input.runner, input.wslPath],
    ])
  })

  it('routes open3d through the venv python of the active platform', () => {
    const extDir = 'E:/pkg/runtime/ext'
    const runner = 'E:/pkg/runtime/ext/open3d_check.py'
    const cands = extCandidates({
      op: 'open3d_check',
      extDir,
      runner,
      winPath: 'D:\\a\\x.glb',
      wslPath: '/mnt/d/a/x.glb',
      isWin: true,
    })
    expect(cands).toEqual([
      [path.join(extDir, 'venv', 'bin', 'python'), runner, 'D:\\a\\x.glb'],
      [path.join(extDir, 'venv', 'bin', 'python'), runner, '/mnt/d/a/x.glb'],
      [
        'wsl.exe',
        '-e',
        path.join(extDir, 'venv', 'bin', 'python'),
        runner,
        '/mnt/d/a/x.glb',
      ],
      [path.join(extDir, 'venv', 'Scripts', 'python.exe'), runner, 'D:\\a\\x.glb'],
    ])
  })
})

describe('ext candidate runner', () => {
  it('stops at the first RAW/EXT line and records every attempt', () => {
    H.spawnSync
      .mockReturnValueOnce({ stdout: 'loading\n', stderr: '', status: 1, error: null })
      .mockReturnValueOnce({ stdout: 'EXT {"ok":true}', stderr: '', status: 0, error: null })
    const cands = [
      ['node', 'runner.js', 'a'],
      ['node', 'runner.js', 'b'],
      ['wsl.exe', '-e', 'node', 'runner.js', 'c'],
    ]
    const run = runExtCandidates({ spawnSync: H.spawnSync as any }, cands)
    expect(run).toEqual({
      raw: '{"ok":true}',
      used: 'node runner.js',
      tried: [
        { cmd: 'node runner.js', status: 1, err: null },
        { cmd: 'node runner.js', status: 0, err: null },
      ],
    })
    expect(H.spawnSync).toHaveBeenCalledTimes(2)
    expect(H.spawnSync.mock.calls[0][2]).toEqual({
      encoding: 'utf8',
      timeout: 120000,
      windowsHide: true,
    })
  })

  it('survives a thrown spawn and reports spawn error messages capped at 80 chars', () => {
    H.spawnSync
      .mockImplementationOnce(() => {
        throw new Error('x'.repeat(120))
      })
      .mockReturnValueOnce({ stdout: 'RAW {"objects":2}', stderr: '', status: 0, error: null })
    const run = runExtCandidates({ spawnSync: H.spawnSync as any }, [
      ['node', 'runner.js', 'a'],
      ['node', 'runner.js', 'b'],
    ])
    expect(run.raw).toBe('{"objects":2}')
    expect(run.tried).toHaveLength(2)
    expect(String(run.tried[0].err)).toHaveLength(80)
  })

  it('returns raw=null when no candidate prints a marker', () => {
    H.spawnSync.mockReturnValue({ stdout: 'noise', stderr: 'err', status: 1, error: null })
    const run = runExtCandidates({ spawnSync: H.spawnSync as any }, [
      ['node', 'runner.js', 'a'],
    ])
    expect(run).toEqual({
      raw: null,
      used: null,
      tried: [{ cmd: 'node runner.js', status: 1, err: null }],
    })
  })
})

describe('ext install hint stays pasteable and absolute', () => {
  const extDir = path.join('E:', 'pkg', 'runtime', 'ext')
  it('builds a gltf-validator command with the resolved absolute dir', () => {
    const hint = extInstallHint({
      op: 'gltf_validate',
      extDir,
      winPath: 'D:\\x.glb',
      isWin: true,
      tried: [],
    })
    expect(hint.ok).toBe(false)
    expect(hint.installed).toBe(false)
    expect(hint.path).toBe('D:\\x.glb')
    expect(hint.install).toContain('npm i gltf-validator')
    expect(hint.install).toContain(path.resolve(extDir))
    expect(String(hint.hint)).toContain(path.resolve(extDir))
  })

  it('offers the platform-specific open3d venv command', () => {
    const win = extInstallHint({ op: 'open3d', extDir, winPath: 'x', isWin: true, tried: [] })
    expect(String(win.install)).toContain('py -3 -m venv venv')
    expect(String(win.install)).toContain('venv\\Scripts\\pip install open3d')
    const posix = extInstallHint({ op: 'open3d', extDir, winPath: 'x', isWin: false, tried: [] })
    expect(String(posix.install)).toContain('python3 -m venv venv')
    expect(String(posix.install)).toContain('venv/bin/pip install open3d')
  })
})

describe('ext result formatting', () => {
  it('formats glTF issues with bounded messages and the numErrors pass rule', () => {
    const messages = Array.from({ length: 15 }, (_, i) => ({
      code: 'CODE_' + i,
      severity: 1,
      pointer: '/x/' + i,
      message: 'm'.repeat(200),
    }))
    const issues = {
      numErrors: 0,
      numWarnings: 2,
      numInfos: 3,
      numHints: 4,
      messages,
    }
    const parsed = {
      issues,
      validatorVersion: '2.0.0',
      info: {
        version: '2.0',
        generator: 'blender',
        drawCallCount: 1,
        totalVertexCount: 2,
        totalTriangleCount: 3,
        maxUVs: 1,
      },
    }
    const out = gltfResult(parsed, 'D:\\x.glb', { used: 'node runner' })
    expect(out).toMatchObject({
      ok: true,
      tool: 'gltf-validator',
      validatorVersion: '2.0.0',
      path: 'D:\\x.glb',
      errors: 0,
      warnings: 2,
      infos: 3,
      hints: 4,
      used: 'node runner',
    })
    expect(out.messages).toHaveLength(12)
    expect((out.messages as Array<{ message: string }>)[0].message).toHaveLength(160)
    expect(out.info).toEqual({
      version: '2.0',
      generator: 'blender',
      drawCallCount: 1,
      totalVertexCount: 2,
      totalTriangleCount: 3,
      maxUVs: 1,
    })
    expect(String(out.note)).toContain('numErrors==0')

    const failed = gltfResult({ issues: { numErrors: 2 } }, 'x', { used: null })
    expect(failed.ok).toBe(false)
    expect(failed.info).toBeNull()
    expect(gltfResult(null, 'x', { used: null }).ok).toBe(false)
  })

  it('merges the open3d payload over the routing footer', () => {
    const run = { used: 'python open3d_check.py x', tried: [{ cmd: 'python' }] }
    expect(open3dResult({ objects: 3, ok: true }, run)).toEqual({
      tool: 'open3d',
      used: 'python open3d_check.py x',
      tried: [{ cmd: 'python' }],
      objects: 3,
      ok: true,
    })
    expect(open3dResult(null, run)).toEqual({
      tool: 'open3d',
      used: 'python open3d_check.py x',
      tried: [{ cmd: 'python' }],
    })
  })
})

describe('extCheckOp public behavior (mocked child_process)', () => {
  it('runs gltf-validate and returns the validator receipt', async () => {
    H.spawnSync.mockReturnValue({
      stdout: 'EXT {"issues":{"numErrors":0},"validatorVersion":"2.0.0"}',
      stderr: '',
      status: 0,
      error: null,
    })
    const out = (await extCheckOp('gltf_validate', { path: 'D:\\x.glb' })) as Record<
      string,
      unknown
    >
    expect(out).toMatchObject({
      ok: true,
      tool: 'gltf-validator',
      validatorVersion: '2.0.0',
      path: 'D:\\x.glb',
    })
    const [cmd, args] = H.spawnSync.mock.calls[0] as [string, string[]]
    expect(cmd).toBe('node')
    expect(args[0]).toMatch(/gltf_validate_runner\.(ts|js)$/)
  })

  it('runs the open3d checker and merges its JSON', async () => {
    H.spawnSync.mockReturnValue({
      stdout: 'RAW {"objects":3}',
      stderr: '',
      status: 0,
      error: null,
    })
    const out = (await extCheckOp('open3d_check', { path: '/home/me/x.glb' })) as Record<
      string,
      unknown
    >
    expect(out).toMatchObject({ tool: 'open3d', objects: 3 })
    expect(out.used).toBeTruthy()
  })

  it('returns the install hint when every candidate fails', async () => {
    H.spawnSync.mockReturnValue({ stdout: 'nope', stderr: '', status: 1, error: null })
    const out = (await extCheckOp('gltf_validate', { path: 'D:\\x.glb' })) as Record<
      string,
      unknown
    >
    expect(out.ok).toBe(false)
    expect(out.installed).toBe(false)
    expect(out.tried).toHaveLength(3)
    expect(String(out.install)).toContain(path.resolve(resolveExtPaths().extDir))
  })
})
