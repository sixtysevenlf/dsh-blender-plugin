// Write gate for the 3.1 session/ownership model.
//
// Requests from the DSH host carry caller headers. When they do, the registry
// decides whether this caller owns the GUI session and holds a covering scope
// grant. Legacy clients without headers keep the 3.0 lease behaviour until the
// six-tool surface replaces them (slice 7).

import { currentCaller } from './caller.js'
import { availabilityFailure } from '../core/availability.js'
import type { AvailabilityFailure } from '../core/availability.js'
import type { SessionRegistry } from '../core/sessions/registry.js'
import { OWNER_TTL_MS, callerKey, fail, ownerMatches } from '../core/sessions/types.js'

let REGISTRY: SessionRegistry | null = null

export function setSessionRegistry(registry: SessionRegistry): void {
  REGISTRY = registry
}

export function sessionRegistry(): SessionRegistry | null {
  return REGISTRY
}

function scopePayload(payload: Record<string, unknown>): unknown {
  return payload.write_scope ?? payload.write_scopes ?? payload.scope
}

/** Resolve the write scope declared by a route payload. */
export function writeScopesFor(payload: Record<string, unknown>, fallback: unknown): unknown {
  const declared = scopePayload(payload)
  return declared === undefined ? fallback : declared
}

export function sessionWriteGate(
  payload: Record<string, unknown> | null | undefined,
  fallbackScope: unknown = 'exclusive',
): AvailabilityFailure | null {
  const caller = currentCaller()
  const registry = REGISTRY
  if (!caller || !registry) return null
  const body = (payload || {}) as Record<string, unknown>
  const sessionId = body.sessionId === undefined ? undefined : String(body.sessionId)
  const result = registry.authorizeWrite(caller, sessionId, writeScopesFor(body, fallbackScope), {
    py: body.py === true,
  })
  if (result.ok) return null
  return result
}

/**
 * Read gate for 3.1 session-bound read routes (/cmd of a read-only command).
 *
 * Reads must not require a write grant (sessions spec: "Read, see, and
 * presence MUST NOT require a grant"), but they still must not touch another
 * DSH session's GUI. Owner and attached agents pass; a foreign DSH session gets
 * session_in_use naming the owner and its remaining lease.
 */
export function sessionReadGate(
  payload: Record<string, unknown> | null | undefined,
): AvailabilityFailure | null {
  const caller = currentCaller()
  const registry = REGISTRY
  if (!caller || !registry) return null
  const body = (payload || {}) as Record<string, unknown>
  const requested = body.sessionId === undefined ? '' : String(body.sessionId)
  const instanceId = body.instanceId === undefined ? '' : String(body.instanceId)
  const byInstance = instanceId
    ? (registry.getSession('user-gui:' + instanceId) ??
      registry.getSession('virtual-gui:' + instanceId))
    : null
  const session = requested
    ? registry.getSession(requested)
    : byInstance || registry.findForCaller(caller)
  if (!session)
    return fail(
      'session_unavailable',
      'session',
      requested
        ? 'the requested GUI session does not exist'
        : 'no GUI session is attached for this caller',
      'call blender_session action=list, then action=attach with an instanceId',
      { sessionId: requested || null },
    )
  if (
    session.owner &&
    !ownerMatches(session.owner, caller) &&
    !session.agents.has(callerKey(caller))
  )
    return fail(
      'session_in_use',
      'session',
      'another DSH session owns this GUI session',
      'ask the owner to detach or release this session, then retry',
      {
        sessionId: session.id,
        owner: {
          hostId: session.owner.hostId,
          dshSessionId: session.owner.dshSessionId,
          agentId: session.owner.agentId,
          expiresInMs: Math.max(0, session.owner.heartbeatAt + OWNER_TTL_MS - Date.now()),
        },
      },
    )
  return null
}

export function gateResponse(failure: AvailabilityFailure): Record<string, unknown> {
  return {
    ...(failure as unknown as Record<string, unknown>),
    error: failure.code,
    hint: failure.fix,
  }
}

export { availabilityFailure }
