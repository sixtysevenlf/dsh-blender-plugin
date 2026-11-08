/** host/backend/spawn：子进程句柄与生命周期（issue #51 的 win32 三件套集中在这里）。 */
import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import { SERVER_PATH } from '../../constants.js'
import { winKillPids } from '../runtime-bridge.js'

let child: ChildProcess | null = null

/** 子进程是否真的还活着：被信号杀掉时 exitCode 仍为 null、只有 signalCode 有值 */
export function childAlive(): boolean {
  return !!(child && child.exitCode === null && child.signalCode === null && child.pid)
}

export function spawnChild(port: number): string {
  // issue #51（win32 宿主）：三处都必须显式给，不能靠环境继承。
  //   ① ELECTRON_RUN_AS_NODE：host 跑在 Electron 里时 process.execPath 是 **GUI 可执行体**，
  //      不带这个变量会另起一个 GUI 实例（或直接被单实例锁吞掉），而不是 node 子进程；
  //   ② windowsHide：console-subsystem 可执行体在"宿主无控制台"时会新配一个控制台（#38/#43 同族）；
  //   ③ detached：win32 上等价 DETACHED_PROCESS，宿主退出后后端继续活着（既定语义）。
  const spawned = spawn(process.execPath, [SERVER_PATH, '--port', String(port)], {
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
    stdio: 'ignore',
    detached: true,
    windowsHide: true,
  })
  child = spawned
  // ★ 先挂监听、再 unref：spawn 的失败（EPERM / EACCES / ENOENT）是**异步 'error' 事件**。
  //   没有监听时它会冒到宿主的 uncaughtException —— 现场（#51）表现为 dsh-desktop-host 每 15 s
  //   整个被打死一次（看护间隔 ENSURE_INTERVAL_MS 一到就重试），并且残留后端占着端口。
  //   有监听则只降级为"后端不可用"，宿主照常跑。
  spawned.on('error', (err: Error) => {
    if (child === spawned) child = null
    try {
      console.error('[dsh-blender] 后端子进程启动失败（已降级，宿主不受影响）：' + err.message)
    } catch {
      /* 日志失败不影响主流程 */
    }
  })
  spawned.on('exit', () => {
    if (child === spawned) child = null
  })
  spawned.unref()
  return 'spawned pid=' + String(spawned.pid)
}

/** stale-handle 分支：句柄在、端口不通 → 按平台停掉不可信的旧句柄并返回其 pid。 */
export function killStaleChild(): number | null {
  const stalePid = child?.pid
  if (!stalePid) return null
  try {
    if (process.platform === 'win32') winKillPids([stalePid])
    else process.kill(stalePid, 'SIGTERM')
  } catch {
    /* 已经没了 */
  }
  child = null
  return stalePid
}

export function getChild(): ChildProcess | null {
  return child
}

/** 杀掉并清空当前子进程句柄；返回是否原本有句柄（调用方据此写回执）。 */
export function killChildHandle(): boolean {
  if (!child) return false
  try {
    child.kill()
  } catch {
    /* 已经退出 */
  }
  child = null
  return true
}
