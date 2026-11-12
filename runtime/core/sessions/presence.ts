// Agent presence rows for one GUI session.
//
// Presence is caller-visible state only: display name, colour, view pose,
// selection, activity and held scopes. Nothing here touches bpy; the add-on
// draws the frusta from the same payload.

import { createHash } from 'node:crypto'
import { publishPresence } from './presence-publish.js'
import type { AgentPresence, CallerIdentity, SessionRecord, ViewPose } from './types.js'
import { callerKey, callerView, colourFor } from './types.js'

const MAX_AGENTS = 8

export function presenceId(c: CallerIdentity): string {
  return createHash('sha1').update(callerKey(c)).digest('hex').slice(0, 12)
}

function nextShortId(s: SessionRecord): string {
  const used = new Set([...s.agents.values()].map((a) => a.shortId))
  for (let i = 1; i <= MAX_AGENTS; i++) {
    const id = 'A' + i
    if (!used.has(id)) return id
  }
  // Do not derive the id from the current agent count: after deletions the
  // count can collide with a live short-id slot (delete one of the first eight,
  // then the count can equal a slot still in use).
  let n = MAX_AGENTS + 1
  while (used.has('A' + n)) n++
  return 'A' + n
}

export function touchAgent(s: SessionRecord, c: CallerIdentity): AgentPresence {
  const key = callerKey(c)
  const now = Date.now()
  // Visibility outlives presence: a session that loses its owner (instance drop,
  // TTL) must stay readable by the agents that were attached to it.
  s.claimants.add(key)
  s.orphanedAt = null
  let agent = s.agents.get(key)
  if (!agent) {
    agent = {
      caller: { ...c },
      id: presenceId(c),
      shortId: nextShortId(s),
      attachedAt: now,
      lastSeen: now,
      view: null,
      selected: [],
      activity: 'attached',
      lastActivityAt: now,
      lastActivity: 'attached',
      color: String(c.color || colourFor(key)),
      displayName: String(c.displayName || c.agentId || 'agent'),
    }
    s.agents.set(key, agent)
  } else {
    agent.caller = { ...agent.caller, ...c }
    agent.lastSeen = now
  }
  s.lastActivityAt = now
  return agent
}

export function setView(
  s: SessionRecord,
  c: CallerIdentity,
  view: Omit<ViewPose, 'updatedAt'>,
): Record<string, unknown>[] {
  const agent = touchAgent(s, c)
  agent.view = { ...view, updatedAt: Date.now() }
  agent.activity = 'see:' + view.viewpoint
  agent.lastActivityAt = agent.view.updatedAt
  agent.lastActivity = agent.activity
  const rows = presenceRows(s)
  publishPresence(s, rows)
  return rows
}

export function setSelection(
  s: SessionRecord,
  c: CallerIdentity,
  selected: string[],
  activity?: string,
): Record<string, unknown>[] {
  const agent = touchAgent(s, c)
  agent.selected = selected.map(String).slice(0, 256)
  agent.activity = activity ? String(activity) : 'selection'
  agent.lastActivityAt = Date.now()
  agent.lastActivity = agent.activity
  const rows = presenceRows(s)
  publishPresence(s, rows)
  return rows
}

export function setNotice(s: SessionRecord, text: string): Record<string, unknown>[] {
  const at = Date.now()
  s.notice = { seq: (s.notice?.seq || 0) + 1, text: String(text).slice(0, 200), at }
  s.lastActivityAt = at
  const rows = presenceRows(s)
  publishPresence(s, rows)
  return rows
}

export function presenceRows(s: SessionRecord): Record<string, unknown>[] {
  return [...s.agents.values()].map((a) => ({
    agent: callerView(a.caller),
    id: a.id,
    shortId: a.shortId,
    displayName: a.displayName,
    color: a.color,
    attachedAt: a.attachedAt,
    lastSeen: a.lastSeen,
    view: a.view,
    selected: a.selected,
    activity: a.activity,
    lastActivityAt: a.lastActivityAt,
    lastActivity: a.lastActivity,
    heldScopes: [...s.grants.values()]
      .filter((g) => callerKey(g.holder) === callerKey(a.caller))
      .map((g) => g.scopes),
  }))
}
