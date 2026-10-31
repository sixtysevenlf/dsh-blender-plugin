import path from 'node:path'
import fs from 'node:fs'
import { winToWsl, wslToWin } from '../../config.js'
import { GPU_PRELUDE, WIN_TMP, pyModuleFile, pyModuleNames } from '../../paths.js'
import { PLUGIN_VERSION } from '../../core/evidence.js'
import { slipFixPath } from '../../core/output.js'
import { describeMissingInput, validateFileOpt } from '../../file_guard.js'
import {
  PATH_GUARD,
  envPrelude,
  fileFingerprint,
  scriptFileBlock,
  shotsBlock,
  workdirBlock,
} from '../../core/scripts.js'
import { KERNEL_BOOTSTRAP } from '../../core/kernel.js'
import { hardExitJobParts } from '../../hard_exit.js'
import { writeHeadlessScript } from '../../staging.js'
import { errorMessage } from '../../core/json.js'
import type { JobsDeps } from './types.js'
import { jobDir } from './snapshot.js'

interface ScriptFileInfo {
  given: string
  resolved: string
  bytes: number
  auto_from_file: boolean
}

export interface PreparedJob {
  id: string
  outdirWin: string
  outdirWsl: string
  dir: string
  outPath: string
  errPath: string
  engineMode: string
  runId: string
  scriptFileInfo: ScriptFileInfo | null
  inputFileInfo: unknown
  fileOpt: string | undefined
  fileLooksPython: boolean
  args: string[]
  sp: ReturnType<typeof writeHeadlessScript>
  timeoutMs: number
  envPairs: Record<string, string | null>
}

export function buildJobScript(E: JobsDeps, opts: Record<string, unknown> = {}): PreparedJob {
  const id = 'job-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5)
  const outdirArg = opts.outdir ? slipFixPath(String(opts.outdir), null) : null
  const outdirWsl = outdirArg ? winToWsl(outdirArg) : winToWsl(WIN_TMP)
  const outdirWin = outdirArg ? wslToWin(outdirArg) : WIN_TMP
  const dir = jobDir(id, outdirWsl)
  const outPath = path.join(dir, 'stdout.log'),
    errPath = path.join(dir, 'stderr.log')
  const engineMode = String(
    opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine,
  ).toLowerCase()
  const runId = /^[A-Za-z0-9_-]{1,64}$/.test(String(opts.runId || ''))
    ? String(opts.runId)
    : 'run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5)
  // ---- script_file（与 headless 同一套语义：显式收 .py；file= 传 .py 自动改当脚本）
  let scriptFileInfo: ScriptFileInfo | null = null
  let script = opts.script ? String(opts.script) : ''
  const fileOpt = validateFileOpt(opts.file) // F-09：空串/类型错显式报错，不再静默当"没传"
  const fileLooksPython = !!(fileOpt && /\.py$/i.test(String(fileOpt)))
  if (opts.scriptFile || fileLooksPython) {
    const given = String(opts.scriptFile || fileOpt)
    try {
      const rp = E.readScriptPath(given)
      script = fs.readFileSync(rp.wsl, 'utf8')
      scriptFileInfo = {
        given: given,
        resolved: rp.win,
        bytes: script.length,
        auto_from_file: !opts.scriptFile && fileLooksPython,
      }
    } catch (e) {
      throw Object.assign(
        new Error('读不到脚本文件：' + given + ' —— ' + errorMessage(e).slice(0, 160)),
        { hint: 'script_file 用 .py 路径（Windows D:\\… 或 WSL /home/… 都行）；file= 是 .blend' },
      )
    }
  }
  const inputFileInfo =
    fileOpt && !fileLooksPython
      ? fileFingerprint(wslToWin(String(fileOpt)), winToWsl(String(fileOpt)))
      : describeMissingInput(fileOpt, fileLooksPython) // F-09：null → {given,opened,reason}（形态冻结：truthy 分支一字未动）
  const args: string[] = ['-b']
  if (fileOpt && !fileLooksPython) args.push(wslToWin(String(fileOpt)))
  if (opts.factoryStartup !== false) args.push('--factory-startup')
  const envPairs: Record<string, string | null> = {
    DSH_RUN_ID: runId,
    DSH_OUTDIR: outdirWin,
    DSH_SESSION: E.SESSION_NAME,
    DSH_PLUGIN_VERSION: PLUGIN_VERSION,
    DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []),
  }
  if (opts.env && typeof opts.env === 'object') {
    const envIn = opts.env as Record<string, unknown>
    for (const k of Object.keys(envIn)) envPairs[String(k)] = String(envIn[k])
  }
  if (scriptFileInfo) envPairs.DSH_SCRIPT_FILE = scriptFileInfo.resolved // 反馈 #4
  const parts = [envPrelude(envPairs)]
  if (opts.bootstrap !== false) parts.push(KERNEL_BOOTSTRAP)
  parts.push('_dsh_stage_emit("engine-prelude")')
  parts.push(
    [
      'try:',
      '    _DSH_FP0 = bpy.context.scene.render.filepath',
      'except Exception:',
      '    _DSH_FP0 = None',
    ].join(String.fromCharCode(10)),
  )
  if (engineMode !== 'keep')
    parts.push(
      GPU_PRELUDE.replace(
        /__DSH_ENGINE__/g,
        "'" + (engineMode === 'cycles' ? 'cycles' : 'eevee') + "'",
      ).replace(/__DSH_GPU_MANUAL__/g, 'False'),
    )
  // v0.8.9：作业层也支持 preload（与 headless 同一套：把 runtime/<name>.py 源码拼到脚本开头）
  const jobMods = Array.isArray(opts.preload)
    ? opts.preload
    : opts.preload
      ? String(opts.preload).split(',')
      : []
  // v0.9.3（F1）：作业层同样支持 shots（as_job=true 时 headless 就是转到这里跑的）
  const jobShotsSpec: Record<string, unknown> | null = Array.isArray(opts.shots)
    ? { views: opts.shots }
    : opts.shots && typeof opts.shots === 'object'
      ? Object.assign({}, opts.shots as Record<string, unknown>)
      : null
  const jobShotsWanted = !!(
    jobShotsSpec &&
    Array.isArray(jobShotsSpec.views) &&
    jobShotsSpec.views.length
  )
  if (jobShotsWanted && !jobMods.some((m) => String(m).trim().replace(/\.py$/, '') === 'qc_render'))
    jobMods.push('qc_render')
  for (const m of jobMods) {
    const name = String(m).trim().replace(/\.py$/, '')
    if (!name) continue
    // T8：模块真实文件在 runtime/py/<domain>/ 下；旧 runtime/<name>.py 只是 re-export shim，
    // 注入通道必须是真实文件，否则 exec 出去的只是 import 语句。
    let f = null
    try {
      f = pyModuleFile(name)
    } catch (e2) {
      f = null
    }
    if (!f || !fs.existsSync(f)) {
      // S9（④）：报错直接给可用模块名 —— 现场「要 preload=vehicle」只能靠试。
      throw new Error(
        'preload 找不到模块：' +
          name +
          '（可用模块名：' +
          pyModuleNames().join(', ') +
          '）—— preload 取模块名（runtime/py/<domain>/<name>.py，旧 runtime/<name>.py 是兼容 shim），例如 preload="vehicle,audit"；离线（后端挂了）见 runtime/py/core/offline_bootstrap.py',
      )
    }
    parts.push(E.preloadChunk(name))
  }
  parts.push('_dsh_stage_emit("preload-done")')
  if (opts.workdir) parts.push(workdirBlock(slipFixPath(String(opts.workdir), null)))
  if (scriptFileInfo) parts.push(scriptFileBlock(scriptFileInfo.resolved))
  parts.push(script)
  parts.push('_dsh_stage_emit("script-end")')
  if (jobShotsWanted)
    parts.push(
      shotsBlock(
        Object.assign({ engine: 'keep', lock: true, warmup: true, tag: 'shot' }, jobShotsSpec, {
          outdir: outdirWin,
        }),
      ),
    )
  parts.push(PATH_GUARD)
  parts.push('_dsh_stage_emit("done")')
  // F-14：done 之后冲刷 stdout/stderr 再 os._exit(0)（跳过 Blender 退出拆解；hardExit=false 回旧行为）
  for (const hx of hardExitJobParts(opts)) parts.push(hx)
  const sp = writeHeadlessScript(parts.join('\n'))
  args.push('--python', sp.win, '--')
  if (opts.outdir) args.push(outdirWin)
  if (Array.isArray(opts.args)) for (const a of opts.args) args.push(String(a))
  const timeoutMs = Math.max(1000, Math.min(86400000, Number(opts.timeoutMs) || 3600000))
  return {
    id: id,
    outdirWin: outdirWin,
    outdirWsl: outdirWsl,
    dir: dir,
    outPath: outPath,
    errPath: errPath,
    engineMode: engineMode,
    runId: runId,
    scriptFileInfo: scriptFileInfo,
    inputFileInfo: inputFileInfo,
    fileOpt: fileOpt,
    fileLooksPython: fileLooksPython,
    args: args,
    sp: sp,
    timeoutMs: timeoutMs,
    envPairs: envPairs,
  }
}
