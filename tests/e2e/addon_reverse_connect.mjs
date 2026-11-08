#!/usr/bin/env node
/**
 * E2E: the add-on dials the Gateway and owns no inbound listener (2.2/2.9).
 *
 * Starts a real Gateway, installs the committed add-on zip as a Blender
 * extension in a throw-away user extension repository, opens GUI Blender,
 * presses the real Connect operator through Python, then checks by PID that the
 * Blender process has no LISTENING TCP socket (and does have an outbound
 * connection to the Gateway).
 *
 * Run with DSH_BLENDER_EXE pointing at Blender 4.2.x. Without Blender the
 * script prints SKIP and exits 0.
 */
import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { ensureBuild } from '../../scripts/ensure-build.mjs'

const root = path.resolve(import.meta.dirname, '..', '..')
const workDir =
  process.env.DSH_BLENDER_WORKDIR ||
  path.join(os.tmpdir(), 'dsh-reverse-connect-e2e-' + String(process.pid))
const zip = path.join(root, 'extension', 'dsh_blender_bridge.zip')
const userScripts = path.join(workDir, 'user_scripts')
const userExtensions = path.join(workDir, 'user_extensions')
const userConfig = path.join(workDir, 'user_config')
const tempDir = path.join(workDir, 'tmp')
const probePath = path.join(workDir, 'probe.py')
const probeResultPath = path.join(workDir, 'probe-result.json')
rmSync(workDir, { recursive: true, force: true })
for (const dir of [userScripts, userExtensions, userConfig, tempDir]) {
  mkdirSync(dir, { recursive: true })
}

ensureBuild()
const { CFG } = await import('../../lib/runtime/config.js')
const exe = process.env.DSH_BLENDER_EXE || CFG.blenderExe
if (!exe || !existsSync(exe)) {
  console.log('SKIP reverse-connect E2E: no DSH_BLENDER_EXE / discovered blender executable')
  process.exit(0)
}
if (!existsSync(zip)) {
  console.log('SKIP reverse-connect E2E: extension zip missing (run npm run build:extension)')
  process.exit(0)
}

const checks = []
const check = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ' · ' + detail : ''))
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
    } catch {
      /* keep polling */
    }
    await sleep(intervalMs)
  }
  return null
}

function listenSocketsForPid(pid) {
  if (process.platform !== 'win32') return null
  const out = execFileSync('netstat', ['-ano', '-p', 'tcp'], { encoding: 'utf8' })
  return out
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith('TCP'))
    .map((line) => line.split(/\s+/))
    .filter((cols) => Number(cols[cols.length - 1]) === pid)
    .map((cols) => ({
      local: cols[1],
      remote: cols[2],
      state: cols[3],
    }))
}

function establishedTo(pid, gatewayPort) {
  const sockets = listenSocketsForPid(pid) || []
  return sockets.filter(
    (s) => s.state === 'ESTABLISHED' && s.remote.endsWith(':' + String(gatewayPort)),
  )
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

// Install the committed zip through Blender's extension command, exactly as a
// user does with Extensions -> Install from Disk.
execFileSync(exe, ['--command', 'extension', 'install-file', '-r', 'user_default', '-e', zip], {
  cwd: root,
  env: blenderEnv,
  stdio: 'ignore',
})
check(
  'installed committed addon as a Blender extension',
  existsSync(path.join(userExtensions, 'user_default', 'dsh_blender_bridge', '__init__.py')),
)

writeFileSync(
  probePath,
  [
    'import json, os, sys, traceback',
    'import addon_utils',
    'import bpy',
    'args = sys.argv[sys.argv.index("--") + 1:]',
    'out_path = args[0]',
    'result = {"background": bool(bpy.app.background), "enabled": False, "running": False}',
    'try:',
    '    addon_utils.enable("bl_ext.user_default.dsh_blender_bridge", default_set=True, persistent=True)',
    '    result["enabled"] = True',
    '    bpy.ops.dshbb.start_server()',
    '    server = getattr(bpy.types, "dshbb_server", None)',
    '    result["running"] = bool(server and getattr(server, "running", False))',
    '    result["pid"] = os.getpid()',
    'except Exception:',
    '    result["error"] = traceback.format_exc()',
    'with open(out_path, "w", encoding="utf-8") as fh:',
    '    json.dump(result, fh)',
    'print("REVERSE_READY " + json.dumps(result))',
  ].join('\n'),
  'utf8',
)

const gatewayPort = await freePort()
const gateway = spawn(
  process.execPath,
  [path.join(root, 'lib', 'runtime', 'gateway.js'), '--port', String(gatewayPort)],
  {
    cwd: root,
    env: {
      ...process.env,
      DSH_BLENDER_WORKDIR: workDir,
      DSH_BLENDER_GATEWAY_PORT: String(gatewayPort),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
)
let gatewayLog = ''
gateway.stdout?.on('data', (chunk) => (gatewayLog += chunk.toString()))
gateway.stderr?.on('data', (chunk) => (gatewayLog += chunk.toString()))

const base = 'http://127.0.0.1:' + String(gatewayPort)
const health = await waitFor(async () => {
  const response = await fetch(base + '/health')
  return response.ok ? await response.json() : null
}, 15000)
check('Gateway is healthy', !!health && health.ok === true, base)

const blender = spawn(
  exe,
  ['--factory-startup', '--python-exit-code', '1', '--python', probePath, '--', probeResultPath],
  {
    cwd: root,
    env: {
      ...blenderEnv,
      DSHBB_GATEWAY_PORT: String(gatewayPort),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
)
let blenderLog = ''
blender.stdout?.on('data', (chunk) => (blenderLog += chunk.toString()))
blender.stderr?.on('data', (chunk) => (blenderLog += chunk.toString()))
const blenderPid = blender.pid
check('Blender GUI process started', !!blenderPid && blenderPid > 0, String(blenderPid || ''))

const probe = await waitFor(
  () => (existsSync(probeResultPath) ? JSON.parse(readFileSync(probeResultPath, 'utf8')) : null),
  45000,
)
check(
  'add-on enabled and Connect operator ran in GUI Blender',
  !!probe && probe.enabled === true && probe.running === true && probe.background === false,
  probe ? JSON.stringify(probe) : 'no probe result',
)

const status = await waitFor(async () => {
  const response = await fetch(base + '/status')
  if (!response.ok) return null
  const body = await response.json()
  const gatewayAddon = body && body.readiness && body.readiness.addon
  return gatewayAddon && gatewayAddon.ready_instances === 1 ? body : null
}, 60000)
check(
  'Gateway sees exactly one ready add-on instance',
  !!status,
  status ? String(status.readiness.addon.instanceId) : 'timeout',
)

const doctor = await waitFor(async () => {
  const response = await fetch(base + '/doctor')
  const body = await response.json()
  return body.ok === true ? body : null
}, 15000)
check(
  'Gateway doctor reports ok with the Gateway protocol',
  !!doctor && doctor.addon && doctor.addon.protocol_from === 'gateway',
  doctor && doctor.addon ? String(doctor.addon.protocol_label || doctor.addon.kind) : 'timeout',
)

if (process.platform === 'win32' && blenderPid) {
  const sockets = listenSocketsForPid(blenderPid) || []
  const listening = sockets.filter((s) => s.state === 'LISTENING' || s.state === 'LISTEN')
  check(
    'Blender PID owns no listening TCP socket',
    listening.length === 0,
    listening.length ? JSON.stringify(listening) : 'no LISTEN lines for PID ' + String(blenderPid),
  )
  const outbound = establishedTo(blenderPid, gatewayPort)
  check(
    'Blender PID has an outbound connection to the Gateway',
    outbound.length > 0,
    outbound.length ? JSON.stringify(outbound) : 'no ESTABLISHED line to gateway port',
  )
} else {
  console.log('SKIP PID socket ownership checks: Windows-only netstat evidence')
}

if (blenderPid) {
  try {
    execFileSync('taskkill', ['/PID', String(blenderPid), '/T', '/F'], { stdio: 'ignore' })
  } catch {
    /* already gone */
  }
}
const closed = await waitFor(async () => {
  const response = await fetch(base + '/status')
  const body = await response.json()
  return body &&
    body.readiness &&
    body.readiness.addon &&
    body.readiness.addon.ready_instances === 0
    ? true
    : null
}, 20000)
check('add-on instance disappears after Blender exits', closed === true)
gateway.kill('SIGTERM')

const failed = checks.filter((c) => !c.ok)
const report = {
  ok: failed.length === 0,
  checks,
  gatewayPort,
  blenderExe: exe,
  blenderPid,
  logs: {
    gateway: gatewayLog.slice(-4000),
    blender: blenderLog.slice(-4000),
  },
}
writeFileSync(
  path.join(workDir, 'reverse-connect-report.json'),
  JSON.stringify(report, null, 2),
  'utf8',
)
console.log(JSON.stringify(report))
if (failed.length) process.exitCode = 1
