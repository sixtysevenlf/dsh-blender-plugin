import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { resolveHome as resolveRuntimeHome } from '../../../runtime/home.js'
import {
  ensureDnnModelsDir,
  ensureGpuDiyAsset,
  GPU_DIY_FILENAME,
  dnnPaths,
  resetDnnHomeCache,
  resolveDnnHome,
} from '../../../src/host/dnn-home.js'

const tempDirs: string[] = []

function tempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-blender-dnn-home-'))
  tempDirs.push(dir)
  return dir
}

afterEach(() => {
  resetDnnHomeCache()
  for (const dir of tempDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true })
})

describe('host dnn home', () => {
  it('resolves DSH_BLENDER_HOME -> DSH_HOME/dsh-blender -> ~/.dsh/dsh-blender like runtime/home', () => {
    const homedir = path.join(tempDir(), 'user')
    const cases = [
      { env: { DSH_BLENDER_HOME: path.join(homedir, 'explicit') }, homedir },
      { env: { DSH_HOME: path.join(homedir, 'dsh') }, homedir },
      { env: {}, homedir },
      { env: { DSH_BLENDER_HOME: '  ', DSH_HOME: '  ' }, homedir },
    ]
    for (const c of cases) {
      const host = resolveDnnHome(c.env, c.homedir)
      const runtime = resolveRuntimeHome(c.env, c.homedir)
      expect(host.home).toBe(runtime.home)
      expect(host.source).toBe(runtime.source)
      expect(host.requestedFrom).toBe(runtime.requestedFrom)
    }
  })

  it('builds the vision/model paths under home', () => {
    const home = tempDir()
    const p = dnnPaths(home)
    expect(p.home).toBe(path.resolve(home))
    expect(p.runtimeJson).toBe(path.join(home, 'vision', 'runtime.json'))
    expect(p.runtimeState).toBe(path.join(home, 'vision', 'runtime-state.json'))
    expect(p.modelsDir).toBe(path.join(home, 'vision', 'models'))
    expect(p.assetPath).toBe(path.join(home, 'vision', 'models', GPU_DIY_FILENAME))
    expect(p.probeScriptSuffix).toBe(path.join('runtime', 'vision', 'worker', 'probe.py'))
  })

  it('creates the models directory idempotently', () => {
    const home = tempDir()
    const first = ensureDnnModelsDir(home)
    expect(first).toBe(path.join(home, 'vision', 'models'))
    expect(fs.statSync(first).isDirectory()).toBe(true)
    fs.writeFileSync(path.join(first, 'keep.txt'), 'keep')
    expect(ensureDnnModelsDir(home)).toBe(first)
    expect(fs.readFileSync(path.join(first, 'keep.txt'), 'utf8')).toBe('keep')
  })

  it('copies GPU-DIY.md only when missing and never overwrites a user file', () => {
    const home = tempDir()
    const modelsDir = ensureDnnModelsDir(home)
    const target = path.join(modelsDir, GPU_DIY_FILENAME)
    const assetText = '# GPU DIY\n\n## 中文\n\n## English\n'
    const created = ensureGpuDiyAsset(home, { assetText })
    expect(created).toMatchObject({ ok: true, created: true, path: target })
    expect(fs.readFileSync(target, 'utf8')).toBe(assetText)

    fs.writeFileSync(target, 'my own notes')
    const skipped = ensureGpuDiyAsset(home, { assetText: 'replacement' })
    expect(skipped).toMatchObject({ ok: true, created: false, path: target })
    expect(fs.readFileSync(target, 'utf8')).toBe('my own notes')
  })

  it('reports a structured failure instead of throwing when the asset is unreadable', () => {
    const home = tempDir()
    const result = ensureGpuDiyAsset(home, {
      readAsset: () => {
        throw new Error('boom')
      },
    })
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.code).toBe('gpu_diy_asset_unavailable')
      expect(result.message).toContain('boom')
    }
  })

  it('ships a bilingual GPU-DIY asset with the required sections', () => {
    const home = tempDir()
    const result = ensureGpuDiyAsset(home)
    expect(result.ok).toBe(true)
    const text = fs.readFileSync(path.join(home, 'vision', 'models', GPU_DIY_FILENAME), 'utf8')
    expect(text).toContain('## 中文')
    expect(text).toContain('## English')
    expect(text).toContain(
      'https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md',
    )
    expect(text).toContain('vision.venv')
    expect(text).toContain('vision.dnn.backend')
    expect(text).toContain('vision.dnn.device')
  })
})
