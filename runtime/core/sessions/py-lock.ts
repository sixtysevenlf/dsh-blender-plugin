// Bounded Python-lock acquisition and timeout reporting.
//
// blender_py is exclusive per GUI session. The lock lives on SessionRecord so a
// long-running script is never reclaimed by the 30s grant TTL; this module owns
// only the wait/retry policy and the timeout envelope shape.

import { setTimeout as sleep } from 'node:timers/promises'
import type { CallerIdentity, SessionRecord, SessionResult } from './types.js'
import { PY_WAIT_MAX_MS, callerKey, callerView, fail } from './types.js'

export type AuthorizeAttempt = () => SessionResult<{ sessionId: string }>

export async function waitForPyLock(
  attempt: AuthorizeAttempt,
  waitMs: number,
): Promise<SessionResult<{ sessionId: string }>> {
  const requested = Number(waitMs)
  const budget =
    Number.isFinite(requested) && requested > 0 ? Math.min(PY_WAIT_MAX_MS, requested) : 0
  const deadline = Date.now() + budget
  for (;;) {
    const r = attempt()
    if ('sessionId' in r) return r
    if (r.code !== 'py_busy' || Date.now() >= deadline) return r
    await sleep(Math.min(250, Math.max(1, deadline - Date.now())))
  }
}

export function pyTimeoutFailure(
  session: SessionRecord,
  c: CallerIdentity,
): SessionResult<{ running: boolean }> {
  if (!session.activePy || callerKey(session.activePy.caller) !== callerKey(c))
    return { ok: true, running: false }
  return fail(
    'py_still_running',
    'session',
    'Python is still running after the caller timeout',
    'wait for the call to finish or inspect the session before retrying',
    {
      holder: callerView(session.activePy.caller),
      startedAt: session.activePy.startedAt,
      retryAfterMs: 500,
    },
  )
}
