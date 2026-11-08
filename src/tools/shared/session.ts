/**
 * Shared plumbing for the six model tools (3.1).
 *
 * Every tool that needs a GUI session asks the same questions: which sessions
 * exist, is one ready, and how does a Gateway availability failure become a
 * tool result with the four-question envelope (stage / root cause / fix /
 * fallback)? Keeping that in one module stops the six tools from growing six
 * slightly different session gates.
 */
import type { AvailabilityStage } from '../../contracts/availability.js'
import { asRecord, isRecord, text } from '../../contracts/dynamic.js'
import { backendPost } from '../../host/http.js'
import { toolError } from '../errors.js'
import type { ToolFailure } from '../errors.js'

export type SessionRow = Record<string, unknown>

export interface SessionList {
  ok: true
  sessions: SessionRow[]
}

export interface SessionListFailure {
  ok: false
  failure: ToolFailure
}

/** POST /lease { action, ... } — the Gateway route is the session transport. */
export async function sessionAction(
  port: number,
  action: string,
  payload: Record<string, unknown> = {},
  timeoutMs = 20000,
): Promise<Record<string, unknown>> {
  try {
    return await backendPost(port, '/lease', { action, ...payload }, timeoutMs)
  } catch (e) {
    return {
      ok: false,
      code: 'gateway_unreachable',
      stage: 'transport',
      root_cause: 'Gateway 不可达 · ' + text(e),
      fix:
        '确认 DSH 插件已启动本地 Gateway（127.0.0.1:' +
        String(port) +
        '）；Gateway 由宿主管理，不要启动第二个 Blender 进程。',
      evidence: { action },
    }
  }
}

/** Map one Gateway availability failure body to the tool error channel. */
export function gatewayFailure(body: Record<string, unknown>, what: string): ToolFailure {
  const code = String(body.code || body.error || 'session_error')
  const stage = typeof body.stage === 'string' ? (body.stage as AvailabilityStage) : undefined
  const root = String(body.root_cause || body.error || what + ' 失败')
  const fix = String(body.fix || body.hint || '会话或 Gateway 状态变化后重试')
  const evidence = isRecord(body.evidence) ? JSON.stringify(body.evidence) : undefined
  return toolError(code, what + ' 失败 · ' + root, fix, evidence, stage)
}

export function isFailureBody(body: Record<string, unknown>): boolean {
  return body.ok === false
}

/** Visible sessions for this caller. */
export async function listSessions(port: number): Promise<SessionList | SessionListFailure> {
  const r = await sessionAction(port, 'list')
  if (r.ok !== true) return { ok: false, failure: gatewayFailure(r, 'session list') }
  const rows = Array.isArray(r.sessions) ? r.sessions : []
  return { ok: true, sessions: rows.filter(isRecord).map(asRecord) }
}

/** A ready row with a connected addon instance; user-gui wins over virtual-gui. */
export function readyRow(sessions: SessionRow[], kind?: string): SessionRow | null {
  const ready = sessions.filter(
    (s) => String(s.status || '') === 'ready' && !!String(s.instanceId || ''),
  )
  const wanted = kind ? ready.filter((s) => String(s.kind || '') === kind) : ready
  return (
    wanted.find((s) => String(s.kind || '') === 'user-gui') ??
    wanted.find((s) => String(s.kind || '') === 'virtual-gui') ??
    wanted[0] ??
    null
  )
}

/**
 * Decision-required failure used when an executing tool has no ready GUI
 * session. It must never spawn Blender; it names the two explicit choices the
 * user can take (attach an open Blender, or consent to a virtual-gui).
 */
export function sessionRequiredFailure(detail?: string): ToolFailure {
  const choices = [
    {
      action: 'attach',
      tool: 'blender_session',
      summary: '在 Blender 里打开 GUI，然后在 N 面板点 Connect，再 blender_session action=attach',
    },
    {
      action: 'start',
      tool: 'blender_session',
      kind: 'virtual-gui',
      summary: '由用户确认后启动 virtual-gui（首次 start 返回 consent_required，需要人类授权）',
    },
  ]
  return toolError(
    'session_required',
    'Blender 未附着：没有 ready 的 GUI 会话（不会自动启动 Blender 或转 headless）',
    '先让用户打开 Blender 并连接（blender_session action=attach），或经用户同意启动 virtual-gui（blender_session action=start kind=virtual-gui）。',
    JSON.stringify({ choices, detail: detail || null }),
    'session',
  )
}

export interface ReadySession {
  ok: true
  session: SessionRow
  instanceId: string
}

export interface NoReadySession {
  ok: false
  failure: ToolFailure
}

/** Resolve the ready GUI session for a session-bound tool call. */
export async function requireReadySession(
  port: number,
  kind?: string,
): Promise<ReadySession | NoReadySession> {
  const listed = await listSessions(port)
  if (!listed.ok) return listed
  const row = readyRow(listed.sessions, kind)
  if (!row) return { ok: false, failure: sessionRequiredFailure() }
  return { ok: true, session: row, instanceId: String(row.instanceId || '') }
}

/** Session id used by mutating routes (txn/py/view) for ownership + scope checks. */
export function sessionIdOf(row: SessionRow): string {
  return String(row.id || '')
}

/**
 * Best-effort presence publication after a successful see/frame. Presence is
 * read-only data; a failure here must never turn a good frame into an error.
 */
export async function publishView(
  port: number,
  sessionId: string,
  view: Record<string, unknown>,
): Promise<void> {
  if (!sessionId) return
  try {
    await sessionAction(port, 'update_view', { sessionId, view }, 5000)
  } catch {
    /* presence is observational */
  }
}
