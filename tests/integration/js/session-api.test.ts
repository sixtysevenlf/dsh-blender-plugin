/**
 * 4.1-4.3: the Gateway session API owns user-gui sessions, ownership, scope
 * grants, pause/resume, and the write gate used by every mutation route.
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

async function setup(
  instanceIds: string[] = ['inst-1'],
  env?: Record<string, string>,
) {
  const backend = await startBackend({ env })
  closers.push(() => backend.stop())
  const addons: Array<{
    close: () => Promise<void> | void
    sendEvent: (name: string, payload?: Record<string, unknown>) => void
    requests: Array<{ type: string; params: Record<string, unknown> }>
  }> = []
  for (const instanceId of instanceIds) {
    const addon = await startFakeWsAddon({ gatewayPort: backend.port, instanceId })
    await addon.ready
    addons.push(addon)
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
const other = headers('dsh-2', 'agent-other', 'main')

describe('Gateway session API', () => {
  it('attaches a ready user-gui session and exposes it through /who', async () => {
    const { backend } = await setup()
    const attach = await backend.post('/lease', { action: 'attach' }, main)
    expect(attach.status).toBe(200)
    expect(attach.json.ok).toBe(true)
    expect(attach.json.session.id).toBe('user-gui:inst-1')
    const who = await backend.get('/who', main)
    expect(who.status).toBe(200)
    expect((who.json.sessions as unknown[]).length).toBe(1)
  })

  it('issues a one-time consent token through the internal session action', async () => {
    const { backend } = await setup()
    const consent = await backend.post('/lease', { action: 'consent' }, main)
    expect(consent.status).toBe(200)
    expect(typeof consent.json.consentToken).toBe('string')
    expect(String(consent.json.consentToken).length).toBeGreaterThan(20)
    expect(Number(consent.json.expiresAt)).toBeGreaterThan(Date.now())
  })

  it('refuses an unnamed attach when two instances are ready', async () => {
    const { backend } = await setup(['inst-1', 'inst-2'])
    const attach = await backend.post('/lease', { action: 'attach' }, main)
    expect(attach.status).toBe(409)
    expect(attach.json.code).toBe('session_ambiguity')
  })

  it('rejects a second DSH session with session_in_use, then allows it after release', async () => {
    const { backend } = await setup()
    expect((await backend.post('/lease', { action: 'attach' }, main)).status).toBe(200)
    const denied = await backend.post('/lease', { action: 'attach' }, other)
    expect(denied.status).toBe(409)
    expect(denied.json.code).toBe('session_in_use')
    const released = await backend.post('/release', {}, main)
    expect(released.status).toBe(200)
    expect(released.json.released).toBe(true)
    const allowed = await backend.post('/lease', { action: 'attach' }, other)
    expect(allowed.status).toBe(200)
    expect(allowed.json.session.owner.dshSessionId).toBe('dsh-2')
  })

  it('lets two agents of one DSH session attach and separates grant scopes', async () => {
    const { backend } = await setup()
    const attached = await backend.post('/lease', { action: 'attach' }, main)
    const sessionId = String(attached.json.session.id)
    expect((await backend.post('/lease', { action: 'attach' }, sub)).status).toBe(200)

    const denied = await backend.post('/act', { code: 'print(1)' }, sub)
    expect(denied.status).toBe(409)
    expect(denied.json.code).toBe('scope_required')

    const granted = await backend.post(
      '/lease',
      { action: 'grant', sessionId, targetAgentId: 'agent-sub', scopes: ['collection:COL_A'] },
      main,
    )
    expect(granted.status).toBe(200)
    expect(granted.json.grant.holder.agentId).toBe('agent-sub')

    const scoped = await backend.post(
      '/act',
      { code: 'print(1)', write_scope: 'collection:COL_A', sessionId },
      sub,
    )
    expect(scoped.status).toBe(200)

    const outOfScope = await backend.post(
      '/act',
      { code: 'print(1)', write_scope: 'collection:COL_B', sessionId },
      sub,
    )
    expect(outOfScope.status).toBe(409)
    expect(outOfScope.json.code).toBe('scope_required')

    const overlapping = await backend.post(
      '/lease',
      { action: 'grant', sessionId, targetAgentId: 'agent-sub', scopes: ['collection:COL_A'] },
      main,
    )
    // Re-granting the same holder replaces its grant rather than conflicting.
    expect(overlapping.status).toBe(200)
  })

  it('rejects agent pause/resume and respects a human pause event from Blender', async () => {
    const { backend, addons } = await setup()
    const attached = await backend.post('/lease', { action: 'attach' }, main)
    const sessionId = String(attached.json.session.id)
    const deniedPause = await backend.post('/lease', { action: 'pause', sessionId }, main)
    expect(deniedPause.status).toBe(409)
    expect(deniedPause.json.code).toBe('human_action_required')
    const deniedResume = await backend.post('/lease', { action: 'resume', sessionId }, main)
    expect(deniedResume.status).toBe(409)
    expect(deniedResume.json.code).toBe('human_action_required')
    addons[0]!.sendEvent('pause', { paused: true })
    await waitFor(async () => {
      const who = await backend.get('/who', main)
      return who.json.sessions?.[0]?.paused === true
    })
    const denied = await backend.post('/act', { code: 'print(1)', sessionId }, main)
    expect(denied.status).toBe(409)
    expect(denied.json.code).toBe('human_paused')
    addons[0]!.sendEvent('pause', { paused: false })
    await waitFor(async () => {
      const who = await backend.get('/who', main)
      return who.json.sessions?.[0]?.paused === false
    })
    const allowed = await backend.post('/act', { code: 'print(1)', sessionId }, main)
    expect(allowed.status).toBe(200)
  })

  it('blocks a write after a human edit and clears it with mark_read', async () => {
    const { backend, addons } = await setup()
    const attached = await backend.post('/lease', { action: 'attach' }, main)
    const sessionId = String(attached.json.session.id)
    addons[0]!.sendEvent('human_edit', {
      instanceId: 'inst-1',
      objects: [{ kind: 'object', name: 'Cube', revision: 2 }],
      collections: [],
    })
    await waitFor(async () => {
      const who = await backend.get('/who', main)
      return who.json.sessions?.[0]?.humanModified?.objects?.[0]?.name === 'Cube'
    })
    const blocked = await backend.post(
      '/act',
      { code: 'print(1)', write_scope: 'object:Cube', sessionId },
      main,
    )
    expect(blocked.status).toBe(409)
    expect(blocked.json.code).toBe('human_modified')
    const marked = await backend.post(
      '/lease',
      { action: 'mark_read', sessionId, scopes: ['object:Cube'] },
      main,
    )
    expect(marked.status).toBe(200)
    expect(marked.json.cleared).toBe(1)
    const allowed = await backend.post(
      '/act',
      { code: 'print(1)', write_scope: 'object:Cube', sessionId },
      main,
    )
    expect(allowed.status).toBe(200)
  })

  it('records presence views without changing the scene', async () => {
    const { backend } = await setup()
    await backend.post('/lease', { action: 'attach' }, main)
    expect((await backend.post('/lease', { action: 'attach' }, sub)).status).toBe(200)
    const presence = await backend.get('/who', main)
    expect(presence.json.sessions[0].agents.length).toBe(2)
  })

  it('publishes a presence snapshot to the attached add-on', async () => {
    const { backend, addons } = await setup()
    const attached = await backend.post('/lease', { action: 'attach' }, main)
    const sessionId = String(attached.json.session.id)
    const view = await backend.post(
      '/lease',
      {
        action: 'update_view',
        sessionId,
        view: { viewpoint: 'viewport', from: [4, -4, 3], lookAt: [0, 0, 0] },
      },
      main,
    )
    expect(view.status).toBe(200)
    expect(view.json.presence[0].view.from).toEqual([4, -4, 3])
    const selection = await backend.post(
      '/lease',
      { action: 'update_selection', sessionId, selected: ['object:Cube'], activity: 'modeling' },
      main,
    )
    expect(selection.status).toBe(200)
    expect(selection.json.presence[0].selected).toContain('object:Cube')
    await waitFor(async () =>
      addons[0]!.requests.some(
        (request) =>
          request.type === 'execute_code' &&
          String(request.params.code || '').includes('set_presence'),
      ),
    )
    const push = addons[0]!.requests.find((request) => request.type === 'execute_code')!
    expect(String(push.params.code)).toContain('presence_draw')
  })

  it('4.8 holds the Python lock for the holder and reports py_busy to other agents', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const backend = await startBackend()
    closers.push(() => backend.stop())
    const addon = await startFakeWsAddon({
      gatewayPort: backend.port,
      instanceId: 'inst-1',
      behavior: async (type: string, params: Record<string, unknown>) => {
        if (type === 'execute_code' && String(params.code || '').includes('print(1)')) {
          await gate
          return { status: 'success', result: { ok: true, ms: 1, stdout: 'done', executed: true } }
        }
        return { status: 'success', result: { type } }
      },
    })
    await addon.ready
    closers.push(() => addon.close())
    expect((await backend.post('/lease', { action: 'attach' }, main)).status).toBe(200)

    const first = backend.post('/act', { code: 'print(1)', py: true }, main)
    await waitFor(async () =>
      (addon.requests as Array<{ type: string }>).some(
        (request) => request.type === 'execute_code',
      ),
    )
    const blocked = await backend.post('/act', { code: 'print(2)', py: true }, sub)
    expect(blocked.status).toBe(409)
    expect(blocked.json.code).toBe('py_busy')
    expect(blocked.json.evidence.holder.agentId).toBe('agent-main')

    release()
    const done = await first
    expect(done.status).toBe(200)
    expect(done.json.ok).toBe(true)

    const after = await backend.post('/act', { code: 'print(3)', py: true }, sub)
    expect(after.status).toBe(409)
    expect(after.json.code).toBe('scope_required')
  })

  it('4.8 gates txn restore behind the exclusive scope and a human pause', async () => {
    const { backend, addons } = await setup()
    const attached = await backend.post('/lease', { action: 'attach' }, main)
    const sessionId = String(attached.json.session.id)
    expect((await backend.post('/txn', { op: 'list' }, sub)).status).toBe(200)

    const denied = await backend.post('/txn', { op: 'restore', args: { label: 's' } }, sub)
    expect(denied.status).toBe(409)
    expect(denied.json.code).toBe('scope_required')

    const granted = await backend.post(
      '/lease',
      { action: 'grant', sessionId, targetAgentId: 'agent-sub', scopes: ['exclusive'] },
      main,
    )
    expect(granted.status).toBe(200)
    const allowed = await backend.post('/txn', { op: 'restore', args: { label: 's' } }, sub)
    expect(allowed.status).toBe(200)

    addons[0]!.sendEvent('pause', { paused: true })
    await waitFor(async () => {
      const who = await backend.get('/who', main)
      const rows = who.json.sessions as Array<{ paused?: boolean }>
      return rows.some((row) => row.paused === true)
    })
    const paused = await backend.post('/txn', { op: 'restore', args: { label: 's' } }, main)
    expect(paused.status).toBe(409)
    expect(paused.json.code).toBe('human_paused')
  })

  it('5.15 keeps virtual-gui off when the feature gate is closed, without spawning anything', async () => {
    const { backend } = await setup(undefined, { DSH_BLENDER_VIRTUAL_GUI: 'off' })
    const before = await backend.get('/status')
    const consent = await backend.post('/lease', { action: 'consent' }, main)
    expect(consent.status).toBe(200)
    const token = String(consent.json.consentToken)
    const started = await backend.post(
      '/lease',
      { action: 'start', kind: 'virtual-gui', consentToken: token },
      main,
    )
    expect(started.status).toBeGreaterThanOrEqual(400)
    expect(started.json.code).toBe('feature_disabled')
    expect(started.json.stage).toBe('config')
    expect(String(started.json.fix || '')).toMatch(/virtual[-_]?gui/i)

    const after = await backend.get('/status')
    expect(after.json.readiness.addon.ready_instances).toBe(
      before.json.readiness.addon.ready_instances,
    )
    const sessions = await backend.post('/lease', { action: 'list' }, main)
    expect(sessions.json.sessions).toEqual([])
  })
})
