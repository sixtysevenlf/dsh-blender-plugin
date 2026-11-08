import type { CallerIdentity, InstanceRef, SessionRecord, SessionResult } from './types.js'
import { fail, ownerMatches, ownerOf } from './types.js'

export function claimExisting(
  session: SessionRecord,
  c: CallerIdentity,
): SessionResult<{ session: SessionRecord }> {
  if (session.owner && !ownerMatches(session.owner, c)) {
    return fail(
      'session_in_use',
      'session',
      'another DSH session owns this GUI session',
      'wait for the owner to release it, or attach another Blender instance',
      { sessionId: session.id, owner: session.owner },
    )
  }
  if (!session.owner) session.owner = ownerOf(c)
  session.status = 'ready'
  session.owner.heartbeatAt = Date.now()
  return { ok: true, session }
}

export function pickInstance(
  instances: InstanceRef[],
  opts: { instanceId?: string },
): SessionResult<{ instance: InstanceRef }> {
  const ready = instances.filter((x) => x.ready)
  if (!ready.length) {
    return fail(
      'session_unavailable',
      'session',
      'no ready GUI session is attached to the Gateway',
      'start Blender with DSH Blender Bridge 2.0.0, or start a virtual-gui session after approval',
      { ready_instances: 0 },
    )
  }
  if (!opts.instanceId && ready.length > 1) {
    return fail(
      'session_ambiguity',
      'session',
      'multiple Blender instances are connected',
      'pass instanceId to choose one; omitting it is allowed only with exactly one ready instance',
      { instances: ready.map((x) => x.instanceId) },
    )
  }
  const instance = opts.instanceId ? ready.find((x) => x.instanceId === opts.instanceId) : ready[0]
  if (!instance) {
    return fail(
      'session_not_found',
      'session',
      'the requested Blender instance is not connected',
      'call blender_session list and attach an instanceId that is ready',
      { instanceId: opts.instanceId, ready: ready.map((x) => x.instanceId) },
    )
  }
  return { ok: true, instance }
}

export function createSession(instance: InstanceRef, c: CallerIdentity): SessionRecord {
  const now = Date.now()
  return {
    id: 'user-gui:' + instance.instanceId,
    kind: 'user-gui',
    instanceId: instance.instanceId,
    status: 'ready',
    owner: ownerOf(c),
    agents: new Map(),
    grants: new Map(),
    paused: false,
    pausedAt: null,
    activePy: null,
    humanEdits: new Map(),
    humanModifiedAt: null,
    display: null,
    provider: null,
    createdAt: now,
    lastActivityAt: now,
  }
}
