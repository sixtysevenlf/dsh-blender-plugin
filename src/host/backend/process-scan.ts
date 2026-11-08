/** host/backend/process-scan：跨平台"按命令行找本插件历史后端并停掉"（热重载兜底）。 */
import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import { IS_WIN } from '../../constants.js'
import { serverPidsFromProcRows, winKillPids, winSamePortHolders } from '../runtime-bridge.js'

/**
 * 按 /proc 扫出"本插件历史代次拉起的后端"，按 signal 停掉（默认 SIGTERM）。
 * 热重载会丢掉 child 句柄（上一代 fiber 持有），只靠句柄停不掉残留进程 —— 这是兜底。
 * v1.0.1：新增 signal 参数（事件循环被同步操作占死的进程只认 SIGKILL）。
 */
export function killBackendByCmd(port: number, signal: NodeJS.Signals = 'SIGTERM'): string[] {
  const killed: string[] = []
  // issue #51（win32）：下面两条候选链（/proc、ps -ax）在 Windows 上都不存在 —— 原先一路
  // 静默 catch 后返回 []，于是 op=stop / op=restart 之后僵尸后端仍占着端口，永远清不掉。
  // 改走 win_proc：先按命令行归属（gateway.js + --port 精确匹配），再按"端口监听者 ∩ 同名映像"兜底。
  if (IS_WIN) {
    try {
      return winKillPids(winSamePortHolders(port))
    } catch {
      return []
    }
  }
  // macOS 支持：把「列进程」抽成一条可移植的候选链 —— /proc 只有 Linux 有，
  // macOS 没有；原先只读 /proc，在 mac 上静默返回 []（热重载后残留后端停不掉）。
  const rows: Array<{ pid: number; toks: string[] }> = []
  let listed = false
  // ① Linux：直接读 /proc（最准，无外部依赖）
  try {
    for (const d of readdirSync('/proc')) {
      if (!/^[0-9]+$/.test(d)) continue
      if (Number(d) === process.pid) continue
      try {
        const toks = readFileSync('/proc/' + d + '/cmdline', 'utf8')
          .split(String.fromCharCode(0))
          .filter(Boolean)
        rows.push({ pid: Number(d), toks })
      } catch {
        /* 进程可能已退出 */
      }
    }
    listed = true
  } catch {
    /* 无 /proc（macOS/Windows）→ 走 ② */
  }
  // ② macOS / 通用 POSIX：ps 输出 pid + 完整命令行
  if (!listed) {
    try {
      for (const line of execFileSync('ps', ['-ax', '-o', 'pid=,command='], {
        encoding: 'utf8',
        timeout: 5000,
      }).split(String.fromCharCode(10))) {
        const m = /^\s*(\d+)\s+(.*)$/.exec(line)
        if (!m) continue
        const pid = Number(m[1])
        if (pid === process.pid) continue
        rows.push({ pid, toks: m[2].trim().split(/\s+/) })
      }
    } catch {
      /* ps 不可用则不兜底 */
    }
  }
  for (const pid of serverPidsFromProcRows(rows, port)) {
    try {
      process.kill(pid, signal)
      killed.push(String(pid))
    } catch {
      /* already gone */
    }
  }
  return killed
}
