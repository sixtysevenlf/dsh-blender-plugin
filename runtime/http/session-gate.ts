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

export function gateResponse(failure: AvailabilityFailure): Record<string, unknown> {
  return {
    ...(failure as unknown as Record<string, unknown>),
    error: failure.code,
    hint: failure.fix,
  }
}

export { availabilityFailure }
