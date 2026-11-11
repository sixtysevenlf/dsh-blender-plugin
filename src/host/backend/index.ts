/** host/backend 公共入口：端口解析、健康探测、启动/自愈、超时包装与暂停开关。 */
import { readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { ABORT_HINT, HERE, HOSTNAME } from '../../constants.js'
import { asRecord } from '../../contracts/dynamic.js'
import { callerHeaders, execSignal } from '../exec-context.js'
import { decideStart, probeHttp } from '../runtime-bridge.js'
import { childAlive, killStaleChild, spawnChild } from './spawn.js'
import { hostIdentity } from './host-id.js'

export { childAlive, getChild, killChildHandle, spawnChild } from './spawn.js'
export { killBackendByCmd } from './process-scan.js'

/**
 * 端口解析（与 runtime/config.ts 同一套来源，避免两处配置漂移）：
 *   插件配置 port → DSH_BLENDER_GATEWAY_PORT / DSH_BLENDER_HTTP_PORT → $DSH_BLENDER_CONFIG
 *   → <包根>/dsh-blender.config.json → ~/.dsh/dsh-blender.config.json → 39877
 */
export function resolveHttpPort(): number {
  const env = Number(process.env.DSH_BLENDER_GATEWAY_PORT || process.env.DSH_BLENDER_HTTP_PORT)
  if (Number.isFinite(env) && env > 0) return env
  const cands = [
    process.env.DSH_BLENDER_CONFIG,
    path.join(HERE, '..', 'dsh-blender.config.json'),
    path.join(os.homedir(), '.dsh', 'dsh-blender.config.json'),
  ].filter((p): p is string => Boolean(p))
  for (const p of cands) {
    try {
      const j = asRecord(JSON.parse(readFileSync(p, 'utf8')))
      const n = Number(j.gatewayPort ?? j.httpPort)
      if (Number.isFinite(n) && n > 0) return n
    } catch {
      /* 没这个文件或不是 JSON：跳过 */
    }
  }
  return 39877
}

/** 用户用 op=stop 显式停过 → 看护不再自动拉起（op=start 解除） */
let paused = false

/** 结构化拒绝：端口上的 Gateway 属于另一个 DSH host。 */
export interface BackendOwnerFailure {
  ok: false
  stage: 'config'
  code: 'GATEWAY_IN_USE'
  root_cause: string
  fix: string
  fallback: { kind: 'unavailable'; summary: string }
  evidence: { port: number; owner: Record<string, unknown> }
}

export type EnsureBackendResult =
  { ok: true; adopted: boolean; failure?: null } | { ok: false; failure?: BackendOwnerFailure }

let lastBackendFailure: BackendOwnerFailure | null = null

/** 最近一次 ensure 失败的原因；启动工具据此把 GATEWAY_IN_USE 透传给模型面。 */
export function getLastBackendFailure(): BackendOwnerFailure | null {
  return lastBackendFailure
}

/**
 * Pure ownership check for a /health payload.
 *
 * A Gateway always reports its owner once it is listening; a payload without an
 * owner (legacy backend, test double) is treated as compatible and attaches.
 */
export function checkGatewayOwnership(
  health: unknown,
  hostId: string,
  port = 0,
): BackendOwnerFailure | null {
  const gateway = asRecord(asRecord(health).gateway)
  const ownerHost = typeof gateway.hostId === 'string' ? gateway.hostId : ''
  if (!ownerHost || ownerHost === hostId) return null
  const ownerPid = typeof gateway.pid === 'number' ? gateway.pid : null
  return {
    ok: false,
    stage: 'config',
    code: 'GATEWAY_IN_USE',
    root_cause:
      '端口 ' +
      String(port) +
      ' 上的 Gateway 属于另一个 DSH host（hostId=' +
      ownerHost +
      (ownerPid === null ? '' : ' pid=' + String(ownerPid)) +
      '）；本 host 不共享、不 attach、不复用该 Gateway。',
    fix: '停止占用该端口的另一个 DSH host，或为本 host 设置不同的 DSH_BLENDER_GATEWAY_PORT。',
    fallback: {
      kind: 'unavailable',
      summary: '本 host 未接管另一个 DSH host 的 Gateway；需要独立端口或先停止对方。',
    },
    evidence: { port, owner: gateway },
  }
}

/** Fetch /health and parse it; null when the port is down or the body is not JSON. */
export async function probeHealth(
  port: number,
  timeoutMs = 1500,
): Promise<Record<string, unknown> | null> {
  const ac = new AbortController()
  const timer = setTimeout(() => ac.abort(), timeoutMs)
  try {
    const r = await fetch(base(port) + '/health', { signal: ac.signal })
    if (!r.ok) return null
    return asRecord(await r.json())
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

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
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}

/**
 * Ensure a usable Gateway for this DSH host.
 *
 * 3.1 rule: a Gateway belongs to exactly one DSH host. When /health reports a
 * different owner hostId we refuse to attach (structured GATEWAY_IN_USE) instead
 * of routing this host's calls through another host's process. A same-host
 * reload reuses the existing Gateway only after the identity matches.
 */
export async function ensureBackendDetailed(port: number): Promise<EnsureBackendResult> {
  const health = await probeHealth(port)
  if (health) {
    const failure = checkGatewayOwnership(health, hostIdentity(), port)
    lastBackendFailure = failure
    return failure ? { ok: false, failure } : { ok: true, adopted: true }
  }
  // A live but non-JSON /health (legacy backend) still counts as running.
  if (await probe(port)) {
    lastBackendFailure = null
    return { ok: true, adopted: true }
  }
  if (paused) return { ok: false }
  // Test-only guard: contract tests exercise the unreachable-backend path on a
  // closed port; never spawn a real server from inside the unit/integration suite.
  if (process.env.DSH_BLENDER_TEST_NO_SPAWN === '1') return { ok: false }
  try {
    await startBackend(port)
  } catch {
    /* ignore */
  }
  for (let i = 0; i < 25; i++) {
    const next = await probeHealth(port)
    if (next) {
      const failure = checkGatewayOwnership(next, hostIdentity(), port)
      lastBackendFailure = failure
      return failure ? { ok: false, failure } : { ok: true, adopted: false }
    }
    if (await probe(port)) {
      lastBackendFailure = null
      return { ok: true, adopted: false }
    }
    // A previous Gateway may still be releasing the port after dispose; retry the
    // spawn once the OS has had a moment to free it instead of waiting for the watchdog.
    if (i === 5) {
      try {
        await startBackend(port)
      } catch {
        /* keep probing */
      }
    }
    await sleep(300)
  }
  return { ok: false }
}

/** Boolean view kept for the existing tool call sites. */
export async function ensureBackend(port: number): Promise<boolean> {
  return (await ensureBackendDetailed(port)).ok
}

/**
 * 修复：**探端口优先**，不再信陈旧句柄/pidfile。
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
    const stalePid = killStaleChild()
    return 'stale-handle(pid=' + String(stalePid) + ')->' + spawnChild(port)
  }
  return spawnChild(port)
}

/**
 * v0.9.2：合并「本插件内部超时」与「宿主 exec.signal」。
 * 两条都只影响**等待**：服务端已开始的 Blender 侧任务照旧跑完（headless/job 既有语义
 * = 客户端断连 ≠ 任务失败，按 runId 回收）。
 */
export async function withTimeout(
  path: string,
  init: RequestInit | undefined,
  timeoutMs: number,
): Promise<Response> {
  const ac = new AbortController()
  const ext = execSignal()
  let fired = false
  const timer = setTimeout(() => {
    fired = true
    ac.abort()
  }, timeoutMs)
  const signal = ext ? AbortSignal.any([ext, ac.signal]) : ac.signal
  try {
    const headers = new Headers((init && init.headers) || undefined)
    for (const [name, value] of Object.entries(callerHeaders())) headers.set(name, value)
    return await fetch(path, { ...(init || {}), headers, signal })
  } catch (e: unknown) {
    if (!fired && ext && ext.aborted) throw new Error(ABORT_HINT, { cause: e })
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
