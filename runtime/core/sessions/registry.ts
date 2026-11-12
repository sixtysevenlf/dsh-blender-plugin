import { claimExisting, createSession, pickInstance } from './attach.js'
import { CFG } from '../../config.js'
import { selectDisplayProvider } from '../../display/provider.js'
import { systemVirtualGuiLimits } from '../../display/limits.js'
import type { VirtualGuiLimits } from '../../display/limits.js'
import { applyHumanPause } from './human.js'
import { applyHumanEditsToSessions, markHumanRead } from './human-edits.js'
import type { HumanEditInput } from './human-edits.js'
import { presenceRows, touchAgent } from './presence.js'
import { publishPresence } from './presence-publish.js'
import {
  doctorFor,
  noticeFor,
  presenceFor,
  updateSelectionFor,
  updateViewFor,
} from './presence-ops.js'
import type { ObservableHost } from './presence-ops.js'
import { pyTimeoutFailure, waitForPyLock } from './py-lock.js'
import { grantTo, listGrants, revokeFrom } from './grant-ops.js'
import type { GrantOpsHost } from './grant-ops.js'
import { displayCapabilities } from '../../display/prerequisites.js'
import type { DisplayCapabilities, DisplayProvider } from '../../display/types.js'
import { authorizeMainWrite, coveringGrant, evaluateWriteGate } from './write-gate.js'
import {
  callerKey,
  callerView,
  fail,
  grantView,
  heartbeatOwner,
  isMain,
  ownerMatches,
  ownerOf,
  sessionView,
} from './types.js'
import { heartbeatSession } from './heartbeat.js'
import { findCallerSession, pruneSessions } from './lookup.js'
import {
  disposeInBackground,
  foreignSession,
  handleInstanceGone,
  reapOrphans,
  removeSession,
  virtualGuiCount as virtualGuiCountOf,
  visibleCaller,
} from './visibility.js'
import { VirtualGuiLifecycle, waitForAddonInstance } from './virtual-gui.js'
import type { ProviderSelector } from './virtual-gui.js'
import type {
  CallerIdentity,
  InstanceRef,
  SessionKind,
  SessionRecord,
  SessionResult,
  ViewPose,
} from './types.js'

export interface SessionRegistryOptions {
  display?: () => DisplayCapabilities
  selectProvider?: ProviderSelector
  waitForInstance?: (instanceId: string, timeoutMs: number) => Promise<boolean>
  limits?: () => VirtualGuiLimits
}

function defaultProviderSelector(capabilities: DisplayCapabilities): DisplayProvider | null {
  return selectDisplayProvider({ capabilities }).provider
}

export class SessionRegistry {
  private sessions = new Map<string, SessionRecord>()
  private instances: InstanceRef[] = []
  private humanPaused = new Map<string, boolean>()

  constructor(
    instances: () => InstanceRef[],
    options: (() => DisplayCapabilities) | SessionRegistryOptions = {},
  ) {
    const opts = typeof options === 'function' ? { display: options } : options
    this.listInstances = instances
    this.displayFn = opts.display || displayCapabilities
    this.limitsFn = opts.limits || (() => systemVirtualGuiLimits(CFG.features.virtualGuiMax))
    this.waitForInstanceFn =
      opts.waitForInstance ||
      ((instanceId, timeoutMs) =>
        waitForAddonInstance(() => this.listInstances(), instanceId, timeoutMs))
    this.lifecycle = new VirtualGuiLifecycle(this, opts.selectProvider || defaultProviderSelector)
  }
  private listInstances: () => InstanceRef[]
  private readonly displayFn: () => DisplayCapabilities
  private readonly limitsFn: () => VirtualGuiLimits
  private readonly waitForInstanceFn: (instanceId: string, timeoutMs: number) => Promise<boolean>
  private readonly lifecycle: VirtualGuiLifecycle

  display(): DisplayCapabilities {
    return this.displayFn()
  }

  refreshInstances(next: InstanceRef[]): void {
    this.instances = next
  }

  guiConnectionCounts(): { total: number; userGui: number; virtualGui: number } {
    this.prune()
    const ready = this.instances.filter((instance) => instance.ready)
    const userGui = ready.filter((instance) => instance.kind === 'user-gui').length
    const virtualGui = ready.filter((instance) => instance.kind === 'virtual-gui').length
    return { total: ready.length, userGui, virtualGui }
  }

  guiConnections(): number {
    return this.guiConnectionCounts().total
  }

  private prune(now = Date.now()): void {
    this.instances = pruneSessions(this.sessions, this.listInstances, now)
    reapOrphans(this.sessions, this.lifecycle, now)
  }

  findForCaller(c: CallerIdentity): SessionRecord | null {
    return findCallerSession(this.sessions, c)
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
    const knownVirtual = this.sessions.get('virtual-gui:' + picked.instance.instanceId)
    if (knownVirtual) {
      if (knownVirtual.status !== 'ready')
        return fail(
          'session_unavailable',
          'session',
          'the virtual-gui session is not ready yet',
          'wait for the addon handshake, or check blender_session status',
          { sessionId: knownVirtual.id },
        )
      if (!ownerMatches(knownVirtual.owner, c))
        return fail(
          'session_in_use',
          'session',
          'another DSH session owns this virtual-gui session',
          'ask the owner to release it',
          { sessionId: knownVirtual.id },
        )
      return this.adopt(claimExisting(knownVirtual, c), c)
    }
    const known = this.sessions.get('user-gui:' + picked.instance.instanceId)
    if (known) return this.adopt(claimExisting(known, c), c)
    if (c.role !== 'main') {
      return fail(
        'owner_required',
        'session',
        'only the main agent may claim a GUI session',
        'ask the main agent to call blender_session action=attach first',
        { instanceId: picked.instance.instanceId },
      )
    }
    // A virtual-gui instance is owned by the DSH session that started it: if its
    // session record is gone (release window or a reconnect raced the reap), it
    // must not be re-created as a user-gui session that anyone can drive.
    if (picked.instance.kind === 'virtual-gui')
      return fail(
        'session_unavailable',
        'session',
        'the virtual-gui instance has no live session record',
        'call blender_session action=start kind=virtual-gui again (or action=stop to clean it up); a virtual-gui is never attached as user-gui',
        { instanceId: picked.instance.instanceId, kind: 'virtual-gui' },
      )
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
    this.prune()
    return [...this.sessions.values()].filter((s) => this.visible(c, s)).map(sessionView)
  }

  /** Owner or attached agent: the only callers allowed to read a session view. */
  private visible(c: CallerIdentity, session: SessionRecord): boolean {
    return visibleCaller(session, c)
  }

  status(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ session: Record<string, unknown> }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    // A session the caller cannot see is not readable by naming its id: the view
    // carries the owner, attached agents and grants.
    if (session && !this.visible(c, session)) return foreignSession(session)
    if (!session)
      return fail(
        'session_not_found',
        'session',
        'the session does not exist',
        'call blender_session list to see live sessions',
        { sessionId: sessionId || null },
      )
    if (ownerMatches(session.owner, c)) heartbeatOwner(session, c)
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
    // A detached agent must not keep write grants: they would block other agents
    // for the whole grant TTL after the agent that owned them is gone.
    for (const [id, grant] of session.grants)
      if (callerKey(grant.holder) === callerKey(c)) session.grants.delete(id)
    publishPresence(session, presenceRows(session))
    return { ok: true, released: false }
  }

  release(c: CallerIdentity, sessionId?: string): SessionResult<{ released: boolean }> {
    this.prune()
    const session = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    if (!session) return { ok: true, released: false }
    const attachedMain = c.role === 'main' && session.agents.has(callerKey(c)) && !session.owner
    if (!isMain(session, c) && !attachedMain) {
      return fail(
        'session_in_use',
        'session',
        'only the main agent may release this GUI session',
        'ask the main agent to release it',
        { sessionId: session.id, owner: session.owner },
      )
    }
    this.releaseSession(session)
    return { ok: true, released: true }
  }

  releaseSession(s: SessionRecord): void {
    if (s.agents.size > 0) publishPresence(s, [])
    s.owner = null
    s.agents.clear()
    s.grants.clear()
    s.activePy = null
    s.paused = false
    s.pausedAt = null
    s.humanEdits.clear()
    s.humanModifiedAt = null
    s.status = 'stopped'
    if (s.kind === 'virtual-gui') {
      // Releasing the owner tears the provider down too; stop() awaits the same
      // dispose path, while detach/release can stay synchronous at the API edge.
      this.sessions.delete(s.id)
      disposeInBackground(this.lifecycle, s)
    }
  }

  getSession(sessionId: string): SessionRecord | null {
    return this.sessions.get(String(sessionId)) ?? null
  }

  virtualGuiCount(): number {
    return virtualGuiCountOf(this.sessions)
  }

  addSession(session: SessionRecord): void {
    this.sessions.set(session.id, session)
  }

  removeSession(sessionId: string): void {
    removeSession(this.sessions, sessionId)
  }

  /** The addon socket dropped: a virtual-gui session for it cannot outlive it. */
  handleInstanceGone(instanceId: string): void {
    handleInstanceGone(this.sessions, this.lifecycle, instanceId)
  }

  limits(): VirtualGuiLimits {
    return this.limitsFn()
  }

  waitForInstance(instanceId: string, timeoutMs: number): Promise<boolean> {
    return this.waitForInstanceFn(instanceId, timeoutMs)
  }

  heartbeat(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ session: Record<string, unknown> }> {
    this.prune()
    const found = sessionId ? this.sessions.get(String(sessionId)) : this.findForCaller(c)
    return heartbeatSession(found ?? null, c)
  }

  doctor(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{
    readiness: Record<string, unknown>
    session: Record<string, unknown> | null
  }> {
    this.prune()
    const session = sessionId
      ? (this.sessions.get(String(sessionId)) ?? null)
      : this.findForCaller(c)
    if (session && !this.visible(c, session)) return foreignSession(session)
    return { ok: true, ...doctorFor(c, session, this.listInstances()) }
  }

  async start(
    c: CallerIdentity,
    opts: { kind?: SessionKind } = {},
  ): Promise<SessionResult<{ session: Record<string, unknown> }>> {
    this.prune()
    return this.lifecycle.start(c, opts)
  }
  async stop(c: CallerIdentity, sessionId?: string): Promise<SessionResult<{ stopped: boolean }>> {
    this.prune()
    return this.lifecycle.stop(c, sessionId)
  }

  grant(
    c: CallerIdentity,
    sessionId: string,
    scopes: unknown,
    target?: CallerIdentity,
    ttlMs?: number,
  ): SessionResult<{ grant: Record<string, unknown> }> {
    return grantTo(this.grantHost(), c, sessionId, scopes, target, ttlMs)
  }

  revoke(
    c: CallerIdentity,
    sessionId: string,
    grantId?: string,
  ): SessionResult<{ revoked: string[] }> {
    return revokeFrom(this.grantHost(), c, sessionId, grantId)
  }

  grants(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ grants: Record<string, unknown>[] }> {
    return listGrants(this.grantHost(), c, sessionId)
  }

  private grantHost(): GrantOpsHost {
    return {
      sessions: this.sessions,
      prune: () => this.prune(),
      findForCaller: (caller) => this.findForCaller(caller),
    }
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

  presence(
    c: CallerIdentity,
    sessionId?: string,
  ): SessionResult<{ presence: Record<string, unknown>[] }> {
    return presenceFor(this.observableHost(), c, sessionId)
  }

  updateView(
    c: CallerIdentity,
    sessionId: string,
    view: Omit<ViewPose, 'updatedAt'>,
  ): SessionResult<{ presence: Record<string, unknown>[] }> {
    return updateViewFor(this.observableHost(), c, sessionId, view)
  }

  notice(
    c: CallerIdentity,
    sessionId: string,
    text: string,
  ): SessionResult<{ presence: Record<string, unknown>[] }> {
    return noticeFor(this.observableHost(), c, sessionId, text)
  }

  updateSelection(
    c: CallerIdentity,
    sessionId: string,
    selected: string[],
    activity?: string,
  ): SessionResult<{ presence: Record<string, unknown>[] }> {
    return updateSelectionFor(this.observableHost(), c, sessionId, selected, activity)
  }

  private observableHost(): ObservableHost {
    return { sessions: this.sessions, prune: () => this.prune() }
  }

  /** Authorize one write call. Returns the owning session or a structured failure. */
  authorizeWrite(
    c: CallerIdentity,
    sessionId: string | undefined,
    scopes: unknown,
    opts: { py?: boolean; pyLock?: boolean; waitMs?: number } = {},
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
    // Attached, not merely same-DSH-session: a detached agent must not write even
    // though it shares hostId/dshSessionId with the owner.
    const attached = session.agents.has(callerKey(c))
    const ownerAgent =
      !!session.owner && ownerMatches(session.owner, c) && session.owner.agentId === c.agentId
    if (!ownerMatches(session.owner, c) || (!attached && !ownerAgent)) {
      // The owner heartbeat expires on a long idle gap (OWNER_TTL_MS). A caller
      // already attached to this session may reclaim the free write lease
      // instead of failing the first py/op call after the gap.
      if (!session.owner && c.role === 'main' && session.agents.has(callerKey(c))) {
        session.owner = ownerOf(c)
        touchAgent(session, c)
      } else if (ownerMatches(session.owner, c) && !attached) {
        return fail(
          'session_in_use',
          'session',
          'this agent is not attached to the GUI session (a detached agent cannot write)',
          'call blender_session action=attach for this session first',
          { sessionId: session.id, caller: callerView(c) },
        )
      } else {
        return fail(
          'session_in_use',
          'session',
          'another DSH session owns this GUI session',
          'wait for the owner to release it',
          { sessionId: session.id, owner: session.owner },
        )
      }
    }
    const gate = evaluateWriteGate(session, c, scopes, opts)
    if (!('effective' in gate)) return gate
    const { effective } = gate
    if (isMain(session, c)) {
      const main = authorizeMainWrite(session, c, effective)
      if (!main.ok) return main
      heartbeatOwner(session, c)
      return { ok: true, sessionId: session.id, mode: 'main' }
    }
    const grant = coveringGrant(session, c, effective)
    if (grant) {
      const now = Date.now()
      grant.heartbeatAt = now
      grant.expiresAt = Math.max(grant.expiresAt, now + 30_000)
      return { ok: true, sessionId: session.id, mode: 'grant', grantId: grant.id }
    }
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

  applyHumanEdits(instanceId: string | undefined, edits: HumanEditInput[]): number {
    return applyHumanEditsToSessions(this.sessions, instanceId, edits)
  }
  markRead(
    c: CallerIdentity,
    sessionId: string | undefined,
    scopes: unknown,
  ): SessionResult<{ cleared: number }> {
    this.prune()
    return markHumanRead(this.sessions, c, sessionId, scopes)
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
