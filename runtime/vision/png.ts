/**
 * Minimal PNG decoder for render pass files.
 *
 * vision_pass is host-local by design (vision_pass design): it must not wait for the
 * Python/OpenCV worker, so the Gateway itself decodes the few PNG flavours the
 * render-pass writer emits. Only the formats a manifest may declare are
 * supported — non-interlaced, 8-bit or 16-bit, grayscale / gray+alpha / RGB /
 * RGBA. Anything else is an explicit decode error, never a guess: guessing
 * dtype or value range is exactly what the pass contract forbids.
 */
import zlib from 'node:zlib'

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

/** PNG colorType -> channel count (palette is deliberately unsupported). */
const CHANNELS: Record<number, number> = { 0: 1, 2: 3, 4: 2, 6: 4 }

/** Hard caps so a corrupt header cannot request an unbounded allocation. */
export const PNG_MAX_PIXELS = 64_000_000
/**
 * Inflated IDAT cap. Peak decode memory is ~2× the inflated size (inflate buffer +
 * concatenated IDAT); unfiltering runs in place, so a 64 MP gray16 pass needs about
 * 0.4 GB, while RGBA16 at the pixel cap is rejected by this byte limit instead of
 * allocating ~1 GB. Exceeding it is a structured decode error, never a partial image.
 */
export const PNG_MAX_INFLATED_BYTES = 512 * 1024 * 1024

export class PngDecodeError extends Error {
  readonly reason: string
  constructor(reason: string) {
    super(reason)
    this.name = 'PngDecodeError'
    this.reason = reason
  }
}

export interface PngDecodeOptions {
  maxPixels?: number
}

export interface PngImage {
  width: number
  height: number
  bitDepth: 8 | 16
  /** PNG color type: 0 gray, 2 RGB, 4 gray+alpha, 6 RGBA. */
  colorType: number
  channels: number
  /** Bytes per pixel (channels * bitDepth/8). */
  bpp: number
  /** Bytes per unfiltered scanline. */
  rowBytes: number
  /** Unfiltered sample bytes, big-endian for 16-bit samples. */
  data: Uint8Array
  /** True only when every chunk CRC was actually verified (zlib.crc32 present). */
  crcChecked: boolean
  /** Largest stored sample value (65535 for 16-bit, 255 for 8-bit). */
  maxValue: number
  /** Read one sample; 16-bit values are returned as 0..65535. */
  sample(x: number, y: number, channel?: number): number
}

function fail(reason: string): never {
  throw new PngDecodeError(reason)
}

function crc32(body: Buffer, type: Buffer, data: Buffer): number | null {
  const fn = (zlib as unknown as { crc32?: (input: Buffer) => number }).crc32
  if (typeof fn !== 'function') return null
  return fn(Buffer.concat([type, data])) >>> 0
}

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c
  const pa = Math.abs(p - a)
  const pb = Math.abs(p - b)
  const pc = Math.abs(p - c)
  if (pa <= pb && pa <= pc) return a
  if (pb <= pc) return b
  return c
}

/**
 * Unfilter scanlines in place: the destination row always starts before its source
 * row (`y*rowBytes` vs `y*(rowBytes+1)+1`), so writes never clobber unread bytes.
 * This halves peak memory versus allocating a second full-size buffer.
 */
function unfilter(
  inflated: Uint8Array,
  width: number,
  height: number,
  bpp: number,
  rowBytes: number,
): Uint8Array {
  const out = inflated
  for (let y = 0; y < height; y += 1) {
    const src = y * (rowBytes + 1)
    const filter = inflated[src]
    const line = src + 1
    const dst = y * rowBytes
    const prev = dst - rowBytes
    for (let i = 0; i < rowBytes; i += 1) {
      const raw = inflated[line + i]
      const left = i >= bpp ? out[dst + i - bpp] : 0
      const up = y > 0 ? out[prev + i] : 0
      const upLeft = y > 0 && i >= bpp ? out[prev + i - bpp] : 0
      let value: number
      switch (filter) {
        case 0:
          value = raw
          break
        case 1:
          value = raw + left
          break
        case 2:
          value = raw + up
          break
        case 3:
          value = raw + ((left + up) >> 1)
          break
        case 4:
          value = raw + paeth(left, up, upLeft)
          break
        default:
          fail('unsupported PNG filter type ' + String(filter) + ' on row ' + String(y))
      }
      out[dst + i] = value & 0xff
    }
  }
  return out
}

/**
 * Decode a PNG buffer. Throws PngDecodeError with a stable reason string; callers
 * map it to the op's structured error instead of inventing a partial image.
 */
export function decodePng(input: Uint8Array, opts: PngDecodeOptions = {}): PngImage {
  const buffer = Buffer.isBuffer(input) ? input : Buffer.from(input)
  if (buffer.length < 8 || !buffer.subarray(0, 8).equals(SIGNATURE)) {
    fail('not a PNG file (signature mismatch)')
  }
  let pos = 8
  let width = 0
  let height = 0
  let bitDepth = 0
  let colorType = -1
  let sawHeader = false
  const idat: Buffer[] = []
  let sawEnd = false
  let crcChecked = true
  while (pos + 8 <= buffer.length) {
    const length = buffer.readUInt32BE(pos)
    const type = buffer.subarray(pos + 4, pos + 8).toString('ascii')
    const start = pos + 8
    const end = start + length
    if (end + 4 > buffer.length) fail('truncated PNG chunk ' + type)
    const body = buffer.subarray(start, end)
    if (type === 'IHDR') {
      if (length !== 13) fail('bad IHDR length ' + String(length))
      width = body.readUInt32BE(0)
      height = body.readUInt32BE(4)
      bitDepth = body[8]
      colorType = body[9]
      const compression = body[10]
      const filter = body[11]
      const interlace = body[12]
      sawHeader = true
      if (compression !== 0) fail('unsupported PNG compression method ' + String(compression))
      if (filter !== 0) fail('unsupported PNG filter method ' + String(filter))
      if (interlace !== 0) fail('interlaced PNG is not supported for pass files')
      if (width <= 0 || height <= 0) fail('empty PNG dimensions')
      if (bitDepth !== 8 && bitDepth !== 16) fail('unsupported PNG bit depth ' + String(bitDepth))
      if (!(colorType in CHANNELS)) fail('unsupported PNG color type ' + String(colorType))
    } else if (type === 'IDAT') {
      idat.push(body)
    } else if (type === 'IEND') {
      sawEnd = true
      break
    }
    const expectedCrc = buffer.readUInt32BE(end)
    const actual = crc32(body, buffer.subarray(pos + 4, pos + 8), body)
    if (actual === null) crcChecked = false
    else if (actual !== expectedCrc) fail('PNG chunk CRC mismatch in ' + type)
    pos = end + 4
  }
  if (!sawHeader) fail('PNG has no IHDR chunk')
  if (!sawEnd) fail('PNG has no IEND chunk')
  if (idat.length === 0) fail('PNG has no IDAT data')
  const channels = CHANNELS[colorType] as number
  const bpp = channels * (bitDepth / 8)
  const rowBytes = width * bpp
  const pixels = width * height
  const maxPixels = opts.maxPixels ?? PNG_MAX_PIXELS
  if (pixels > maxPixels)
    fail('PNG has ' + String(pixels) + ' pixels, over limit ' + String(maxPixels))
  const expected = height * (rowBytes + 1)
  if (expected > PNG_MAX_INFLATED_BYTES) fail('PNG would inflate past the decode limit')
  let inflated: Buffer
  try {
    inflated = zlib.inflateSync(Buffer.concat(idat), { maxOutputLength: expected })
  } catch (e) {
    fail('PNG IDAT inflate failed: ' + (e instanceof Error ? e.message : String(e)))
  }
  if (inflated.length !== expected) {
    fail('PNG IDAT size ' + String(inflated.length) + ' does not match ' + String(expected))
  }
  const data = unfilter(inflated, width, height, bpp, rowBytes)
  const bytesPerSample = bitDepth / 8
  const maxValue = bitDepth === 16 ? 65535 : 255
  const image: PngImage = {
    width,
    height,
    bitDepth: bitDepth as 8 | 16,
    colorType,
    channels,
    bpp,
    rowBytes,
    data,
    crcChecked,
    maxValue,
    sample(x: number, y: number, channel = 0): number {
      if (x < 0 || y < 0 || x >= width || y >= height || channel < 0 || channel >= channels) {
        fail('sample out of bounds')
      }
      const offset = y * rowBytes + x * bpp + channel * bytesPerSample
      if (bytesPerSample === 2) return (data[offset] << 8) | data[offset + 1]
      return data[offset]
    },
  }
  return image
}
