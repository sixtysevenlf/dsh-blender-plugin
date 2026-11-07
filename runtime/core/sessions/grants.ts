import type { CallerIdentity, SessionRecord, SessionResult } from './types.js'
import { callerKey, fail, grantView, isMain } from './types.js'
import { anyOverlap, parseScopes } from './scopes.js'
import type { ScopeSpec } from './scopes.js'

/** Validate one grant request against the owning session and live grants. */
export function resolveGrantContext(
  session: SessionRecord,
  c: CallerIdentity,
  scopes: unknown,
  target?: CallerIdentity,
): SessionResult<{ parsed: ScopeSpec[]; holder: CallerIdentity }> {
  if (!isMain(session, c)) {
    return fail(
      'scope_forbidden',
      'session',
      'only the main agent recorded at owner claim may grant scopes',
      'ask the main agent to grant the scope',
      { sessionId: session.id },
    )
  }
  const parsed = parseScopes(scopes)
  if (!parsed.length) {
    return fail(
      'invalid_params',
      'session',
      'grant requires at least one scope',
      'pass scopes such as exclusive or collection:COL_Lights',
      {},
    )
  }
  const holder = grantHolder(c, target)
  if (!holder.agentId) {
    return fail(
      'invalid_params',
      'session',
      'grant target must name an agentId',
      'pass target agentId',
      {},
    )
  }
  if (holder.dshSessionId !== String(c.dshSessionId)) {
    return fail(
      'scope_forbidden',
      'session',
      'a grant cannot cross DSH sessions',
      'grant only to agents in this DSH session',
      {},
    )
  }
  const conflict = grantConflict(session, holder, parsed)
  if (conflict) return conflict
  return { ok: true, parsed, holder }
}

function grantHolder(c: CallerIdentity, target?: CallerIdentity): CallerIdentity {
  if (!target) return { ...c }
  return {
    hostId: String(target.hostId || c.hostId),
    dshSessionId: String(target.dshSessionId || c.dshSessionId),
    agentId: String(target.agentId || ''),
    parentAgentId: String(target.parentAgentId || ''),
    role: 'subagent',
  }
}

function grantConflict(
  session: SessionRecord,
  holder: CallerIdentity,
  parsed: ScopeSpec[],
): SessionResult<{ parsed: ScopeSpec[]; holder: CallerIdentity }> | null {
  for (const existing of session.grants.values()) {
    if (callerKey(existing.holder) === callerKey(holder)) continue
    if (!anyOverlap(existing.scopes, parsed)) continue
    return fail(
      'scope_conflict',
      'session',
      'the scope overlaps a live grant held by another agent',
      'wait for the holder to release it, or grant a non-overlapping scope',
      { grant: grantView(existing), requested: parsed },
    )
  }
  return null
}
