// Human pause propagation from the Blender UI.
//
// Pause is owned by the human in Blender: the add-on reports the N-panel toggle
// and the Gateway mirrors it onto every session bound to that instance. Agent
// calls can neither pause nor resume (see SessionRegistry.pause/resume).

import type { SessionRecord } from './types.js'

export function applyHumanPause(
  sessions: Map<string, SessionRecord>,
  instanceId: string | undefined,
  paused: boolean,
): number {
  const id = String(instanceId || '')
  let changed = 0
  for (const s of sessions.values()) {
    if (id && s.instanceId !== id) continue
    s.paused = paused
    s.pausedAt = paused ? Date.now() : null
    changed++
  }
  return changed
}
