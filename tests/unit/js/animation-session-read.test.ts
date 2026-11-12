import { describe, expect, it, vi } from 'vitest'

vi.mock('node:child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:child_process')>()
  return { ...actual, spawnSync: vi.fn() }
})

import { PLAN_READ_ONLY_OPS, PLAN_SESSION_READ_OPS } from '../../../runtime/catalog/index.js'
import { createPlanDispatch, type PlanDispatchDeps } from '../../../runtime/core/plan-dispatch.js'
import { READ_ONLY_OPS } from '../../../src/tools/op/read-only.js'
import { SESSION_READ_OPS, isSessionRead, opGateMode } from '../../../src/tools/op/session-read.js'
import { planRequestBody } from '../../../src/tools/op/plan.js'
import { createPlanRoutes } from '../../../runtime/http/routes/plan.js'
import { SessionRegistry } from '../../../runtime/core/sessions/registry.js'
import type { CallerIdentity } from '../../../runtime/core/sessions/types.js'
import { runWithCaller } from '../../../runtime/http/caller.js'
import { LEASE, leaseGate } from '../../../runtime/http/lease.js'
import { setSessionRegistry } from '../../../runtime/http/session-gate.js'

const readOnly = new Set<string>(READ_ONLY_OPS)

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
  return { session, sessionId: session.id }
}

describe('animation session-read ops', () => {
  it('mirrors the catalog session-read list and keeps the ops out of READ_ONLY_OPS', () => {
    expect(SESSION_READ_OPS).toEqual(PLAN_SESSION_READ_OPS)
    expect(SESSION_READ_OPS).toEqual(['animation_report', 'animation_help'])
    expect(isSessionRead('animation_report')).toBe(true)
    expect(isSessionRead('animation_help')).toBe(true)
    expect(isSessionRead('animation_selftest')).toBe(false)
    for (const op of SESSION_READ_OPS) expect(READ_ONLY_OPS).not.toContain(op)
    for (const op of SESSION_READ_OPS) expect(PLAN_READ_ONLY_OPS).not.toContain(op)
  })

  it('requires a session for session-read ops even on dry_run', () => {
    const report = opGateMode('animation_report', true, readOnly)
    expect(report).toEqual({ readOnly: false, sessionRead: true, needsSession: true })
    const selftest = opGateMode('animation_selftest', true, readOnly)
    expect(selftest).toEqual({ readOnly: false, sessionRead: false, needsSession: false })
    const selftestLive = opGateMode('animation_selftest', false, readOnly)
    expect(selftestLive.needsSession).toBe(true)
  })

  it('keeps catalog reads session-free', () => {
    expect(opGateMode('catalog', false, readOnly)).toEqual({
      readOnly: true,
      sessionRead: false,
      needsSession: false,
    })
    expect(opGateMode('audit_scene', true, readOnly)).toEqual({
      readOnly: true,
      sessionRead: false,
      needsSession: false,
    })
  })

  it('omits write_scope for session-read ops only', () => {
    const report = planRequestBody('animation_report', {}, { objects: ['Cube'] }, {})
    expect(report).toEqual({ op: 'animation_report', args: { objects: ['Cube'] } })
    const selftest = planRequestBody('animation_selftest', {}, {}, {})
    expect(selftest.write_scope).toBe('exclusive')
    const declared = planRequestBody('animation_report', { write_scope: 'collection:rig' }, {}, {})
    expect(declared.write_scope).toBeUndefined()
    const custom = planRequestBody('audit_mesh', { write_scope: 'collection:mesh' }, {}, {})
    expect(custom.write_scope).toBe('collection:mesh')
  })

  it('passes the session-read gate without a write grant and refuses a foreign session', () => {
    const { sessionId } = setup()
    expect(runWithCaller(main(), () => leaseGate({ sessionId }, { readOnly: true }))).toBeNull()
    expect(runWithCaller(sub(), () => leaseGate({ sessionId }, { readOnly: true }))).toBeNull()
    const foreign = runWithCaller(otherSession(), () =>
      leaseGate({ sessionId, op: 'animation_report' }, { readOnly: true }),
    ) as any
    expect(foreign).toMatchObject({ ok: false, error: 'session_in_use' })
    expect(foreign.evidence.owner).toMatchObject({ dshSessionId: 'dsh-1' })
    const selftestDenied = runWithCaller(sub(), () =>
      leaseGate({ sessionId, op: 'animation_selftest', write_scope: 'exclusive' }),
    ) as any
    expect(selftestDenied).toMatchObject({ ok: false, error: 'scope_required' })
  })

  it('routes animation_report through the read gate and animation_selftest through the write gate', async () => {
    const calls: Array<{ op?: string; opts?: { readOnly?: boolean } }> = []
    let response: any = null
    const route = createPlanRoutes({
      engine: { plan: async (op: string) => ({ op }) } as any,
      json: (_res: unknown, code: number, obj: unknown) => {
        response = { code, obj }
      },
      readBody: async () => JSON.stringify({ op: 'animation_report', args: {} }),
      leaseGate: (payload: any, opts?: { readOnly?: boolean }) => {
        calls.push({ op: String(payload?.op || ''), opts })
        return null
      },
      isReadOnly: (path: string, op: unknown) =>
        path === '/plan' && PLAN_READ_ONLY_OPS.includes(String(op)),
      pushRtt: () => {},
      stats: {},
    } as any)
    const req = {} as any
    const res = {} as any
    const url = new URL('http://127.0.0.1/plan')

    await route.plan(req, res, url, '/plan')
    expect(calls).toEqual([{ op: 'animation_report', opts: { readOnly: true } }])

    calls.length = 0
    route.plan = createPlanRoutes({
      engine: { plan: async (op: string) => ({ op }) } as any,
      json: (_res: unknown, code: number, obj: unknown) => {
        response = { code, obj }
      },
      readBody: async () => JSON.stringify({ op: 'animation_selftest', args: {} }),
      leaseGate: (payload: any, opts?: { readOnly?: boolean }) => {
        calls.push({ op: String(payload?.op || ''), opts })
        return null
      },
      isReadOnly: (path: string, op: unknown) =>
        path === '/plan' && PLAN_READ_ONLY_OPS.includes(String(op)),
      pushRtt: () => {},
      stats: {},
    } as any).plan

    await route.plan(req, res, url, '/plan')
    expect(calls).toEqual([{ op: 'animation_selftest', opts: undefined }])

    calls.length = 0
    route.plan = createPlanRoutes({
      engine: { plan: async (op: string) => ({ op }) } as any,
      json: (_res: unknown, code: number, obj: unknown) => {
        response = { code, obj }
      },
      readBody: async () => JSON.stringify({ op: 'audit_mesh', args: {} }),
      leaseGate: (payload: any, opts?: { readOnly?: boolean }) => {
        calls.push({ op: String(payload?.op || ''), opts })
        return null
      },
      isReadOnly: (path: string, op: unknown) =>
        path === '/plan' && PLAN_READ_ONLY_OPS.includes(String(op)),
      pushRtt: () => {},
      stats: {},
    } as any).plan

    await route.plan(req, res, url, '/plan')
    expect(calls).toEqual([])
    expect(response.code).toBe(200)
  })

  it('keeps animation ops out of the host-local shortcuts', async () => {
    const planCallInner = vi.fn(async () => ({ ok: true }))
    const visionOp = vi.fn(async () => ({ ok: true }))
    const dispatch = createPlanDispatch({
      RENDER_FLAG_WSL: '',
      RENDER_STALE_MS: 0,
      addon: { send: vi.fn() } as unknown as PlanDispatchDeps['addon'],
      clearRenderFlag: () => true,
      ensurePerf: async () => undefined,
      ensureQc: async () => undefined,
      ensureRenderGuard: async () => undefined,
      ensureRunner: async () => undefined,
      ensureView: async () => undefined,
      loopBoard: async () => ({ best: null, board: [] }),
      loopExport: async () => ({ ok: true }),
      loopStart: async () => ({ ok: true }),
      loopStatus: async () => null,
      loopStop: async () => ({ ok: true }),
      planCallInner,
      visionOp,
      readRenderFlag: () => ({ present: false, stale: false, ageMs: null, info: null }),
    })
    expect(await dispatch.planCall('animation_report', { frame_start: 1 })).toEqual({ ok: true })
    expect(await dispatch.planCall('animation_selftest', {})).toEqual({ ok: true })
    expect(planCallInner).toHaveBeenNthCalledWith(1, 'animation_report', { frame_start: 1 })
    expect(planCallInner).toHaveBeenNthCalledWith(2, 'animation_selftest', {})
    expect(visionOp).not.toHaveBeenCalled()
  })
})
