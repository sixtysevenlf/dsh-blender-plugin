import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { PY_ROOT } from '../../paths.js'
import {
  extCandidates,
  extInstallHint,
  runExtCandidates,
  toWslPath,
} from './ext-check-candidates.js'
import { gltfResult, open3dResult } from './ext-check-result.js'

const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const PACKAGE_ROOT = path.dirname(PY_ROOT)

export interface ExtPaths {
  extDir: string
  gltfRunner: string
  open3dRunner: string
  gltfRunnerExists: boolean
  open3dRunnerExists: boolean
}

/** Resolve both source and built layouts without hard-coding .mjs / lib paths. */
export function resolveExtPaths(): ExtPaths {
  const extDir = path.join(PACKAGE_ROOT, 'runtime', 'ext')
  const builtRunner = path.join(RUNTIME_DIR, 'ext', 'gltf_validate_runner.js')
  const sourceRunner = path.join(extDir, 'gltf_validate_runner.ts')
  const gltfRunner = fs.existsSync(builtRunner) ? builtRunner : sourceRunner
  const open3dRunner = path.join(PY_ROOT, 'py', 'export', 'open3d_check.py')
  return {
    extDir,
    gltfRunner,
    open3dRunner,
    gltfRunnerExists: fs.existsSync(gltfRunner),
    open3dRunnerExists: fs.existsSync(open3dRunner),
  }
}

/** External validators (glTF-Validator / open3d) run locally and never take a Blender round-trip. */
export async function extCheckOp(op: string, payload?: unknown): Promise<unknown> {
  const p = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
  const cp = await import('node:child_process')
  const { extDir, gltfRunner, open3dRunner } = resolveExtPaths()
  const winPath = String(p.path || '')
  const wslPath = toWslPath(winPath)
  // open3d_check.py 已随导出域移入 runtime/py/export/；ext/ 只留 venv 与 gltf_validate_runner.mjs。
  const runner = op === 'gltf_validate' ? gltfRunner : open3dRunner
  const isWin = process.platform === 'win32'
  const cands = extCandidates({ op, extDir, runner, winPath, wslPath, isWin })
  const run = runExtCandidates(cp, cands)
  if (!run.raw) return extInstallHint({ op, extDir, winPath, isWin, tried: run.tried })
  let parsed: Record<string, unknown> | null
  try {
    parsed = JSON.parse(run.raw) as Record<string, unknown>
  } catch (e) {
    parsed = { parse_error: String(e).slice(0, 120), raw: run.raw.slice(0, 400) }
  }
  if (op === 'gltf_validate') return gltfResult(parsed, winPath, run)
  return open3dResult(parsed, run)
}
