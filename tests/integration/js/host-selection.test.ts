
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { startBackend, type Backend } from '../../helpers/backend'

// v2.3 host integration, split out of the old host_routes_selftest.mjs:
// removed viewer routes, CORS policy, and the selection push/poll/consume flow.
// The addon posts, the client polls. All three routes are zero-Blender.

let backend: Backend

beforeAll(async () => { backend = await startBackend() })
afterAll(async () => { await backend.stop() })

const ORIGIN = 'http://127.0.0.1:3080'
const marker = {
  source: 'blender-addon',
  sent_at: 1710000000000,
  selection: {
    kind: 'objects',
    count: 2,
    names: ['Cube', 'Lamp'],
    active: 'Cube',
    mode: 'OBJECT',
    collections: [{ name: 'COL_Asset', count: 2 }],
    objects: [
      { name: 'Cube', type: 'MESH', dimensions_mm: [100, 100, 100] },
      { name: 'Lamp', type: 'LIGHT' },
    ],
  },
  scene: { name: 'Scene', file: '/tmp/marker.blend' },
  activity: { state: 'idle', human_active: true, agent_active: false },
}

describe('host selection flow', () => {
  it('serves CORS preflight and echoes only allowed origins', async () => {
    const preflight = await fetch(backend.base + '/host/selection', {
      method: 'OPTIONS', headers: { origin: ORIGIN, 'access-control-request-method': 'POST' },
    })
    expect(preflight.status).toBe(204)
    expect(await preflight.text()).toBe('')
    expect(preflight.headers.get('access-control-allow-origin')).toBe(ORIGIN)
    expect(String(preflight.headers.get('access-control-allow-methods')).split(',').map((m) => m.trim())).toEqual(['GET', 'POST', 'OPTIONS'])

    const app = await fetch(backend.base + '/host/state', { headers: { origin: 'dsh-app://app' } })
    expect(app.headers.get('access-control-allow-origin')).toBe('dsh-app://app')

    const evil = await fetch(backend.base + '/host/state', { headers: { origin: 'http://evil.example' } })
    expect(evil.headers.get('access-control-allow-origin')).toBeNull()
  })

  it('404s the removed viewer and pane routes', async () => {
    for (const route of ['/host/view/latest', '/host/view.png', '/host/view/capture', '/host/view/pick',
      '/host/pick/request', '/host/pick/result', '/host/ready']) {
      const response = await backend.get(route)
      expect(response.status, route).toBe(404)
      expect(response.json.ok).toBe(false)
    }
  })

  it('stores a pushed selection and reports it as unconsumed', async () => {
    const pushed = await backend.post('/host/selection', marker)
    expect(pushed.status).toBe(200)
    expect(pushed.json.ok).toBe(true)
    expect(pushed.json.seq).toBe(1)

    const latest = await backend.get('/host/selection/latest')
    expect(latest.status).toBe(200)
    expect(latest.json.unconsumed).toBe(true)
    expect(latest.json.selection.names).toEqual(['Cube', 'Lamp'])
    expect(latest.json.selection.collections).toEqual([{ name: 'COL_Asset', count: 2 }])
    expect(latest.json.selection.active).toBe('Cube')
    expect(latest.json.scene.name).toBe('Scene')
    expect(latest.json.activity.state).toBe('idle')
  })

  it('consumes the selection and hides it afterwards', async () => {
    const consumed = await backend.post('/host/selection/consume', { seq: 1, ok: true, how: 'actions' })
    expect(consumed.status).toBe(200)
    expect(consumed.json.ok).toBe(true)
    expect(consumed.json.consumed_seq).toBe(1)

    const latest = await backend.get('/host/selection/latest')
    expect(latest.json.unconsumed).toBe(false)
    expect(latest.json.selection).toBeNull()
    expect(latest.json.last_consume).toMatchObject({ seq: 1, ok: true, how: 'actions' })
  })

  it('rejects invalid consume seqs and non-object payloads with 400', async () => {
    const zero = await backend.post('/host/selection/consume', { seq: 0 })
    expect(zero.status).toBe(400)
    expect(zero.json.error).toBe('invalid_seq')

    const invalid = await fetch(backend.base + '/host/selection', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify([1, 2]),
    })
    expect(invalid.status).toBe(400)
    const invalidBody = await invalid.json() as { error: string }
    expect(invalidBody.error).toBe('invalid_payload')

    const malformed = await fetch(backend.base + '/host/selection', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: '{not json',
    })
    expect(malformed.status).toBe(400)
  })
})
