import { AsyncLocalStorage } from 'node:async_hooks'
import { hostIdentity } from './backend/host-id.js'

export interface CallerInfo {
  hostId: string
  dshSessionId: string
  agentId: string
  parentAgentId?: string
  role: 'main' | 'subagent'
  callId?: string
  turnId?: string
  displayName?: string
}

export interface ExecContextStore {
  signal?: AbortSignal
  caller?: CallerInfo
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}
function text(value: unknown): string {
  return value === undefined || value === null ? '' : String(value)
}
function firstText(...values: unknown[]): string {
  for (const value of values) {
    const found = text(value)
    if (found) return found
  }
  return ''
}

/** Derive the durable caller identity from the host's exec/agent objects. */
export function callerFromExec(exec: unknown): CallerInfo {
  const e = record(exec)
  const agent = record(e.agent)
  const session = record(agent.session)
  const parent = record(agent.parent)
  const agentId = firstText(agent.id, agent.agentId, 'unknown-agent')
  const parentAgentId = firstText(
    agent.parentAgentId,
    parent.id,
    parent.agentId,
    session.parentAgentId,
  )
  const dshSessionId = firstText(
    session.id,
    agent.sessionId,
    e.dshSessionId,
    process.env.DSH_SESSION_ID,
    'unknown-session',
  )
  // Same stable identity the Gateway owner record uses (survives plugin reload).
  const hostId = hostIdentity()
  const role =
    agent.role === 'main' || parentAgentId === '' ? 'main' : ('subagent' as 'main' | 'subagent')
  return {
    hostId,
    dshSessionId,
    agentId,
    ...(parentAgentId ? { parentAgentId } : {}),
    role,
    ...(firstText(e.callId) ? { callId: firstText(e.callId) } : {}),
    ...(firstText(agent.turnId, record(agent.turn).id, agent.currentTurnId)
      ? { turnId: firstText(agent.turnId, record(agent.turn).id, agent.currentTurnId) }
      : {}),
    ...(firstText(agent.displayName, agent.name)
      ? { displayName: firstText(agent.displayName, agent.name) }
      : {}),
  }
}

export function execCaller(): CallerInfo | null {
  const s = EXEC_CTX.getStore()
  return s && s.caller ? s.caller : null
}

export function callerHeaders(): Record<string, string> {
  const caller = execCaller()
  if (!caller) return {}
  const headers: Record<string, string> = {
    'x-dsh-host-id': caller.hostId,
    'x-dsh-session-id': caller.dshSessionId,
    'x-dsh-agent-id': caller.agentId,
    'x-dsh-agent-role': caller.role,
  }
  if (caller.parentAgentId) headers['x-dsh-parent-agent-id'] = caller.parentAgentId
  if (caller.callId) headers['x-dsh-call-id'] = caller.callId
  if (caller.turnId) headers['x-dsh-turn-id'] = caller.turnId
  if (caller.displayName) headers['x-dsh-display-name'] = caller.displayName
  return headers
}

// v0.9.2（DSH 更新适配 R3）：把宿主的 exec（exec.signal = 用户打断 / per-tool 截止）
// 放进 AsyncLocalStorage，传输层 withTimeout() 自动合并它 —— **六个工具体一行不改**
// 就能响应宿主取消；每个工具另补静态 timeoutMs（见 T），把"上游设默认截止"从
// "Blender 还在跑、工具已被判超时"的静默错位，变成"先由本插件给出可读错误"。
export const EXEC_CTX = new AsyncLocalStorage<ExecContextStore>()
/** 当前工具调用对应的宿主 exec.signal（没有则 undefined） */
export function execSignal(): AbortSignal | undefined {
  const s = EXEC_CTX.getStore()
  return s?.signal instanceof AbortSignal ? s.signal : undefined
}
