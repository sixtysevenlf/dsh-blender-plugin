#!/usr/bin/env node
/**
 * E2E: a virtual-gui session starts a real Blender through the Windows
 * hidden-desktop provider, waits for the add-on handshake, reports the display,
 * and stops without leaving a process or a half session behind (3.1 tasks 4.4/4.9).
 *
 * Windows only. Requires a Blender 4.2.x executable (--blender, DSH_BLENDER_EXE,
 * or the documented fallback), a built lib/, the committed add-on zip, and a
 * built vendor/win-hidden-desktop helper. The add-on is installed into a
 * throw-away Blender user directory, exactly as Extensions -> Install from Disk.
 *
 * Usage:
 *   node tests/e2e/virtual_gui_session.mjs [--blender <blender.exe>] [--json <path>]
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { ensureBuild } from '../../scripts/ensure-build.mjs'

const root = path.resolve(import.meta.dirname, '..', '..')
const argv = process.argv.slice(2)
const option = (name) => {
  const index = argv.indexOf(name)
  return index >= 0 ? argv[index + 1] : ''
}
const zip = path.join(root, 'extension', 'dsh_blender_bridge.zip')
const exe = path.resolve(
  option('--blender') ||
    process.env.DSH_BLENDER_EXE ||
    'D:/blender-medium/blender-4.2.10-windows-x64/blender.exe',
)
const jsonPath = option('--json') ? path.resolve(option('--json')) : ''
const workDir =
  process.env.DSH_BLENDER_WORKDIR || path.join(os.tmpdir(), 'dsh-vgui-e2e-' + String(process.pid))
const userScripts = path.join(workDir, 'user_scripts')
const userExtensions = path.join(workDir, 'user_extensions')
const userConfig = path.join(workDir, 'user_config')
const tempDir = path.join(workDir, 'tmp')

const checks = []
const record = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail: detail || '' })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ': ' + detail : ''))
}

if (process.platform !== 'win32') {
  console.log('SKIP virtual-gui session E2E: Windows only')
  process.exit(0)
}
if (!existsSync(exe)) {
  console.log('SKIP virtual-gui session E2E: no Blender executable at ' + exe)
  process.exit(0)
}
if (!existsSync(zip)) {
  console.log('SKIP virtual-gui session E2E: add-on zip missing (run npm run build:extension)')
  process.exit(0)
}

rmSync(workDir, { recursive: true, force: true })
for (const dir of [userScripts, userExtensions, userConfig, tempDir]) {
  mkdirSync(dir, { recursive: true })
}
const blenderEnv = {
  ...process.env,
  BLENDER_USER_SCRIPTS: userScripts,
  BLENDER_USER_EXTENSIONS: userExtensions,
  BLENDER_USER_CONFIG: userConfig,
  TEMP: tempDir,
  TMP: tempDir,
  PYTHONIOENCODING: 'utf-8',
  PYTHONUTF8: '1',
}

function runBlender(args) {
  return execFileSync(exe, args, {
    cwd: root,
    env: blenderEnv,
    stdio: 'ignore',
    timeout: 240000,
  })
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port
      server.close(() => resolve(port))
    })
  })
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitFor(fn, timeoutMs, intervalMs = 250) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const value = await fn()
      if (value) return value
    } catch (e) {
      /* keep polling */
    }
    await sleep(intervalMs)
  }
  return null
}

function blenderPids() {
  try {
    const out = execFileSync('tasklist', ['/FI', 'IMAGENAME eq blender.exe', '/FO', 'CSV', '/NH'], {
      encoding: 'utf8',
    })
    return out
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.startsWith('"'))
      .map((line) => Number(line.split('","')[1]))
      .filter((pid) => Number.isFinite(pid) && pid > 0)
  } catch (e) {
    return []
  }
}

// Install the committed add-on the same way a user does, then persist it in the
// throw-away preferences so a normal GUI launch enables it and auto-connects.
runBlender(['--command', 'extension', 'install-file', '-r', 'user_default', '-e', zip])
const extensionDir = path.join(userExtensions, 'user_default', 'dsh_blender_bridge')
record(
  'installed the committed add-on as a Blender extension',
  existsSync(path.join(extensionDir, '__init__.py')),
  extensionDir,
)
if (!existsSync(path.join(extensionDir, '__init__.py'))) {
  console.log('SKIP virtual-gui session E2E: add-on install did not produce ' + extensionDir)
  process.exit(0)
}
runBlender([
  '--background',
  '--factory-startup',
  '--python-expr',
  'import addon_utils, bpy; addon_utils.enable("bl_ext.user_default.dsh_blender_bridge", default_set=True, persistent=True); bpy.ops.wm.save_userpref()',
])
record(
  'persisted the enabled add-on in throw-away user preferences',
  existsSync(path.join(userConfig, 'userpref.blend')),
  userConfig,
)

const port = await freePort()
process.env.DSH_BLENDER_GATEWAY_PORT = String(port)
process.env.DSH_BLENDER_VIRTUAL_GUI = 'on'
process.env.DSH_BLENDER_EXE = exe
process.env.DSH_BLENDER_WORKDIR = workDir
process.env.BLENDER_USER_SCRIPTS = userScripts
process.env.BLENDER_USER_EXTENSIONS = userExtensions
process.env.BLENDER_USER_CONFIG = userConfig
process.env.TEMP = tempDir
process.env.TMP = tempDir

ensureBuild()
const { CFG } = await import('../../lib/runtime/config.js')
const { createGatewayApp } = await import('../../lib/runtime/gateway/app.js')
const { SessionRegistry } = await import('../../lib/runtime/core/sessions/registry.js')
const { WinHiddenDesktopProvider } = await import('../../lib/runtime/display/win-hidden-desktop.js')

record(
  'the Gateway port follows DSH_BLENDER_GATEWAY_PORT',
  CFG.gatewayPort === port,
  String(port) + ' / ' + String(CFG.gatewayPort),
)

const app = createGatewayApp({ port })
const caller = {
  hostId: 'e2e-host',
  dshSessionId: 'e2e-dsh-session',
  agentId: 'e2e-agent',
  role: 'main',
  displayName: 'E2E',
}
let session = null
let stopped = false
const startedAt = Date.now()
try {
  await new Promise((resolve, reject) => {
    app.server.once('error', reject)
    app.server.listen(port, '127.0.0.1', () => resolve(true))
  })
  record('Gateway listens on the chosen loopback port', true, '127.0.0.1:' + String(port))
  const registry = app.sessionRegistry
  const capability = registry.display()
  record(
    'virtual-gui capabilities are ready on this machine',
    capability.ready === true,
    'provider=' + String(capability.provider) + ' missing=' + JSON.stringify(capability.missing),
  )

  const started = await registry.start(caller)
  record(
    'virtual-gui start returned a ready session',
    started.ok === true && !!started.session,
    started.ok ? '' : String(started.code || 'start_failed'),
  )
  if (!started.ok) throw new Error('start failed: ' + String(started.code || 'unknown'))
  session = started.session

  const instanceId = String(session.instanceId || '')
  const display = session.display || null
  record(
    'session id is virtual-gui:<instanceId>',
    String(session.id) === 'virtual-gui:' + instanceId,
    String(session.id),
  )
  record(
    'session kind and status are virtual-gui/ready',
    session.kind === 'virtual-gui' && session.status === 'ready',
    String(session.kind) + ' / ' + String(session.status),
  )
  record(
    'session records the display provider and pid',
    !!display && display.provider === 'win-hidden-desktop' && Number(display.pid) > 0,
    JSON.stringify(display),
  )

  const instance = await waitFor(
    () => app.addonGateway.list().find((x) => x.instanceId === instanceId && x.ready),
    15000,
  )
  record(
    'the spawned add-on handshakes with the exact instanceId',
    !!instance,
    instanceId + ' / instances=' + JSON.stringify(app.addonGateway.list().map((x) => x.instanceId)),
  )
  record(
    'the handshake reports the virtual-gui kind',
    !!instance && instance.kind === 'virtual-gui',
    instance ? String(instance.kind) : '',
  )
  const blenderAlive = blenderPids().includes(Number(display.pid))
  record('the reported Blender pid is running', blenderAlive, String(display.pid))

  stopped = true
  const stop = await registry.stop(caller, String(session.id))
  const sessionGone = registry.getSession(String(session.id)) === null
  record(
    'stop reports stopped and removes the session',
    stop.ok === true && stop.stopped === true && sessionGone,
    JSON.stringify(stop),
  )
  const disconnected = await waitFor(() => {
    const row = app.addonGateway.list().find((x) => x.instanceId === instanceId)
    return !row || row.ready === false
  }, 15000)
  record('the add-on connection is no longer ready after stop', !!disconnected, instanceId)
  const pidGone = await waitFor(() => !blenderPids().includes(Number(display.pid)), 15000)
  record('no Blender process is left behind', !!pidGone, String(display.pid))

  const again = await registry.stop(caller, String(session.id))
  record(
    'a second stop is idempotent',
    again.ok === true && again.stopped === false,
    JSON.stringify(again),
  )

  // Negative path: a provider that cannot find its helper must fail closed and
  // leave no session and no process behind (4.9).
  const readyCapability = registry.display()
  const badRegistry = new SessionRegistry(() => [], {
    display: () => readyCapability,
    selectProvider: () => new WinHiddenDesktopProvider({ helperPath: null, blenderExe: exe }),
    waitForInstance: async () => false,
    limits: () => ({
      limit: 4,
      configured: 4,
      low_resource: false,
      reason: 'configured',
      total_mem_bytes: 0,
      free_mem_bytes: 0,
    }),
  })
  const badStart = await badRegistry.start(caller)
  record(
    'a missing helper fails closed as helper_missing',
    badStart.ok === false && String(badStart.code) === 'helper_missing',
    String(badStart.code) + ' stage=' + String(badStart.stage),
  )
  record(
    'the failed start registers no session',
    badRegistry.virtualGuiCount() === 0 && badRegistry.findForCaller(caller) === null,
    String(badRegistry.virtualGuiCount()),
  )

  const elapsedMs = Date.now() - startedAt
  console.log(
    'virtual-gui E2E: ' +
      String(checks.filter((c) => c.ok).length) +
      '/' +
      String(checks.length) +
      ' checks passed in ' +
      String(elapsedMs) +
      'ms',
  )
  if (jsonPath) {
    mkdirSync(path.dirname(jsonPath), { recursive: true })
    writeFileSync(
      jsonPath,
      JSON.stringify(
        { generatedAt: new Date().toISOString(), checks, session, elapsedMs },
        null,
        2,
      ) + '\n',
    )
  }
  if (checks.some((c) => !c.ok)) process.exitCode = 1
} catch (error) {
  record(
    'the E2E completed without an unexpected error',
    false,
    String(error && error.message ? error.message : error),
  )
  if (session) {
    try {
      if (!stopped) await app.sessionRegistry.stop(caller, String(session.id))
    } catch (e) {
      /* best effort */
    }
  }
  process.exitCode = 1
} finally {
  app.close()
}
