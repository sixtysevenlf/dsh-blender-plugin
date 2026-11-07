#!/usr/bin/env node
/**
 * Clean-install smoke for the packaged plugin.
 *
 *   npm run build            -> lib/index.js + compiled lib/runtime JS (prepack too)
 *   npm pack                 -> tarball in a temp dir
 *   npm install --offline    -> install that tarball into an empty prefix
 *   node driver              -> entry metadata + syntax, GATEWAY_PATH resolves to
 *                               lib/runtime/gateway.js, then boot the packaged
 *                               lib/runtime/gateway.js, connect a packaged fake
 *                               WS addon, and assert /health, /status, and
 *                               /doctor agree on a ready protocol-12 instance.
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

const DRIVER = `
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const pkgRoot = path.join(process.cwd(), 'node_modules', 'dsh-blender-plugin')
const out = (label, value) =>
  console.log('SMOKE ' + label + ' ' + (typeof value === 'string' ? value : JSON.stringify(value)))

const pkg = JSON.parse(fs.readFileSync(path.join(pkgRoot, 'package.json'), 'utf8'))
out('package-entry', { main: pkg.main, exports: Object.keys(pkg.exports || {}) })
const syntax = spawnSync(process.execPath, ['--check', path.join(pkgRoot, 'lib', 'index.js')], {
  encoding: 'utf8',
})
if (syntax.status !== 0) throw new Error('node --check lib/index.js failed: ' + syntax.stderr)
out('entry-syntax', 'ok')

const constants = await import(pathToFileURL(path.join(pkgRoot, 'lib', 'constants.js')).href)
out('GATEWAY_PATH', constants.GATEWAY_PATH)
if (!fs.existsSync(constants.GATEWAY_PATH))
  throw new Error('GATEWAY_PATH does not exist: ' + constants.GATEWAY_PATH)
if (!/lib[\\\\/]runtime[\\\\/]gateway\\.js$/.test(constants.GATEWAY_PATH))
  throw new Error('GATEWAY_PATH is not lib/runtime/gateway.js: ' + constants.GATEWAY_PATH)

const { startFakeWsAddon } = await import(
  pathToFileURL(path.join(pkgRoot, 'tests', 'helpers', 'fake-ws-addon.mjs')).href
)

const freePort = () =>
  new Promise((resolve, reject) => {
    const s = net.createServer()
    s.once('error', reject)
    s.listen(0, '127.0.0.1', () => {
      const address = s.address()
      const port = address && typeof address === 'object' ? address.port : 0
      s.close(() => resolve(port))
    })
  })

const gatewayPort = await freePort()
const workdir = path.join(process.cwd(), 'work')
fs.mkdirSync(workdir, { recursive: true })
const env = {
  ...process.env,
  DSH_BLENDER_GATEWAY_PORT: String(gatewayPort),
  DSH_BLENDER_WORKDIR: workdir,
}
const gateway = spawn(process.execPath, [constants.GATEWAY_PATH, '--port', String(gatewayPort)], {
  cwd: process.cwd(),
  env,
  stdio: ['ignore', 'pipe', 'pipe'],
  windowsHide: true,
})
let stdout = ''
let stderr = ''
gateway.stdout.on('data', (d) => {
  stdout += String(d)
})
gateway.stderr.on('data', (d) => {
  stderr += String(d)
})
const waitHealth = async () => {
  const deadline = Date.now() + 45000
  while (Date.now() < deadline) {
    try {
      const r = await fetch('http://127.0.0.1:' + gatewayPort + '/health')
      if (r.ok) return await r.json()
    } catch (e) {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error('gateway /health timeout; stderr=' + stderr.slice(-800))
}
let addon = null
try {
  const health = await waitHealth()
  out('health', { ok: health.ok, plugin: health.plugin && health.plugin.version })

  addon = await startFakeWsAddon({ gatewayPort })
  await addon.ready

  const statusResponse = await fetch('http://127.0.0.1:' + gatewayPort + '/status')
  const status = await statusResponse.json()
  out('status', {
    version: status.plugin && status.plugin.version,
    runtimeKeys: status.plugin && status.plugin.runtime && Object.keys(status.plugin.runtime).length,
    addonError: status.addonError,
  })
  if (!statusResponse.ok) throw new Error('/status failed ' + statusResponse.status)
  if (status.addonError) throw new Error('/status addonError: ' + status.addonError)

  const doctorResponse = await fetch('http://127.0.0.1:' + gatewayPort + '/doctor')
  const doctor = await doctorResponse.json()
  out('doctor', {
    ok: doctor.ok,
    kind: doctor.addon && doctor.addon.kind,
    connected: doctor.addon && doctor.addon.connected,
    protocol: doctor.addon && doctor.addon.protocol,
  })
  if (!doctor.ok || !doctor.addon || doctor.addon.kind !== 'ok')
    throw new Error('doctor not ok')
  if (doctor.addon.protocol !== 12) throw new Error('doctor protocol is not 12')
  if (doctor.addon.connected !== true) throw new Error('doctor did not report a connected addon')
} finally {
  if (addon) await addon.close()
  gateway.kill('SIGTERM')
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      gateway.kill('SIGKILL')
      resolve()
    }, 8000)
    gateway.once('exit', () => {
      clearTimeout(timer)
      resolve()
    })
  })
}
out('gateway-stdout-tail', stdout.slice(-700))
out('gateway-stderr', stderr.slice(-400))
`

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
    const detail = r.error
      ? String(r.error.message || r.error)
      : String(r.stderr || r.stdout || '').slice(-1600)
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
  console.log(
    'SMOKE pack ' +
      JSON.stringify({
        name: meta.name,
        version: meta.version,
        entries: meta.entryCount,
        size: meta.size,
      }),
  )
  for (const required of [
    'lib/index.js',
    'lib/runtime/gateway.js',
    'lib/runtime/catalog/index.js',
    'addon/dsh_blender_bridge.zip',
    'tests/helpers/fake-ws-addon.mjs',
  ]) {
    if (!files.includes(required)) throw new Error('tarball missing ' + required)
  }
  if (files.some((f) => f.startsWith('runtime/') && /\.(?:ts|mts)$/.test(f)))
    throw new Error('tarball ships runtime TypeScript')
  if (files.some((f) => f.startsWith('lib/runtime/') && f.endsWith('.js.map')))
    console.log('SMOKE sourcemaps ok')

  const app = path.join(tmp, 'app')
  fs.mkdirSync(app, { recursive: true })
  const tgz = path.join(packDir, meta.filename)
  run('npm', [
    'install',
    '--prefix',
    app,
    '--no-audit',
    '--no-fund',
    '--ignore-scripts',
    '--legacy-peer-deps',
    '--offline',
    tgz,
  ])
  const installed = path.join(app, 'node_modules', 'dsh-blender-plugin')
  const got = JSON.parse(fs.readFileSync(path.join(installed, 'package.json'), 'utf8'))
  console.log(
    'SMOKE install ' +
      JSON.stringify({ version: got.version, entry: got.main, files: meta.entryCount }),
  )
  if (fs.existsSync(path.join(installed, 'runtime', 'server' + '.mjs')))
    throw new Error('tarball still ships the legacy runtime server entry')
  if (fs.existsSync(path.join(installed, 'runtime', 'catalog.mjs')))
    throw new Error('tarball still ships runtime/catalog.mjs')
  console.log('SMOKE no-runtime-mjs ok')

  const driver = run(process.execPath, ['--input-type=module', '-e', DRIVER], {
    cwd: app,
    env: cleanEnv(),
  })
  console.log(driver.trim())
  if (!driver.includes('SMOKE doctor {"ok":true'))
    throw new Error('install smoke doctor was not ok')
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
