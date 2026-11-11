// Presence/observation operations extracted from SessionRegistry so the
// registry class stays inside its module budget. These are observational: they
// never authorize a scene write and never require a grant.
import { presenceRows, setNotice, setSelection, setView } from './presence.js'
import { callerKey, callerView, fail, sessionView } from './types.js'
import type { CallerIdentity, SessionRecord, SessionResult, ViewPose } from './types.js'

export interface ObservableHost {
  sessions: Map<string, SessionRecord>
  prune(): void
}

export function observableSession(
  host: ObservableHost,
  c: CallerIdentity,
  sessionId: string,
): SessionResult<{ session: SessionRecord }> {
  host.prune()
  const session = host.sessions.get(String(sessionId))
  if (!session)
    return fail('session_not_found', 'session', 'the session does not exist', 'attach first', {})
  if (!session.agents.has(callerKey(c)))
    return fail(
      'session_in_use',
      'session',
      'the caller is not attached to this GUI session',
      'attach this agent to the session first',
      {},
    )
  return { ok: true, session }
}

export function updateViewFor(
  host: ObservableHost,
  c: CallerIdentity,
  sessionId: string,
  view: Omit<ViewPose, 'updatedAt'>,
): SessionResult<{ presence: Record<string, unknown>[] }> {
  const r = observableSession(host, c, sessionId)
  if (!('session' in r)) return r
  return { ok: true, presence: setView(r.session, c, view) }
}

export function noticeFor(
  host: ObservableHost,
  c: CallerIdentity,
  sessionId: string,
  text: string,
): SessionResult<{ presence: Record<string, unknown>[] }> {
  const r = observableSession(host, c, sessionId)
  if (!('session' in r)) return r
  return { ok: true, presence: setNotice(r.session, text) }
}

export function updateSelectionFor(
  host: ObservableHost,
  c: CallerIdentity,
  sessionId: string,
  selected: string[],
  activity?: string,
): SessionResult<{ presence: Record<string, unknown>[] }> {
  const r = observableSession(host, c, sessionId)
  if (!('session' in r)) return r
  return { ok: true, presence: setSelection(r.session, c, selected, activity) }
}

export function doctorFor(
  c: CallerIdentity,
  session: SessionRecord | null,
  instances: { ready: boolean }[],
): {
  readiness: Record<string, unknown>
  session: Record<string, unknown> | null
} {
  const ready = instances.filter((x) => x.ready)
  return {
    readiness: {
      ready:
        !!session &&
        session.status === 'ready' &&
        !!session.owner &&
        session.owner.hostId === c.hostId &&
        session.owner.dshSessionId === c.dshSessionId,
      session_ready: !!session && session.status === 'ready',
      instances: ready.length,
      sessionId: session ? session.id : null,
      owner: session ? session.owner : null,
      paused: session ? session.paused : false,
      activePy: session && session.activePy ? callerView(session.activePy.caller) : null,
    },
    session: session ? sessionView(session) : null,
  }
}

export function presenceFor(
  host: ObservableHost,
  c: CallerIdentity,
  sessionId?: string,
): SessionResult<{ presence: Record<string, unknown>[] }> {
  host.prune()
  const session = sessionId ? host.sessions.get(String(sessionId)) : findVisible(host, c)
  if (!session)
    return fail(
      'session_not_found',
      'session',
      'the session does not exist',
      'call blender_session list to see live sessions',
      { sessionId: sessionId || null },
    )
  if (!session.agents.has(callerKey(c)) && !hasOwner(c, session))
    return fail(
      'session_in_use',
      'session',
      'the caller cannot observe this GUI session',
      'attach this agent to the session first',
      {},
    )
  return { ok: true, presence: presenceRows(session) }
}

function hasOwner(c: CallerIdentity, session: SessionRecord): boolean {
  return (
    !!session.owner &&
    session.owner.hostId === c.hostId &&
    session.owner.dshSessionId === c.dshSessionId
  )
}

function findVisible(host: ObservableHost, c: CallerIdentity): SessionRecord | null {
  for (const session of host.sessions.values()) {
    if (hasOwner(c, session) || session.agents.has(callerKey(c))) return session
  }
  return null
}
