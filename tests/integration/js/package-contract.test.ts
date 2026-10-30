import { afterAll, describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pkgPath, PKG_ROOT } from '../../helpers/pkg'

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-t14-pack-'))
const npmEnv = {
  ...process.env,
  TEMP: tmpRoot,
  TMP: tmpRoot,
  npm_config_cache: path.join(tmpRoot, 'npm-cache'),
  npm_config_offline: 'true',
  npm_config_audit: 'false',
  npm_config_fund: 'false',
  npm_config_ignore_scripts: 'true',
  npm_config_legacy_peer_deps: 'true',
}

function npm(args: string[]): string {
  return execFileSync('npm', args, { cwd: PKG_ROOT, env: npmEnv, encoding: 'utf8', shell: true, timeout: 60000 })
}

afterAll(() => {
  try { fs.rmSync(tmpRoot, { recursive: true, force: true }) } catch (e) {}
})

describe('npm package install contract', () => {
  it('packs the runtime, addon, docs, skills, and tests without bundled environments', () => {
    const outDir = path.join(tmpRoot, 'pack')
    fs.mkdirSync(outDir, { recursive: true })
    const raw = npm(['pack', '--json', '--pack-destination', outDir])
    const meta = JSON.parse(raw.trim().slice(raw.indexOf('[')))[0]
    expect(meta.name).toBe('@dsh-external/dsh-blender-plugin')
    expect(meta.version).toBe('2.6.2')
    const files = meta.files.map((f: any) => f.path)
    expect(files).toContain('lib/index.js')
    expect(files).toContain('runtime/server.mjs')
    expect(files).toContain('runtime/catalog.mjs')
    expect(files).toContain('addon/dsh_blender_bridge.py')
    expect(files).toContain('package.json')
    expect(files.some((f: string) => f.startsWith('skills/'))).toBe(true)
    expect(files.some((f: string) => f.startsWith('docs/'))).toBe(true)
    expect(files.some((f: string) => f.startsWith('tests/'))).toBe(true)
    expect(files.some((f: string) => f.includes('ext/venv'))).toBe(false)
    expect(files.some((f: string) => f.includes('node_modules'))).toBe(false)
    expect(files.some((f: string) => f.includes('pkg.tgz'))).toBe(false)
    expect(files.some((f: string) => f.endsWith('.pyc'))).toBe(false)
    expect(meta.size).toBeLessThan(10 * 1024 * 1024)
    expect(meta.entryCount).toBeGreaterThan(200)
  })

  it('installs the tarball offline and exposes a syntax-valid entry point', () => {
    const outDir = path.join(tmpRoot, 'install-pack')
    fs.mkdirSync(outDir, { recursive: true })
    const raw = npm(['pack', '--json', '--pack-destination', outDir])
    const meta = JSON.parse(raw.trim().slice(raw.indexOf('[')))[0]
    const tgz = path.join(outDir, meta.filename)
    expect(fs.existsSync(tgz)).toBe(true)

    const app = path.join(tmpRoot, 'app')
    fs.mkdirSync(app, { recursive: true })
    const installOut = npm(['install', '--prefix', app, '--no-audit', '--no-fund', '--ignore-scripts', '--legacy-peer-deps', '--offline', tgz])
    expect(installOut).toContain('added 1 package')

    const installed = path.join(app, 'node_modules', '@dsh-external', 'dsh-blender-plugin')
    const pkg = JSON.parse(fs.readFileSync(path.join(installed, 'package.json'), 'utf8'))
    expect(pkg.name).toBe('@dsh-external/dsh-blender-plugin')
    expect(fs.existsSync(path.join(installed, 'lib', 'index.js'))).toBe(true)
    expect(fs.existsSync(path.join(installed, 'runtime', 'server.mjs'))).toBe(true)
    expect(fs.existsSync(path.join(installed, 'addon', 'dsh_blender_bridge.py'))).toBe(true)
    expect(fs.existsSync(path.join(installed, 'skills', 'blender-modeling', 'SKILL.md'))).toBe(true)

    execFileSync(process.execPath, ['--check', path.join(installed, 'lib', 'index.js')], { encoding: 'utf8' })

    const addon = fs.readFileSync(path.join(installed, 'addon', 'dsh_blender_bridge.py'), 'utf8')
    expect(addon).toContain('ADDON_PROTOCOL_VERSION = 11')
    expect(addon).toContain('bl_info = {')
    expect(addon).toContain('"version": (1, 10)')
    const addonDir = path.join(installed, 'addon')
    const pyFiles = fs.readdirSync(addonDir).filter((f) => f.endsWith('.py'))
    expect(pyFiles).toEqual(['dsh_blender_bridge.py'])
  }, 30000)

  it('keeps the old-addon removal path documented', () => {
    const migrations = fs.readFileSync(pkgPath('MIGRATIONS.md'), 'utf8')
    expect(migrations).toContain('停用旧 addon')
    expect(migrations).toContain('dsh_blender_bridge.py')
    expect(migrations).toContain('重启 DSH')
  })
})
