// Job ownership rules shared by the queue and the six-tool surface.
//
// Queue jobs are created by an agent call, so cancellation and collection are
// restricted to that agent, its parent agent, and the main agent of the same
// DSH session. The rule is pure so section 6 can apply it to persisted jobs
// without importing queue state here.

import type { CallerIdentity } from './types.js'

export interface JobOwner {
  hostId: string
  dshSessionId: string
  agentId: string
  parentAgentId?: string
}

export type JobManageAction = 'collect' | 'cancel' | 'status'

export interface JobManageDecision {
  allowed: boolean
  reason: 'owner' | 'parent' | 'main' | 'other_session' | 'not_owner'
}

export function jobOwnerFromCaller(c: CallerIdentity): JobOwner {
  return {
    hostId: String(c.hostId || ''),
    dshSessionId: String(c.dshSessionId || ''),
    agentId: String(c.agentId || ''),
    ...(c.parentAgentId ? { parentAgentId: String(c.parentAgentId) } : {}),
  }
}

export function canManageJob(c: CallerIdentity, owner: JobOwner): JobManageDecision {
  if (owner.hostId !== c.hostId || owner.dshSessionId !== c.dshSessionId)
    return { allowed: false, reason: 'other_session' }
  if (owner.agentId === c.agentId) return { allowed: true, reason: 'owner' }
  if (owner.parentAgentId && owner.parentAgentId === c.agentId)
    return { allowed: true, reason: 'parent' }
  if (c.role === 'main') return { allowed: true, reason: 'main' }
  return { allowed: false, reason: 'not_owner' }
}

export function jobPermissionFailure(decision: JobManageDecision, action: JobManageAction): string {
  if (decision.allowed) return ''
  if (decision.reason === 'other_session')
    return action + ' is limited to the DSH session that submitted the job'
  return action + ' is limited to the owning agent, its parent agent, or the main agent'
}
