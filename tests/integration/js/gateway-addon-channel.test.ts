import { afterEach, describe, expect, it } from 'vitest'
import net from 'node:net'
import crypto from 'node:crypto'
import { createGatewayApp } from '../../../runtime/gateway/app.js'
import type { GatewayApp } from '../../../runtime/gateway/app.js'
import { encodeText, parseFrames } from '../../../runtime/transport/ws.js'

const apps: GatewayApp[] = []
const sockets: net.Socket[] = []

afterEach(async () => {
  for (const s of sockets.splice(0)) {
    try {
      s.destroy()
    } catch (e) {
      /* ignore */
    }
  }
  for (const app of apps.splice(0)) app.close()
  await new Promise((resolve) => setTimeout(resolve, 20))
})

function listen(app: GatewayApp): Promise<number> {
  return new Promise((resolve, reject) => {
    app.server.once('error', reject)
    app.server.listen(0, '127.0.0.1', () => {
      const addr = app.server.address()
      if (!addr || typeof addr === 'string') return reject(new Error('no address'))
      resolve(addr.port)
    })
  })
}

interface Conn {
  socket: net.Socket
  messages: Record<string, unknown>[]
  waiters: Array<(m: Record<string, unknown>) => void>
  buffer: Buffer
}

async function dial(port: number): Promise<Conn> {
  const socket = net.connect({ host: '127.0.0.1', port })
  sockets.push(socket)
  const key = crypto.randomBytes(16).toString('base64')
  await new Promise<void>((resolve, reject) => {
    socket.once('connect', resolve)
    socket.once('error', reject)
  })
  socket.write(
    'GET /addon HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
      'Sec-WebSocket-Key: ' +
      key +
      '\r\nSec-WebSocket-Version: 13\r\n\r\n',
  )
  const conn: Conn = { socket, messages: [], waiters: [], buffer: Buffer.alloc(0) }
  let headerDone = false
  socket.on('data', (chunk: Buffer) => {
    conn.buffer = Buffer.concat([conn.buffer, chunk])
    if (!headerDone) {
      const idx = conn.buffer.indexOf('\r\n\r\n')
      if (idx < 0) return
      headerDone = true
      conn.buffer = conn.buffer.subarray(idx + 4)
    }
    const parsed = parseFrames(conn.buffer, { requireMask: false })
    conn.buffer = parsed.rest
    for (const frame of parsed.frames) {
      if (frame.opcode !== 1) continue
      const msg = JSON.parse(frame.payload.toString('utf8')) as Record<string, unknown>
      const w = conn.waiters.shift()
      if (w) w(msg)
      else conn.messages.push(msg)
    }
  })
  return conn
}

function nextMessage(conn: Conn, timeoutMs = 2000): Promise<Record<string, unknown>> {
  if (conn.messages.length) return Promise.resolve(conn.messages.shift() as Record<string, unknown>)
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('message timeout')), timeoutMs)
    conn.waiters.push((m) => {
      clearTimeout(t)
      resolve(m)
    })
  })
}

function send(conn: Conn, msg: Record<string, unknown>): void {
  conn.socket.write(encodeText(JSON.stringify(msg), { mask: true }))
}

function handshake(instanceId: string, protocol = 12): Record<string, unknown> {
  return {
    type: 'handshake',
    instanceId,
    kind: 'user-gui',
    version: '2.0.0',
    protocol,
    project: 'demo.blend',
    capabilities: ['see', 'op'],
  }
}

/** Raw upgrade request so the test controls the Origin header. */
function upgradeStatus(port: number, origin: string): Promise<string> {
  const CRLF = String.fromCharCode(13, 10)
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port })
    sockets.push(socket)
    let buf = ''
    socket.once('connect', () => {
      const lines = [
        'GET /addon HTTP/1.1',
        'Host: 127.0.0.1',
        'Upgrade: websocket',
        'Connection: Upgrade',
        'Sec-WebSocket-Key: ' + crypto.randomBytes(16).toString('base64'),
        'Sec-WebSocket-Version: 13',
      ]
      if (origin) lines.push('Origin: ' + origin)
      socket.write(lines.join(CRLF) + CRLF + CRLF)
    })
    socket.on('data', (chunk: Buffer) => {
      buf += chunk.toString('utf8')
      if (buf.includes(CRLF)) resolve(buf.split(CRLF)[0])
    })
    socket.once('error', reject)
  })
}

describe('Gateway addon WebSocket channel', () => {
  it('rejects a browser page origin and keeps the native (no Origin) channel', async () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    apps.push(app)
    const port = await listen(app)
    expect(await upgradeStatus(port, 'http://evil.example')).toContain('403')
    expect(await upgradeStatus(port, 'http://127.0.0.1:3000')).toContain('101')
    expect(await upgradeStatus(port, '')).toContain('101')
  })

  it('serves /health and upgrades /addon on the same port', async () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    apps.push(app)
    const port = await listen(app)
    const res = await fetch('http://127.0.0.1:' + port + '/health')
    expect(res.ok).toBe(true)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.ok).toBe(true)
    const conn = await dial(port)
    send(conn, handshake('inst-a'))
    expect(await nextMessage(conn)).toMatchObject({
      type: 'handshake_ok',
      protocol: 12,
      instanceId: 'inst-a',
    })
    expect(app.addonGateway.list()).toHaveLength(1)
  })

  it('rejects protocol 11 with a structured handshake error', async () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    apps.push(app)
    const port = await listen(app)
    const conn = await dial(port)
    send(conn, handshake('old', 11))
    expect(await nextMessage(conn)).toMatchObject({
      type: 'handshake_error',
      stage: 'handshake',
      code: 'unsupported_protocol',
      upgrade_required: true,
    })
    expect(app.addonGateway.list()).toHaveLength(0)
  })

  it('routes requests by instanceId and rejects ambiguity', async () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    apps.push(app)
    const port = await listen(app)
    const a = await dial(port)
    const b = await dial(port)
    send(a, handshake('inst-a'))
    await nextMessage(a)
    send(b, handshake('inst-b'))
    await nextMessage(b)

    await expect(app.addonGateway.send('ping', {})).rejects.toMatchObject({
      code: 'ambiguous_instance',
    })

    const pending = app.addonGateway.send('ping', {}, 2000, { instanceId: 'inst-b' })
    const req = await nextMessage(b)
    expect(b.messages).toHaveLength(0)
    expect(req).toMatchObject({ type: 'ping' })
    expect(typeof req.requestId).toBe('string')
    send(b, { status: 'success', result: { pong: true }, requestId: req.requestId })
    await expect(pending).resolves.toEqual({ pong: true })
    expect(a.messages).toHaveLength(0)
  })

  it('keeps the last identity after a disconnect and lists registry fields', async () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    apps.push(app)
    const port = await listen(app)
    const conn = await dial(port)
    send(conn, handshake('inst-a'))
    await nextMessage(conn)

    const before = app.addonGateway.list()
    expect(before).toHaveLength(1)
    expect(before[0]).toMatchObject({
      instanceId: 'inst-a',
      kind: 'user-gui',
      version: '2.0.0',
      protocol: 12,
      project: 'demo.blend',
      capabilities: ['see', 'op'],
      ready: true,
    })
    expect(typeof before[0].connectedAt).toBe('number')
    expect(typeof before[0].lastSeen).toBe('number')
    expect(before[0].epoch).toBeGreaterThan(0)

    await new Promise<void>((resolve) => {
      conn.socket.once('close', () => resolve())
      conn.socket.end()
    })
    await new Promise((resolve) => setTimeout(resolve, 50))
    const after = app.addonGateway.list()
    expect(after).toHaveLength(1)
    expect(after[0].instanceId).toBe('inst-a')
    expect(after[0].ready).toBe(false)
    expect(after[0].lastSeen).toBeGreaterThanOrEqual(before[0].lastSeen)
  })

  it('rejects missing handshake fields and unknown kinds with the same structured stage', async () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    apps.push(app)
    const port = await listen(app)

    const missing = await dial(port)
    send(missing, { type: 'handshake', instanceId: 'x', kind: 'user-gui', version: '2.0.0' })
    expect(await nextMessage(missing)).toMatchObject({
      type: 'handshake_error',
      stage: 'handshake',
      code: 'missing_field',
    })

    const kind = await dial(port)
    send(kind, { ...handshake('y'), kind: 'headless' })
    expect(await nextMessage(kind)).toMatchObject({
      type: 'handshake_error',
      stage: 'handshake',
      code: 'unknown_kind',
      upgrade_required: true,
    })
    expect(app.addonGateway.list()).toHaveLength(0)
  })

  it('fails in-flight requests on a silent-instance reconnect and never replays them', async () => {
    // heartbeatMs also bounds how long a live connection may block a takeover, so
    // a short value makes the old connection look silent without waiting 15s.
    const app = createGatewayApp({ port: 0, heartbeatMs: 60 })
    apps.push(app)
    const port = await listen(app)
    const first = await dial(port)
    send(first, handshake('inst-a'))
    await nextMessage(first)
    await new Promise((resolve) => setTimeout(resolve, 120))

    const pending = app.addonGateway.send('ping', {}, 5000, { instanceId: 'inst-a' })
    const pendingAssert = expect(pending).rejects.toMatchObject({
      code: 'instance_disconnected',
      message: 'instance reconnected',
    })
    const second = await dial(port)
    send(second, handshake('inst-a'))
    await nextMessage(second)
    await pendingAssert
    await new Promise((resolve) => setTimeout(resolve, 120))
    expect(first.socket.destroyed).toBe(true)
    expect(app.addonGateway.list()[0].epoch).toBeGreaterThan(1)
  })

  it('refuses to replace a connection that is still answering, and keeps it routable', async () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60000 })
    apps.push(app)
    const port = await listen(app)
    const first = await dial(port)
    send(first, handshake('inst-a'))
    await nextMessage(first)
    const second = await dial(port)
    send(second, handshake('inst-a'))
    // The new socket gets an explicit refusal instead of seizing the instance.
    expect(await nextMessage(second)).toMatchObject({ type: 'error', code: 'instance_in_use' })
    expect(app.addonGateway.list()).toHaveLength(1)
    expect(app.addonGateway.list()[0].epoch).toBe(1)
    const pending = app.addonGateway.send('ping', {}, 2000, { instanceId: 'inst-a' })
    const req = await nextMessage(first)
    send(first, { status: 'success', result: { ok: 1 }, requestId: req.requestId })
    await expect(pending).resolves.toEqual({ ok: 1 })
  })

  it('replaces a silent connection for the same instanceId', async () => {
    const app = createGatewayApp({ port: 0, heartbeatMs: 60 })
    apps.push(app)
    const port = await listen(app)
    const first = await dial(port)
    send(first, handshake('inst-a'))
    await nextMessage(first)
    await new Promise((resolve) => setTimeout(resolve, 120))
    const second = await dial(port)
    send(second, handshake('inst-a'))
    await nextMessage(second)
    expect(app.addonGateway.list()).toHaveLength(1)
    expect(app.addonGateway.list()[0].epoch).toBeGreaterThan(1)
    // The new connection receives the next request; the old epoch is gone.
    const pending = app.addonGateway.send('ping', {}, 2000, { instanceId: 'inst-a' })
    const req = await nextMessage(second)
    send(second, { status: 'success', result: { ok: 1 }, requestId: req.requestId })
    await expect(pending).resolves.toEqual({ ok: 1 })
  })

  it('replaces a silent instanceId without emitting connection_closed', async () => {
    const events: Record<string, unknown>[] = []
    const app = createGatewayApp({
      port: 0,
      heartbeatMs: 60,
      onEvent: (event) => events.push(event),
    })
    apps.push(app)
    const port = await listen(app)
    const first = await dial(port)
    send(first, handshake('inst-a'))
    await nextMessage(first)
    await new Promise((resolve) => setTimeout(resolve, 120))

    const second = await dial(port)
    send(second, handshake('inst-a'))
    await nextMessage(second)
    await new Promise((resolve) => setTimeout(resolve, 80))

    expect(app.addonGateway.list()).toHaveLength(1)
    expect(app.addonGateway.list()[0]).toMatchObject({ instanceId: 'inst-a', ready: true })
    // A superseded connection must not look like a real disconnect: the session
    // registry maps connection_closed -> handleInstanceGone (virtual-gui stop).
    expect(events.filter((e) => e.type === 'connection_closed')).toHaveLength(0)
    expect(events.filter((e) => e.type === 'handshake_ok')).toHaveLength(2)

    await new Promise<void>((resolve) => {
      second.socket.once('close', () => resolve())
      second.socket.end()
    })
    await new Promise((resolve) => setTimeout(resolve, 80))
    expect(events.filter((e) => e.type === 'connection_closed')).toHaveLength(1)
  })

  it('rejects a plain HTTP request to /addon with 400', async () => {
    const app = createGatewayApp({ port: 0 })
    apps.push(app)
    const port = await listen(app)
    const raw = await new Promise<string>((resolve, reject) => {
      const socket = net.connect({ host: '127.0.0.1', port }, () => {
        socket.write(
          'GET /addon HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n',
        )
      })
      sockets.push(socket)
      let text = ''
      socket.on('data', (chunk: Buffer) => {
        text += chunk.toString('utf8')
      })
      socket.on('close', () => resolve(text))
      socket.on('error', reject)
      setTimeout(() => resolve(text), 1000)
    })
    expect(raw).toContain('HTTP/1.1 400 Bad Request')
  })
})
