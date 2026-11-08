// 3.1 GUI session registry: ownership, scope grants, presence, and the
// session-level guard used by every Gateway write route.
//
// This is intentionally in-memory: the Gateway owns the live process/session
// state, and a Gateway restart recreates it from addon handshakes. No scene data
// is stored here; presence only keeps caller-visible view poses and labels.

import { availabilityFailure } from '../availability.js'
import type { AvailabilityFailure } from '../availability.js'
import type { ScopeSpec } from './scopes.js'
import type { DisplayInstance, DisplayProvider } from '../../display/types.js'

export const OWNER_TTL_MS = 120_000
export const OWNER_HEARTBEAT_MS = 30_000
export const GRANT_TTL_MS = 30_000
export const GRANT_HEARTBEAT_MS = 10_000
export const PY_WAIT_MAX_MS = 5_000

export type SessionKind = 'user-gui' | 'virtual-gui'
export type SessionStatus = 'starting' | 'ready' | 'stopping' | 'stopped' | 'failed'

export interface CallerIdentity {
  hostId: string
  dshSessionId: string
  agentId: string
  parentAgentId?: string
  role?: 'main' | 'subagent'
  callId?: string
  turnId?: string
  displayName?: string
  color?: string
}

export interface InstanceRef {
  instanceId: string
  kind?: string
  ready: boolean
  project?: string
}

export interface ViewPose {
  viewpoint: 'viewport' | 'camera' | 'custom'
  from?: [number, number, number]
  lookAt?: [number, number, number]
  lens?: number
  ortho?: boolean
  orthoScale?: number
  camera?: string
  frame?: number
  viewMatrix?: number[]
  followUser?: boolean
  updatedAt: number
}

export interface AgentPresence {
  caller: CallerIdentity
  attachedAt: number
  lastSeen: number
  view: ViewPose | null
  selected: string[]
  activity: string
  color: string
  displayName: string
}

/** One object/collection the human changed since the last mark_read. */
export interface HumanEdit {
  kind: 'object' | 'collection'
  name: string
  revision: number
  at: number
}

export interface GrantRecord {
  id: string
  sessionId: string
  holder: CallerIdentity
  grantedBy: CallerIdentity
  scopes: ScopeSpec[]
  createdAt: number
  heartbeatAt: number
  expiresAt: number
}

export interface Owner {
  hostId: string
  dshSessionId: string
  agentId: string
  claimedAt: number
  heartbeatAt: number
}

export interface SessionRecord {
  id: string
  kind: SessionKind
  instanceId: string | null
  status: SessionStatus
  owner: Owner | null
  agents: Map<string, AgentPresence>
  grants: Map<string, GrantRecord>
  paused: boolean
  pausedAt: number | null
  activePy: { caller: CallerIdentity; startedAt: number } | null
  humanEdits: Map<string, HumanEdit>
  humanModifiedAt: number | null
  /** Set for virtual-gui: the provider handle and the provider that owns it. */
  display: DisplayInstance | null
  provider: DisplayProvider | null
  createdAt: number
  lastActivityAt: number
}

export type SessionResult<T> = ({ ok: true } & T) | AvailabilityFailure

export const COLORS = ['#4C8DFF', '#FF6B6B', '#2FBF71', '#F5A623', '#9B59B6', '#20B2AA']

export function fail(
  code: string,
  stage: 'config' | 'session' | 'queue',
  rootCause: string,
  fix: string,
  evidence: Record<string, unknown> = {},
): AvailabilityFailure {
  return availabilityFailure({
    stage,
    code,
    rootCause,
    fix,
    fallback: { kind: 'unavailable', summary: 'operation unavailable' },
    evidence,
  })
}

export function callerKey(c: CallerIdentity): string {
  return [c.hostId, c.dshSessionId, c.agentId].map((x) => String(x || '')).join('|')
}

export function ownerOf(c: CallerIdentity): Owner {
  return {
    hostId: String(c.hostId || ''),
    dshSessionId: String(c.dshSessionId || ''),
    agentId: String(c.agentId || ''),
    claimedAt: Date.now(),
    heartbeatAt: Date.now(),
  }
}

export function ownerMatches(owner: Owner | null, c: CallerIdentity): boolean {
  return !!owner && owner.hostId === c.hostId && owner.dshSessionId === c.dshSessionId
}

export function isMain(session: SessionRecord, c: CallerIdentity): boolean {
  return !!session.owner && session.owner.agentId === c.agentId && ownerMatches(session.owner, c)
}

export function colourFor(key: string): string {
  let h = 0
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0
  return COLORS[h % COLORS.length] as string
}

export function callerView(c: CallerIdentity): Record<string, unknown> {
  return {
    hostId: c.hostId,
    dshSessionId: c.dshSessionId,
    agentId: c.agentId,
    parentAgentId: c.parentAgentId || null,
    role: c.role || 'subagent',
  }
}

export function grantView(g: GrantRecord): Record<string, unknown> {
  return {
    id: g.id,
    holder: callerView(g.holder),
    grantedBy: callerView(g.grantedBy),
    scopes: g.scopes,
    createdAt: g.createdAt,
    expiresAt: g.expiresAt,
    expiresInMs: Math.max(0, g.expiresAt - Date.now()),
  }
}

export function sessionView(s: SessionRecord): Record<string, unknown> {
  return {
    id: s.id,
    kind: s.kind,
    instanceId: s.instanceId,
    status: s.status,
    owner: s.owner
      ? {
          hostId: s.owner.hostId,
          dshSessionId: s.owner.dshSessionId,
          agentId: s.owner.agentId,
          claimedAt: s.owner.claimedAt,
          heartbeatAt: s.owner.heartbeatAt,
          expiresInMs: Math.max(0, s.owner.heartbeatAt + OWNER_TTL_MS - Date.now()),
        }
      : null,
    paused: s.paused,
    agents: [...s.agents.values()].map((a) => ({
      agent: callerView(a.caller),
      displayName: a.displayName,
      color: a.color,
      attachedAt: a.attachedAt,
      lastSeen: a.lastSeen,
      view: a.view,
      selected: a.selected,
      activity: a.activity,
    })),
    grants: [...s.grants.values()].map(grantView),
    activePy: s.activePy
      ? { holder: callerView(s.activePy.caller), startedAt: s.activePy.startedAt }
      : null,
    display: s.display
      ? {
          id: s.display.id,
          provider: s.display.provider,
          display: s.display.display,
          pid: s.display.pid,
        }
      : null,
    humanModified: s.humanEdits.size
      ? {
          at: s.humanModifiedAt,
          objects: [...s.humanEdits.values()]
            .filter((edit) => edit.kind === 'object')
            .map((edit) => ({ name: edit.name, revision: edit.revision })),
          collections: [...s.humanEdits.values()]
            .filter((edit) => edit.kind === 'collection')
            .map((edit) => ({ name: edit.name, revision: edit.revision })),
        }
      : null,
    createdAt: s.createdAt,
    lastActivityAt: s.lastActivityAt,
  }
}
