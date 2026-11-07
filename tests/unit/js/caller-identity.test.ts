import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, describe, expect, it } from 'vitest'
import { EXEC_CTX, callerFromExec, callerHeaders } from '../../../src/host/exec-context.js'
import { backendPost } from '../../../src/host/http.js'

const servers: http.Server[] = []
afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) =>
        new Promise<void>((resolve) => {
          server.close(() => resolve())
        }),
    ),
  )
})

describe('caller identity from the DSH exec object', () => {
  it('derives host, session, agent, parent and role from a subagent', () => {
    const caller = callerFromExec({
      callId: 'call-1',
      agent: {
        id: 'agent-sub',
        name: 'researcher',
        session: { id: 'dsh-session-1' },
        parent: { id: 'agent-main' },
      },
    })
    expect(caller.agentId).toBe('agent-sub')
    expect(caller.dshSessionId).toBe('dsh-session-1')
    expect(caller.parentAgentId).toBe('agent-main')
    expect(caller.role).toBe('subagent')
    expect(caller.callId).toBe('call-1')
    expect(caller.displayName).toBe('researcher')
    expect(caller.hostId).toContain('host-')
  })

  it('treats a root agent without a parent as main', () => {
    const caller = callerFromExec({ agent: { id: 'agent-main', session: { id: 's' } } })
    expect(caller.role).toBe('main')
    expect(caller.parentAgentId).toBeUndefined()
  })

  it('emits only the headers the Gateway needs', () => {
    const caller = {
      hostId: 'host-a',
      dshSessionId: 'dsh-1',
      agentId: 'agent-main',
      role: 'main' as const,
      parentAgentId: 'parent-1',
      callId: 'call-9',
      turnId: 'turn-2',
    }
    const headers = EXEC_CTX.run({ caller }, () => callerHeaders())
    expect(headers['x-dsh-host-id']).toBe('host-a')
    expect(headers['x-dsh-session-id']).toBe('dsh-1')
    expect(headers['x-dsh-agent-id']).toBe('agent-main')
    expect(headers['x-dsh-agent-role']).toBe('main')
    expect(headers['x-dsh-parent-agent-id']).toBe('parent-1')
    expect(headers['x-dsh-call-id']).toBe('call-9')
    expect(headers['x-dsh-turn-id']).toBe('turn-2')
  })

  it('sends caller headers on every backend POST', async () => {
    let seen: http.IncomingHttpHeaders = {}
    const server = http.createServer((req, res) => {
      seen = req.headers
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ ok: true }))
    })
    servers.push(server)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
    const port = (server.address() as AddressInfo).port
    const caller = {
      hostId: 'host-a',
      dshSessionId: 'dsh-1',
      agentId: 'agent-main',
      role: 'main' as const,
    }
    const out = await EXEC_CTX.run({ caller }, () => backendPost(port, '/status', {}))
    expect(out.ok).toBe(true)
    expect(seen['x-dsh-host-id']).toBe('host-a')
    expect(seen['x-dsh-session-id']).toBe('dsh-1')
    expect(seen['x-dsh-agent-id']).toBe('agent-main')
    expect(seen['x-dsh-agent-role']).toBe('main')
  })
})
