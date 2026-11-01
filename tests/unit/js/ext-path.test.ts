import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { resolveExtPaths } from '../../../runtime/core/plan/ext-check.js'

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

  it('keeps the venv under a real runtime/ext directory', () => {
    const p = resolveExtPaths()
    expect(path.basename(p.extDir)).toBe('ext')
    expect(path.basename(path.dirname(p.extDir))).toBe('runtime')
    expect(
      fs.existsSync(path.join(p.extDir, 'gltf_validate_runner.ts')) || fs.existsSync(p.gltfRunner),
    ).toBe(true)
  })
})
