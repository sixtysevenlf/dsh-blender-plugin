/**
 * runtime/win_proc.ts —— Windows 上的「进程 / 端口占用」原语（issue #51）。
 *
 * 背景：残留后端的清理原先只认 POSIX —— Linux 读 /proc，macOS 走 `ps -ax`。
 * Windows 两样都没有，于是 `killBackendByCmd`（host 侧 stop/restart）与
 * `samePortHolders`（后端 EADDRINUSE 自愈）在 win32 上**静默返回 []**：
 * op=stop / op=restart 之后，僵尸后端仍占着 39877（或自定义端口），谁都不清。
 *
 * 这里给三条判据，**按可靠性排序**（调用方也按这个顺序用）：
 *   ① 命令行归属（CIM / Win32_Process）：命令行里含 `gateway.js` 且 `--port <n>` 精确匹配
 *      —— 唯一不会误杀的判据，也是 kill 前必须通过的判据；
 *   ② 端口监听者（`netstat -ano`）：拿到占端口的 PID，但**必须**再按映像名过滤；
 *   ③ 进程映像名（`tasklist`）：给 ② 做归属过滤 —— 只认与当前进程**同名**的可执行体
 *      （win32 后端就是用 process.execPath 拉起来的），避免把占了同名端口的别的程序杀掉。
 *
 * 约定：非 Windows 平台一律返回空数组（调用方保留各自的 POSIX 路径）；
 *       所有子进程调用都带 `windowsHide: true`（issue #38 / #43 同族口径）；
 *       解析器是纯函数，单测不需要 Windows（tests/unit/js/win-spawn.test.ts）。
 */
import { execFileSync } from 'node:child_process'
import path from 'node:path'

export const IS_WIN = process.platform === 'win32'

export interface WinProcOptions {
  timeoutMs?: number
}

function timeoutOption(value: unknown, fallback: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Number(value) || fallback))
}

/**
 * 解析 `netstat -ano -p TCP` 的 LISTENING 行，返回占着目标端口的 PID（去重、升序）。
 *
 * 真实输出（Windows 10/11，列分隔为若干空格）：
 *   TCP    0.0.0.0:39877     0.0.0.0:0      LISTENING     1234
 *   TCP    [::]:39877        [::]:0         LISTENING     5678
 *   TCP    127.0.0.1:39877   127.0.0.1:5013 ESTABLISHED   1234   ← 不是监听，忽略
 *
 * ★ 只认 LISTENING：ESTABLISHED 行同样带本地端口，拿它当"占用者"会把正常连接的服务端拉进来。
 * ★ 端口必须**精确**匹配：39877 不能命中 3398770 / 139877。
 */
export function parseNetstatListeners(text: string | null | undefined, port: unknown): number[] {
  const want = Number(port)
  const pids = new Set<number>()
  if (!Number.isInteger(want) || want <= 0) return []
  for (const rawLine of String(text == null ? '' : text).split(/\r?\n/)) {
    const cols = rawLine.trim().split(/\s+/)
    if (cols.length < 5) continue
    if (!/^TCP$/i.test(cols[0])) continue
    if (!/^LISTENING$/i.test(cols[3])) continue
    const m = /:(\d+)$/.exec(cols[1]) // 同时吃 "0.0.0.0:39877" 与 "[::]:39877"
    if (!m || Number(m[1]) !== want) continue
    const pid = Number(cols[4])
    if (Number.isInteger(pid) && pid > 0 && pid !== process.pid) pids.add(pid)
  }
  return [...pids].sort((a, b) => a - b)
}

/**
 * 解析 `tasklist /FO CSV /NH` 的一行 → 映像名（形如 `"node.exe"` / `"DeepSeek Harness.exe"`）。
 * 解析不出来回 ''（调用方据此保守跳过，宁可不杀也不误杀）。
 */
export function parseTasklistImage(text: string | null | undefined): string {
  const line = String(text == null ? '' : text).split(/\r?\n/)[0] || ''
  const m = /^"([^"]*)"/.exec(line.trim())
  return m ? m[1] : ''
}

/** 某 PID 的映像名（tasklist）。拿不到回 ''。 */
export function tasklistImageName(pid: unknown, timeoutMs = 3000): string {
  if (!IS_WIN) return ''
  const id = Number(pid)
  if (!Number.isInteger(id) || id <= 0) return ''
  try {
    const out = execFileSync('tasklist.exe', ['/FI', 'PID eq ' + String(id), '/FO', 'CSV', '/NH'], {
      encoding: 'utf8',
      timeout: timeoutOption(timeoutMs, 3000, 1000, 15000),
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    })
    return parseTasklistImage(out)
  } catch {
    return ''
  }
}

/**
 * 判据 ①：按**命令行归属**扫出"本插件的后端进程"（CIM / Win32_Process）。
 * 命令行必须同时满足：独立 argv 里出现 `gateway.js` / `gateway.mjs`、且 `--port <port>` 精确匹配。
 * 与 POSIX 侧同一条自伤教训保持一致（2026-09-29：子串匹配会把调用方自己的 shell 一起杀掉）。
 */
export function winServerPidsByCmdline(port: unknown, opts: WinProcOptions = {}): number[] {
  if (!IS_WIN) return []
  const p = String(Math.trunc(Number(port)))
  if (!/^\d+$/.test(p)) return []
  const timeoutMs = timeoutOption(opts.timeoutMs, 8000, 2000, 15000)
  const script = [
    '$ErrorActionPreference = "SilentlyContinue";',
    'Get-CimInstance Win32_Process |',
    "Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine -match '(gateway\\.(mjs|js))' -and $_.CommandLine -match '--port\\s+" +
      p +
      "(\\s|$)' } |",
    'ForEach-Object { [string]$_.ProcessId }',
  ].join(' ')
  try {
    const out = execFileSync(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', script],
      {
        encoding: 'utf8',
        timeout: timeoutMs,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore'],
      },
    )
    const pids: number[] = []
    for (const line of String(out).split(/\r?\n/)) {
      const n = Number(line.trim())
      if (Number.isInteger(n) && n > 0 && n !== process.pid) pids.push(n)
    }
    return [...new Set(pids)].sort((a, b) => a - b)
  } catch {
    return []
  }
}

/**
 * 判据 ② + ③：占端口的监听者 ∩（映像名 == 当前进程的映像名）。
 * 只在 ① 不可用（PowerShell / CIM 被策略挡住）时兜底；不满足归属判据的 PID 一律不返回。
 */
export function winPortHolderPids(port: unknown, opts: WinProcOptions = {}): number[] {
  if (!IS_WIN) return []
  const timeoutMs = timeoutOption(opts.timeoutMs, 4000, 1000, 10000)
  let text: string
  try {
    text = execFileSync('netstat.exe', ['-ano', '-p', 'TCP'], {
      encoding: 'utf8',
      timeout: timeoutMs,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    })
  } catch {
    return []
  }
  const want = path.basename(process.execPath).toLowerCase()
  const out: number[] = []
  for (const pid of parseNetstatListeners(text, port)) {
    const image = tasklistImageName(pid).toLowerCase()
    if (image && image === want) out.push(pid)
  }
  return out
}

/** 占端口的监听者（任意进程，不做映像名过滤）；用于失败归因，不用于 kill。 */
export function winPortListenerPids(port: unknown, opts: WinProcOptions = {}): number[] {
  if (!IS_WIN) return []
  const timeoutMs = timeoutOption(opts.timeoutMs, 4000, 1000, 10000)
  try {
    const text = execFileSync('netstat.exe', ['-ano', '-p', 'TCP'], {
      encoding: 'utf8',
      timeout: timeoutMs,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    })
    return parseNetstatListeners(text, port)
  } catch {
    return []
  }
}

/** Windows 上"占着本端口的同族后端"：先按命令行归属，再按端口 + 同名映像兜底。 */
export function winSamePortHolders(port: unknown): number[] {
  if (!IS_WIN) return []
  const byCmd = winServerPidsByCmdline(port)
  if (byCmd.length) return byCmd
  return winPortHolderPids(port)
}

/**
 * 用 taskkill 结束进程树。Windows 没有优雅 SIGTERM（process.kill 直接 TerminateProcess），
 * 所以这里统一 `/T /F`：残留后端可能还带着 headless 子进程，只杀父进程会留孤儿。
 * 返回**实际执行过 taskkill 的 PID 字符串**（与 POSIX 侧 killed 数组同口径）。
 */
export function winKillPids(pids: ReadonlyArray<unknown>, opts: WinProcOptions = {}): string[] {
  const out: string[] = []
  if (!IS_WIN) return out
  const timeoutMs = timeoutOption(opts.timeoutMs, 5000, 1000, 15000)
  for (const raw of pids || []) {
    const pid = Number(raw)
    if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) continue
    try {
      execFileSync('taskkill.exe', ['/PID', String(pid), '/T', '/F'], {
        stdio: 'ignore',
        timeout: timeoutMs,
        windowsHide: true,
      })
      out.push(String(pid))
    } catch {
      /* 已退出 / 权限不足：都当"没杀到"，交给上层报错 */
    }
  }
  return out
}
