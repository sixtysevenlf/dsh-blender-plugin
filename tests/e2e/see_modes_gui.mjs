#!/usr/bin/env node
/**
 * E2E: blender_see modes against real GUI Blender (4.6).
 *
 * Starts a real Gateway, installs the committed extension zip into a throw-away
 * user extension repository, opens GUI Blender, attaches the main agent, then
 * captures with `mode=current` and `mode=camera` through the real /view route.
 *
 * Checks:
 *   - current and camera both return a PNG plus the 3.1 x-dsh-view fields;
 *   - camera uses the evaluated scene camera, the render aspect ratio, and
 *     reports its name/frame/view matrix;
 *   - neither mode moves the user's 3D view, the active scene camera, or the
 *     current frame;
 *   - output image is a real PNG (magic bytes), not an empty response.
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
  path.join(os.tmpdir(), 'dsh-see-modes-e2e-' + String(process.pid))
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
  console.log('SKIP see modes E2E: no DSH_BLENDER_EXE / discovered blender executable')
  process.exit(0)
}
if (!existsSync(zip)) {
  console.log('SKIP see modes E2E: extension zip missing (run npm run build:extension)')
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

const headers = {
  'x-dsh-host-id': 'host-e2e',
  'x-dsh-session-id': 'dsh-e2e',
  'x-dsh-agent-id': 'agent-main',
  'x-dsh-agent-role': 'main',
  'content-type': 'application/json',
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
    'print("SEE_READY " + json.dumps(result))',
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
  const addon = body && body.readiness && body.readiness.addon
  return addon && addon.ready_instances === 1 ? body : null
}, 60000)
check(
  'Gateway sees exactly one ready add-on instance',
  !!status,
  status ? String(status.readiness.addon.instanceId) : 'timeout',
)

async function postJson(route, body) {
  const response = await fetch(base + route, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  return { status: response.status, json: await response.json() }
}

const attach = await postJson('/lease', { action: 'attach' })
check(
  'the main agent attaches the user-gui session',
  attach.status === 200,
  JSON.stringify(attach.json).slice(0, 200),
)

async function act(code) {
  const response = await fetch(base + '/act', {
    method: 'POST',
    headers,
    body: JSON.stringify({ code, timeoutMs: 30000 }),
  })
  return await response.json()
}

const VIEW_STATE = [
  'import bpy, json',
  'state = {"camera": None, "frame": None, "view": None, "screen": False}',
  'scene = bpy.context.scene',
  'state["camera"] = scene.camera.name if scene.camera else None',
  'state["frame"] = int(scene.frame_current)',
  'for area in bpy.context.screen.areas if bpy.context.screen else []:',
  '    if area.type == "VIEW_3D":',
  '        state["screen"] = True',
  '        r3d = area.spaces.active.region_3d',
  '        state["view"] = [[round(float(v), 6) for v in row] for row in r3d.view_matrix]',
  'print("VIEWSTATE " + json.dumps(state))',
].join('\n')

function viewStateOf(result) {
  const line = String(result.stdout || '')
    .split(/\r?\n/)
    .find((row) => row.startsWith('VIEWSTATE '))
  return line ? JSON.parse(line.slice('VIEWSTATE '.length)) : null
}

const before = viewStateOf(await act(VIEW_STATE))
check(
  'read the user view state through the GUI session',
  !!before && before.camera === 'Camera',
  before ? JSON.stringify({ camera: before.camera, frame: before.frame }) : 'no state',
)

async function capture(body) {
  const response = await fetch(base + '/view', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  const bytes = Buffer.from(await response.arrayBuffer())
  const header = response.headers.get('x-dsh-view')
  const errorBody = response.status === 200 ? null : bytes.toString('utf8').slice(0, 500)
  let meta = null
  try {
    meta = header ? JSON.parse(decodeURIComponent(header)) : null
  } catch {
    meta = null
  }
  return { status: response.status, bytes, meta, error: errorBody }
}

const current = await capture({ mode: 'current', width: 1024, height: 576 })
check(
  'mode=current returns a PNG with the 16:9 tier',
  current.status === 200 &&
    current.bytes.subarray(0, 4).toString('hex') === '89504e47' &&
    current.meta &&
    current.meta.mode === 'current' &&
    current.meta.width === 1024 &&
    current.meta.height === 576,
  current.meta
    ? JSON.stringify({
        mode: current.meta.mode,
        width: current.meta.width,
        height: current.meta.height,
        bytes: current.bytes.length,
      })
    : 'HTTP ' + current.status,
)
check(
  'mode=current reports the live view matrix',
  !!current.meta &&
    Array.isArray(current.meta.view_matrix) &&
    current.meta.view_matrix.length === 4 &&
    current.meta.view_matrix[0].length === 4,
  current.meta && current.meta.view_matrix
    ? current.meta.view_matrix.length + 'x' + current.meta.view_matrix[0].length
    : 'missing',
)

const camera = await capture({ mode: 'camera', max_size: 1024 })
const ratio = camera.meta ? (camera.meta.width * 9) / (camera.meta.height * 16) : 0
check(
  'mode=camera returns a PNG at the render aspect ratio',
  camera.status === 200 &&
    camera.bytes.subarray(0, 4).toString('hex') === '89504e47' &&
    camera.meta &&
    camera.meta.mode === 'camera' &&
    Math.abs(ratio - 1) < 0.02,
  camera.meta
    ? JSON.stringify({
        mode: camera.meta.mode,
        camera: camera.meta.camera,
        frame: camera.meta.frame,
        width: camera.meta.width,
        height: camera.meta.height,
      })
    : 'HTTP ' + camera.status + ' ' + String(camera.error || ''),
)
check(
  'mode=camera names the evaluated scene camera and frame',
  !!camera.meta && camera.meta.camera === 'Camera' && camera.meta.frame === 1,
  camera.meta
    ? JSON.stringify({ camera: camera.meta.camera, frame: camera.meta.frame })
    : 'missing',
)
check(
  'mode=camera reports a view matrix and preview flags',
  !!camera.meta &&
    Array.isArray(camera.meta.view_matrix) &&
    Array.isArray(camera.meta.view_matrix[0]) &&
    typeof camera.meta.preview_only === 'boolean',
  camera.meta
    ? JSON.stringify({
        preview_only: camera.meta.preview_only,
        dof_ignored: camera.meta.dof_ignored,
      })
    : 'missing',
)

const named = await capture({ mode: 'camera', camera: 'Camera', max_size: 1024 })
check(
  'mode=camera accepts a named camera object',
  named.status === 200 && !!named.meta && named.meta.camera === 'Camera',
  named.meta ? String(named.meta.camera) : 'HTTP ' + named.status + ' ' + String(named.error || ''),
)

const makeCam2 = await act(
  [
    'import bpy',
    "data = bpy.data.cameras.new('Cam2')",
    "obj = bpy.data.objects.new('Cam2', data)",
    'bpy.context.scene.collection.objects.link(obj)',
    'obj.location = (3.0, 3.0, 3.0)',
    'print("CAM2_READY")',
  ].join('\n'),
)
check(
  'created a second camera for the named-camera check',
  String(makeCam2.stdout || '').includes('CAM2_READY'),
  String(makeCam2.stdout || '')
    .trim()
    .slice(0, 120),
)
const cam2 = await capture({ mode: 'camera', camera: 'Cam2', max_size: 768 })
check(
  'mode=camera selects the requested camera by name',
  cam2.status === 200 &&
    !!cam2.meta &&
    cam2.meta.camera === 'Cam2' &&
    Math.max(cam2.meta.width, cam2.meta.height) === 768,
  cam2.meta
    ? JSON.stringify({ camera: cam2.meta.camera, width: cam2.meta.width, height: cam2.meta.height })
    : 'HTTP ' + cam2.status + ' ' + String(cam2.error || ''),
)

const panoSet = await act(
  ['import bpy', "bpy.context.scene.camera.data.type = 'PANO'", 'print("PANO_SET")'].join('\n'),
)
check(
  'switched the active camera to PANO through the GUI session',
  String(panoSet.stdout || '').includes('PANO_SET'),
  String(panoSet.stdout || '')
    .trim()
    .slice(0, 120),
)
const pano = await capture({ mode: 'camera', max_size: 1024 })
check(
  'mode=camera reports PANO as unsupported and points at blender_run',
  pano.status === 502 &&
    String(pano.error || '').includes('PANO') &&
    String(pano.error || '').includes('blender_run'),
  'HTTP ' + pano.status + ' ' + String(pano.error || '').slice(0, 160),
)
const panoRestore = await act(
  [
    'import bpy',
    "bpy.context.scene.camera.data.type = 'PERSP'",
    "bpy.data.objects.remove(bpy.data.objects['Cam2'], do_unlink=True)",
    'print("RESTORED")',
  ].join('\n'),
)
check(
  'restored the PERSP camera and removed the helper camera',
  String(panoRestore.stdout || '').includes('RESTORED'),
  String(panoRestore.stdout || '')
    .trim()
    .slice(0, 120),
)

const after = viewStateOf(await act(VIEW_STATE))
check(
  'captures left the user view, scene camera, and current frame untouched',
  !!before &&
    !!after &&
    JSON.stringify(before.view) === JSON.stringify(after.view) &&
    before.camera === after.camera &&
    before.frame === after.frame,
  after
    ? JSON.stringify({
        camera: after.camera,
        frame: after.frame,
        sameView: JSON.stringify(before && before.view) === JSON.stringify(after.view),
      })
    : 'no state',
)

if (blenderPid) {
  try {
    execFileSync('taskkill', ['/PID', String(blenderPid), '/T', '/F'], { stdio: 'ignore' })
  } catch {
    /* already gone */
  }
}
await waitFor(async () => {
  const response = await fetch(base + '/status')
  const body = await response.json()
  const addon = body && body.readiness && body.readiness.addon
  return addon && addon.ready_instances === 0 ? true : null
}, 20000)
gateway.kill('SIGTERM')

const failed = checks.filter((c) => !c.ok)
const report = {
  ok: failed.length === 0,
  checks,
  gatewayPort,
  blenderExe: exe,
  blenderPid,
  artifacts: {
    current: path.join(workDir, 'current.png'),
    camera: path.join(workDir, 'camera.png'),
  },
  logs: { gateway: gatewayLog.slice(-4000), blender: blenderLog.slice(-4000) },
}
writeFileSync(path.join(workDir, 'see-modes-report.json'), JSON.stringify(report, null, 2), 'utf8')
writeFileSync(report.artifacts.current, current.bytes)
writeFileSync(report.artifacts.camera, camera.bytes)
console.log(JSON.stringify(report))
if (failed.length) process.exitCode = 1
