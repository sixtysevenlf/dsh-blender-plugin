#!/usr/bin/env node
/**
 * E2E: real GUI Blender draws agent presence without moving the user's view or
 * touching the scene (4.17).
 *
 * Starts a real Gateway, installs the committed extension zip into a throw-away
 * user extension repository, opens GUI Blender, attaches two agents of one DSH
 * session, pushes two view poses, then reads the add-on snapshot and the live
 * VIEW_3D view matrix back through /act.
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
  path.join(os.tmpdir(), 'dsh-presence-draw-e2e-' + String(process.pid))
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
  console.log('SKIP presence E2E: no DSH_BLENDER_EXE / discovered blender executable')
  process.exit(0)
}
if (!existsSync(zip)) {
  console.log('SKIP presence E2E: extension zip missing (run npm run build:extension)')
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

execFileSync(exe, ['--command', 'extension', 'install-file', '-r', 'user_default', '-e', zip], {
  cwd: root,
  env: blenderEnv,
  stdio: 'ignore',
})

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
    'print("PRESENCE_READY " + json.dumps(result))',
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
    env: { ...blenderEnv, DSHBB_GATEWAY_PORT: String(gatewayPort) },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
)
let blenderLog = ''
blender.stdout?.on('data', (chunk) => (blenderLog += chunk.toString()))
blender.stderr?.on('data', (chunk) => (blenderLog += chunk.toString()))
const blenderPid = blender.pid

const probe = await waitFor(
  () => (existsSync(probeResultPath) ? JSON.parse(readFileSync(probeResultPath, 'utf8')) : null),
  45000,
)
check(
  'add-on enabled and Connect ran in GUI Blender',
  !!probe && probe.enabled === true && probe.running === true && probe.background === false,
  probe ? JSON.stringify(probe) : 'no probe result',
)

const status = await waitFor(async () => {
  const response = await fetch(base + '/status')
  if (!response.ok) return null
  const body = await response.json()
  const addon = body && body.readiness && body.readiness.addon
  return addon && addon.ready_instances === 1 ? body : null
}, 60000)
check('Gateway sees exactly one ready add-on instance', !!status)

const headers = {
  'content-type': 'application/json',
  'x-dsh-host-id': 'e2e-presence',
  'x-dsh-session-id': 'dsh-presence',
  'x-dsh-agent-id': 'agent-main',
  'x-dsh-agent-role': 'main',
}
const subHeaders = {
  ...headers,
  'x-dsh-agent-id': 'agent-sub',
  'x-dsh-agent-role': 'subagent',
  'x-dsh-parent-agent-id': 'agent-main',
}

async function post(route, body, requestHeaders = headers) {
  const response = await fetch(base + route, {
    method: 'POST',
    headers: requestHeaders,
    body: JSON.stringify(body),
  })
  return { status: response.status, body: await response.json() }
}

const attached = await post('/lease', { action: 'attach' })
const sessionId = attached.body && attached.body.session ? String(attached.body.session.id) : ''
check('attaches the GUI Blender session', attached.status === 200 && !!sessionId, sessionId)

async function readAddonState() {
  const response = await post(
    '/act',
    {
      sessionId,
      code: [
        'import bpy, json',
        'from bl_ext.user_default.dsh_blender_bridge import presence_draw',
        'space = next((a.spaces.active for a in bpy.context.screen.areas if a.type == "VIEW_3D"), None)',
        'region = space.region_3d if space else None',
        'view = [round(float(v), 5) for row in region.view_matrix for v in row] if region else None',
        'state = presence_draw.get_presence()',
        'print("PRESENCE " + json.dumps({',
        '    "agents": state["agents"],',
        '    "paused": state["paused"],',
        '    "handles": len(presence_draw._HANDLES),',
        '    "objects": sorted(o.name for o in bpy.data.objects),',
        '    "selected": sorted(o.name for o in bpy.context.selected_objects),',
        '    "view": view,',
        '    "camera": bpy.context.scene.camera.name if bpy.context.scene.camera else None,',
        '    "frame": bpy.context.scene.frame_current,',
        '}))',
      ].join('\n'),
    },
    headers,
  )
  const text = String((response.body && response.body.stdout) || '')
  const line = text.split(/\r?\n/).find((item) => item.startsWith('PRESENCE '))
  if (!line) throw new Error('no PRESENCE line in /act output: ' + text.slice(-800))
  return JSON.parse(line.slice('PRESENCE '.length))
}

const baseline = await readAddonState()
check(
  'starts with no presence handlers and an untouched scene',
  baseline.handles === 0 && baseline.agents.length === 0 && !!baseline.view,
  JSON.stringify({ handles: baseline.handles, objects: baseline.objects.length }),
)

const mainView = await post('/lease', {
  action: 'update_view',
  sessionId,
  view: { viewpoint: 'viewport', from: [6, -6, 4], lookAt: [0, 0, 0] },
})
check(
  'publishes the main agent view pose',
  mainView.status === 200 && !!mainView.body.presence && mainView.body.presence.length === 1,
)

const attachedSub = await post('/lease', { action: 'attach' }, subHeaders)
check('attaches a second agent of the same DSH session', attachedSub.status === 200)
const subView = await post(
  '/lease',
  {
    action: 'update_view',
    sessionId,
    view: { viewpoint: 'viewport', from: [-5, 5, 3], lookAt: [0, 0, 0] },
  },
  subHeaders,
)
check(
  'publishes the second agent view pose',
  subView.status === 200 && subView.body.presence.length === 2,
)

const [frameA, frameB] = await Promise.all([
  fetch(base + '/frame.png?size=320'),
  fetch(base + '/frame.png?size=320'),
])
const [bytesA, bytesB] = await Promise.all([
  frameA.arrayBuffer().then((buffer) => Buffer.from(buffer)),
  frameB.arrayBuffer().then((buffer) => Buffer.from(buffer)),
])
check(
  'two agents can capture the GUI viewport concurrently',
  frameA.status === 200 &&
    frameB.status === 200 &&
    frameA.headers.get('content-type') === 'image/png' &&
    bytesA.subarray(1, 4).toString('ascii') === 'PNG' &&
    bytesB.subarray(1, 4).toString('ascii') === 'PNG',
  JSON.stringify({ a: bytesA.length, b: bytesB.length }),
)

const selected = await post('/lease', {
  action: 'update_selection',
  sessionId,
  selected: ['object:Cube'],
  activity: 'modeling',
})
check('publishes the main agent selection', selected.status === 200)

await waitFor(async () => {
  const state = await readAddonState()
  return state.handles === 2 && state.agents.length === 2 ? state : null
}, 15000)

const after = await readAddonState()
const names = after.agents.map((agent) => agent.displayName)
const colors = after.agents.map((agent) => agent.color)
const views = after.agents.map((agent) => agent.view && agent.view.from)
check(
  'add-on holds two independent agents with distinct colours and view poses',
  names.length === 2 &&
    new Set(names).size === 2 &&
    new Set(colors).size === 2 &&
    JSON.stringify(views[0]) !== JSON.stringify(views[1]),
  JSON.stringify({ names, colors, views }),
)
check(
  'view handlers are installed without moving the user view or camera',
  after.handles === 2 &&
    JSON.stringify(after.view) === JSON.stringify(baseline.view) &&
    after.camera === baseline.camera &&
    after.frame === baseline.frame,
  JSON.stringify({
    handles: after.handles,
    viewChanged: JSON.stringify(after.view) !== JSON.stringify(baseline.view),
  }),
)
check(
  'presence drawing does not add/remove objects or change the selection',
  JSON.stringify(after.objects) === JSON.stringify(baseline.objects) &&
    JSON.stringify(after.selected) === JSON.stringify(baseline.selected),
  JSON.stringify({ objects: after.objects, selected: after.selected }),
)
const mainAgent = after.agents.find((agent) => agent.displayName.includes('main'))
check(
  'the main agent selection is visible in the presence snapshot',
  !!mainAgent && mainAgent.selected.includes('object:Cube'),
  JSON.stringify(mainAgent ? mainAgent.selected : null),
)

const cleared = await post(
  '/act',
  {
    sessionId,
    code: 'from bl_ext.user_default.dsh_blender_bridge import presence_draw; presence_draw.clear_presence(); print("CLEARED")',
  },
  headers,
)
const clearedState = await waitFor(async () => {
  const state = await readAddonState()
  return state.handles === 0 && state.agents.length === 0 ? state : null
}, 10000)
check(
  'clearing presence removes both draw handlers',
  cleared.status === 200 && !!clearedState,
  JSON.stringify(clearedState ? { handles: clearedState.handles } : null),
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
  logs: { gateway: gatewayLog.slice(-4000), blender: blenderLog.slice(-4000) },
}
writeFileSync(path.join(workDir, 'presence-report.json'), JSON.stringify(report, null, 2), 'utf8')
console.log(JSON.stringify(report))
if (failed.length) process.exitCode = 1
