import { spawn, execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { probeHttp, decideStart } from '../../runtime/backend_probe.mjs'
import { ABORT_HINT, HERE, HOSTNAME, SERVER_PATH } from '../constants.js'
import { execSignal } from './exec-context.js'

/**
 * 端口解析（与 runtime/config.mjs 同一套来源，避免两处配置漂移）：
 *   插件配置 port → DSH_BLENDER_HTTP_PORT → $DSH_BLENDER_CONFIG → <包根>/dsh-blender.config.json
 *   → ~/.dsh/dsh-blender.config.json → 9877
 */
export function resolveHttpPort(): number {
  const env = Number(process.env.DSH_BLENDER_HTTP_PORT)
  if (Number.isFinite(env) && env > 0) return env
  const cands = [
    process.env.DSH_BLENDER_CONFIG,
    path.join(HERE, '..', 'dsh-blender.config.json'),
    path.join(os.homedir(), '.dsh', 'dsh-blender.config.json')
  ].filter(Boolean) as string[]
  for (const p of cands) {
    try {
      const j = JSON.parse(readFileSync(p, 'utf8'))
      const n = Number(j && j.httpPort)
      if (Number.isFinite(n) && n > 0) return n
    } catch (e: any) {
      /* 没这个文件或不是 JSON：跳过 */
    }
  }
  return 9877
}

let child: any = null
/** 用户用 op=stop 显式停过 → 看护不再自动拉起（op=start 解除） */
let paused = false

export function base(port: number): string {
  return 'http://' + HOSTNAME + ':' + String(port)
}
export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export async function probe(port: number, timeoutMs = 1500): Promise<boolean> {
  const ac = new AbortController()
  const timer = setTimeout(() => ac.abort(), timeoutMs)
  try {
    const r = await fetch(base(port) + '/health', { signal: ac.signal })
    return r.ok
  } catch (e: any) {
    return false
  } finally {
    clearTimeout(timer)
  }
}

export async function ensureBackend(port: number): Promise<boolean> {
  if (await probe(port)) return true
  if (paused) return false
  try {
    await startBackend(port)
  } catch (e: any) {
    /* ignore */
  }
  for (let i = 0; i < 25; i++) {
    if (await probe(port)) return true
    await sleep(300)
  }
  return false
}

/** 子进程是否真的还活着：被信号杀掉时 exitCode 仍为 null、只有 signalCode 有值 */
export function childAlive(): boolean {
  return !!(child && child.exitCode === null && child.signalCode === null && child.pid)
}

export function spawnChild(port: number): string {
  child = spawn(process.execPath, [SERVER_PATH, '--port', String(port)], { stdio: 'ignore', detached: true })
  child.unref()
  return 'spawned pid=' + String(child.pid)
}

/**
 * S7 修复：**探端口优先**，不再信陈旧句柄/pidfile。
 * 现场 bug：句柄还在（childAlive）但端口根本没监听时，旧实现回 'already-spawned'，
 * 用户拿到一个打不通的后端；反过来，真起来了却因为等待窗口太短回 '启动失败'。
 * 现在：端口能服务 ⇒ already-running；端口不通但句柄在 ⇒ 判为陈旧句柄，SIGTERM 后重拉。
 */
export async function startBackend(port: number): Promise<string> {
  const url = base(port) + '/health'
  const portUp = await probeHttp(url, 800)
  const decision = decideStart({ portUp, childAlive: childAlive() })
  if (decision === 'already-running') return 'already-running'
  if (decision === 'stale-handle') {
    const stalePid = child && child.pid
    try {
      if (stalePid) process.kill(stalePid, 'SIGTERM')
    } catch (e: any) {
      /* 已经没了 */
    }
    child = null
    return 'stale-handle(pid=' + String(stalePid) + ')->' + spawnChild(port)
  }
  return spawnChild(port)
}

/**
 * 按 /proc 扫出"本插件历史代次拉起的后端"，按 signal 停掉（默认 SIGTERM）。
 * 热重载会丢掉 child 句柄（上一代 fiber 持有），只靠句柄停不掉残留进程 —— 这是兜底。
 * v1.0.1+S10：新增 signal 参数（事件循环被同步操作占死的进程只认 SIGKILL）。
 */
export function killBackendByCmd(port: number, signal: NodeJS.Signals = 'SIGTERM'): string[] {
  const killed: string[] = []
  const wantPort = String(port)
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
          .split('\u0000')
          .filter(Boolean)
        rows.push({ pid: Number(d), toks })
      } catch (e: any) {
        /* 进程可能已退出 */
      }
    }
    listed = true
  } catch (e: any) {
    /* 无 /proc（macOS/Windows）→ 走 ② */
  }
  // ② macOS / 通用 POSIX：ps 输出 pid + 完整命令行
  if (!listed) {
    try {
      for (const line of execFileSync('ps', ['-ax', '-o', 'pid=,command='], { encoding: 'utf8', timeout: 5000 }).split('\n')) {
        const m = /^\s*(\d+)\s+(.*)$/.exec(line)
        if (!m) continue
        const pid = Number(m[1])
        if (pid === process.pid) continue
        rows.push({ pid, toks: m[2].trim().split(/\s+/) })
      }
    } catch (e: any) {
      /* ps 不可用则不兜底 */
    }
  }
  for (const r of rows) {
    // ★ v1.0.1+S10（自伤修复）：按 **argv token** 精确匹配，绝不拿整条 cmdline 做子串匹配 ——
    //   调用方的 `bash -c "... node runtime/server.mjs --port 9877 ..."` 命令行同样含这两个串，
    //   子串匹配会把调用方自己的 shell 一起 SIGKILL 掉（2026-09-29 实测踩到，A/B 当场抓出）。
    let isServer = false
    let portOk = false
    let isShell = false
    for (let i = 0; i < r.toks.length; i++) {
      if (i === 0 && /(^|\/)(bash|sh|zsh|dash|fish|bwrap)$/.test(r.toks[i])) isShell = true
      if (i >= 1 && /(^|\/)server\.mjs$/.test(r.toks[i])) isServer = true // 必须是独立 argv 且以 server.mjs 结尾
      if (r.toks[i] === '--port' && r.toks[i + 1] === wantPort) portOk = true
    }
    if (isServer && portOk && !isShell) {
      try {
        process.kill(r.pid, signal)
        killed.push(String(r.pid))
      } catch (e: any) {
        /* 已退出 */
      }
    }
  }
  return killed
}

/**
 * v0.9.2：合并「本插件内部超时」与「宿主 exec.signal」。
 * 两条都只影响**等待**：服务端已开始的 Blender 侧任务照旧跑完（headless/job 既有语义
 * = 客户端断连 ≠ 任务失败，按 runId 回收）。
 */
export async function withTimeout(path: string, init: any, timeoutMs: number): Promise<Response> {
  const ac = new AbortController()
  const ext = execSignal()
  let fired = false
  const timer = setTimeout(() => {
    fired = true
    ac.abort()
  }, timeoutMs)
  const signal = ext ? AbortSignal.any([ext, ac.signal]) : ac.signal
  try {
    return await fetch(path, { ...(init || {}), signal })
  } catch (e: any) {
    if (!fired && ext && ext.aborted) throw new Error(ABORT_HINT)
    throw e
  } finally {
    clearTimeout(timer)
  }
}

/** 用户用 op=stop 显式停过 → 看护不再自动拉起（op=start 解除）。跨模块经访问器读写，保持 live 状态只有一份。 */
export function isPaused(): boolean {
  return paused
}
export function setPaused(v: boolean): void {
  paused = v
}
export function getChild(): any {
  return child
}
/** 杀掉并清空当前子进程句柄；返回是否原本有句柄（调用方据此写回执）。 */
export function killChildHandle(): boolean {
  if (!child) return false
  try {
    child.kill()
  } catch (e: any) {
    /* 已经退出 */
  }
  child = null
  return true
}
