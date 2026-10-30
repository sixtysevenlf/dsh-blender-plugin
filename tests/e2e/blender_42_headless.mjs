#!/usr/bin/env node
/**
 * T14 E2E: real Blender 4.2 LTS headless round-trip.
 *
 * Not part of unit/integration: this starts a real Blender process. It is the
 * evidence for ENV-BLENDER-42 and for the engine.headless path that the
 * blender_rt_headless tool ultimately uses.
 *
 * Run with DSH_BLENDER_EXE pointing at Blender 4.2.x. Without a discoverable
 * Blender the script prints SKIP and exits 0, so a machine without Blender does
 * not report a false product failure.
 */
import { existsSync, mkdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { ensureBuild } from '../../scripts/ensure-build.mjs'

const workDir = process.env.DSH_BLENDER_WORKDIR
  || path.join(os.tmpdir(), 'dsh-t14-blender42-' + String(process.pid))
mkdirSync(workDir, { recursive: true })
process.env.DSH_BLENDER_WORKDIR = workDir

ensureBuild()
const { createEngine } = await import('../../lib/runtime/engine.js')
const { CFG } = await import('../../lib/runtime/config.js')
const exe = process.env.DSH_BLENDER_EXE || CFG.blenderExe
if (!exe || !existsSync(exe)) {
  console.log('SKIP Blender 4.2 E2E: no DSH_BLENDER_EXE / discovered blender executable')
  process.exit(0)
}

const engine = createEngine()
const result = await engine.headless({
  script: 'import bpy; print("T14_BLENDER_E2E", bpy.app.version_string)',
  engine: 'none',
  factoryStartup: true,
  timeoutMs: 120000,
})
const stdout = String(result && result.stdout || '')
const ok = !!result && result.ok === true && result.exitCode === 0
  && stdout.includes('T14_BLENDER_E2E 4.2.')
if (!ok) {
  console.error(JSON.stringify({
    ok: result && result.ok,
    exitCode: result && result.exitCode,
    timedOut: result && result.timedOut,
    reason: result && result.reason,
    stdout: stdout.slice(0, 1200),
  }, null, 2))
  process.exit(1)
}
console.log('PASS Blender 4.2 headless E2E · ' + String(result.ms) + 'ms · ' + workDir)
