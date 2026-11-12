/**
 * Regression: /plan must not demand a GUI session for host-local vision ops.
 *
 * The real Gateway path (caller headers present) used to 409 with
 * session_unavailable / stage=session for `{op:'vision_status'}` because the
 * /plan read-only list only covered PLAN_READ_ONLY_OPS. This test drives the
 * real createPlanRoutes with the real isReadOnly/leaseGate and a registry that
 * has no attached session, which is exactly the reported failure.
 */
import { Readable } from 'node:stream'
import type { IncomingMessage } from 'node:http'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPlanRoutes } from '../../../runtime/http/routes/plan.js'
import { callerFromRequest, runWithCaller } from '../../../runtime/http/caller.js'
import { isReadOnly, leaseGate } from '../../../runtime/http/lease.js'
import { readBody } from '../../../runtime/http/response.js'
import { SessionRegistry } from '../../../runtime/core/sessions/registry.js'
import { setSessionRegistry } from '../../../runtime/http/session-gate.js'
import { VISION_OPS } from '../../../runtime/vision/dispatch.js'

const CALLER_HEADERS = {
  'x-dsh-host-id': 'host-a',
  'x-dsh-session-id': 'dsh-1',
  'x-dsh-agent-id': 'agent-main',
  'x-dsh-agent-role': 'main',
  'content-type': 'application/json',
}

function request(body: unknown): IncomingMessage {
  const req = Readable.from([Buffer.from(JSON.stringify(body))]) as unknown as IncomingMessage
  ;(req as IncomingMessage & { headers: Record<string, string> }).headers = { ...CALLER_HEADERS }
  return req
}

function setup() {
  // A registry with no GUI session attached: the exact reported precondition.
  const registry = new SessionRegistry(() => [])
  setSessionRegistry(registry)
  return { registry }
}

function route() {
  return createPlanRoutes({
    engine: { plan: async (op: string) => ({ op, host_local: true }) } as never,
    isReadOnly,
    leaseGate,
    readBody,
    json: (_res: unknown, code: number, obj: unknown) => {
      response = { code, obj }
    },
    pushRtt: () => {},
    stats: {},
  } as never)
}

let response: { code: number; obj: unknown } | null = null

describe('/plan host-local vision ops', () => {
  beforeEach(() => {
    response = null
    setup()
  })

  it('serves vision_status without a session or a lease', async () => {
    const req = request({ op: 'vision_status', args: {}, write_scope: 'exclusive' })
    await runWithCaller(callerFromRequest(req), () =>
      route().plan(req, {} as never, new URL('http://127.0.0.1/plan'), '/plan'),
    )
    expect(response?.code).toBe(200)
    expect(response?.obj).toMatchObject({ ok: true, op: 'vision_status' })
    expect(response?.obj).not.toMatchObject({ stage: 'session' })
    expect(response?.obj).not.toHaveProperty('code', 'session_unavailable')
  })

  it('serves every host-local vision op without a session', async () => {
    for (const op of VISION_OPS) {
      response = null
      const req = request({ op, args: {}, write_scope: 'exclusive' })
      await runWithCaller(callerFromRequest(req), () =>
        route().plan(req, {} as never, new URL('http://127.0.0.1/plan'), '/plan'),
      )
      expect(response?.code, op).toBe(200)
      expect(response?.obj, op).not.toMatchObject({ stage: 'session' })
    }
  })

  it('still gates a scene-writing op behind the session', async () => {
    const req = request({ op: 'object_add', args: {}, write_scope: 'exclusive' })
    await runWithCaller(callerFromRequest(req), () =>
      route().plan(req, {} as never, new URL('http://127.0.0.1/plan'), '/plan'),
    )
    expect(response?.code).toBe(409)
    expect(response?.obj).toMatchObject({
      stage: 'session',
      root_cause: 'no GUI session is attached for this caller',
    })
  })
})
