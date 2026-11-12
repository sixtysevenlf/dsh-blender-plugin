/**
 * 4.5/4.18: GUI instance isolation and multi-agent sessions on one Gateway.
 *
 * These use the real Gateway and the fake WebSocket add-on: they prove ownership,
 * scope grants, presence fan-out and instance routing without claiming a real
 * Blender scene was exercised (tests/e2e/addon_presence_draw.mjs does that).
 */
import { afterEach, describe, expect, it } from 'vitest'
import { startBackend } from '../../helpers/backend'
import { startFakeWsAddon } from '../../helpers/fake-ws-addon.mjs'

const closers: Array<() => Promise<void>> = []
afterEach(async () => {
  while (closers.length) {
    const close = closers.pop()!
    try {
      await close()
    } catch {
      /* best effort */
    }
  }
})

function headers(
  dshSessionId: string,
  agentId: string,
  role: 'main' | 'subagent',
  parent?: string,
) {
  return {
    'x-dsh-host-id': 'host-it',
    'x-dsh-session-id': dshSessionId,
    'x-dsh-agent-id': agentId,
    'x-dsh-agent-role': role,
    ...(parent ? { 'x-dsh-parent-agent-id': parent } : {}),
  }
}

async function setup(instanceIds: string[] = ['inst-1']) {
  const backend = await startBackend()
  closers.push(() => backend.stop())
  const addons: Array<{
    close: () => Promise<void> | void
    requests: Array<{ type: string; params: Record<string, unknown> }>
  }> = []
  for (const instanceId of instanceIds) {
    const addon = await startFakeWsAddon({ gatewayPort: backend.port, instanceId })
    await addon.ready
    addons.push(addon as never)
    closers.push(() => addon.close())
  }
  return { backend, addons }
}

async function waitFor(predicate: () => Promise<boolean>, timeoutMs = 3000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  throw new Error('condition not met before timeout')
}

const main = headers('dsh-1', 'agent-main', 'main')
const sub = headers('dsh-1', 'agent-sub', 'subagent', 'agent-main')
const sub2 = headers('dsh-1', 'agent-sub-2', 'subagent', 'agent-main')
const other = headers('dsh-2', 'agent-other', 'main')

describe('multi-agent session and GUI instance isolation', () => {
  it('4.5 keeps two instances isolated and rejects cross-session execution', async () => {
    const { backend, addons } = await setup(['inst-1', 'inst-2'])
    const first = await backend.post('/lease', { action: 'attach', instanceId: 'inst-1' }, main)
    expect(first.status).toBe(200)
    const second = await backend.post('/lease', { action: 'attach', instanceId: 'inst-2' }, other)
    expect(second.status).toBe(200)
    const firstSession = String(first.json.session.id)
    const secondSession = String(second.json.session.id)
    expect(firstSession).not.toBe(secondSession)

    const cross = await backend.post('/act', { code: 'print(1)', sessionId: secondSession }, main)
    expect(cross.status).toBe(409)
    expect(cross.json.code).toBe('session_in_use')

    await (addons[1] as never as { close: () => Promise<void> }).close()
    await waitFor(async () => {
      const status = await backend.get('/status')
      return status.json.readiness.addon.ready_instances === 1
    })
    const stillWorks = await backend.post(
      '/act',
      { code: 'print(1)', sessionId: firstSession },
      main,
    )
    expect(stillWorks.status).toBe(200)
    const offline = await backend.post(
      '/act',
      { code: 'print(1)', sessionId: secondSession },
      other,
    )
    expect(offline.status === 200 ? offline.json.ok === false : true).toBe(true)
    const otherWho = await backend.get('/who', other)
    const otherSessions = otherWho.json.sessions as Array<{ id: string }>
    expect(otherSessions.some((session) => session.id === firstSession)).toBe(false)
  })

  it('4.18 lets two agents share one user-gui session without sharing scopes', async () => {
    const { backend, addons } = await setup()
    const attached = await backend.post('/lease', { action: 'attach' }, main)
    expect(attached.status).toBe(200)
    const sessionId = String(attached.json.session.id)
    expect((await backend.post('/lease', { action: 'attach' }, sub)).status).toBe(200)
    expect((await backend.post('/lease', { action: 'attach' }, sub2)).status).toBe(200)

    const [mainView, subView] = await Promise.all([
      backend.post(
        '/lease',
        {
          action: 'update_view',
          sessionId,
          view: { viewpoint: 'viewport', from: [6, -6, 4], lookAt: [0, 0, 0] },
        },
        main,
      ),
      backend.post(
        '/lease',
        {
          action: 'update_view',
          sessionId,
          view: { viewpoint: 'viewport', from: [-5, 5, 3], lookAt: [0, 0, 0] },
        },
        sub,
      ),
    ])
    expect(mainView.status).toBe(200)
    expect(subView.status).toBe(200)
    const who = await backend.get('/who', main)
    const agents = who.json.sessions[0].agents as Array<{ displayName: string }>
    expect(agents.length).toBe(3)
    expect(new Set(agents.map((agent) => agent.displayName)).size).toBe(3)

    const granted = await backend.post(
      '/lease',
      { action: 'grant', sessionId, targetAgentId: 'agent-sub', scopes: ['collection:COL_A'] },
      main,
    )
    expect(granted.status).toBe(200)
    const [mainWrite, subWrite] = await Promise.all([
      backend.post('/act', { code: 'print(1)', write_scope: 'object:Cube', sessionId }, main),
      backend.post('/act', { code: 'print(2)', write_scope: 'collection:COL_A', sessionId }, sub),
    ])
    expect(mainWrite.status).toBe(200)
    expect(subWrite.status).toBe(200)

    const conflict = await backend.post(
      '/lease',
      { action: 'grant', sessionId, targetAgentId: 'agent-sub-2', scopes: ['collection:COL_A'] },
      main,
    )
    expect(conflict.status).toBe(409)
    expect(conflict.json.code).toBe('scope_conflict')

    const denied = await backend.post('/lease', { action: 'attach' }, other)
    expect(denied.status).toBe(409)
    expect(denied.json.code).toBe('session_in_use')
    expect((await backend.post('/release', {}, main)).status).toBe(200)
    const claimed = await backend.post('/lease', { action: 'attach' }, other)
    expect(claimed.status).toBe(200)
    expect(claimed.json.session.owner.dshSessionId).toBe('dsh-2')

    await waitFor(async () =>
      addons[0]!.requests.some(
        (request) =>
          request.type === 'get_viewport_screenshot' && typeof request.params.presence === 'string',
      ),
    )
    const presencePushes = addons[0]!.requests.filter(
      (request) =>
        request.type === 'get_viewport_screenshot' && typeof request.params.presence === 'string',
    )
    const presencePayloads = presencePushes.map((request) =>
      JSON.parse(String(request.params.presence)),
    )
    expect(presencePayloads.length).toBeGreaterThan(0)
    expect(
      presencePayloads.some(
        (payload) => Array.isArray(payload.agents) && payload.agents.length >= 3,
      ),
    ).toBe(true)
    expect(presencePayloads.every((payload) => typeof payload.paused === 'boolean')).toBe(true)
  })
  it('keeps the py lock when a Gateway timeout leaves Blender executing', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({
      gatewayPort: backend.port,
      instanceId: 'inst-hang',
      behavior: (type: string) =>
        type === 'execute_code'
          ? new Promise(() => {})
          : { status: 'success', result: { pong: true, type } },
    })
    closers.push(() => addon.close())
    await addon.ready
    const attached = await backend.post('/lease', { action: 'attach' }, main)
    expect(attached.status).toBe(200)
    const sessionId = String(attached.json.session.id)

    // The addon never answers: the Gateway gives up at timeoutMs, but the script
    // keeps running in Blender, so the lock must stay and the receipt must say so.
    const timedOut = await backend.post(
      '/act',
      { code: 'import time; time.sleep(5)', py: true, sessionId, timeoutMs: 400 },
      main,
    )
    expect(timedOut.status).toBe(409)
    expect(timedOut.json.code).toBe('py_still_running')
    expect(timedOut.json.evidence.holder).toBeTruthy()

    const second = await backend.post(
      '/act',
      { code: 'pass', py: true, sessionId, timeoutMs: 400, waitMs: 0 },
      main,
    )
    expect(second.status).toBe(409)
    expect(second.json.code).toBe('py_busy')
  }, 60_000)
  it('serves a dry-run plan without any session, and gates the real execution', async () => {
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const dry = await backend.post('/plan', { op: 'gate_deliver', args: { dry_run: true } }, main)
    expect(dry.status).toBe(200)
    const gated = await backend.post('/plan', { op: 'gate_deliver', args: {} }, main)
    expect(gated.status).toBe(409)
    expect(gated.json.code).toBe('session_unavailable')
  }, 60_000)
})
