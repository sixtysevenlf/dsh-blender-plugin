import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { startBackend, type Backend } from '../../helpers/backend'
import { startFakeWsAddon } from '../../helpers/fake-ws-addon.mjs'

// GET /host/scene-items feeds the client-side @ mention list. The data comes from
// a live addon; the failure path must be an explicit 502 with a readable error,
// never an empty 200 (the client would silently show no mentions). Positive
// object/material/collection payloads are covered by the Blender E2E suite.

let backend: Backend
let addon: Awaited<ReturnType<typeof startFakeWsAddon>>

beforeAll(async () => {
  backend = await startBackend()
  addon = await startFakeWsAddon({
    gatewayPort: backend.port,
    behavior: (type: string) => {
      if (type === 'get_addon_info')
        return {
          status: 'success',
          result: { name: 'fake', version: '2.0.0', protocol: 12, paused: false },
        }
      if (type === 'execute_code') return { status: 'error', message: 'scene items unavailable' }
      return { status: 'success', result: { type } }
    },
  })
  await addon.ready
})
afterAll(async () => {
  await addon.close()
  await backend.stop()
})

describe('host scene items failure path', () => {
  it('turns an addon refusal into an explicit 502', async () => {
    const response = await backend.get('/host/scene-items?q=Cu&limit=5')
    expect(response.status).toBe(502)
    expect(response.json.ok).toBe(false)
    expect(String(response.json.error)).toMatch(
      /scene items|scene-items|execute_code|unavailable|失败/,
    )
  })

  it('502s when no addon is reachable at all', async () => {
    const offline = await startBackend()
    try {
      const response = await offline.get('/host/scene-items?q=Cu&limit=5')
      expect(response.status).toBe(502)
      expect(response.json.ok).toBe(false)
      expect(typeof response.json.error).toBe('string')
    } finally {
      await offline.stop()
    }
  })
})
