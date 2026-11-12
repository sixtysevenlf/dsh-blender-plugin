// Owner heartbeat extracted from registry.ts (file budget): renew, reclaim, or
// refuse depending on who is asking.
import { touchAgent } from './presence.js'
import { fail, isMain, ownerMatches, ownerOf, sessionView } from './types.js'
import type { CallerIdentity, SessionRecord, SessionResult } from './types.js'

export function heartbeatSession(
  session: SessionRecord | null,
  c: CallerIdentity,
): SessionResult<{ session: Record<string, unknown> }> {
  if (!session)
    return fail(
      'session_not_found',
      'session',
      'the session does not exist',
      're-attach the GUI session',
      {},
    )
  if (session.owner && !ownerMatches(session.owner, c)) {
    return fail(
      'session_in_use',
      'session',
      'another DSH session owns this GUI session',
      'wait for the owner to release it',
      { sessionId: session.id },
    )
  }
  if (session.owner && !isMain(session, c)) {
    return fail(
      'session_in_use',
      'session',
      'only the owning main agent may heartbeat this GUI session',
      'ask the main agent to heartbeat',
      { sessionId: session.id },
    )
  }
  if (!session.owner) {
    if (c.role !== 'main') {
      return fail(
        'owner_required',
        'session',
        'only the main agent may reclaim a heartbeat-expired GUI session',
        'ask the main agent to heartbeat or re-attach first',
        { sessionId: session.id },
      )
    }
    session.owner = ownerOf(c)
  }
  session.owner.heartbeatAt = Date.now()
  touchAgent(session, c)
  return { ok: true, session: sessionView(session) }
}
