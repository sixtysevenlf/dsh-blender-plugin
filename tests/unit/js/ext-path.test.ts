import fs from 'node:fs'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resolveExtPaths } from '../../../runtime/core/plan/ext-check.js'

afterEach(() => vi.restoreAllMocks())

describe('ext external-validator paths', () => {
  it('resolves an existing glTF-Validator runner in this layout', () => {
    const p = resolveExtPaths()
    expect(
      p.gltfRunner.endsWith('gltf_validate_runner.js') ||
        p.gltfRunner.endsWith('gltf_validate_runner.ts'),
    ).toBe(true)
    expect(fs.existsSync(p.gltfRunner)).toBe(true)
    expect(p.gltfRunnerExists).toBe(true)
  })

  it('resolves the open3d checker under runtime/py/export', () => {
    const p = resolveExtPaths()
    expect(path.basename(p.open3dRunner)).toBe('open3d_check.py')
    expect(p.open3dRunner.replace(/\\/g, '/')).toMatch(/\/runtime\/py\/export\/open3d_check\.py$/)
    expect(fs.existsSync(p.open3dRunner)).toBe(true)
    expect(p.open3dRunnerExists).toBe(true)
  })

  it('prefers the built .js runner on the production layout (F0.2)', () => {
    // Source checkout: only the .ts runner exists, so it is selected.
    expect(resolveExtPaths().gltfRunner.endsWith('gltf_validate_runner.ts')).toBe(true)
    // Build layout: when the compiled .js runner exists, it must win.
    const spy = vi.spyOn(fs, 'existsSync').mockImplementation((p) => {
      return String(p).endsWith('gltf_validate_runner.js')
    })
    const built = resolveExtPaths()
    expect(built.gltfRunner.endsWith('gltf_validate_runner.js')).toBe(true)
    expect(built.gltfRunnerExists).toBe(true)
    spy.mockRestore()
  })

  it('selects the built .js runner from the real compiled lib layout (F0.2)', async () => {
    // The source test above imports runtime/*.ts; the production layout resolves
    // paths relative to lib/runtime. Exercise the compiled resolver against the
    // real build output (npm run build produces lib/runtime/ext/*.js).
    const built = (await import('../../../lib/runtime/core/plan/ext-check.js')) as {
      resolveExtPaths: () => {
        gltfRunner: string
        gltfRunnerExists: boolean
        extDir: string
      }
    }
    const p = built.resolveExtPaths()
    expect(p.gltfRunner.replace(/\\/g, '/')).toMatch(
      /\/lib\/runtime\/ext\/gltf_validate_runner\.js$/,
    )
    expect(fs.existsSync(p.gltfRunner)).toBe(true)
    expect(p.gltfRunnerExists).toBe(true)
  })

  it('keeps the venv under a real runtime/ext directory', () => {
    const p = resolveExtPaths()
    expect(path.basename(p.extDir)).toBe('ext')
    expect(path.basename(path.dirname(p.extDir))).toBe('runtime')
    expect(
      fs.existsSync(path.join(p.extDir, 'gltf_validate_runner.ts')) || fs.existsSync(p.gltfRunner),
    ).toBe(true)
  })
})
