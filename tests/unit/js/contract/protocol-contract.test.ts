import { afterAll, describe, expect, it } from 'vitest'
import { PROTOCOLS, detectProtocol, resolveProtocol } from '../../../../runtime/addon-protocol.mjs'
import { AddonClient } from '../../../../runtime/engine.mjs'
import { startFakeAddon } from '../../../helpers/fake-addon.mjs'

describe('flat addon protocol', () => {
  it('encodes requests without framing and decodes success envelopes', () => {
    const plan = PROTOCOLS.flat.plan('get_scene_info', { depth: 1 })
    expect(plan.wire).toEqual({ type: 'get_scene_info', params: { depth: 1 } })
    expect(PROTOCOLS.flat.encode(plan.wire)).toBe('{"type":"get_scene_info","params":{"depth":1}}')
    expect(PROTOCOLS.flat.decode('{"status":"success","result":{"objects":[]}}')).toEqual({ ok: true, data: { objects: [] } })
    expect(PROTOCOLS.flat.decode('{"status":"success"}')).toEqual({ ok: true, data: {} })
  })

  it('maps error envelopes to {ok:false,message} without throwing', () => {
    expect(PROTOCOLS.flat.decode('{"status":"error"}')).toEqual({ ok: false, message: 'addon error' })
    expect(PROTOCOLS.flat.decode('{"status":"error","message":"boom"}')).toEqual({ ok: false, message: 'boom' })
  })

  it('treats only the flat protocol as supported', async () => {
    expect(Object.keys(PROTOCOLS)).toEqual(['flat'])
    const resolved = await resolveProtocol()
    expect(resolved.proto).toBe(PROTOCOLS.flat)
  })
})

describe('flat protocol transport behavior', () => {
  let addon: Awaited<ReturnType<typeof startFakeAddon>>
  let client: any

  afterAll(async () => {
    client?.close()
    await addon?.close()
  })

  it('detects the addon by one ping and reports the flat id', async () => {
    addon = await startFakeAddon()
    const detected = await detectProtocol('127.0.0.1', addon.port, 1500)
    expect(detected?.id).toBe('flat')
    expect(detected?.ms).toBeGreaterThanOrEqual(0)
  })

  it('accumulates partial frames until the JSON envelope closes', async () => {
    client = new AddonClient({ host: '127.0.0.1', port: addon.port })
    let settled: unknown
    let rejected: unknown
    client.pending = {
      proto: PROTOCOLS.flat,
      resolve: (v: unknown) => { settled = v },
      reject: (e: unknown) => { rejected = e },
      timer: setTimeout(() => {}, 1000),
      post: undefined,
    }
    client._onData(Buffer.from('{"status":"suc'))
    expect(settled).toBeUndefined()
    expect(rejected).toBeUndefined()
    client._onData(Buffer.from('cess","result":{"split":true}}'))
    expect(rejected).toBeUndefined()
    expect(settled).toEqual({ split: true })
    clearTimeout(client.pending?.timer)
    client.pending = null
  })

  it('times out a command that never responds', async () => {
    await expect(client.send('hang', {}, 50)).rejects.toThrow('addon timeout: hang')
  })

  it('rejects in-flight work when the socket closes', async () => {
    await expect(client.send('disconnect', {}, 500)).rejects.toThrow('socket closed')
  })

  it('surfaces the addon pause error unchanged', async () => {
    await expect(client.send('paused', {}, 1000)).rejects.toThrow('AI 操作已暂停')
  })

  it('lets the client decide on protocol mismatch instead of hard-gating', async () => {
    const old = await startFakeAddon({
      behavior: (type: string) => type === 'get_addon_info'
        ? { status: 'success', result: { protocol: 10, name: 'old' } }
        : { status: 'success', result: { pong: true } },
    })
    const c = new AddonClient({ host: '127.0.0.1', port: old.port })
    try {
      const info = await c.send('get_addon_info', {}, 1000)
      expect(info.protocol).toBe(10)
      const pong = await c.send('ping', {}, 1000)
      expect(pong).toEqual({ pong: true })
    } finally {
      c.close()
      await old.close()
    }
  })
})
