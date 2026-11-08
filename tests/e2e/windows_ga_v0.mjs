#!/usr/bin/env node
/**
 * E2E: Windows virtual-gui GA V0 acceptance (3.1 task 5.5).
 *
 * Runs the full product path on a real Windows desktop: the Gateway starts two
 * virtual-gui sessions through the bundled hidden-desktop helper, both Blender
 * add-ons reverse-connect, and each instance proves:
 *   - hardware OpenGL (renderer is not llvmpipe/software),
 *   - a GPUOffScreen.draw_view3d viewport capture succeeds,
 *   - the two instances are isolated (commands and pixels do not cross),
 *   - stop leaves neither Blender nor helper processes behind.
 *
 * Lock screen and RDP are recorded as scenarios, but this script never locks the
 * interactive session (that would require the human to unlock it). It also
 * records whether the run had administrator rights.
 *
 * Windows only. Requires Blender 4.2.x (--blender, DSH_BLENDER_EXE, or the
 * documented fallback), a built lib/, the committed add-on zip, and a built
 * vendor/win-hidden-desktop helper.
 *
 * Usage:
 *   node tests/e2e/windows_ga_v0.mjs [--blender <blender.exe>] [--json <path>] [--evidence <dir>]
 */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
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
const zip = path.join(root, 'addon', 'dsh_blender_bridge.zip')
const exe = path.resolve(
  option('--blender') ||
    process.env.DSH_BLENDER_EXE ||
    'D:/blender-medium/blender-4.2.10-windows-x64/blender.exe',
)
const jsonPath = option('--json') ? path.resolve(option('--json')) : ''
const evidenceDir = path.resolve(
  option('--evidence') || path.join(root, 'local-workspace', '5.5-windows-ga-v0'),
)
const workDir = path.join(os.tmpdir(), 'dsh-win-ga-' + String(process.pid))
const userScripts = path.join(workDir, 'user_scripts')
const userExtensions = path.join(workDir, 'user_extensions')
const userConfig = path.join(workDir, 'user_config')
const tempDir = path.join(workDir, 'tmp')

const checks = []
const scenarios = []
const record = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail: detail || '' })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ': ' + detail : ''))
}
const scenario = (name, status, detail) => {
  scenarios.push({ name, status, detail: detail || '' })
  console.log('SCENARIO ' + name + ' ' + status + (detail ? ': ' + detail : ''))
}
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex')

if (process.platform !== 'win32') {
  console.log('SKIP Windows GA V0: Windows only')
  process.exit(0)
}
if (!existsSync(exe)) {
  console.log('SKIP Windows GA V0: no Blender executable at ' + exe)
  process.exit(0)
}
if (!existsSync(zip)) {
  console.log('SKIP Windows GA V0: add-on zip missing (run npm run build:addon)')
  process.exit(0)
}

rmSync(workDir, { recursive: true, force: true })
mkdirSync(evidenceDir, { recursive: true })
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

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

function tasklistPids(image) {
  try {
    const out = execFileSync('tasklist', ['/FI', 'IMAGENAME eq ' + image, '/FO', 'CSV', '/NH'], {
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

function runQuiet(command, args) {
  try {
    return execFileSync(command, args, { encoding: 'utf8', timeout: 30000 }).trim()
  } catch (error) {
    const out = error && (error.stdout || error.stderr)
    return out ? String(out).trim() : ''
  }
}

function environmentFacts() {
  const sessionName = process.env.SESSIONNAME || ''
  const elevated = runQuiet('powershell', [
    '-NoProfile',
    '-Command',
    '([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)',
  ])
  const user = runQuiet('whoami', [])
  const sessions = runQuiet('quser', [])
  return {
    sessionName,
    rdp: /^RDP/i.test(sessionName),
    elevated,
    user,
    sessions,
    quser: sessions,
    os: runQuiet('cmd', ['/c', 'ver']),
  }
}

const facts = environmentFacts()
scenario(
  'non-admin console session',
  facts.elevated === 'False' ? 'exercised' : 'not-exercised',
  'user=' + facts.user + ' elevated=' + (facts.elevated || 'unknown'),
)
scenario(
  'RDP session',
  facts.rdp ? 'exercised' : 'not-exercised',
  facts.rdp
    ? 'running under ' + facts.sessionName
    : 'no RDP session available (SESSIONNAME=' + (facts.sessionName || 'unset') + ')',
)
scenario(
  'lock screen',
  'not-exercised',
  'locking the interactive session would require the human to unlock it; hidden desktops are separate from the interactive desktop but a lock/unlock run remains unverified',
)

// Install the committed add-on the way a user does, then persist it so the
// provider-launched Blender enables and auto-connects it.
runBlender(['--command', 'extension', 'install-file', '-r', 'user_default', '-e', zip])
const extensionDir = path.join(userExtensions, 'user_default', 'dsh_blender_bridge')
record(
  'installed the committed add-on as a Blender extension',
  existsSync(path.join(extensionDir, '__init__.py')),
  extensionDir,
)
if (!existsSync(path.join(extensionDir, '__init__.py'))) {
  console.log('SKIP Windows GA V0: add-on install did not produce ' + extensionDir)
  process.exit(0)
}
runBlender([
  '--background',
  '--factory-startup',
  '--python-expr',
  'import addon_utils, bpy; addon_utils.enable("bl_ext.user_default.dsh_blender_bridge", default_set=True, persistent=True); bpy.ops.wm.save_userpref()',
])

const port = await freePort()
process.env.DSH_BLENDER_GATEWAY_PORT = String(port)
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

const caller = (suffix) => ({
  hostId: 'ga-host',
  dshSessionId: 'ga-session-' + suffix,
  agentId: 'ga-agent-' + suffix,
  role: 'main',
  displayName: 'GA ' + suffix,
})
const callers = [caller('a'), caller('b')]
const app = createGatewayApp({ port })
const started = []
const gpu = []
let done = false

const GPU_CODE = [
  'import bpy, gpu, json',
  'facts = {"background": bool(bpy.app.background), "version": bpy.app.version_string,',
  '         "vendor": gpu.platform.vendor_get(), "renderer": gpu.platform.renderer_get(),',
  '         "gl_version": gpu.platform.version_get()}',
  'print("DSH_GA_GPU " + json.dumps(facts))',
].join('\n')

const MARK_CODE = (name, rgb) =>
  [
    'import bpy, json',
    'obj = bpy.data.objects.get("Cube")',
    'mat = bpy.data.materials.get("GA_MARK")',
    'if mat is None:',
    '    mat = bpy.data.materials.new("GA_MARK")',
    'mat.diffuse_color = (' + rgb.join(', ') + ', 1.0)',
    'if obj is not None:',
    '    if obj.data.materials:',
    '        obj.data.materials[0] = mat',
    '    else:',
    '        obj.data.materials.append(mat)',
    'for area in bpy.context.screen.areas:',
    '    if area.type == "VIEW_3D":',
    '        area.spaces.active.shading.type = "SOLID"',
    '        area.spaces.active.shading.color_type = "MATERIAL"',
    '        break',
    'obj["ga_marker"] = "' + name + '"',
    'print("DSH_GA_MARK " + json.dumps({"marker": obj.get("ga_marker"), "color": list(mat.diffuse_color)[:3]}))',
  ].join('\n')

const WIN_CODE = [
  'import bpy, ctypes, json',
  'from ctypes import wintypes',
  'user32 = ctypes.windll.user32',
  'kernel32 = ctypes.windll.kernel32',
  'pid = int(kernel32.GetCurrentProcessId())',
  'windows = []',
  'desktops = set()',
  'user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]',
  'user32.EnumWindows.restype = wintypes.BOOL',
  'user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]',
  'user32.GetWindowThreadProcessId.restype = wintypes.DWORD',
  'user32.GetThreadDesktop.argtypes = [wintypes.DWORD]',
  'user32.GetThreadDesktop.restype = wintypes.HANDLE',
  'user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]',
  'user32.GetUserObjectInformationW.restype = wintypes.BOOL',
  'def cb(hwnd, lparam):',
  '    owner = wintypes.DWORD()',
  '    tid = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)))',
  '    if int(owner.value) == pid:',
  '        hdesk = user32.GetThreadDesktop(tid)',
  '        buf = ctypes.create_unicode_buffer(256)',
  '        need = wintypes.DWORD()',
  '        user32.GetUserObjectInformationW(hdesk, 2, ctypes.cast(buf, ctypes.c_void_p), 512, ctypes.byref(need))',
  '        desktops.add(buf.value)',
  '        windows.append({"desktop": buf.value, "visible": bool(user32.IsWindowVisible(hwnd))})',
  '    return True',
  'user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)',
  'print("DSH_GA_WIN " + json.dumps({"pid": pid, "windows": len(windows), "desktops": sorted(desktops), "visible": sum(1 for w in windows if w["visible"])}))',
].join('\n')

function pixelCode(filepath) {
  return [
    'import bpy, json',
    'import numpy as np',
    'img = bpy.data.images.load(' + JSON.stringify(filepath) + ')',
    'arr = np.empty(len(img.pixels), dtype=np.float32)',
    'img.pixels.foreach_get(arr)',
    'arr = arr.reshape(-1, 4)[:, :3]',
    'print("DSH_GA_PIX " + json.dumps({"width": int(img.size[0]), "height": int(img.size[1]), "mean": [float(x) for x in arr.mean(axis=0)], "std": float(arr.std()), "max": float(arr.max())}))',
    'bpy.data.images.remove(img)',
  ].join('\n')
}

async function runCode(instanceId, code, timeoutMs = 60000) {
  const res = await app.addonClient.send('execute_code', { code }, timeoutMs, { instanceId })
  const rec = res && typeof res === 'object' ? res : null
  if (rec && rec.error) throw new Error(String(rec.error))
  return rec && typeof rec.result === 'string' ? rec.result : ''
}

const tagged = (text, tag) => {
  const line = text
    .split(/\r?\n/)
    .filter((x) => x.startsWith(tag + ' '))
    .pop()
  return line ? JSON.parse(line.slice(tag.length + 1)) : null
}

try {
  await new Promise((resolve, reject) => {
    app.server.once('error', reject)
    app.server.listen(port, '127.0.0.1', () => resolve(true))
  })
  record('Gateway listens on the chosen loopback port', true, '127.0.0.1:' + String(port))
  const capability = app.sessionRegistry.display()
  record(
    'virtual-gui defaults to auto on Windows after the V0 acceptance',
    capability.feature === 'auto' && capability.feature_source === 'default:v0',
    'feature=' + String(capability.feature) + ' source=' + String(capability.feature_source),
  )
  record(
    'virtual-gui capabilities are ready on this machine',
    capability.ready === true,
    'provider=' + String(capability.provider) + ' missing=' + JSON.stringify(capability.missing),
  )

  const startAt = Date.now()
  const results = await Promise.all(
    callers.map(async (who) => {
      const consent = app.sessionRegistry.issueConsent(who)
      if (!consent.ok) return { ok: false, code: 'consent_failed' }
      return {
        who,
        started: await app.sessionRegistry.start(who, { consentToken: consent.consentToken }),
      }
    }),
  )
  const startMs = Date.now() - startAt
  for (const item of results) {
    if (!item.who) {
      record('both virtual-gui starts were issued', false, JSON.stringify(item))
      continue
    }
    const result = item.started
    const ok = result && result.ok === true && !!result.session
    record(
      'virtual-gui start returned a ready session for ' + item.who.agentId,
      ok,
      ok ? String(result.session.id) : String((result && result.code) || 'start_failed'),
    )
    if (ok) started.push({ who: item.who, session: result.session })
  }
  if (started.length !== 2)
    throw new Error('expected two ready virtual-gui sessions, got ' + String(started.length))
  record(
    'two virtual-gui instances started and are active concurrently',
    true,
    String(startMs) + 'ms',
  )

  const instanceIds = started.map((x) => String(x.session.instanceId))
  const pids = started.map((x) => Number((x.session.display || {}).pid || 0))
  const desktops = started.map((x) => String((x.session.display || {}).display || ''))
  record(
    'the two sessions have distinct instance ids, pids, and desktops',
    new Set(instanceIds).size === 2 && new Set(pids).size === 2 && new Set(desktops).size === 2,
    JSON.stringify({ instanceIds, pids, desktops }),
  )
  record(
    'both desktops are private hidden desktops',
    desktops.every((name) => name.length > 0 && !/^default$/i.test(name)),
    JSON.stringify(desktops),
  )

  const instances = await waitFor(
    () =>
      instanceIds.every((id) =>
        app.addonGateway.list().some((x) => x.instanceId === id && x.ready),
      ) || null,
    30000,
  )
  record(
    'both spawned add-ons handshake with their exact instance ids',
    !!instances,
    JSON.stringify(
      app.addonGateway.list().map((x) => ({ id: x.instanceId, kind: x.kind, ready: x.ready })),
    ),
  )
  const bothAlive = await waitFor(
    () => pids.every((pid) => tasklistPids('blender.exe').includes(pid)),
    15000,
  )
  record('both Blender processes are alive at the same time', !!bothAlive, JSON.stringify(pids))

  for (let index = 0; index < started.length; index += 1) {
    const session = started[index].session
    const instanceId = String(session.instanceId)
    const text = await runCode(instanceId, GPU_CODE)
    const info = tagged(text, 'DSH_GA_GPU')
    gpu.push({ instanceId, info })
    record(
      'instance ' + instanceId + ' reports a non-software OpenGL renderer',
      !!info &&
        info.background === false &&
        !/llvmpipe|softpipe|swiftshader|software/i.test(String(info.renderer)),
      info
        ? String(info.renderer) + ' / ' + String(info.vendor) + ' / ' + String(info.gl_version)
        : 'no GPU info',
    )
    const winText = await runCode(instanceId, WIN_CODE).catch(
      (error) => 'DSH_GA_WIN_ERR ' + String(error.message),
    )
    const win = tagged(winText, 'DSH_GA_WIN')
    record(
      'instance ' + instanceId + ' windows live on one private desktop',
      !!win && Array.isArray(win.desktops) && win.desktops.length === 1 && win.windows > 0,
      win ? JSON.stringify(win) : winText.slice(0, 300),
    )
  }

  const colors = [
    [1.0, 0.05, 0.05],
    [0.05, 0.15, 1.0],
  ]
  const shots = []
  for (let index = 0; index < started.length; index += 1) {
    const instanceId = String(started[index].session.instanceId)
    const marker = await runCode(instanceId, MARK_CODE('marker-' + String(index), colors[index]))
    record(
      'instance ' + instanceId + ' accepted its isolation marker',
      !!tagged(marker, 'DSH_GA_MARK'),
      marker.trim().slice(0, 200),
    )
    const filepath = path.join(evidenceDir, 'instance-' + String(index) + '-viewport.png')
    const shot = await app.addonClient.send(
      'get_viewport_screenshot',
      { max_size: 800, filepath, format: 'png' },
      60000,
      { instanceId },
    )
    const shotRec = shot && typeof shot === 'object' ? shot : {}
    const ok = shotRec.success === true && shotRec.method === 'offscreen' && existsSync(filepath)
    record(
      'instance ' + instanceId + ' GPUOffScreen viewport capture succeeded',
      ok,
      'method=' +
        String(shotRec.method) +
        ' size=' +
        String(shotRec.width) +
        'x' +
        String(shotRec.height),
    )
    const bytes = existsSync(filepath) ? readFileSync(filepath) : Buffer.alloc(0)
    const pixText = bytes.length ? await runCode(instanceId, pixelCode(filepath)) : ''
    const pix = pixText ? tagged(pixText, 'DSH_GA_PIX') : null
    shots.push({
      instanceId,
      filepath,
      sha256: bytes.length ? sha256(bytes) : '',
      bytes: bytes.length,
      pixels: pix,
    })
    record(
      'instance ' + instanceId + ' viewport PNG has non-flat pixels',
      !!pix && pix.max > 0.02 && pix.std > 0.003,
      pix
        ? 'mean=' + JSON.stringify(pix.mean) + ' std=' + String(pix.std) + ' max=' + String(pix.max)
        : 'no pixels',
    )
  }
  record(
    'the two viewport captures differ',
    shots.length === 2 && shots[0].sha256 !== shots[1].sha256,
    shots.map((x) => String(x.sha256).slice(0, 16)).join(' / '),
  )
  if (shots.length === 2 && shots[0].pixels && shots[1].pixels) {
    const [a, b] = shots
    record(
      'each capture shows its own marker colour (no pixel cross-talk)',
      a.pixels.mean[0] > b.pixels.mean[0] && b.pixels.mean[2] > a.pixels.mean[2],
      'A=' + JSON.stringify(a.pixels.mean) + ' B=' + JSON.stringify(b.pixels.mean),
    )
  }
  for (let index = 0; index < started.length; index += 1) {
    const instanceId = String(started[index].session.instanceId)
    const check = await app.addonClient.send(
      'execute_code',
      {
        code: [
          'import bpy, json',
          'obj = bpy.data.objects.get("Cube")',
          'print("DSH_GA_STATE " + json.dumps({"marker": obj.get("ga_marker") if obj else None, "objects": len(bpy.data.objects)}))',
        ].join('\n'),
      },
      60000,
      { instanceId },
    )
    const text = check && typeof check.result === 'string' ? check.result : ''
    const state = tagged(text, 'DSH_GA_STATE')
    record(
      'instance ' + instanceId + ' still reports only its own marker',
      !!state && state.marker === 'marker-' + String(index),
      JSON.stringify(state),
    )
  }

  const stopAt = Date.now()
  for (const { who, session } of started) {
    const stop = await app.sessionRegistry.stop(who, String(session.id))
    record(
      'stop for ' + String(session.id) + ' reports success',
      stop.ok === true && stop.stopped === true,
      JSON.stringify(stop),
    )
  }
  record(
    'stop removed both sessions',
    started.every(({ session }) => app.sessionRegistry.getSession(String(session.id)) === null),
    JSON.stringify(started.map(({ session }) => session.id)),
  )
  const pidsGone = await waitFor(
    () => pids.every((pid) => !tasklistPids('blender.exe').includes(pid)),
    20000,
  )
  record('no Blender process is left behind', !!pidsGone, JSON.stringify(pids))
  const helpersGone = await waitFor(
    () => tasklistPids('dsh-blender-hidden-desktop.exe').length === 0,
    15000,
  )
  record(
    'no hidden-desktop helper process is left behind',
    !!helpersGone,
    JSON.stringify(tasklistPids('dsh-blender-hidden-desktop.exe')),
  )
  const stopMs = Date.now() - stopAt
  for (const { who, session } of started) {
    const again = await app.sessionRegistry.stop(who, String(session.id))
    record(
      'a second stop for ' + String(session.id) + ' is idempotent',
      again.ok === true && again.stopped === false,
      JSON.stringify(again),
    )
  }

  scenario(
    'lock screen',
    'not-exercised',
    'recorded limitation; the hidden desktop is independent of the interactive desktop but a lock/unlock run was not performed',
  )
  done = true
  const report = {
    generatedAt: new Date().toISOString(),
    task: '5.5',
    blender: exe,
    gatewayPort: port,
    environment: facts,
    scenarios,
    startMs,
    stopMs,
    gpu,
    sessions: started.map(({ session }) => session),
    shots: shots.map((x) => ({
      instanceId: x.instanceId,
      filepath: x.filepath,
      sha256: x.sha256,
      bytes: x.bytes,
      pixels: x.pixels,
    })),
    checks,
    passed: checks.filter((c) => c.ok).length,
    total: checks.length,
  }
  if (jsonPath) writeFileSync(jsonPath, JSON.stringify(report, null, 2) + '\n')
  writeFileSync(path.join(evidenceDir, 'report.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(
    'Windows GA V0: ' + String(report.passed) + '/' + String(report.total) + ' checks passed',
  )
  if (checks.some((c) => !c.ok)) process.exitCode = 1
} catch (error) {
  record(
    'the acceptance run completed without an unexpected error',
    false,
    String(error && error.stack ? error.stack : error),
  )
  process.exitCode = 1
} finally {
  if (!done) {
    for (const { who, session } of started) {
      try {
        await app.sessionRegistry.stop(who, String(session.id))
      } catch (e) {
        /* best effort */
      }
    }
  }
  app.close()
}
