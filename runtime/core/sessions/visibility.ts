// Read-side session helpers extracted from registry.ts (file budget): caller
// visibility, the cross-session refusal, provider teardown, and the virtual-gui
// orphan sweep. None of them owns state; they read or mutate one SessionRecord.
import { publishPresence } from './presence-publish.js'
import type { VirtualGuiLifecycle } from './virtual-gui.js'
import { callerKey, ownerMatches } from './types.js'
import type { CallerIdentity, SessionRecord, SessionResult } from './types.js'
import { fail } from './types.js'

/** How long a virtual-gui without an owner stays reclaimable before it is stopped. */
export const ORPHAN_GRACE_MS = 60_000

/** /status and /doctor must not expose another DSH session's owner, agents or grants. */
export function foreignSession(session: SessionRecord): SessionResult<never> {
  return fail(
    'session_in_use',
    'session',
    '该会话属于另一个 DSH 会话（' +
      String(session.owner?.dshSessionId || '') +
      '），不能按 id 读取',
    '用自己会话的 sessionId，或先在对方会话 release；list 只返回可见会话',
    { sessionId: session.id, owner: session.owner ? { ...session.owner } : null },
  )
}

/**
 * Release/detach stay synchronous at the API edge, so provider teardown runs in
 * the background. Its failure must not disappear: without this the display
 * helper or Xvfb would stay alive with nothing recording why.
 */
export function disposeInBackground(lifecycle: VirtualGuiLifecycle, session: SessionRecord): void {
  const report = (why: unknown): void => {
    process.stderr.write(
      '[dsh-blender] virtual-gui dispose failed (' +
        String(session.id) +
        '): ' +
        String(why) +
        String.fromCharCode(10),
    )
  }
  lifecycle.disposeSession(session).then((failure) => {
    if (failure) report(failure.root_cause || failure.code)
  }, report)
}

/** A caller sees a session when it owns it, is attached, or was attached before. */
export function visibleCaller(session: SessionRecord, c: CallerIdentity): boolean {
  return (
    ownerMatches(session.owner, c) ||
    session.agents.has(callerKey(c)) ||
    session.claimants.has(callerKey(c))
  )
}

/** A virtual-gui exists for its owner; stop it once the reclaim grace has passed. */
export function reapOrphans(
  sessions: Map<string, SessionRecord>,
  lifecycle: VirtualGuiLifecycle,
  now = Date.now(),
): void {
  for (const s of [...sessions.values()]) {
    // Attached agents do not keep it alive: only the owner's lease can drive the
    // display. The grace window still lets the creator reclaim and stop it.
    if (s.kind !== 'virtual-gui' || s.owner || !s.orphanedAt) continue
    if (now - s.orphanedAt <= ORPHAN_GRACE_MS) continue
    sessions.delete(s.id)
    disposeInBackground(lifecycle, s)
  }
}

/** virtual-gui sessions that hold (or are about to hold) a display slot. */
export function virtualGuiCount(sessions: Map<string, SessionRecord>): number {
  let count = 0
  for (const s of sessions.values()) {
    if (s.kind === 'virtual-gui' && (s.status === 'starting' || s.status === 'ready')) count++
  }
  return count
}

/** The addon socket dropped: a virtual-gui session for it cannot outlive it. */
export function handleInstanceGone(
  sessions: Map<string, SessionRecord>,
  lifecycle: VirtualGuiLifecycle,
  instanceId: string,
): void {
  const session = sessions.get('virtual-gui:' + String(instanceId))
  if (!session) return
  session.status = 'stopped'
  sessions.delete(session.id)
  disposeInBackground(lifecycle, session)
}

/** Drop the session's presence rows before forgetting it. */
export function removeSession(sessions: Map<string, SessionRecord>, sessionId: string): void {
  const session = sessions.get(String(sessionId))
  if (session && session.agents.size > 0) publishPresence(session, [])
  sessions.delete(sessionId)
}
