import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { errorMessage } from '../json.js'
import { PY_ROOT } from '../../paths.js'

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
  const wslPath = /^[A-Za-z]:[\\/]/.test(winPath)
    ? '/mnt/' + winPath[0].toLowerCase() + winPath.slice(2).replace(/\\/g, '/')
    : winPath
  // T8：open3d_check.py 已随导出域移入 runtime/py/export/；ext/ 只留 venv 与 gltf_validate_runner.mjs。
  const runner = op === 'gltf_validate' ? gltfRunner : open3dRunner
  // 后端跑在 WSL 时 node 看到的是 Linux 文件系统：**先给 WSL 路径**；在 Windows 时反过来。
  const isWin = process.platform === 'win32'
  const first = isWin ? winPath : wslPath
  const second = isWin ? wslPath : winPath
  const cands: string[][] =
    op === 'gltf_validate'
      ? [
          ['node', runner, first],
          ['node', runner, second],
          ['wsl.exe', '-e', 'node', runner, wslPath],
        ]
      : [
          [path.join(extDir, 'venv', 'bin', 'python'), runner, first],
          [path.join(extDir, 'venv', 'bin', 'python'), runner, second],
          ['wsl.exe', '-e', path.join(extDir, 'venv', 'bin', 'python'), runner, wslPath],
          [path.join(extDir, 'venv', 'Scripts', 'python.exe'), runner, winPath],
        ]
  const tried: Array<Record<string, unknown>> = []
  let raw: string | null = null,
    used: string | null = null
  for (const c of cands) {
    try {
      const r = cp.spawnSync(c[0], c.slice(1), {
        encoding: 'utf8',
        timeout: 120000,
        windowsHide: true,
      })
      const out = String((r.stdout || '') + (r.stderr || ''))
      tried.push({
        cmd: c.slice(0, 2).join(' '),
        status: r.status,
        err: r.error && r.error.message ? String(r.error.message).slice(0, 80) : null,
      })
      const m = out.match(/^(RAW|EXT) (.+)$/m)
      if (m) {
        raw = m[2]
        used = c.slice(0, 2).join(' ')
        break
      }
    } catch (e) {
      tried.push({ cmd: c.slice(0, 2).join(' '), err: String(errorMessage(e)).slice(0, 80) })
    }
  }
  if (!raw) {
    // 安装提示必须是**可直接粘的绝对路径**：旧版写成相对路径 'dsh-blender-plugin/runtime/ext/venv'，
    // 用户若正好在 runtime/ext 里执行，会再套一层 runtime/ext/dsh-blender-plugin/...（现场踩过）。
    const extAbs = path.resolve(extDir)
    const installCmd =
      op === 'gltf_validate'
        ? 'cd ' + (isWin ? '/d "' : '"') + extAbs + '" && npm i gltf-validator'
        : isWin
          ? 'cd /d "' + extAbs + '" && py -3 -m venv venv && venv\\Scripts\\pip install open3d'
          : 'cd "' + extAbs + '" && python3 -m venv venv && venv/bin/pip install open3d'
    return {
      ok: false,
      installed: false,
      path: winPath,
      tried: tried,
      install: installCmd,
      hint:
        '依赖必须装在 ' +
        extAbs +
        ' 下（上面那条自带绝对路径与 cd，在哪个目录执行都一样）；' +
        '两种解释器都试过了 —— 后端在 Windows 时会用 wsl.exe 调 Linux venv',
    }
  }
  let parsed: Record<string, unknown> | null = null
  try {
    parsed = JSON.parse(raw) as Record<string, unknown>
  } catch (e) {
    parsed = { parse_error: String(e).slice(0, 120), raw: raw.slice(0, 400) }
  }
  if (op === 'gltf_validate') {
    const iss = ((parsed && parsed.issues) || {}) as Record<string, unknown>
    const rawMsgs = Array.isArray(iss.messages) ? iss.messages : []
    const msgs = rawMsgs.slice(0, 12).map((m: unknown) => {
      const mm = m as Record<string, unknown>
      return {
        code: mm.code,
        severity: mm.severity,
        pointer: mm.pointer,
        message: String(mm.message || '').slice(0, 160),
      }
    })
    return {
      ok: Number(iss.numErrors) === 0 && !!(parsed && parsed.issues),
      tool: 'gltf-validator',
      validatorVersion: parsed && parsed.validatorVersion,
      path: winPath,
      errors: iss.numErrors,
      warnings: iss.numWarnings,
      infos: iss.numInfos,
      hints: iss.numHints,
      messages: msgs,
      used: used,
      info:
        parsed && parsed.info
          ? (() => {
              const pi = parsed.info as Record<string, unknown>
              return {
                version: pi.version,
                generator: pi.generator,
                drawCallCount: pi.drawCallCount,
                totalVertexCount: pi.totalVertexCount,
                totalTriangleCount: pi.totalTriangleCount,
                maxUVs: pi.maxUVs,
              }
            })()
          : null,
      note: '规则码（messages[].code）可直接写进 gate spec 的 pass_if；numErrors==0 才算过',
    }
  }
  return Object.assign({ tool: 'open3d', used: used, tried: tried }, parsed || {})
}
