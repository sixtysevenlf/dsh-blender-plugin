import { describe, expect, it } from 'vitest'
import {
  canManageJob,
  jobOwnerFromCaller,
  jobPermissionFailure,
} from '../../../runtime/core/sessions/job-owner.js'
import type { CallerIdentity } from '../../../runtime/core/sessions/types.js'

const caller = (over: Partial<CallerIdentity> = {}): CallerIdentity => ({
  hostId: 'host-a',
  dshSessionId: 'dsh-1',
  agentId: 'agent-main',
  role: 'main',
  ...over,
})

const owner = (over: Partial<CallerIdentity> = {}) => jobOwnerFromCaller(caller(over))

describe('3.1 job ownership', () => {
  it('records the submitting agent and parent', () => {
    const record = owner({
      agentId: 'agent-sub',
      parentAgentId: 'agent-main',
      role: 'subagent',
    })
    expect(record).toEqual({
      hostId: 'host-a',
      dshSessionId: 'dsh-1',
      agentId: 'agent-sub',
      parentAgentId: 'agent-main',
    })
  })

  it('allows the owner, its parent and the main agent of the same DSH session', () => {
    const record = owner({ agentId: 'agent-sub', parentAgentId: 'agent-main', role: 'subagent' })
    expect(canManageJob(caller({ agentId: 'agent-sub' }), record)).toEqual({
      allowed: true,
      reason: 'owner',
    })
    expect(canManageJob(caller({ agentId: 'agent-main' }), record)).toEqual({
      allowed: true,
      reason: 'parent',
    })
    expect(canManageJob(caller({ agentId: 'agent-lead', role: 'main' }), record)).toEqual({
      allowed: true,
      reason: 'main',
    })
  })

  it('rejects another agent of the same session and every other DSH session', () => {
    const record = owner({ agentId: 'agent-sub', parentAgentId: 'agent-main', role: 'subagent' })
    const peer = canManageJob(caller({ agentId: 'agent-peer', role: 'subagent' }), record)
    expect(peer).toEqual({ allowed: false, reason: 'not_owner' })
    expect(jobPermissionFailure(peer, 'cancel')).toContain('owning agent')
    const foreign = canManageJob(
      caller({ dshSessionId: 'dsh-2', agentId: 'agent-main', role: 'main' }),
      record,
    )
    expect(foreign).toEqual({ allowed: false, reason: 'other_session' })
    expect(jobPermissionFailure(foreign, 'collect')).toContain('DSH session')
    expect(jobPermissionFailure(peer, 'status')).toContain('status')
  })
})
