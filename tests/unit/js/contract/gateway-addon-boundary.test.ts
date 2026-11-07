import { readFile } from 'node:fs/promises'
import { describe, expect, it } from 'vitest'

const root = new URL('../../../../', import.meta.url)
const handshake = {
  instanceId: 'inst-1',
  kind: 'user-gui',
  version: '2.0.0',
  protocol: 12,
  project: 'demo.blend',
  capabilities: ['see', 'op'],
}

describe('3.1 addon Gateway boundary', () => {
  it('keeps the addon free of inbound listeners', async () => {
    const source = await readFile(new URL('addon/dsh_blender_bridge/server.py', root), 'utf8')
    expect(source).not.toMatch(/\bsocket\.socket\s*\(/)
    expect(source).not.toMatch(/\.bind\s*\(/)
    expect(source).not.toMatch(/\.listen\s*\(/)
    expect(source).not.toMatch(/\.accept\s*\(/)
    expect(source).not.toContain('SO_REUSEADDR')
  })

  it('rejects protocol 11 and accepts protocol 12', async () => {
    const mod: any = await import('../../../../runtime/transport/addon-ws.js')
    expect(typeof mod.validateHandshake).toBe('function')
    expect(mod.validateHandshake({ ...handshake, protocol: 11 })).toMatchObject({
      ok: false,
      stage: 'handshake',
    })
    expect(mod.validateHandshake(handshake)).toMatchObject({ ok: true })
  })
})
