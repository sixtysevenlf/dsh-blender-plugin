import { describe, expect, it } from 'vitest'
import zlib from 'node:zlib'
import { PngDecodeError, decodePng } from '../../../runtime/vision/png.js'
import { encodePng, gray16Png } from '../../helpers/png-writer.js'

describe('decodePng', () => {
  it('decodes 16-bit grayscale samples big-endian', () => {
    const buf = gray16Png(4, 3, (x, y) => x * 1000 + y)
    const image = decodePng(buf)
    expect(image.width).toBe(4)
    expect(image.height).toBe(3)
    expect(image.bitDepth).toBe(16)
    expect(image.colorType).toBe(0)
    expect(image.channels).toBe(1)
    expect(image.maxValue).toBe(65535)
    expect(image.sample(0, 0)).toBe(0)
    expect(image.sample(3, 2)).toBe(3002)
    expect(image.sample(1, 1)).toBe(1001)
  })

  it('decodes RGB8 and RGBA8 channels independently', () => {
    const rgb = encodePng({
      width: 2,
      height: 2,
      bitDepth: 8,
      colorType: 2,
      sample: (x, y, c) => x * 30 + y * 3 + c,
    })
    const image = decodePng(rgb)
    expect(image.channels).toBe(3)
    expect(image.bpp).toBe(3)
    expect(image.sample(1, 1, 0)).toBe(33)
    expect(image.sample(1, 1, 2)).toBe(35)

    const rgba = encodePng({
      width: 1,
      height: 1,
      bitDepth: 8,
      colorType: 6,
      sample: (_x, _y, c) => [10, 20, 30, 255][c],
    })
    const rgbaImage = decodePng(rgba)
    expect(rgbaImage.channels).toBe(4)
    expect(rgbaImage.sample(0, 0, 3)).toBe(255)
  })

  it('rejects a non-PNG buffer with a stable reason', () => {
    let caught: unknown
    try {
      decodePng(Buffer.from('not a png at all'))
    } catch (e) {
      caught = e
    }
    expect(caught).toBeInstanceOf(PngDecodeError)
    expect((caught as PngDecodeError).reason).toContain('signature')
  })

  it('rejects interlaced and unsupported formats instead of guessing', () => {
    const interlaced = gray16Png(2, 2, () => 1)
    // IHDR body starts at byte 16 (8 signature + 4 length + 4 type): bitDepth 24,
    // colorType 25, compression 26, filter method 27, interlace 28.
    interlaced[28] = 1
    expect(() => decodePng(interlaced)).toThrowError(/interlaced/)
    const palette = gray16Png(1, 1, () => 0)
    palette[25] = 3
    expect(() => decodePng(palette)).toThrowError(/color type/)
  })

  it('rejects a truncated IDAT and a bad CRC', () => {
    const buf = gray16Png(2, 2, () => 7)
    const truncated = buf.subarray(0, buf.length - 20)
    expect(() => decodePng(truncated)).toThrowError(PngDecodeError)
    const corrupted = Buffer.from(buf)
    corrupted[40] = corrupted[40] ^ 0xff
    expect(() => decodePng(corrupted)).toThrowError(/CRC|inflate|size/)
  })

  it('unfilters Sub/Up/Average/Paeth rows to the same pixels as filter 0', () => {
    const value = (x: number, y: number) => (x * 7919 + y * 104729) % 65536
    for (const filter of [0, 1, 2, 3, 4]) {
      const buf = encodePng({
        width: 9,
        height: 5,
        bitDepth: 16,
        colorType: 0,
        filters: [filter],
        sample: value,
      })
      const image = decodePng(buf)
      for (let y = 0; y < 5; y += 1) {
        for (let x = 0; x < 9; x += 1) {
          expect(image.sample(x, y), 'filter ' + filter).toBe(value(x, y))
        }
      }
    }
  })

  it('enforces the pixel cap before allocating', () => {
    const buf = gray16Png(4, 4, () => 1)
    expect(() => decodePng(buf, { maxPixels: 8 })).toThrowError(/over limit/)
  })

  it('reports whether CRCs were actually verified instead of pretending', () => {
    // Node 20+ 有 zlib.crc32；缺省时要如实报 crcChecked:false。
    const hasCrc32 = typeof (zlib as { crc32?: unknown }).crc32 === 'function'
    const image = decodePng(gray16Png(2, 2, () => 1))
    expect(image.crcChecked).toBe(hasCrc32)
  })
})
