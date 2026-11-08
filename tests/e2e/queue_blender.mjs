#!/usr/bin/env node
/**
 * E2E: real Blender through the job queue (6.13/6.14 render camera animation part).
 *
 * Not part of unit/integration: every job below starts a real Blender process.
 * Run with DSH_BLENDER_EXE pointing at Blender 4.2.x. Without a discoverable
 * Blender the script prints SKIP and exits 0, so a machine without Blender does
 * not report a false product failure.
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import { ensureBuild } from '../../scripts/ensure-build.mjs'

const root = path.resolve(import.meta.dirname, '..', '..')
const workDir =
  process.env.DSH_BLENDER_WORKDIR || path.join(os.tmpdir(), 'dsh-queue-e2e-' + String(process.pid))
process.env.DSH_BLENDER_WORKDIR = workDir
rmSync(workDir, { recursive: true, force: true })
mkdirSync(path.join(workDir, 'queue'), { recursive: true })

ensureBuild()
const { JobQueue } = await import('../../lib/runtime/core/queue/manager.js')
const { SpawnJobExecutor } = await import('../../lib/runtime/core/queue/spawn-executor.js')
const { JsonJobStore } = await import('../../lib/runtime/core/queue/store.js')
const { CFG } = await import('../../lib/runtime/config.js')
const exe = process.env.DSH_BLENDER_EXE || CFG.blenderExe
if (!exe || !existsSync(exe)) {
  console.log('SKIP queue Blender E2E: no DSH_BLENDER_EXE / discovered blender executable')
  process.exit(0)
}

const caller = { hostId: 'e2e', dshSessionId: 'e2e', agentId: 'e2e-main', role: 'main' }
const queue = new JobQueue({
  executor: new SpawnJobExecutor({ blenderExe: exe }),
  store: new JsonJobStore(path.join(workDir, 'queue', 'state.json')),
})
const checks = []
const check = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail })
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? ' · ' + detail : ''))
}

const scene = path.join(workDir, 'animated-camera.blend')
const makeScene = queue.submit(caller, {
  kind: 'headless',
  script: [
    'import bpy, json',
    'bpy.ops.wm.read_factory_settings(use_empty=True)',
    'bpy.ops.mesh.primitive_cube_add(size=2)',
    'bpy.ops.object.camera_add(location=(6, -6, 5))',
    'cam = bpy.context.object',
    'cam.name = "Camera"',
    'bpy.context.scene.camera = cam',
    'cam.location = (6, -6, 5); cam.keyframe_insert("location", frame=1)',
    'cam.location = (0, -8, 3); cam.keyframe_insert("location", frame=2)',
    'bpy.ops.wm.save_as_mainfile(filepath=' + JSON.stringify(scene) + ')',
    'print("HEADLESS " + json.dumps({"scene": ' + JSON.stringify(scene) + '}))',
  ].join('\n'),
  timeout_ms: 120000,
})
await queue.wait(caller, makeScene.id, 120000)
const sceneState = queue.status(makeScene.id)
check(
  'headless makes an animated-camera scene',
  sceneState.state === 'completed' && existsSync(scene),
  sceneState.state,
)

const render = queue.submit(caller, {
  kind: 'render',
  file: scene,
  camera: 'Camera',
  frames: [1, 2],
  resolution: [96, 96],
  engine: 'BLENDER_WORKBENCH',
  format: 'PNG',
  outdir: path.join(workDir, 'frames'),
})
await queue.wait(caller, render.id, 120000)
const renderState = queue.status(render.id)
const hashes = (renderState.artifacts || []).map((file) =>
  createHash('sha256').update(readFileSync(file)).digest('hex'),
)
check(
  'render evaluates a keyframed camera for both frames',
  renderState.state === 'completed' && hashes.length === 2 && hashes[0] !== hashes[1],
  String(hashes.length) + ' frames',
)

const headless = queue.submit(caller, {
  kind: 'headless',
  script:
    'import bpy, json\nprint("HEADLESS " + json.dumps({"version": bpy.app.version_string, "objects": len(bpy.data.objects)}))',
  timeout_ms: 120000,
})
await queue.wait(caller, headless.id, 120000)
const headlessState = queue.status(headless.id)
check(
  'headless returns the HEADLESS json result',
  headlessState.state === 'completed' &&
    String(headlessState.result?.parsed?.version || '').startsWith('4.2.'),
  JSON.stringify(headlessState.result?.parsed || null),
)

const cancel = queue.submit(caller, {
  kind: 'headless',
  script: 'import time; print("sleeping", flush=True); time.sleep(60)',
  timeout_ms: 120000,
})
await new Promise((resolve) => setTimeout(resolve, 2000))
const beforeCancel = queue.status(cancel.id)
const canceled = queue.cancel(caller, cancel.id)
await new Promise((resolve) => setTimeout(resolve, 1500))
const afterCancel = queue.status(cancel.id)
let orphan = false
try {
  if (process.platform === 'win32') {
    orphan = /blender\.exe/i.test(
      execFileSync('tasklist.exe', ['/FI', 'PID eq ' + String(beforeCancel.pid)], {
        encoding: 'utf8',
      }),
    )
  } else {
    orphan =
      execFileSync('ps', ['-o', 'pid=', '-p', String(beforeCancel.pid)], {
        encoding: 'utf8',
      }).trim().length > 0
  }
} catch {
  orphan = false
}
check(
  'cancel stops the process tree without an orphan',
  canceled.canceled === true && afterCancel.state === 'canceled' && orphan === false,
  String(beforeCancel.pid),
)

const failed = checks.filter((item) => !item.ok)
if (failed.length) {
  console.error('FAIL queue Blender E2E · ' + failed.map((item) => item.name).join(', '))
  process.exit(1)
}
console.log('PASS queue Blender E2E · ' + workDir + ' · blender ' + path.basename(exe))
