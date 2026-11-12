import type { CallerIdentity, GrantRecord, SessionRecord, SessionResult } from './types.js'
import { callerKey, callerView, fail } from './types.js'
import { anyOverlap, parseScopes, scopesCover } from './scopes.js'
import { conflictingHumanEdits, noteHumanConflict } from './human-edits.js'
import type { ScopeSpec } from './scopes.js'

/** State checks shared by every agent write: ownership, pause, python lock, scope syntax. */
export function evaluateWriteGate(
  session: SessionRecord,
  c: CallerIdentity,
  scopes: unknown,
  opts: { py?: boolean; pyLock?: boolean },
): SessionResult<{ effective: ScopeSpec[] }> {
  if (session.paused) {
    return fail(
      'human_paused',
      'session',
      'the human paused agent writes in Blender',
      'wait for the human to resume writes in the DSH Blender Bridge panel',
      { sessionId: session.id, pausedAt: session.pausedAt },
    )
  }
  const effectiveScopes = parseScopes(scopes)
  const humanEdits = conflictingHumanEdits(session, effectiveScopes)
  if (humanEdits.length) {
    noteHumanConflict(session, effectiveScopes, callerKey(c))
    return fail(
      'human_modified',
      'session',
      'the human changed objects covered by this write scope',
      're-read the changed objects, then call blender_session mark_read with the same scope before writing',
      {
        sessionId: session.id,
        humanModifiedAt: session.humanModifiedAt,
        changed: humanEdits.map((edit) => ({
          kind: edit.kind,
          name: edit.name,
          revision: edit.revision,
        })),
      },
    )
  }
  // py is a session-wide mutex, not a per-agent one: a second py call from the
  // same holder must also fail (overwriting activePy would let its endPy clear
  // the other call's lock). Same-holder retries go through waitMs in beginPy.
  if (opts.py && opts.pyLock !== false && session.activePy) {
    return fail(
      'py_busy',
      'session',
      'another Python call is already running in this session',
      'retry after the active Python call finishes, or pass wait_ms to wait for it',
      {
        holder: callerView(session.activePy.caller),
        startedAt: session.activePy.startedAt,
        retryAfterMs: 250,
      },
    )
  }
  const writes = parseScopes(scopes)
  if (opts.py && !writes.some((s) => s.kind === 'exclusive')) {
    return fail(
      'invalid_params',
      'session',
      'blender_py requires an exclusive scope',
      'declare exclusive for Python execution',
      {},
    )
  }
  return { ok: true, effective: writes.length ? writes : [{ kind: 'exclusive' }] }
}

/** Main-agent writes are implicit, but must not overlap a live grant held by someone else. */
export function authorizeMainWrite(
  session: SessionRecord,
  c: CallerIdentity,
  effective: ScopeSpec[],
): SessionResult<{ mode: 'main' }> {
  for (const g of session.grants.values()) {
    if (callerKey(g.holder) === callerKey(c)) continue
    if (!anyOverlap(g.scopes, effective)) continue
    return fail(
      'scope_conflict',
      'session',
      'the write overlaps a live grant held by another agent',
      'revoke the grant or wait for it to expire, then retry',
      { grant: g, requested: effective },
    )
  }
  return { ok: true, mode: 'main' }
}

export function coveringGrant(
  session: SessionRecord,
  c: CallerIdentity,
  effective: ScopeSpec[],
): GrantRecord | null {
  for (const g of session.grants.values()) {
    if (callerKey(g.holder) !== callerKey(c)) continue
    if (scopesCover(g.scopes, effective)) return g
  }
  return null
}
