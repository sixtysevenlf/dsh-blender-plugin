// Port and process-owner primitives (F1.3). Shared by the HTTP lifecycle,
// the host process scan and tests; the Windows branch delegates to win_proc.
import fs from 'node:fs'
import { winKillPids, winSamePortHolders } from './win_proc.js'

export interface ProcRow {
  pid: number
  toks: string[]
}

export interface PortArgResult {
  port: number | null
  source: 'argv' | 'env' | 'default'
}

/** Parse an explicit CLI port flag (argv token, not a substring). */
export function parsePortArg(argv: readonly string[], flag = '--port'): number | null {
  const i = argv.indexOf(flag)
  if (i < 0) return null
  const n = Number(argv[i + 1])
  return Number.isInteger(n) && n > 0 ? n : null
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
      if (i >= 1 && /(^|\/)server\.(mjs|js)$/.test(r.toks[i])) isServer = true
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
