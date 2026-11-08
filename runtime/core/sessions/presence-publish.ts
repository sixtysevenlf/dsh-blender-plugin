// Push presence snapshots to the add-on viewport.
//
// The Gateway is the only writer of presence; the add-on only draws the rows it
// receives. A missing add-on or a stale session is ignored: presence is a
// best-effort visual aid, never a reason to fail a tool call.
import type { SessionRecord } from './types.js'

export interface PresencePush {
  instanceId: string
  agents: Record<string, unknown>[]
  paused: boolean
}

export type PresencePublisher = (push: PresencePush) => void

let publisher: PresencePublisher | null = null

export function setPresencePublisher(next: PresencePublisher | null): void {
  publisher = next
}

export function publishPresence(session: SessionRecord, rows: Record<string, unknown>[]): void {
  if (!publisher || session.kind !== 'user-gui' || !session.instanceId) return
  publisher({
    instanceId: session.instanceId,
    agents: rows,
    paused: session.paused,
  })
}
