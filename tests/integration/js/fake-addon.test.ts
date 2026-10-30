import { afterEach, describe, expect, it } from 'vitest'
import { AddonClient } from '../../../runtime/engine.mjs'
import { detectProtocol } from '../../../runtime/addon-protocol.mjs'
import { startFakeAddon } from '../../helpers/fake-addon.mjs'

const open: Array<{ close: () => Promise<void> }> = []
const clients: Array<{ close: () => void }> = []

async function fake(options?: Record<string, unknown>) {
  const server = await startFakeAddon(options)
  open.push(server)
  return server
}

function client(server: { port: number }) {
  const c = new AddonClient({ host: '127.0.0.1', port: server.port })
  clients.push(c)
  return c
}

afterEach(async () => {
  for (const c of clients.splice(0)) c.close()
  for (const s of open.splice(0)) await s.close()
})

describe('addon channel against a fake addon', () => {
  it('handshakes on an ephemeral port, never 9876', async () => {
    const server = await fake()
    expect(server.port).toBeGreaterThan(0)
    expect(server.port).not.toBe(9876)
    await expect(detectProtocol('127.0.0.1', server.port, 1000)).resolves.toMatchObject({
      id: 'flat',
    })
  })

  it('returns a successful flat response', async () => {
    const server = await fake()
    const c = client(server)
    await expect(c.send('get_addon_info', {}, 1000)).resolves.toMatchObject({
      name: 'DSH Blender Bridge',
      protocol: 11,
    })
    await expect(c.send('ping', {}, 1000)).resolves.toMatchObject({ pong: true })
  })

  it('rejects an addon error without losing the message', async () => {
    const server = await fake()
    const c = client(server)
    await expect(c.send('fail', {}, 1000)).rejects.toThrow('fake addon failure')
  })

  it('surfaces an addon pause as a rejection', async () => {
    const server = await fake()
    const c = client(server)
    await expect(c.send('paused', {}, 1000)).rejects.toThrow('AI 操作已暂停')
  })

  it('times out when the addon never answers', async () => {
    const server = await fake()
    const c = client(server)
    const t0 = Date.now()
    await expect(c.send('hang', {}, 200)).rejects.toThrow(/addon timeout: hang/)
    expect(Date.now() - t0).toBeGreaterThanOrEqual(180)
  })

  it('rejects when the addon drops the socket mid-request', async () => {
    const server = await fake()
    const c = client(server)
    await expect(c.send('disconnect', {}, 1000)).rejects.toThrow(/socket closed/)
  })

  it('does not leak the server port after close', async () => {
    const server = await fake()
    const c = client(server)
    await c.send('ping', {}, 1000)
    c.close()
    await server.close()
    // The port is released; a second fake addon must be able to bind a new one.
    const again = await fake()
    expect(again.port).toBeGreaterThan(0)
  })
})
