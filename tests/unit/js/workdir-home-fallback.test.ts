import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { PKG_ROOT } from '../../helpers/pkg'

const ORIGINAL = {
  DSH_BLENDER_HOME: process.env.DSH_BLENDER_HOME,
  DSH_BLENDER_WORKDIR: process.env.DSH_BLENDER_WORKDIR,
  DSH_BLENDER_CONFIG: process.env.DSH_BLENDER_CONFIG,
  DSH_BLENDER_PLUGIN_CONFIG: process.env.DSH_BLENDER_PLUGIN_CONFIG,
}

let root = ''

function restore(name: keyof typeof ORIGINAL): void {
  const value = ORIGINAL[name]
  if (value === undefined) delete process.env[name]
  else process.env[name] = value
}

function underPackageRoot(candidate: string): boolean {
  const base = path.resolve(PKG_ROOT) + path.sep
  return path.resolve(candidate).toLowerCase().startsWith(base.toLowerCase())
}

describe('workDir fallback stays out of the package root', () => {
  beforeAll(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-workdir-fallback-'))
    // A file in the path makes mkdir fail even on Windows (ENOTDIR), so the
    // configured home is genuinely unwritable instead of merely missing.
    fs.writeFileSync(path.join(root, 'not-a-dir'), '')
    delete process.env.DSH_BLENDER_WORKDIR
    delete process.env.DSH_BLENDER_PLUGIN_CONFIG
    process.env.DSH_BLENDER_HOME = path.join(root, 'not-a-dir', 'home')
    process.env.DSH_BLENDER_CONFIG = path.join(root, 'missing-config.json')
    vi.resetModules()
  })

  afterAll(() => {
    restore('DSH_BLENDER_HOME')
    restore('DSH_BLENDER_WORKDIR')
    restore('DSH_BLENDER_CONFIG')
    restore('DSH_BLENDER_PLUGIN_CONFIG')
    vi.resetModules()
    fs.rmSync(root, { recursive: true, force: true })
  })

  it('falls back to the OS temp directory when the configured home is unwritable', async () => {
    const cfg = await import('../../../runtime/config.js')
    const described = cfg.describeConfig({})
    const fallback = described.workDir.fallback
    if (!fallback) throw new Error('expected a workDir fallback')
    expect(fallback.usedFrom).toBe('fallback:tmpdir')
    expect(path.resolve(fallback.used)).toBe(path.resolve(os.tmpdir(), 'dsh-blender-rt'))
    expect(underPackageRoot(fallback.used)).toBe(false)
  })

  it('stages headless scripts outside the package root', async () => {
    const staging = await import('../../../runtime/staging.js')
    const staged = staging.writeHeadlessScript('print("probe")')
    try {
      expect(underPackageRoot(staged.staging.used)).toBe(false)
      expect(underPackageRoot(staged.wsl)).toBe(false)
    } finally {
      fs.rmSync(staged.wsl, { force: true })
    }
  })
})
