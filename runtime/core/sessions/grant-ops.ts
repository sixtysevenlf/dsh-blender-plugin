// Scope-grant operations extracted from the session registry (module budget).
import { resolveGrantContext } from './grants.js'
import { GRANT_TTL_MS, callerKey, fail, grantView, isMain, ownerMatches } from './types.js'
import type { CallerIdentity, GrantRecord, SessionRecord, SessionResult } from './types.js'

export interface GrantOpsHost {
  sessions: Map<string, SessionRecord>
  prune(): void
  findForCaller(c: CallerIdentity): SessionRecord | null
}

export function grantTo(
  host: GrantOpsHost,
  c: CallerIdentity,
  sessionId: string,
  scopes: unknown,
  target?: CallerIdentity,
  ttlMs?: number,
): SessionResult<{ grant: Record<string, unknown> }> {
  host.prune()
  const session = host.sessions.get(String(sessionId))
  if (!session)
    return fail('session_not_found', 'session', 'the session does not exist', 'attach first', {})
  const ctx = resolveGrantContext(session, c, scopes, target)
  if (!('holder' in ctx)) return ctx
  const { parsed, holder } = ctx
  const now = Date.now()
  const requestedTtl = Number(ttlMs)
  const ttl =
    Number.isFinite(requestedTtl) && requestedTtl > 0
      ? Math.min(GRANT_TTL_MS, requestedTtl)
      : GRANT_TTL_MS
  const grant: GrantRecord = {
    id: 'grant_' + Math.random().toString(16).slice(2, 10),
    sessionId: session.id,
    holder,
    grantedBy: { ...c },
    scopes: parsed,
    createdAt: now,
    heartbeatAt: now,
    expiresAt: now + ttl,
  }
  for (const [existingId, existing] of session.grants) {
    if (callerKey(existing.holder) === callerKey(holder)) session.grants.delete(existingId)
  }
  session.grants.set(grant.id, grant)
  session.lastActivityAt = now
  return { ok: true, grant: grantView(grant) }
}

export function revokeFrom(
  host: GrantOpsHost,
  c: CallerIdentity,
  sessionId: string,
  grantId?: string,
): SessionResult<{ revoked: string[] }> {
  host.prune()
  const session = host.sessions.get(String(sessionId))
  if (!session)
    return fail('session_not_found', 'session', 'the session does not exist', 'attach first', {})
  if (!isMain(session, c))
    return fail(
      'scope_forbidden',
      'session',
      'only the main agent may revoke scopes',
      'ask the main agent to revoke',
      {},
    )
  const revoked: string[] = []
  for (const [id, g] of session.grants) {
    if (grantId && id !== String(grantId)) continue
    if (!grantId && callerKey(g.holder) === callerKey(c)) continue
    session.grants.delete(id)
    revoked.push(id)
  }
  return { ok: true, revoked }
}

export function listGrants(
  host: GrantOpsHost,
  c: CallerIdentity,
  sessionId?: string,
): SessionResult<{ grants: Record<string, unknown>[] }> {
  host.prune()
  const session = sessionId ? host.sessions.get(String(sessionId)) : host.findForCaller(c)
  if (!session) return { ok: true, grants: [] }
  if (!ownerMatches(session.owner, c))
    return fail(
      'session_in_use',
      'session',
      'another DSH session owns this GUI session',
      'wait for the owner to release it',
      { sessionId: session.id },
    )
  const all = [...session.grants.values()]
  const visible = isMain(session, c) ? all : all.filter((g) => callerKey(g.holder) === callerKey(c))
  return { ok: true, grants: visible.map(grantView) }
}
