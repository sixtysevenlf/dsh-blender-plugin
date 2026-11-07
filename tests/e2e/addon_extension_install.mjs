#!/usr/bin/env node
/**
 * E2E: the committed add-on installs as a Blender 4.2 extension and upgrades a
 * legacy 1.11 install (8.10).
 *
 * Installs the 3.0.6 legacy zip through the old addons directory, proves it
 * reports 1.11 / protocol 11, removes it, installs the committed extension zip
 * with Blender's `--command extension install-file`, proves the enabled module
 * lives under user_default/dsh_blender_bridge with the bl_ext namespace, then
 * opens GUI Blender and checks the Gateway handshake. The old zip install path
 * is not used for the 2.0.0 package.
 *
 * Run with DSH_BLENDER_EXE pointing at Blender 4.2.x. Without Blender the
 * script prints SKIP and exits 0 (release-machine E2E, never a fake pass).
 */
import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import { ensureBuild } from '../../scripts/ensure-build.mjs'

const root = path.resolve(import.meta.dirname, '..', '..')
const workDir =
  process.env.DSH_BLENDER_WORKDIR || path.join(root, '.dsh-ops', 'extension-install-e2e')
const zip = path.join(root, 'addon', 'dsh_blender_bridge.zip')
const userScripts = path.join(workDir, 'user_scripts')
const userExtensions = path.join(workDir, 'user_extensions')
const userConfig = path.join(workDir, 'user_config')
const tempDir = path.join(workDir, 'tmp')
const legacyZip = path.join(workDir, 'legacy-addon.zip')
const legacyAddons = path.join(userScripts, 'addons')
const legacyModule = path.join(legacyAddons, 'dsh_blender_bridge')
const legacyProbePath = path.join(workDir, 'legacy-probe.py')
const extensionProbePath = path.join(workDir, 'extension-probe.py')
const guiProbePath = path.join(workDir, 'gui-probe.py')
const legacyResultPath = path.join(workDir, 'legacy-result.json')
const extensionResultPath = path.join(workDir, 'extension-result.json')
const guiResultPath = path.join(workDir, 'gui-result.json')
rmSync(workDir, { recursive: true, force: true })
for (const dir of [userScripts, userExtensions, userConfig, tempDir, legacyAddons]) {
  mkdirSync(dir, { recursive: true })
}

ensureBuild()
const { CFG } = await import('../../lib/runtime/config.js')
const exe = process.env.DSH_BLENDER_EXE || CFG.blenderExe
if (!exe || !existsSync(exe)) {
  console.log('SKIP extension-install E2E: no DSH_BLENDER_EXE / discovered blender executable')
  process.exit(0)
}
if (!existsSync(zip)) {
  console.log('SKIP extension-install E2E: addon zip missing (run npm run build:addon)')
  process.exit(0)
}

const checks = []
const check = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ' · ' + detail : ''))
}

const blenderEnv = {
  ...process.env,
  BLENDER_USER_CONFIG: userConfig,
  BLENDER_USER_SCRIPTS: userScripts,
  BLENDER_USER_EXTENSIONS: userExtensions,
  TMP: tempDir,
  TEMP: tempDir,
  PYTHONIOENCODING: 'utf-8',
  PYTHONUTF8: '1',
}

function blender(args) {
  return execFileSync(exe, args, {
    cwd: root,
    env: blenderEnv,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

/** Read one of the pre-4.2 legacy release zips from git history. */
function legacyZipFromTag(tag) {
  try {
    const data = execFileSync('git', ['show', tag + ':addon/dsh_blender_bridge.zip'], {
      cwd: root,
      maxBuffer: 32 * 1024 * 1024,
    })
    writeFileSync(legacyZip, data)
    return true
  } catch {
    return false
  }
}

function writeProbe(file, body) {
  writeFileSync(file, body, 'utf8')
}

writeProbe(
  legacyProbePath,
  [
    'import json, os, sys',
    'import addon_utils, bpy',
    'args = sys.argv[sys.argv.index("--") + 1:]',
    'out_path = args[0]',
    'addon_utils.enable("dsh_blender_bridge", default_set=True)',
    'import dsh_blender_bridge as addon',
    'result = {',
    '    "module": "dsh_blender_bridge",',
    '    "file": addon.__file__,',
    '    "version": list(addon.ADDON_VERSION),',
    '    "protocol": addon.ADDON_PROTOCOL_VERSION,',
    '    "has_bl_info": hasattr(addon, "bl_info"),',
    '    "background": bool(bpy.app.background),',
    '}',
    'with open(out_path, "w", encoding="utf-8") as fh:',
    '    json.dump(result, fh)',
    'print("LEGACY_PROBE " + json.dumps(result))',
  ].join('\n'),
)

writeProbe(
  extensionProbePath,
  [
    'import json, os, sys',
    'import addon_utils, bpy',
    'args = sys.argv[sys.argv.index("--") + 1:]',
    'out_path = args[0]',
    'mod = addon_utils.enable("bl_ext.user_default.dsh_blender_bridge", default_set=True)',
    'result = {',
    '    "module": mod.__name__,',
    '    "file": mod.__file__,',
    '    "version": list(mod.ADDON_VERSION),',
    '    "protocol": mod.ADDON_PROTOCOL_VERSION,',
    '    "commands": len(mod.command_registry.COMMAND_NAMES),',
    '    "has_bl_info": hasattr(mod, "bl_info"),',
    '    "has_legacy_port_prop": hasattr(bpy.types.Scene, "dshbb_port"),',
    '    "background": bool(bpy.app.background),',
    '}',
    'with open(out_path, "w", encoding="utf-8") as fh:',
    '    json.dump(result, fh)',
    'print("EXTENSION_PROBE " + json.dumps(result))',
  ].join('\n'),
)

writeProbe(
  guiProbePath,
  [
    'import json, os, sys, traceback',
    'import addon_utils, bpy',
    'args = sys.argv[sys.argv.index("--") + 1:]',
    'out_path = args[0]',
    'result = {"background": bool(bpy.app.background), "enabled": False, "running": False}',
    'try:',
    '    mod = addon_utils.enable("bl_ext.user_default.dsh_blender_bridge", default_set=True)',
    '    result["file"] = mod.__file__',
    '    result["version"] = list(mod.ADDON_VERSION)',
    '    result["protocol"] = mod.ADDON_PROTOCOL_VERSION',
    '    result["enabled"] = True',
    '    bpy.ops.dshbb.start_server()',
    '    server = getattr(bpy.types, "dshbb_server", None)',
    '    result["running"] = bool(server and server.running)',
    'except Exception:',
    '    result["error"] = traceback.format_exc()',
    'with open(out_path, "w", encoding="utf-8") as fh:',
    '    json.dump(result, fh)',
    'print("EXTENSION_GUI_PROBE " + json.dumps(result))',
  ].join('\n'),
)

// 1. A real 3.0.6 legacy install is the upgrade starting point. The current
// commit must keep that old package readable; the new package never enters the
// old addons directory.
const hasLegacy = legacyZipFromTag('v3.0.6')
check(
  'a legacy 1.11 zip is available for the upgrade case',
  hasLegacy,
  hasLegacy ? 'v3.0.6' : 'git tag missing',
)
if (hasLegacy) {
  execFileSync('tar', ['-xf', legacyZip, '-C', legacyAddons], { stdio: 'ignore' })
  check(
    'legacy zip extracts into the old addons directory',
    existsSync(path.join(legacyModule, '__init__.py')),
    legacyModule,
  )
  blender([
    '--background',
    '--factory-startup',
    '--python-exit-code',
    '1',
    '--python',
    legacyProbePath,
    '--',
    legacyResultPath,
  ])
  const legacy = JSON.parse(readFileSync(legacyResultPath, 'utf8'))
  check(
    'legacy package reports 1.11 / protocol 11',
    Array.isArray(legacy.version) &&
      legacy.version[0] === 1 &&
      legacy.version[1] === 11 &&
      legacy.protocol === 11,
    JSON.stringify(legacy),
  )
  check('legacy package carries bl_info metadata', legacy.has_bl_info === true)
  rmSync(legacyModule, { recursive: true, force: true })
}

// 2. Install the committed zip as a Blender 4.2 extension. This is the only
// install path the 2.0.0 package supports.
let installOutput = ''
try {
  installOutput = blender([
    '--command',
    'extension',
    'install-file',
    '-r',
    'user_default',
    '-e',
    zip,
  ])
} catch (error) {
  installOutput = String(error.stderr || error.message || error)
}
check(
  'extension install-file accepted the committed zip',
  /Preferences saved/.test(installOutput),
  installOutput.trim(),
)
check(
  'extension package is on disk under the user extension repository',
  existsSync(
    path.join(userExtensions, 'user_default', 'dsh_blender_bridge', 'blender_manifest.toml'),
  ),
  path.join(userExtensions, 'user_default', 'dsh_blender_bridge'),
)
check(
  'legacy addons directory no longer holds the 2.0.0 package',
  !existsSync(path.join(legacyAddons, 'dsh_blender_bridge')),
  path.join(legacyAddons, 'dsh_blender_bridge'),
)

// 3. Enabling the installed extension resolves the bl_ext namespace and the
// 2.0.0 / protocol 12 surface.
blender([
  '--background',
  '--factory-startup',
  '--python-exit-code',
  '1',
  '--python',
  extensionProbePath,
  '--',
  extensionResultPath,
])
const extension = JSON.parse(readFileSync(extensionResultPath, 'utf8'))
const extensionDir = path.join(userExtensions, 'user_default', 'dsh_blender_bridge') + path.sep
check(
  'enabled module name uses the bl_ext namespace',
  extension.module === 'bl_ext.user_default.dsh_blender_bridge',
  extension.module,
)
check(
  'enabled module file lives in the extension repository',
  path.resolve(extension.file).startsWith(path.resolve(extensionDir)),
  extension.file,
)
check(
  'upgrade lands on 2.0.0 / protocol 12',
  Array.isArray(extension.version) &&
    extension.version[0] === 2 &&
    extension.version[1] === 0 &&
    extension.protocol === 12,
  JSON.stringify(extension),
)
check('extension package has no bl_info', extension.has_bl_info === false)
check('legacy inbound port property is gone', extension.has_legacy_port_prop === false)

// 4. GUI Blender connects to a real Gateway: install + enable + handshake.
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

const blenderProcess = spawn(
  exe,
  ['--factory-startup', '--python-exit-code', '1', '--python', guiProbePath, '--', guiResultPath],
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
blenderProcess.stdout?.on('data', (chunk) => (blenderLog += chunk.toString()))
blenderProcess.stderr?.on('data', (chunk) => (blenderLog += chunk.toString()))
const blenderPid = blenderProcess.pid
check('Blender GUI process started', !!blenderPid && blenderPid > 0, String(blenderPid || ''))

const gui = await waitFor(
  () => (existsSync(guiResultPath) ? JSON.parse(readFileSync(guiResultPath, 'utf8')) : null),
  45000,
)
check(
  'GUI Blender enabled the extension and started the connection',
  !!gui && gui.enabled === true && gui.running === true && gui.background === false,
  gui ? JSON.stringify(gui) : 'no GUI probe result',
)

const status = await waitFor(async () => {
  const response = await fetch(base + '/status')
  if (!response.ok) return null
  const body = await response.json()
  const addon = body && body.readiness && body.readiness.addon
  return addon && addon.ready_instances === 1 ? body : null
}, 60000)
check(
  'Gateway sees exactly one ready extension instance',
  !!status,
  status ? String(status.readiness.addon.instanceId) : 'timeout',
)
const doctor = await waitFor(async () => {
  const response = await fetch(base + '/doctor')
  const body = await response.json()
  return body.ok === true ? body : null
}, 15000)
check(
  'Gateway doctor reports the extension handshake',
  !!doctor && doctor.addon && doctor.addon.protocol_from === 'gateway',
  doctor && doctor.addon ? String(doctor.addon.protocol_label || doctor.addon.kind) : 'timeout',
)

if (blenderPid) {
  try {
    execFileSync('taskkill', ['/PID', String(blenderPid), '/T', '/F'], { stdio: 'ignore' })
  } catch {
    /* already gone */
  }
}
gateway.kill('SIGTERM')

const failed = checks.filter((item) => !item.ok)
const report = {
  ok: failed.length === 0,
  checks,
  gatewayPort,
  blenderExe: exe,
  blenderPid,
  legacyZip: hasLegacy ? 'v3.0.6:addon/dsh_blender_bridge.zip' : null,
  extensionZip: zip,
  gatewayLogTail: gatewayLog.slice(-4000),
  blenderLogTail: blenderLog.slice(-4000),
}
const reportPath = path.join(root, 'local-workspace', '8.10-extension-install.json')
mkdirSync(path.dirname(reportPath), { recursive: true })
writeFileSync(reportPath, JSON.stringify(report, null, 2), 'utf8')
console.log('REPORT ' + reportPath)
if (failed.length > 0) process.exit(1)
