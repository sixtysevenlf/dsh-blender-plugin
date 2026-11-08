// Human-edit conflict tracking for 3.1 GUI sessions.
//
// The add-on reports object/collection names changed outside an agent command.
// The write gate turns those records into human_modified until the agent
// re-reads and marks the matching scope as read.
import { parseScopes } from './scopes.js'
import type { ScopeSpec } from './scopes.js'
import { callerKey, fail, ownerMatches } from './types.js'
import type { CallerIdentity, HumanEdit, SessionRecord, SessionResult } from './types.js'

export interface HumanEditInput {
  kind?: unknown
  name?: unknown
  revision?: unknown
}

function editKey(kind: string, name: string): string {
  return kind + ':' + name
}

/** Apply one add-on event to every session bound to that instance. */
export function applyHumanEditsToSessions(
  sessions: Map<string, SessionRecord>,
  instanceId: unknown,
  edits: HumanEditInput[],
): number {
  const id = String(instanceId || '')
  if (!id) return 0
  let recorded = 0
  for (const session of sessions.values()) {
    if (session.instanceId === id) recorded += recordHumanEdits(session, edits)
  }
  return recorded
}

/** find session for caller (same rule as the registry's attach lookup). */
function sessionFor(sessions: Map<string, SessionRecord>, c: CallerIdentity, sessionId?: string) {
  if (sessionId) return sessions.get(String(sessionId)) || null
  for (const session of sessions.values()) {
    if (ownerMatches(session.owner, c)) return session
    if (session.agents.has(callerKey(c))) return session
  }
  return null
}

/** Clear human-edit records the caller has re-read. */
export function markHumanRead(
  sessions: Map<string, SessionRecord>,
  c: CallerIdentity,
  sessionId: string | undefined,
  scopes: unknown,
): SessionResult<{ cleared: number }> {
  const session = sessionFor(sessions, c, sessionId)
  if (!session)
    return fail('session_not_found', 'session', 'the session does not exist', 'attach first', {})
  if (!ownerMatches(session.owner, c))
    return fail(
      'session_in_use',
      'session',
      'another DSH session owns this GUI session',
      'wait for the owner to release it',
      { sessionId: session.id },
    )
  return { ok: true, cleared: clearHumanEdits(session, parseScopes(scopes)) }
}

/** Merge edits from one add-on instance; newest revision wins. */
export function recordHumanEdits(
  session: SessionRecord,
  edits: HumanEditInput[],
  now = Date.now(),
): number {
  let recorded = 0
  for (const edit of edits) {
    const kind =
      edit.kind === 'collection' ? 'collection' : edit.kind === 'object' ? 'object' : null
    const name = String(edit.name || '').trim()
    if (!kind || !name) continue
    const key = editKey(kind, name)
    const previous = session.humanEdits.get(key)
    const revision = Number.isFinite(Number(edit.revision))
      ? Number(edit.revision)
      : previous
        ? previous.revision + 1
        : 1
    if (previous && previous.revision > revision) continue
    session.humanEdits.set(key, { kind, name, revision, at: now })
    recorded += 1
  }
  if (recorded) session.humanModifiedAt = now
  return recorded
}

function editOverlapsScope(edit: HumanEdit, scope: ScopeSpec): boolean {
  if (scope.kind === 'exclusive') return true
  const value = String(scope.value || '')
  if (!value) return false
  if (scope.kind === 'object') return edit.kind === 'object' && edit.name === value
  if (scope.kind === 'collection') return edit.kind === 'collection' && edit.name === value
  // prefix: object-name prefix only; a collection prefix has no stable mapping here.
  return edit.kind === 'object' && edit.name.startsWith(value)
}

/** Edits the requested write scopes would overwrite; empty means no conflict. */
export function conflictingHumanEdits(session: SessionRecord, scopes: ScopeSpec[]): HumanEdit[] {
  if (!session.humanEdits.size) return []
  const out = new Map<string, HumanEdit>()
  for (const edit of session.humanEdits.values()) {
    if (scopes.some((scope) => editOverlapsScope(edit, scope))) {
      out.set(editKey(edit.kind, edit.name), edit)
    }
  }
  return [...out.values()].sort((a, b) => a.name.localeCompare(b.name))
}

/** Clear the edits covered by the scopes the caller just re-read. */
export function clearHumanEdits(session: SessionRecord, scopes: ScopeSpec[]): number {
  if (!scopes.some((scope) => scope.kind === 'exclusive') && !scopes.length) {
    session.humanEdits.clear()
    session.humanModifiedAt = null
    return 0
  }
  let cleared = 0
  for (const [key, edit] of session.humanEdits) {
    if (scopes.some((scope) => editOverlapsScope(edit, scope))) {
      session.humanEdits.delete(key)
      cleared += 1
    }
  }
  if (!session.humanEdits.size) session.humanModifiedAt = null
  return cleared
}
