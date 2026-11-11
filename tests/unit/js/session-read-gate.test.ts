import { describe, expect, it } from 'vitest'
import { SessionRegistry } from '../../../runtime/core/sessions/registry.js'
import type { CallerIdentity } from '../../../runtime/core/sessions/types.js'
import { runWithCaller } from '../../../runtime/http/caller.js'
import { LEASE, leaseGate } from '../../../runtime/http/lease.js'
import { setSessionRegistry } from '../../../runtime/http/session-gate.js'
import { readOnlyCommand } from '../../../runtime/http/routes/commands.js'

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

function setup() {
  LEASE.holder = null
  LEASE.expiresAt = null
  const registry = new SessionRegistry(() => [{ instanceId: 'inst-1', ready: true }])
  const attached = registry.attach(main())
  if (!attached.ok) throw new Error('main attach failed: ' + attached.code)
  const session = registry.getSession('user-gui:inst-1')
  if (!session) throw new Error('missing session')
  const joined = registry.attach(sub(), { instanceId: 'inst-1' })
  if (!joined.ok) throw new Error('sub attach failed: ' + joined.code)
  setSessionRegistry(registry)
  return { registry, session, sessionId: session.id }
}

describe('3.1 session read gate for read-only /cmd', () => {
  it('lets the owner and an attached subagent run a read command without a grant', () => {
    const { sessionId } = setup()
    expect(runWithCaller(main(), () => leaseGate({ sessionId }, { readOnly: true }))).toBeNull()
    expect(runWithCaller(sub(), () => leaseGate({ sessionId }, { readOnly: true }))).toBeNull()
  })

  it('keeps reads available while a human paused writes', () => {
    const { session, sessionId } = setup()
    session.paused = true
    expect(runWithCaller(sub(), () => leaseGate({ sessionId }, { readOnly: true }))).toBeNull()
    const write = runWithCaller(sub(), () => leaseGate({ sessionId })) as any
    expect(write).toMatchObject({ ok: false, error: 'human_paused' })
  })

  it('does not treat a read as a write that needs an exclusive scope', () => {
    const { sessionId } = setup()
    const write = runWithCaller(sub(), () => leaseGate({ sessionId })) as any
    expect(write).toMatchObject({ ok: false, error: 'scope_required' })
  })

  it('still refuses a foreign DSH session and names the owner', () => {
    const { sessionId } = setup()
    const out = runWithCaller(otherSession(), () =>
      leaseGate({ sessionId }, { readOnly: true }),
    ) as any
    expect(out).toMatchObject({ ok: false, error: 'session_in_use' })
    expect(out.evidence.owner).toMatchObject({ dshSessionId: 'dsh-1' })
  })

  it('resolves a read gate by instanceId when no sessionId is sent', () => {
    setup()
    const out = runWithCaller(otherSession(), () =>
      leaseGate({ instanceId: 'inst-1' }, { readOnly: true }),
    ) as any
    expect(out).toMatchObject({ ok: false, error: 'session_in_use' })
  })

  it('requires a session for a read command from an unattached caller', () => {
    setup()
    const out = runWithCaller(otherSession({ dshSessionId: 'dsh-3' }), () =>
      leaseGate({}, { readOnly: true }),
    ) as any
    expect(out).toMatchObject({ ok: false, error: 'session_unavailable' })
  })

  it('classifies catalog read commands and wire ping as read-only', () => {
    expect(readOnlyCommand('get_scene_info')).toBe(true)
    expect(readOnlyCommand('ping')).toBe(true)
    expect(readOnlyCommand('get_viewport_screenshot')).toBe(false)
    expect(readOnlyCommand('execute_code')).toBe(false)
    expect(readOnlyCommand('set_texture')).toBe(false)
  })
})
