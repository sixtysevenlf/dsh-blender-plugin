/**
 * Minimal PNG writer for tests.
 *
 * The pass decoder only consumes PNGs, so the tests need to *produce* exact
 * pixel values without depending on an encoder package: we emit one non-
 * interlaced image with filter type 0 per scanline and a CRC per chunk.
 */
import zlib from 'node:zlib'

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
const CHANNELS: Record<number, number> = { 0: 1, 2: 3, 4: 2, 6: 4 }

function crc32(buffer: Buffer): number {
  const fn = (zlib as unknown as { crc32?: (input: Buffer) => number }).crc32
  if (typeof fn === 'function') return fn(buffer) >>> 0
  // Fallback CRC-32 (IEEE) so the helper works on a Node build without zlib.crc32.
  let crc = 0xffffffff
  for (let i = 0; i < buffer.length; i += 1) {
    crc ^= buffer[i]
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1))
    }
  }
  return (crc ^ 0xffffffff) >>> 0
}

function chunk(type: string, body: Buffer): Buffer {
  const typeBuf = Buffer.from(type, 'ascii')
  const length = Buffer.alloc(4)
  length.writeUInt32BE(body.length, 0)
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, body])), 0)
  return Buffer.concat([length, typeBuf, body, crc])
}

export interface PngSpec {
  width: number
  height: number
  bitDepth: 8 | 16
  /** 0 gray, 2 RGB, 4 gray+alpha, 6 RGBA. */
  colorType: 0 | 2 | 4 | 6
  /** Value per pixel channel, 0..255 (8-bit) or 0..65535 (16-bit). */
  sample: (x: number, y: number, channel: number) => number
  /** PNG filter type per row (cycles); defaults to 0 for every row. */
  filters?: number[]
}

export function encodePng(spec: PngSpec): Buffer {
  const channels = CHANNELS[spec.colorType]
  const bytesPerSample = spec.bitDepth / 8
  const rowBytes = spec.width * channels * bytesPerSample
  const raw = Buffer.alloc((rowBytes + 1) * spec.height)
  const filters = spec.filters ?? [0]
  let previous = Buffer.alloc(rowBytes)
  for (let y = 0; y < spec.height; y += 1) {
    const filter = filters[y % filters.length]
    const rowStart = y * (rowBytes + 1)
    raw[rowStart] = filter
    const line = Buffer.alloc(rowBytes)
    for (let x = 0; x < spec.width; x += 1) {
      for (let c = 0; c < channels; c += 1) {
        const value = spec.sample(x, y, c)
        const offset = (x * channels + c) * bytesPerSample
        if (bytesPerSample === 2) {
          line[offset] = (value >> 8) & 0xff
          line[offset + 1] = value & 0xff
        } else {
          line[offset] = value & 0xff
        }
      }
    }
    for (let i = 0; i < rowBytes; i += 1) {
      const left = i >= bytesPerSample ? line[i - bytesPerSample] : 0
      const up = previous[i]
      const upLeft = i >= bytesPerSample ? previous[i - bytesPerSample] : 0
      let predictor = 0
      if (filter === 1) predictor = left
      else if (filter === 2) predictor = up
      else if (filter === 3) predictor = (left + up) >> 1
      else if (filter === 4) {
        const p = left + up - upLeft
        const pa = Math.abs(p - left)
        const pb = Math.abs(p - up)
        const pc = Math.abs(p - upLeft)
        predictor = pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft
      }
      raw[rowStart + 1 + i] = (line[i] - predictor) & 0xff
    }
    previous = line
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(spec.width, 0)
  ihdr.writeUInt32BE(spec.height, 4)
  ihdr[8] = spec.bitDepth
  ihdr[9] = spec.colorType
  ihdr[10] = 0
  ihdr[11] = 0
  ihdr[12] = 0
  return Buffer.concat([
    SIGNATURE,
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

/** Gray 16-bit PNG from a width*height value function (depth / object index). */
export function gray16Png(
  width: number,
  height: number,
  value: (x: number, y: number) => number,
): Buffer {
  return encodePng({ width, height, bitDepth: 16, colorType: 0, sample: (x, y) => value(x, y) })
}

/** Gray 8-bit PNG (alpha). */
export function gray8Png(
  width: number,
  height: number,
  value: (x: number, y: number) => number,
): Buffer {
  return encodePng({ width, height, bitDepth: 8, colorType: 0, sample: (x, y) => value(x, y) })
}

/** RGB 8-bit PNG from per-channel functions (normal). */
export function rgb8Png(
  width: number,
  height: number,
  value: (x: number, y: number, channel: number) => number,
): Buffer {
  return encodePng({ width, height, bitDepth: 8, colorType: 2, sample: value })
}
