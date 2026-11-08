// Addon control channel: handshake contract + the Gateway-side WebSocket endpoint.
// The addon dials the Gateway; the Gateway never dials the addon. Every request
// carries a requestId and every response must echo it.
import { randomUUID } from 'node:crypto'
import type { IncomingMessage, Server } from 'node:http'
import type { Socket } from 'node:net'
import {
  acceptKey,
  closePayload,
  encodeClose,
  encodeFrame,
  encodeText,
  OP_BINARY,
  OP_CLOSE,
  OP_CONTINUATION,
  OP_PING,
  OP_PONG,
  OP_TEXT,
  parseFrames,
} from './ws.js'
import type { WsFrame } from './ws.js'
import {
  ADDON_PROTOCOL_VERSION,
  ADDON_WS_PATH,
  HEARTBEAT_DEAD_MS,
  HEARTBEAT_MS,
  MAX_MESSAGE_BYTES,
} from './gateway-schema.generated.js'
import { validateHandshake } from './addon-handshake.js'
import type { AddonHandshake, AddonHandshakeError, AddonInstanceView } from './addon-handshake.js'

export { validateHandshake, gatewayInstances, gatewayHandshakeError } from './addon-handshake.js'
export type {
  AddonHandshake,
  AddonHandshakeError,
  AddonInstanceView,
  HandshakeResult,
} from './addon-handshake.js'

export {
  ADDON_KINDS,
  ADDON_PROTOCOL_VERSION,
  ADDON_VERSION,
  ADDON_WS_PATH,
  HEARTBEAT_DEAD_MS,
  HEARTBEAT_MS,
  GATEWAY_DEFAULT_PORT,
} from './gateway-schema.generated.js'

export class GatewayChannelError extends Error {
  stage: string
  code: string
  instanceIds?: string[]
  constructor(stage: string, code: string, message: string, instanceIds?: string[]) {
    super(message)
    this.name = 'GatewayChannelError'
    this.stage = stage
    this.code = code
    if (instanceIds) this.instanceIds = instanceIds
  }
}

interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: NodeJS.Timeout
  instanceId: string
}

interface Connection {
  socket: Socket
  epoch: number
  instance: AddonHandshake | null
  buffer: Buffer
  fragments: Buffer[]
  fragmentOpcode: number | null
  fragmentBytes: number
  lastSeen: number
  closed: boolean
}

export interface AddonGatewayOptions {
  gatewayId?: string
  heartbeatMs?: number
  deadAfterMs?: number
  maxMessageBytes?: number
  onEvent?: (event: Record<string, unknown>) => void
}

/**
 * Gateway-side addon endpoint. Attach to the single HTTP server with attach();
 * the /addon upgrade path shares that server's port (no second listener).
 */
export class AddonGateway {
  readonly gatewayId: string
  readonly instances = new Map<string, AddonInstanceView>()
  /** Most recent rejected handshake, so /doctor can report a version mismatch. */
  lastHandshakeError: AddonHandshakeError | null = null
  private readonly sockets = new Set<Connection>()
  private readonly pending = new Map<string, PendingRequest>()
  private readonly heartbeatMs: number
  private readonly deadAfterMs: number
  private readonly maxMessageBytes: number
  private readonly onEvent?: (event: Record<string, unknown>) => void
  private heartbeat: NodeJS.Timeout | null = null
  private nextEpoch = 1
  private closed = false

  constructor(opts: AddonGatewayOptions = {}) {
    this.gatewayId = opts.gatewayId || 'gw-' + randomUUID()
    this.heartbeatMs = opts.heartbeatMs || HEARTBEAT_MS
    this.deadAfterMs = opts.deadAfterMs || HEARTBEAT_DEAD_MS
    this.maxMessageBytes = opts.maxMessageBytes || MAX_MESSAGE_BYTES
    this.onEvent = opts.onEvent
  }

  /** Attach the /addon WebSocket upgrade path to the Gateway HTTP server. */
  attach(server: Server): void {
    server.on('upgrade', (req, socket, head) => this.handleUpgrade(req, socket as Socket, head))
    this.heartbeat = setInterval(() => this.sweepHeartbeat(), this.heartbeatMs)
    this.heartbeat.unref?.()
  }

  list(): AddonInstanceView[] {
    return Array.from(this.instances.values()).map((x) => ({
      ...x,
      capabilities: x.capabilities.slice(),
    }))
  }

  /** Wait until the first addon is ready; used by engine.start without spawning anything. */
  async ensure(): Promise<void> {
    return
  }

  async send(
    type: string,
    params: Record<string, unknown> = {},
    timeoutMs = 120000,
    opts: { instanceId?: string } = {},
  ): Promise<unknown> {
    const { instance, connection } = this.resolveInstance(opts.instanceId)
    const requestId = 'req-' + randomUUID()
    const body = JSON.stringify({ type, params: params || {}, requestId })
    return new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(
        () => {
          this.pending.delete(requestId)
          reject(
            new GatewayChannelError(
              'addon',
              'addon_timeout',
              'addon timeout: no reply to ' +
                type +
                ' within ' +
                String(timeoutMs) +
                'ms (instance=' +
                instance.instanceId +
                ')',
            ),
          )
        },
        Math.max(1, timeoutMs),
      )
      this.pending.set(requestId, { resolve, reject, timer, instanceId: instance.instanceId })
      try {
        connection.socket.write(encodeText(body))
      } catch (e) {
        clearTimeout(timer)
        this.pending.delete(requestId)
        reject(
          new GatewayChannelError(
            'transport',
            'addon_write_failed',
            '向 addon 写请求失败：' + (e instanceof Error ? e.message : String(e)),
          ),
        )
      }
    })
  }

  close(): void {
    this.closed = true
    if (this.heartbeat) clearInterval(this.heartbeat)
    this.heartbeat = null
    for (const conn of this.sockets) this.destroyConnection(conn, 1001, 'gateway stopped')
    this.sockets.clear()
    this.instances.clear()
    this.rejectAll('Gateway 已停止')
  }

  private resolveInstance(instanceId?: string): {
    instance: AddonInstanceView
    connection: Connection
  } {
    if (instanceId) {
      const view = this.instances.get(instanceId)
      const conn = view ? this.connectionFor(instanceId) : null
      if (!view || !conn || !view.ready) {
        throw new GatewayChannelError(
          'transport',
          'instance_not_found',
          '没有可路由的 Blender 实例：' + instanceId,
          this.list().map((x) => x.instanceId),
        )
      }
      return { instance: view, connection: conn }
    }
    const ready = this.list().filter((x) => x.ready)
    if (ready.length === 0) {
      throw new GatewayChannelError(
        'transport',
        'no_addon',
        '没有 Blender addon 连接到 Gateway（应为 ' +
          ADDON_PROTOCOL_VERSION +
          ' 协议）；先启动 Blender 并确认扩展已连接。',
      )
    }
    if (ready.length > 1) {
      throw new GatewayChannelError(
        'transport',
        'ambiguous_instance',
        '有多个 Blender 实例连接，调用必须显式给 instanceId：' +
          ready.map((x) => x.instanceId).join(' / '),
        ready.map((x) => x.instanceId),
      )
    }
    const only = ready[0]
    return { instance: only, connection: this.connectionFor(only.instanceId) as Connection }
  }

  private connectionFor(instanceId: string): Connection | null {
    for (const conn of this.sockets) {
      if (conn.instance && conn.instance.instanceId === instanceId && !conn.closed) return conn
    }
    return null
  }

  private handleUpgrade(req: IncomingMessage, socket: Socket, head: Buffer): void {
    if (this.closed) {
      socket.destroy()
      return
    }
    const path = String(req.url || '').split('?')[0]
    if (path !== ADDON_WS_PATH) {
      socket.write('HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
      socket.destroy()
      return
    }
    const key = String(req.headers['sec-websocket-key'] || '')
    const upgrade = String(req.headers.upgrade || '').toLowerCase()
    if (
      upgrade !== 'websocket' ||
      !key ||
      String(req.headers['sec-websocket-version'] || '') !== '13'
    ) {
      socket.write('HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
      socket.destroy()
      return
    }
    const accept = acceptKey(key)
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\n' +
        'Upgrade: websocket\r\n' +
        'Connection: Upgrade\r\n' +
        'Sec-WebSocket-Accept: ' +
        accept +
        '\r\n\r\n',
    )
    socket.setNoDelay(true)
    const conn: Connection = {
      socket,
      epoch: this.nextEpoch++,
      instance: null,
      buffer: head && head.length ? Buffer.from(head) : Buffer.alloc(0),
      fragments: [],
      fragmentOpcode: null,
      fragmentBytes: 0,
      lastSeen: Date.now(),
      closed: false,
    }
    this.sockets.add(conn)
    socket.on('data', (chunk: Buffer) => this.onData(conn, chunk))
    socket.on('error', () => this.dropConnection(conn))
    socket.on('end', () => this.dropConnection(conn))
    socket.on('close', () => this.dropConnection(conn))
    this.onEvent?.({ type: 'connection_open', epoch: conn.epoch })
    if (conn.buffer.length) this.onData(conn, Buffer.alloc(0))
  }

  private onData(conn: Connection, chunk: Buffer): void {
    if (conn.closed) return
    conn.buffer = Buffer.concat([conn.buffer, chunk])
    const parsed = parseFrames(conn.buffer, {
      requireMask: true,
      maxMessageBytes: this.maxMessageBytes,
    })
    conn.buffer = parsed.rest
    if (parsed.error) {
      this.destroyConnection(conn, 1002, parsed.error)
      return
    }
    for (const frame of parsed.frames) this.onFrame(conn, frame)
  }

  private onFrame(conn: Connection, frame: WsFrame): void {
    conn.lastSeen = Date.now()
    if (frame.opcode === OP_PING) {
      conn.socket.write(encodeFrame(OP_PONG, frame.payload))
      return
    }
    if (frame.opcode === OP_PONG) return
    if (frame.opcode === OP_CLOSE) {
      const p = closePayload(frame.payload)
      this.destroyConnection(conn, p.code === null ? 1000 : p.code, p.reason)
      return
    }
    if (frame.opcode === OP_BINARY || frame.opcode === OP_TEXT) {
      if (frame.fin) {
        this.onMessage(conn, frame.payload)
        return
      }
      conn.fragmentOpcode = frame.opcode
      conn.fragments = [frame.payload]
      conn.fragmentBytes = frame.payload.length
      return
    }
    if (frame.opcode === OP_CONTINUATION) {
      if (conn.fragmentOpcode === null) {
        this.destroyConnection(conn, 1002, 'unexpected continuation frame')
        return
      }
      conn.fragments.push(frame.payload)
      conn.fragmentBytes += frame.payload.length
      if (conn.fragmentBytes > this.maxMessageBytes) {
        this.destroyConnection(conn, 1009, 'message too large')
        return
      }
      if (frame.fin) {
        const payload = Buffer.concat(conn.fragments)
        conn.fragments = []
        conn.fragmentBytes = 0
        const op = conn.fragmentOpcode
        conn.fragmentOpcode = null
        if (op === OP_TEXT || op === OP_BINARY) this.onMessage(conn, payload)
      }
    }
  }

  private onMessage(conn: Connection, payload: Buffer): void {
    let msg: Record<string, unknown>
    try {
      const parsed: unknown = JSON.parse(payload.toString('utf8'))
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
        throw new Error('not an object')
      msg = parsed as Record<string, unknown>
    } catch (e) {
      this.sendJson(conn, {
        type: 'protocol_error',
        message: '消息不是 JSON 对象：' + (e as Error).message,
      })
      return
    }
    if (msg.type === 'handshake') {
      this.handleHandshake(conn, msg)
      return
    }
    if (msg.type === 'heartbeat' || msg.type === 'pong') {
      conn.lastSeen = Date.now()
      return
    }
    if (msg.type === 'event') {
      this.onEvent?.({
        ...msg,
        type: 'event',
        instanceId: conn.instance?.instanceId || null,
      })
      return
    }
    if (typeof msg.requestId === 'string') {
      this.handleResponse(msg)
      return
    }
    this.sendJson(conn, { type: 'protocol_error', message: '未知消息类型：' + String(msg.type) })
  }

  private handleHandshake(conn: Connection, msg: Record<string, unknown>): void {
    const value = (
      msg.handshake && typeof msg.handshake === 'object' ? msg.handshake : msg
    ) as unknown
    const result = validateHandshake(value)
    if (!result.ok) {
      this.lastHandshakeError = { ...result, at: Date.now() }
      this.sendJson(conn, {
        type: 'handshake_error',
        stage: result.stage,
        code: result.code,
        message: result.message,
        fix: result.fix,
        ...(result.upgrade_required ? { upgrade_required: true } : {}),
      })
      this.onEvent?.({ type: 'handshake_rejected', code: result.code, epoch: conn.epoch })
      setTimeout(() => this.destroyConnection(conn, 1008, result.message), 20)
      return
    }
    const hs = result.handshake
    const previous = this.connectionFor(hs.instanceId)
    if (previous && previous !== conn) {
      this.failPending(previous.instance?.instanceId || hs.instanceId, 'instance reconnected')
      this.destroyConnection(previous, 4001, 'instance reconnected')
    }
    conn.instance = hs
    const view: AddonInstanceView = {
      instanceId: hs.instanceId,
      kind: hs.kind,
      version: hs.version,
      protocol: hs.protocol,
      project: hs.project,
      capabilities: hs.capabilities.slice(),
      ready: true,
      connectedAt: Date.now(),
      lastSeen: Date.now(),
      epoch: conn.epoch,
    }
    this.instances.set(hs.instanceId, view)
    this.lastHandshakeError = null
    this.sendJson(conn, {
      type: 'handshake_ok',
      gatewayId: this.gatewayId,
      protocol: ADDON_PROTOCOL_VERSION,
      instanceId: hs.instanceId,
      epoch: conn.epoch,
    })
    this.onEvent?.({ type: 'handshake_ok', instanceId: hs.instanceId, epoch: conn.epoch })
  }

  private handleResponse(msg: Record<string, unknown>): void {
    const requestId = String(msg.requestId)
    const pending = this.pending.get(requestId)
    if (!pending) return
    this.pending.delete(requestId)
    clearTimeout(pending.timer)
    if (msg.status === 'error') {
      const err = new GatewayChannelError(
        typeof msg.stage === 'string' ? msg.stage : 'addon',
        typeof msg.code === 'string' ? msg.code : 'addon_error',
        String(msg.message || 'addon error'),
      )
      pending.reject(err)
      return
    }
    pending.resolve(msg.result === undefined ? {} : msg.result)
  }

  private sendJson(conn: Connection, msg: Record<string, unknown>): void {
    if (conn.closed) return
    try {
      conn.socket.write(encodeText(JSON.stringify(msg)))
    } catch (e) {
      this.destroyConnection(conn, 1011, 'write failed')
    }
  }

  private sweepHeartbeat(): void {
    const now = Date.now()
    for (const conn of this.sockets) {
      if (conn.closed) continue
      if (now - conn.lastSeen > this.deadAfterMs) {
        this.destroyConnection(conn, 1001, 'heartbeat timeout')
        continue
      }
      try {
        conn.socket.write(encodeFrame(OP_PING, Buffer.alloc(0)))
      } catch (e) {
        this.destroyConnection(conn, 1011, 'heartbeat write failed')
      }
    }
  }

  private dropConnection(conn: Connection): void {
    if (conn.closed) return
    conn.closed = true
    this.sockets.delete(conn)
    try {
      conn.socket.destroy()
    } catch (e) {
      /* already gone */
    }
    if (conn.instance) {
      const current = this.instances.get(conn.instance.instanceId)
      if (current && current.epoch === conn.epoch) {
        current.ready = false
        current.lastSeen = Date.now()
      }
      this.failPending(conn.instance.instanceId, 'addon 连接已断开：' + conn.instance.instanceId)
      this.onEvent?.({
        type: 'connection_closed',
        instanceId: conn.instance.instanceId,
        epoch: conn.epoch,
      })
    }
  }

  private destroyConnection(conn: Connection, code: number, reason: string): void {
    if (conn.closed) return
    try {
      conn.socket.write(encodeClose(code, reason))
    } catch (e) {
      /* ignore */
    }
    try {
      conn.socket.end()
    } catch (e) {
      /* ignore */
    }
    this.dropConnection(conn)
    setTimeout(() => {
      try {
        conn.socket.destroy()
      } catch (e) {
        /* ignore */
      }
    }, 50)
  }

  private failPending(instanceId: string, message: string): void {
    for (const [id, p] of this.pending) {
      if (p.instanceId !== instanceId) continue
      clearTimeout(p.timer)
      this.pending.delete(id)
      p.reject(new GatewayChannelError('transport', 'instance_disconnected', message))
    }
  }

  private rejectAll(message: string): void {
    for (const [id, p] of this.pending) {
      clearTimeout(p.timer)
      this.pending.delete(id)
      p.reject(new GatewayChannelError('transport', 'gateway_closed', message))
    }
  }
}

/** AddonClient-compatible facade used by the engine. */
export class GatewayAddonClient {
  private readonly gateway: AddonGateway
  readonly protocolInfo = {
    proto: { id: 'ws', label: 'Gateway WebSocket (protocol 12)', panelHint: '' },
    from: 'gateway',
  }
  constructor(gateway: AddonGateway) {
    this.gateway = gateway
  }
  async ensure(): Promise<void> {
    await this.gateway.ensure()
  }
  async protocol(): Promise<typeof this.protocolInfo> {
    return this.protocolInfo
  }
  send(
    type: string,
    params: Record<string, unknown> = {},
    timeoutMs = 120000,
    opts: { instanceId?: string; noMark?: boolean } = {},
  ): Promise<unknown> {
    return this.gateway.send(type, params, timeoutMs, { instanceId: opts.instanceId })
  }
  close(): void {
    this.gateway.close()
  }
  get instances(): AddonInstanceView[] {
    return this.gateway.list()
  }
  get lastHandshakeError(): AddonHandshakeError | null {
    return this.gateway.lastHandshakeError
  }
}
