import path from 'node:path'
import fs from 'node:fs'
import { winToWsl, wslToWin } from '../../../config.js'
import { WIN_TMP } from '../../../paths.js'
import { PLUGIN_VERSION } from '../../../core/evidence.js'
import { slipFixPath } from '../../../core/output/index.js'
import { describeMissingInput, validateFileOpt } from '../../../file_guard.js'
import { fileFingerprint } from '../../../core/scripts.js'
import { errorMessage } from '../../../core/json.js'
import { jobDir } from '../snapshot.js'
import type { JobsDeps } from '../types.js'

interface ScriptFileInfo {
  given: string
  resolved: string
  bytes: number
  auto_from_file: boolean
}

export interface JobBuildArgs {
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
  script: string
  args: string[]
  envPairs: Record<string, string | null>
}

export function prepareJobArgs(E: JobsDeps, opts: Record<string, unknown> = {}): JobBuildArgs {
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
  return {
    id,
    outdirWin,
    outdirWsl,
    dir,
    outPath,
    errPath,
    engineMode,
    runId,
    scriptFileInfo,
    inputFileInfo,
    fileOpt,
    fileLooksPython,
    script,
    args,
    envPairs,
  }
}
