import { PLUGIN_VERSION } from '../../core/evidence.js'
import { USER_CONFIG_WIN, USER_SCRIPTS_WIN } from '../../paths.js'
import { baseChildEnv, withWslEnv } from '../../proc_env.js'
import type { HeadlessOpts, PreparedHeadless } from './types.js'

/** Build the child environment, including explicit env, WSL bridging and optional user Blender config. */
export function buildChildEnv(
  p: PreparedHeadless,
  opts: HeadlessOpts,
  sessionName: string,
): Record<string, string | undefined> {
  const childEnv = baseChildEnv({
    DSH_RUN_ID: p.runId,
    DSH_OUTDIR: p.outdirWin,
    DSH_ARGS: JSON.stringify(Array.isArray(opts.args) ? opts.args.map(String) : []),
    DSH_SESSION: sessionName,
    DSH_PLUGIN_VERSION: PLUGIN_VERSION,
  })
  if (p.envOpt) {
    for (const k of Object.keys(p.envOpt)) childEnv[String(k)] = String(p.envOpt[k])
  }
  // ⚠ 实测坑（v0.9.1）：从 WSL 侧 spawn Windows 的 blender.exe 时，**env 不会自动跨界** ——
  // Node 把 DSH_* 传给了 spawn，但子进程 os.environ 里一个都看不到（要靠 WSLENV 声明）。
  // 这里自动声明；同时脚本里还会再注入一次（双保险，见 DSH_ENV_PRELUDE）。
  withWslEnv(childEnv, p.envOpt || undefined)
  if (opts.useUserConfig) {
    if (USER_CONFIG_WIN) childEnv.BLENDER_USER_CONFIG = USER_CONFIG_WIN
    if (USER_SCRIPTS_WIN) childEnv.BLENDER_USER_SCRIPTS = USER_SCRIPTS_WIN
  }
  return childEnv
}
