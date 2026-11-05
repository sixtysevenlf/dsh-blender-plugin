// 启动子进程计划与 detached spawn
import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import { wslToWin } from '../config.js'
import { USER_CONFIG_WIN, USER_SCRIPTS_WIN } from '../paths.js'
import { writeHeadlessScript } from '../staging.js'
import { baseChildEnv, withWslEnv } from '../proc_env.js'
import { LAUNCH_BOOT_PY } from './resolve.js'
import type { LaunchOpts } from './resolve.js'

export interface LaunchPlan {
  sp: ReturnType<typeof writeHeadlessScript>
  args: string[]
  childEnv: Record<string, string | undefined>
}

/** 写启动脚本、拼参数与子进程环境（dryRun 也要先拿到这些） */
export function buildLaunchPlan(opts: LaunchOpts, statusWsl: string): LaunchPlan {
  const sp = writeHeadlessScript(LAUNCH_BOOT_PY)
  const args = ['--python', sp.win]
  if (opts.file) args.push(wslToWin(String(opts.file)))
  const childEnv = withWslEnv(baseChildEnv({})) as Record<string, string | undefined>
  if (USER_SCRIPTS_WIN) childEnv.BLENDER_USER_SCRIPTS = USER_SCRIPTS_WIN
  if (USER_CONFIG_WIN) childEnv.BLENDER_USER_CONFIG = USER_CONFIG_WIN
  if (opts.addonFile) childEnv.DSH_BLENDER_ADDON_FILE = String(opts.addonFile)
  if (opts.addonModule) childEnv.DSH_BLENDER_ADDON_MODULE = String(opts.addonModule)
  childEnv.DSH_BLENDER_LAUNCH_STATUS = wslToWin(statusWsl)
  try {
    fs.rmSync(statusWsl, { force: true })
  } catch (e) {
    /* 旧状态删不掉也无所谓 */
  }
  return { sp, args, childEnv }
}

/** detached spawn；spawn 不存在的可执行文件不会抛，而是异步 emit 'error'（不挂 handler 会打挂后端） */
export function spawnDetachedBlender(
  exe: string,
  args: string[],
  childEnv: Record<string, string | undefined>,
): { child: ChildProcess | null; spawnErrRef: { value: unknown } } {
  const spawnErrRef: { value: unknown } = { value: null }
  let child: ChildProcess | null = null
  try {
    child = spawn(exe, args, { env: childEnv, stdio: 'ignore', detached: true })
  } catch (e) {
    spawnErrRef.value = e
  }
  if (child) {
    // ⚠ 实测坑（v0.9.4 自检抓到）：spawn 不存在的可执行文件**不会抛**，而是异步 emit 'error'。
    child.on('error', (e: Error) => {
      spawnErrRef.value = e
    })
    try {
      child.unref()
    } catch (e) {
      /* ignore */
    }
  } else if (!spawnErrRef.value) {
    spawnErrRef.value = new Error('spawn 返回空句柄')
  }
  return { child, spawnErrRef }
}
