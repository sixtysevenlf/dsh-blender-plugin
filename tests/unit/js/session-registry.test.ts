import { afterEach, describe, expect, it, vi } from 'vitest'
import { SessionRegistry } from '../../../runtime/core/sessions/registry.js'
import type { CallerIdentity, InstanceRef } from '../../../runtime/core/sessions/types.js'

const main = (over: Partial<CallerIdentity> = {}): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-1',
  agentId: 'agent-main',
  role: 'main',
  ...over,
})
const sub = (over: Partial<CallerIdentity> = {}): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-1',
  agentId: 'agent-sub',
  parentAgentId: 'agent-main',
  role: 'subagent',
  ...over,
})
const otherSession = (over: Partial<CallerIdentity> = {}): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-2',
  agentId: 'agent-other',
  role: 'main',
  ...over,
})

function setup(instances: InstanceRef[] = [{ instanceId: 'inst-1', ready: true }]) {
  const state = { instances }
  const registry = new SessionRegistry(() => state.instances)
  return { registry, state }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('3.1 GUI session registry', () => {
  it('attaches the only ready user-gui instance and records ownership', () => {
    const { registry } = setup()
    const r = registry.attach(main())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.session.id).toBe('user-gui:inst-1')
    expect((r.session.owner as Record<string, unknown>).dshSessionId).toBe('dsh-1')
    expect((r.session.agents as unknown[]).length).toBe(1)
  })

  it('never creates a user-gui session for a virtual-gui instance', () => {
    const { registry } = setup([{ instanceId: 'vgui-1', kind: 'virtual-gui', ready: true }])
    const r = registry.attach(main(), { instanceId: 'vgui-1' })
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.code).toBe('session_unavailable')
    expect(r.evidence).toMatchObject({ kind: 'virtual-gui' })
    expect(registry.list()).toHaveLength(0)
  })

  it('counts every ready GUI Blender window, split by kind', () => {
    const { registry } = setup([
      { instanceId: 'gui-1', kind: 'user-gui', ready: true },
      { instanceId: 'gui-2', kind: 'user-gui', ready: false },
      { instanceId: 'vgui-1', kind: 'virtual-gui', ready: true },
    ])
    expect(registry.guiConnectionCounts()).toEqual({ total: 2, userGui: 1, virtualGui: 1 })
    expect(registry.guiConnections()).toBe(2)
  })

  it('lists every session visible to one caller instead of only the first', () => {
    const { registry } = setup([
      { instanceId: 'inst-1', ready: true },
      { instanceId: 'inst-2', ready: true },
    ])
    expect(registry.attach(main(), { instanceId: 'inst-1' }).ok).toBe(true)
    expect(registry.attach(main(), { instanceId: 'inst-2' }).ok).toBe(true)
    expect(registry.listFor(main()).map((s) => s.id)).toEqual([
      'user-gui:inst-1',
      'user-gui:inst-2',
    ])
  })

  it('refuses an unnamed attach when two instances are ready', () => {
    const { registry } = setup([
      { instanceId: 'inst-1', ready: true },
      { instanceId: 'inst-2', ready: true },
    ])
    const r = registry.attach(main())
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.code).toBe('session_ambiguity')
    expect(r.stage).toBe('session')
  })

  it('reports session_in_use to a second DSH session without exposing the scene', () => {
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    const r = registry.attach(otherSession())
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.code).toBe('session_in_use')
    expect(r.stage).toBe('session')
    expect((r.evidence.owner as Record<string, unknown>).dshSessionId).toBe('dsh-1')
  })

  it('lets the owner release and the next DSH session claim', () => {
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    expect(registry.release(main()).ok).toBe(true)
    const r = registry.attach(otherSession())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect((r.session.owner as Record<string, unknown>).dshSessionId).toBe('dsh-2')
  })

  it('allows a second agent of the same DSH session to attach', () => {
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    const r = registry.attach(sub())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect((r.session.agents as unknown[]).length).toBe(2)
  })

  it('requires a scope grant for subagent writes and authorizes non-overlapping grants', () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    const denied = registry.authorizeWrite(sub(), sessionId, ['collection:COL_A'])
    expect(denied.ok).toBe(false)
    if (denied.ok) return
    expect(denied.code).toBe('scope_required')
    const granted = registry.grant(main(), sessionId, ['collection:COL_A'], sub())
    expect(granted.ok).toBe(true)
    const allowed = registry.authorizeWrite(sub(), sessionId, ['collection:COL_A'])
    expect(allowed.ok).toBe(true)
    if (!allowed.ok) return
    expect(allowed.mode).toBe('grant')
  })

  it('rejects an overlapping grant with scope_conflict', () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub({ agentId: 'agent-sub-2' })).ok).toBe(true)
    expect(registry.grant(main(), sessionId, ['collection:COL_A'], sub()).ok).toBe(true)
    const r = registry.grant(
      main(),
      sessionId,
      ['collection:COL_A'],
      sub({ agentId: 'agent-sub-2' }),
    )
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.code).toBe('scope_conflict')
    expect(r.stage).toBe('session')
  })

  it('lets the main agent write without an explicit grant but conflicts with a live grant', () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    expect(registry.grant(main(), sessionId, ['collection:COL_A'], sub()).ok).toBe(true)
    const blocked = registry.authorizeWrite(main(), sessionId, ['collection:COL_A'])
    expect(blocked.ok).toBe(false)
    if (blocked.ok) return
    expect(blocked.code).toBe('scope_conflict')
    const other = registry.authorizeWrite(main(), sessionId, ['collection:COL_B'])
    expect(other.ok).toBe(true)
  })

  it('rejects agent pause/resume and mirrors a human pause from Blender', () => {
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    const denied = registry.pause(main())
    expect(denied.ok).toBe(false)
    if (denied.ok) return
    expect(denied.code).toBe('human_action_required')
    expect(registry.applyHumanPause('inst-1', true)).toBe(1)
    const write = registry.authorizeWrite(main(), undefined, ['exclusive'])
    expect(write.ok).toBe(false)
    if (write.ok) return
    expect(write.code).toBe('human_paused')
    expect(registry.status(main()).ok).toBe(true)
    expect(registry.resume(main()).ok).toBe(false)
    expect(registry.applyHumanPause('inst-1', false)).toBe(1)
    expect(registry.authorizeWrite(main(), undefined, ['exclusive']).ok).toBe(true)
  })

  it('never pauses every session when the pause event has no instance id', () => {
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    expect(registry.applyHumanPause(undefined, true)).toBe(0)
    const view = registry.status(main())
    expect(view.ok).toBe(true)
    if (!view.ok) return
    expect(view.session.paused).toBe(false)
  })

  it('inherits the Blender pause when a session attaches after the human paused', () => {
    const { registry } = setup()
    expect(registry.applyHumanPause('inst-1', true)).toBe(0)
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    expect(attached.session.paused).toBe(true)
    const write = registry.authorizeWrite(main(), undefined, ['exclusive'])
    expect(write.ok).toBe(false)
    if (write.ok) return
    expect(write.code).toBe('human_paused')
  })

  it('keeps blender_py exclusive and reports py_busy for a second agent', async () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    expect((await registry.beginPy(main(), sessionId)).ok).toBe(true)
    expect(registry.grant(main(), sessionId, ['exclusive'], sub()).ok).toBe(true)
    const r = await registry.beginPy(sub(), sessionId)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.code).toBe('py_busy')
    expect(registry.endPy(main(), sessionId).ok).toBe(true)
    expect((await registry.beginPy(sub(), sessionId)).ok).toBe(true)
  })

  it('rejects a second py from the same holder instead of overwriting the lock', async () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect((await registry.beginPy(main(), sessionId)).ok).toBe(true)
    const again = await registry.beginPy(main(), sessionId)
    expect(again.ok).toBe(false)
    if (again.ok) return
    expect(again.code).toBe('py_busy')
    // The first call still owns the lock, so ending it releases exactly once.
    expect(registry.endPy(main(), sessionId).ok).toBe(true)
    expect(registry.endPy(main(), sessionId).ok).toBe(true)
  })

  it('lets a bounded wait_ms wait for the active blender_py to finish', async () => {
    vi.useFakeTimers()
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    expect((await registry.beginPy(main(), sessionId)).ok).toBe(true)
    expect(registry.grant(main(), sessionId, ['exclusive'], sub()).ok).toBe(true)
    const waiting = registry.beginPy(sub(), sessionId, 1000)
    await vi.advanceTimersByTimeAsync(100)
    expect(registry.endPy(main(), sessionId).ok).toBe(true)
    await vi.advanceTimersByTimeAsync(250)
    const r = await waiting
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.sessionId).toBe(sessionId)
  })

  it('does not expire the active py grant or lock by the 30s TTL', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    expect(registry.grant(main(), sessionId, ['exclusive'], sub()).ok).toBe(true)
    expect((await registry.beginPy(sub(), sessionId)).ok).toBe(true)
    vi.advanceTimersByTime(31_000)
    const blocked = registry.authorizeWrite(main(), sessionId, ['exclusive'])
    expect(blocked.ok).toBe(false)
    if (blocked.ok) return
    expect(blocked.code).toBe('scope_conflict')
    const timeout = registry.timeoutPy(sub(), sessionId)
    expect(timeout.ok).toBe(false)
    if (timeout.ok) return
    expect(timeout.code).toBe('py_still_running')
    expect((await registry.beginPy(main(), sessionId)).ok).toBe(false)
    expect(registry.endPy(sub(), sessionId).ok).toBe(true)
    vi.advanceTimersByTime(31_000)
    expect(registry.authorizeWrite(main(), sessionId, ['exclusive']).ok).toBe(true)
  })

  it('detaches a subagent without releasing ownership and lets the owner release', () => {
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    expect(registry.attach(sub()).ok).toBe(true)
    expect(registry.detach(sub()).ok).toBe(true)
    expect(registry.attach(otherSession()).ok).toBe(false)
    const released = registry.detach(main())
    expect(released.ok).toBe(true)
    expect(registry.attach(otherSession()).ok).toBe(true)
  })

  it('drops a detached agent grants so they cannot block other agents', () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    expect(registry.grant(main(), sessionId, ['collection:Body'], sub()).ok).toBe(true)
    expect(registry.detach(sub(), sessionId).ok).toBe(true)
    // The same collection is writable again right away, not after the 30s TTL.
    const other = registry.authorizeWrite(otherSession(), sessionId, ['collection:Body'])
    expect(other.ok).toBe(false)
    const owner = registry.authorizeWrite(main(), sessionId, ['collection:Body'])
    expect(owner.ok).toBe(true)
  })

  it('reclaims stale ownership after the 120s TTL', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    vi.advanceTimersByTime(121_000)
    const r = registry.attach(otherSession())
    expect(r.ok).toBe(true)
  })

  it('keeps ownership when the heartbeat is refreshed before the TTL', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    vi.advanceTimersByTime(60_000)
    expect(registry.heartbeat(main()).ok).toBe(true)
    vi.advanceTimersByTime(90_000)
    expect(registry.attach(otherSession()).ok).toBe(false)
  })

  it('does not let a subagent heartbeat or extend the owner lease', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    expect(registry.attach(sub()).ok).toBe(true)
    const attached = registry.attach(main())
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    vi.advanceTimersByTime(60_000)
    const beat = registry.heartbeat(sub())
    expect(beat.ok).toBe(false)
    if (beat.ok) return
    expect(beat.code).toBe('session_in_use')
    // A status call from the subagent must not refresh the main owner's lease.
    expect(registry.status(sub(), sessionId).ok).toBe(true)
    vi.advanceTimersByTime(61_000)
    expect(registry.attach(otherSession()).ok).toBe(true)
  })

  it('does not refresh the owner lease when a subagent attaches', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    vi.advanceTimersByTime(60_000)
    expect(registry.attach(sub()).ok).toBe(true)
    vi.advanceTimersByTime(61_000)
    expect(registry.attach(otherSession()).ok).toBe(true)
  })

  it('lets an attached caller reclaim writes after the owner TTL', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    vi.advanceTimersByTime(121_000)
    const write = registry.authorizeWrite(main(), sessionId, ['exclusive'])
    expect(write.ok).toBe(true)
  })

  it('records presence and view poses without touching the scene', () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    const view = registry.updateView(sub(), sessionId, {
      viewpoint: 'custom',
      from: [1, 2, 3],
      lookAt: [0, 0, 0],
      lens: 50,
    })
    expect(view.ok).toBe(true)
    const presence = registry.presence(main(), sessionId)
    expect(presence.ok).toBe(true)
    if (!presence.ok) return
    expect(presence.presence.length).toBe(2)
    const subView = presence.presence.find(
      (p) => (p.agent as Record<string, unknown>).agentId === 'agent-sub',
    )
    expect(subView).toBeTruthy()
    expect((subView?.view as Record<string, unknown>).viewpoint).toBe('custom')
    expect(String(subView?.id)).toMatch(/^[0-9a-f]{12}$/)
    expect(String(subView?.shortId)).toBe('A' + '2')
    expect(String(subView?.lastActivity)).toBe('see:custom')
    expect(Number(subView?.lastActivityAt)).toBeGreaterThan(0)
  })

  it('keeps presence updates after the owner TTL expires', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    vi.advanceTimersByTime(121_000)
    const view = registry.updateView(sub(), sessionId, {
      viewpoint: 'camera',
      from: [1, 2, 3],
      lookAt: [0, 0, 0],
    })
    expect(view.ok).toBe(true)
    if (!view.ok) return
    expect(view.presence.length).toBe(2)
    expect(registry.notice(sub(), sessionId, 'still here').ok).toBe(true)
  })

  it('refuses presence updates from an agent outside the session', () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    const r = registry.updateView(otherSession(), sessionId, {
      viewpoint: 'custom',
      from: [1, 2, 3],
      lookAt: [0, 0, 0],
    })
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.code).toBe('session_in_use')
  })

  it('publishes a monotonically increasing transient notice', () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    const first = registry.notice(main(), sessionId, 'SEE full · 整窗口截图，未记录观察位姿')
    expect(first.ok).toBe(true)
    const readNotice = () => {
      const status = registry.status(main(), sessionId)
      expect(status.ok).toBe(true)
      if (!status.ok) return null
      return status.session.notice as { seq: number; text: string } | null
    }
    expect(readNotice()).toMatchObject({
      seq: 1,
      text: 'SEE full · 整窗口截图，未记录观察位姿',
    })
    expect(registry.notice(main(), sessionId, 'second').ok).toBe(true)
    expect(readNotice()).toMatchObject({ seq: 2, text: 'second' })
  })

  it('releases ownership when the instance disconnects', () => {
    const { registry, state } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    state.instances = [{ instanceId: 'inst-1', ready: false }]
    const r = registry.status(main(), sessionId)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.session.status).toBe('starting')
    expect(r.session.owner).toBeNull()
    expect((r.session.agents as unknown[]).length).toBe(0)
    const doctor = registry.doctor(main())
    expect(doctor.ok).toBe(true)
    if (!doctor.ok) return
    expect(doctor.readiness.ready).toBe(false)
  })

  it('refuses to make a subagent the owner when it claims first', () => {
    const { registry } = setup()
    const r = registry.attach(sub())
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.code).toBe('owner_required')
    expect(registry.list()).toEqual([])
  })

  it('refreshes the owner heartbeat on a status call', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    expect(registry.attach(main()).ok).toBe(true)
    vi.advanceTimersByTime(60_000)
    expect(registry.status(main()).ok).toBe(true)
    vi.advanceTimersByTime(90_000)
    expect(registry.attach(otherSession()).ok).toBe(false)
  })

  it('refreshes a grant TTL on an authorized write', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    expect(registry.grant(main(), sessionId, ['collection:COL_A'], sub()).ok).toBe(true)
    vi.advanceTimersByTime(20_000)
    expect(registry.authorizeWrite(sub(), sessionId, ['collection:COL_A']).ok).toBe(true)
    vi.advanceTimersByTime(20_000)
    // 20s since the last write: a 30s grant that was not refreshed would have
    // expired at t=30s. The write above must have renewed it.
    expect(registry.authorizeWrite(sub(), sessionId, ['collection:COL_A']).ok).toBe(true)
  })

  it('keeps the Python lock when the owner heartbeat expires', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    expect((await registry.beginPy(main(), sessionId)).ok).toBe(true)
    vi.advanceTimersByTime(121_000)
    const status = registry.status(main(), sessionId)
    expect(status.ok).toBe(true)
    if (!status.ok) return
    expect(status.session.activePy).not.toBeNull()
    const timeout = registry.timeoutPy(main(), sessionId)
    expect(timeout.ok).toBe(false)
    if (timeout.ok) return
    expect(timeout.code).toBe('py_still_running')
  })

  it('expires a transient notice after its bounded lifetime', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-08T00:00:00Z'))
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.notice(main(), sessionId, 'full capture').ok).toBe(true)
    vi.advanceTimersByTime(16_000)
    const status = registry.status(main(), sessionId)
    expect(status.ok).toBe(true)
    if (!status.ok) return
    expect(status.session.notice).toBeNull()
  })

  it('does not let a subagent grant or revoke scopes', () => {
    const { registry } = setup()
    const attached = registry.attach(main())
    expect(attached.ok).toBe(true)
    if (!attached.ok) return
    const sessionId = String(attached.session.id)
    expect(registry.attach(sub()).ok).toBe(true)
    const denied = registry.grant(sub(), sessionId, ['exclusive'])
    expect(denied.ok).toBe(false)
    if (denied.ok) return
    expect(denied.code).toBe('scope_forbidden')
  })
})
