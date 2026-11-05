// headless 选项入口（拆分）：路径 / 前导 / 环境 / 时限
import { PLUGIN_VERSION } from '../../../core/evidence.js'
import type { HeadlessDeps, HeadlessOpts, PreparedHeadless } from '../types.js'
import { resolveHeadlessPaths } from './paths.js'
import { buildEnginePrelude, buildHeadlessPreload } from './gpu.js'

export function prepareHeadlessOptions(opts: HeadlessOpts, deps: HeadlessDeps): PreparedHeadless {
  const {
    outdirArg,
    outdirWsl,
    outdirWin,
    scriptFileInfo,
    fileOpt,
    fileLooksPython,
    inputFileInfo,
    script,
  } = resolveHeadlessPaths(opts, deps)
  const { mods, pre, shotsSpec, shotsWanted } = buildHeadlessPreload(opts, deps)
  const { engineMode, gpuOff, gpuManual, gpuPre } = buildEnginePrelude(opts)
  // ---- v0.9.3：**runId 先定**（客户端可以自带一个 id）—— 客户端等待窗口到点时要能立刻报出
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
  // v0.8.10：expect 前后哨兵 —— "build 返回空却不抛异常"必须被判失败
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
