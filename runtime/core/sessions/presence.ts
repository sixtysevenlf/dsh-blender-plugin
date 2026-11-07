// Agent presence rows for one GUI session.
//
// Presence is caller-visible state only: display name, colour, view pose,
// selection, activity and held scopes. Nothing here touches bpy; the add-on
// draws the frusta from the same payload.

import type { AgentPresence, CallerIdentity, SessionRecord, ViewPose } from './types.js'
import { callerKey, callerView, colourFor } from './types.js'

export function touchAgent(s: SessionRecord, c: CallerIdentity): AgentPresence {
  const key = callerKey(c)
  const now = Date.now()
  let agent = s.agents.get(key)
  if (!agent) {
    agent = {
      caller: { ...c },
      attachedAt: now,
      lastSeen: now,
      view: null,
      selected: [],
      activity: 'idle',
      color: String(c.color || colourFor(key)),
      displayName: String(c.displayName || 'agent-' + c.agentId),
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
  return presenceRows(s)
}

export function setSelection(
  s: SessionRecord,
  c: CallerIdentity,
  selected: string[],
  activity?: string,
): Record<string, unknown>[] {
  const agent = touchAgent(s, c)
  agent.selected = selected.map(String).slice(0, 256)
  if (activity) agent.activity = String(activity)
  return presenceRows(s)
}

export function presenceRows(s: SessionRecord): Record<string, unknown>[] {
  return [...s.agents.values()].map((a) => ({
    agent: callerView(a.caller),
    displayName: a.displayName,
    color: a.color,
    attachedAt: a.attachedAt,
    lastSeen: a.lastSeen,
    view: a.view,
    selected: a.selected,
    activity: a.activity,
  }))
}
