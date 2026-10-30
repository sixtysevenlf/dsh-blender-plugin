import { afterAll, describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pkgPath, PKG_ROOT } from '../../helpers/pkg'
import { ensureBuild } from '../../helpers/ensure-build'

ensureBuild()
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-t14-pack-'))
/**
 * Environment for the child npm. Strip every inherited npm_config_* /
 * npm_lifecycle_* entry: when the whole suite runs under `npm test`, npm
 * exports npm config with BOTH cases (NPM_CONFIG_CACHE="..." and
 * npm_config_cache="..."). Spreading process.env then keeps the uppercase
 * value, and on Windows (case-insensitive environment) the inherited default
 * cache wins over the per-test cache override -> EPERM on the user profile.
 */
function npmEnvFor(extra: Record<string, string>): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {}
  for (const [key, value] of Object.entries(process.env)) {
    const lower = key.toLowerCase()
    if (lower.startsWith('npm_config_') || lower.startsWith('npm_lifecycle_')) continue
    env[key] = value
  }
  return { ...env, ...extra }
}

const npmEnv = npmEnvFor({
  TEMP: tmpRoot,
  TMP: tmpRoot,
  npm_config_cache: path.join(tmpRoot, 'npm-cache'),
  npm_config_offline: 'true',
  npm_config_audit: 'false',
  npm_config_fund: 'false',
  npm_config_ignore_scripts: 'true',
  npm_config_legacy_peer_deps: 'true',
})

function npm(args: string[]): string {
  return execFileSync('npm', args, { cwd: PKG_ROOT, env: npmEnv, encoding: 'utf8', shell: true, timeout: 60000 })
}

function packMeta(raw: string): any {
  const start = raw.search(/[[{]/)
  const parsed = JSON.parse(raw.slice(start))
  return Array.isArray(parsed) ? parsed[0] : Object.values(parsed)[0]
}

afterAll(() => {
  try { fs.rmSync(tmpRoot, { recursive: true, force: true }) } catch (e) {}
})

describe('npm package install contract', () => {
  it('packs the runtime, addon, docs, skills, and tests without bundled environments', () => {
    const outDir = path.join(tmpRoot, 'pack')
    fs.mkdirSync(outDir, { recursive: true })
    const raw = npm(['pack', '--json', '--pack-destination', outDir])
    const meta = packMeta(raw)
    expect(meta.name).toBe('@dsh-external/dsh-blender-plugin')
    expect(meta.version).toBe('2.6.2')
    const files = meta.files.map((f: any) => f.path)
    expect(files).toContain('lib/index.js')
    expect(files).toContain('lib/runtime/server.js')
    expect(files).toContain('lib/runtime/catalog.js')
    expect(files).toContain('addon/dsh_blender_bridge.zip')
    expect(files).toContain('addon/dsh_blender_bridge/__init__.py')
    expect(files).toContain('package.json')
    expect(files.some((f: string) => f.startsWith('skills/'))).toBe(true)
    expect(files.some((f: string) => f.startsWith('docs/'))).toBe(true)
    expect(files.some((f: string) => f.startsWith('tests/'))).toBe(true)
    expect(files.some((f: string) => f.includes('ext/venv'))).toBe(false)
    expect(files.some((f: string) => f.includes('node_modules'))).toBe(false)
    expect(files.some((f: string) => f.includes('pkg.tgz'))).toBe(false)
    expect(files.some((f: string) => f.endsWith('.pyc'))).toBe(false)
    // T15: runtime/source TypeScript must not ship; .d.ts declarations are allowed (package types).
    expect(files.some((f: string) => f.endsWith('.ts') && !f.endsWith('.d.ts'))).toBe(false)
    expect(files.some((f: string) => f.startsWith('runtime/') && /\.(?:ts|mts)$/.test(f))).toBe(false)
    expect(files.some((f: string) => f.startsWith('runtime/') && f.endsWith('.py'))).toBe(true)
    expect(meta.size).toBeLessThan(10 * 1024 * 1024)
    expect(meta.entryCount).toBeGreaterThan(200)
  })

  it('installs the tarball offline and exposes a syntax-valid entry point', () => {
    const outDir = path.join(tmpRoot, 'install-pack')
    fs.mkdirSync(outDir, { recursive: true })
    const raw = npm(['pack', '--json', '--pack-destination', outDir])
    const meta = packMeta(raw)
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
    expect(fs.existsSync(path.join(installed, 'lib', 'runtime', 'server.js'))).toBe(true)
    expect(fs.existsSync(path.join(installed, 'runtime', 'server.mjs'))).toBe(false)
    expect(fs.existsSync(path.join(installed, 'addon', 'dsh_blender_bridge.zip'))).toBe(true)
    expect(fs.existsSync(path.join(installed, 'addon', 'dsh_blender_bridge', '__init__.py'))).toBe(true)
    expect(fs.existsSync(path.join(installed, 'skills', 'blender-modeling', 'SKILL.md'))).toBe(true)

    execFileSync(process.execPath, ['--check', path.join(installed, 'lib', 'index.js')], { encoding: 'utf8' })

    const addonInit = fs.readFileSync(path.join(installed, 'addon', 'dsh_blender_bridge', '__init__.py'), 'utf8')
    const addonProtocol = fs.readFileSync(path.join(installed, 'addon', 'dsh_blender_bridge', 'protocol.py'), 'utf8')
    expect(addonProtocol).toContain('ADDON_PROTOCOL_VERSION = 11')
    expect(addonInit).toContain('bl_info = {')
    expect(addonInit).toContain('"version": (1, 11)')
    const addonDir = path.join(installed, 'addon')
    expect(fs.readdirSync(addonDir).filter((f) => f.endsWith('.py'))).toEqual([])
    expect(fs.statSync(path.join(addonDir, 'dsh_blender_bridge.zip')).size).toBeGreaterThan(100000)
  }, 30000)

  it('keeps the old-addon removal path documented', () => {
    const migrations = fs.readFileSync(pkgPath('MIGRATIONS.md'), 'utf8')
    expect(migrations).toContain('停用并移除旧 addon')
    expect(migrations).toContain('dsh_blender_bridge.py')
    expect(migrations).toContain('重启 DSH')
  })
})
