// Port and process-owner primitives. Shared by the HTTP lifecycle,
// the host process scan and tests; the Windows branch delegates to win_proc.
import fs from 'node:fs'
import { winKillPids, winPortListenerPids, winSamePortHolders } from './win_proc.js'

export interface ProcRow {
  pid: number
  toks: string[]
}

/** Parse an explicit CLI port flag (argv token, not a substring).
 * Keeps the pre-F1.3 semantics exactly: when the flag is present, the next
 * token is passed through Number() even if it is NaN/0/negative/fractional;
 * only a missing flag returns null (and lets the caller fall back to env/CFG). */
export function parsePortArg(argv: readonly string[], flag = '--port'): number | null {
  const i = argv.indexOf(flag)
  if (i < 0) return null
  return Number(argv[i + 1])
}

/** Pure argv-token matcher for the /proc rows (also used by unit tests). */
export function serverPidsFromProcRows(
  rows: readonly ProcRow[],
  port: unknown,
  selfPid = process.pid,
): number[] {
  const out: number[] = []
  const wantPort = String(port)
  for (const r of rows) {
    if (r.pid === selfPid) continue
    let isServer = false
    let portOk = false
    let isShell = false
    for (let i = 0; i < r.toks.length; i++) {
      if (i === 0 && /(^|\/)(bash|sh|zsh|dash|fish|bwrap)$/.test(r.toks[i])) isShell = true
      if (i >= 1 && /(^|\/)gateway\.(mjs|js)$/.test(r.toks[i])) isServer = true
      if (r.toks[i] === '--port' && r.toks[i + 1] === wantPort) portOk = true
    }
    if (isServer && portOk && !isShell) out.push(r.pid)
  }
  return out
}

/** Read /proc/pid/cmdline rows; [] on platforms without /proc. */
export function readProcRows(): ProcRow[] {
  const rows: ProcRow[] = []
  try {
    for (const d of fs.readdirSync('/proc')) {
      if (!/^[0-9]+$/.test(d) || Number(d) === process.pid) continue
      try {
        const toks = fs
          .readFileSync('/proc/' + d + '/cmdline', 'utf8')
          .split(String.fromCharCode(0))
          .filter(Boolean)
        rows.push({ pid: Number(d), toks })
      } catch {
        /* process exited */
      }
    }
  } catch {
    /* no /proc (Windows/macOS) */
  }
  return rows
}

/** PIDs occupying the port in this plugin's process family. */
export function samePortHolders(port: unknown): number[] {
  const found = serverPidsFromProcRows(readProcRows(), port)
  if (!found.length && process.platform === 'win32') {
    try {
      return winSamePortHolders(port)
    } catch {
      /* probe failed: do not steal */
    }
  }
  return found
}

/** Kill the holders; Windows goes through taskkill /T /F, POSIX uses a signal. */
export function killHolders(port: unknown, signal: NodeJS.Signals | number): number[] {
  const holders = samePortHolders(port)
  if (process.platform === 'win32') {
    try {
      return winKillPids(holders).map(Number)
    } catch {
      return []
    }
  }
  const killed: number[] = []
  for (const pid of holders) {
    try {
      process.kill(pid, signal)
      killed.push(pid)
    } catch {
      /* already gone */
    }
  }
  return killed
}

/** Pure parser for /proc/net/tcp{,6}: LISTEN rows with HEX local port -> socket inode. */
export function parseProcNetTcpListeners(
  text: string | null | undefined,
  port: unknown,
): Array<{ inode: number }> {
  const want = Number(port)
  const out: Array<{ inode: number }> = []
  if (!Number.isInteger(want) || want <= 0) return out
  for (const raw of String(text == null ? '' : text)
    .split(/\r?\n/)
    .slice(1)) {
    const cols = raw.trim().split(/\s+/)
    if (cols.length < 10) continue
    const local = /^[0-9A-Fa-f]+:([0-9A-Fa-f]{4})$/.exec(cols[1] || '')
    if (!local || parseInt(local[1], 16) !== want) continue
    if ((cols[3] || '').toUpperCase() !== '0A') continue
    const inode = Number(cols[9])
    if (Number.isInteger(inode) && inode > 0) out.push({ inode })
  }
  return out
}

function linuxPortListenerPids(port: unknown): number[] {
  let rows: Array<{ inode: number }> = []
  for (const file of ['/proc/net/tcp', '/proc/net/tcp6']) {
    try {
      rows = rows.concat(parseProcNetTcpListeners(fs.readFileSync(file, 'utf8'), port))
    } catch {
      /* no rows */
    }
  }
  const inodes = new Set(rows.map((row) => row.inode))
  if (!inodes.size) return []
  const pids: number[] = []
  let entries: string[]
  try {
    entries = fs.readdirSync('/proc')
  } catch {
    return []
  }
  for (const dir of entries) {
    if (!/^[0-9]+$/.test(dir)) continue
    const pid = Number(dir)
    if (pid === process.pid) continue
    let fds: string[]
    try {
      fds = fs.readdirSync('/proc/' + dir + '/fd')
    } catch {
      continue
    }
    let hit = false
    for (const fd of fds) {
      try {
        const target = fs.readlinkSync('/proc/' + dir + '/fd/' + fd)
        const m = /^socket:\[(\d+)\]$/.exec(target)
        if (m && inodes.has(Number(m[1]))) {
          hit = true
          break
        }
      } catch {
        /* fd closed */
      }
    }
    if (hit) pids.push(pid)
  }
  return [...new Set(pids)].sort((a, b) => a - b)
}

/** Any process listening on the port (not filtered to this plugin's family). */
export function portListenerPids(port: unknown): number[] {
  if (process.platform === 'win32') {
    try {
      return winPortListenerPids(port)
    } catch {
      /* fall through */
    }
  }
  if (process.platform === 'linux') {
    const found = linuxPortListenerPids(port)
    if (found.length) return found
  }
  return samePortHolders(port)
}

/** True when the holder still answers /health (busy, not stale). */
export async function holderResponsive(port: unknown, ms = 1200): Promise<boolean> {
  const ac = new AbortController()
  const t = setTimeout(() => ac.abort(), ms)
  try {
    return (await fetch('http://127.0.0.1:' + String(port) + '/health', { signal: ac.signal })).ok
  } catch {
    return false
  } finally {
    clearTimeout(t)
  }
}
