#!/usr/bin/env node
/**
 * Clean-install smoke for the T15 migration (plan 8.2 B5/B6).
 *
 *   npm run build            -> lib/index.js + compiled lib/runtime JS (prepack too)
 *   npm pack                 -> tarball in a temp dir
 *   npm install --offline    -> install that tarball into an empty prefix
 *   node driver              -> entry metadata + syntax, SERVER_PATH resolves to
 *                               lib/runtime/server.js, doctor kind=ok against the
 *                               packaged fake addon, then boot the packaged
 *                               lib/runtime/server.js and hit /health + /status.
 *
 * All child npm calls run with npm_config_* stripped: under 'npm test' npm
 * exports both NPM_CONFIG_CACHE and npm_config_cache, and on Windows the
 * inherited uppercase default cache would otherwise win over our temp cache.
 * Prints SMOKE lines; exits non-zero on any failure. Nothing is left behind.
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ensureBuild } from './ensure-build.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-install-smoke-'))

const DRIVER = "import { spawn, spawnSync } from 'node:child_process'\nimport fs from 'node:fs'\nimport net from 'node:net'\nimport path from 'node:path'\nimport { pathToFileURL } from 'node:url'\n\nconst pkgRoot = path.join(process.cwd(), 'node_modules', '@dsh-external', 'dsh-blender-plugin')\nconst out = (label, value) => console.log('SMOKE ' + label + ' ' + (typeof value === 'string' ? value : JSON.stringify(value)))\n\n// Host entry (lib/index.js) imports DSH-host peer packages (@deepseek-ai/dsh-tools)\n// which a clean install outside a DSH checkout cannot resolve by design; verify the\n// built file parses and expose the package entry metadata. The standalone backend\n// entry (lib/runtime/engine.js / SERVER_PATH) is imported for real below.\nconst pkg = JSON.parse(fs.readFileSync(path.join(pkgRoot, 'package.json'), 'utf8'))\nout('package-entry', { main: pkg.main, exports: Object.keys(pkg.exports || {}) })\nconst syntax = spawnSync(process.execPath, ['--check', path.join(pkgRoot, 'lib', 'index.js')], { encoding: 'utf8' })\nif (syntax.status !== 0) throw new Error('node --check lib/index.js failed: ' + syntax.stderr)\nout('entry-syntax', 'ok')\n\nconst constants = await import(pathToFileURL(path.join(pkgRoot, 'lib', 'constants.js')).href)\nout('SERVER_PATH', constants.SERVER_PATH)\nif (!fs.existsSync(constants.SERVER_PATH)) throw new Error('SERVER_PATH does not exist: ' + constants.SERVER_PATH)\nif (!/lib[\\\\/]runtime[\\\\/]server\\.js$/.test(constants.SERVER_PATH)) throw new Error('SERVER_PATH is not lib/runtime/server.js: ' + constants.SERVER_PATH)\n\nconst enginePath = path.join(pkgRoot, 'lib', 'runtime', 'engine.js')\nconst { startFakeAddon } = await import(pathToFileURL(path.join(pkgRoot, 'tests', 'helpers', 'fake-addon.mjs')).href)\nconst addon = await startFakeAddon({})\nprocess.env.DSH_BLENDER_ADDON_PORT = String(addon.port)\nconst engineMod = await import(pathToFileURL(enginePath).href)\nout('engine-exports', Object.keys(engineMod).length)\nconst engine = engineMod.createEngine()\nconst doctor = await engine.doctor()\nout('doctor', { ok: doctor.ok, kind: doctor.addon && doctor.addon.kind, protocol: doctor.addon && doctor.addon.protocol })\nif (!doctor.ok || !doctor.addon || doctor.addon.kind !== 'ok') throw new Error('doctor not ok')\n\nconst fp = engineMod.catalogFingerprint()\nout('fingerprint', fp)\nif (fp.families !== 34 || fp.opsCount !== 228 || fp.commandCatalogKeys !== 39) throw new Error('catalog fingerprint changed')\n\nconst freePort = () => new Promise((resolve, reject) => {\n  const s = net.createServer()\n  s.once('error', reject)\n  s.listen(0, '127.0.0.1', () => { const a = s.address(); const p = a && typeof a === 'object' ? a.port : 0; s.close(() => resolve(p)) })\n})\nconst httpPort = await freePort()\nconst workerPort = await freePort()\nconst workdir = path.join(process.cwd(), 'work')\nfs.mkdirSync(workdir, { recursive: true })\nconst env = { ...process.env, DSH_BLENDER_HTTP_PORT: String(httpPort), DSH_BLENDER_WORKER_PORT: String(workerPort), DSH_BLENDER_WORKDIR: workdir, DSH_BLENDER_ADDON_PORT: String(addon.port) }\nconst server = spawn(process.execPath, [constants.SERVER_PATH], { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })\nlet stdout = ''\nlet stderr = ''\nserver.stdout.on('data', (d) => { stdout += String(d) })\nserver.stderr.on('data', (d) => { stderr += String(d) })\nasync function waitHealth() {\n  const deadline = Date.now() + 20000\n  while (Date.now() < deadline) {\n    try { const r = await fetch('http://127.0.0.1:' + httpPort + '/health'); if (r.ok) return await r.json() } catch { /* not up yet */ }\n    await new Promise((r) => setTimeout(r, 250))\n  }\n  throw new Error('server /health timeout; stderr=' + stderr.slice(-800))\n}\ntry {\n  const health = await waitHealth()\n  out('health', { ok: health.ok, plugin: health.plugin && health.plugin.version })\n  const sr = await fetch('http://127.0.0.1:' + httpPort + '/status')\n  const status = await sr.json()\n  out('status', { version: status.plugin && status.plugin.version, runtimeKeys: status.plugin && status.plugin.runtime && Object.keys(status.plugin.runtime).length })\n  if (!sr.ok) throw new Error('/status failed ' + sr.status)\n} finally {\n  server.kill('SIGTERM')\n  await new Promise((resolve) => {\n    const t = setTimeout(() => { server.kill('SIGKILL'); resolve() }, 8000)\n    server.once('exit', () => { clearTimeout(t); resolve() })\n  })\n  await addon.close()\n}\nout('server-stdout-tail', stdout.slice(-700))\nout('server-stderr', stderr.slice(-400))\n"

function cleanEnv(extra = {}) {
  const env = {}
  for (const [key, value] of Object.entries(process.env)) {
    const lower = key.toLowerCase()
    if (lower.startsWith('npm_config_') || lower.startsWith('npm_lifecycle_')) continue
    env[key] = value
  }
  return { ...env, TEMP: tmp, TMP: tmp, ...extra }
}

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, {
    cwd: ROOT,
    shell: cmd === 'npm',
    env: cleanEnv({ npm_config_cache: path.join(tmp, 'npm-cache'), npm_config_offline: 'true' }),
    encoding: 'utf8',
    maxBuffer: 256 * 1024 * 1024,
    ...opts,
  })
  if (r.status !== 0) {
    const detail = r.error ? String(r.error.message || r.error) : String(r.stderr || r.stdout || '').slice(-1600)
    throw new Error(cmd + ' ' + args.join(' ') + ' failed (' + r.status + '): ' + detail)
  }
  return r.stdout
}

function packMeta(stdout) {
  const start = stdout.search(/[[{]/)
  if (start < 0) throw new Error('npm pack printed no JSON: ' + stdout.slice(-500))
  const parsed = JSON.parse(stdout.slice(start))
  return Array.isArray(parsed) ? parsed[0] : Object.values(parsed)[0]
}

let exitCode = 0
try {
  ensureBuild()
  const packDir = path.join(tmp, 'pack')
  fs.mkdirSync(packDir, { recursive: true })
  const meta = packMeta(run('npm', ['pack', '--json', '--pack-destination', packDir]))
  const files = meta.files.map((f) => f.path)
  console.log('SMOKE pack ' + JSON.stringify({ name: meta.name, version: meta.version, entries: meta.entryCount, size: meta.size }))
  for (const required of ['lib/index.js', 'lib/runtime/server.js', 'lib/runtime/catalog.js', 'addon/dsh_blender_bridge.zip', 'tests/helpers/fake-addon.mjs']) {
    if (!files.includes(required)) throw new Error('tarball missing ' + required)
  }
  if (files.some((f) => f.startsWith('runtime/') && /\.(?:ts|mts)$/.test(f))) throw new Error('tarball ships runtime TypeScript')
  if (files.some((f) => f.startsWith('lib/runtime/') && f.endsWith('.js.map'))) console.log('SMOKE sourcemaps ok')

  const app = path.join(tmp, 'app')
  fs.mkdirSync(app, { recursive: true })
  const tgz = path.join(packDir, meta.filename)
  run('npm', ['install', '--prefix', app, '--no-audit', '--no-fund', '--ignore-scripts', '--legacy-peer-deps', '--offline', tgz])
  const installed = path.join(app, 'node_modules', '@dsh-external', 'dsh-blender-plugin')
  const got = JSON.parse(fs.readFileSync(path.join(installed, 'package.json'), 'utf8'))
  console.log('SMOKE install ' + JSON.stringify({ version: got.version, entry: got.main, files: meta.entryCount }))
  if (fs.existsSync(path.join(installed, 'runtime', 'server.mjs'))) throw new Error('tarball still ships runtime/server.mjs')
  if (fs.existsSync(path.join(installed, 'runtime', 'catalog.mjs'))) throw new Error('tarball still ships runtime/catalog.mjs')
  console.log('SMOKE no-runtime-mjs ok')

  const driver = run(process.execPath, ['--input-type=module', '-e', DRIVER], { cwd: app, env: cleanEnv() })
  console.log(driver.trim())
  if (!driver.includes('SMOKE doctor {"ok":true')) throw new Error('install smoke doctor was not ok')
  if (!driver.includes('SMOKE health')) throw new Error('install smoke never reached /health')
  console.log('SMOKE PASS')
} catch (e) {
  exitCode = 1
  console.error('SMOKE FAIL ' + String((e && e.message) || e))
} finally {
  try {
    fs.rmSync(tmp, { recursive: true, force: true })
  } catch {
    /* best effort */
  }
}
process.exit(exitCode)
