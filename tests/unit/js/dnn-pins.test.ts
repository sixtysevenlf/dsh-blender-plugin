import { describe, expect, it } from 'vitest'
import {
  DNN_PINS,
  DNN_PIN_SOURCE,
  dnnPinReport,
  pinById,
  pinIsVerified,
} from '../../../src/host/dnn-pins.js'

describe('dnn pins', () => {
  it('locks the five YOLOv5 v7.0 presets in n/s/m/l/x order', () => {
    expect(DNN_PINS.map((p) => p.id)).toEqual([
      'yolov5n',
      'yolov5s',
      'yolov5m',
      'yolov5l',
      'yolov5x',
    ])
    for (const pin of DNN_PINS) {
      expect(pin.filename).toBe(pin.id + '.onnx')
      expect(pin.url).toBe(
        'https://github.com/ultralytics/yolov5/releases/download/v7.0/' + pin.id + '.onnx',
      )
      expect(pin.license).toBe('GPL-3.0')
      expect(pin.source).toBe(DNN_PIN_SOURCE)
    }
  })

  it('never stores a partial or invented checksum', () => {
    for (const pin of DNN_PINS) {
      const hasSha = pin.sha256 !== null
      const hasBytes = pin.bytes !== null
      expect(hasSha).toBe(hasBytes)
      if (pin.sha256 !== null) expect(pin.sha256).toMatch(/^[0-9a-f]{64}$/)
      if (pin.bytes !== null) expect(pin.bytes).toBeGreaterThan(0)
      expect(pinIsVerified(pin)).toBe(hasSha && hasBytes)
    }
  })

  it('pinById only accepts the fixed preset ids', () => {
    expect(pinById('yolov5n')?.id).toBe('yolov5n')
    expect(pinById('yolov5')).toBeUndefined()
    expect(pinById('YOLOV5N')).toBeUndefined()
    expect(pinById('../yolov5n')).toBeUndefined()
    expect(pinById('')).toBeUndefined()
  })

  it('reports downloadable rows for the doctor/config-card surface', () => {
    const rows = dnnPinReport()
    expect(rows).toHaveLength(5)
    const n = rows.find((r) => r.id === 'yolov5n')
    expect(n).toBeDefined()
    expect(n).toMatchObject({ id: 'yolov5n', license: 'GPL-3.0', source: DNN_PIN_SOURCE })
    expect(typeof n?.verified).toBe('boolean')
    expect(n?.url.endsWith('/yolov5n.onnx')).toBe(true)
  })
})
