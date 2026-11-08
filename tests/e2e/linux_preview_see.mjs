#!/usr/bin/env node
/**
 * E2E: Linux preview chain for virtual-gui + blender_see (3.1 tasks 5.8 / 5.20d).
 *
 * Runs on Linux: a real Gateway, the committed add-on installed into a
 * throw-away Blender profile, and one virtual-gui instance started through the
 * Xvfb provider. It then calls the host `blender_see` implementation and checks
 * that the returned PNG is a real GUI frame (non-black, non-single-color), that
 * the reported GL renderer matches the capability (hardware GPU vs preview-only
 * llvmpipe), and that after stop the tool fails without spawning a headless
 * Blender.
 *
 * CI installs Xvfb + Blender 4.2.10 and has no GPU: the run exercises the
 * software preview path and is explicit evidence for the call chain only, never
 * for hardware GL. A machine with VirtualGL + a readable /dev/dri exercises the
 * hardware transport.
 *
 * Usage:
 *   node tests/e2e/linux_preview_see.mjs [--blender <path>] [--json <path>]
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { inflateSync } from 'node:zlib'
import { ensureBuild } from '../../scripts/ensure-build.mjs'

const root = path.resolve(import.meta.dirname, '..', '..')
const argv = process.argv.slice(2)
const option = (name) => {
  const index = argv.indexOf(name)
  return index >= 0 ? argv[index + 1] : ''
}
const zip = path.join(root, 'extension', 'dsh_blender_bridge.zip')
const exe = path.resolve(
  option('--blender') || process.env.DSH_BLENDER_EXE || '/opt/blender/blender',
)
const jsonPath = option('--json') ? path.resolve(option('--json')) : ''
const workDir =
  process.env.DSH_BLENDER_WORKDIR || path.join(os.tmpdir(), 'dsh-linux-see-' + String(process.pid))
const userScripts = path.join(workDir, 'user_scripts')
const userExtensions = path.join(workDir, 'user_extensions')
const userConfig = path.join(workDir, 'user_config')
const tempDir = path.join(workDir, 'tmp')

const checks = []
const record = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail: detail || '' })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ': ' + detail : ''))
}

if (process.platform !== 'linux') {
  console.log('SKIP Linux preview see E2E: Linux only')
  process.exit(0)
}
if (!existsSync(exe)) {
  console.log('SKIP Linux preview see E2E: no Blender executable at ' + exe)
  process.exit(0)
}
if (!existsSync(zip)) {
  console.log('SKIP Linux preview see E2E: add-on zip missing (run npm run build:extension)')
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
  TMPDIR: tempDir,
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

function processAlive(pid) {
  if (!(Number(pid) > 0)) return false
  try {
    process.kill(Number(pid), 0)
    return true
  } catch (e) {
    return false
  }
}

function pgrep(pattern) {
  try {
    const out = execFileSync('pgrep', ['-af', pattern], { encoding: 'utf8' })
    return out.trim().split(/\n/).filter(Boolean)
  } catch (e) {
    return []
  }
}

/** Match the process name exactly so the node process running this script is not counted. */
function pgrepExact(name) {
  try {
    const out = execFileSync('pgrep', ['-x', name], { encoding: 'utf8' })
    return out.trim().split(/\n/).filter(Boolean)
  } catch (e) {
    return []
  }
}

/** Minimal 8-bit RGB/RGBA PNG decoder (no dependency; Blender screenshots only). */
function decodePng(buffer) {
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
  if (buffer.length < 8 || !buffer.subarray(0, 8).equals(sig)) throw new Error('not a PNG')
  let offset = 8
  let width = 0
  let height = 0
  let colorType = 0
  let bitDepth = 0
  const idat = []
  while (offset + 8 <= buffer.length) {
    const length = buffer.readUInt32BE(offset)
    const type = buffer.toString('ascii', offset + 4, offset + 8)
    const data = buffer.subarray(offset + 8, offset + 8 + length)
    offset += 12 + length
    if (type === 'IHDR') {
      width = data.readUInt32BE(0)
      height = data.readUInt32BE(4)
      bitDepth = data[8]
      colorType = data[9]
      if (data[10] !== 0 || data[11] !== 0 || data[12] !== 0) throw new Error('interlaced PNG')
    } else if (type === 'IDAT') {
      idat.push(data)
    } else if (type === 'IEND') {
      break
    }
  }
  if (bitDepth !== 8 || (colorType !== 2 && colorType !== 6))
    throw new Error(
      'unsupported PNG color type ' + String(colorType) + ' depth ' + String(bitDepth),
    )
  const channels = colorType === 6 ? 4 : 3
  const stride = width * channels
  const raw = inflateSync(Buffer.concat(idat))
  const pixels = Buffer.alloc(stride * height)
  let prev = Buffer.alloc(stride)
  let src = 0
  for (let y = 0; y < height; y += 1) {
    const filter = raw[src]
    src += 1
    const line = Buffer.from(raw.subarray(src, src + stride))
    src += stride
    for (let x = 0; x < stride; x += 1) {
      const left = x >= channels ? line[x - channels] : 0
      const up = prev[x]
      const upLeft = x >= channels ? prev[x - channels] : 0
      if (filter === 1) line[x] = (line[x] + left) & 0xff
      else if (filter === 2) line[x] = (line[x] + up) & 0xff
      else if (filter === 3) line[x] = (line[x] + Math.floor((left + up) / 2)) & 0xff
      else if (filter === 4) {
        const p = left + up - upLeft
        const pa = Math.abs(p - left)
        const pb = Math.abs(p - up)
        const pc = Math.abs(p - upLeft)
        line[x] = (line[x] + (pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft)) & 0xff
      }
    }
    line.copy(pixels, y * stride)
    prev = line
  }
  return { width, height, channels, pixels }
}

function pixelStats(image) {
  const { pixels, channels } = image
  const count = pixels.length / channels
  const sum = [0, 0, 0]
  const min = [255, 255, 255]
  const max = [0, 0, 0]
  const colors = new Set()
  for (let i = 0; i < count; i += 1) {
    const base = i * channels
    let key = 0
    for (let c = 0; c < 3; c += 1) {
      const value = pixels[base + c]
      sum[c] += value
      if (value < min[c]) min[c] = value
      if (value > max[c]) max[c] = value
      key = (key * 1024 + value) % 4294967296
    }
    if (colors.size < 4096) colors.add(key)
  }
  return {
    mean: sum.map((value) => Math.round((value / count) * 10) / 10),
    min,
    max,
    distinct_approx: colors.size,
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
  console.log('SKIP Linux preview see E2E: add-on install did not produce ' + extensionDir)
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
process.env.TMPDIR = tempDir

ensureBuild()
const { CFG } = await import('../../lib/runtime/config.js')
const { createGatewayApp } = await import('../../lib/runtime/gateway/app.js')
const { executeSee } = await import('../../lib/tools/see/execute.js')

record(
  'the Gateway port follows DSH_BLENDER_GATEWAY_PORT',
  CFG.gatewayPort === port,
  String(port) + ' / ' + String(CFG.gatewayPort),
)

/** A host context with just the attachment service blender_see uses. */
function seeContext(store) {
  return {
    get(name) {
      if (name !== 'attachments') return undefined
      return {
        imageLimits: { mediaTypes: ['image/png'] },
        async saveImage(input) {
          const data = Buffer.from(input.data)
          store.push(data)
          return {
            attachmentId: 'linux-see-' + String(store.length),
            mediaType: 'image/png',
            bytes: data.length,
            width: 0,
            height: 0,
            name: input.name,
          }
        },
      }
    },
  }
}

const app = createGatewayApp({ port })
const caller = {
  hostId: 'e2e-linux-host',
  dshSessionId: 'e2e-linux-session',
  agentId: 'e2e-linux-agent',
  role: 'main',
  displayName: 'E2E-Linux',
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
    'the Linux preview capability is ready with the feature explicitly on',
    capability.ready === true && capability.preview === true,
    'hardware_gl=' +
      String(capability.hardware_gl) +
      ' missing=' +
      JSON.stringify(capability.missing),
  )

  const noConsent = await registry.start(caller)
  record(
    'start without a consent token is refused before spawning',
    noConsent.ok === false && noConsent.code === 'consent_required',
    String(noConsent.code || ''),
  )
  const consent = registry.issueConsent(caller)
  const started = await registry.start(caller, { consentToken: consent.consentToken })
  record(
    'virtual-gui start returned a ready Linux session',
    started.ok === true && !!started.session,
    started.ok ? '' : String(started.code || 'start_failed'),
  )
  if (!started.ok) throw new Error('start failed: ' + String(started.code || 'unknown'))
  session = started.session
  const instanceId = String(session.instanceId || '')
  const display = session.display || null
  record(
    'session records the Linux provider and its own X display',
    !!display &&
      display.provider === 'linux-xvfb-virtualgl' &&
      /^:\d+$/.test(String(display.display || '')),
    JSON.stringify(display),
  )

  const instance = await waitFor(
    () => app.addonGateway.list().find((x) => x.instanceId === instanceId && x.ready),
    30000,
  )
  record(
    'the spawned add-on handshakes with the exact instanceId',
    !!instance && instance.kind === 'virtual-gui',
    instanceId + ' / instances=' + JSON.stringify(app.addonGateway.list().map((x) => x.instanceId)),
  )
  record('the reported Blender pid is running', processAlive(display.pid), String(display.pid))

  const probeCode =
    'import bpy, gpu\n' +
    "print('DSH_BG=' + str(bpy.app.background))\n" +
    "print('DSH_RENDERER=' + gpu.platform.renderer_get())\n" +
    "print('DSH_VENDOR=' + gpu.platform.vendor_get())\n"
  const out = await app.engine.act(probeCode, 120000)
  const stdout = String(out.stdout || '')
  const field = (name) => {
    const line = stdout.split(/\n/).find((item) => item.startsWith(name + '='))
    return line ? line.slice(name.length + 1) : ''
  }
  const renderer = field('DSH_RENDERER')
  const softwareGl = /llvmpipe|softpipe|swrast|software/i.test(renderer)
  record('Blender runs as a real GUI process (not background)', field('DSH_BG') === 'False', stdout)
  if (capability.hardware_gl) {
    record(
      'the hardware GL capability reports a real GPU renderer',
      Boolean(renderer) && !softwareGl,
      renderer,
    )
  } else {
    record(
      'the software preview path reports llvmpipe and is labeled preview-only',
      softwareGl,
      renderer,
    )
  }

  const pngs = []
  const see = await executeSee(seeContext(pngs), port, { aspect: '16:9', max_size: 1024 }, session)
  record(
    'blender_see returned a GUI PNG through the Gateway',
    pngs.length === 1 && pngs[0].length > 1000 && !!see.image,
    'bytes=' +
      String(pngs[0] ? pngs[0].length : 0) +
      ' text=' +
      String(see.text || '').slice(0, 160),
  )
  if (pngs.length !== 1) throw new Error('blender_see returned no PNG')
  const image = decodePng(pngs[0])
  const stats = pixelStats(image)
  record(
    'the PNG is a valid decoded frame with a sensible size',
    image.width >= 500 && image.height >= 300,
    String(image.width) + 'x' + String(image.height),
  )
  record(
    'the frame is not all black',
    stats.mean.some((value) => value > 2),
    JSON.stringify(stats.mean),
  )
  record(
    'the frame is not a single color',
    stats.distinct_approx > 10 || stats.max.some((value, index) => value - stats.min[index] > 2),
    JSON.stringify({ distinct: stats.distinct_approx, min: stats.min, max: stats.max }),
  )

  stopped = true
  const stop = await registry.stop(caller, String(session.id))
  record(
    'stop removes the session',
    stop.ok === true && stop.stopped === true && registry.getSession(String(session.id)) === null,
    JSON.stringify(stop),
  )
  const pidGone = await waitFor(() => !processAlive(display.pid), 15000)
  record('no Blender process is left behind', !!pidGone, String(display.pid))
  const xvfbGone = await waitFor(() => pgrep('Xvfb ' + String(display.display)).length === 0, 15000)
  record('no Xvfb process is left behind', !!xvfbGone, String(display.display))
  const again = await registry.stop(caller, String(session.id))
  record(
    'a second stop is idempotent',
    again.ok === true && again.stopped === false,
    JSON.stringify(again),
  )

  const before = pgrepExact('blender').length
  const pngsAfter = []
  const after = await executeSee(
    seeContext(pngsAfter),
    port,
    { aspect: '16:9', max_size: 1024 },
    session,
  )
  const afterText = String(after.text || '')
  record(
    'blender_see after stop fails instead of falling back to headless',
    !!after && after.image === undefined && /失败|不可用|unreachable|no ready/i.test(afterText),
    afterText.slice(0, 220),
  )
  const afterCount = pgrepExact('blender').length
  record(
    'the failed see did not spawn a headless Blender',
    afterCount <= before,
    'before=' + String(before) + ' after=' + String(afterCount),
  )

  const elapsedMs = Date.now() - startedAt
  console.log(
    'Linux preview see E2E: ' +
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
        {
          generatedAt: new Date().toISOString(),
          platform: process.platform,
          blender: exe,
          capability: {
            hardware_gl: capability.hardware_gl,
            missing: capability.missing,
            preview: capability.preview,
          },
          renderer,
          vendor: field('DSH_VENDOR'),
          session,
          image: { width: image.width, height: image.height, channels: image.channels },
          stats,
          checks,
          elapsedMs,
        },
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
