// Minimal RFC 6455 frame codec used by the Gateway <-> addon control channel.
// No third-party dependency; the addon carries the matching Python implementation.
import crypto from 'node:crypto'

export const OP_CONTINUATION = 0x0
export const OP_TEXT = 0x1
export const OP_BINARY = 0x2
export const OP_CLOSE = 0x8
export const OP_PING = 0x9
export const OP_PONG = 0xa

export const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
export const DEFAULT_MAX_MESSAGE_BYTES = 16 * 1024 * 1024

export interface WsFrame {
  fin: boolean
  opcode: number
  payload: Buffer
}

export interface ParsedFrames {
  frames: WsFrame[]
  rest: Buffer
  error?: string
}

/** Sec-WebSocket-Accept for a client key. */
export function acceptKey(key: string): string {
  return crypto
    .createHash('sha1')
    .update(String(key || '') + WS_GUID)
    .digest('base64')
}

export interface EncodeOptions {
  mask?: boolean
  fin?: boolean
  maskKey?: Buffer
}

/** Encode one unmasked (server) or masked (client) frame. */
export function encodeFrame(
  opcode: number,
  payload: Buffer | string = Buffer.alloc(0),
  opts: EncodeOptions = {},
): Buffer {
  const body = Buffer.isBuffer(payload) ? payload : Buffer.from(String(payload), 'utf8')
  const fin = opts.fin !== false
  const mask = opts.mask === true
  const len = body.length
  if (len > DEFAULT_MAX_MESSAGE_BYTES) throw new Error('websocket frame too large: ' + String(len))
  if (opcode >= 0x8 && len > 125) throw new Error('websocket control frame too large')
  let headerLen = 2
  if (len >= 126 && len <= 0xffff) headerLen += 2
  else if (len > 0xffff) headerLen += 8
  if (mask) headerLen += 4
  const out = Buffer.alloc(headerLen + len)
  out[0] = (fin ? 0x80 : 0) | (opcode & 0x0f)
  let off = 2
  if (len < 126) {
    out[1] = len | (mask ? 0x80 : 0)
  } else if (len <= 0xffff) {
    out[1] = 126 | (mask ? 0x80 : 0)
    out.writeUInt16BE(len, 2)
    off = 4
  } else {
    out[1] = 127 | (mask ? 0x80 : 0)
    out.writeBigUInt64BE(BigInt(len), 2)
    off = 10
  }
  if (mask) {
    const key = opts.maskKey || crypto.randomBytes(4)
    key.copy(out, off)
    off += 4
    for (let i = 0; i < len; i++) out[off + i] = body[i] ^ key[i & 3]
  } else {
    body.copy(out, off)
  }
  return out
}

export function encodeText(text: string, opts: EncodeOptions = {}): Buffer {
  return encodeFrame(OP_TEXT, Buffer.from(text, 'utf8'), opts)
}

export function encodeClose(code = 1000, reason = '', opts: EncodeOptions = {}): Buffer {
  const r = Buffer.from(String(reason || ''), 'utf8')
  const b = Buffer.alloc(2 + r.length)
  b.writeUInt16BE(code, 0)
  r.copy(b, 2)
  return encodeFrame(OP_CLOSE, b, opts)
}

/**
 * Parse as many complete frames as the buffer holds. Never throws on hostile
 * input: returns an error string and the unparsed remainder.
 */
export function parseFrames(
  input: Buffer,
  opts: { requireMask?: boolean; maxMessageBytes?: number } = {},
): ParsedFrames {
  const requireMask = opts.requireMask !== false
  const maxMessageBytes = opts.maxMessageBytes || DEFAULT_MAX_MESSAGE_BYTES
  let buf = Buffer.isBuffer(input) ? input : Buffer.from(input)
  const frames: WsFrame[] = []
  for (;;) {
    if (buf.length < 2) return { frames, rest: buf }
    const b0 = buf[0]
    const b1 = buf[1]
    if (b0 & 0x70) return { frames, rest: buf, error: 'websocket RSV bits are not supported' }
    const fin = (b0 & 0x80) !== 0
    const opcode = b0 & 0x0f
    const masked = (b1 & 0x80) !== 0
    if (requireMask && !masked) return { frames, rest: buf, error: 'client frame is not masked' }
    if (!requireMask && masked) return { frames, rest: buf, error: 'server frame is masked' }
    if (opcode >= 0x8 && opcode !== OP_CLOSE && opcode !== OP_PING && opcode !== OP_PONG) {
      return { frames, rest: buf, error: 'unknown control opcode: ' + String(opcode) }
    }
    if (opcode >= 0x8 && !fin) return { frames, rest: buf, error: 'fragmented control frame' }
    let len = b1 & 0x7f
    let off = 2
    if (len === 126) {
      if (buf.length < 4) return { frames, rest: buf }
      len = buf.readUInt16BE(2)
      off = 4
    } else if (len === 127) {
      if (buf.length < 10) return { frames, rest: buf }
      const big = buf.readBigUInt64BE(2)
      if (big > BigInt(maxMessageBytes))
        return { frames, rest: buf, error: 'websocket frame too large' }
      len = Number(big)
      off = 10
    }
    if (len > maxMessageBytes) return { frames, rest: buf, error: 'websocket frame too large' }
    if (opcode >= 0x8 && len > 125)
      return { frames, rest: buf, error: 'websocket control frame too large' }
    let key: Buffer | null = null
    if (masked) {
      if (buf.length < off + 4) return { frames, rest: buf }
      key = buf.subarray(off, off + 4)
      off += 4
    }
    if (buf.length < off + len) return { frames, rest: buf }
    const payload = Buffer.from(buf.subarray(off, off + len))
    if (key) for (let i = 0; i < payload.length; i++) payload[i] ^= key[i & 3]
    frames.push({ fin, opcode, payload })
    buf = buf.subarray(off + len)
    if (frames.length > 0 && buf.length === 0) return { frames, rest: buf }
  }
}

export function closePayload(payload: Buffer): { code: number | null; reason: string } {
  if (payload.length < 2) return { code: null, reason: '' }
  return { code: payload.readUInt16BE(0), reason: payload.subarray(2).toString('utf8') }
}
