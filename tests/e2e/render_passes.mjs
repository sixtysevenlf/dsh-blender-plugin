#!/usr/bin/env node
/**
 * E2E: real-Blender render passes (tasks 5.3 / 5.4).
 *
 * Exercises the repo's own `lib/` through the job queue, so it tests the code in this
 * checkout rather than a DSH-installed copy. Every job starts a real Blender process:
 * it is not part of unit/integration. Without a discoverable Blender the script prints
 * SKIP and exits 0 so a machine without Blender does not report a false product failure.
 *
 * Coverage:
 * - beauty / depth / normal / alpha land as PNG with the manifest contract intact
 * - object_index decodes back to the manifest mapping (the IndexOB normalization)
 * - depth metres, normal camera-space unit vectors and alpha range are checked against
 *   the actual pixels by `tests/e2e/blender/render_passes_verify.py`, run in the same
 *   Blender so the epoch of the pass files and the reader match
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { ensureBuild } from '../../scripts/ensure-build.mjs'

const root = path.resolve(import.meta.dirname, '..', '..')
const workDir =
  process.env.DSH_BLENDER_WORKDIR || path.join(os.tmpdir(), 'dsh-passes-e2e-' + String(process.pid))
process.env.DSH_BLENDER_WORKDIR = workDir
rmSync(workDir, { recursive: true, force: true })
mkdirSync(path.join(workDir, 'queue'), { recursive: true })

ensureBuild()
const { JobQueue } = await import('../../lib/runtime/core/queue/manager.js')
const { SpawnJobExecutor } = await import('../../lib/runtime/core/queue/spawn-executor.js')
const { JsonJobStore } = await import('../../lib/runtime/core/queue/store.js')
const { createRenderCache } = await import('../../lib/runtime/core/queue/render-cache.js')
const { CFG } = await import('../../lib/runtime/config.js')

const exe = process.env.DSH_BLENDER_EXE || CFG.blenderExe
if (!exe || !existsSync(exe)) {
  console.log('SKIP render-pass E2E: no DSH_BLENDER_EXE / discovered blender executable')
  process.exit(0)
}

const PASSES = (process.env.DSH_E2E_PASSES || 'beauty,depth,normal,alpha,object_index')
  .split(',')
  .map((name) => name.trim())
  .filter(Boolean)
/** Cycles is the only engine that exposes Object Index on Blender 5.x; override to check others. */
const ENGINE = process.env.DSH_E2E_ENGINE || 'CYCLES'
const checks = []
const check = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ' · ' + detail : ''))
}

const caller = { hostId: 'e2e', dshSessionId: 'e2e', agentId: 'e2e-main', role: 'main' }
/** Every executor.start() call: a cache hit must not Increase this number. */
let spawnStarts = 0
// Keep the cache inside the E2E work dir so the run never touches the real home.
const cache = createRenderCache({ home: path.join(workDir, 'home') })?.cache ?? null
check('render cache is available', cache !== null, cache ? 'ok' : 'createRenderCache returned null')
const queue = new JobQueue({
  executor: (() => {
    const executor = new SpawnJobExecutor({ blenderExe: exe })
    const startOriginal = executor.start.bind(executor)
    executor.start = (job) => {
      spawnStarts += 1
      return startOriginal(job)
    }
    return executor
  })(),
  store: new JsonJobStore(path.join(workDir, 'queue', 'state.json')),
  renderCache: cache,
})

const scene = path.join(workDir, 'passes-scene.blend')
const sceneJob = queue.submit(caller, {
  kind: 'headless',
  timeoutMs: 180000,
  script: [
    'import bpy',
    'bpy.ops.wm.read_factory_settings(use_empty=True)',
    'sc = bpy.context.scene',
    'sc.render.resolution_x = 128',
    'sc.render.resolution_y = 128',
    'sc.render.resolution_percentage = 100',
    'sc.frame_start = 1',
    'sc.frame_end = 2',
    'bpy.ops.object.camera_add(location=(0.0, -8.0, 3.2))',
    'cam = bpy.context.object',
    'cam.name = "CamMain"',
    'cam.rotation_euler = (1.15, 0.0, 0.0)',
    'sc.camera = cam',
    'bpy.ops.object.light_add(type="SUN", location=(4.0, -4.0, 8.0))',
    'bpy.context.object.data.energy = 3.0',
    'bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0.0, 0.0, -1.0))',
    'bpy.context.object.name = "Ground"',
    'bpy.ops.mesh.primitive_cube_add(size=2.0, location=(-2.0, 0.0, 1.0))',
    'bpy.context.object.name = "CubeMain"',
    'bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(2.0, 0.0, 1.0))',
    'bpy.context.object.name = "SphereMain"',
    'cube = bpy.data.objects["CubeMain"]',
    'cube.location.x = -2.0; cube.keyframe_insert("location", frame=1)',
    'cube.location.x = -1.0; cube.keyframe_insert("location", frame=2)',
    'bpy.ops.wm.save_as_mainfile(filepath=' + JSON.stringify(scene) + ')',
    'print("SCENE_OK")',
  ].join('\n'),
})
const sceneDone = await queue.wait(caller, sceneJob.id, 240000)
check('scene job completes', sceneDone.job.state === 'completed', String(sceneDone.job.state))
if (!existsSync(scene)) {
  console.error('FAIL render-pass E2E · scene file was not created')
  process.exit(1)
}

const outdir = path.join(workDir, 'out')
// The ground plane is flat at z=-1, so its world normal is +Z. The camera is pitched
// down by 1.15 rad about X; rotating +Z into that camera's space gives (0, sin, cos).
// This is independent of the Python conversion, so the verifier is a real cross-check.
const CAMERA_PITCH = 1.15
const groundNormalCamera = [0, Math.sin(CAMERA_PITCH), Math.cos(CAMERA_PITCH)]
const expectFile = path.join(workDir, 'expect.json')
writeFileSync(
  expectFile,
  JSON.stringify(
    {
      groundNormalCamera,
      cameraPitch: CAMERA_PITCH,
      resolution: [128, 128],
    },
    null,
    2,
  ),
)
const renderJob = queue.submit(caller, {
  kind: 'render',
  file: scene,
  passes: PASSES,
  frames: [1, 2],
  resolution: [128, 128],
  samples: 8,
  engine: ENGINE,
  outdir,
  timeoutMs: 300000,
})
const rendered = await queue.wait(caller, renderJob.id, 300000)
const renderedJob = rendered.job
check(
  'render job completes',
  renderedJob.state === 'completed',
  String(renderedJob.state) +
    (renderedJob.error ? ' · ' + JSON.stringify(renderedJob.error).slice(0, 300) : ''),
)

function walk(dir, depth = 0, acc = []) {
  if (depth > 4 || !existsSync(dir)) return acc
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(p, depth + 1, acc)
    else acc.push(p)
  }
  return acc
}

const files = walk(outdir)
const manifests = files.filter((p) => path.basename(p) === 'manifest.json')
check('one manifest per frame', manifests.length === 2, String(manifests.length))

for (const manifestPath of manifests.sort()) {
  const frameDir = path.dirname(manifestPath)
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  const label = 'frame ' + String(manifest.frame)
  check(
    label + ':manifestVersion 1',
    manifest.manifestVersion === 1,
    String(manifest.manifestVersion),
  )
  check(
    label + ':no degraded passes',
    JSON.stringify(manifest.degraded) === '[]',
    JSON.stringify(manifest.degraded),
  )
  check(
    label + ':blenderVersion recorded',
    Boolean(manifest.blenderVersion),
    String(manifest.blenderVersion),
  )
  check(
    label + ':resolution honoured',
    JSON.stringify(manifest.resolution) === JSON.stringify([128, 128]),
    JSON.stringify(manifest.resolution),
  )
  for (const name of PASSES) {
    const entry = manifest.passes[name]
    check(label + ':' + name + ' in manifest', Boolean(entry), entry ? entry.path : 'missing')
    if (!entry) continue
    check(label + ':' + name + ' file exists', existsSync(entry.path), entry.path)
    const size = existsSync(entry.path) ? readFileSync(entry.path).length : 0
    check(
      label + ':' + name + ' bytes match file',
      size === entry.bytes,
      String(size) + ' vs ' + String(entry.bytes),
    )
  }
  const depthEntry = manifest.passes.depth
  if (depthEntry) {
    check(
      label + ':depth carries near/far',
      Number.isFinite(depthEntry.near) && Number.isFinite(depthEntry.far),
      depthEntry.near + '..' + depthEntry.far,
    )
    check(
      label + ':depth reserves raw 0 for no geometry',
      depthEntry.noGeometryValue === 0 && depthEntry.clampedAt === 'far',
      String(depthEntry.noGeometryValue) + '/' + String(depthEntry.clampedAt),
    )
  }
  const normalEntry = manifest.passes.normal
  if (normalEntry) {
    check(
      label + ':normal is camera space',
      normalEntry.coordinateSpace === 'camera',
      String(normalEntry.coordinateSpace),
    )
    check(
      label + ':normal comes from the world->camera conversion',
      normalEntry.source === 'converted:world->camera',
      String(normalEntry.source),
    )
  }
  const beautyEntry = manifest.passes.beauty
  if (beautyEntry) {
    check(
      label + ':beauty manifest names the real format',
      beautyEntry.format === 'PNG' && beautyEntry.dtype === 'uint8' && beautyEntry.channels === 3,
      String(beautyEntry.format) +
        '/' +
        String(beautyEntry.dtype) +
        '/' +
        String(beautyEntry.channels),
    )
  }
  const indexEntry = manifest.passes.object_index
  if (indexEntry) {
    check(
      label + ':object_index dtype uint16',
      indexEntry.dtype === 'uint16',
      String(indexEntry.dtype),
    )
    check(
      label + ':object_index has mapping',
      Object.keys(manifest.objectIndex?.mapping || {}).length > 0,
      String(Object.keys(manifest.objectIndex?.mapping || {}).length),
    )
  }

  // Pixel truth: run the verifier in the same Blender that wrote the passes.
  const verifyScript = path.join(root, 'tests', 'e2e', 'blender', 'render_passes_verify.py')
  let verifyOut = ''
  try {
    verifyOut = execFileSync(
      exe,
      ['-b', '--factory-startup', '--python', verifyScript, '--', frameDir, expectFile],
      {
        encoding: 'utf8',
        timeout: 300000,
      },
    )
  } catch (error) {
    verifyOut = String(error.stdout || '') + String(error.stderr || '')
  }
  const verifyLine = verifyOut.split(/\r?\n/).find((line) => line.startsWith('VERIFY_RESULT '))
  let verify = null
  try {
    verify = verifyLine ? JSON.parse(verifyLine.slice('VERIFY_RESULT '.length)) : null
  } catch {
    verify = null
  }
  check(
    label + ':pixel verification ran',
    verify !== null,
    verifyLine ? 'parsed' : verifyOut.slice(-300),
  )
  if (verify) {
    check(label + ':pixel verification passes', verify.ok === true, JSON.stringify(verify.failures))
  }
  for (const line of verifyOut.split(/\r?\n/)) {
    if (line.startsWith('FAIL ')) console.log('    ' + label + ' · ' + line)
  }
}

const failed = checks.filter((item) => !item.ok)
if (failed.length) {
  console.error('FAIL render-pass E2E · ' + failed.map((item) => item.name).join(' | '))
  process.exit(1)
}

// ---------------------------------------------------------------------------
// object_index is Cycles-only: an EEVEE submission must fail explicitly rather
// than emit an all-zero or all-one index image. Blender renamed the engine, so
// pick the EEVEE identifier from the version the render reported.
// ---------------------------------------------------------------------------
if (ENGINE === 'CYCLES' && PASSES.includes('object_index')) {
  const version = String(JSON.parse(readFileSync(manifests.sort()[0], 'utf8')).blenderVersion || '')
  const eevee = version.startsWith('5.') ? 'BLENDER_EEVEE' : 'BLENDER_EEVEE_NEXT'
  const eeveeJob = queue.submit(caller, {
    kind: 'render',
    file: scene,
    passes: ['object_index'],
    frames: [1],
    resolution: [32, 32],
    samples: 1,
    engine: eevee,
    outdir: path.join(workDir, 'out-eevee'),
    timeoutMs: 120000,
  })
  const eeveeDone = (await queue.wait(caller, eeveeJob.id, 120000)).job
  const eeveeFailure = eeveeDone.error || {}
  check(
    'object_index fails on ' + eevee,
    eeveeDone.state === 'failed' && eeveeFailure.code === 'pass_unsupported',
    String(eeveeDone.state) + ' · ' + JSON.stringify(eeveeFailure).slice(0, 240),
  )
  check(
    'the failure names Cycles as the requirement',
    /Cycles/.test(String(eeveeFailure.root_cause)) && /CYCLES/.test(String(eeveeFailure.fix)),
    String(eeveeFailure.root_cause) + ' | ' + String(eeveeFailure.fix),
  )
  check(
    'the failure produced no index image',
    walk(path.join(workDir, 'out-eevee')).filter((p) => p.endsWith('.png')).length === 0,
    String(walk(path.join(workDir, 'out-eevee')).length) + ' files written',
  )
}

// ---------------------------------------------------------------------------
// Cache: the same submission must be served without spawning Blender, and the
// hit must still copy the frames to the outdir the caller asked for.
// ---------------------------------------------------------------------------
const hitOutdir = path.join(workDir, 'out-hit')
const startsBeforeHit = spawnStarts
const cachedJob = queue.submit(caller, {
  kind: 'render',
  file: scene,
  passes: PASSES,
  frames: [1, 2],
  resolution: [128, 128],
  samples: 8,
  engine: ENGINE,
  outdir: hitOutdir,
  timeoutMs: 300000,
})
const cachedDone = (await queue.wait(caller, cachedJob.id, 120000)).job
const cachedHit = Boolean(cachedDone.result?.cache?.hit || cachedDone.cache?.hit)
check(
  'second submit is a cache hit',
  cachedHit,
  JSON.stringify(cachedDone.result?.cache || cachedDone.cache || null),
)
check(
  'cache hit did not spawn Blender',
  spawnStarts === startsBeforeHit && cachedDone.pid === null,
  'executor.start calls ' +
    String(spawnStarts - startsBeforeHit) +
    ' · pid ' +
    String(cachedDone.pid),
)
check(
  'cache hit keeps the requested outdir',
  cachedDone.params?.outdir === hitOutdir,
  String(cachedDone.params?.outdir),
)
const hitManifests = walk(hitOutdir).filter((p) => path.basename(p) === 'manifest.json')
check(
  'cache hit copies both frames into the outdir',
  hitManifests.length === 2,
  String(hitManifests.length) + ' manifests under ' + hitOutdir,
)
check(
  'cache hit artifacts live in the outdir',
  cachedDone.artifacts.length > 0 &&
    cachedDone.artifacts.every((artifact) => artifact.startsWith(hitOutdir)),
  String(cachedDone.artifacts.length) + ' artifacts',
)
for (const manifestPath of hitManifests) {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  const frameDir = path.dirname(manifestPath).split(path.sep).join('/')
  check(
    'cache hit manifest ' + String(manifest.frame) + ' points at its own copy',
    Object.values(manifest.passes).every((pass) => pass.path.startsWith(frameDir)),
    frameDir,
  )
}

// ---------------------------------------------------------------------------
// The pass entry writes 8-bit PNG only: a non-PNG beauty request must be a
// structured rejection, not a manifest that lies about dtype/colorspace.
// ---------------------------------------------------------------------------
const jpegJob = queue.submit(caller, {
  kind: 'render',
  file: scene,
  passes: ['beauty'],
  frames: [1],
  resolution: [32, 32],
  samples: 1,
  engine: ENGINE,
  format: 'OPEN_EXR',
  outdir: path.join(workDir, 'out-exr'),
  timeoutMs: 120000,
})
const jpegDone = (await queue.wait(caller, jpegJob.id, 120000)).job
const formatFailure = jpegDone.error || {}
check(
  'non-PNG pass render is rejected as pass_unsupported',
  jpegDone.state === 'failed' && formatFailure.code === 'pass_unsupported',
  String(jpegDone.state) + ' · ' + JSON.stringify(formatFailure).slice(0, 240),
)
check(
  'the rejection names PNG as the supported format',
  /PNG/.test(String(formatFailure.fix)),
  String(formatFailure.fix),
)

// ---------------------------------------------------------------------------
// vision_pass: the same manifests must decode into bounded numbers.
// ---------------------------------------------------------------------------
const { runVisionPass } = await import('../../lib/runtime/vision/pass.js')
const manifestPaths = manifests.sort()
const vision = runVisionPass(
  { manifests: manifestPaths, passes: ['depth', 'normal', 'object_index', 'alpha'] },
  { cwd: workDir },
)
check(
  'vision_pass ok',
  vision.ok === true,
  JSON.stringify(vision.error || vision.warnings || null).slice(0, 400),
)
const aggregate = vision.aggregate || {}
check('vision_pass saw both frames', aggregate.frame_count === 2, String(aggregate.frame_count))
check(
  'vision_pass decoded all four passes',
  JSON.stringify(aggregate.passes) === JSON.stringify(['depth', 'normal', 'object_index', 'alpha']),
  JSON.stringify(aggregate.passes),
)
const depthRange = aggregate.depth?.min
check(
  'vision_pass depth is in metres',
  typeof depthRange?.min === 'number' && depthRange.min > 0.05 && depthRange.max < 2000,
  JSON.stringify(aggregate.depth || null),
)
const decodedObjects = (aggregate.objects || []).map((row) => row.name)
check(
  'vision_pass resolves object names from the index map',
  ['CubeMain', 'Ground', 'SphereMain'].every((name) => decodedObjects.includes(name)),
  JSON.stringify(decodedObjects),
)
check(
  'vision_pass returns no pixel arrays',
  JSON.stringify(vision).length < 200000 &&
    !(vision.frames || []).some((frame) =>
      Object.values(frame).some((value) => Array.isArray(value) && value.length > 16),
    ),
  String(JSON.stringify(vision).length) + ' chars',
)
const visionFailed = checks.filter((item) => !item.ok)
if (visionFailed.length) {
  console.error('FAIL render-pass E2E · ' + visionFailed.map((item) => item.name).join(' | '))
  process.exit(1)
}
console.log('PASS render-pass E2E · ' + workDir + ' · blender ' + path.basename(exe))
