import { describe, expect, it } from 'vitest'
import {
  acceptKey,
  closePayload,
  encodeClose,
  encodeFrame,
  encodeText,
  OP_CLOSE,
  OP_PING,
  OP_TEXT,
  parseFrames,
} from '../../../../runtime/transport/ws.js'

describe('minimal WebSocket frame codec', () => {
  it('computes the RFC 6455 accept key', () => {
    expect(acceptKey('dGhlIHNhbXBsZSBub25jZQ==')).toBe('s3pPLMBiTxaQ9kYGzzhZRbK+xOo=')
  })

  it('round-trips a masked text frame', () => {
    const wire = encodeText('hello 世界', { mask: true })
    const parsed = parseFrames(wire, { requireMask: true })
    expect(parsed.error).toBeUndefined()
    expect(parsed.rest.length).toBe(0)
    expect(parsed.frames).toHaveLength(1)
    expect(parsed.frames[0].opcode).toBe(OP_TEXT)
    expect(parsed.frames[0].payload.toString('utf8')).toBe('hello 世界')
  })

  it('round-trips 126 and 65536 byte payloads', () => {
    for (const size of [126, 65536]) {
      const payload = Buffer.alloc(size, 7)
      const parsed = parseFrames(encodeFrame(2, payload, { mask: true }), { requireMask: true })
      expect(parsed.error).toBeUndefined()
      expect(parsed.frames[0].payload.length).toBe(size)
    }
  })

  it('buffers a partial frame byte by byte', () => {
    const wire = encodeText('partial', { mask: true })
    let buf: Buffer = Buffer.alloc(0)
    let frames = 0
    for (const byte of wire) {
      buf = Buffer.concat([buf, Buffer.from([byte])])
      const parsed = parseFrames(buf, { requireMask: true })
      expect(parsed.error).toBeUndefined()
      frames += parsed.frames.length
      buf = parsed.rest
    }
    expect(frames).toBe(1)
    expect(buf.length).toBe(0)
  })

  it('rejects unmasked client frames and masked server frames', () => {
    expect(parseFrames(encodeText('x'), { requireMask: true }).error).toMatch(/not masked/)
    expect(parseFrames(encodeText('x', { mask: true }), { requireMask: false }).error).toMatch(
      /is masked/,
    )
  })

  it('parses ping/close control frames and close payload', () => {
    const ping = encodeFrame(OP_PING, Buffer.from('p'), { mask: true })
    const close = encodeClose(1001, 'bye', { mask: true })
    const parsed = parseFrames(Buffer.concat([ping, close]), { requireMask: true })
    expect(parsed.frames.map((f) => f.opcode)).toEqual([OP_PING, OP_CLOSE])
    expect(closePayload(parsed.frames[1].payload)).toEqual({ code: 1001, reason: 'bye' })
  })

  it('rejects oversized messages without throwing', () => {
    const parsed = parseFrames(encodeFrame(OP_TEXT, Buffer.alloc(64), { mask: true }), {
      requireMask: true,
      maxMessageBytes: 32,
    })
    expect(parsed.error).toMatch(/too large/)
  })

  it('rejects fragmented control frames', () => {
    const wire = encodeFrame(OP_PING, Buffer.alloc(0), { mask: true, fin: false })
    expect(parseFrames(wire, { requireMask: true }).error).toMatch(/fragmented control/)
  })
})
