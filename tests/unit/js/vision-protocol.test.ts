import { describe, expect, it } from 'vitest'
import {
  MAX_FRAME_BYTES,
  PROTOCOL_VERSION,
  REQUEST_TIMEOUT_MS,
  JsonLinesDecoder,
  VisionError,
  encodeFrame,
  toVisionErrorPayload,
} from '../../../runtime/vision/protocol.js'

// Protocol framing is the contract between the Gateway and its private vision
// worker: newline-delimited JSON, one frame limit, explicit protocol errors.
// These tests stay pure (no child process, no Python).

function frame(value: unknown): Buffer {
  return Buffer.from(JSON.stringify(value) + '\n', 'utf8')
}

describe('vision protocol constants', () => {
  it('pins the documented protocol limits', () => {
    expect(PROTOCOL_VERSION).toBe(1)
    expect(MAX_FRAME_BYTES).toBe(8 * 1024 * 1024)
    expect(REQUEST_TIMEOUT_MS).toBe(30_000)
  })
})

describe('encodeFrame', () => {
  it('produces exactly one newline-delimited JSON object', () => {
    const buf = encodeFrame({ id: 'r1', op: 'status', args: {} })
    expect(buf.at(-1)).toBe(0x0a)
    expect(buf.toString('utf8').split('\n')).toHaveLength(2)
    expect(JSON.parse(buf.toString('utf8').trim())).toEqual({ id: 'r1', op: 'status', args: {} })
  })

  it('rejects an oversized frame with a protocol error', () => {
    const err = (() => {
      try {
        encodeFrame({ id: 'r2', op: 'analyze', args: { blob: 'x'.repeat(MAX_FRAME_BYTES) } })
        return null
      } catch (e) {
        return e
      }
    })()
    expect(err).toBeInstanceOf(VisionError)
    const v = err as InstanceType<typeof VisionError>
    expect(v.code).toBe('vision_protocol_error')
    expect(v.stage).toBe('vision')
    expect(v.retryable).toBe(true)
  })
})

describe('JsonLinesDecoder', () => {
  it('reassembles a frame split across chunks', () => {
    const decoder = new JsonLinesDecoder()
    const buf = frame({ id: 'a', ok: true, result: { n: 1 } })
    expect(decoder.push(buf.subarray(0, 5))).toEqual([])
    expect(decoder.push(buf.subarray(5, 12))).toEqual([])
    const out = decoder.push(buf.subarray(12))
    expect(out).toHaveLength(1)
    expect(out[0]).toEqual({ id: 'a', ok: true, result: { n: 1 } })
  })

  it('returns several frames from one chunk and tolerates CRLF', () => {
    const decoder = new JsonLinesDecoder()
    const payload = Buffer.concat([
      Buffer.from('{"id":"a","ok":true}\r\n{"id":"b","ok":false}\n'),
      Buffer.from('{"id":"c"'),
    ])
    expect(decoder.push(payload).map((f) => f.id)).toEqual(['a', 'b'])
    expect(decoder.push(Buffer.from('}\n')).map((f) => f.id)).toEqual(['c'])
  })

  it('rejects invalid JSON without swallowing subsequent state silently', () => {
    const decoder = new JsonLinesDecoder()
    let caught: unknown = null
    try {
      decoder.push(Buffer.from('{not json}\n'))
    } catch (e) {
      caught = e
    }
    expect(caught).toBeInstanceOf(VisionError)
    expect((caught as InstanceType<typeof VisionError>).code).toBe('vision_protocol_error')
    expect((caught as InstanceType<typeof VisionError>).stage).toBe('vision')
    expect(decoder.pendingBytes).toBe(0)
  })

  it('rejects a JSON scalar or array frame', () => {
    for (const bad of ['42\n', '[]\n', 'null\n', '"x"\n']) {
      const decoder = new JsonLinesDecoder()
      expect(() => decoder.push(Buffer.from(bad))).toThrowError(/object/i)
    }
  })

  it('rejects a frame that grows past the byte limit before its newline', () => {
    const decoder = new JsonLinesDecoder({ maxFrameBytes: 32 })
    expect(() => decoder.push(Buffer.from('x'.repeat(33)))).toThrowError(VisionError)
    expect(decoder.pendingBytes).toBe(0)
  })

  it('reports the real byte count when the pending tail overflows', () => {
    const decoder = new JsonLinesDecoder({ maxFrameBytes: 100 })
    let error: InstanceType<typeof VisionError> | null = null
    try {
      decoder.push(Buffer.from('x'.repeat(150)))
    } catch (e) {
      error = e as InstanceType<typeof VisionError>
    }
    expect(error?.code).toBe('vision_protocol_error')
    expect(error?.details).toMatchObject({ bytes: 150, limit: 100 })
    expect(decoder.pendingBytes).toBe(0)
  })

  it('rejects an oversized line that arrives complete in one chunk', () => {
    const decoder = new JsonLinesDecoder({ maxFrameBytes: 32 })
    const line = '{"id":"a","result":"' + 'x'.repeat(40) + '"}'
    let error: InstanceType<typeof VisionError> | null = null
    try {
      decoder.push(Buffer.from(line + '\n'))
    } catch (e) {
      error = e as InstanceType<typeof VisionError>
    }
    expect(error?.code).toBe('vision_protocol_error')
    // 去掉结尾换行后的真实字节数，而不是 reset 之后的 0。
    expect(error?.details).toMatchObject({ bytes: line.length, limit: 32 })
    expect(decoder.pendingBytes).toBe(0)
  })

  it('accepts a line exactly at the limit and measures CRLF without the terminator', () => {
    const decoder = new JsonLinesDecoder({ maxFrameBytes: 16 })
    const body = '{"a":1234567890}' // 16 bytes
    expect(decoder.push(Buffer.from(body + '\r\n'))).toHaveLength(1)
  })

  it('drops the partial tail when reset', () => {
    const decoder = new JsonLinesDecoder()
    decoder.push(Buffer.from('{"id":"a"'))
    expect(decoder.pendingBytes).toBeGreaterThan(0)
    decoder.reset()
    expect(decoder.pendingBytes).toBe(0)
    expect(decoder.push(frame({ id: 'b' })).map((f) => f.id)).toEqual(['b'])
  })
})

describe('VisionError', () => {
  it('carries the documented structured fields', () => {
    const err = new VisionError({
      code: 'vision_custom_env_invalid',
      stage: 'config',
      message: 'venv missing cv2',
      fix: 'install opencv-python-headless into the venv',
      retryable: false,
      details: { missing: ['cv2'] },
    })
    expect(err).toBeInstanceOf(Error)
    expect(err.code).toBe('vision_custom_env_invalid')
    expect(err.stage).toBe('config')
    expect(err.fix).toContain('opencv')
    expect(err.retryable).toBe(false)
    expect(err.details).toEqual({ missing: ['cv2'] })
    expect(err.toPayload()).toEqual({
      code: 'vision_custom_env_invalid',
      stage: 'config',
      message: 'venv missing cv2',
      fix: 'install opencv-python-headless into the venv',
      retryable: false,
      details: { missing: ['cv2'] },
    })
  })

  it('wraps unknown failures as stage=vision without inventing success', () => {
    const wrapped = VisionError.from(new Error('boom'))
    expect(wrapped.code).toBe('vision_worker_failed')
    expect(wrapped.stage).toBe('vision')
    expect(wrapped.message).toContain('boom')
    expect(wrapped.retryable).toBe(true)

    const passthrough = new VisionError({
      code: 'vision_invalid_image',
      stage: 'input',
      message: 'bad',
      retryable: false,
    })
    expect(VisionError.from(passthrough)).toBe(passthrough)
  })

  it('rejects an invalid stage instead of silently coercing it', () => {
    expect(
      () =>
        new VisionError({
          code: 'x',
          // @ts-expect-error stage must be one of the documented values
          stage: 'backend',
          message: 'm',
          retryable: false,
        }),
    ).toThrowError(/stage/i)
  })
})

describe('toVisionErrorPayload', () => {
  it('preserves worker payloads and defaults retryable to false', () => {
    const payload = toVisionErrorPayload({
      code: 'dnn_no_model',
      stage: 'config',
      message: 'no onnx',
    })
    expect(payload.code).toBe('dnn_no_model')
    expect(payload.retryable).toBe(false)
    expect(payload.fix).toBeUndefined()
  })

  it('rejects malformed worker payloads', () => {
    expect(() => toVisionErrorPayload({ code: 'x' })).toThrowError(/stage/i)
    expect(() => toVisionErrorPayload({ stage: 'vision' })).toThrowError(/code/i)
  })
})
