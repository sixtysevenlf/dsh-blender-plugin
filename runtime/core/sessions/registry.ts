import { claimExisting, createSession, pickInstance } from './attach.js'
import { startVirtualGuiGate } from './display-gate.js'
import { applyHumanPause } from './human.js'
import { presenceRows, setSelection, setView, touchAgent } from './presence.js'
import { pyTimeoutFailure, waitForPyLock } from './py-lock.js'
import { resolveGrantContext } from './grants.js'
import { displayCapabilities } from '../../display/prerequisites.js'
import type { DisplayCapabilities } from '../../display/types.js'
import { authorizeMainWrite, coveringGrant, evaluateWriteGate } from './write-gate.js'
import {
  GRANT_TTL_MS,
  OWNER_TTL_MS,
  callerKey,
  callerView,
  fail,
  grantView,
  isMain,
  ownerMatches,
  ownerOf,
  sessionView,
} from './types.js'
import type {
  CallerIdentity,
  GrantRecord,
  InstanceRef,
  SessionKind,
  SessionRecord,
  SessionResult,
  ViewPose,
} from './types.js'

export class SessionRegistry {
  private sessions = new Map<string, SessionRecord>()
  private instances: InstanceRef[] = []
  private humanPaused = new Map<string, boolean>()

  constructor(
    instances: () => InstanceRef[],
    display: () => DisplayCapabilities = displayCapabilities,
  ) {
    this.listInstances = instances
    this.display = display
  }
  private listInstances: () => InstanceRef[]
  private display: () => DisplayCapabilities

  refreshInstances(next: InstanceRef[]): void {
    this.instances = next
  }

  private prune(now = Date.now()): void {
    const instances = this.listInstances()
    this.instances = instances
    for (const s of this.sessions.values()) {
      if (s.owner && now - s.owner.heartbeatAt > OWNER_TTL_MS) {
        s.owner = null
        s.activePy = null
        s.grants.clear()
      }
      for (const [id, g] of s.grants) {
        if (g.expiresAt > now) continue
        // An active blender_py holds its exclusive grant for the whole call;
        // the 30s grant TTL must not expire under a long-running script.
        if (s.activePy && callerKey(g.holder) === callerKey(s.activePy.caller)) continue
        s.grants.delete(id)
      }
      if (s.kind === 'user-gui' && s.instanceId) {
        const inst = instances.find((x) => x.instanceId === s.instanceId)
        if (!inst || !inst.ready) {
          // A disconnected user-gui instance releases ownership immediately; a
          // reconnect claims fresh rather than inheriting a stale writer.
          s.owner = null
          s.agents.clear()
          s.grants.clear()
          s.activePy = null
          s.paused = false
          s.status = inst ? 'starting' : 'stopped'
        }
      }
    }
  }

  private findForCaller(c: CallerIdentity): SessionRecord | null {
    for (const s of this.sessions.values()) {
      if (ownerMatches(s.owner, c)) return s
      if (s.agents.has(callerKey(c))) return s
    }
    return null
  }

  private ensureAttached(
    c: CallerIdentity,
    opts: { instanceId?: string; kind?: SessionKind } = {},
  ): SessionResult<{ session: SessionRecord }> {
    this.prune()
    const requested = opts.instanceId ? 'user-gui:' + String(opts.instanceId) : ''
    const existing = requested ? (this.sessions.get(requested) ?? null) : this.findForCaller(c)
    if (existing) return this.adopt(claimExisting(existing, c), c)
    const picked = pickInstance(this.listInstances(), opts)
    if (!('instance' in picked)) return picked
    const known = this.sessions.get('user-gui:' + picked.instance.instanceId)
    if (known) return this.adopt(claimExisting(known, c), c)
    const created = createSession(picked.instance, c)
    created.paused = this.humanPaused.get(picked.instance.instanceId) === true
    if (created.paused) created.pausedAt = Date.now()
    this.sessions.set(created.id, created)
    touchAgent(created, c)
    return { ok: true, session: created }
  }

  private adopt(
    result: SessionResult<{ session: SessionRecord }>,
    c: CallerIdentity,
  ): SessionResult<{ session: SessionRecord }> {
    if (result.ok) touchAgent(result.session, c)
    return result
  }

  list(): Record<string, unknown>[] {
    this.prune()
    return [...this.sessions.values()].map(sessionView)
  }

  listFor(c: CallerIdentity): Record<string, unknown>[] {
    const s = this.findForCaller(c)
    return s ? [sessionView(s)] : []
  }

  status(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ session: Record<string, unknown> }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session)
      return fail(
        'session_not_found',
        'session',
        'the session does not exist',
        'call blender_session list to see live sessions',
        { sessionId: sessionId || null },
      )
    if (ownerMatches(session.owner, c) || session.agents.has(callerKey(c))) touchAgent(session, c)
    return { ok: true, session: sessionView(session) }
  }

  attach(
    c: CallerIdentity,
    opts: { instanceId?: string; kind?: SessionKind } = {},
  ): SessionResult<{ session: Record<string, unknown> }> {
    if (opts.kind && opts.kind !== 'user-gui') {
      return fail(
        'invalid_params',
        'session',
        'attach only applies to user-gui; virtual-gui sessions are started by the Gateway',
        'use blender_session start kind=virtual-gui',
        { kind: opts.kind },
      )
    }
    const r = this.ensureAttached(c, opts)
    if (!('session' in r)) return r
    return { ok: true, session: sessionView(r.session) }
  }

  claim(
    c: CallerIdentity,
    opts: { instanceId?: string } = {},
  ): SessionResult<{ session: Record<string, unknown> }> {
    return this.attach(c, opts)
  }

  detach(c: CallerIdentity, sessionId?: string): SessionResult<{ released: boolean }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session) return { ok: true, released: false }
    if (session.owner && ownerMatches(session.owner, c) && isMain(session, c)) {
      this.releaseSession(session)
      return { ok: true, released: true }
    }
    session.agents.delete(callerKey(c))
    return { ok: true, released: false }
  }

  release(c: CallerIdentity, sessionId?: string): SessionResult<{ released: boolean }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session) return { ok: true, released: false }
    if (!ownerMatches(session.owner, c)) {
      return fail(
        'session_in_use',
        'session',
        'only the owning DSH session can release this GUI session',
        'ask the owner to release it',
        { sessionId: session.id, owner: session.owner },
      )
    }
    this.releaseSession(session)
    return { ok: true, released: true }
  }

  private releaseSession(s: SessionRecord): void {
    s.owner = null
    s.agents.clear()
    s.grants.clear()
    s.activePy = null
    s.paused = false
    s.pausedAt = null
    s.status = 'stopped'
  }

  heartbeat(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ session: Record<string, unknown> }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
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
    if (!session.owner) session.owner = ownerOf(c)
    session.owner.heartbeatAt = Date.now()
    touchAgent(session, c)
    return { ok: true, session: sessionView(session) }
  }

  doctor(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{
    readiness: Record<string, unknown>
    session: Record<string, unknown> | null
  }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    const ready = this.listInstances().filter((x) => x.ready)
    return {
      ok: true,
      readiness: {
        ready:
          !!session &&
          session.status === 'ready' &&
          !!session.owner &&
          ownerMatches(session.owner, c),
        session_ready: !!session && session.status === 'ready',
        instances: ready.length,
        sessionId: session ? session.id : null,
        owner: session ? session.owner : null,
        paused: session ? session.paused : false,
        activePy: session && session.activePy ? callerView(session.activePy.caller) : null,
      },
      session: session ? sessionView(session) : null,
    }
  }

  start(
    c: CallerIdentity,
    opts: { kind?: SessionKind; consentToken?: string } = {},
  ): SessionResult<Record<string, unknown>> {
    const kind = opts.kind || 'virtual-gui'
    if (kind !== 'virtual-gui')
      return fail(
        'invalid_params',
        'session',
        'start only applies to virtual-gui',
        'use attach for a user-started Blender',
        { kind },
      )
    return startVirtualGuiGate(this.display())
  }

  stop(c: CallerIdentity, sessionId?: string): SessionResult<{ stopped: boolean }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session) return { ok: true, stopped: false }
    if (session.kind !== 'virtual-gui') {
      return fail(
        'invalid_params',
        'session',
        'stop does not control a user-started Blender',
        'close Blender yourself; stop only manages virtual-gui',
        { sessionId: session.id },
      )
    }
    if (!ownerMatches(session.owner, c)) {
      return fail(
        'session_in_use',
        'session',
        'only the owning DSH session can stop this virtual-gui session',
        'ask the owner to stop it',
        { sessionId: session.id },
      )
    }
    this.releaseSession(session)
    return { ok: true, stopped: true }
  }

  grant(
    c: CallerIdentity,
    sessionId: string,
    scopes: unknown,
    target?: CallerIdentity,
    ttlMs?: number,
  ): SessionResult<{ grant: Record<string, unknown> }> {
    this.prune()
    const session = this.sessions.get(String(sessionId))
    if (!session)
      return fail('session_not_found', 'session', 'the session does not exist', 'attach first', {})
    const ctx = resolveGrantContext(session, c, scopes, target)
    if (!('holder' in ctx)) return ctx
    const { parsed, holder } = ctx
    const now = Date.now()
    const requestedTtl = Number(ttlMs)
    const ttl =
      Number.isFinite(requestedTtl) && requestedTtl > 0
        ? Math.min(GRANT_TTL_MS, requestedTtl)
        : GRANT_TTL_MS
    const grant: GrantRecord = {
      id: 'grant_' + Math.random().toString(16).slice(2, 10),
      sessionId: session.id,
      holder,
      grantedBy: { ...c },
      scopes: parsed,
      createdAt: now,
      heartbeatAt: now,
      expiresAt: now + ttl,
    }
    for (const [existingId, existing] of session.grants) {
      if (callerKey(existing.holder) === callerKey(holder)) session.grants.delete(existingId)
    }
    session.grants.set(grant.id, grant)
    session.lastActivityAt = now
    return { ok: true, grant: grantView(grant) }
  }

  revoke(
    c: CallerIdentity,
    sessionId: string,
    grantId?: string,
  ): SessionResult<{ revoked: string[] }> {
    this.prune()
    const session = this.sessions.get(String(sessionId))
    if (!session)
      return fail('session_not_found', 'session', 'the session does not exist', 'attach first', {})
    if (!isMain(session, c))
      return fail(
        'scope_forbidden',
        'session',
        'only the main agent may revoke scopes',
        'ask the main agent to revoke',
        {},
      )
    const revoked: string[] = []
    for (const [id, g] of session.grants) {
      if (grantId && id !== String(grantId)) continue
      if (!grantId && callerKey(g.holder) === callerKey(c)) continue
      session.grants.delete(id)
      revoked.push(id)
    }
    return { ok: true, revoked }
  }

  grants(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ grants: Record<string, unknown>[] }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session) return { ok: true, grants: [] }
    if (!ownerMatches(session.owner, c))
      return fail(
        'session_in_use',
        'session',
        'another DSH session owns this GUI session',
        'wait for the owner to release it',
        { sessionId: session.id },
      )
    const all = [...session.grants.values()]
    const visible = isMain(session, c)
      ? all
      : all.filter((g) => callerKey(g.holder) === callerKey(c))
    return { ok: true, grants: visible.map(grantView) }
  }

  pause(c: CallerIdentity, _sessionId?: string): SessionResult<{ paused: boolean }> {
    return fail(
      'human_action_required',
      'session',
      'agent calls cannot pause Blender writes',
      'ask the human to pause AI operations in the Blender N-panel',
      { caller: callerView(c) },
    )
  }

  resume(c: CallerIdentity, _sessionId?: string): SessionResult<{ paused: boolean }> {
    return fail(
      'human_action_required',
      'session',
      'only the human can resume agent writes in Blender',
      'resume AI operations in the Blender N-panel',
      { caller: callerView(c) },
    )
  }

  private mustOwn(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ session: SessionRecord }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session)
      return fail('session_not_found', 'session', 'the session does not exist', 'attach first', {})
    if (!ownerMatches(session.owner, c))
      return fail(
        'session_in_use',
        'session',
        'another DSH session owns this GUI session',
        'wait for the owner to release it',
        {},
      )
    return { ok: true, session }
  }

  presence(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ presence: Record<string, unknown>[] }> {
    const r = this.status(c, sessionId)
    if (!('session' in r)) return r
    const session = this.sessions.get(String(r.session.id))
    return { ok: true, presence: session ? presenceRows(session) : [] }
  }

  updateView(
    c: CallerIdentity,
    sessionId: string,
    view: Omit<ViewPose, 'updatedAt'>,
  ): SessionResult<{ presence: Record<string, unknown>[] }> {
    const r = this.writableSession(c, sessionId)
    if (!('session' in r)) return r
    return { ok: true, presence: setView(r.session, c, view) }
  }

  updateSelection(
    c: CallerIdentity,
    sessionId: string,
    selected: string[],
    activity?: string,
  ): SessionResult<{ presence: Record<string, unknown>[] }> {
    this.prune()
    const r = this.writableSession(c, sessionId)
    if (!('session' in r)) return r
    return { ok: true, presence: setSelection(r.session, c, selected, activity) }
  }

  private writableSession(
    c: CallerIdentity,
    sessionId: string,
  ): SessionResult<{ session: SessionRecord }> {
    this.prune()
    const session = this.sessions.get(String(sessionId))
    if (!session)
      return fail('session_not_found', 'session', 'the session does not exist', 'attach first', {})
    if (!ownerMatches(session.owner, c))
      return fail(
        'session_in_use',
        'session',
        'another DSH session owns this GUI session',
        'wait for the owner to release it',
        {},
      )
    return { ok: true, session }
  }

  /** Authorize one write call. Returns the owning session or a structured failure. */
  authorizeWrite(
    c: CallerIdentity,
    sessionId: string | undefined,
    scopes: unknown,
    opts: { py?: boolean; waitMs?: number } = {},
  ): SessionResult<{ sessionId: string; mode: 'main' | 'grant'; grantId?: string }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session) {
      return fail(
        'session_unavailable',
        'session',
        'no GUI session is attached for this caller',
        'attach a ready Blender session first',
        {},
      )
    }
    if (!ownerMatches(session.owner, c)) {
      return fail(
        'session_in_use',
        'session',
        'another DSH session owns this GUI session',
        'wait for the owner to release it',
        { sessionId: session.id, owner: session.owner },
      )
    }
    const gate = evaluateWriteGate(session, c, scopes, opts)
    if (!('effective' in gate)) return gate
    const { effective } = gate
    if (isMain(session, c)) {
      const main = authorizeMainWrite(session, c, effective)
      if (!main.ok) return main
      return { ok: true, sessionId: session.id, mode: 'main' }
    }
    const grant = coveringGrant(session, c, effective)
    if (grant) return { ok: true, sessionId: session.id, mode: 'grant', grantId: grant.id }
    return fail(
      'scope_required',
      'session',
      'the write scope was not granted to this agent',
      'ask the main agent to grant the declared scope',
      {
        requested: effective,
        held: [...session.grants.values()]
          .filter((g) => callerKey(g.holder) === callerKey(c))
          .map(grantView),
      },
    )
  }

  async beginPy(
    c: CallerIdentity,
    sessionId?: string,
    waitMs = 0,
  ): Promise<SessionResult<{ sessionId: string }>> {
    const r = await waitForPyLock(
      () => this.authorizeWrite(c, sessionId, [{ kind: 'exclusive' }], { py: true }),
      waitMs,
    )
    if (!('sessionId' in r)) return r
    const session = this.sessions.get(r.sessionId)
    if (!session)
      return fail('session_not_found', 'session', 'the session disappeared', 're-attach', {})
    session.activePy = { caller: { ...c }, startedAt: Date.now() }
    return { ok: true, sessionId: session.id }
  }

  /** Report a timed-out Python call without releasing the lock. */
  timeoutPy(c: CallerIdentity, sessionId?: string): SessionResult<{ running: boolean }> {
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    return session ? pyTimeoutFailure(session, c) : { ok: true, running: false }
  }

  applyHumanPause(instanceId: string | undefined, paused: boolean): number {
    const id = String(instanceId || '')
    if (id) this.humanPaused.set(id, paused)
    return applyHumanPause(this.sessions, instanceId, paused)
  }

  endPy(c: CallerIdentity, sessionId?: string): SessionResult<{ released: boolean }> {
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session || !session.activePy) return { ok: true, released: false }
    if (callerKey(session.activePy.caller) !== callerKey(c) && !isMain(session, c)) {
      return fail(
        'scope_forbidden',
        'session',
        'only the holder or the main agent can end Python execution',
        'ask the holder to finish',
        {},
      )
    }
    session.activePy = null
    return { ok: true, released: true }
  }
}
