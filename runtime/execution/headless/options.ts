import fs from 'node:fs'
import { winToWsl, wslToWin } from '../../config.js'
import { GPU_PRELUDE, WIN_TMP, pyModuleFile } from '../../paths.js'
import { describeMissingInput, validateFileOpt } from '../../file_guard.js'
import { fileFingerprint } from '../../core/scripts.js'
import { errorMessage } from '../../core/json.js'
import { slipFixPath } from '../../core/output.js'
import { PLUGIN_VERSION } from '../../core/evidence.js'
import type { HeadlessDeps, HeadlessOpts, PreparedHeadless, ScriptFileInfo } from './types.js'

/**
 * Normalize the request into the values the script builder and process runner need.
 * This intentionally stops before any Blender process or receipt is created.
 */
export function prepareHeadlessOptions(opts: HeadlessOpts, deps: HeadlessDeps): PreparedHeadless {
  // ---- v0.9.3（D4）：outdir 接受 WSL 路径（/home/…、/mnt/d/…）与 Windows 路径。
  // 旧版把入参**原样**塞给 Windows 的 blender.exe → '/home/x' 被当相对盘根，静默写到 C:\home\x。
  // 现在：Node 侧用 outdirWsl 读写、Blender 侧用 outdirWin（/home/… → \\wsl.localhost\<distro>\home\…）。
  const outdirArg = opts.outdir ? slipFixPath(String(opts.outdir), null) : null
  const outdirWsl = outdirArg ? winToWsl(outdirArg) : null
  const outdirWin = outdirArg ? wslToWin(outdirArg) : WIN_TMP
  // v0.9.1（93-B3）：scriptFile= 显式收 .py；file= 传 .py 时自动改当脚本（老语义 file= 仍是 .blend）
  let scriptFileInfo: ScriptFileInfo | null = null
  let script = opts.script ? String(opts.script) : ''
  const fileOpt = validateFileOpt(opts.file) // F-09：空串/类型错显式报错（直连 /headless 的兜底；工具端另有 ALIAS 拦截）
  const fileLooksPython = !!(fileOpt && /\.py$/i.test(String(fileOpt)))
  if (opts.scriptFile || fileLooksPython) {
    const given = String(opts.scriptFile || fileOpt)
    try {
      const rp = deps.readScriptPath(given)
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
        {
          hint: 'scriptFile 用 .py 路径（Windows D:\\… 或 WSL /home/… 都行）；file= 是 .blend',
        },
      )
    }
  }
  // ---- v0.9.3（F6）：inputFile 指纹 —— 探针跑在 02:55 版、04:07 才发现文件已被重存（浪费 4 次探针）
  // （必须在 fileLooksPython 之后：否则 TDZ ReferenceError —— 实测踩过）
  const inputFileInfo =
    fileOpt && !fileLooksPython
      ? fileFingerprint(wslToWin(String(fileOpt)), winToWsl(String(fileOpt)))
      : describeMissingInput(fileOpt, fileLooksPython) // F-09：null → {given,opened,reason}（形态冻结：truthy 分支一字未动）
  // preload: 把 runtime 里的 python 模块（view/perf/runner/contract/planner...）源码拼进脚本开头
  const mods: unknown[] = Array.isArray(opts.preload)
    ? opts.preload
    : opts.preload
      ? String(opts.preload).split(',')
      : []
  // ---- v0.9.3（F1）：shots=[…] 多视角渲染一体化 —— 自动带上 qc_render（渲染 harness 就在里面）
  const shotsSpec: Record<string, unknown> | null = Array.isArray(opts.shots)
    ? { views: opts.shots }
    : opts.shots && typeof opts.shots === 'object'
      ? Object.assign({}, opts.shots as Record<string, unknown>)
      : null
  const shotsWanted = !!(shotsSpec && Array.isArray(shotsSpec.views) && shotsSpec.views.length)
  if (shotsWanted && !mods.some((m) => String(m).trim().replace(/\.py$/, '') === 'qc_render'))
    mods.push('qc_render')
  let pre = ''
  for (const m of mods) {
    const name = String(m).trim().replace(/\.py$/, '')
    if (!name) continue
    // T8：同作业层，取 runtime/py/<domain>/ 的真实文件（旧路径是 shim）。
    const f = pyModuleFile(name)
    if (!fs.existsSync(f)) throw new Error('preload 找不到模块：' + name + '（' + f + '）')
    pre += deps.preloadChunk(name) + '\n'
  }
  // ---- 引擎前导（v0.8.0）：默认 eevee + 光追；可选 cycles / keep（向后兼容 gpu:"false"=keep）
  const engineMode = String(
    opts.engine === undefined || opts.engine === null ? 'eevee' : opts.engine,
  ).toLowerCase()
  const legacyGpu = String(
    opts.gpu === undefined || opts.gpu === null ? '' : opts.gpu,
  ).toLowerCase()
  const gpuOff =
    engineMode === 'keep' ||
    engineMode === 'off' ||
    engineMode === 'none' ||
    legacyGpu === 'false' ||
    legacyGpu === 'off' ||
    legacyGpu === '0'
  const gpuManual = legacyGpu === 'true' || legacyGpu === 'required'
  let gpuPre = ''
  if (!gpuOff) {
    const modeLit = engineMode === 'cycles' || engineMode === 'cycle' ? 'cycles' : 'eevee'
    gpuPre =
      '# ---- DSH engine prelude (' +
      modeLit +
      ') ----\n' +
      GPU_PRELUDE.replace(/__DSH_ENGINE__/g, "'" + modeLit + "'").replace(
        /__DSH_GPU_MANUAL__/g,
        gpuManual ? 'True' : 'False',
      ) +
      '\n'
  }
  // ---- v0.9.3（D1）：**runId 先定**（客户端可以自带一个 id）—— 客户端等待窗口到点时要能立刻报出
  // "用这个 id 去 collect"，所以 id 必须在发请求前就存在，不能等服务端生成完再回传。
  const runId = /^[A-Za-z0-9_-]{1,64}$/.test(String(opts.runId || ''))
    ? String(opts.runId)
    : 'run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5)
  const envPairs: Record<string, string | null> = {
    DSH_RUN_ID: runId,
    DSH_OUTDIR: outdirWin,
    DSH_SESSION: deps.SESSION_NAME,
    DSH_PLUGIN_VERSION: PLUGIN_VERSION,
    DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []),
  }
  const envOpt: Record<string, unknown> | null =
    opts.env && typeof opts.env === 'object' ? (opts.env as Record<string, unknown>) : null
  if (envOpt) for (const k of Object.keys(envOpt)) envPairs[String(k)] = String(envOpt[k])
  if (scriptFileInfo) envPairs.DSH_SCRIPT_FILE = scriptFileInfo.resolved // 反馈 #4
  // v0.8.10（B2）：expect 前后哨兵 —— "build 返回空却不抛异常"必须被判失败
  const exp: Record<string, unknown> | null =
    opts.expect && typeof opts.expect === 'object' ? (opts.expect as Record<string, unknown>) : null
  const timeoutMs = Math.max(1000, Math.min(1800000, Number(opts.timeoutMs) || 180000))
  return {
    opts,
    outdirArg,
    outdirWsl,
    outdirWin,
    scriptFileInfo,
    fileOpt,
    fileLooksPython,
    inputFileInfo,
    script,
    mods,
    shotsSpec,
    shotsWanted,
    engineMode,
    gpuOff,
    gpuManual,
    runId,
    envPairs,
    envOpt,
    exp,
    pre,
    gpuPre,
    timeoutMs,
  }
}
